"""Synthetic fixtures only; never call an external provider."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from crypto_radar.db import connect
from crypto_radar.collectors.attention import ApeSettings, ApeWisdomCollector
from crypto_radar.validation_report import build_report, indicators, minutes


START=datetime(2026,10,29,tzinfo=timezone.utc)


def ts(hours=0):
    return (START+timedelta(hours=hours)).isoformat()


class ValidationReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'fixture.db'
        self.db=connect(self.path)
        self.addCleanup(self.db.close)
        self.network=patch('socket.socket.connect',side_effect=AssertionError('No external access'))
        self.network.start()
        self.addCleanup(self.network.stop)
        cfg={'ai':{'min_pre_score':45},'filters':{'min_history_points':6,'anomaly_threshold':2},
             'v12':{'enabled':True,'social':{'enabled':True,'providers':['apewisdom'],
                    'provider_options':{'apewisdom':{'enabled':True,'access_confirmed':True,
                        'asset_map':{'coin':'COIN.X'},'poll_seconds':1800}}}}}
        self.insert('scan_runs',id=1,started_ts=ts(),completed_ts=ts(30),status='completed',config_json=json.dumps(cfg))
        self.insert('research_episodes',id=1,coin_id='coin',started_ts=ts(),last_signal_ts=ts(2),
            anchor_ts=ts(),anchor_price=100,significant_move_pct=5,move_window_hours=24,
            significant_move_ts=ts(1),first_v11_candidate_ts=ts(1),first_market_anomaly_ts=ts())
        for h,price in ((0,100),(.5,102),(1,106),(2,110),(3,115),(6,120),(12,125),(24,130),(30,140)):
            self.insert('market_observations',coin_id='coin',ts=ts(h),price=price)
        self.evaluation(1,1,0)
        for h in (1,3,6,12,24):
            price={1:106,3:115,6:120,12:125,24:130}[h]
            self.insert('experiment_outcomes',evaluation_id=1,horizon_hours=h,target_ts=ts(h),
                observed_ts=ts(h),later_price=price,return_pct=(price/100-1)*100,
                timing_status='within_tolerance',excess_btc_pct=2)
        self.db.commit()

    def insert(self,table,**values):
        for col in self.db.execute('PRAGMA table_info('+table+')'):
            if col['name'] not in values and col['notnull'] and col['dflt_value'] is None:
                values[col['name']]=0 if col['type'] in ('INTEGER','REAL') else '{}'
        keys=','.join(values)
        self.db.execute(f"INSERT INTO {table} ({keys}) VALUES ({','.join('?' for _ in values)})",list(values.values()))

    def evaluation(self,eid,episode,hour,score=30):
        features={'market_features':{'history_count':10,'volume_z':1,'components':{'early_move':8}}}
        if eid != 1:
            self.insert('scan_runs',id=eid,started_ts=ts(hour),completed_ts=ts(hour),status='completed',config_json='{}')
        self.insert('experiment_evaluations',id=eid,scan_id=eid,coin_id='coin',signal_version='1.1',
            experiment_group='market_only',detected_ts=ts(hour),evidence_cutoff_ts=ts(hour),
            market_ts=ts(hour),start_price=100,episode_id=episode,pre_score=score,
            reason='below_threshold',decision='rejected',features_json=json.dumps(features))

    def report(self,cutoff=30):
        self.db.commit()
        return build_report(self.path,ts(cutoff))

    def point(self,hour,mentions,status='available',complete=1):
        return dict(run_id=str(hour),observed_ts=ts(hour),mentions=mentions,upvotes=mentions*2,
                    rank=100-mentions,status=status,complete=complete)

    def test_rolling_negative_relative_rank_and_missing_zero_denominator(self):
        result=indicators([self.point(0,10),self.point(.5,8)],ts(.5))['latest_indicator']
        self.assertEqual(result['rolling_mentions_delta'],-2)
        self.assertEqual(result['relative_rolling_mentions_change'],-.2)
        self.assertEqual(result['rolling_rank_delta'],2)
        self.assertEqual(result['rolling_upvotes_delta'],-4)
        result=indicators([self.point(0,0),self.point(.5,8)],ts(.5))['latest_indicator']
        self.assertIsNone(result['relative_rolling_mentions_change'])

    def test_repeated_partial_stale_future_and_missing(self):
        rows=[self.point(0,5),self.point(.5,5,'repeated'),self.point(1,999)]
        self.assertFalse(indicators(rows,ts(.5))['available'])
        self.assertEqual(len(indicators(rows,ts(.5))['samples']),1)
        self.assertFalse(indicators([self.point(0,5)],ts(2))['available'])
        self.assertFalse(indicators([self.point(0,5,complete=0)],ts())['available'])
        self.assertIsNone(indicators([],ts())['latest_indicator'])

    def test_baseline_minimum_sampling_persistence_and_no_lookahead(self):
        rows=[self.point(i/2,10+i%2) for i in range(12)]
        self.assertIsNone(indicators(rows,ts(5.5))['latest_indicator']['level_z'])
        rows += [self.point(6,30),self.point(6.5,40),self.point(7,100000)]
        a=indicators(rows,ts(6.5))['latest_indicator']
        self.assertEqual(a['baseline_samples'],13)
        self.assertEqual(a['elevated_streak'],2)
        self.assertIsNotNone(a['rolling_delta_rate_excess_per_hour'])
        self.assertEqual(a,indicators(rows[:-1],ts(6.5))['latest_indicator'])
        frequent=[self.point(i/60,5+i) for i in range(30)]
        self.assertEqual(len(indicators(frequent,ts(.5))['samples']),1)

    def test_fixture_api_to_resolution_storage_and_report(self):
        session=Mock()
        session.get.return_value=Mock(status_code=200,content=b'{}',headers={})
        session.get.return_value.json.return_value={'pages':1,'current_page':1,'results':[
            {'ticker':'COIN.X','mentions':10,'rank':1,'upvotes':20},
            {'ticker':'UNKNOWN.X','mentions':5,'rank':2}]}
        collector=ApeWisdomCollector(ApeSettings(enabled=True,access_confirmed=True,asset_map={'coin':'COIN.X'}),session)
        self.assertEqual(collector.collect_persist(self.db,[{'id':'coin'}],START,lambda:START),('available',2))
        report=self.report(0)
        self.assertEqual(report['health']['distinct_resolved_assets'],1)
        self.assertEqual(report['health']['unresolved_tickers'],{'UNKNOWN.X':1})
        self.assertEqual(report['health']['coverage'][0]['indicators'][0]['mentions'],10)
        self.assertEqual(report['health']['provider_timestamp_rows'],0)
        self.assertEqual(report['comparisons']['status'],'insufficient_social_data')
        with self.assertRaises(ValueError):
            ApeSettings(asset_map={'coin':'COIN.X','other':'coin.x'})

    def test_timing_signs_equality_and_move_reproduction(self):
        report=self.report()
        self.assertEqual(report['timing']['early'],0)
        self.assertEqual(report['timing']['simultaneous'],1)
        self.assertEqual(report['timing']['historical_oct29']['nonnegative_lead_minutes']['median'],0)
        self.assertEqual(report['timing']['move_recomputation_mismatches'],[])
        self.assertEqual(minutes(ts(),ts(1)),60)
        self.assertEqual(minutes(ts(2),ts(1)),-60)
        self.assertIsNone(minutes(None,ts()))
        self.assertIsNone(self.report(.5)['timing']['episodes'][0]['significant_move_ts'])

    def test_one_anchor_per_episode_forward_returns_and_missing_benchmarks(self):
        self.evaluation(2,1,.5,score=99)
        report=self.report()
        anchors=report['comparisons']['anchors']
        self.assertEqual(len(anchors),1)
        self.assertEqual(anchors[0]['evaluation_id'],1)
        metric=report['comparisons']['groups'][0]['horizons'][0]['metrics']
        self.assertAlmostEqual(metric['return_pct']['mean'],6)
        self.assertIsNone(metric['excess_eth_pct']['mean'])
        self.assertEqual(metric['return_pct']['positive_frequency'],1)
        self.assertIsNone(self.report(.5)['comparisons']['anchors'][0]['horizons'][0]['return_pct'])

    def test_outcomes_late_or_inconsistent_are_excluded(self):
        self.db.execute("UPDATE experiment_outcomes SET timing_status='late' WHERE horizon_hours=1")
        self.db.execute('UPDATE experiment_outcomes SET return_pct=900 WHERE horizon_hours=3')
        horizons=self.report()['comparisons']['anchors'][0]['horizons']
        self.assertIsNone(horizons[0]['return_pct'])
        self.assertIsNone(horizons[1]['return_pct'])
        self.assertTrue(horizons[1]['return_recomputation_mismatch'])

    def test_future_news_never_changes_anchor_group(self):
        self.insert('news_evidence',coin_id='coin',fingerprint='late',first_observed_ts=ts(.1),publication_ts=ts(-2))
        self.assertEqual(self.report()['comparisons']['anchors'][0]['group'],'no_observed_upstream_signal')

    def test_configured_assets_are_not_live_coverage(self):
        self.insert('attention_runs',id='unresolved',provider='apewisdom',query_key='all-crypto',
            observed_ts=ts(),status='available',complete=1)
        self.insert('apewisdom_snapshots',run_id='unresolved',ticker='COIN.X',observed_ts=ts(),mentions=1)
        h=self.report(0)['health']
        self.assertEqual(h['distinct_resolved_assets'],0)
        self.assertEqual(h['data_status'],'unresolved')
        self.assertEqual(h['coverage'][0]['rows'],0)

    def test_health_gaps_failed_rate_limited_and_repeated_snapshots(self):
        for run,hour,status in [('a',0,'available'),('b',.5,'repeated'),('c',3,'failed')]:
            self.insert('attention_runs',id=run,provider='apewisdom',query_key='all-crypto',
                observed_ts=ts(hour),status=status,complete=int(status!='failed'))
            if status!='failed':
                self.insert('apewisdom_snapshots',run_id=run,ticker='COIN.X',coin_id='coin',observed_ts=ts(hour),mentions=1)
        self.insert('collector_runs',scan_id=1,collector='social:apewisdom',coin_id='*',observed_ts=ts(3),status='rate_limited')
        h=self.report(3)['health']
        self.assertEqual(h['repeated_responses'],1)
        self.assertEqual(h['run_states']['failed'],1)
        self.assertEqual(h['collector_states'][0]['status'],'rate_limited')
        self.assertEqual(len(h['polling_gaps']),1)
        self.assertFalse(h['coverage'][0]['availability'])

    def test_independent_episodes_are_separate_but_repeated_evaluations_are_not(self):
        self.insert('research_episodes',id=2,coin_id='coin',started_ts=ts(2),last_signal_ts=ts(3),
            anchor_ts=ts(2),anchor_price=110,significant_move_pct=5,move_window_hours=24)
        self.evaluation(2,2,2)
        self.assertEqual(len(self.report()['comparisons']['anchors']),2)

    def test_report_deterministic_and_source_unchanged_cli_missing_file(self):
        self.db.commit()
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.report(),self.report())
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        missing=Path(self.temp.name)/'missing.db'
        process=subprocess.run([sys.executable,'-B','-m','crypto_radar.validation_report','--db',str(missing)],capture_output=True)
        self.assertEqual(process.returncode,2)
        self.assertFalse(missing.exists())


if __name__=='__main__':
    unittest.main()
