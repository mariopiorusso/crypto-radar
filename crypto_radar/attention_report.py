"""Read-only, as-of descriptive attention cohorts; no production signal scoring."""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from statistics import mean, median

from .episode_report import HORIZONS, METRICS


def features(db, coin, cutoff, max_age_seconds=900):
    """Availability is evidence received by cutoff, never retrospective event time."""
    end=datetime.fromisoformat(cutoff); lower=(end-timedelta(seconds=max_age_seconds)).isoformat()
    ape=db.execute('''SELECT s.*,r.status,r.complete FROM apewisdom_snapshots s
        JOIN attention_runs r ON r.id=s.run_id WHERE s.coin_id=? AND s.observed_ts<=?
        AND s.observed_ts>=? ORDER BY s.observed_ts DESC LIMIT 1''',(coin,cutoff,lower)).fetchone()
    ape_ok=bool(ape and ape['status']=='available' and ape['complete'] and ape['mentions'] is not None)
    delta=None; rank_delta=None; upvote_delta=None
    if ape_ok:
        previous=db.execute('''SELECT s.* FROM apewisdom_snapshots s JOIN attention_runs r ON r.id=s.run_id
            WHERE s.coin_id=? AND s.observed_ts<? AND s.observed_ts>=?
            AND r.complete=1 AND r.status='available' ORDER BY s.observed_ts DESC LIMIT 1''',
            (coin,ape['observed_ts'],(datetime.fromisoformat(ape['observed_ts'])-timedelta(seconds=max_age_seconds)).isoformat())).fetchone()
        if previous:
            def difference(name):
                return ape[name]-previous[name] if ape[name] is not None and previous[name] is not None else None
            delta=difference('mentions'); rank_delta=difference('rank'); upvote_delta=difference('upvotes')
    run=db.execute('''SELECT * FROM attention_runs WHERE provider='x' AND query_key=? AND observed_ts<=?
        AND observed_ts>=? ORDER BY observed_ts DESC,id DESC LIMIT 1''',(coin,cutoff,lower)).fetchone()
    x_ok=bool(run and run['complete'] and run['status']=='available')
    count=None; velocity=None; engagement_delta=None; delayed=None
    if x_ok:
        rows=db.execute('''SELECT p.* FROM x_posts p JOIN x_post_assets a ON a.post_id=p.post_id
            WHERE a.coin_id=? AND a.first_seen_ts<=? AND p.first_seen_ts<=?
            AND p.event_ts>=? AND p.event_ts<?''',
            (coin,cutoff,cutoff,run['window_start'],run['window_end'])).fetchall()
        count=len(rows)
        delayed=sum((datetime.fromisoformat(r['first_seen_ts'])-datetime.fromisoformat(r['event_ts'])).total_seconds()>max_age_seconds for r in rows)
        duration=(datetime.fromisoformat(run['window_end'])-datetime.fromisoformat(run['window_start'])).total_seconds()
        velocity=count/(duration/60) if duration>0 else None
        changes=[]
        for row in rows:
            metrics=db.execute('''SELECT max(likes),max(reposts),max(replies),max(quotes) FROM x_post_metrics WHERE post_id=?
                AND observed_ts<=? GROUP BY observed_ts ORDER BY observed_ts DESC LIMIT 2''',(row['post_id'],cutoff)).fetchall()
            if len(metrics)==2 and all(v is not None for m in metrics for v in m):
                changes.append(sum(metrics[0])-sum(metrics[1]))
        engagement_delta=sum(changes) if changes else None
    return dict(apewisdom_observed=ape_ok,x_observed=x_ok,rolling_mentions_delta=delta,
        rolling_rank_delta=rank_delta,rolling_upvotes_delta=upvote_delta,
        x_observed_posts=count,x_observed_posts_per_minute=velocity,
        x_engagement_delta=engagement_delta,x_delayed_posts=delayed,
        x_gap_seconds=run['gap_seconds'] if run else None,
        x_search_status=run['status'] if run else 'unavailable')


