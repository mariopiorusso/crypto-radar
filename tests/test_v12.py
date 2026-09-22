import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from crypto_radar.config import normalize
from crypto_radar.db import connect, save_markets, SCHEMA
from crypto_radar.migrations import migrate, STATEMENTS
from crypto_radar.collectors.social import SocialObservation, FixtureSocialCollector
from crypto_radar.intelligence.social import social_features
from crypto_radar.intelligence.catalysts import normalize_news, news_features
from crypto_radar import experiment, experiment_ai, experiment_measurement as measurement
from crypto_radar.main import run_scan
from crypto_radar.policies import daily_usage, reserve_alert
from test_v11 import COIN, NOW, response


def buckets(now=NOW, sources=('community',)):
    return [SocialObservation(coin_id='example',source=s,
        window_start=now-timedelta(minutes=5*(i+1)),window_end=now-timedelta(minutes=5*i),
        mentions=40 if i < 3 else 10,unique_authors=20 if i < 3 else 5,
        engagement=80.0 if i < 3 else 20.0,sentiment=0.5 if i < 3 else 0.1)
        for s in sources for i in range(24)]


def feature_rows(items):
    return [dict(r.model_dump(),window_start=r.window_start.isoformat(),window_end=r.window_end.isoformat(),
                 observed_ts=r.window_end.isoformat()) for r in items]


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.cfg=normalize({'v12':{'social':{'baseline_hours':1,'min_coverage':1.0}}})['v12']['social']

    def test_acceleration_and_exact_windows(self):
        f=social_features(feature_rows(buckets()),NOW,self.cfg)
        self.assertEqual(f['status'],'ok')
        self.assertEqual((f['mentions_5m'],f['mentions_15m'],f['mentions_1h']),(40,120,210))
        self.assertEqual(f['baseline_windows'],12)
        for key in ('mention_acceleration','author_acceleration','engagement_acceleration'):
            self.assertEqual(f[key],4)
        self.assertAlmostEqual(f['sentiment_change'],0.4)
        self.assertEqual(f['score'],100)

    def test_popularity_does_not_change_acceleration(self):
        original=buckets()
        multiplied=[r.model_copy(update=dict(mentions=r.mentions*100,unique_authors=r.unique_authors*100,
                                              engagement=r.engagement*100)) for r in original]
        self.assertEqual(social_features(feature_rows(original),NOW,self.cfg)['score'],
                         social_features(feature_rows(multiplied),NOW,self.cfg)['score'])

    def test_missing_bucket_and_insufficient_history(self):
        for items in (buckets()[:5],buckets()[1:],buckets()[:-1]):
            self.assertEqual(social_features(feature_rows(items),NOW,self.cfg)['status'],'insufficient_history')
        self.assertEqual(social_features([],NOW,self.cfg)['status'],'unavailable')

    def test_no_future_or_late_arrival_leak(self):
        rows=feature_rows(buckets())
        rows[0]['observed_ts']=(NOW+timedelta(seconds=1)).isoformat()
        self.assertIsNone(social_features(rows,NOW,self.cfg)['mentions_5m'])

    def test_zero_baseline_and_missing_metrics(self):
        rows=feature_rows(buckets())
        for r in rows:
            r['unique_authors']=None
            r['engagement']=None
        f=social_features(rows,NOW,self.cfg)
        self.assertIsNone(f['author_acceleration'])
        self.assertIsNone(f['engagement_acceleration'])
        for r in rows[12:]:
            r['mentions']=0
        self.assertEqual(social_features(rows,NOW,self.cfg)['status'],'zero_baseline')

    def test_concentration_duplicates_and_sources(self):
        rows=feature_rows(buckets(sources=('a','b')))
        self.assertEqual(social_features(rows,NOW,self.cfg)['source_count'],2)
        for key in ('duplicate_fraction','top_author_fraction'):
            altered=[dict(r,**{key:0.9}) for r in rows]
            f=social_features(altered,NOW,self.cfg)
            self.assertEqual(f['status'],'suspected_promotion')
            self.assertEqual(f['score'],0)

    def test_schema_rejects_malformed_provider_metrics(self):
        data=buckets()[0].model_dump()
        for changes in ({'mentions':-1},{'mentions':True},{'unique_authors':999},
                        {'sentiment':float('nan')},{'window_end':NOW+timedelta(seconds=1)},
                        {'window_start':NOW.replace(tzinfo=None)}):
            with self.assertRaises(ValueError):
                SocialObservation.model_validate(dict(data,**changes))

    def test_configuration_fail_closed(self):
        for settings in ({'social':{'provider':'fixture'}},{'scoring':{'candidate_threshold':101}},
                         {'social':{'api_key':'secret'}},{'episodes':{'quiet_hours':0}}):
            with self.assertRaises(ValueError):
                normalize({'v12':settings})


class ExperimentCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'test.db')
        self.conn=connect(self.path)
        self.addCleanup(self.conn.close)
        self.cfg=normalize({'database':{'path':self.path},'ai':{'enabled':False},
            'v12':{'news':{'enabled':False},'social':{'enabled':True,'baseline_hours':1,'min_coverage':1.0},
                   'market_regime':{'min_broad_assets':2}}})
        self.net=patch('socket.socket.connect',side_effect=AssertionError('No network in offline tests'))
        self.net.start(); self.addCleanup(self.net.stop)
        self.env=patch.dict(os.environ,{},clear=True)
        self.env.start(); self.addCleanup(self.env.stop)

    def scan(self,mode='all',coins=None,provider=None,now=NOW,news=None):
        with patch('crypto_radar.main.now_utc',return_value=now), \
             patch('crypto_radar.main.fetch_markets',return_value=coins or [COIN]) as fetch, \
             patch('crypto_radar.main.recent_news',return_value=news or []) as rss, \
             patch('crypto_radar.main.investigate',return_value=response()) as ai, \
             patch('crypto_radar.main.email.send_alert') as email, \
             patch('crypto_radar.main.telegram.send_alert') as telegram:
            result=run_scan(self.cfg,mode,provider)
            self.assertEqual(fetch.call_count,1)
            self.last_calls=ai.call_count
            self.last_news_calls=rss.call_count
            self.last_alerts=email.call_count+telegram.call_count
            return result


