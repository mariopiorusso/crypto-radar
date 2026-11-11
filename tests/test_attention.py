import copy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from crypto_radar.db import connect
from crypto_radar.config import normalize
from crypto_radar.collectors.attention import (ApeSettings,ApeWisdomCollector,XSettings,XCollector,
    resolve,reserve,finish_usage)
from crypto_radar.collectors.social_providers import ProviderCollection,ProviderError
from crypto_radar.attention_report import features,build_report
from crypto_radar.analysis_snapshot import export_snapshot
from crypto_radar.migrations import migrate

NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)
ASSETS=[{'id':'example','symbol':'ex'}]
FIXTURES=Path(__file__).parent/'fixtures'


def fixture(name):
    return json.loads((FIXTURES/name).read_text())


def response(payload,status=200):
    result=Mock(status_code=status,content=b'{}',headers={})
    result.json.return_value=copy.deepcopy(payload)
    return result


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'test.db')
        self.db=connect(self.path); self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        self.network=patch('socket.socket.connect',side_effect=AssertionError('Offline tests only'))
        self.network.start(); self.addCleanup(self.network.stop)
        self.env=patch.dict(os.environ,{'X_BEARER_TOKEN':'fixture-secret'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.session=Mock()
        self.session.get.return_value=response(fixture('x_recent.json'))
        self.settings=XSettings(enabled=True,access_confirmed=True,pricing_confirmed=True,
            request_usd=0.01,post_usd=0.01,queries={'example':'$EX'},max_attempts=1)

    def x(self,settings=None,now=NOW):
        return XCollector(settings or self.settings,self.session).collect_persist(self.db,ASSETS,now,lambda:now)

    def ape(self,now=NOW,payload=None,settings=None):
        self.session.get.return_value=response(payload or fixture('apewisdom.json'))
        return ApeWisdomCollector(settings or ApeSettings(enabled=True,access_confirmed=True,
            asset_map={'example':'EX'}),self.session).collect_persist(self.db,ASSETS,now,lambda:now)

    def test_snapshot_native_semantics_unresolved_and_repeat(self):
        self.assertEqual(self.ape(),('available',2))
        row=self.db.execute("SELECT * FROM apewisdom_snapshots WHERE ticker='EX'").fetchone()
        self.assertEqual(row['mentions'],12); self.assertIsNone(row['provider_ts'])
        self.assertIsNone(self.db.execute("SELECT coin_id FROM apewisdom_snapshots WHERE ticker='UNKNOWN'").fetchone()[0])
        self.assertEqual(self.ape(NOW+timedelta(minutes=5))[0],'repeated')
        self.assertFalse(features(self.db,'example',(NOW+timedelta(minutes=5)).isoformat())['apewisdom_observed'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],0)

    def test_rolling_deltas_first_seen_and_stale(self):
        self.ape()
        data=fixture('apewisdom.json'); data['results'][0]['mentions']='15'
        later=NOW+timedelta(minutes=5); self.ape(later,data)
        f=features(self.db,'example',later.isoformat())
        self.assertEqual(f['rolling_mentions_delta'],3)
        self.assertIsNone(features(self.db,'example',NOW.isoformat())['rolling_mentions_delta'])
        self.assertFalse(features(self.db,'example',(later+timedelta(hours=1)).isoformat())['apewisdom_observed'])
        self.assertFalse(features(self.db,'example',(NOW-timedelta(seconds=1)).isoformat())['apewisdom_observed'])

    def test_ape_negative_net_votes_are_preserved(self):
        for value in (-8, '-8', -2**63):
            with self.subTest(value=value):
                data=fixture('apewisdom.json')
                data['results'][0]['upvotes']=value
                self.ape(payload=data)
                row=self.db.execute("SELECT upvotes FROM apewisdom_snapshots WHERE ticker='EX' ORDER BY rowid DESC LIMIT 1").fetchone()
                self.assertEqual(row[0],int(value))

    def test_ape_invalid_counts_fail_without_partial_snapshots(self):
        for field,value in (('mentions',-1),('rank',-1),('mentions_24h_ago',-1),
                            ('rank_24h_ago',-1),('upvotes',True),('upvotes',1.5),
                            ('upvotes',-2**63-1),('upvotes',2**63)):
            with self.subTest(field=field,value=value):
                data=fixture('apewisdom.json'); data['results'][1][field]=value
                with self.assertRaises(ValueError): self.ape(payload=data)
                row=self.db.execute('SELECT status,error FROM attention_runs ORDER BY rowid DESC LIMIT 1').fetchone()
                self.assertEqual(tuple(row),('failed','ValueError'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM apewisdom_snapshots').fetchone()[0],0)

    def test_explicit_asset_resolution_rejects_ambiguity(self):
        self.assertEqual(resolve(ASSETS,{'other':'EX'}),{})
        with self.assertRaises(ValueError):
            ApeSettings(asset_map={'one':'EX','two':'ex'})
        with self.assertRaises(ValueError):
            XSettings(post_usd=float('nan'))

    def test_ape_supports_full_scan_mapping_and_provider_suffix(self):
        mapping = {f'coin-{i}': f'COIN{i}.X' for i in range(100)}
        self.assertEqual(len(ApeSettings(asset_map=mapping).asset_map), 100)
        with self.assertRaises(ValueError):
            ApeSettings(asset_map={**mapping, 'extra': 'EXTRA.X'})
        with self.assertRaises(ValueError):
            XSettings(asset_map=mapping)
        data = fixture('apewisdom.json')
        data['results'][0]['ticker'] = 'EX.X'
        self.ape(payload=data, settings=ApeSettings(enabled=True, access_confirmed=True,
            asset_map={'example': 'EX.X'}))
        row = self.db.execute("SELECT coin_id FROM apewisdom_snapshots WHERE ticker='EX.X'").fetchone()
        self.assertEqual(row[0], 'example')

    def test_x_dedup_overlapping_queries_privacy_and_observed_cost(self):
        settings=self.settings.model_copy(update={'queries':{'example':'$EX','other':'$OTHER'}})
        collector=XCollector(settings,self.session)
        collector.collect_persist(self.db,ASSETS+[{'id':'other'}],NOW,lambda:NOW)
        self.assertEqual(self.db.execute('SELECT count(*) FROM x_posts').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM x_post_assets').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT sum(estimated_microusd) FROM provider_usage').fetchone()[0],40000)
        dump='\n'.join(self.db.iterdump())
        for private in ('fixture-secret','private-author','This must never be stored'):
            self.assertNotIn(private,dump)
        self.assertEqual(features(self.db,'example',NOW.isoformat())['x_observed_posts'],1)
        self.assertFalse(features(self.db,'example',(NOW-timedelta(seconds=1)).isoformat())['x_observed'])

    def test_post_dedup_across_polls_and_restart(self):
        self.x()
        # Overlapping windows contain the same post, whose first-seen time is immutable.
        settings=self.settings.model_copy(update={'overlap_seconds':3600})
        self.x(settings,NOW+timedelta(minutes=5))
        self.assertEqual(self.db.execute('SELECT count(*) FROM x_posts').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT first_seen_ts FROM x_posts').fetchone()[0],NOW.isoformat())
        with closing(sqlite3.connect(self.path)) as other:
            self.assertEqual(other.execute('SELECT count(*) FROM provider_usage').fetchone()[0],2)

    def test_incomplete_search_not_zero_activity(self):
        payload=fixture('x_recent.json'); payload['meta']['next_token']='next-page'
        self.session.get.return_value=response(payload)
        self.x(self.settings.model_copy(update={'max_pages':1}))
        row=self.db.execute('SELECT * FROM attention_runs').fetchone()
        self.assertEqual(row['status'],'partial'); self.assertFalse(row['complete'])
        f=features(self.db,'example',NOW.isoformat())
        self.assertFalse(f['x_observed']); self.assertIsNone(f['x_observed_posts'])

    def test_pagination_and_empty_complete_zero(self):
        payload=fixture('x_recent.json'); payload['meta']['next_token']='next'
        self.session.get.side_effect=[response(payload),response({'meta':{'result_count':0}})]
        self.x()
        self.assertEqual(self.session.get.call_count,2)
        self.assertEqual(self.session.get.call_args.kwargs['params']['next_token'],'next')
        self.assertTrue(self.db.execute('SELECT complete FROM attention_runs').fetchone()[0])

    def test_empty_results_mean_zero_only_when_complete(self):
        self.session.get.return_value=response({'meta':{'result_count':0}})
        self.x(); self.assertEqual(features(self.db,'example',NOW.isoformat())['x_observed_posts'],0)

    def test_budget_checked_before_network_and_uncertain_survives_restart(self):
        settings=self.settings.model_copy(update={'monthly_limit_usd':1.02})
        self.session.get.side_effect=requests.Timeout('secret URL must not be persisted')
        with self.assertRaises(requests.Timeout): self.x(settings)
        self.assertEqual(self.db.execute('SELECT status FROM provider_usage').fetchone()[0],'uncertain')
        with closing(sqlite3.connect(self.path)) as other:
            with self.assertRaises(ProviderError) as exc: reserve(other,settings,NOW,100)
            self.assertEqual(exc.exception.status,'budget_exhausted')
        self.assertEqual(self.session.get.call_count,1)

    def test_pricing_missing_and_daily_limit_fail_closed(self):
        for update,state in (({'post_usd':None},'pricing_unknown'),({'max_posts_per_day':10},'daily_limit'),
                              ({'monthly_limit_usd':0.0},'budget_exhausted'),({'access_confirmed':False},'access_unverified')):
            with self.subTest(state=state):
                with self.assertRaises(ProviderError) as exc: self.x(self.settings.model_copy(update=update))
                self.assertEqual(exc.exception.status,state)
        self.session.get.assert_not_called()

    def test_http_rate_limit_auth_schema_outage_and_bounded_retry(self):
        for code,state in ((429,'rate_limited'),(401,'unauthorized'),(503,'failed')):
            self.session.get.return_value=response({},code)
            with self.assertRaises(ProviderError) as exc: self.x()
            self.assertEqual(exc.exception.status,state)
        self.session.get.return_value=response({'data':{},'meta':{}})
        with self.assertRaises(ProviderError): self.x()
        self.session.get.side_effect=[requests.Timeout(),response(fixture('x_recent.json'))]
        with patch('crypto_radar.collectors.attention.time.sleep') as sleep:
            self.x(self.settings.model_copy(update={'max_attempts':2}))
            sleep.assert_called_once_with(1)

    def test_disabled_and_failure_isolation(self):
        ape=ApeWisdomCollector(ApeSettings(enabled=True),self.session)
        x=XCollector(self.settings,self.session)
        collection=ProviderCollection({'apewisdom':ape,'x':x},{'apewisdom':ape.settings,'x':self.settings})
        results=list(collection.batches(ASSETS,NOW,lambda:NOW,conn=self.db))
        self.assertEqual([r.status for r in results],['access_unverified','available'])
        self.session.get.reset_mock()
        disabled=XSettings()
        result=list(ProviderCollection({'x':XCollector(disabled,self.session)},{'x':disabled}).batches(ASSETS,NOW,lambda:NOW,conn=self.db))
        self.assertEqual(result[0].status,'disabled'); self.session.get.assert_not_called()
        cfg=normalize({'v12':{'social':{'providers':['x','apewisdom']}}})
        self.assertFalse(cfg['v12']['social']['enabled'])

    def test_retention_cascades_and_no_resurrection_first_seen(self):
        self.x()
        self.session.get.return_value=response({'meta':{'result_count':0}})
        self.x(now=NOW+timedelta(days=2))
        for table in ('x_posts','x_post_assets','x_post_metrics'):
            self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_migration_backup_and_compact_report(self):
        self.ape()
        compact=str(Path(self.tmp.name)/'compact.db')
        export_snapshot(self.path,compact,as_of=NOW)
        self.assertEqual(build_report(self.path),build_report(compact))
        self.assertIsNone(migrate(self.db,self.path))
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],4)

    def insert(self,table,**values):
        for col in self.db.execute('PRAGMA table_info('+table+')'):
            if col['name'] not in values and col['notnull'] and col['dflt_value'] is None:
                values[col['name']]='{}' if col['name'].endswith('_json') else ('fixture' if col['type']=='TEXT' else 0)
        self.db.execute('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))

    def cohort_fixture(self):
        self.ape(NOW-timedelta(minutes=5))
        data=fixture('apewisdom.json'); data['results'][0]['mentions']='15'
        self.ape(NOW,data)
        self.session.get.return_value=response(fixture('x_recent.json')); self.x()
        # Two evaluations in episode 1, one in episode 2: 4% and 10% -> 7%.
        for eid,episode,ret in ((1,1,2.0),(2,1,6.0),(3,2,10.0)):
            self.insert('scan_runs',id=eid,started_ts=NOW.isoformat(),status='completed')
            if eid!=2:
                self.insert('research_episodes',id=episode,coin_id='example',started_ts=NOW.isoformat(),
                    last_signal_ts=NOW.isoformat(),anchor_ts=NOW.isoformat(),anchor_price=1.0,
                    first_market_anomaly_ts=(NOW+timedelta(minutes=10)).isoformat())
            self.insert('experiment_evaluations',id=eid,scan_id=eid,coin_id='example',
                signal_version='1.2',experiment_group='no_signal',episode_id=episode,
                detected_ts=NOW.isoformat(),evidence_cutoff_ts=NOW.isoformat(),market_ts=NOW.isoformat())
            for horizon in (1,3,6,12,24):
                self.insert('experiment_outcomes',evaluation_id=eid,horizon_hours=horizon,
                    target_ts=(NOW+timedelta(hours=horizon)).isoformat(),timing_status='within_tolerance',
                    return_pct=ret,excess_btc_pct=ret-1,excess_eth_pct=None,excess_broad_pct=ret-2)
        self.db.commit()

    def test_asof_cohorts_episode_weighting_timing_and_benchmarks(self):
        self.cohort_fixture()
        report=build_report(self.path); cohort=next(c for c in report['cohorts'] if c['cohort']=='both')
        self.assertEqual(cohort['episodes'],2); self.assertEqual(cohort['evaluations'],3)
        for h in cohort['horizons']:
            self.assertEqual(h['metrics']['return_pct']['mean'],7)
            self.assertEqual(h['metrics']['excess_btc_pct']['mean'],6)
            self.assertEqual(h['metrics']['excess_broad_pct']['mean'],5)
            self.assertIsNone(h['metrics']['excess_eth_pct']['mean'])
        self.assertEqual([r['lead_minutes'] for r in report['attention_to_volume_anomaly']],[10,10])
        self.db.execute('UPDATE experiment_evaluations SET evidence_cutoff_ts=?',((NOW-timedelta(minutes=6)).isoformat(),))
        self.db.commit()
        self.assertEqual(next(c for c in build_report(self.path)['cohorts'] if c['cohort']=='neither')['episodes'],2)

    def test_full_compact_populated_report_equivalence(self):
        self.cohort_fixture()
        compact=str(Path(self.tmp.name)/'populated.db')
        export_snapshot(self.path,compact,as_of=NOW)
        self.assertEqual(build_report(self.path),build_report(compact))

    def test_v3_migration_backup_and_rollback(self):
        for table in ('x_post_metrics','x_post_assets','x_posts','apewisdom_snapshots','attention_runs','provider_usage'):
            self.db.execute('DROP TABLE '+table)
        self.db.execute('DELETE FROM schema_migrations WHERE version=4')
        self.db.execute('PRAGMA user_version=3'); self.db.commit()
        from crypto_radar import attention_schema
        with patch.object(attention_schema,'STATEMENTS',attention_schema.STATEMENTS+['INVALID SQL']):
            with self.assertRaises(sqlite3.OperationalError): migrate(self.db,self.path)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],3)
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='attention_runs'").fetchone())
        backup=migrate(self.db,self.path)
        with closing(sqlite3.connect(backup)) as old:
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0],3)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],4)

    def test_delete_request_cascades_without_network(self):
        self.x()
        from crypto_radar.attention_maintenance import delete_posts
        before=self.session.get.call_count
        self.assertEqual(delete_posts(self.db,['12345']),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM x_post_assets').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM x_post_metrics').fetchone()[0],0)
        self.assertEqual(self.session.get.call_count,before)

    def test_atomic_allowance_and_month_rollover(self):
        settings=self.settings.model_copy(update={'monthly_limit_usd':1.5})
        reserve(self.db,settings,NOW,100)
        with closing(sqlite3.connect(self.path)) as other:
            with self.assertRaises(ProviderError): reserve(other,settings,NOW,100)
            reserve(other,settings,NOW.replace(month=11),100)
        self.assertEqual(self.db.execute('SELECT count(*) FROM provider_usage').fetchone()[0],2)

    def test_shared_scheduler_cooldown_survives_new_collector(self):
        collector=XCollector(self.settings,self.session)
        collection=ProviderCollection({'x':collector},{'x':self.settings})
        history={'x':(NOW.isoformat(),'rate_limited','retry_after:1200')}
        result=list(collection.batches(ASSETS,NOW+timedelta(minutes=10),lambda:NOW,history,conn=self.db))
        self.assertEqual(result[0].status,'cooldown'); self.session.get.assert_not_called()

    def test_outage_gap_and_query_change_have_explicit_coverage(self):
        self.x()
        self.session.get.return_value=response({'meta':{'result_count':0}})
        self.x(now=NOW+timedelta(hours=2))
        run=self.db.execute('SELECT * FROM attention_runs ORDER BY observed_ts DESC LIMIT 1').fetchone()
        self.assertEqual(run['gap_seconds'],6300)
        self.x(self.settings.model_copy(update={'queries':{'example':'bitcoin'}}),NOW+timedelta(hours=2,minutes=5))
        self.assertEqual(self.db.execute('SELECT count(DISTINCT query_hash) FROM attention_runs').fetchone()[0],2)

    def test_engagement_changes_use_distinct_receipts_and_no_future_metrics(self):
        self.x()
        payload=fixture('x_recent.json'); payload['data'][0]['public_metrics']['like_count']=5
        self.session.get.return_value=response(payload)
        later=NOW+timedelta(minutes=5)
        self.x(self.settings.model_copy(update={'overlap_seconds':3600}),later)
        self.assertIsNone(features(self.db,'example',NOW.isoformat())['x_engagement_delta'])
        self.assertEqual(features(self.db,'example',later.isoformat())['x_engagement_delta'],3)

    def test_backfill_is_identified_and_cannot_trigger_early_attention(self):
        payload=fixture('x_recent.json'); payload['data'][0]['created_at']='2026-10-20T10:00:00Z'
        self.session.get.return_value=response(payload)
        self.x(self.settings.model_copy(update={'lookback_seconds':10800}))
        f=features(self.db,'example',NOW.isoformat())
        self.assertEqual(f['x_delayed_posts'],1)
        self.assertEqual(f['x_observed_posts'],1)


