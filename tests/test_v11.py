import copy
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from pydantic import ValidationError
from crypto_radar.config import normalize, snapshot
from crypto_radar.db import connect, SCHEMA, save_markets
from crypto_radar.filters import is_stablecoin
from crypto_radar.intelligence.statistical import score_candidate
from crypto_radar.intelligence.schemas import Assessment
from crypto_radar.intelligence.ai import validate_response
from crypto_radar.migrations import migrate
from crypto_radar.outcomes import create_outcomes, fill_due_outcomes
from crypto_radar.policies import cooldown_allowed, reserve_call, reserve_alert, update_episode
from crypto_radar.main import run_scan
from crypto_radar.alerts import email
from crypto_radar.operational import process_lock
from crypto_radar.intelligence.ai import investigate

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
COIN = dict(id="example", name="Example", symbol="ex", current_price=10,
            market_cap=100000000, total_volume=40000000, price_change_percentage_24h=0)
VALID = dict(surge_score=85, stage="early", catalyst_strength=8, manipulation_risk=3,
             confidence=0.8, thesis="Evidence", risks="Uncertainty")


def response(data=None, status="completed"):
    obj = SimpleNamespace(status=status, output_text=json.dumps(VALID if data is None else data),
                          model="test-model", usage=None)
    obj.model_dump_json = lambda: json.dumps(dict(status=obj.status, output_text=obj.output_text, model=obj.model))
    return obj


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "radar.db")
        self.conn = connect(self.path)
        self.addCleanup(self.conn.close)
        self.cfg = normalize({"database": {"path": self.path}})
        self.conn.execute("INSERT INTO scan_runs VALUES (1,? ,NULL,'running','{}','hash','test','test',NULL)", (NOW.isoformat(),))
        self.conn.commit()

    def evaluation(self, coin="example"):
        sid = self.conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM scan_runs").fetchone()[0]
        self.conn.execute("INSERT INTO scan_runs (id,started_ts,status,config_json,config_hash,app_version,scoring_version) VALUES (?,?,'running','{}','hash','test','test')", (sid,NOW.isoformat()))
        eid = self.conn.execute("INSERT INTO candidate_evaluations(scan_id,coin_id,market_ts,decision) VALUES (?,?,?,'candidate')", (sid,coin,NOW.isoformat())).lastrowid
        self.conn.commit()
        return eid

    def assessment(self):
        aid = self.conn.execute("INSERT INTO assessments(ts,coin_id,symbol,pre_score) VALUES (?,'example','ex',50)", (NOW.isoformat(),)).lastrowid
        self.conn.commit()
        return aid


class ScoringTests(unittest.TestCase):
    def test_score_components_and_baseline(self):
        cfg = normalize({})
        hist = [{"total_volume": v} for v in (10000000,11000000,12000000,13000000,14000000,15000000)]
        score = score_candidate(COIN, hist, cfg["filters"])
        self.assertEqual(score["pre_score"],100)
        self.assertEqual(score["history_count"],6)
        self.assertEqual(score["baseline_mean"],12500000)
        self.assertEqual(sum(score["components"].values()),100)

    def test_cold_start_and_price_penalty(self):
        cfg = normalize({})["filters"]
        self.assertEqual(score_candidate(COIN,[],cfg)["pre_score"],45)
        self.assertEqual(score_candidate(dict(COIN,price_change_percentage_24h=10),[],cfg)["pre_score"],30)
        self.assertIsNone(score_candidate(dict(COIN,price_change_percentage_24h=36),[],cfg))

    def test_invalid_and_illiquid(self):
        for edits in ({"current_price":0},{"total_volume":float("nan")},{"market_cap":1}):
            self.assertIsNone(score_candidate(dict(COIN,**edits),[],normalize({})["filters"]))

    def test_stablecoin_ids_not_symbols(self):
        cfg = normalize({})
        self.assertTrue(is_stablecoin(dict(COIN,id="tether"),cfg))
        self.assertFalse(is_stablecoin(dict(COIN,symbol="usdt",current_price=1),cfg))
        cfg["filters"]["stablecoin_ids"] = ["example"]
        self.assertTrue(is_stablecoin(COIN,cfg))

    def test_config_rejects_secrets_and_invalid_values(self):
        for cfg in ({"api_key":"secret"},{"ai":{"daily_call_budget":-1}},{"ai":{"enabled":"false"}}):
            with self.assertRaises(ValueError):
                normalize(cfg)
        self.assertEqual(normalize({})["ai"]["daily_call_budget"],50)
        self.assertEqual(snapshot(normalize({})),snapshot(normalize({})))


