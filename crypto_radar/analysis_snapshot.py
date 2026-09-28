"""Read-only, schema-v2 research export. Original names are lossless SQL views."""
import argparse
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import zipfile

VERSION = '1.0'
# Explicit policies: a new source table requires review, never silent omission.
TIMES = {
    'market_observations': 'ts', 'assessments': 'ts', 'outcomes': 'target_ts',
    'schema_migrations': 'applied_ts', 'scan_runs': 'started_ts',
    'candidate_evaluations': 'market_ts', 'ai_calls': 'reserved_ts',
    'signal_state': 'last_ts', 'alert_events': 'attempted_ts',
    'research_episodes': 'last_signal_ts', 'social_observations': 'observed_ts',
    'social_features': 'asof_ts', 'news_evidence': 'first_observed_ts',
    'collector_runs': 'observed_ts', 'experiment_evaluations': 'detected_ts',
    'evaluation_news': None, 'benchmark_members': 'observed_ts',
    'benchmark_context': 'observed_ts', 'experiment_outcomes': 'target_ts',
    'benchmark_outcomes': 'observed_ts', 'v12_ai_calls': 'reserved_ts',
}


def q(name):
    return '"' + name.replace('"', '""') + '"'


def page_storage(path, roots):
    """Allocated B-tree/overflow pages on the frozen copy, when dbstat is unavailable.

    SQLite file format: https://sqlite.org/fileformat2.html (B-tree pages).
    This measures page allocation, not the byte size of logical SQL values.
    """
    def uint(data, offset, size):
        return int.from_bytes(data[offset:offset+size], 'big')
    def varint(data, offset):
        value = 0
        for i in range(9):
            b = data[offset+i]
            value = (value << (8 if i == 8 else 7)) | (b if i == 8 else b & 127)
            if b < 128 or i == 8:
                return value, offset+i+1
        raise ValueError('Invalid varint')
    result = []
    with path.open('rb') as stream:
        header = stream.read(100)
        size = uint(header,16,2)
        size = 65536 if size == 1 else size
        usable = size-header[20]
        count = path.stat().st_size//size
        def page(number):
            if not 1 <= number <= count:
                raise ValueError('Invalid B-tree page')
            stream.seek((number-1)*size)
            return stream.read(size)
        owned = set()
        for name, root in [('sqlite_schema',1), *roots]:
            todo = [root]; seen = set()
            while todo:
                number = todo.pop()
                if number in seen or number in owned:
                    raise ValueError('Duplicate B-tree page')
                seen.add(number)
                data = page(number); base = 100 if number == 1 else 0
                kind = data[base]
                if kind not in (2,5,10,13):
                    raise ValueError('Unsupported B-tree type')
                interior = kind in (2,5)
                if interior:
                    todo.append(uint(data,base+8,4))
                for i in range(uint(data,base+3,2)):
                    offset = uint(data,base+(12 if interior else 8)+2*i,2)
                    if interior:
                        todo.append(uint(data,offset,4)); offset += 4
                    if kind == 5:
                        continue
                    payload,offset = varint(data,offset)
                    if kind == 13:
                        _,offset = varint(data,offset)
                    maximum = usable-35 if kind == 13 else (usable-12)*64//255-23
                    if payload > maximum:
                        minimum = (usable-12)*32//255-23
                        local = minimum+(payload-minimum)%(usable-4)
                        if local > maximum:
                            local = minimum
                        overflow = uint(data,offset+local,4)
                        while overflow:
                            if overflow in seen or overflow in owned:
                                raise ValueError('Duplicate overflow page')
                            seen.add(overflow)
                            overflow = uint(page(overflow),0,4)
            owned.update(seen)
            result.append(dict(name=name,bytes=len(seen)*size))
    return sorted(result,key=lambda r:r['bytes'],reverse=True)