from test_v12 import ExperimentCase


class NativeRuntimeTests(ExperimentCase):
    def test_new_providers_share_scanner_and_do_not_change_scores(self):
        from crypto_radar.collectors.social import FixtureSocialCollector
        from crypto_radar.collectors.social_providers import ProviderSettings
        from test_social_providers import observations
        stock=FixtureSocialCollector(observations())
        ape_settings=ApeSettings(enabled=True,access_confirmed=True,asset_map={'example':'EX'})
        x_settings=XSettings(enabled=True,access_confirmed=True,pricing_confirmed=True,
            request_usd=0.01,post_usd=0.01,queries={'example':'$EX'})
        ape_session=Mock(); ape_session.get.return_value=response(fixture('apewisdom.json'))
        from test_v11 import NOW as SCAN_NOW
        x_data=fixture('x_recent.json')
        x_data['data'][0]['created_at']=(SCAN_NOW-timedelta(minutes=5)).isoformat()
        x_session=Mock(); x_session.get.return_value=response(x_data)
        collectors={'stockgeist':stock,'apewisdom':ApeWisdomCollector(ape_settings,ape_session),
                    'x':XCollector(x_settings,x_session)}
        settings={'stockgeist':ProviderSettings(enabled=True),'apewisdom':ape_settings,'x':x_settings}
        with patch.dict(os.environ,{'X_BEARER_TOKEN':'offline'}):
            result=self.scan(provider=ProviderCollection(collectors,settings))
        self.assertEqual(result['errors'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],24)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM apewisdom_snapshots').fetchone()[0],2)
        states=dict(self.conn.execute("SELECT collector,status FROM collector_runs WHERE collector LIKE 'social:%'"))
        self.assertEqual(set(states.values()),{'available'})
        social=json.loads(self.conn.execute('SELECT features_json FROM social_features').fetchone()[0])
        self.assertEqual(social['selected_provider'],'stockgeist')
        self.assertEqual(set(social['providers']),{'stockgeist'})
        self.assertEqual(self.last_calls,0); self.assertEqual(self.last_alerts,0)
        before=build_report(self.path)
        saved=json.loads(self.conn.execute("SELECT features_json FROM experiment_evaluations WHERE signal_version='1.2'").fetchone()[0])
        self.assertEqual(saved['attention_evidence']['values']['x_observed_posts'],1)
        self.conn.execute('DELETE FROM x_posts'); self.conn.commit()
        self.assertEqual(build_report(self.path),before)


if __name__=='__main__':
    unittest.main()
