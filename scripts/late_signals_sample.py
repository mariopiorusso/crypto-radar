"""Extract a deterministic, point-in-time late-signal audit from a snapshot."""
import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamps must include a timezone')
    return result.astimezone(timezone.utc)


def select_episodes(rows, cutoff, limit=10):
    end = instant(cutoff)
    selected = []
    for row in rows:
        e = dict(row)
        names = ('anchor_ts', 'significant_move_ts', 'first_market_anomaly_ts', 'first_v11_candidate_ts')
        if not all(e.get(name) for name in names):
            continue
        anchor, move, anomaly, candidate = (instant(e[name]) for name in names)
        if anomaly < move < candidate <= end and anchor <= move:
            selected.append(e)
    selected.sort(key=lambda e: (instant(e['anchor_ts']), e['coin_id'], e['id']))
    return selected[:limit]


def available_evaluation(rows, move):
    """Latest evaluation whose inputs and completed scan existed before crossing."""
    event = instant(move)
    eligible = []
    for raw in rows:
        row = dict(raw)
        times = ('detected_ts', 'evidence_cutoff_ts', 'market_ts', 'started_ts', 'completed_ts')
        if all(row.get(t) and instant(row[t]) < event for t in times):
            eligible.append(row)
    return max(eligible, key=lambda r: (instant(r['detected_ts']), r['id']), default=None)


def build(db, cutoff):
    episodes = select_episodes(db.execute('SELECT * FROM research_episodes'), cutoff)
    result = []
    for e in episodes:
        end = (instant(e['anchor_ts']) + timedelta(hours=e['move_window_hours'])).isoformat()
        crossing = db.execute('''SELECT ts FROM market_observations WHERE coin_id=? AND ts>=?
            AND ts<=? AND price>=? ORDER BY ts LIMIT 1''',
            (e['coin_id'], e['anchor_ts'], end,
             e['anchor_price'] * (1 + e['significant_move_pct'] / 100))).fetchone()
        if not crossing or instant(crossing['ts']) != instant(e['significant_move_ts']):
            raise ValueError(f"Move crossing does not reproduce for episode {e['id']}")
        rows = db.execute('''SELECT e.*,s.started_ts,s.completed_ts,s.config_json
            FROM experiment_evaluations e JOIN scan_runs s ON s.id=e.scan_id
            WHERE e.episode_id=? AND e.signal_version='1.1' ''', (e['id'],))
        evaluation = available_evaluation(rows, e['significant_move_ts'])
        row = dict(source='2026-11-23 snapshot manifest 1iFe5mlm0QbEVcey7q5TYkzQ9aPJvzQqs',
            episode_id=e['id'], coin_id=e['coin_id'], definition=e['definition_version'],
            fixed_anchor_utc=e['anchor_ts'], move_crossing_utc=e['significant_move_ts'],
            market_anomaly_utc=e['first_market_anomaly_ts'], v11_candidate_utc=e['first_v11_candidate_ts'],
            anomaly_to_crossing_minutes=(instant(e['significant_move_ts'])-instant(e['first_market_anomaly_ts'])).total_seconds()/60,
            candidate_after_crossing_minutes=(instant(e['first_v11_candidate_ts'])-instant(e['significant_move_ts'])).total_seconds()/60)
        if evaluation:
            cfg = json.loads(evaluation['config_json']); features = json.loads(evaluation['features_json'] or '{}')
            market = features.get('market_features') or {}
            row.update(evaluation_id=evaluation['id'], evaluation_utc=evaluation['detected_ts'],
                evaluation_completed_utc=evaluation['completed_ts'], evidence_cutoff_utc=evaluation['evidence_cutoff_ts'],
                market_utc=evaluation['market_ts'], decision=evaluation['decision'], reason=evaluation['reason'],
                score=evaluation['pre_score'], threshold=cfg.get('ai', {}).get('min_pre_score'),
                history_count=market.get('history_count'), volume_z=market.get('volume_z'),
                volume_vs_baseline=market.get('volume_vs_baseline'), components=json.dumps(market.get('components'),sort_keys=True))
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--cutoff', required=True)
    parser.add_argument('--csv', required=True)
    args = parser.parse_args()
    uri = Path(args.db).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows = build(db, args.cutoff)
    fields = ['source','episode_id','coin_id','definition','fixed_anchor_utc','move_crossing_utc',
              'market_anomaly_utc','v11_candidate_utc','anomaly_to_crossing_minutes',
              'candidate_after_crossing_minutes','evaluation_id','evaluation_utc','evaluation_completed_utc',
              'evidence_cutoff_utc','market_utc','decision','reason','score','threshold',
              'history_count','volume_z','volume_vs_baseline','components']
    with open(args.csv, 'w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    print(f'Wrote {len(rows)} episodes')


if __name__ == '__main__':
    main()
