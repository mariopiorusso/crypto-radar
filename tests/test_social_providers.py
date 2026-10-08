import json
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from crypto_radar.collectors.social import SocialObservation, FixtureSocialCollector, make_collector
from crypto_radar.collectors.social_providers import ProviderSettings, ProviderCollection, ProviderError
from crypto_radar.collectors.stockgeist import StockGeistCollector
from crypto_radar.intelligence.social import social_features
from crypto_radar.config import normalize
from crypto_radar.migrations import migrate
from crypto_radar.analysis_snapshot import export_snapshot
from crypto_radar.episode_report import build_report
from test_v12 import buckets, feature_rows, ExperimentCase
from test_v11 import NOW, COIN


def observations(provider='stockgeist',scale=1):
    return [r.model_copy(update=dict(provider=provider,provider_asset_id='EX',
             mentions=r.mentions*scale,provider_timestamp=NOW)) for r in buckets()]


def collection(second=None):
    collectors={'stockgeist':FixtureSocialCollector(observations())}
    if second is not None: collectors['mock_second']=second
    return ProviderCollection(collectors,{name:ProviderSettings(enabled=True) for name in collectors})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.network=patch('socket.socket.connect',side_effect=AssertionError('No paid/external calls'))
        self.network.start(); self.addCleanup(self.network.stop)
        self.environment=patch.dict(os.environ,{'TEST_SOCIAL_TOKEN':'offline-token'})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.settings=ProviderSettings(enabled=True,asset_map={'example':'EX'},token_env='TEST_SOCIAL_TOKEN')
        self.session=Mock()
        response=self.session.get.return_value
        response.status_code=200; response.content=b'{}'
        self.point=dict(timestamp=(NOW-timedelta(minutes=10)).isoformat(),symbol='EX',asset_class='crypto',
                        sources=['reddit'],total_count=0,pos_total_count=0,neg_total_count=0,neu_total_count=0)
        self.payload=dict(server_timestamp=NOW.isoformat(),data={'EX':[self.point]})
        response.json.return_value=self.payload
        self.adapter=StockGeistCollector(self.settings,self.session)

    def test_documented_response_zero_missing_and_native(self):
        self.point['api_key']='never persist'
        result=self.adapter.collect([COIN],NOW)[0]
        self.assertEqual(result.provider,'stockgeist'); self.assertEqual(result.coin_id,'example')
        self.assertEqual(result.mentions,0); self.assertIsNone(result.unique_authors)
        self.assertIsNone(result.engagement); self.assertIsNone(result.sentiment)
        self.assertEqual(result.native['total_count'],0); self.assertNotIn('api_key',result.native)
        self.assertEqual(result.window_end,NOW-timedelta(minutes=5))
        self.assertEqual(self.session.get.call_args.kwargs['headers'],{'token':'offline-token'})
        self.assertFalse(self.session.get.call_args.kwargs['allow_redirects'])
        del self.point['total_count']
        self.assertIsNone(self.adapter.collect([COIN],NOW)[0].mentions)

    def test_sentiment_and_timestamp_convention(self):
        self.point.update(total_count=10,pos_total_count=7,neg_total_count=2,neu_total_count=1)
        self.assertEqual(self.adapter.collect([COIN],NOW)[0].sentiment,0.5)
        self.settings.timestamp_convention='end'
        self.assertEqual(self.adapter.collect([COIN],NOW)[0].window_end,NOW-timedelta(minutes=10))

    def test_future_and_unfinished_buckets_excluded(self):
        self.point['timestamp']=NOW.isoformat()
        self.assertEqual(self.adapter.collect([COIN],NOW),[])

    def test_malformed_and_unmapped_payloads(self):
        for changes in ({'total_count':True},{'total_count':-1},{'timestamp':'bad'},
                        {'total_count':1},{'sources':['unknown']},{'symbol':'OTHER'}):
            old=dict(self.point); self.point.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ProviderError): self.adapter.collect([COIN],NOW)
            self.point.clear(); self.point.update(old)
        self.payload['data']={'UNKNOWN':[self.point]}
        with self.assertRaises(ProviderError): self.adapter.collect([COIN],NOW)

    def test_credentials_rate_limit_timeout_and_no_auto_retry(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ProviderError) as exc:
            self.adapter.collect([COIN],NOW)
        self.assertEqual(exc.exception.status,'missing_credentials'); self.session.get.assert_not_called()
        self.session.get.return_value.status_code=429
        self.session.get.return_value.headers={'Retry-After':'1200'}
        with self.assertRaises(ProviderError) as exc: self.adapter.collect([COIN],NOW)
        self.assertEqual(exc.exception.retry_seconds,1200)
        self.assertEqual(self.session.get.call_count,1)

    def test_config_defaults_multiple_registry_and_secrets_rejected(self):
        cfg=normalize({})['v12']['social']
        self.assertEqual(make_collector(cfg).collectors,{})
        with patch('crypto_radar.collectors.social_providers.registry',return_value={'stockgeist':Mock(),'mock_second':Mock()}):
            cfg=normalize({'v12':{'social':{'providers':['stockgeist','mock_second'],
                'provider_options':{'stockgeist':{'enabled':True},'mock_second':{'enabled':True}}}}})['v12']['social']
            self.assertEqual(len(make_collector(cfg).collectors),2)
        for settings in ({'api_key':'secret'},{'asset_map':{'a':'EX','b':'EX'}},{'poll_seconds':0}):
            with self.assertRaises(ValueError): ProviderSettings(**settings)