def revision():
    try:
        args = dict(cwd=Path(__file__).resolve().parents[1], capture_output=True,
                    text=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], **args)
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'], **args)
        return {'head': head.stdout.strip() if head.returncode == 0 else None,
                'dirty': bool(dirty.stdout.strip()) if dirty.returncode == 0 else None}
    except (OSError, subprocess.TimeoutExpired):
        return {'head': None, 'dirty': None}


def relations(conn, tables):
    edges = []
    for table in tables:
        for fk in conn.execute(f'PRAGMA src.foreign_key_list({q(table)})'):
            edges.append((table, (fk[3],), fk[2], (fk[4],)))
    edges.extend([
        ('outcomes', ('assessment_id',), 'assessments', ('id',)),
        ('benchmark_outcomes', ('evaluation_id', 'horizon_hours'),
         'experiment_outcomes', ('evaluation_id', 'horizon_hours')),
    ])
    return edges


def select_rows(conn, cutoff):
    """Ownership closure keeps complete connected research evidence, not sampled arms."""
    for table, stamp in TIMES.items():
        conn.execute(f'CREATE TEMP TABLE {q("keep_"+table)} (rid INTEGER PRIMARY KEY)')
        where = '1' if table in ('schema_migrations', 'signal_state') else (
            f'julianday({q(stamp)}) >= julianday(?)' if stamp else '0')
        conn.execute(f'INSERT INTO {q("keep_"+table)} SELECT rowid FROM src.{q(table)} WHERE {where}',
                     (cutoff,) if '?' in where else ())
    edges = relations(conn, TIMES)
    complete = all(conn.execute(f'SELECT count(*) FROM {q("keep_"+t)}').fetchone()[0] ==
                   conn.execute(f'SELECT count(*) FROM src.{q(t)} WHERE {q(stamp)} IS NOT NULL').fetchone()[0]
                   for t, stamp in TIMES.items() if stamp and t not in ('schema_migrations','signal_state'))
    if complete:
        for t in TIMES:
            conn.execute(f'INSERT OR IGNORE INTO {q("keep_"+t)} SELECT rowid FROM src.{q(t)}')
    # Child evidence follows retained parents; foreign-key parents always follow children.
    # Shared news/features do not pull unrelated historical evaluations into the cohort.
    ownership = {'scan_id', 'episode_id', 'evaluation_id', 'assessment_id', 'research_episode_id',
                 'experiment_evaluation_id', 'ai_call_id', 'v12_ai_call_id'}
    while not complete:
        before = conn.total_changes
        for child, cc, parent, pc in edges:
            join = ' AND '.join(f'c.{q(a)}=p.{q(b)}' for a, b in zip(cc, pc))
            conn.execute(f'INSERT OR IGNORE INTO {q("keep_"+parent)} '
                         f'SELECT p.rowid FROM src.{q(parent)} p JOIN src.{q(child)} c ON {join} '
                         f'JOIN {q("keep_"+child)} k ON k.rid=c.rowid')
            if cc[0] in ownership:
                conn.execute(f'INSERT OR IGNORE INTO {q("keep_"+child)} '
                             f'SELECT c.rowid FROM src.{q(child)} c JOIN src.{q(parent)} p ON {join} '
                             f'JOIN {q("keep_"+parent)} k ON k.rid=p.rowid')
        if conn.total_changes == before:
            break
    # Preserve raw paths and all universe members from the oldest retained context,
    # plus seven days for rolling baselines. This also preserves broad-market endpoints.
    anchors = [cutoff]
    for table, column in [('research_episodes', 'anchor_ts'), ('candidate_evaluations', 'market_ts'),
                          ('experiment_evaluations', 'market_ts'), ('assessments', 'market_observed_ts'),
                          ('outcomes', 'anchor_ts'), ('social_features', 'asof_ts'),
                          ('benchmark_context', 'observed_ts'), ('benchmark_members', 'observed_ts')]:
        value = conn.execute(f'SELECT min(s.{q(column)}) FROM src.{q(table)} s '
                             f'JOIN {q("keep_"+table)} k ON k.rid=s.rowid').fetchone()[0]
        if value:
            anchors.append(value)
    earliest = min(datetime.fromisoformat(v).astimezone(timezone.utc) for v in anchors)
    baseline = (earliest - timedelta(days=7)).isoformat()
    for table, column in [('market_observations', 'ts'), ('social_observations', 'observed_ts')]:
        conn.execute(f'INSERT OR IGNORE INTO {q("keep_"+table)} SELECT rowid FROM src.{q(table)} '
                     f'WHERE julianday({q(column)})>=julianday(?)', (baseline,))
    return edges, baseline