class ValidationTests(unittest.TestCase):
    def test_valid_boundaries(self):
        for score in (0,100):
            self.assertEqual(Assessment.model_validate(dict(VALID,surge_score=score)).surge_score,score)

    def test_reject_invalid_fields(self):
        for changes in ({"surge_score":101},{"surge_score":True},{"surge_score":"80"},
                        {"catalyst_strength":11},{"manipulation_risk":-1},
                        {"confidence":float("nan")},{"confidence":1.1},{"stage":"buy"},
                        {"extra":1},{"thesis":"x"*351}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Assessment.model_validate(dict(VALID,**changes))
        with self.assertRaises(ValidationError):
            Assessment.model_validate({})

    def test_incomplete_and_missing_output(self):
        with self.assertRaises(ValueError):
            validate_response(response(status="incomplete"))
        with self.assertRaises(ValueError):
            validate_response(response({}))


class PolicyTests(DatabaseCase):
    def test_cooldown_strength_and_restart(self):
        reserve_call(self.conn,self.evaluation(),"example",50,NOW,self.cfg["ai"],"test","{}")
        self.assertFalse(cooldown_allowed(self.conn,"example",59,NOW+timedelta(minutes=5),self.cfg["ai"]))
        self.assertTrue(cooldown_allowed(self.conn,"example",60,NOW+timedelta(minutes=5),self.cfg["ai"]))
        self.assertTrue(cooldown_allowed(self.conn,"example",50,NOW+timedelta(minutes=60),self.cfg["ai"]))
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory=sqlite3.Row
            self.assertFalse(cooldown_allowed(conn,"example",50,NOW,self.cfg["ai"]))

    def test_budget_failure_and_utc_rollover(self):
        cfg = dict(self.cfg["ai"],daily_call_budget=1)
        first,_ = reserve_call(self.conn,self.evaluation(),"example",50,NOW,cfg,"test","{}")
        self.conn.execute("UPDATE ai_calls SET status='failed' WHERE id=?",(first,)); self.conn.commit()
        eid=self.evaluation("other")
        self.assertEqual(reserve_call(self.conn,eid,"other",50,NOW,cfg,"test","{}")[1],"daily_budget")
        self.assertIsNotNone(reserve_call(self.conn,eid,"other",50,NOW+timedelta(days=1),cfg,"test","{}")[0])

    def test_alert_channels_and_strengthening(self):
        aid=self.assessment()
        self.assertIsNotNone(reserve_alert(self.conn,aid,"example",1,"email",80,"text",NOW,10))
        self.assertIsNotNone(reserve_alert(self.conn,aid,"example",1,"telegram",80,"text",NOW,10))
        self.assertIsNone(reserve_alert(self.conn,self.assessment(),"example",1,"email",89,"text",NOW,10))
        self.assertIsNotNone(reserve_alert(self.conn,self.assessment(),"example",1,"email",90,"text",NOW,10))
        self.assertIsNotNone(reserve_alert(self.conn,self.assessment(),"example",2,"email",80,"text",NOW,10))

    def test_episode_reset_excludes_downtime(self):
        self.cfg["alerts"]["episode_reset_hours"]=1
        for minute in range(0,65,5):
            episode=update_episode(self.conn,"example",False,NOW+timedelta(minutes=minute),self.cfg)
        self.assertEqual(episode,2)
        episode=update_episode(self.conn,"example",False,NOW+timedelta(days=2),self.cfg)
        self.assertEqual(episode,2)


class OutcomeTests(DatabaseCase):
    def test_exact_target_and_immutable_history(self):
        aid=self.assessment()
        create_outcomes(self.conn,aid,NOW.isoformat(),10); self.conn.commit()
        target=NOW+timedelta(hours=1)
        save_markets(self.conn,[dict(COIN,current_price=11)],target.isoformat())
        save_markets(self.conn,[dict(COIN,current_price=99)],target.isoformat())
        fill_due_outcomes(self.conn,target-timedelta(seconds=1))
        self.assertIsNone(self.conn.execute('SELECT later_price FROM outcomes WHERE horizon_hours=1').fetchone()[0])
        fill_due_outcomes(self.conn,target)
        row=self.conn.execute('SELECT * FROM outcomes WHERE horizon_hours=1').fetchone()
        self.assertEqual(row['later_price'],11)
        self.assertEqual(row['timing_error_seconds'],0)

    def test_timing_and_earliest_observation(self):
        aid=self.assessment()
        create_outcomes(self.conn,aid,NOW.isoformat(),10,600); self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0],5)
        save_markets(self.conn,[dict(COIN,current_price=11)],(NOW+timedelta(hours=1,minutes=10)).isoformat())
        save_markets(self.conn,[dict(COIN,current_price=12)],(NOW+timedelta(hours=2)).isoformat())
        fill_due_outcomes(self.conn,NOW+timedelta(hours=4))
        row=self.conn.execute("SELECT * FROM outcomes WHERE horizon_hours=1").fetchone()
        self.assertEqual(row["timing_status"],"within_tolerance")
        self.assertEqual(row["timing_error_seconds"],600)
        self.assertAlmostEqual(row["return_pct"],10)
        self.assertEqual(self.conn.execute("SELECT timing_status FROM outcomes WHERE horizon_hours=3").fetchone()[0],"missing")
        save_markets(self.conn,[COIN],(NOW+timedelta(hours=5)).isoformat())
        fill_due_outcomes(self.conn,NOW+timedelta(hours=5))
        self.assertEqual(self.conn.execute("SELECT timing_status FROM outcomes WHERE horizon_hours=3").fetchone()[0],"late")


