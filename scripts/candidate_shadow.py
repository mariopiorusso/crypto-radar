"""Run the frozen, read-only candidate-shadow feasibility study."""
import argparse, csv, json, sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from scripts.late_signals_sample import instant

CUTOFF = '2026-11-26T19:47:11.507850Z'
START = '2026-11-24T19:54:18.610593Z'
END = '2026-11-25T19:47:11.507850Z'


def outcome(episode, prices):
    start = instant(episode['anchor_ts']); end = start + timedelta(hours=24)
    if not episode['anchor_price'] or episode['anchor_price'] <= 0:
        return 'unknown', None
    points = sorted((instant(t), p) for t, p in prices if p is not None and p > 0 and start <= instant(t) <= end)
    crossings = [t for t,p in points if t > start and p >= episode['anchor_price'] * 1.05]
    if crossings:
        return 'mover', crossings[0]
    times = [t for t,_ in points]
    if not times or times[0]-start > timedelta(minutes=15) or end-times[-1] > timedelta(minutes=15):
        return 'unknown', None
    if any(b-a > timedelta(minutes=15) for a,b in zip(times,times[1:])):
        return 'unknown', None
    return 'control', None


def shadow_time(rows, start, end):
    candidates=[]
    for row in rows:
        if not (row['reason']=='below_threshold' or (row['decision']=='assessed' and row['eligible'])):
            continue
        if row['pre_score'] is None or row['pre_score'] < 35:
            continue
        keys=('detected_ts','evidence_cutoff_ts','market_ts','started_ts','completed_ts')
        if not all(row[k] for k in keys):
            continue
        times=[instant(row[k]) for k in keys]; decision=times[-1]
        if all(t <= decision for t in times) and start <= decision <= end:
            candidates.append(decision)
    return min(candidates, default=None)


def build(db):
    eps=[dict(r) for r in db.execute('SELECT * FROM research_episodes ORDER BY id')]
    baseline={'crossings':0,'candidate_present':0,'candidate_early':0,'candidate_simultaneous':0,'candidate_late':0}
    for e in eps:
        if e['significant_move_ts'] and instant(e['significant_move_ts']) <= instant(CUTOFF):
            baseline['crossings']+=1
            if e['first_v11_candidate_ts'] and instant(e['first_v11_candidate_ts'])<=instant(CUTOFF):
                baseline['candidate_present']+=1
                delta=instant(e['first_v11_candidate_ts'])-instant(e['significant_move_ts'])
                baseline['candidate_early' if delta.total_seconds()<0 else 'candidate_simultaneous' if delta.total_seconds()==0 else 'candidate_late']+=1
    selected=[e for e in eps if e['anchor_ts'] and instant(START)<=instant(e['anchor_ts'])<=instant(END)]
    coins=sorted({e['coin_id'] for e in selected}); prices=defaultdict(list)
    if coins:
        query='SELECT coin_id,ts,price FROM market_observations WHERE coin_id IN ('+','.join('?'*len(coins))+') AND ts>=? AND ts<=?'
        for coin,t,p in db.execute(query,coins+[START,CUTOFF]): prices[coin].append((t,p))
    result=[]
    for e in selected:
        start=instant(e['anchor_ts']); end=start+timedelta(hours=24); kind,cross=outcome(e,prices[e['coin_id']])
        rows=[dict(r) for r in db.execute('''SELECT e.pre_score,e.reason,e.decision,e.eligible,e.detected_ts,e.evidence_cutoff_ts,e.market_ts,s.started_ts,s.completed_ts FROM experiment_evaluations e JOIN scan_runs s ON s.id=e.scan_id WHERE e.episode_id=? AND e.signal_version='1.1' ''',(e['id'],))]
        shadow=shadow_time(rows,start,end)
        recorded=instant(e['first_v11_candidate_ts']) if e['first_v11_candidate_ts'] else None
        if recorded and not (start<=recorded<=end and recorded<=instant(CUTOFF)): recorded=None
        overlaps=sum(1 for other in selected if other['id']!=e['id'] and other['coin_id']==e['coin_id'] and abs((instant(other['anchor_ts'])-start).total_seconds())<86400)
        result.append(dict(episode_id=e['id'],coin_id=e['coin_id'],anchor=e['anchor_ts'],outcome=kind,crossing=cross.isoformat() if cross else '',stored_crossing=e['significant_move_ts'] or '',recorded_candidate=recorded.isoformat() if recorded else '',shadow_candidate=shadow.isoformat() if shadow else '',recorded_early=int(bool(cross and recorded and recorded<cross)),shadow_early=int(bool(cross and shadow and shadow<cross)),shadow_lead_minutes=round((cross-shadow).total_seconds()/60,3) if cross and shadow and shadow<cross else '',evaluation_rows=len(rows),overlapping_same_asset=overlaps))
    return baseline,result


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--csv',required=True);a=p.parse_args()
    with sqlite3.connect(Path(a.db).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN');baseline,rows=build(db)
    fields=['episode_id','coin_id','anchor','outcome','crossing','stored_crossing','recorded_candidate','shadow_candidate','recorded_early','shadow_early','shadow_lead_minutes','evaluation_rows','overlapping_same_asset']
    with open(a.csv,'w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    print(json.dumps({'baseline':baseline,'holdout_episodes':len(rows),'movers':sum(r['outcome']=='mover' for r in rows),'controls':sum(r['outcome']=='control' for r in rows),'unknown':sum(r['outcome']=='unknown' for r in rows),'recorded_early':sum(r['recorded_early'] for r in rows),'shadow_early':sum(r['shadow_early'] for r in rows),'control_recorded':sum(bool(r['recorded_candidate']) for r in rows if r['outcome']=='control'),'control_shadow':sum(bool(r['shadow_candidate']) for r in rows if r['outcome']=='control'),'no_evaluations':sum(not r['evaluation_rows'] for r in rows),'overlap_episodes':sum(bool(r['overlapping_same_asset']) for r in rows),'crossing_mismatches':sum(bool(r['crossing'] and r['stored_crossing'] and instant(r['crossing'])!=instant(r['stored_crossing'])) for r in rows)},indent=2))
if __name__=='__main__':main()