class RuntimeTests(ExperimentCase):
    def test_combined_mode_persists_independent_groups(self):
        summary=self.scan(provider=FixtureSocialCollector(buckets()))
        self.assertEqual(summary['errors'],0)
        self.assertEqual(summary['v11_candidates'],1)
        self.assertEqual(summary['v12_candidates'],1)
        self.assertEqual(summary['overlapping_candidates'],1)
        rows=self.conn.execute('SELECT signal_version,experiment_group,eligible,reason FROM experiment_evaluations ORDER BY id').fetchall()
        self.assertEqual([tuple(r) for r in rows],[('1.1','market_only',1,'ai_disabled'),('1.2','social_only',1,'ai_disabled')])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM candidate_evaluations').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM experiment_outcomes').fetchone()[0],10)
        self.assertEqual(self.last_calls,0)

    def test_v11_only_does_not_collect_upstream(self):
        class Broken:
            def collect(self,*args): raise AssertionError('Must not run')
        result=self.scan('v11',provider=Broken())
        self.assertEqual(result['errors'],0)
        self.assertEqual(self.last_news_calls,0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM social_features').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT DISTINCT signal_version FROM experiment_evaluations').fetchone()[0],'1.1')

    def test_v12_only_has_no_fabricated_control(self):
        result=self.scan('v12',provider=FixtureSocialCollector(buckets()))
        self.assertEqual(result['errors'],0)
        self.assertEqual(result['v12_only_candidates'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM candidate_evaluations').fetchone()[0],0)
        self.assertIsNone(self.conn.execute('SELECT first_v11_candidate_ts FROM research_episodes').fetchone()[0])

    def test_missing_provider_gracefully_records_unavailable(self):
        result=self.scan()
        self.assertEqual(result['errors'],0)
        self.assertEqual(result['social_status'],'unavailable')
        self.assertEqual(result['v12_candidates'],0)

    def test_provider_failure_does_not_stop_control_or_outcomes(self):
        class Broken:
            def collect(self,*args): raise TimeoutError('never log provider secrets')
        result=self.scan(provider=Broken())
        self.assertEqual(result['social_status'],'failed')
        self.assertEqual(result['v11_candidates'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM experiment_evaluations').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM experiment_outcomes').fetchone()[0],10)

    def test_malformed_items_do_not_discard_valid_observations(self):
        class Partial:
            def collect(self,*args): return buckets()+[{'mentions':-1}]
        result=self.scan(provider=Partial())
        self.assertEqual(result['social_status'],'partial_error')
        self.assertEqual(result['v12_candidates'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM social_observations').fetchone()[0],24)

    def test_duplicate_social_buckets_do_not_inflate_counts(self):
        provider=FixtureSocialCollector(buckets())
        self.scan(provider=provider)
        self.scan(provider=provider,now=NOW+timedelta(seconds=1))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM social_observations').fetchone()[0],24)

    def test_stablecoins_never_eligible_or_sent_to_ai(self):
        coins=[dict(COIN,id=i) for i in ('usd1-wlfi','usd1','bfusd','tether')]
        result=self.scan(coins=coins)
        self.assertEqual(result['v11_candidates']+result['v12_candidates'],0)
        self.assertEqual(self.conn.execute('SELECT SUM(eligible) FROM experiment_evaluations').fetchone()[0],0)
        self.assertEqual(self.last_calls,0)

    def test_v11_scoring_retained_with_experiment_enabled(self):
        self.scan('v11')
        before=dict(self.conn.execute('SELECT pre_score,features_json,decision,reason FROM candidate_evaluations').fetchone())
        self.scan('all',now=NOW+timedelta(minutes=5))
        after=dict(self.conn.execute('SELECT pre_score,features_json,decision,reason FROM candidate_evaluations ORDER BY id DESC').fetchone())
        # Baseline history grows by one, but V1.1 still requires six points and retains its exact score.
        self.assertEqual(before['pre_score'],after['pre_score'])
        for key in ('decision','reason'):
            self.assertEqual(before[key],after[key])
        self.assertEqual(json.loads(before['features_json'])['components'],json.loads(after['features_json'])['components'])

    def test_future_market_history_cannot_influence_detection(self):
        for i in range(6):
            save_markets(self.conn,[dict(COIN,total_volume=10000000+i*1000000)],
                         (NOW+timedelta(hours=i+1)).isoformat())
        result=self.scan('v11')
        self.assertEqual(result['errors'],0)
        row=self.conn.execute('SELECT pre_score,features_json FROM candidate_evaluations').fetchone()
        self.assertEqual(row['pre_score'],45)
        self.assertEqual(json.loads(row['features_json'])['history_count'],0)

    def test_news_only_group_and_bounded_polling(self):
        self.cfg['v12']['news'].update(enabled=True,assets_per_scan=1)
        news=[{'title':'Example announces partnership','published':'Wed, 16 Sep 2026 12:00:00 GMT','source':'Publisher'}]
        result=self.scan(news=news)
        self.assertEqual(result['v12_candidates'],1)
        self.assertEqual(self.conn.execute("SELECT experiment_group FROM experiment_evaluations WHERE signal_version='1.2'").fetchone()[0],'news_only')
        self.scan(now=NOW+timedelta(minutes=5),news=news)
        self.assertEqual(self.last_news_calls,0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM news_evidence').fetchone()[0],1)

    def test_social_news_and_market_social_news_groups(self):
        self.cfg['v12']['news'].update(enabled=True)
        news=[{'title':'Example announces partnership','published':'Wed, 16 Sep 2026 12:00:00 GMT'}]
        self.scan(provider=FixtureSocialCollector(buckets()),news=news)
        self.assertEqual(self.conn.execute("SELECT experiment_group FROM experiment_evaluations WHERE signal_version='1.2'").fetchone()[0],'market_social_news')
        self.scan(provider=FixtureSocialCollector(buckets()),news=news,coins=[dict(COIN,total_volume=6000000)],now=NOW+timedelta(seconds=1))
        self.assertEqual(self.conn.execute("SELECT experiment_group FROM experiment_evaluations WHERE signal_version='1.2' ORDER BY id DESC").fetchone()[0],'social_news')

    def enable_ai(self):
        os.environ['OPENAI_API_KEY']='offline-test'
        self.cfg['ai']['enabled']=True
        self.cfg['v12']['ai']['enabled']=True

    def test_shared_scan_budget_and_overlap_no_double_call(self):
        self.enable_ai()
        result=self.scan(provider=FixtureSocialCollector(buckets()))
        self.assertEqual(self.last_calls,1)
        self.assertEqual(result['v11_ai_calls'],1)
        self.assertEqual(result['v12_ai_calls'],0)
        self.assertEqual(self.conn.execute("SELECT reason FROM experiment_evaluations WHERE signal_version='1.2'").fetchone()[0],'v11_assessed_same_scan')

    def test_cross_version_scores_cannot_bypass_cooldown(self):
        self.enable_ai()
        self.scan('v11')
        result=self.scan('all',provider=FixtureSocialCollector(buckets()),now=NOW+timedelta(seconds=1))
        self.assertEqual(result['ai_calls_made'],0)
        self.assertEqual(self.conn.execute("SELECT reason FROM experiment_evaluations WHERE signal_version='1.2'").fetchone()[0],'cooldown')

    def test_v12_scan_and_daily_caps(self):
        self.enable_ai()
        self.cfg['v12']['ai']['max_candidates_per_scan']=0
        self.scan('v12',provider=FixtureSocialCollector(buckets()))
        self.assertEqual(self.last_calls,0)
        self.cfg['v12']['ai']['max_candidates_per_scan']=2
        self.cfg['v12']['ai']['daily_call_budget']=0
        self.scan('v12',provider=FixtureSocialCollector(buckets()),now=NOW+timedelta(seconds=1))
        self.assertEqual(self.last_calls,0)
        self.assertEqual(self.conn.execute("SELECT reason FROM experiment_evaluations ORDER BY id DESC").fetchone()[0],'v12_daily_budget')

    def test_invalid_v12_response_preserved_and_not_assessed(self):
        self.enable_ai()
        with patch('crypto_radar.main.now_utc',return_value=NOW), \
             patch('crypto_radar.main.fetch_markets',return_value=[COIN]), \
             patch('crypto_radar.main.investigate',return_value=response({'stage':'buy'})):
            summary=run_scan(self.cfg,'v12',FixtureSocialCollector(buckets()))
        self.assertEqual(summary['errors'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],0)
        row=self.conn.execute('SELECT status,response_json FROM v12_ai_calls').fetchone()
        self.assertEqual(row['status'],'failed')
        self.assertIsNotNone(row['response_json'])

    def test_news_failure_keeps_scanner_operational(self):
        self.cfg['v12']['news']['enabled']=True
        with patch('crypto_radar.main.now_utc',return_value=NOW), \
             patch('crypto_radar.main.fetch_markets',return_value=[COIN]), \
             patch('crypto_radar.main.recent_news',side_effect=TimeoutError):
            result=run_scan(self.cfg,'all',FixtureSocialCollector(buckets()))
        self.assertEqual(result['news_status'],'partial_error')
        self.assertEqual(result['v11_candidates'],1)
        self.assertEqual(result['v12_candidates'],1)
        result=self.scan('all',provider=FixtureSocialCollector(buckets()),now=NOW+timedelta(minutes=5))
        self.assertEqual(result['errors'],0)

    def test_stale_market_after_slow_news_is_not_candidate(self):
        self.cfg['v12']['news']['enabled']=True
        time=[NOW]
        def slow_news(*args):
            time[0]=NOW+timedelta(minutes=20)
            return [{'title':'Example partnership','published':'Wed, 16 Sep 2026 12:20:00 GMT'}]
        with patch('crypto_radar.main.now_utc',side_effect=lambda:time[0]), \
             patch('crypto_radar.main.fetch_markets',return_value=[COIN]), \
             patch('crypto_radar.main.recent_news',side_effect=slow_news):
            result=run_scan(self.cfg,'v12')
        self.assertEqual(result['v12_candidates'],0)
        row=self.conn.execute('SELECT reason,detected_ts,market_ts FROM experiment_evaluations').fetchone()
        self.assertEqual(row['reason'],'stale_market_snapshot')
        self.assertNotEqual(row['detected_ts'],row['market_ts'])

    def test_experiment_measurement_failure_does_not_stop_v11(self):
        self.enable_ai()
        with patch('crypto_radar.main.experiment_measurement.fill_outcomes',side_effect=RuntimeError):
            result=self.scan('v11')
        self.assertEqual(result['v11_ai_calls'],1)
        self.assertEqual(result['errors'],1)

    def test_v12_dossier_validation_and_daily_budget(self):
        self.enable_ai()
        self.cfg['ai']['daily_call_budget']=1
        result=self.scan('v12',provider=FixtureSocialCollector(buckets()))
        self.assertEqual(result['errors'],0)
        self.assertEqual(self.last_calls,1)
        call=self.conn.execute('SELECT * FROM v12_ai_calls').fetchone()
        dossier=json.loads(json.loads(call['request_json'])['input'])
        for key in ('MARKET_EVIDENCE','NEWS_EVIDENCE','SOCIAL_EVIDENCE','MARKET_REGIME'):
            self.assertIn(key,dossier)
        self.assertNotIn('offline-test',call['request_json'])
        self.assertEqual(daily_usage(self.conn,NOW.date().isoformat()),1)
        self.scan('v11',now=NOW+timedelta(minutes=5))
        self.assertEqual(self.last_calls,0)
        self.assertEqual(self.conn.execute('SELECT reason FROM candidate_evaluations').fetchone()[0],'daily_budget')

    def test_cooldown_restart_and_strengthening(self):
        self.scan('v12',provider=FixtureSocialCollector(buckets()))
        e=dict(self.conn.execute("SELECT * FROM experiment_evaluations WHERE signal_version='1.2'").fetchone())
        request={'model':'offline','input':'{}'}
        e['pre_score']=50
        call,reason=experiment_ai.reserve(self.conn,e,NOW,self.cfg,request)
        self.assertIsNotNone(call)
        self.scan('v12',provider=FixtureSocialCollector(buckets()),now=NOW+timedelta(minutes=5))
        e=dict(self.conn.execute('SELECT * FROM experiment_evaluations ORDER BY id DESC').fetchone())
        e['pre_score']=59
        with closing(connect(self.path)) as reopened:
            self.assertEqual(experiment_ai.reserve(reopened,e,NOW+timedelta(minutes=5),self.cfg,request)[1],'cooldown')
            e['pre_score']=60
            self.assertIsNotNone(experiment_ai.reserve(reopened,e,NOW+timedelta(minutes=5),self.cfg,request)[0])

    def test_persistent_shared_alert_episode(self):
        self.enable_ai()
        self.scan('v12',provider=FixtureSocialCollector(buckets()))
        aid=self.conn.execute('SELECT id FROM assessments').fetchone()[0]
        ep=self.conn.execute('SELECT id FROM research_episodes').fetchone()[0]
        first=reserve_alert(self.conn,aid,'example',1,'email',80,'test',NOW,10,ep)
        self.assertIsNotNone(first)
        # Different legacy episode cannot duplicate the same continuing research episode.
        self.assertIsNone(reserve_alert(self.conn,aid,'example',2,'email',85,'test',NOW,10,ep))
        self.assertIsNotNone(reserve_alert(self.conn,aid,'example',1,'telegram',80,'test',NOW,10,ep))
        self.assertEqual(self.last_alerts,0)


class MeasurementTests(ExperimentCase):
    def test_relative_returns_exact_windows_and_frozen_basket(self):
        markets=[COIN,dict(COIN,id='bitcoin',current_price=100),dict(COIN,id='ethereum',current_price=50)]
        self.scan('v11',coins=markets)
        later=NOW+timedelta(hours=1,minutes=3)
        save_markets(self.conn,[dict(COIN,current_price=12),dict(COIN,id='bitcoin',current_price=110),
                                dict(COIN,id='ethereum',current_price=52.5)],later.isoformat())
        measurement.fill_outcomes(self.conn,later,self.cfg)
        row=self.conn.execute('''SELECT o.* FROM experiment_outcomes o JOIN experiment_evaluations e
            ON e.id=o.evaluation_id WHERE e.coin_id='example' AND horizon_hours=1''').fetchone()
        self.assertEqual(row['timing_error_seconds'],180)
        self.assertEqual(row['timing_status'],'within_tolerance')
        for key,value in (('return_pct',20),('btc_return_pct',10),('eth_return_pct',5),('broad_return_pct',7.5),
                          ('excess_btc_pct',10),('excess_eth_pct',15),('excess_broad_pct',12.5)):
            self.assertAlmostEqual(row[key],value)

    def test_missing_and_late_benchmark_not_fabricated(self):
        self.scan('v11',coins=[COIN,dict(COIN,id='bitcoin')])
        later=NOW+timedelta(hours=2)
        save_markets(self.conn,[dict(COIN,current_price=12)],later.isoformat())
        measurement.fill_outcomes(self.conn,later,self.cfg)
        row=self.conn.execute('''SELECT o.* FROM experiment_outcomes o JOIN experiment_evaluations e
            ON e.id=o.evaluation_id WHERE e.coin_id='example' AND horizon_hours=1''').fetchone()
        self.assertEqual(row['timing_status'],'late')
        self.assertIsNone(row['btc_return_pct'])
        self.assertIsNone(row['excess_btc_pct'])
        self.assertEqual(row['benchmark_status'],'partial_or_missing')

    def test_chronology_negative_and_positive_leads(self):
        save_markets(self.conn,[dict(COIN,current_price=9)],(NOW-timedelta(hours=1)).isoformat())
        save_markets(self.conn,[COIN],(NOW-timedelta(minutes=10)).isoformat())
        self.scan('v12',provider=FixtureSocialCollector(buckets()))
        self.scan('v11',now=NOW+timedelta(minutes=5))
        measurement.fill_outcomes(self.conn,NOW+timedelta(minutes=5),self.cfg)
        row=self.conn.execute('SELECT * FROM experiment_lead_times').fetchone()
        self.assertAlmostEqual(row['v12_before_v11_minutes'],5,places=4)
        self.assertAlmostEqual(row['v12_before_move_minutes'],-10,places=4)
        self.assertEqual(row['anchor_price'],9)

    def test_episode_does_not_reset_over_unobserved_downtime(self):
        self.scan('v11')
        self.scan('v11',now=NOW+timedelta(days=2))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM research_episodes').fetchone()[0],1)

    def test_episode_resets_after_continuously_observed_quiet_period(self):
        self.cfg['v12']['episodes']['quiet_hours']=1
        self.scan('v11')
        for minute in range(5,60,5):
            save_markets(self.conn,[dict(COIN,total_volume=6000000)],(NOW+timedelta(minutes=minute)).isoformat())
        self.scan('v11',now=NOW+timedelta(hours=1))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM research_episodes').fetchone()[0],2)

    def test_future_move_not_used_before_it_is_observed(self):
        self.scan('v11')
        later=NOW+timedelta(hours=1)
        save_markets(self.conn,[dict(COIN,current_price=11)],later.isoformat())
        measurement.fill_moves(self.conn,NOW)
        self.assertIsNone(self.conn.execute('SELECT significant_move_ts FROM research_episodes').fetchone()[0])
        measurement.fill_moves(self.conn,later)
        self.assertEqual(self.conn.execute('SELECT significant_move_ts FROM research_episodes').fetchone()[0],later.isoformat())

    def test_news_first_seen_immutable_and_no_false_confirmations(self):
        item={'title':'Example partnership - Publisher','source':'Publisher','published':'Wed, 16 Sep 2026 11:00:00 GMT'}
        experiment.ingest_news(self.conn,COIN,[item],NOW,self.cfg['v12']['news'])
        experiment.ingest_news(self.conn,COIN,[item,dict(item,source='Syndicator')],NOW+timedelta(hours=1),self.cfg['v12']['news'])
        rows=self.conn.execute('SELECT * FROM news_evidence').fetchall()
        self.assertEqual(rows[0]['first_observed_ts'],NOW.isoformat())
        at_detection=news_features(rows,NOW,self.cfg['v12']['news'])
        self.assertEqual(len(at_detection['evidence']),1)
        self.assertIsNone(at_detection['evidence'][0]['independent_confirmations'])
        self.assertEqual(at_detection['unique_headlines'],1)


class MigrationV12Tests(unittest.TestCase):
    def legacy(self,path):
        conn=sqlite3.connect(path)
        conn.executescript(SCHEMA)
        for statement in STATEMENTS:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations VALUES (1,'original')")
        conn.execute('PRAGMA user_version=1')
        conn.execute("INSERT INTO market_observations(ts,coin_id,symbol,name,price) VALUES ('old','asset','a','Asset',1)")
        conn.execute("INSERT INTO assessments(ts,coin_id,symbol,pre_score) VALUES ('old','asset','a',50)")
        conn.execute("INSERT INTO outcomes(assessment_id,horizon_hours,target_ts,start_price) VALUES (1,1,'later',1)")
        conn.commit()
        return conn

    def test_v1_backup_preservation_idempotence_foreign_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db.sqlite'
            with closing(self.legacy(path)) as conn:
                before={t:conn.execute('SELECT * FROM '+t).fetchall() for t in ('market_observations','assessments','outcomes','candidate_evaluations')}
                backup=migrate(conn,path)
                with closing(sqlite3.connect(backup)) as old:
                    self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0],1)
                    for table,records in before.items():
                        self.assertEqual(old.execute('SELECT * FROM '+table).fetchall(),records)
                        actual=conn.execute('SELECT * FROM '+table).fetchall()
                        self.assertEqual([r[:len(records[0])] for r in actual] if records else actual,records)
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],2)
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertIsNone(migrate(conn,path))

    def test_v12_failure_rolls_back_every_statement(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db.sqlite'
            with closing(self.legacy(path)) as conn:
                with patch('crypto_radar.experiment_schema.STATEMENTS',['CREATE TABLE partial(x)','INVALID SQL']):
                    with self.assertRaises(sqlite3.OperationalError):
                        migrate(conn,path)
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],1)
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],1)
