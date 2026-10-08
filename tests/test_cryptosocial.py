"""Synthetic offline evidence only; all external connections forbidden."""
import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from crypto_radar.collectors.cryptosocial import (
    CryptoSocialSettings, CryptoSocialCollector, RedditSource, SourceItem,
    SourceResult, AssetResolver, bucket_start,
)
from crypto_radar.collectors.social_providers import ProviderCollection, ProviderSettings, ProviderError
from crypto_radar.collectors.social import FixtureSocialCollector, make_collector
from crypto_radar.config import normalize
from crypto_radar.intelligence.social import social_features
from crypto_radar.analysis_snapshot import export_snapshot
from crypto_radar.episode_report import build_report
from crypto_radar.migrations import migrate
from test_v12 import ExperimentCase, feature_rows
from test_social_providers import observations
from test_v11 import NOW, COIN


def settings(**changes):
    return CryptoSocialSettings(enabled=True,asset_map={'example':'EX','bitcoin':'BTC','ethereum':'ETH'},
        aliases={'example':['example','$EX'],'bitcoin':['bitcoin','btc','$btc'],
                 'ethereum':['ethereum','eth','$eth']},communities=['CryptoCurrency','Bitcoin'],
        finalization_lag_seconds=0, **changes)


def item(**changes):
    base = SourceItem('t3_fixture',NOW-timedelta(minutes=3),'CryptoCurrency',
                      'Bitcoin ETH example', 'synthetic-author', 5, 2)
    return replace(base,**changes)


def adapter(result=None, options=None):
    source = Mock()
    source.collect.return_value = result if result is not None else SourceResult([item()],[NOW-timedelta(minutes=5)])
    return CryptoSocialCollector(options or settings(), {'reddit':source}), source


