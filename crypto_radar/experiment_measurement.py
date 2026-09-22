"""Forward measurements shared by both detectors, independent of AI selection."""
import json
import math
from datetime import datetime, timedelta
from statistics import mean

from .outcomes import HORIZONS
from .filters import is_stablecoin


def ret(start, end):
    return (end/start-1)*100


def valid_price(value):
    try:
        price = float(value)
        return price if math.isfinite(price) and price > 0 else None
    except (ValueError,TypeError):
        return None


def episode(conn, coin, now, market_ts, flags, cfg):
    latest = conn.execute('SELECT * FROM research_episodes WHERE coin_id=? ORDER BY id DESC LIMIT 1', (coin['id'],)).fetchone()
    if not any(flags.values()):
        return latest['id'] if latest else None
    reset = latest is None
    if latest and (now-datetime.fromisoformat(latest['last_signal_ts'])).total_seconds() >= cfg['v12']['episodes']['quiet_hours']*3600:
        # Downtime cannot establish independence: require continuous market observations in the quiet interval.
        points = conn.execute('SELECT ts FROM market_observations WHERE coin_id=? AND ts>=? AND ts<=? ORDER BY ts',
                              (coin['id'],latest['last_signal_ts'],market_ts)).fetchall()
        times = [datetime.fromisoformat(latest['last_signal_ts'])]+[datetime.fromisoformat(p['ts']) for p in points]+[now]
        reset = all(0 <= (b-a).total_seconds() <= cfg['scan_interval_seconds']*2 for a,b in zip(times,times[1:]))
    if reset:
        move = cfg['v12']['significant_move']
        target = now-timedelta(hours=move['lookback_hours'])
        anchor = conn.execute('''SELECT ts,price FROM market_observations WHERE coin_id=?
            AND ts<=? AND ts>=? AND price>0 ORDER BY ts DESC LIMIT 1''',
            (coin['id'],target.isoformat(),(target-timedelta(seconds=move['anchor_tolerance_seconds'])).isoformat())).fetchone()
        anchor_ts, price = (anchor['ts'],anchor['price']) if anchor else (market_ts,valid_price(coin['current_price']))
        eid = conn.execute('''INSERT INTO research_episodes
            (coin_id,started_ts,last_signal_ts,anchor_ts,anchor_price,significant_move_pct,move_window_hours,definition_version)
            VALUES (?,?,?,?,?,?,?,'fixed-anchor-1.2')''',
            (coin['id'],now.isoformat(),now.isoformat(),anchor_ts,price,move['threshold_pct'],move['window_hours'])).lastrowid
    else:
        eid = latest['id']
        conn.execute('UPDATE research_episodes SET last_signal_ts=? WHERE id=?',(now.isoformat(),eid))
    for key, active in flags.items():
        if key not in ('social_anomaly','news_event','market_anomaly','v11_candidate','v12_candidate'):
            raise ValueError('Unknown chronology flag')
        if active:
            conn.execute(f'UPDATE research_episodes SET first_{key}_ts=COALESCE(first_{key}_ts,?) WHERE id=?', (now.isoformat(),eid))
    return eid


def fill_moves(conn, now):
    for e in conn.execute('SELECT * FROM research_episodes WHERE significant_move_ts IS NULL').fetchall():
        end = min(now, datetime.fromisoformat(e['anchor_ts'])+timedelta(hours=e['move_window_hours']))
        point = conn.execute('''SELECT ts FROM market_observations WHERE coin_id=? AND ts>=? AND ts<=?
            AND price>=? ORDER BY ts LIMIT 1''',
            (e['coin_id'],e['anchor_ts'],end.isoformat(),e['anchor_price']*(1+e['significant_move_pct']/100))).fetchone()
        if point:
            conn.execute('UPDATE research_episodes SET significant_move_ts=? WHERE id=?',(point['ts'],e['id']))


