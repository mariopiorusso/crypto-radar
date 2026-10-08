"""Deterministic descriptive episode statistics from read-only schema-v2 snapshots."""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from statistics import mean, median


HORIZONS = (1, 3, 6, 12, 24)
METRICS = ('return_pct', 'excess_btc_pct', 'excess_eth_pct', 'excess_broad_pct')
SIGNALS = ('social_anomaly', 'news_event', 'market_anomaly', 'v11_candidate', 'v12_candidate')


def describe(values, total):
    values = [v for v in values if v is not None]
    return dict(measured_episodes=len(values), missing_episodes=total-len(values),
                mean=mean(values) if values else None,
                median=median(values) if values else None)


def bounds(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return dict(start=min(values, key=datetime.fromisoformat) if values else None,
                end=max(values, key=datetime.fromisoformat) if values else None)


def build_report(path):
    # Original relation names also decode compact snapshot text dictionaries.
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')  # All sections see one consistent read transaction.
        return _report(db)


def _report(db):
    episodes = {r['id']: dict(r) for r in db.execute('SELECT * FROM research_episodes ORDER BY id')}
    membership = defaultdict(list)
    unassigned = 0
    for r in db.execute('''SELECT signal_version,experiment_group,episode_id,
            count(*) AS evaluations, min(detected_ts) AS first_detection,
            max(detected_ts) AS last_detection FROM experiment_evaluations
            GROUP BY signal_version,experiment_group,episode_id
            ORDER BY signal_version,experiment_group,episode_id'''):
        if r['episode_id'] not in episodes:
            unassigned += r['evaluations']
        else:
            membership[r['signal_version'], r['experiment_group']].append(dict(r))
    # LEFT JOIN preserves absent outcomes; every linked evaluation is in the denominator.
    aggregates = defaultdict(list)
    metrics = ','.join(f'avg(o.{m}) AS {m},count(o.{m}) AS n_{m}' for m in METRICS)
    for h in HORIZONS:
        query = f'''SELECT e.signal_version,e.experiment_group,e.episode_id,count(*) AS evaluations,
            count(o.evaluation_id) AS outcome_rows,{metrics},
            avg(abs(o.timing_error_seconds)) AS timing_error,
            sum(CASE WHEN o.timing_status='within_tolerance' THEN 1 ELSE 0 END) AS on_time,
            sum(CASE WHEN o.timing_status='late' THEN 1 ELSE 0 END) AS late,
            sum(CASE WHEN o.timing_status='pending' THEN 1 ELSE 0 END) AS pending,
            sum(CASE WHEN o.timing_status='missing' THEN 1 ELSE 0 END) AS missing,
            sum(CASE WHEN o.timing_status NOT IN ('within_tolerance','late','pending','missing')
                THEN 1 ELSE 0 END) AS other
            FROM experiment_evaluations e LEFT JOIN experiment_outcomes o
            ON o.evaluation_id=e.id AND o.horizon_hours=?
            JOIN research_episodes p ON p.id=e.episode_id
            GROUP BY e.signal_version,e.experiment_group,e.episode_id
            ORDER BY e.signal_version,e.experiment_group,e.episode_id'''
        for r in db.execute(query, (h,)):
            aggregates[r['signal_version'], r['experiment_group'], h].append(dict(r))
    groups = []
    incomplete = False
    for (version, group), members in sorted(membership.items()):
        cohort = [episodes[r['episode_id']] for r in members]
        n = len(cohort)
        horizons = []
        for h in HORIZONS:
            rows = aggregates[version, group, h]
            stats = {}
            for metric in METRICS:
                stats[metric] = describe([r[metric] for r in rows], n)
                stats[metric]['partially_measured_episodes'] = sum(
                    0 < r['n_'+metric] < r['evaluations'] for r in rows)
                stats[metric]['measured_evaluations'] = sum(r['n_'+metric] for r in rows)
                stats[metric]['missing_evaluations'] = sum(
                    r['evaluations']-r['n_'+metric] for r in rows)
                incomplete |= stats[metric]['missing_evaluations'] > 0
            timing = {s: dict(evaluations=sum(r[s] for r in rows),
                             episodes=sum(r[s] > 0 for r in rows))
                      for s in ('on_time', 'late', 'pending', 'missing', 'other')}
            timing['absent'] = dict(evaluations=sum(r['evaluations']-r['outcome_rows'] for r in rows),
                                   episodes=sum(r['evaluations'] > r['outcome_rows'] for r in rows))
            timing['absolute_error_seconds'] = describe([r['timing_error'] for r in rows], n)
            horizons.append(dict(horizon_hours=h, distinct_episodes=n, metrics=stats, timing=timing))
        chronology = {}
        for signal in SIGNALS:
            values = [(datetime.fromisoformat(e['significant_move_ts'])-
                       datetime.fromisoformat(e['first_'+signal+'_ts'])).total_seconds()/60
                      for e in cohort if e['significant_move_ts'] and e['first_'+signal+'_ts']]
            chronology[signal] = dict(describe(values, n),
                signal_missing_episodes=sum(e['first_'+signal+'_ts'] is None for e in cohort),
                move_missing_episodes=sum(e['significant_move_ts'] is None for e in cohort),
                before_move=sum(v > 0 for v in values), at_move=sum(v == 0 for v in values),
                after_move=sum(v < 0 for v in values))
        groups.append(dict(signal_version=version, experiment_group=group, distinct_episodes=n,
            evaluations=sum(r['evaluations'] for r in members),
            episode_starts=bounds(cohort, 'started_ts'), last_signals=bounds(cohort, 'last_signal_ts'),
            first_detections=bounds(members, 'first_detection'),
            last_detections=bounds(members, 'last_detection'),
            horizons=horizons, signal_to_move_minutes=chronology))
    collectors = [dict(r) for r in db.execute('''SELECT collector,status,count(*) AS runs,
        sum(item_count) AS recorded_items,min(observed_ts) AS start,max(observed_ts) AS end
        FROM collector_runs GROUP BY collector,status ORDER BY collector,status''')]
    social = dict(db.execute('''SELECT count(*) AS observation_rows,
        sum(CASE WHEN mentions=0 THEN 1 ELSE 0 END) AS observed_zero_rows,
        min(observed_ts) AS start,max(observed_ts) AS end FROM social_observations''').fetchone())
    social['observed_zero_rows'] = social['observed_zero_rows'] or 0
    social['providers']=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_social_observations'").fetchone():
        social['providers']=[dict(r) for r in db.execute('''SELECT provider,availability,count(*) observation_rows,
            sum(CASE WHEN mentions=0 THEN 1 ELSE 0 END) observed_zero_rows,
            sum(CASE WHEN mentions IS NULL THEN 1 ELSE 0 END) unavailable_mentions,
            min(observed_ts) start,max(observed_ts) end
            FROM provider_social_observations GROUP BY provider,availability ORDER BY provider,availability''')]
        social['observation_rows']+=sum(r['observation_rows'] for r in social['providers'])
        social['observed_zero_rows']+=sum(r['observed_zero_rows'] for r in social['providers'])
        starts=[r['start'] for r in social['providers'] if r['start']]+([social['start']] if social['start'] else [])
        ends=[r['end'] for r in social['providers'] if r['end']]+([social['end']] if social['end'] else [])
        social['start']=min(starts) if starts else None
        social['end']=max(ends) if ends else None
        social['sources']=[dict(r) for r in db.execute('''SELECT provider,source,availability,
            count(*) observation_rows,sum(mentions IS NULL) missing_mentions,sum(mentions=0) zero_mentions,
            sum(unique_authors IS NULL) missing_authors,sum(engagement IS NULL) missing_engagement,
            min(window_start) first_event_window,min(receipt_ts) first_receipt,max(receipt_ts) last_receipt,
            min(CASE WHEN mentions>0 THEN receipt_ts END) first_attention_receipt
            FROM provider_social_observations GROUP BY provider,source,availability ORDER BY provider,source,availability''')]
        social['chronology_note']='First attention receipt is availability of measured mentions, not an anomaly or a predictive signal.'
    social['feature_statuses'] = [dict(r) for r in db.execute('''SELECT status,count(*) AS rows,
        sum(CASE WHEN mentions_5m IS NULL THEN 1 ELSE 0 END) AS missing_5m,
        sum(CASE WHEN mentions_5m=0 THEN 1 ELSE 0 END) AS recorded_zero_5m
        FROM social_features GROUP BY status ORDER BY status''')]
    warnings = [
        'Descriptive only: no predictive claims or roadmap advancement. Episodes can share assets and overlapping windows; groups are not independent arms.',
        'Only retained detailed rows are analyzed; older compact aggregates cannot reconstruct episode distributions.',
        'Returns include late measurements. Timing status counts can overlap within episodes; inspect timing and partial coverage before comparisons.',
        'Missing move timestamps do not prove no move: observation gaps and incomplete follow-up can hide crossings.',
        'Freeze a forward cohort and prespecified controls; the protocol suggests about four weeks and roughly 500 reasonably independent episodes before initial comparison, not an automatic gate.'
    ]
    if incomplete:
        warnings.append('Outcome or benchmark coverage is incomplete; available-case denominators differ and can bias comparisons.')
    if unassigned:
        warnings.append(f'{unassigned} evaluations lack a valid episode and are excluded, never treated as independent episodes.')
    if not social['observation_rows']:
        warnings.append('No social observations: social activity is unavailable/missing, not measured zero.')
    if any(r['status'] != 'ok' for r in social['feature_statuses']):
        warnings.append('Social features include unavailable or limited-quality statuses; recorded zeros do not override these statuses.')
    if not collectors:
        warnings.append('No collector runs recorded; collector availability is unknown.')
    if not groups:
        warnings.append('No evaluable episode cohorts.')
    return dict(report_version=1, methodology={
        'weighting': 'Mean available evaluations per episode/version/group/horizon, then equal-weight mean and median of episode means, independently per metric.',
        'absolute_return': 'Signed unadjusted return_pct in percent, not the magnitude of return.',
        'lead_lag': 'Minutes from first recorded episode signal to recorded significant move; positive means signal preceded move. One pair per episode, independent of horizon.',
        'availability_scope': 'Collector and social row counts cover all retained detail, not independent research samples.',
        'selection': 'All evaluations with valid episode links, including rejected/excluded decisions; no eligibility filter.'},
        retained_episodes=len(episodes), cohort_starts=bounds(list(episodes.values()), 'started_ts'),
        cohort_last_signals=bounds(list(episodes.values()), 'last_signal_ts'),
        excluded_unassigned_evaluations=unassigned, groups=groups, collectors=collectors,
        social=social, warnings=warnings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Existing full or compact SQLite snapshot (read-only)')
    args = parser.parse_args()
    try:
        report = build_report(args.db)
    except (sqlite3.Error, ValueError, OSError) as exc:
        parser.exit(2, f'Cannot report snapshot: {exc}\n')
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
