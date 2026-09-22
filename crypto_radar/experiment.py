"""Independent deterministic detector and evidence capture in the shared runtime."""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from .collectors.social import SocialObservation, UnavailableSocialCollector, make_collector
from .intelligence.social import social_features
from .intelligence.catalysts import normalize_news, news_features
from . import experiment_measurement as measurement
from . import experiment_ai
from .filters import is_stablecoin
from .policies import update_episode

log = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc)


def status(conn, scan_id, collector, coin, ts, state, count=0, error=None):
    conn.execute('INSERT OR REPLACE INTO collector_runs VALUES (?,?,?,?,?,?,?)',
                 (scan_id,collector,coin,ts,state,count,error))


def collect_social(conn, scan_id, markets, now, cfg, provider=None, clock=utcnow):
    provider = provider or make_collector(cfg)
    if not cfg['enabled'] or isinstance(provider,UnavailableSocialCollector):
        status(conn,scan_id,'social','*',now.isoformat(),'unavailable')
        return 'unavailable', 0
    errors = 0
    count = 0
    try:
        supplied = provider.collect(markets,now)
        observed_ts = clock().isoformat()
        if not isinstance(supplied,list):
            raise ValueError('Social collector must return a bounded list')
        if len(supplied) > 100000:
            raise ValueError('Social batch too large')
        ids = {c['id'] for c in markets}
        for item in supplied:
            try:
                item = SocialObservation.model_validate(item)
                if item.coin_id not in ids or item.window_end > now:
                    raise ValueError('Unexpected asset or future social bucket')
                # Metadata is an explicit whitelist; tokens and arbitrary provider payloads never persist.
                metadata = {key:item.metadata[key] for key in ('community','duplicate_method','concentration_method') if key in item.metadata}
                payload = json.dumps(metadata,allow_nan=False)
                if len(payload) > 4096:
                    raise ValueError('Social metadata too large')
                count += conn.execute('''INSERT OR IGNORE INTO social_observations
                    (coin_id,source,window_start,window_end,observed_ts,mentions,unique_authors,engagement,
                     sentiment,duplicate_fraction,top_author_fraction,metadata_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (item.coin_id,item.source,item.window_start.isoformat(),item.window_end.isoformat(),
                     observed_ts,item.mentions,item.unique_authors,item.engagement,item.sentiment,
                     item.duplicate_fraction,item.top_author_fraction,payload)).rowcount
            except (ValueError,TypeError):
                errors += 1
        state = 'partial_error' if errors else 'available'
    except Exception as exc:
        errors += 1
        state = 'failed'
        log.error('Social collector failed: %s',type(exc).__name__)
    status(conn,scan_id,'social','*',now.isoformat(),state,count,'invalid_or_failed' if errors else None)
    conn.commit()
    return state, errors


def ingest_news(conn, coin, items, observed, cfg):
    for item in items:
        data = normalize_news(item,coin,observed)
        quality = 0.8 if data['source'].lower() in {s.lower() for s in cfg['trusted_sources']} else None
        conn.execute('''INSERT OR IGNORE INTO news_evidence
            (coin_id,fingerprint,first_observed_ts,publication_ts,title,link,source,catalyst_type,relevance,source_quality,event_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (coin['id'],data['fingerprint'],data['first_observed_ts'],data['publication_ts'],data['title'],
             data['link'],data['source'],data['catalyst_type'],data['relevance'],quality,data['event_key']))


def collect_news(conn, scan_id, markets, cfg, fetch_news, clock, cached):
    if not cfg['enabled']:
        status(conn,scan_id,'news','*',clock().isoformat(),'disabled')
        return 'disabled', 0
    errors = 0
    # Persist already-fetched V1.1 news with its REAL completion timestamp.
    for coin in markets:
        if coin['id'] in cached:
            items, observed = cached[coin['id']]
            try:
                ingest_news(conn,coin,items,observed,cfg)
                status(conn,scan_id,'news',coin['id'],observed.isoformat(),'available',len(items))
            except (ValueError,TypeError):
                errors += 1
    due = []
    for coin in markets:
        if coin['id'] in cached:
            continue
        last = conn.execute("SELECT MAX(observed_ts) FROM collector_runs WHERE collector='news' AND coin_id=?",(coin['id'],)).fetchone()[0]
        if not last or (clock()-datetime.fromisoformat(last)).total_seconds() >= cfg['poll_minutes']*60:
            due.append((last or '',coin['id'],coin))
    for _, _, coin in sorted(due)[:cfg['assets_per_scan']]:
        try:
            items = fetch_news(coin['name'],coin['symbol'],cfg['max_items'])
            observed = clock()
            ingest_news(conn,coin,items,observed,cfg)
            status(conn,scan_id,'news',coin['id'],observed.isoformat(),'available',len(items))
        except Exception as exc:
            errors += 1
            status(conn,scan_id,'news',coin['id'],clock().isoformat(),'failed',error=type(exc).__name__)
            log.error('News collector asset=%s failed: %s',coin['id'],type(exc).__name__)
    conn.commit()
    return ('partial_error' if errors else 'available'),errors


def mirror_v11(conn, scan_id, records, markets, observed, market_ts, cfg):
    for coin, stats, eid, eligible in records:
        decision = conn.execute('SELECT decision,reason FROM candidate_evaluations WHERE id=?',(eid,)).fetchone()
        flags = {'v11_candidate':eligible,'market_anomaly':bool(stats and stats['volume_z'] >= cfg['filters']['anomaly_threshold'])}
        ep = measurement.episode(conn,coin,observed,market_ts,flags,cfg) if stats else None
        features = dict(market_score=stats['pre_score'] if stats else None,
                        pre_score=stats['pre_score'] if stats else None,market_features=stats,
                        min_broad_assets=cfg['v12']['market_regime']['min_broad_assets'])
        measurement.register_evaluation(conn,scan_id,coin,observed.isoformat(),market_ts,'1.1','market_only',
            features,eligible,decision['decision'],decision['reason'],ep,cfg,markets,v11_id=eid)
    conn.commit()


def run_detector(conn,scan_id,markets,stats_by_coin,market_ts,cfg,summary,fetch_news,
                 investigate,validate_response,deliver_alerts,cached=None,provider=None,clock=utcnow):
    settings = cfg['v12']
    assets = [c for c in markets if not is_stablecoin(c,cfg)]
    state, errors = collect_social(conn,scan_id,assets,clock(),settings['social'],provider,clock)
    summary['social_status'] = state
    summary['errors'] += errors
    state, errors = collect_news(conn,scan_id,assets,settings['news'],fetch_news,clock,cached or {})
    summary['news_status'] = state
    summary['errors'] += errors
    cutoff = clock()
    v11 = {r[0] for r in conn.execute("SELECT coin_id FROM experiment_evaluations WHERE scan_id=? AND signal_version='1.1' AND eligible=1",(scan_id,))}
    candidates = []
    for coin in markets:
        market = stats_by_coin.get(coin['id'])
        rows = conn.execute('SELECT * FROM social_observations WHERE coin_id=? AND window_end>=?',
            (coin['id'],(cutoff-timedelta(hours=settings['social']['baseline_hours']+1)).isoformat())).fetchall()
        social = social_features(rows,cutoff,settings['social'])
        keys = ('status','mentions_5m','mentions_15m','mentions_1h','baseline_windows','mention_acceleration',
                'author_acceleration','engagement_acceleration','sentiment_change','source_count',
                'duplicate_fraction','top_author_fraction','score')
        sid = conn.execute('''INSERT INTO social_features
            (scan_id,coin_id,asof_ts,status,mentions_5m,mentions_15m,mentions_1h,baseline_windows,mention_acceleration,
             author_acceleration,engagement_acceleration,sentiment_change,source_count,duplicate_fraction,
             top_author_fraction,score,features_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (scan_id,coin['id'],cutoff.isoformat(),*[social[k] for k in keys],json.dumps(social,allow_nan=False))).lastrowid
        news = news_features(conn.execute('''SELECT e.*, (SELECT MIN(first_observed_ts) FROM news_evidence
            WHERE coin_id=e.coin_id AND event_key=e.event_key) AS event_first_observed_ts
            FROM news_evidence e WHERE coin_id=? AND first_observed_ts>=?''',
            (coin['id'],(cutoff-timedelta(hours=settings['news']['novelty_hours'])).isoformat())).fetchall(),cutoff,settings['news'])
        s = social['score'] >= settings['scoring']['social_threshold'] and settings['social']['enabled']
        n = news['score'] >= settings['scoring']['news_threshold'] and settings['news']['enabled']
        m = bool(market and market['pre_score'] >= cfg['ai']['min_pre_score'])
        group = 'market_social_news' if s and n and m else 'social_news' if s and n else 'social_only' if s else 'news_only' if n else 'no_upstream_signal'
        score = (0.4*social['score']+0.4*news['score']+0.2*market['pre_score'] if group=='market_social_news'
                 else (social['score']+news['score'])/2 if s and n else social['score'] if s else news['score'] if n else 0)
        excluded = is_stablecoin(coin,cfg) or market is None
        eligible = not excluded and bool(s or n) and score >= settings['scoring']['candidate_threshold']
        stale = (cutoff-datetime.fromisoformat(market_ts)).total_seconds() > settings['max_market_age_seconds']
        eligible = eligible and not stale
        decision = 'excluded' if excluded else 'candidate' if eligible else 'rejected'
        reason = 'stablecoin_or_market_filters' if excluded else None if eligible else 'insufficient_upstream_evidence'
        if stale and not excluded:
            reason = 'stale_market_snapshot'
        if summary['detector'] == 'v12':
            update_episode(conn,coin['id'],eligible,cutoff,cfg)
        flags = dict(social_anomaly=bool(s),news_event=bool(n),market_anomaly=bool(market and market['volume_z']>=cfg['filters']['anomaly_threshold']),v12_candidate=eligible)
        ep = measurement.episode(conn,coin,cutoff,market_ts,flags,cfg) if not excluded else None
        stats = dict(market_score=market['pre_score'] if market else None,social_score=social['score'],news_score=news['score'],
                     pre_score=round(score,2),market_features=market,social_features=social,
                     news_features={k:v for k,v in news.items() if k!='evidence'},scoring_version='1.2',
                     min_broad_assets=settings['market_regime']['min_broad_assets'])
        eid = measurement.register_evaluation(conn,scan_id,coin,cutoff.isoformat(),market_ts,'1.2',group,stats,
            eligible,decision,reason,ep,cfg,markets,social_id=sid,news=news['evidence'])
        if eligible:
            summary['v12_candidates'] += 1
            summary['overlapping_candidates'] += int(coin['id'] in v11)
            summary['v12_only_candidates'] += int(coin['id'] not in v11)
            candidates.append((eid,coin,social,news))
    measurement.fill_moves(conn,cutoff)
    conn.commit()
    for eid,coin,_,_ in candidates:
        if coin['id'] not in v11:
            move = conn.execute('''SELECT p.significant_move_ts FROM experiment_evaluations e
                JOIN research_episodes p ON p.id=e.episode_id WHERE e.id=?''',(eid,)).fetchone()
            key = 'v12_only_already_moved' if move and move[0] else 'v12_only_no_move_yet'
            summary[key] += 1
    for eid,coin,social,news in sorted(candidates,key=lambda c: conn.execute('SELECT pre_score FROM experiment_evaluations WHERE id=?',(c[0],)).fetchone()[0],reverse=True):
        evaluation = conn.execute('SELECT * FROM experiment_evaluations WHERE id=?',(eid,)).fetchone()
        call_id = None
        try:
            reason = None
            if not cfg['ai']['enabled'] or not settings['ai']['enabled']:
                reason = 'ai_disabled'
            elif not os.getenv('OPENAI_API_KEY','').strip():
                reason = 'missing_api_key'
            elif conn.execute('SELECT 1 FROM ai_calls WHERE coin_id=? AND evaluation_id IN (SELECT id FROM candidate_evaluations WHERE scan_id=?)', (coin['id'],scan_id)).fetchone():
                reason = 'v11_assessed_same_scan'
            elif summary['ai_calls_made'] >= cfg['ai']['max_candidates_per_scan'] or summary['v12_ai_calls'] >= settings['ai']['max_candidates_per_scan']:
                reason = 'scan_limit'
            if not reason:
                run = conn.execute('SELECT config_json,config_hash FROM scan_runs WHERE id=?',(scan_id,)).fetchone()
                regime = dict(
                    benchmarks=[dict(r) for r in conn.execute('SELECT * FROM benchmark_context WHERE evaluation_id=?',(eid,))],
                    benchmark_snapshots=[m for m in markets if m['id'] in ('bitcoin','ethereum')],
                    observed_ts=market_ts,
                    broad_member_count=conn.execute('SELECT COUNT(*) FROM benchmark_members WHERE scan_id=? AND coin_id!=?',
                        (scan_id,coin['id'])).fetchone()[0])
                request = experiment_ai.build_request(evaluation,coin,news['evidence'],social,regime,run['config_json'],run['config_hash'],cfg['ai'])
                call_id,reason = experiment_ai.reserve(conn,evaluation,clock(),cfg,request)
            if reason:
                conn.execute("UPDATE experiment_evaluations SET decision='suppressed',reason=? WHERE id=?",(reason,eid))
                summary['v12_suppressed'] += 1
                conn.commit()
                continue
            summary['ai_calls_made'] += 1
            summary['v12_ai_calls'] += 1
            response = investigate(request,cfg['ai']['timeout_seconds'])
            conn.execute('UPDATE v12_ai_calls SET response_json=?,returned_model=?,usage_json=? WHERE id=?',
                (response.model_dump_json(),response.model,response.usage.model_dump_json() if response.usage else None,call_id))
            conn.commit()
            result = validate_response(response)
            with conn:
                aid = conn.execute('''INSERT INTO assessments
                    (ts,coin_id,symbol,pre_score,surge_score,stage,catalyst_strength,manipulation_risk,confidence,
                     thesis,risks,raw_json,market_observed_ts,assessment_kind,experiment_evaluation_id,v12_ai_call_id)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'ai_v12',?,?)''',
                    (clock().isoformat(),coin['id'],coin['symbol'],evaluation['pre_score'],result['surge_score'],
                     result['stage'],result['catalyst_strength'],result['manipulation_risk'],result['confidence'],
                     result['thesis'],result['risks'],json.dumps(result),market_ts,eid,call_id)).lastrowid
                conn.execute("UPDATE v12_ai_calls SET status='succeeded',completed_ts=? WHERE id=?",(clock().isoformat(),call_id))
                conn.execute("UPDATE experiment_evaluations SET decision='assessed' WHERE id=?",(eid,))
            if settings['alerts']['enabled'] and result['surge_score'] >= cfg['ai']['alert_score']:
                # Existing persistent per-channel dedup remains authoritative; V1.1 episode numbers are reused.
                state = conn.execute('SELECT episode FROM signal_state WHERE coin_id=?',(coin['id'],)).fetchone()
                deliver_alerts(conn,cfg,aid,coin,result,state[0] if state else 1,summary)
        except Exception as exc:
            conn.rollback()
            summary['errors'] += 1
            with conn:
                if call_id:
                    conn.execute("UPDATE v12_ai_calls SET status='failed',completed_ts=?,error=? WHERE id=?",(clock().isoformat(),type(exc).__name__,call_id))
                conn.execute("UPDATE experiment_evaluations SET decision='failed',reason=? WHERE id=?",(type(exc).__name__,eid))
            log.error('V1.2 candidate %s failed: %s',coin['id'],type(exc).__name__)