class MigrationTests(unittest.TestCase):
    def test_backup_preservation_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.db"
            conn=sqlite3.connect(path); conn.executescript(SCHEMA)
            conn.execute("INSERT INTO market_observations VALUES ('2026-01-01','coin','c','Coin',1,2,3,4)")
            conn.execute("INSERT INTO assessments(id,ts,coin_id,symbol,pre_score) VALUES (1,'2026-01-01','coin','c',10)")
            conn.execute("INSERT INTO outcomes(assessment_id,horizon_hours,target_ts,start_price) VALUES (1,1,'2026-01-01',1)")
            conn.commit()
            before={t:conn.execute('SELECT * FROM '+t).fetchall() for t in ('market_observations','assessments','outcomes')}
            backup=migrate(conn,path)
            self.assertTrue(Path(backup).exists())
            self.assertIsNone(migrate(conn,path))
            with closing(sqlite3.connect(backup)) as old:
                for table,rows in before.items():
                    self.assertEqual(old.execute('SELECT * FROM '+table).fetchall(),rows)
                    actual=conn.execute('SELECT * FROM '+table).fetchall()
                    self.assertEqual([r[:len(rows[0])] for r in actual],rows)
            conn.close()

    def test_transaction_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.db"
            conn=sqlite3.connect(path); conn.executescript(SCHEMA)
            with patch('crypto_radar.migrations.STATEMENTS',["ALTER TABLE assessments ADD COLUMN test TEXT","INVALID SQL"]):
                with self.assertRaises(sqlite3.OperationalError):
                    migrate(conn,path)
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertNotIn('test',[r[1] for r in conn.execute('PRAGMA table_info(assessments)')])
            conn.close()


class ScanTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        # Network must fail closed, even if a test forgets a specific transport mock.
        self.network=patch('socket.socket.connect',side_effect=AssertionError('Network forbidden in tests'))
        self.network.start(); self.addCleanup(self.network.stop)

    def test_all_evaluations_and_full_dossier(self):
        coins=[COIN,dict(COIN,id='tether'),dict(COIN,id='weak',total_volume=6000000)]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'},clear=True), patch('crypto_radar.main.fetch_markets',return_value=coins), patch('crypto_radar.main.recent_news',return_value=[{'title':'News'}]), patch('crypto_radar.main.investigate',return_value=response()) as ai:
            summary=run_scan(self.cfg)
        self.assertEqual(ai.call_count,1)
        self.assertEqual(summary['errors'],0)
        rows=self.conn.execute('SELECT coin_id,decision FROM candidate_evaluations').fetchall()
        self.assertEqual(dict(rows),{'example':'assessed','tether':'excluded','weak':'rejected'})
        call=self.conn.execute('SELECT * FROM ai_calls').fetchone()
        dossier=json.loads(json.loads(call['request_json'])['input'])
        self.assertEqual(dossier['market_snapshot'],COIN)
        self.assertEqual(dossier['news'],[{'title':'News'}])
        self.assertNotIn('OPENAI_API_KEY',call['request_json'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],5)

    def test_invalid_ai_is_recorded_without_assessment(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'},clear=True), patch('crypto_radar.main.fetch_markets',return_value=[COIN]), patch('crypto_radar.main.recent_news',return_value=[]), patch('crypto_radar.main.investigate',return_value=response({'surge_score':999})):
            summary=run_scan(self.cfg)
        self.assertEqual(summary['errors'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT status FROM ai_calls').fetchone()[0],'failed')
        self.assertIsNotNone(self.conn.execute('SELECT response_json FROM ai_calls').fetchone()[0])

    def test_disabled_ai_and_scan_limit_are_preserved(self):
        self.cfg['ai']['enabled']=False
        with patch('crypto_radar.main.fetch_markets',return_value=[COIN]), patch('crypto_radar.main.investigate') as ai:
            run_scan(self.cfg)
        ai.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT reason FROM candidate_evaluations').fetchone()[0],'ai_disabled')
        self.cfg['ai']['enabled']=True
        self.cfg['ai']['max_candidates_per_scan']=0
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'},clear=True), patch('crypto_radar.main.fetch_markets',return_value=[COIN]), patch('crypto_radar.main.investigate') as ai:
            run_scan(self.cfg)
        ai.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT reason FROM candidate_evaluations ORDER BY id DESC LIMIT 1').fetchone()[0],'scan_limit')

    def test_channel_failure_does_not_block_other_channel(self):
        self.cfg['alerts']['email_enabled']=True
        env={'OPENAI_API_KEY':'test','TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','SMTP_HOST':'test','EMAIL_FROM':'from@example.invalid','EMAIL_TO':'to@example.invalid'}
        with patch.dict(os.environ,env,clear=True), patch('crypto_radar.main.fetch_markets',return_value=[COIN]), patch('crypto_radar.main.recent_news',return_value=[]), patch('crypto_radar.main.investigate',return_value=response()), patch('crypto_radar.main.telegram.send_alert',side_effect=TimeoutError), patch('crypto_radar.main.email.send_alert',return_value=None) as mail:
            summary=run_scan(self.cfg)
        mail.assert_called_once()
        self.assertEqual(summary['errors'],1)
        self.assertEqual(dict(self.conn.execute('SELECT channel,status FROM alert_events').fetchall()),{'telegram':'delivery_unknown','email':'sent'})

    def test_email_transport_is_mocked(self):
        env={'SMTP_HOST':'smtp.example.invalid','EMAIL_FROM':'from@example.invalid','EMAIL_TO':'to@example.invalid','SMTP_USERNAME':'user','SMTP_PASSWORD':'password'}
        with patch.dict(os.environ,env,clear=True), patch('crypto_radar.alerts.email.ssl.create_default_context'), patch('crypto_radar.alerts.email.smtplib.SMTP') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value={}
            email.send_alert('Test')
            client=smtp.return_value.__enter__.return_value
            client.starttls.assert_called_once()
            client.login.assert_called_once_with('user','password')
            client.send_message.assert_called_once()

    def test_local_relay_uses_loopback_without_tls_or_credentials(self):
        env={'SMTP_HOST':'127.0.0.1','SMTP_PORT':'1025','SMTP_SECURITY':'local',
             'EMAIL_FROM':'radar@example.invalid','EMAIL_TO':'to@example.invalid'}
        with patch.dict(os.environ,env,clear=True), patch('crypto_radar.alerts.email.smtplib.SMTP') as smtp:
            client=smtp.return_value.__enter__.return_value
            client.send_message.return_value={}
            email.send_alert('Test')
            smtp.assert_called_once_with('127.0.0.1',1025,timeout=60)
            client.starttls.assert_not_called()
            client.login.assert_not_called()

    def test_local_relay_rejects_remote_hosts_and_credentials(self):
        env={'SMTP_SECURITY':'local','EMAIL_FROM':'radar@example.invalid','EMAIL_TO':'to@example.invalid'}
        for extra in ({'SMTP_HOST':'192.168.1.1'}, {'SMTP_HOST':'smtp.example.invalid'},
                      {'SMTP_HOST':'127.0.0.1','SMTP_PASSWORD':'secret'}):
            with patch.dict(os.environ,dict(env,**extra),clear=True), patch('crypto_radar.alerts.email.smtplib.SMTP') as smtp:
                with self.assertRaises(ValueError):
                    email.send_alert('Test')
                smtp.assert_not_called()

    def test_repeated_scan_cooldown_and_alert_dedup(self):
        env={'OPENAI_API_KEY':'test','SMTP_HOST':'test','EMAIL_FROM':'from@example.invalid','EMAIL_TO':'to@example.invalid'}
        self.cfg['alerts']['email_enabled']=True
        with patch.dict(os.environ,env,clear=True), patch('crypto_radar.main.fetch_markets',return_value=[COIN]), patch('crypto_radar.main.recent_news',return_value=[]), patch('crypto_radar.main.investigate',return_value=response()) as ai, patch('crypto_radar.main.email.send_alert') as mail, patch('crypto_radar.main.now_utc',return_value=NOW):
            mail.return_value=None
            run_scan(self.cfg)
            second=run_scan(self.cfg)
            self.assertEqual(second['candidates_suppressed_cooldown'],1)
            self.assertEqual(ai.call_count,1)
            with patch('crypto_radar.main.now_utc',return_value=NOW+timedelta(hours=1)):
                third=run_scan(self.cfg)
            self.assertEqual(ai.call_count,2)
            self.assertEqual(third['alerts_suppressed'],1)
            mail.assert_called_once()

    def test_daily_budget_records_non_ai_candidates(self):
        self.cfg['ai']['daily_call_budget']=0
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'},clear=True), patch('crypto_radar.main.fetch_markets',return_value=[COIN]), patch('crypto_radar.main.investigate') as ai, patch('crypto_radar.main.recent_news') as news:
            run_scan(self.cfg)
        ai.assert_not_called(); news.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT reason FROM candidate_evaluations').fetchone()[0],'daily_budget')

    def test_collection_failure_persists_summary(self):
        with patch('crypto_radar.main.fetch_markets',side_effect=TimeoutError):
            summary=run_scan(self.cfg)
        self.assertEqual(summary['errors'],1)
        row=self.conn.execute('SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(row['status'],'errors')
        self.assertIn('duration_seconds',json.loads(row['summary_json']))

    def test_sdk_retries_disabled_without_live_call(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'},clear=True), patch('crypto_radar.intelligence.ai.OpenAI') as client:
            investigate({'model':'test','input':'dossier'},30)
            client.assert_called_once_with(api_key='test',timeout=30,max_retries=0)
            client.return_value.__enter__.return_value.responses.create.assert_called_once_with(model='test',input='dossier')

    def test_process_lock_excludes_second_instance(self):
        with process_lock(self.path):
            with self.assertRaises(OSError):
                with process_lock(self.path):
                    self.fail('Second scanner acquired lock')


if __name__ == '__main__':
    unittest.main()