class TextEncoder:
    """SHA-indexed interning avoids duplicating large JSON in a text UNIQUE index."""
    def __init__(self, conn):
        self.conn = conn
        self.cache = OrderedDict()
        conn.execute('CREATE TABLE snapshot_text (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute('CREATE TABLE text_index (digest BLOB PRIMARY KEY, id INTEGER NOT NULL) WITHOUT ROWID')

    def encode(self, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError('Non-text storage in a TEXT source column; unsupported schema data')
        digest = hashlib.sha256(value.encode('utf-8')).digest()
        if digest in self.cache:
            self.cache.move_to_end(digest)
            return self.cache[digest]
        existing = self.conn.execute('SELECT id FROM text_index WHERE digest=?', (digest,)).fetchone()
        if existing:
            ident = existing[0]
            if self.conn.execute('SELECT value FROM snapshot_text WHERE id=?', (ident,)).fetchone()[0] != value:
                raise ValueError('Text digest collision')
        else:
            ident = self.conn.execute('INSERT INTO snapshot_text(value) VALUES (?)', (value,)).lastrowid
            self.conn.execute('INSERT INTO text_index VALUES (?,?)', (digest, ident))
        self.cache[digest] = ident
        if len(self.cache) > 100000:
            self.cache.popitem(last=False)
        return ident


def copy_table(conn, table, encoder):
    columns = list(conn.execute(f'PRAGMA src.table_info({q(table)})'))
    text_positions = [i for i, c in enumerate(columns) if c[2].upper() == 'TEXT']
    definitions = [q(c[1]) + (' INTEGER REFERENCES snapshot_text(id)' if i in text_positions else ' '+c[2])
                   for i, c in enumerate(columns)]
    keys = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
    if keys:
        definitions.append('PRIMARY KEY (' + ','.join(map(q, keys)) + ')')
    physical = 'data_' + table
    conn.execute(f'CREATE TABLE {q(physical)} ({",".join(definitions)})')
    names = ','.join('s.'+q(c[1]) for c in columns)
    cursor = conn.execute(f'SELECT {names} FROM src.{q(table)} s '
                          f'JOIN {q("keep_"+table)} k ON k.rid=s.rowid ORDER BY s.rowid')
    while batch := cursor.fetchmany(2000):
        encoded = []
        for row in batch:
            values = list(row)
            for i in text_positions:
                values[i] = encoder.encode(values[i])
            encoded.append(values)
        conn.executemany(f'INSERT INTO {q(physical)} VALUES ({",".join("?" for _ in columns)})', encoded)
    expressions = [f'(SELECT value FROM snapshot_text WHERE id=d.{q(c[1])}) AS {q(c[1])}'
                   if i in text_positions else 'd.'+q(c[1]) for i, c in enumerate(columns)]
    conn.execute(f'CREATE VIEW {q(table)} AS SELECT {",".join(expressions)} FROM {q(physical)} d')
    # Useful integer joins without replicating all live scheduling indexes.
    for c in columns:
        if c[1].endswith('_id') and c[2].upper() == 'INTEGER' and c[1] not in keys[:1]:
            conn.execute(f'CREATE INDEX {q(physical+"_"+c[1])} ON {q(physical)} ({q(c[1])})')


def diagnostics(conn, table):
    stamp = TIMES[table]
    names = [r[1] for r in conn.execute(f'PRAGMA src.table_info({q(table)})')]
    size = '+'.join(f'coalesce(length(cast({q(n)} AS BLOB)),0)' for n in names)
    source = conn.execute(f'SELECT count(*),sum({size})'+
                          (f',min({q(stamp)}),max({q(stamp)})' if stamp else ',NULL,NULL')+
                          f' FROM src.{q(table)}').fetchone()
    exported = conn.execute(f'SELECT count(*)'+
                            (f',min({q(stamp)}),max({q(stamp)})' if stamp else ',NULL,NULL')+
                            f' FROM {q(table)}').fetchone()
    return dict(source_rows=source[0], logical_payload_bytes=source[1] or 0,
                source_min=source[2], source_max=source[3], exported_rows=exported[0],
                exported_min=exported[1], exported_max=exported[2])


def factor_measurements(conn, table):
    """Separate repeated measured payloads from evaluation keys, without averaging."""
    physical = 'data_'+table
    columns = list(conn.execute(f'PRAGMA table_info({q(physical)})'))
    source_columns = list(conn.execute(f'PRAGMA src.table_info({q(table)})'))
    text_columns = {c[1] for c in source_columns if c[2] == 'TEXT'}
    keys = [c for c in sorted(columns,key=lambda c:c[5]) if c[5]]
    values = [c for c in columns if not c[5]]
    payload = physical+'_payload'
    def definition(c):
        return q(c[1])+' '+c[2]+(' REFERENCES snapshot_text(id)' if c[1] in text_columns else '')
    conn.execute(f'CREATE TABLE {q(payload)} (payload_id INTEGER PRIMARY KEY,'+
                 ','.join(definition(c) for c in values)+')')
    value_names = ','.join(q(c[1]) for c in values)
    conn.execute(f'INSERT INTO {q(payload)} ({value_names}) SELECT DISTINCT {value_names} FROM {q(physical)}')
    conn.execute(f'CREATE INDEX payload_match ON {q(payload)} ({value_names})')
    mapping = physical+'_map'
    conn.execute(f'CREATE TABLE {q(mapping)} ('+','.join(definition(c) for c in keys)+
                 f',payload_id INTEGER NOT NULL REFERENCES {q(payload)}(payload_id),PRIMARY KEY ('+
                 ','.join(q(c[1]) for c in keys)+')) WITHOUT ROWID')
    join = ' AND '.join(f'd.{q(c[1])} IS p.{q(c[1])}' for c in values)
    conn.execute(f'INSERT INTO {q(mapping)} SELECT '+','.join('d.'+q(c[1]) for c in keys)+
                 f',p.payload_id FROM {q(physical)} d JOIN {q(payload)} p ON {join}')
    conn.execute('DROP INDEX payload_match')
    conn.execute(f'DROP VIEW {q(table)}')
    conn.execute(f'DROP TABLE {q(physical)}')
    conn.execute(f'ALTER TABLE {q(mapping)} RENAME TO {q(physical)}')
    key_names = {c[1] for c in keys}
    expressions = []
    for c in source_columns:
        ref = ('d.' if c[1] in key_names else 'p.')+q(c[1])
        expr = f'(SELECT value FROM snapshot_text WHERE id={ref})' if c[2]=='TEXT' else ref
        expressions.append(expr+' AS '+q(c[1]))
    conn.execute(f'CREATE VIEW {q(table)} AS SELECT '+','.join(expressions)+
                 f' FROM {q(physical)} d JOIN {q(payload)} p ON p.payload_id=d.payload_id')


def summaries(conn):
    conn.execute('CREATE TABLE snapshot_history (source_table TEXT, day TEXT, dimensions_json TEXT, rows INTEGER)')
    # Omitted data: descriptive longitudinal denominators, never pretend to retain paths.
    for table, stamp in TIMES.items():
        if not stamp:
            continue
        cols = {r[1] for r in conn.execute(f'PRAGMA src.table_info({q(table)})')}
        dims = [n for n in ('coin_id', 'signal_version', 'experiment_group', 'decision', 'status',
                            'collector', 'horizon_hours', 'timing_status') if n in cols]
        obj = 'json_object('+','.join("'"+n+"',s."+q(n) for n in dims)+')'
        conn.execute(f'INSERT INTO snapshot_history SELECT ?,date(s.{q(stamp)}),{obj},count(*) '
                     f'FROM src.{q(table)} s WHERE NOT EXISTS '
                     f'(SELECT 1 FROM {q("keep_"+table)} k WHERE k.rid=s.rowid) GROUP BY 2,3', (table,))
    conn.execute('''CREATE TABLE market_daily_history AS SELECT coin_id,date(ts) day,
        count(*) observations,count(price) priced_observations,min(price) low,max(price) high,
        avg(price) mean_price,min(ts) first_ts,max(ts) last_ts
        FROM src.market_observations s WHERE NOT EXISTS
        (SELECT 1 FROM keep_market_observations k WHERE k.rid=s.rowid) GROUP BY coin_id,date(ts)''')
    conn.execute('''CREATE TABLE outcome_daily_history AS SELECT date(o.target_ts) day,
        e.signal_version,e.experiment_group,o.horizon_hours,o.timing_status,
        count(*) outcomes,count(o.return_pct) measured,count(DISTINCT e.episode_id) episodes,
        avg(o.return_pct) mean_return,min(o.return_pct) min_return,max(o.return_pct) max_return,
        count(o.excess_btc_pct) btc_measured,avg(o.excess_btc_pct) mean_excess_btc,
        count(o.excess_eth_pct) eth_measured,avg(o.excess_eth_pct) mean_excess_eth,
        count(o.excess_broad_pct) broad_measured,avg(o.excess_broad_pct) mean_excess_broad
        FROM src.experiment_outcomes o JOIN src.experiment_evaluations e ON e.id=o.evaluation_id
        WHERE NOT EXISTS (SELECT 1 FROM keep_experiment_outcomes k WHERE k.rid=o.rowid)
        GROUP BY 1,2,3,4,5''')
    views = {
        'analysis_groups': 'SELECT signal_version,experiment_group,decision,count(*) evaluations,count(DISTINCT episode_id) episodes,count(DISTINCT coin_id) coins FROM experiment_evaluations GROUP BY 1,2,3',
        'analysis_episodes': 'SELECT episode_id,coin_id,signal_version,experiment_group,count(*) evaluations,min(detected_ts) first_ts,max(detected_ts) last_ts FROM experiment_evaluations GROUP BY 1,2,3,4',
        'analysis_outcomes': "SELECT '1.1' detector,horizon_hours,timing_status,count(*) rows,count(observed_ts) observed,count(return_pct) returns,max(abs(timing_error_seconds)) max_error_seconds FROM outcomes GROUP BY 2,3 UNION ALL SELECT 'experiment',horizon_hours,timing_status,count(*),count(observed_ts),count(return_pct),max(abs(timing_error_seconds)) FROM experiment_outcomes GROUP BY 2,3",
        'analysis_collectors': 'SELECT collector,status,count(*) runs,sum(item_count) items,min(observed_ts) first_ts,max(observed_ts) last_ts FROM collector_runs GROUP BY 1,2',
        'analysis_social': 'SELECT status,count(*) features,count(mentions_5m) measured_mentions,count(mention_acceleration) measured_acceleration FROM social_features GROUP BY 1',
        'analysis_social_observations': 'SELECT source,coin_id,count(*) windows,count(unique_authors) author_windows,count(engagement) engagement_windows,min(window_start) first_window,max(window_end) last_window FROM social_observations GROUP BY 1,2',
        'analysis_news': 'SELECT e.signal_version,e.experiment_group,count(*) evaluations,sum(EXISTS(SELECT 1 FROM evaluation_news n WHERE n.evaluation_id=e.id)) with_news FROM experiment_evaluations e GROUP BY 1,2',
        'analysis_benchmarks': 'SELECT horizon_hours,benchmark_status,count(*) outcomes,count(btc_return_pct) btc,count(eth_return_pct) eth,count(broad_return_pct) broad FROM experiment_outcomes GROUP BY 1,2',
        'analysis_candidates': 'SELECT decision,count(*) evaluations,count(pre_score) scored FROM candidate_evaluations GROUP BY 1',
        'analysis_ai': "SELECT '1.1' detector,status,count(*) calls FROM ai_calls GROUP BY 2 UNION ALL SELECT '1.2',status,count(*) FROM v12_ai_calls GROUP BY 2",
        'analysis_scans': 'SELECT status,count(*) scans,min(started_ts) first_ts,max(started_ts) last_ts FROM scan_runs GROUP BY 1',
    }
    for name, sql in views.items():
        conn.execute(f'CREATE VIEW {q(name)} AS {sql}')


def validate(conn, edges):
    if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ValueError('quick_check failed')
    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('integrity_check failed')
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Encoded text reference integrity failed')
    for child, cc, parent, pc in edges:
        joins = ' AND '.join(f'c.{q(a)}=p.{q(b)}' for a, b in zip(cc, pc))
        present = ' AND '.join('c.'+q(a)+' IS NOT NULL' for a in cc)
        if conn.execute(f'SELECT 1 FROM {q(child)} c WHERE {present} AND NOT EXISTS '
                        f'(SELECT 1 FROM {q(parent)} p WHERE {joins}) LIMIT 1').fetchone():
            raise ValueError(f'Orphaned relationship: {child} -> {parent}')


def export_snapshot(source, output=None, history_days=84, zipped=False, as_of=None):
    if history_days < 1:
        raise ValueError('history_days must be positive')
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('as_of must have a timezone')
    now = now.astimezone(timezone.utc)
    source = Path(source).resolve(strict=True)
    output = Path(output) if output else source.parent/'analysis-snapshots'/f'crypto-radar-analysis-{now.strftime("%Y%m%dT%H%M%S%fZ")}.db'
    output = output.resolve()
    if output == source or output.suffix != '.db':
        raise ValueError('Output must be a separate .db file')
    output.parent.mkdir(parents=True, exist_ok=True)
    zip_path = output.with_suffix('.zip')
    if output.exists() or (zipped and zip_path.exists()):
        raise FileExistsError('Snapshot already exists; choose a new output')
    # Exclusive reservation prevents overlapping exports from overwriting each other.
    reservation = output.with_suffix('.db.lock')
    with reservation.open('x'):
        pass
    try:
        with tempfile.TemporaryDirectory(prefix='analysis-', dir=output.parent) as temporary:
            backup = Path(temporary)/'source.db'
            built = Path(temporary)/'analysis.db'
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=30)) as live:
                with closing(sqlite3.connect(backup)) as frozen:
                    live.backup(frozen, pages=1024, sleep=0.01)
            with closing(sqlite3.connect(built.as_uri(), uri=True)) as conn:
                conn.execute('ATTACH DATABASE ? AS src', (backup.as_uri()+'?mode=ro',))
                tables = {r[0] for r in conn.execute("SELECT name FROM src.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                version = conn.execute('PRAGMA src.user_version').fetchone()[0]
                if tables != set(TIMES) or version != 2:
                    raise ValueError('Unsupported source schema; retention policy requires schema v2 tables')
                source_schema = list(conn.execute("SELECT name,sql FROM src.sqlite_master WHERE sql IS NOT NULL ORDER BY name"))
                conn.execute('CREATE TABLE snapshot_source_schema (name TEXT PRIMARY KEY, sql TEXT)')
                conn.executemany('INSERT INTO snapshot_source_schema VALUES (?,?)', source_schema)
                cutoff = (now-timedelta(days=history_days)).isoformat()
                edges, baseline = select_rows(conn, cutoff)
                encoder = TextEncoder(conn)
                for table in TIMES:
                    copy_table(conn, table, encoder)
                for table in ('benchmark_outcomes', 'benchmark_context', 'experiment_outcomes'):
                    factor_measurements(conn, table)
                stats = {table: diagnostics(conn, table) for table in TIMES}
                summaries(conn)
                for _, sql in conn.execute("SELECT name,sql FROM src.sqlite_master WHERE type='view'").fetchall():
                    conn.execute(sql)
                try:
                    storage = [dict(name=r[0], bytes=r[1]) for r in conn.execute("SELECT name,sum(pgsize) FROM dbstat('src') GROUP BY name")]
                except sqlite3.OperationalError:
                    roots = conn.execute("SELECT name,rootpage FROM src.sqlite_master WHERE rootpage>0").fetchall()
                    storage = page_storage(backup, roots)
                metadata = dict(exporter_version=VERSION, export_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    cutoff_reference_utc=now.isoformat(), source_db_path=str(source), source_schema_version=version,
                    source_snapshot_bytes=backup.stat().st_size, source_file_bytes=source.stat().st_size,
                    source_storage_pages=storage, source_page_count=conn.execute('PRAGMA src.page_count').fetchone()[0],
                    source_free_pages=conn.execute('PRAGMA src.freelist_count').fetchone()[0],
                    requested_history_days=history_days, requested_cutoff_utc=cutoff, baseline_start_utc=baseline,
                    git=revision(), tables=stats,
                    policy='Recent rows plus complete ownership/FK closure; all market/social paths from oldest retained anchor minus 7 days. Older omitted rows summarized by day and strata. TEXT values losslessly interned; original names are SQL views. No row sampling or rounding.',
                    limitations='Older aggregate-only data cannot reconstruct individual paths or causal chronology. Existing sampling gaps and missing social data remain. Export is analysis-only, not a scanner database. Long episodes can extend detail beyond the requested window. Seven-day raw baseline may not reproduce custom longer baselines; saved features/configs retained. Source snapshot is current, not a historical as-of reconstruction.',
                    integrity_check='ok', quick_check='ok', foreign_key_check='ok', relationship_check='ok')
                conn.execute('DROP TABLE text_index')
                conn.execute('CREATE TABLE snapshot_metadata (key TEXT PRIMARY KEY,value_json TEXT NOT NULL)')
                conn.executemany('INSERT INTO snapshot_metadata VALUES (?,?)', [(k,json.dumps(v)) for k,v in metadata.items()])
                conn.commit()
                validate(conn, edges)
                conn.execute('VACUUM')  # Destination ONLY, never source or frozen backup.
                if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('Post-compaction quick_check failed')
            # Hard links publish without replace semantics, including concurrent external creation.
            os.link(built, output)
            if zipped:
                with zipfile.ZipFile(zip_path, 'x', zipfile.ZIP_LZMA) as archive:
                    archive.write(output, output.name)
            return dict(path=str(output), zip_path=str(zip_path) if zipped else None,
                        analysis_bytes=output.stat().st_size, zip_bytes=zip_path.stat().st_size if zipped else None,
                        compression_ratio=output.stat().st_size/zip_path.stat().st_size if zipped else None, **metadata)
    finally:
        reservation.unlink()


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root/'data/crypto_radar.db')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--history-days', type=int, default=84)
    parser.add_argument('--zip', action='store_true')
    parser.add_argument('--as-of', type=datetime.fromisoformat, help='Timezone-aware cutoff reference (not time travel)')
    args = parser.parse_args()
    result = export_snapshot(args.source, args.output, args.history_days, args.zip, args.as_of)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