def report(db, max_age_seconds=900):
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    if 'attention_runs' not in names:
        return {'status':'unavailable','cohorts':[]}
    cohorts=defaultdict(list); signals=[]
    for evaluation in db.execute('SELECT * FROM experiment_evaluations ORDER BY id'):
        if evaluation['episode_id'] is None:
            continue
        # Both timestamps constrain the information set.
        cutoff=min(evaluation['detected_ts'],evaluation['evidence_cutoff_ts'])
        saved=json.loads(evaluation['features_json']).get('attention_evidence')
        f=(saved['values'] if saved and saved['asof_ts']==cutoff and saved['max_age_seconds']==max_age_seconds
           else features(db,evaluation['coin_id'],cutoff,max_age_seconds))
        group=('both' if f['apewisdom_observed'] and f['x_observed'] else
               'apewisdom_only' if f['apewisdom_observed'] else 'x_only' if f['x_observed'] else 'neither')
        cohorts[group].append((dict(evaluation),f))
        if (f['rolling_mentions_delta'] or 0)>0 or (f['x_observed_posts'] or 0)-(f['x_delayed_posts'] or 0)>0:
            signals.append((evaluation['episode_id'],cutoff))
    output=[]
    for name in ('apewisdom_only','x_only','both','neither'):
        members=cohorts[name]; episode_ids={e['episode_id'] for e,f in members}; horizons=[]
        for h in HORIZONS:
            values={metric:defaultdict(list) for metric in METRICS}
            for e,f in members:
                outcome=db.execute("SELECT * FROM experiment_outcomes WHERE evaluation_id=? AND horizon_hours=? AND timing_status='within_tolerance'",(e['id'],h)).fetchone()
                if outcome:
                    for metric in METRICS:
                        if outcome[metric] is not None:
                            values[metric][e['episode_id']].append(outcome[metric])
            summary={}
            for metric,episodes in values.items():
                means=[mean(v) for v in episodes.values()]
                summary[metric]=dict(measured_episodes=len(means),missing_episodes=len(episode_ids)-len(means),
                    mean=mean(means) if means else None,median=median(means) if means else None)
            horizons.append(dict(hours=h,metrics=summary))
        output.append(dict(cohort=name,episodes=len(episode_ids),evaluations=len(members),
            news_observed_evaluations=sum(bool(db.execute('SELECT 1 FROM evaluation_news WHERE evaluation_id=? LIMIT 1',(e['id'],)).fetchone()) for e,f in members),
            feature_observations=[dict(evaluation_id=e['id'],**f) for e,f in members],horizons=horizons))
    first={}
    for episode,ts in signals:
        first[episode]=min(first.get(episode,ts),ts)
    timing=[]
    for episode,ts in sorted(first.items()):
        row=db.execute('SELECT first_market_anomaly_ts FROM research_episodes WHERE id=?',(episode,)).fetchone()
        market=row[0] if row else None
        timing.append(dict(episode_id=episode,attention_first_available_ts=ts,market_anomaly_ts=market,
            lead_minutes=(datetime.fromisoformat(market)-datetime.fromisoformat(ts)).total_seconds()/60 if market else None))
    usage=[dict(r) for r in db.execute('''SELECT provider,billing_period,currency,sum(requests) requests,
        sum(coalesce(resources,resources_reserved)) resources_or_reserved,
        sum(coalesce(estimated_microusd,reserved_microusd))/1000000.0 estimated_or_reserved_usd,
        sum(status IN ('reserved','uncertain')) uncertain_requests FROM provider_usage GROUP BY 1,2,3''')]
    states=[dict(r) for r in db.execute('SELECT provider,status,count(*) runs FROM attention_runs GROUP BY 1,2')]
    total=sum(len(v) for v in cohorts.values())
    missing={p:sum(not f[p+'_observed'] for rows in cohorts.values() for e,f in rows)/total if total else None for p in ('apewisdom','x')}
    return dict(status='descriptive',cohorts=output,attention_to_volume_anomaly=timing,usage=usage,collection=states,
        missing_rates=missing,methodology={
            'as_of':'Evidence received by min(detection,evidence cutoff); historical comparison fields never backdated.',
            'rolling':'ApeWisdom changes are rolling-window deltas, never interval mention counts; repeated responses are freshness-unknown.',
            'x':'Velocity is observed search-result density, never an estimate of total X activity; delayed posts are identified.',
            'weighting':'Average evaluations per episode/cohort/horizon then weight episodes equally. Episodes may appear in multiple cohorts; cohorts are not independent experiments.',
            'outcomes':'Only within-tolerance measurements. Missing benchmarks stay null. No predictive significance claim.',
            'retention':'Scanner evaluations freeze numeric as-of features for later outcomes. Alternate freshness windows and older evaluations require retained raw evidence; removed raw evidence is unavailable.'})


def build_report(path, max_age_seconds=900):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        return report(db,max_age_seconds)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--max-age-seconds',type=int,default=900)
    args=parser.parse_args()
    if args.max_age_seconds<=0:
        parser.error('Freshness window must be positive')
    print(json.dumps(build_report(args.db,args.max_age_seconds),indent=2,allow_nan=False))


if __name__=='__main__':
    main()