def register_evaluation(conn, scan_id, coin, ts, market_ts, version, group, stats,
                        eligible, decision, reason, episode_id, cfg, markets,
                        v11_id=None, social_id=None, news=None):
    price = valid_price(coin.get('current_price'))
    anchor = conn.execute('SELECT anchor_price FROM research_episodes WHERE id=?',(episode_id,)).fetchone()
    movement = ret(anchor[0],price) if anchor and price and price > 0 else None
    eid = conn.execute('''INSERT INTO experiment_evaluations
        (scan_id,coin_id,signal_version,experiment_group,detected_ts,evidence_cutoff_ts,market_ts,start_price,
         episode_id,v11_evaluation_id,social_feature_id,market_score,social_score,news_score,pre_score,
         eligible,decision,reason,price_move_before_pct,features_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (scan_id,coin['id'],version,group,ts,ts,market_ts,price,episode_id,v11_id,social_id,
         stats.get('market_score'),stats.get('social_score'),stats.get('news_score'),stats.get('pre_score'),
         int(eligible),decision,reason,movement,json.dumps(stats,allow_nan=False))).lastrowid
    for item in news or []:
        conn.execute('INSERT INTO evaluation_news VALUES (?,?,?,?,?)',
            (eid,item['id'],item['age_seconds'],item['novelty'],item['independent_confirmations']))
    # Measure rejected controls too. Invalid/stablecoin rows stay auditable, with no outcome price invented.
    if decision == 'excluded' or not price or price <= 0:
        return eid
    for m in markets:
        value = valid_price(m.get('current_price'))
        if not value or value <= 0:
            continue
        labels = []
        if m['id'] in ('bitcoin','ethereum'):
            labels.append('btc' if m['id']=='bitcoin' else 'eth')
        if cfg['v12']['market_regime']['broad_enabled'] and not is_stablecoin(m,cfg) and m['id'] != coin['id']:
            conn.execute('INSERT OR IGNORE INTO benchmark_members VALUES (?,?,?,?)',
                         (scan_id,m['id'],market_ts,value))
        for label in labels:
            conn.execute('INSERT INTO benchmark_context VALUES (?,?,?,?,?)',(eid,label,m['id'],market_ts,value))
    for h in HORIZONS:
        # Anchor to the actual market price, NOT AI completion or RSS publication time.
        target = datetime.fromisoformat(market_ts)+timedelta(hours=h)
        conn.execute('''INSERT INTO experiment_outcomes
            (evaluation_id,horizon_hours,target_ts,tolerance_seconds) VALUES (?,?,?,?)''',
            (eid,h,target.isoformat(),cfg['outcomes']['tolerance_seconds']))
    return eid


def fill_outcomes(conn, now, cfg):
    rows = conn.execute('''SELECT o.*,e.coin_id,e.start_price,e.features_json,e.scan_id FROM experiment_outcomes o
        JOIN experiment_evaluations e ON e.id=o.evaluation_id
        WHERE o.observed_ts IS NULL AND o.target_ts<=?''',(now.isoformat(),)).fetchall()
    for row in rows:
        point = conn.execute('''SELECT ts,price FROM market_observations WHERE coin_id=?
            AND ts>=? AND ts<=? AND price>0 ORDER BY ts LIMIT 1''',
            (row['coin_id'],row['target_ts'],now.isoformat())).fetchone()
        if not point:
            if (now-datetime.fromisoformat(row['target_ts'])).total_seconds() > row['tolerance_seconds']:
                conn.execute("UPDATE experiment_outcomes SET timing_status='missing' WHERE evaluation_id=? AND horizon_hours=?",
                    (row['evaluation_id'],row['horizon_hours']))
            continue
        error = (datetime.fromisoformat(point['ts'])-datetime.fromisoformat(row['target_ts'])).total_seconds()
        absolute = ret(row['start_price'],point['price'])
        returns = {'btc':[], 'eth':[], 'broad':[]}
        expected = {'btc':0, 'eth':0, 'broad':0}
        benchmarks = list(conn.execute('SELECT * FROM benchmark_context WHERE evaluation_id=?',(row['evaluation_id'],)).fetchall())
        benchmarks += list(conn.execute("SELECT *, 'broad' AS benchmark FROM benchmark_members WHERE scan_id=? AND coin_id!=?",
                                       (row['scan_id'],row['coin_id'])).fetchall())
        for b in benchmarks:
            expected[b['benchmark']] += 1
            # Exact shared collection timestamps: no approximate mismatched windows.
            endpoint = conn.execute('SELECT price FROM market_observations WHERE coin_id=? AND ts=? AND price>0',
                                    (b['coin_id'],point['ts'])).fetchone()
            value = ret(b['start_price'],endpoint[0]) if endpoint else None
            if value is not None:
                returns[b['benchmark']].append(value)
            if b['benchmark'] != 'broad':
                conn.execute('INSERT OR REPLACE INTO benchmark_outcomes VALUES (?,?,?,?,?,?,?,?)',
                    (row['evaluation_id'],row['horizon_hours'],b['benchmark'],b['coin_id'],
                     point['ts'] if endpoint else None,endpoint[0] if endpoint else None,value,
                     'matched' if endpoint else 'missing'))
        values = {}
        minimum = json.loads(row['features_json']).get('min_broad_assets',cfg['v12']['market_regime']['min_broad_assets'])
        for key, measured in returns.items():
            enough = len(measured) >= (minimum if key=='broad' else 1)
            # Freeze the initial equal-weight basket; never silently remove delisted/missing assets.
            values[key] = mean(measured) if enough and len(measured)==expected[key] else None
        status = 'matched' if all(v is not None for v in values.values()) else 'partial_or_missing'
        conn.execute('''UPDATE experiment_outcomes SET observed_ts=?,later_price=?,return_pct=?,
            timing_error_seconds=?,timing_status=?,btc_return_pct=?,eth_return_pct=?,broad_return_pct=?,
            excess_btc_pct=?,excess_eth_pct=?,excess_broad_pct=?,benchmark_status=?
            WHERE evaluation_id=? AND horizon_hours=?''',
            (point['ts'],point['price'],absolute,error,'within_tolerance' if error<=row['tolerance_seconds'] else 'late',
             values['btc'],values['eth'],values['broad'],
             *[absolute-values[k] if values[k] is not None else None for k in ('btc','eth','broad')],
             status,row['evaluation_id'],row['horizon_hours']))
    fill_moves(conn,now)
    conn.commit()
