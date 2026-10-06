import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import zipfile

from crypto_radar.db import connect
from crypto_radar.analysis_snapshot import export_snapshot, TIMES, page_storage


class AnalysisSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.db'
        self.db = connect(self.source)
        self.addCleanup(self.db.close)
        self.now = datetime(2026, 9, 28, tzinfo=timezone.utc)
        self.recent = (self.now-timedelta(days=1)).isoformat()
        self.old = (self.now-timedelta(days=100)).isoformat()
        self.ancient = (self.now-timedelta(days=250)).isoformat()
        for sid, ts in [(1,self.old),(2,self.recent),(3,self.ancient)]:
            self.insert('scan_runs', id=sid, started_ts=ts, status='completed')
            self.insert('candidate_evaluations',id=sid,scan_id=sid,coin_id='coin',market_ts=ts,decision='suppressed')
            self.insert('market_observations',ts=ts,coin_id='coin',price=10.25)
        self.insert('research_episodes',id=1,coin_id='coin',started_ts=self.old,last_signal_ts=self.recent,
                    anchor_ts=self.old,anchor_price=10,significant_move_pct=5,move_window_hours=24)
        self.insert('social_features',id=1,scan_id=2,coin_id='coin',asof_ts=self.recent,status='unavailable',mentions_5m=None)
        self.insert('social_features',id=2,scan_id=2,coin_id='zero',asof_ts=self.recent,status='ok',mentions_5m=0)
        self.insert('social_observations',id=1,coin_id='zero',window_start=self.old,window_end=self.recent,
                    observed_ts=self.recent,mentions=0)
        for eid,ts in [(1,self.old),(2,self.recent)]:
            self.insert('experiment_evaluations',id=eid,scan_id=eid,coin_id='coin',signal_version='1.2',
                        experiment_group='no_signal',detected_ts=ts,evidence_cutoff_ts=ts,market_ts=ts,
                        episode_id=1,v11_evaluation_id=eid,social_feature_id=1,features_json='{"missing":null,"x":0}')
            for h in (1,3,6,12,24):
                self.insert('experiment_outcomes',evaluation_id=eid,horizon_hours=h,target_ts=ts,
                            observed_ts=self.recent if h==1 else None,timing_status='late' if h==1 else 'pending',
                            timing_error_seconds=3600 if h==1 else None)
                self.insert('benchmark_outcomes',evaluation_id=eid,horizon_hours=h,benchmark='btc',coin_id='bitcoin',status='missing')
            self.insert('benchmark_context',evaluation_id=eid,benchmark='btc',coin_id='bitcoin',observed_ts=ts,start_price=100)
            self.insert('benchmark_members',scan_id=eid,coin_id='other',observed_ts=ts,start_price=20)
        self.insert('news_evidence',id=1,coin_id='coin',first_observed_ts=self.ancient,publication_ts=self.ancient,title='Old catalyst')
        self.insert('evaluation_news',evaluation_id=2,news_id=1,novelty=0.5)
        self.insert('collector_runs',scan_id=2,collector='social',coin_id='coin',observed_ts=self.recent,status='unavailable',item_count=0)
        self.db.commit()
        self.network = patch('socket.socket.connect',side_effect=AssertionError('offline only'))
        self.network.start(); self.addCleanup(self.network.stop)

    def insert(self, table, **values):
        for col in self.db.execute('PRAGMA table_info('+table+')'):
            if col['name'] not in values and col['notnull'] and col['dflt_value'] is None:
                values[col['name']] = '{}' if col['name'].endswith('_json') else ('fixture' if col['type']=='TEXT' else 0)
        keys=','.join(values)
        self.db.execute('INSERT INTO '+table+' ('+keys+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))

    def export(self, name='out.db', **kwargs):
        result=export_snapshot(self.source,self.root/name,as_of=self.now,**kwargs)
        out=sqlite3.connect(result['path'])
        self.addCleanup(out.close)
        return result,out

    def test_source_bytes_and_schema_unchanged(self):
        before=hashlib.sha256(self.source.read_bytes()).digest()
        self.export()
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(),before)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],3)

    def test_storage_diagnostics_cover_allocated_pages_and_overflow(self):
        self.db.execute('UPDATE experiment_evaluations SET features_json=?',('x'*20000,))
        self.db.commit()
        roots=self.db.execute('SELECT name,rootpage FROM sqlite_master WHERE rootpage>0').fetchall()
        storage=page_storage(self.source,roots)
        pages=self.db.execute('PRAGMA page_count').fetchone()[0]
        free=self.db.execute('PRAGMA freelist_count').fetchone()[0]
        size=self.db.execute('PRAGMA page_size').fetchone()[0]
        self.assertEqual(sum(r['bytes'] for r in storage),(pages-free)*size)

    def test_active_wal_writer_not_stopped_or_included_uncommitted(self):
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute("UPDATE scan_runs SET status='uncommitted' WHERE id=2")
        _,out=self.export()
        self.assertEqual(out.execute('SELECT status FROM scan_runs WHERE id=2').fetchone()[0],'completed')
        self.db.rollback()

    def test_episode_and_cross_cutoff_dependents(self):
        _,out=self.export()
        self.assertEqual(out.execute('SELECT count(*) FROM research_episodes').fetchone()[0],1)
        self.assertEqual(out.execute('SELECT count(*) FROM experiment_evaluations').fetchone()[0],2)
        self.assertEqual(out.execute('SELECT count(*) FROM experiment_outcomes').fetchone()[0],10)
        self.assertEqual(out.execute('SELECT count(*) FROM scan_runs WHERE id=1').fetchone()[0],1)

    def test_benchmark_evidence(self):
        _,out=self.export()
        for table,count in [('benchmark_context',2),('benchmark_members',2),('benchmark_outcomes',10)]:
            self.assertEqual(out.execute('SELECT count(*) FROM '+table).fetchone()[0],count)

    def test_old_news_relationship_preserved(self):
        _,out=self.export()
        self.assertEqual(out.execute('SELECT n.title FROM news_evidence n JOIN evaluation_news e ON e.news_id=n.id').fetchone()[0],'Old catalyst')

    def test_social_missing_is_not_zero(self):
        _,out=self.export()
        self.assertEqual(out.execute('SELECT status,mentions_5m FROM social_features ORDER BY id').fetchall(),[('unavailable',None),('ok',0)])
        self.assertEqual(out.execute('SELECT mentions FROM social_observations').fetchone()[0],0)

    def test_horizons_and_timing_quality(self):
        _,out=self.export()
        self.assertEqual(out.execute('SELECT DISTINCT horizon_hours FROM experiment_outcomes ORDER BY 1').fetchall(),[(1,),(3,),(6,),(12,),(24,)])
        self.assertEqual(out.execute('SELECT timing_error_seconds FROM experiment_outcomes WHERE horizon_hours=1 LIMIT 1').fetchone()[0],3600)

    def test_metadata_counts_and_cutoff(self):
        result,out=self.export()
        meta={k:json.loads(v) for k,v in out.execute('SELECT * FROM snapshot_metadata')}
        self.assertEqual(meta['requested_history_days'],84)
        self.assertEqual(meta['tables']['scan_runs']['source_rows'],3)
        self.assertEqual(meta['tables']['scan_runs']['exported_rows'],2)
        self.assertEqual(result['analysis_bytes'],Path(result['path']).stat().st_size)

    def test_deterministic_selection_and_old_history(self):
        _,a=self.export('a.db'); _,b=self.export('b.db')
        for table in TIMES:
            self.assertEqual(a.execute('SELECT * FROM '+table).fetchall(),b.execute('SELECT * FROM '+table).fetchall())
        self.assertEqual(a.execute('SELECT observations FROM market_daily_history').fetchone()[0],1)
        self.assertEqual(a.execute("SELECT sum(rows) FROM snapshot_history WHERE source_table='scan_runs'").fetchone()[0],1)

    def test_integrity_and_exact_values(self):
        _,out=self.export()
        self.assertEqual(out.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
        self.assertEqual(out.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(out.execute('SELECT features_json FROM experiment_evaluations LIMIT 1').fetchone()[0],'{"missing":null,"x":0}')

    def test_long_json_reconstruction_is_byte_exact(self):
        value=json.dumps({'unicode':'é😀','nested':[{'text':'a,b:c{}[]', 'n':1.23456789, 'empty':None}]*40},ensure_ascii=False)
        self.db.execute('UPDATE experiment_evaluations SET features_json=?',(value,));self.db.commit()
        _,out=self.export()
        self.assertEqual(out.execute('SELECT features_json FROM experiment_evaluations').fetchall(),[(value,),(value,)])

    def test_ai_legacy_outcomes_and_all_retained_values_match_source(self):
        self.insert('ai_calls',id=1,evaluation_id=2,coin_id='coin',budget_date='2026-09-01',
                    reserved_ts=self.old,status='completed',requested_model='test')
        self.insert('assessments',id=1,ts=self.ancient,coin_id='coin',ai_call_id=1,evaluation_id=2)
        self.insert('outcomes',assessment_id=1,horizon_hours=24,target_ts=self.old,anchor_ts=self.old)
        self.insert('alert_events',id=1,assessment_id=1,coin_id='coin',attempted_ts=self.old,status='suppressed')
        self.db.commit()
        _,out=self.export()
        for table in TIMES:
            actual=set(out.execute('SELECT * FROM '+table).fetchall())
            expected={tuple(r) for r in self.db.execute('SELECT * FROM '+table)}
            self.assertTrue(actual.issubset(expected),table)
        for table in ('ai_calls','assessments','outcomes','alert_events'):
            self.assertEqual(out.execute('SELECT count(*) FROM '+table).fetchone()[0],1)

    def test_cutoff_inclusive_and_no_duplicate_measurement_keys(self):
        cutoff=(self.now-timedelta(days=84)).isoformat()
        before=(self.now-timedelta(days=84,seconds=1)).isoformat()
        self.insert('scan_runs',id=4,started_ts=cutoff,status='completed')
        self.insert('scan_runs',id=5,started_ts=before,status='completed')
        self.db.commit()
        _,out=self.export()
        self.assertEqual(out.execute('SELECT id FROM scan_runs WHERE id IN (4,5)').fetchall(),[(4,)])
        self.assertEqual(out.execute('SELECT count(*) FROM experiment_outcomes').fetchone()[0],10)
        self.assertLess(out.execute('SELECT count(*) FROM data_benchmark_outcomes_payload').fetchone()[0],10)

    def test_zip_and_no_overwrite(self):
        result,_=self.export(zipped=True)
        before=Path(result['path']).read_bytes()
        with zipfile.ZipFile(result['zip_path']) as z:
            self.assertEqual(z.read('out.db'),before)
        with self.assertRaises(FileExistsError): self.export(zipped=True)
        self.assertEqual(Path(result['path']).read_bytes(),before)

    def test_unknown_schema_and_orphans_fail_closed(self):
        self.db.execute('CREATE TABLE unexpected (id INTEGER)'); self.db.commit()
        with self.assertRaisesRegex(ValueError,'Unsupported'): self.export()
        self.assertFalse((self.root/'out.db').exists())
        self.db.execute('DROP TABLE unexpected')
        self.db.execute("INSERT INTO benchmark_outcomes VALUES (999,1,'btc','bitcoin',NULL,NULL,NULL,'missing')")
        self.db.commit()
        # Seed orphan with a recent observed timestamp so validation must see it.
        self.db.execute('UPDATE benchmark_outcomes SET observed_ts=? WHERE evaluation_id=999',(self.recent,));self.db.commit()
        with self.assertRaisesRegex(ValueError,'Orphaned'): self.export()


if __name__=='__main__':
    unittest.main()
