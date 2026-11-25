"""Describe historical ApeWisdom coverage before recorded move anchors."""
import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def instant(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return dt.astimezone(timezone.utc)


def prior_coverage(rows, anchor):
    """Count strictly prior mapped rows and changed complete runs, by receipt time."""
    t = instant(anchor)
    prior = [r for r in rows if instant(r['observed_ts']) < t and
             instant(r['run_ts']) < t]
    changed = [r for r in prior if r['status'] == 'available' and r['complete'] == 1]
    latest = max((instant(r['observed_ts']) for r in changed), default=None)
    latest_response = max(prior, key=lambda r: instant(r['observed_ts']), default=None)
    age = (t - latest).total_seconds() / 60 if latest else None
    result = {'mapped_rows': len(prior), 'changed_runs': len({r['run_id'] for r in changed}),
              'repeated_rows': sum(r['status'] == 'repeated' for r in prior),
              'other_rows': sum(r['status'] not in ('available', 'repeated') or not r['complete'] for r in prior),
              'latest_changed_utc': latest.isoformat() if latest else '',
              'latest_age_minutes': round(age, 3) if age is not None else ''}
    for threshold in (30, 60, 120):
        result[f'usable_{threshold}m'] = int(result['changed_runs'] >= 12 and age is not None and age <= threshold
            and latest_response is not None and latest_response['status'] == 'available' and latest_response['complete'] == 1)
    return result


def build(db):
    db.row_factory = sqlite3.Row
    episodes = db.execute('SELECT * FROM research_episodes WHERE significant_move_ts IS NOT NULL ORDER BY anchor_ts,coin_id,id').fetchall()
    snapshots = db.execute('''SELECT s.*,r.observed_ts AS run_ts,r.status,r.complete
        FROM apewisdom_snapshots s JOIN attention_runs r ON r.id=s.run_id
        WHERE r.provider='apewisdom' AND s.coin_id IS NOT NULL''').fetchall()
    by_coin = defaultdict(list)
    for row in snapshots:
        by_coin[row['coin_id']].append(dict(row))
    output = []
    for episode in episodes:
        coin = episode['coin_id']
        output.append(dict(episode_id=episode['id'], coin_id=coin, anchor_utc=episode['anchor_ts'],
            crossing_utc=episode['significant_move_ts'], **prior_coverage(by_coin[coin], episode['anchor_ts'])))
    return output


def gaps(db, rows):
    """Describe first uncovered episode for each frequently uncovered asset.

    A matching ticker is a lead for review, not proof of provider identity.
    """
    counts = Counter(r['coin_id'] for r in rows if not r['mapped_rows'])
    result = []
    for coin, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        first = next(r for r in rows if r['coin_id'] == coin and not r['mapped_rows'])
        market = db.execute('''SELECT symbol,name FROM market_observations
            WHERE coin_id=? AND ts<? ORDER BY ts DESC LIMIT 1''', (coin, first['anchor_utc'])).fetchone()
        ticker = market['symbol'].upper() + '.X' if market and market['symbol'] else ''
        ticker_rows = db.execute('''SELECT count(*) FROM apewisdom_snapshots s
            JOIN attention_runs r ON r.id=s.run_id WHERE r.provider='apewisdom'
            AND s.ticker=? AND s.observed_ts<? AND r.observed_ts<?''',
            (ticker, first['anchor_utc'], first['anchor_utc'])).fetchone()[0] if ticker else 0
        result.append(dict(coin_id=coin, uncovered_episodes=count, example_episode_id=first['episode_id'],
            example_anchor_utc=first['anchor_utc'], market_symbol=market['symbol'] if market else '',
            market_name=market['name'] if market else '', candidate_ticker=ticker,
            candidate_ticker_rows_before_anchor=ticker_rows))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--csv', required=True)
    parser.add_argument('--gaps', required=True)
    args = parser.parse_args()
    with sqlite3.connect(Path(args.db).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = build(db)
        gap_rows = gaps(db, rows)
    with open(args.csv, 'w', encoding='utf-8', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with open(args.gaps, 'w', encoding='utf-8', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=list(gap_rows[0])); writer.writeheader(); writer.writerows(gap_rows)
    print('episodes', len(rows), 'assets', len({r['coin_id'] for r in rows}))
    print('usable', {m: sum(r[f'usable_{m}m'] for r in rows) for m in (30, 60, 120)})
    print('no mapped prior', sum(not r['mapped_rows'] for r in rows))
    print('top no mapped prior', Counter(r['coin_id'] for r in rows if not r['mapped_rows']).most_common(15))


if __name__ == '__main__':
    main()