class ProviderResearchTests(ExperimentCase):
    def test_independent_baselines_and_no_cross_provider_sum(self):
        a=feature_rows(observations()); b=feature_rows(observations('mock_second',100))
        features=social_features(a+b,NOW,self.cfg['v12']['social'])
        for name in ('stockgeist','mock_second'):
            self.assertEqual(features['providers'][name]['mention_acceleration'],4)
        self.assertIn(features['mentions_5m'],(40,4000)); self.assertNotEqual(features['mentions_5m'],4040)
        self.assertEqual(set(features['providers']),{'stockgeist','mock_second'})
        for row in b: row['mentions']=100000  # only B's baseline changes
        changed=social_features(a+b,NOW,self.cfg['v12']['social'])
        self.assertEqual(changed['providers']['stockgeist']['mention_acceleration'],4)
        self.assertEqual(changed['providers']['mock_second']['mention_acceleration'],1)

    def test_receipt_time_and_staleness_no_lookahead(self):
        rows=feature_rows(observations())
        for row in rows: row['observed_ts']=(NOW+timedelta(seconds=1)).isoformat()
        self.assertEqual(social_features(rows,NOW,self.cfg['v12']['social'])['score'],0)
        rows=feature_rows(observations())
        features=social_features(rows,NOW+timedelta(hours=1),self.cfg['v12']['social'])
        self.assertEqual(features['status'],'stale'); self.assertEqual(features['score'],0)

    def test_runtime_persistence_provenance_and_provider_failure_isolation(self):
        failed=Mock(); failed.collect.side_effect=ProviderError('rate_limited',1200)
        summary=self.scan(provider=collection(failed))
        self.assertGreater(summary['errors'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],24)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM social_observations').fetchone()[0],0)
        feature=json.loads(self.conn.execute('SELECT features_json FROM social_features').fetchone()[0])
        self.assertEqual(feature['selected_provider'],'stockgeist')
        self.assertEqual(len(feature['providers']['stockgeist']['observation_ids']),24)
        states=dict(self.conn.execute("SELECT collector,status FROM collector_runs WHERE collector LIKE 'social:%'"))
        self.assertEqual(states['social:mock_second'],'rate_limited')
        self.assertEqual(states['social:stockgeist'],'available')
        self.assertEqual(self.last_calls,0); self.assertEqual(self.last_alerts,0)

    def test_simultaneous_nulls_and_idempotence(self):
        second=FixtureSocialCollector([r.model_copy(update={'mentions':None,'unique_authors':None}) for r in observations('mock_second')])
        group=collection(second)
        self.scan(provider=group)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],48)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM provider_social_observations WHERE provider='mock_second' AND mentions IS NULL").fetchone()[0],24)
        self.scan(provider=group,now=NOW+timedelta(minutes=5))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],48)

    def test_persistent_rate_limit_and_disabled_provider(self):
        failed=Mock(); failed.collect.side_effect=ProviderError('rate_limited',1200)
        self.scan(provider=collection(failed))
        another=Mock()
        self.scan(provider=collection(another),now=NOW+timedelta(minutes=5))
        another.collect.assert_not_called()
        group=collection(another); group.settings['mock_second'].enabled=False
        self.scan(provider=group,now=NOW+timedelta(hours=1))
        another.collect.assert_not_called()

    def test_compact_snapshot_preserves_provider_evidence_and_report(self):
        self.scan(provider=collection(FixtureSocialCollector(observations('mock_second',100))))
        path=self.path+'.compact.db'
        export_snapshot(self.path,path,as_of=NOW)
        with closing(sqlite3.connect(path)) as compact:
            self.assertEqual(compact.execute('SELECT count(DISTINCT provider) FROM provider_social_observations').fetchone()[0],2)
            self.assertEqual(compact.execute('SELECT count(*) FROM provider_social_observations WHERE unique_authors IS NULL').fetchone()[0],0)
            self.assertEqual(compact.execute('SELECT native_json FROM provider_social_observations LIMIT 1').fetchone()[0],'{}')
        full=build_report(self.path); compact=build_report(path)
        self.assertEqual(full['social'],compact['social'])

    def test_v2_migration_preserves_legacy_and_snapshot_compatibility(self):
        self.conn.execute('''INSERT INTO social_observations
            (coin_id,source,window_start,window_end,observed_ts,mentions,metadata_json)
            VALUES (?,?,?,?,?,0,'{}')''',('example','legacy', (NOW-timedelta(minutes=5)).isoformat(),NOW.isoformat(),NOW.isoformat()))
        self.conn.execute('DROP TABLE social_source_items')
        self.conn.execute('DROP TABLE provider_social_observations')
        self.conn.execute('DELETE FROM schema_migrations WHERE version>=3')
        self.conn.execute('PRAGMA user_version=2'); self.conn.commit()
        export_snapshot(self.path,self.path+'.v2.db',as_of=NOW)
        from crypto_radar import social_schema
        with patch.object(social_schema,'STATEMENTS',social_schema.STATEMENTS+['INVALID SQL']):
            with self.assertRaises(sqlite3.OperationalError): migrate(self.conn,self.path)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='provider_social_observations'").fetchone())
        backup=migrate(self.conn,self.path)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0],4)
        self.assertEqual(self.conn.execute('SELECT mentions FROM social_observations').fetchone()[0],0)
        self.assertIsNone(migrate(self.conn,self.path))

    def test_delayed_finalized_bucket_can_supply_research_without_future_receipts(self):
        rows=feature_rows(observations())
        for row in rows:
            row['window_start']=(datetime.fromisoformat(row['window_start'])-timedelta(minutes=5)).isoformat()
            row['window_end']=(datetime.fromisoformat(row['window_end'])-timedelta(minutes=5)).isoformat()
            row['observed_ts']=NOW.isoformat()
        feature=social_features(rows,NOW,self.cfg['v12']['social'])
        self.assertEqual(feature['mention_acceleration'],4)
        self.assertEqual(feature['providers']['stockgeist']['freshness_seconds'],300)

    def test_bad_provider_provenance_cannot_poison_other_provider(self):
        bad=FixtureSocialCollector(observations('stockgeist'))  # wrong identity for mock_second
        result=self.scan(provider=collection(bad))
        self.assertGreater(result['errors'],0)
        self.assertEqual(self.conn.execute('SELECT DISTINCT provider FROM provider_social_observations').fetchall()[0][0],'stockgeist')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],24)
