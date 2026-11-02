"""Read-only ApeWisdom health, chronological indicators and episode timing research."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from statistics import mean, median, pstdev

from .episode_report import HORIZONS, METRICS
from .experiment_measurement import ret


def dt(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone-aware timestamps required')
    return value.astimezone(timezone.utc)


def minutes(signal, event):
    return (dt(event)-dt(signal)).total_seconds()/60 if signal and event else None


def summary(values):
    values = [v for v in values if v is not None]
    return dict(n=len(values), mean=mean(values) if values else None,
                median=median(values) if values else None)


def indicators(rows, cutoff, max_age=3600, sample_seconds=1800,
               baseline_min=12, baseline_hours=24):
    """One sample per elapsed poll interval, changed complete responses only."""
    end = dt(cutoff)
    history = []
    output = []
    latest = None
    for row in sorted(rows, key=lambda r: (dt(r['observed_ts']), r['run_id'])):
        ts = dt(row['observed_ts'])
        if ts > end:
            continue
        latest = row
        if row['status'] != 'available' or not row['complete'] or row['mentions'] is None:
            continue
        if history and (ts-dt(history[-1]['observed_ts'])).total_seconds() < sample_seconds:
            continue
        previous = history[-1] if history else None
        adjacent = previous and 0 < (ts-dt(previous['observed_ts'])).total_seconds() <= max_age
        baseline = [r for r in history if ts-timedelta(hours=baseline_hours) <= dt(r['observed_ts']) < ts]
        enough = len(baseline) >= baseline_min
        mu = mean(r['mentions'] for r in baseline) if enough else None
        sd = pstdev(r['mentions'] for r in baseline) if enough else None
        delta = row['mentions']-previous['mentions'] if adjacent else None
        relative = delta/previous['mentions'] if adjacent and previous['mentions'] > 0 else None
        change = lambda key: (row[key]-previous[key] if adjacent and row[key] is not None
                              and previous[key] is not None else None)
        # Baseline uses only earlier samples. Zero variance cannot establish an anomaly.
        level_z = (row['mentions']-mu)/sd if sd and sd > 0 else None
        excess_rate = None
        if adjacent and enough and len(baseline) > 1:
            rates = [(b['mentions']-a['mentions'])/((dt(b['observed_ts'])-dt(a['observed_ts'])).total_seconds()/3600)
                     for a, b in zip(baseline, baseline[1:])
                     if 0 < (dt(b['observed_ts'])-dt(a['observed_ts'])).total_seconds() <= max_age]
            if len(rates) >= baseline_min-1:
                excess_rate = delta/((ts-dt(previous['observed_ts'])).total_seconds()/3600)-mean(rates)
        elevated = None if not enough else row['mentions'] > mu
        streak = (output[-1]['elevated_streak'] or 0)+1 if elevated and adjacent and output else 1 if elevated else 0 if enough else None
        output.append(dict(observed_ts=row['observed_ts'], run_id=row['run_id'], mentions=row['mentions'],
            rolling_mentions_delta=delta, relative_rolling_mentions_change=relative,
            rolling_rank_delta=change('rank'), rolling_upvotes_delta=change('upvotes'),
            baseline_samples=len(baseline), baseline_mean=mu, level_z=level_z,
            rolling_delta_rate_excess_per_hour=excess_rate, elevated_streak=streak))
        history.append(row)
    age = (end-dt(latest['observed_ts'])).total_seconds() if latest else None
    available = bool(latest and latest['status'] == 'available' and latest['complete']
                     and latest['mentions'] is not None and age <= max_age)
    return dict(available=available, receipt_age_seconds=age, samples=output,
                latest_indicator=output[-1] if output and available else None)


def health(db, as_of, cfg):
    runs = [dict(r) for r in db.execute("SELECT * FROM attention_runs WHERE provider='apewisdom' AND observed_ts<=? ORDER BY observed_ts,id", (as_of,))]
    rows = [dict(r) for r in db.execute('''SELECT s.*,r.status,r.complete FROM apewisdom_snapshots s
        JOIN attention_runs r ON r.id=s.run_id WHERE r.provider='apewisdom' AND s.observed_ts<=?
        AND r.observed_ts<=? ORDER BY s.observed_ts,s.run_id,s.ticker''', (as_of, as_of))]
    statuses = [dict(r) for r in db.execute("SELECT status,count(*) runs FROM collector_runs WHERE collector='social:apewisdom' AND observed_ts<=? GROUP BY status", (as_of,))]
    poll = cfg.get('poll_seconds', 1800)
    successful = [r for r in runs if r['complete'] and r['status'] in ('available', 'repeated')]
    changed = [r for r in runs if r['complete'] and r['status'] == 'available']
    gaps = [dict(start=a['observed_ts'], end=b['observed_ts'], seconds=(dt(b['observed_ts'])-dt(a['observed_ts'])).total_seconds())
            for a, b in zip(runs, runs[1:])]
    by_coin = defaultdict(list)
    unresolved = Counter()
    for r in rows:
        if r['coin_id'] is None:
            unresolved[r['ticker']] += 1
        else:
            by_coin[r['coin_id']].append(r)
    coverage = []
    for coin in sorted(set(cfg.get('asset_map', {})) | set(by_coin)):
        points = by_coin.get(coin, [])
        state = indicators(points, as_of, sample_seconds=poll)
        coverage.append(dict(coin_id=coin, configured_ticker=cfg.get('asset_map', {}).get(coin),
            rows=len(points), first=points[0]['observed_ts'] if points else None,
            last=points[-1]['observed_ts'] if points else None,
            availability=state['available'], receipt_age_seconds=state['receipt_age_seconds'],
            accepted_samples=len(state['samples']), indicators=state['samples']))
    def interval_stats(items):
        return summary([(dt(b['observed_ts'])-dt(a['observed_ts'])).total_seconds() for a, b in zip(items, items[1:])])
    last_success = successful[-1]['observed_ts'] if successful else None
    latest_changed = changed[-1]['observed_ts'] if changed else None
    changed_age = (dt(as_of)-dt(latest_changed)).total_seconds() if latest_changed else None
    return dict(provider='apewisdom', observation_rows=len(rows), distinct_resolved_assets=len(by_coin),
        first_observation=rows[0]['observed_ts'] if rows else None,
        last_observation=rows[-1]['observed_ts'] if rows else None,
        last_successful_collection=last_success, last_changed_response=latest_changed,
        changed_response_age_seconds=changed_age,
        data_status='unavailable' if not rows else 'unresolved' if not by_coin else 'available' if any(c['availability'] for c in coverage) else 'stale_or_unknown',
        run_states=dict(Counter(r['status'] for r in runs)), collector_states=statuses,
        repeated_responses=sum(r['status']=='repeated' for r in runs),
        duplicate_keys=len(rows)-len({(r['run_id'], r['ticker']) for r in rows}),
        unresolved_tickers=dict(unresolved), coverage=coverage,
        ambiguous_configured_tickers=[ticker for ticker,n in Counter(v.upper() for v in cfg.get('asset_map',{}).values()).items() if n>1],
        poll_interval_seconds=interval_stats(runs), changed_response_interval_seconds=interval_stats(changed),
        polling_gaps=[g for g in gaps if g['seconds'] > poll*2],
        ongoing_gap_seconds=(dt(as_of)-dt(runs[-1]['observed_ts'])).total_seconds() if runs else None,
        provider_refresh_frequency='Unknown: no provider update timestamp; changed receipts only bound observed response changes.',
        provider_timestamp_rows=sum(r['provider_ts'] is not None for r in rows),
        timestamp_errors=sum(dt(r['observed_ts']) < dt(next(x['observed_ts'] for x in runs if x['id']==r['run_id'])) for r in rows))


def first_evaluation(db, episode_id, as_of, before=None, candidate=False):
    # Reuse the episode index; never average repeated detections into sample size.
    conditions = " AND eligible=1" if candidate else ""
    conditions += " AND detected_ts<?" if before else ""
    params = [episode_id, as_of, as_of] + ([before] if before else [])
    order = 'DESC' if before else 'ASC'
    row = db.execute(f'''SELECT * FROM experiment_evaluations WHERE episode_id=? AND signal_version='1.1'
        AND detected_ts<=? AND evidence_cutoff_ts<=? {conditions}
        ORDER BY detected_ts {order},id {order} LIMIT 1''', params).fetchone()
    return dict(row) if row else None


def diagnostic(db, evaluation):
    if not evaluation:
        return None
    features = json.loads(evaluation['features_json'])
    market = features.get('market_features') or {}
    scan = db.execute('SELECT config_json,started_ts,completed_ts FROM scan_runs WHERE id=?', (evaluation['scan_id'],)).fetchone()
    cfg = json.loads(scan['config_json']) if scan else {}
    threshold = cfg.get('ai', {}).get('min_pre_score')
    points = db.execute('SELECT ts,source_updated_ts FROM market_observations WHERE coin_id=? AND ts<=? ORDER BY ts DESC LIMIT 2',
                        (evaluation['coin_id'], evaluation['market_ts'])).fetchall()
    original = db.execute('SELECT features_json FROM candidate_evaluations WHERE id=?', (evaluation.get('v11_evaluation_id'),)).fetchone()
    captured = (json.loads(original[0]).get('observation_timing', {}) if original and original[0] else {})
    computed = captured.get('candidate_computed_ts')
    return dict(evaluation_id=evaluation['id'], detected_ts=evaluation['detected_ts'],
        candidate_computed_ts=computed,
        market_to_computed_seconds=(dt(computed)-dt(evaluation['market_ts'])).total_seconds() if computed else None,
        market_ts=evaluation['market_ts'], decision=evaluation['decision'], reason=evaluation['reason'],
        score=evaluation['pre_score'], threshold=threshold,
        threshold_shortfall=max(0, threshold-evaluation['pre_score']) if threshold is not None and evaluation['pre_score'] is not None else None,
        history_count=market.get('history_count'), components=market.get('components'),
        volume_z=market.get('volume_z'), volume_vs_baseline=market.get('volume_vs_baseline'),
        change_24h=market.get('change_24h'),
        min_history_points=cfg.get('filters', {}).get('min_history_points'),
        anomaly_threshold=cfg.get('filters', {}).get('anomaly_threshold'),
        market_to_detection_seconds=(dt(evaluation['detected_ts'])-dt(evaluation['market_ts'])).total_seconds(),
        preceding_market_interval_seconds=(dt(points[0]['ts'])-dt(points[1]['ts'])).total_seconds() if len(points)==2 else None,
        source_age_seconds=(dt(points[0]['ts'])-dt(points[0]['source_updated_ts'])).total_seconds() if points and points[0]['source_updated_ts'] else None,
        scan_duration_seconds=(dt(scan['completed_ts'])-dt(scan['started_ts'])).total_seconds() if scan and scan['completed_ts'] else None)


def timing(db, as_of):
    result = []
    for raw in db.execute('SELECT * FROM research_episodes WHERE started_ts<=? ORDER BY id', (as_of,)):
        e = dict(raw)
        move = e['significant_move_ts'] if e['significant_move_ts'] and dt(e['significant_move_ts']) <= dt(as_of) else None
        end = min(dt(as_of), dt(e['anchor_ts'])+timedelta(hours=e['move_window_hours']))
        actual = db.execute('''SELECT ts FROM market_observations WHERE coin_id=? AND ts>=? AND ts<=?
            AND price>=? ORDER BY ts LIMIT 1''', (e['coin_id'], e['anchor_ts'], end.isoformat(),
            e['anchor_price']*(1+e['significant_move_pct']/100))).fetchone()
        news_end = min(e['last_signal_ts'], as_of)
        news = db.execute('''SELECT first_observed_ts,publication_ts FROM news_evidence WHERE coin_id=?
            AND first_observed_ts>=? AND first_observed_ts<=? ORDER BY first_observed_ts LIMIT 1''',
            (e['coin_id'], e['anchor_ts'], news_end)).fetchone()
        ape = db.execute('''SELECT s.observed_ts FROM apewisdom_snapshots s JOIN attention_runs r ON r.id=s.run_id
            WHERE s.coin_id=? AND r.provider='apewisdom' AND r.complete=1 AND r.status='available'
            AND s.mentions IS NOT NULL AND s.observed_ts>=? AND s.observed_ts<=? AND r.observed_ts<=?
            ORDER BY s.observed_ts LIMIT 1''', (e['coin_id'], e['anchor_ts'], news_end, as_of)).fetchone()
        recorded = {name: e['first_'+name+'_ts'] if e['first_'+name+'_ts'] and dt(e['first_'+name+'_ts'])<=dt(as_of) else None
                    for name in ('market_anomaly','v11_candidate','news_event')}
        signals = dict(news_observation=news['first_observed_ts'] if news else None,
            apewisdom_observation=ape[0] if ape else None, **recorded)
        leads = {name: minutes(ts, move) for name, ts in signals.items()}
        row = dict(episode_id=e['id'], coin_id=e['coin_id'], started_ts=e['started_ts'],
            last_signal_ts=min(e['last_signal_ts'], as_of), anchor_ts=e['anchor_ts'],
            definition_version=e['definition_version'], significant_move_ts=move,
            recomputed_move_ts=actual[0] if actual else None,
            move_reproduced=move==(actual[0] if actual else None), signals=signals, lead_minutes=leads,
            news_publication_to_receipt_seconds=(dt(news['first_observed_ts'])-dt(news['publication_ts'])).total_seconds() if news and news['publication_ts'] else None)
        if move:
            row['last_pre_move_evaluation'] = diagnostic(db, first_evaluation(db, e['id'], as_of, before=move))
            row['first_candidate_evaluation'] = diagnostic(db, first_evaluation(db, e['id'], as_of, candidate=True))
            previous = db.execute('SELECT ts FROM market_observations WHERE coin_id=? AND ts<? ORDER BY ts DESC LIMIT 1', (e['coin_id'],move)).fetchone()
            row['move_crossing_bracket_seconds'] = (dt(move)-dt(previous[0])).total_seconds() if previous else None
        result.append(row)
    moved = [r for r in result if r['significant_move_ts']]
    candidate = [r for r in moved if r['lead_minutes']['v11_candidate'] is not None]
    lead = [r['lead_minutes']['v11_candidate'] for r in candidate]
    historical = [r for r in moved if dt(r['significant_move_ts']) < dt('2026-10-30T00:00:00+00:00')]
    historical_leads = [r['lead_minutes']['v11_candidate'] for r in historical if r['lead_minutes']['v11_candidate'] is not None]
    late = [r for r in candidate if r['lead_minutes']['v11_candidate'] < 0]
    chronology = {}
    for name in ('news_observation','market_anomaly','v11_candidate','apewisdom_observation'):
        values=[r['lead_minutes'][name] for r in moved if r['lead_minutes'][name] is not None]
        chronology[name]=dict(summary(values),missing_episodes=len(moved)-len(values),
            early=sum(v>0 for v in values),simultaneous=sum(v==0 for v in values),late=sum(v<0 for v in values))
    return dict(episodes=result, significant_move_episodes=len(moved), v11_candidate_episodes=len(candidate),
        early=sum(v>0 for v in lead), simultaneous=sum(v==0 for v in lead), late=sum(v<0 for v in lead),
        early_lead_minutes=summary([v for v in lead if v>0]),
        signal_chronology=chronology,
        move_recomputation_mismatches=[r['episode_id'] for r in moved if not r['move_reproduced']],
        historical_oct29=dict(significant_move_episodes=len(historical), v11_candidate_episodes=len(historical_leads),
            early=sum(v>0 for v in historical_leads), simultaneous=sum(v==0 for v in historical_leads),
            nonnegative_lead_minutes=summary([v for v in historical_leads if v>=0]),
            early_lead_minutes=summary([v for v in historical_leads if v>0])),
        late_pre_move_reasons=dict(Counter((r['last_pre_move_evaluation'] or {}).get('reason') or 'unrecorded' for r in late)),
        late_pre_move_diagnostics=[dict(episode_id=r['episode_id'], **(r['last_pre_move_evaluation'] or {})) for r in late])


def controls(db, as_of, episode_rows):
    groups = defaultdict(list)
    anchors = []
    for episode in episode_rows:
        e = first_evaluation(db, episode['episode_id'], as_of)
        if not e or e['start_price'] is None or e['start_price']<=0 or e['decision']=='excluded':
            continue
        cutoff = min(e['detected_ts'], e['evidence_cutoff_ts'])
        if dt(e['market_ts']) > dt(cutoff):
            continue
        news = db.execute('SELECT 1 FROM news_evidence WHERE coin_id=? AND first_observed_ts<=? AND first_observed_ts>=? LIMIT 1',
                          (e['coin_id'],cutoff,(dt(cutoff)-timedelta(hours=24)).isoformat())).fetchone()
        d = diagnostic(db,e)
        market = d['volume_z'] is not None and d['anomaly_threshold'] is not None and d['volume_z']>=d['anomaly_threshold']
        points = [dict(r) for r in db.execute('''SELECT s.*,r.status,r.complete FROM apewisdom_snapshots s
            JOIN attention_runs r ON r.id=s.run_id WHERE s.coin_id=? AND r.provider='apewisdom'
            AND s.observed_ts<=? AND r.observed_ts<=? ORDER BY s.observed_ts''',(e['coin_id'],cutoff,cutoff))]
        social = indicators(points,cutoff)
        f = social['latest_indicator']
        attention = bool(f and f['rolling_mentions_delta'] is not None and f['rolling_mentions_delta']>0)
        flags = [name for name, active in (('news', bool(news)), ('market', market), ('apewisdom', attention)) if active]
        label = '+'.join(flags) or 'no_observed_upstream_signal'
        anchor = dict(episode_id=episode['episode_id'],evaluation_id=e['id'],coin_id=e['coin_id'],asof_ts=cutoff,
            group=label, apewisdom_available=social['available'],
            lead_minutes=minutes(cutoff,episode['significant_move_ts']), horizons=[])
        for h in HORIZONS:
            o = db.execute('SELECT * FROM experiment_outcomes WHERE evaluation_id=? AND horizon_hours=?',(e['id'],h)).fetchone()
            valid = bool(o and o['timing_status']=='within_tolerance' and o['observed_ts']
                         and dt(o['observed_ts'])<=dt(as_of) and dt(o['target_ts'])>=dt(cutoff)
                         and dt(o['target_ts'])==dt(e['market_ts'])+timedelta(hours=h)
                         and 0 <= (dt(o['observed_ts'])-dt(o['target_ts'])).total_seconds() <= o['tolerance_seconds'])
            mismatch = None
            if valid and o['later_price'] and o['return_pct'] is not None:
                mismatch = abs(ret(e['start_price'],o['later_price'])-o['return_pct'])>1e-8
                valid = not mismatch
            anchor['horizons'].append(dict(hours=h, return_recomputation_mismatch=mismatch,
                **{m:o[m] if valid else None for m in METRICS}))
        anchors.append(anchor)
        groups[label].append(anchor)
    output = []
    social_episodes=sum('apewisdom' in a['group'] for a in anchors)
    for label in ('no_observed_upstream_signal','news','market','apewisdom','news+apewisdom',
                  'market+apewisdom','news+market','news+market+apewisdom'):
        members=groups[label]
        horizons=[]
        for h in HORIZONS:
            stats={}
            for m in METRICS:
                vals=[next(x for x in a['horizons'] if x['hours']==h)[m] for a in members]
                measured=[v for v in vals if v is not None]
                suppressed='apewisdom' in label and social_episodes<20
                stats[m]=dict(summary([] if suppressed else measured),missing_episodes=len(vals)-len(measured),
                    measured_episodes=len(measured), estimate_suppressed=suppressed,
                    positive_frequency=sum(v>0 for v in measured)/len(measured) if measured and not suppressed else None,
                    uncertainty=None,uncertainty_reason='Asset and time dependence; no validated independent-block inference.')
            horizons.append(dict(hours=h,metrics=stats))
        output.append(dict(group=label,independent_episode_ids=len(members),distinct_assets=len({a['coin_id'] for a in members}),
            missing_apewisdom_episodes=sum(not a['apewisdom_available'] for a in members),
            lead_minutes=summary([a['lead_minutes'] for a in members]),horizons=horizons))
    # Exact asset/day strata: expose lack of overlap rather than invent adjusted effects.
    strata=defaultdict(set)
    for a in anchors:
        strata[a['coin_id'],a['asof_ts'][:10]].add(a['group'])
    overlap=sum(a['coin_id']==b['coin_id'] and abs((dt(a['asof_ts'])-dt(b['asof_ts'])).total_seconds())<86400
                for i,a in enumerate(anchors) for b in anchors[i+1:])
    return dict(status='insufficient_social_data' if social_episodes<20 else 'descriptive_only',
        social_positive_episodes=social_episodes,groups=output,anchors=anchors,
        matched_asset_day_strata=len(strata),mixed_group_matched_strata=sum(len(g)>1 for g in strata.values()),
        overlapping_same_asset_24h_anchor_pairs=overlap,
        matching_limit='No adjusted comparison without prespecified regime matching and sufficient common support. No-signal means no observed signal, not verified absence.',
        selection='Earliest retained V1.1 evaluation per existing episode, fixed before outcomes; excluded or invalid first evaluations are omitted, never replaced by a later favorable sample.')


def build_report(path, as_of=None):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=30)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        required={'research_episodes','experiment_evaluations','experiment_outcomes','attention_runs',
                  'apewisdom_snapshots','collector_runs','scan_runs','market_observations','news_evidence'}
        if required-names:
            raise ValueError('Missing required relations: '+', '.join(sorted(required-names)))
        latest=db.execute("SELECT * FROM scan_runs WHERE status='completed' ORDER BY started_ts DESC,id DESC LIMIT 1").fetchone()
        if not latest and not as_of:
            raise ValueError('No completed scan; supply --as-of')
        end=dt(as_of or latest['completed_ts'] or latest['started_ts']).isoformat()
        scan=db.execute('SELECT * FROM scan_runs WHERE started_ts<=? ORDER BY started_ts DESC,id DESC LIMIT 1',(end,)).fetchone()
        cfg=json.loads(scan['config_json']) if scan else {}
        social_cfg=cfg.get('v12',{}).get('social',{})
        ape_cfg=social_cfg.get('provider_options',{}).get('apewisdom',{})
        h=health(db,end,ape_cfg)
        t=timing(db,end)
        c=controls(db,end,t['episodes'])
        return dict(report_version=1,as_of=end,source_schema_version=db.execute('PRAGMA user_version').fetchone()[0],
            runtime=dict(scan_id=scan['id'] if scan else None, v12_enabled=cfg.get('v12',{}).get('enabled'),
                social_enabled=social_cfg.get('enabled'),providers=social_cfg.get('providers',[]),
                apewisdom_enabled=ape_cfg.get('enabled',False),access_confirmed=ape_cfg.get('access_confirmed',False),
                configured_assets=len(ape_cfg.get('asset_map',{})),poll_seconds=ape_cfg.get('poll_seconds',1800),
                permission_evidence='Operator flag only; provider permission and numeric rate limits are not independently verified.'),
            health=h,timing=t,comparisons=c,
            decision='B: correct/monitor asset resolution and data availability; continue observation. No detector change justified.',
            warnings=[
                'Live rows are observed receipts, not proof of provider update time or coverage of all social activity.',
                'No social performance claim: require prospective resolved history, baseline maturity and independent episodes.',
                'Rolling deltas include expirations and corrections; negative values are retained, never interval post counts.',
                'Existing episode IDs are the sampling unit, but assets/windows can remain correlated; no inferential confidence intervals.',
                'Chronology summaries are retrospective; cohort membership uses one fixed as-of evaluation and never future signals.',
                'Move is the first sampled upward threshold crossing from the fixed anchor within its configured window, not true continuous-price onset.',
                'Historical cutoff cannot undo retrospectively changed episode membership or recover pruned detail.',
                'As-of observed returns only, within tolerance; missing benchmarks and observations remain null.'
            ])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--as-of',help='Timezone-aware cutoff; defaults to latest completed scan')
    args=parser.parse_args()
    try:
        result=build_report(args.db,args.as_of)
    except (sqlite3.Error,ValueError,OSError,KeyError) as exc:
        parser.exit(2,f'Cannot build validation report: {exc}\n')
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))


if __name__=='__main__':
    main()