class SourceTests(unittest.TestCase):
    def setUp(self):
        network=patch('socket.socket.connect',side_effect=AssertionError('External calls forbidden'))
        network.start(); self.addCleanup(network.stop)

    def test_success_multi_asset_engagement_author_and_provenance(self):
        collector,source=adapter()
        rows=collector.collect([COIN,{'id':'bitcoin','symbol':'btc'},{'id':'ethereum','symbol':'eth'}],NOW)
        self.assertEqual(len(rows),3)
        self.assertEqual({r.coin_id for r in rows},{'example','bitcoin','ethereum'})
        for r in rows:
            self.assertEqual((r.mentions,r.unique_authors,r.engagement),(1,1,7.0))
            self.assertEqual(r.evidence[0].item_id,'t3_fixture')
            self.assertIsNone(r.sentiment); self.assertIsNone(r.positive)
            self.assertIsNone(r.receipt_timestamp)
            self.assertEqual(r.metadata['evidence_kind'],'synthetic_offline_only')
        self.assertEqual(source.collect.call_count,1)

    def test_empty_complete_measured_zero_and_no_coverage_missing(self):
        collector,_=adapter(SourceResult([], [NOW-timedelta(minutes=5)]))
        row=collector.collect([COIN],NOW)[0]
        self.assertEqual((row.mentions,row.unique_authors,row.engagement),(0,0,0.0))
        self.assertIsNone(row.sentiment)
        collector,_=adapter(SourceResult())
        self.assertEqual(collector.collect([COIN],NOW),[])

    def test_missing_metrics_remain_null_not_zero(self):
        collector,_=adapter(SourceResult([item(author=None,score=None)], [NOW-timedelta(minutes=5)]))
        row=collector.collect([COIN],NOW)[0]
        self.assertIsNone(row.unique_authors); self.assertIsNone(row.engagement)
        self.assertEqual(row.mentions,1)

    def test_multi_community_same_author_and_negative_score(self):
        collector,_=adapter(SourceResult([item(score=-3),item(item_id='t3_two',community='Bitcoin')], [NOW-timedelta(minutes=5)]))
        row=collector.collect([COIN],NOW)[0]
        self.assertEqual((row.mentions,row.unique_authors,row.engagement),(2,1,9.0))
        self.assertEqual({e.community for e in row.evidence},{'cryptocurrency','bitcoin'})

    def test_duplicate_item_and_conflicting_duplicate(self):
        collector,_=adapter(SourceResult([item(),item()], [NOW-timedelta(minutes=5)]))
        self.assertEqual(collector.collect([COIN],NOW)[0].mentions,1)
        collector,_=adapter(SourceResult([item(),item(text='changed')], [NOW-timedelta(minutes=5)]))
        with self.assertRaises(ProviderError) as error: collector.collect([COIN],NOW)
        self.assertEqual(error.exception.status,'malformed')

    def test_delayed_future_stale_and_unfinished_items(self):
        old=NOW-timedelta(hours=2,minutes=3)
        collector,_=adapter(SourceResult([item(event_time=old)], [bucket_start(old)]))
        self.assertEqual(collector.collect([COIN],NOW)[0].provider_timestamp,old)
        collector,_=adapter(SourceResult([item(event_time=NOW+timedelta(seconds=1))], []))
        with self.assertRaises(ProviderError): collector.collect([COIN],NOW)
        collector,_=adapter(SourceResult([item(event_time=NOW-timedelta(days=2)),item(item_id='unfinished',event_time=NOW)], []))
        self.assertEqual(collector.collect([COIN],NOW),[])

    def test_timezone_and_deterministic_boundaries(self):
        local=datetime(2026,1,2,14,7,3,tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(bucket_start(local),datetime(2026,1,2,12,5,tzinfo=timezone.utc))
        with self.assertRaises(ValueError): bucket_start(local.replace(tzinfo=None))
        self.assertEqual(bucket_start(NOW),NOW)

    def test_malformed_response_and_uncovered_items(self):
        bad=[{},SourceResult([item(score=True)],[NOW-timedelta(minutes=5)]),
             SourceResult([item(comments=-1)],[NOW-timedelta(minutes=5)]),
             SourceResult([item(community='private')],[NOW-timedelta(minutes=5)]),
             SourceResult([item(event_time=NOW.replace(tzinfo=None))],[]),
             SourceResult([item()],[]),SourceResult([], [NOW]),
             SourceResult([], [NOW-timedelta(minutes=4)])]
        for response in bad:
            with self.subTest(response=response),self.assertRaises(ProviderError):
                adapter(response)[0].collect([COIN],NOW)

    def test_partial_response_rejected_without_false_zero(self):
        collector,_=adapter(SourceResult([], [NOW-timedelta(minutes=5)],False))
        with self.assertRaises(ProviderError) as error: collector.collect([COIN],NOW)
        self.assertEqual(error.exception.status,'partial_error')

    def test_approved_access_absent_transport_never_connects(self):
        with self.assertRaises(ProviderError) as error: RedditSource().collect([],NOW,NOW)
        self.assertEqual(error.exception.status,'approval_required')
        with self.assertRaises(ProviderError): CryptoSocialCollector(settings()).collect([COIN],NOW)

    def test_source_auth_timeout_rate_limit_status_propagation(self):
        for status in ('unauthorized','rate_limited','failed'):
            collector,source=adapter()
            source.collect.side_effect=ProviderError(status,1200 if status=='rate_limited' else None)
            with self.subTest(status=status),self.assertRaises(ProviderError) as error:
                collector.collect([COIN],NOW)
            self.assertEqual(error.exception.status,status)
            self.assertEqual(source.collect.call_count,1)
        collector,source=adapter(); source.collect.side_effect=TimeoutError()
        with self.assertRaises(ProviderError) as error: collector.collect([COIN],NOW)
        self.assertEqual(error.exception.status,'failed')

    def test_asset_resolution_token_cashtag_case_and_ambiguity(self):
        resolver=AssetResolver(settings())
        for text in ('BTC','$BTC','Bitcoin','bItCoIn','btc'):
            self.assertEqual(resolver.resolve(text),['bitcoin'])
        for text in ('ETH','Ethereum','$eth'):
            self.assertEqual(resolver.resolve(text),['ethereum'])
        for text in ('btcoin','method','ETHEREUMish','no token here'):
            self.assertEqual(resolver.resolve(text),[])
        self.assertEqual(resolver.resolve('BTC and ETH'),['bitcoin','ethereum'])
        with self.assertRaises(ValueError):
            CryptoSocialSettings(asset_map={'chainlink':'LINK'},aliases={'chainlink':['link']})
        explicit=CryptoSocialSettings(asset_map={'chainlink':'LINK'},aliases={'chainlink':['$link','chainlink']})
        self.assertEqual(AssetResolver(explicit).resolve('click this link'),[])
        self.assertEqual(AssetResolver(explicit).resolve('$LINK'),['chainlink'])

    def test_mapping_coverage_and_metric_version_isolates_baselines(self):
        a,_=adapter(); b,_=adapter(options=settings(mapping_version='aliases-v2'))
        self.assertNotEqual(a.version,b.version)
        a_rows=feature_rows(a.collect([COIN],NOW)); b_rows=feature_rows(b.collect([COIN],NOW))
        cfg=normalize({})['v12']['social']
        f=social_features(a_rows+b_rows,NOW,cfg)['providers']['cryptosocial']
        self.assertEqual(len(f['sources']),1)
        self.assertEqual(f['mentions_5m'],1)

    def test_mapping_change_does_not_reuse_old_baseline(self):
        old=observations('cryptosocial')
        for r in old: r.metadata={'normalization_version':'old'}
        new=old[0].model_copy(update={'metadata':{'normalization_version':'new'}})
        rows=feature_rows(old[1:]+[new])
        cfg=normalize({'v12':{'social':{'baseline_hours':1,'min_coverage':1.0}}})['v12']['social']
        f=social_features(rows,NOW,cfg)['providers']['cryptosocial']
        self.assertEqual(f['baseline_windows'],0)
        self.assertIsNone(f['mention_acceleration'])

    def test_safe_defaults_and_both_providers_configurable(self):
        self.assertEqual(make_collector(normalize({})['v12']['social']).collectors,{})
        cfg=normalize({'v12':{'social':{'providers':['cryptosocial','stockgeist'],
              'provider_options':{'cryptosocial':settings().model_dump(),'stockgeist':{'enabled':True}}}}})
        group=make_collector(cfg['v12']['social'])
        self.assertEqual(set(group.collectors),{'cryptosocial','stockgeist'})
        self.assertEqual(group.scoring_providers(),{'stockgeist'})


class PersistenceTests(ExperimentCase):
    def group(self,result=None,third=None):
        collector,_=adapter(result)
        collectors={'cryptosocial':collector,'stockgeist':FixtureSocialCollector(observations())}
        options={'cryptosocial':settings(),'stockgeist':ProviderSettings(enabled=True)}
        if third is not None:
            collectors['mock_third']=third; options['mock_third']=ProviderSettings(enabled=True)
        return ProviderCollection(collectors,options)

    def test_coexistence_third_provider_and_failure_isolation(self):
        third=Mock(); third.collect.side_effect=ProviderError('unauthorized')
        result=self.scan(provider=self.group(third=third))
        self.assertGreater(result['errors'],0)
        self.assertEqual({r[0] for r in self.conn.execute('SELECT DISTINCT provider FROM provider_social_observations')},
                         {'cryptosocial','stockgeist'})
        self.assertEqual(self.last_calls,0); self.assertEqual(self.last_alerts,0)
        third=FixtureSocialCollector(observations('mock_third'))
        self.scan(provider=self.group(third=third),now=NOW+timedelta(minutes=20))
        self.assertEqual(self.conn.execute('SELECT count(DISTINCT provider) FROM provider_social_observations').fetchone()[0],3)

    def test_source_failure_preserves_stockgeist(self):
        group=self.group(); group.collectors['cryptosocial']=CryptoSocialCollector(settings())
        self.scan(provider=group)
        self.assertEqual(self.conn.execute("SELECT status FROM collector_runs WHERE collector='social:cryptosocial'").fetchone()[0],'approval_required')
        self.assertEqual(self.conn.execute('SELECT DISTINCT provider FROM provider_social_observations').fetchone()[0],'stockgeist')

    def test_rate_limit_cooldown_survives_new_collector(self):
        group=self.group()
        group.collectors['cryptosocial'].sources['reddit'].collect.side_effect=ProviderError('rate_limited',1200)
        self.scan(provider=group)
        replacement=self.group()
        self.scan(provider=replacement,now=NOW+timedelta(minutes=5))
        replacement.collectors['cryptosocial'].sources['reddit'].collect.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT status FROM collector_runs WHERE collector='social:cryptosocial' ORDER BY observed_ts DESC LIMIT 1").fetchone()[0],'cooldown')

    def test_restart_deduplication_first_receipt_and_no_personal_data(self):
        self.scan(provider=self.group())
        self.scan(provider=self.group(),now=NOW+timedelta(minutes=5))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM social_source_items').fetchone()[0],1)
        row=dict(self.conn.execute('SELECT * FROM social_source_items').fetchone())
        self.assertEqual(row['receipt_ts'],NOW.isoformat())
        self.assertEqual(row['event_ts'],item().event_time.isoformat())
        self.assertNotIn('synthetic-author',json.dumps(row))
        self.assertNotIn('Bitcoin ETH example',json.dumps(row))
        self.assertEqual(set(json.loads(row['assets_json'])),{'example','bitcoin','ethereum'})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM provider_social_observations WHERE provider='cryptosocial'").fetchone()[0],1)

    def test_delayed_receipt_no_lookahead_staleness_and_report(self):
        posted=NOW-timedelta(hours=2,minutes=3)
        self.scan(provider=self.group(SourceResult([item(event_time=posted)], [bucket_start(posted)])))
        row=dict(self.conn.execute("SELECT * FROM provider_social_observations WHERE provider='cryptosocial'").fetchone())
        self.assertEqual(row['receipt_ts'],NOW.isoformat()); self.assertEqual(row['availability'],'stale')
        cfg=self.cfg['v12']['social']
        self.assertEqual(social_features([row],NOW-timedelta(seconds=1),cfg)['score'],0)
        row['observed_ts']=posted.isoformat()
        self.assertIsNone(social_features([row],NOW-timedelta(seconds=1),cfg)['mentions_5m'])
        report=build_report(self.path)
        source=next(r for r in report['social']['sources'] if r['provider']=='cryptosocial')
        self.assertEqual(source['first_attention_receipt'],NOW.isoformat())
        self.assertEqual(source['first_event_window'],bucket_start(posted).isoformat())

    def test_snapshot_preserves_normalized_chronology_excludes_items(self):
        self.scan(provider=self.group(SourceResult([item(author=None,score=None)], [NOW-timedelta(minutes=5)])))
        output=self.path+'.compact.db'
        exported=export_snapshot(self.path,output,as_of=NOW)
        self.assertIn('social_source_items',exported['excluded_tables'])
        with closing(sqlite3.connect(output)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='social_source_items'").fetchone())
            row=db.execute("SELECT mentions,unique_authors,engagement,receipt_ts,metadata_json FROM provider_social_observations WHERE provider='cryptosocial'").fetchone()
            self.assertEqual(row[:4],(1,None,None,NOW.isoformat()))
            self.assertIn('cryptosocial-v1',row[4])
        self.assertEqual(build_report(self.path)['social'],build_report(output)['social'])

    def test_observation_only_never_changes_candidate_score(self):
        self.scan(provider=self.group())
        feature=json.loads(self.conn.execute('SELECT features_json FROM social_features ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(set(feature['providers']),{'stockgeist'})
        self.assertEqual(feature['score'],100)
        self.assertEqual(self.last_calls,0); self.assertEqual(self.last_alerts,0)

    def test_cryptosocial_only_is_observation_without_candidates(self):
        collector,_=adapter()
        group=ProviderCollection({'cryptosocial':collector},{'cryptosocial':settings()})
        self.scan(provider=group)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM provider_social_observations WHERE provider='cryptosocial'").fetchone()[0],1)
        f=json.loads(self.conn.execute('SELECT features_json FROM social_features').fetchone()[0])
        self.assertEqual(f['score'],0)
        self.assertEqual(self.conn.execute("SELECT sum(eligible) FROM experiment_evaluations WHERE signal_version='1.2'").fetchone()[0],0)
        self.assertEqual(self.last_calls,0); self.assertEqual(self.last_alerts,0)

    def test_research_baselines_do_not_mix_providers(self):
        own=[r.model_copy(update={'provider':'cryptosocial','metadata':{'normalization_version':'cs-v1'}}) for r in observations()]
        features=social_features(feature_rows(own)+feature_rows(observations(scale=100)),NOW,self.cfg['v12']['social'])
        self.assertEqual(features['providers']['cryptosocial']['mention_acceleration'],4)
        self.assertEqual(features['providers']['stockgeist']['mention_acceleration'],4)

    def test_v3_additive_backup_and_transaction_rollback(self):
        self.scan(provider=self.group())
        before=self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0]
        self.conn.execute('DROP TABLE social_source_items')
        self.conn.execute('DELETE FROM schema_migrations WHERE version=4')
        self.conn.execute('PRAGMA user_version=3'); self.conn.commit()
        from crypto_radar import cryptosocial_schema
        with patch.object(cryptosocial_schema,'STATEMENTS',cryptosocial_schema.STATEMENTS+['INVALID SQL']):
            with self.assertRaises(sqlite3.OperationalError): migrate(self.conn,self.path)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0],3)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='social_source_items'").fetchone())
        export_snapshot(self.path,self.path+'.v3.db',as_of=NOW)
        backup=migrate(self.conn,self.path)
        with closing(sqlite3.connect(backup)) as old:
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(old.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],before)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0],4)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM provider_social_observations').fetchone()[0],before)
        self.assertIsNone(migrate(self.conn,self.path))
        self.assertEqual(len(self.conn.execute("PRAGMA index_list('social_source_items')").fetchall()),3)


if __name__=='__main__':
    unittest.main()
