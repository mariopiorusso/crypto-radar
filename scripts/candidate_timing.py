"""Describe recorded candidate timing without modifying the source database."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sqlite3
from scripts.late_signals_sample import instant, available_evaluation


def timing(episodes, key):
    values = [(instant(e[key]) - instant(e['significant_move_ts'])).total_seconds()
              for e in episodes if e[key]]
    return {'present': len(values), 'early': sum(v < 0 for v in values),
            'simultaneous': sum(v == 0 for v in values), 'late': sum(v > 0 for v in values)}


def target(e):
    move = instant(e['significant_move_ts'])
    return bool(e['first_market_anomaly_ts'] and instant(e['first_market_anomaly_ts']) < move
                and (not e['first_v11_candidate_ts'] or instant(e['first_v11_candidate_ts']) > move))


def build(db):
    episodes = [dict(e) for e in db.execute('SELECT * FROM research_episodes WHERE significant_move_ts IS NOT NULL ORDER BY id')]
    summary = {key: timing(episodes, key) for key in ('first_news_event_ts', 'first_market_anomaly_ts', 'first_v11_candidate_ts')}
    output = []
    for e in filter(target, episodes):
        rows = list(db.execute('''SELECT e.*, s.started_ts, s.completed_ts, s.config_json
            FROM experiment_evaluations e JOIN scan_runs s ON s.id=e.scan_id
            WHERE e.episode_id=? AND e.signal_version='1.1' ''', (e['id'],)))
        ev = available_evaluation(rows, e['significant_move_ts'])
        move = instant(e['significant_move_ts'])
        record = dict(episode_id=e['id'], coin_id=e['coin_id'], fixed_anchor=e['anchor_ts'], crossing=e['significant_move_ts'],
                      anomaly=e['first_market_anomaly_ts'], candidate=e['first_v11_candidate_ts'],
                      candidate_state='late' if e['first_v11_candidate_ts'] else 'not recorded',
                      anomaly_lead_minutes=(move-instant(e['first_market_anomaly_ts'])).total_seconds()/60)
        if ev:
            features = json.loads(ev['features_json'] or '{}').get('market_features') or {}
            config = json.loads(ev['config_json'] or '{}')
            record.update(evaluation_id=ev['id'], evaluation_detected=ev['detected_ts'], evaluation_completed=ev['completed_ts'],
                market_ts=ev['market_ts'], decision=ev['decision'], reason=ev['reason'], score=ev['pre_score'],
                threshold=config.get('ai', {}).get('min_pre_score'), history_count=features.get('history_count'),
                volume_vs_baseline=features.get('volume_vs_baseline'), volume_ratio_component=(features.get('components') or {}).get('volume_ratio'),
                minutes_since_completed=(move-instant(ev['completed_ts'])).total_seconds()/60,
                minutes_since_market=(move-instant(ev['market_ts'])).total_seconds()/60,
                configured_scan_seconds=config.get('scan_interval_seconds'))
        output.append(record)
    return summary, output


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--db', required=True); parser.add_argument('--csv', required=True)
    args = parser.parse_args()
    with sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        summary, rows = build(db)
    fields = ['episode_id','coin_id','fixed_anchor','crossing','anomaly','candidate','candidate_state','anomaly_lead_minutes',
              'evaluation_id','evaluation_detected','evaluation_completed','market_ts','decision','reason','score','threshold',
              'history_count','volume_vs_baseline','volume_ratio_component','minutes_since_completed','minutes_since_market','configured_scan_seconds']
    with open(args.csv,'w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    print(json.dumps({'timing':summary,'target':len(rows),'state':dict(Counter(r['candidate_state'] for r in rows)),
                      'evaluation':sum('evaluation_id' in r for r in rows),
                      'reason':dict(Counter(r.get('reason') or 'missing' for r in rows)),
                      'zero_volume_component':sum(r.get('volume_ratio_component') == 0 for r in rows)},indent=2))

if __name__ == '__main__': main()
