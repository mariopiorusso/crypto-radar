import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from . import __version__
from .config import load_config, normalize, snapshot, ROOT
from .collectors.market import fetch_markets
from .collectors.news import recent_news
from .intelligence.statistical import score_candidate, SCORING_VERSION
from .intelligence.ai import build_request, investigate, validate_response
from .alerts import email, telegram
from .db import connect, save_markets, history
from .filters import is_stablecoin
from .outcomes import create_outcomes, fill_due_outcomes
from .policies import cooldown_allowed, reserve_call, update_episode, reserve_alert, daily_usage
from .operational import process_lock, setup_logging
from . import experiment, experiment_measurement

log = logging.getLogger(__name__)


def now_utc():
    return datetime.now(timezone.utc)


def deliver_alerts(conn, cfg, aid, coin, result, episode, summary):
    research = conn.execute('''SELECT e.episode_id FROM experiment_evaluations e JOIN assessments a
        ON e.id=a.experiment_evaluation_id OR e.v11_evaluation_id=a.evaluation_id WHERE a.id=?''',(aid,)).fetchone()
    text = (f"CRYPTO RADAR — EXPERIMENTAL SIGNAL\n"
            f"{coin['name']} ({coin['symbol'].upper()}) — {result['surge_score']}/100\n"
            f"Stage: {result['stage']}\n{result['thesis']}\nRisks: {result['risks']}\n"
            "Experimental research signal — not investment advice.")
    channels = []
    if cfg["alerts"]["telegram_enabled"]:
        ready = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        if ready:
            channels.append(("telegram", telegram.send_alert))
    if cfg["alerts"]["email_enabled"]:
        if email.configured():
            channels.append(("email", email.send_alert))
        else:
            summary["errors"] += 1
            log.error("Email enabled but SMTP_HOST/EMAIL_FROM/EMAIL_TO missing")
    if not channels:
        channels.append(("console", lambda message: log.info("%s", message)))
    for channel, sender in channels:
        event_id = reserve_alert(conn, aid, coin["id"], episode, channel,
                                 result["surge_score"], text, now_utc(),
                                 cfg["alerts"]["material_score_increase"], research[0] if research else None)
        if event_id is None:
            summary["alerts_suppressed"] += 1
            continue
        summary["alerts_generated"] += 1
        try:
            message_id = sender(text)
            status = "console" if channel == "console" else "sent"
            conn.execute("UPDATE alert_events SET status=?,message_id=? WHERE id=?",
                         (status, message_id, event_id))
            summary["alerts_sent"] += int(channel != "console")
        except Exception as exc:
            # Exception text can contain credentials or recipient addresses.
            conn.execute("UPDATE alert_events SET status='delivery_unknown',error=? WHERE id=?",
                         (type(exc).__name__, event_id))
            summary["errors"] += 1
            log.error("Alert channel=%s delivery uncertain: %s", channel, type(exc).__name__)
        conn.commit()


def run_scan(cfg, detector=None, social_provider=None):
    cfg = normalize(cfg)
    detector = detector or ('all' if cfg['v12']['enabled'] else 'v11')
    if detector not in ('v11','v12','all'):
        raise ValueError('Unknown detector mode')
    # Explicit CLI/function mode overrides the configuration enable switch.
    cfg['v12']['enabled'] = detector in ('v12','all')
    started = time.monotonic()
    summary = dict.fromkeys(("coins_fetched", "coins_excluded", "candidates_found",
        "candidates_suppressed_cooldown", "ai_calls_made", "alerts_generated",
        "alerts_sent", "alerts_suppressed", "errors", "v11_candidates", "v12_candidates",
        "overlapping_candidates", "v12_only_candidates", "v11_ai_calls", "v12_ai_calls",
        "v12_suppressed", "v12_only_already_moved", "v12_only_no_move_yet"), 0)
    summary.update(detector=detector, social_status='disabled', news_status='on_demand_v11')
    conn = None
    scan_id = None
    try:
        conn = connect(cfg["database"]["path"])
        config_json, config_hash = snapshot(cfg)
        scan_id = conn.execute("""INSERT INTO scan_runs
            (started_ts,status,config_json,config_hash,app_version,scoring_version)
            VALUES (?,'running',?,?,?,?)""",
            (now_utc().isoformat(), config_json, config_hash, __version__, SCORING_VERSION)).lastrowid
        conn.commit()
        markets = fetch_markets(cfg["currency"], cfg["coin_count"])
        observed = now_utc()
        market_ts = save_markets(conn, markets, observed.isoformat())
        summary["coins_fetched"] = len(markets)
        fill_due_outcomes(conn, observed, cfg["outcomes"]["tolerance_seconds"])
        try:
            experiment_measurement.fill_outcomes(conn, observed, cfg)
        except Exception as exc:
            conn.rollback()
            summary['errors'] += 1
            log.error('Experimental measurement failed: %s',type(exc).__name__)
        candidates = []
        records, stats_by_coin, news_cache = [], {}, {}
        for coin in markets:
            hist = []
            for row in history(conn, coin['id']):
                historical_ts = datetime.fromisoformat(row['ts'])
                if historical_ts.tzinfo is None:
                    historical_ts = historical_ts.replace(tzinfo=timezone.utc)
                if historical_ts < observed:
                    hist.append(dict(row))
            stats = None
            if is_stablecoin(coin, cfg):
                decision, reason = "excluded", "stablecoin"
            else:
                stats = score_candidate(coin, hist, cfg["filters"])
                if stats is None:
                    decision, reason = "excluded", "invalid_data_or_market_filters"
                elif stats["pre_score"] < cfg["ai"]["min_pre_score"]:
                    decision, reason = "rejected", "below_threshold"
                else:
                    decision, reason = "candidate", None
            eligible = decision == "candidate"
            stats_by_coin[coin['id']] = stats
            if detector == 'v12':
                summary['coins_excluded'] += int(decision == 'excluded')
                continue
            episode = update_episode(conn, coin["id"], eligible, observed, cfg)
            eid = conn.execute("""INSERT INTO candidate_evaluations
                (scan_id,coin_id,market_ts,pre_score,features_json,decision,reason)
                VALUES (?,?,?,?,?,?,?)""", (scan_id, coin["id"], market_ts,
                stats["pre_score"] if stats else None,
                json.dumps(stats, allow_nan=False) if stats else None, decision, reason)).lastrowid
            summary["coins_excluded"] += int(decision == "excluded")
            records.append((coin, stats, eid, eligible))
            if eligible:
                summary["candidates_found"] += 1
                candidates.append((coin, stats, hist, eid, episode))
        conn.commit()
        summary['v11_candidates'] = len(candidates)
        try:
            experiment.mirror_v11(conn, scan_id, records, markets, observed, market_ts, cfg)
        except Exception as exc:
            conn.rollback()
            summary['errors'] += 1
            log.error('Control measurement failed: %s',type(exc).__name__)
        candidates.sort(key=lambda item: item[1]["pre_score"], reverse=True)
        for coin, stats, hist, eid, episode in candidates:
            call_id = None
            try:
                reason = None
                if not cfg["ai"]["enabled"]:
                    reason = "ai_disabled"
                elif not os.getenv("OPENAI_API_KEY", "").strip():
                    reason = "missing_api_key"
                elif not cooldown_allowed(conn, coin["id"], stats["pre_score"], now_utc(), cfg["ai"]):
                    reason = "cooldown"
                elif summary["ai_calls_made"] >= cfg["ai"]["max_candidates_per_scan"]:
                    reason = "scan_limit"
                elif daily_usage(conn, now_utc().date().isoformat()) >= cfg["ai"]["daily_call_budget"]:
                    reason = "daily_budget"
                if not reason:
                    news = recent_news(coin["name"], coin["symbol"], cfg["ai"]["max_news_items"])
                    news_cache[coin['id']] = (news, now_utc())
                    request = build_request(coin, stats, news, hist, config_json, config_hash, market_ts, cfg["ai"])
                    call_id, reason = reserve_call(conn, eid, coin["id"], stats["pre_score"], now_utc(),
                        cfg["ai"], request["model"], json.dumps(request, ensure_ascii=False, allow_nan=False))
                if reason:
                    conn.execute("UPDATE candidate_evaluations SET decision='suppressed',reason=? WHERE id=?", (reason, eid))
                    conn.commit()
                    summary["candidates_suppressed_cooldown"] += int(reason == "cooldown")
                    continue
                summary["ai_calls_made"] += 1
                summary['v11_ai_calls'] += 1
                response = investigate(request, cfg["ai"]["timeout_seconds"])
                # Persist raw output before validation, including refusals and malformed responses.
                conn.execute("""UPDATE ai_calls SET response_json=?,returned_model=?,usage_json=? WHERE id=?""",
                    (response.model_dump_json(), response.model,
                     response.usage.model_dump_json() if response.usage else None, call_id))
                conn.commit()
                result = validate_response(response)
                completed_ts = now_utc().isoformat()
                with conn:
                    aid = conn.execute("""INSERT INTO assessments
                        (ts,coin_id,symbol,pre_score,surge_score,stage,catalyst_strength,
                         manipulation_risk,confidence,thesis,risks,raw_json,ai_call_id,
                         evaluation_id,market_observed_ts,assessment_kind)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ai')""",
                        (completed_ts, coin["id"], coin["symbol"], stats["pre_score"],
                         result["surge_score"], result["stage"], result["catalyst_strength"],
                         result["manipulation_risk"], result["confidence"], result["thesis"],
                         result["risks"], json.dumps(result), call_id, eid, market_ts)).lastrowid
                    create_outcomes(conn, aid, market_ts, float(coin["current_price"]), cfg["outcomes"]["tolerance_seconds"])
                    conn.execute("UPDATE ai_calls SET status='succeeded',completed_ts=? WHERE id=?", (completed_ts, call_id))
                    conn.execute("UPDATE candidate_evaluations SET decision='assessed' WHERE id=?", (eid,))
                if result["surge_score"] >= cfg["ai"]["alert_score"]:
                    deliver_alerts(conn, cfg, aid, coin, result, episode, summary)
            except Exception as exc:
                conn.rollback()
                summary["errors"] += 1
                with conn:
                    if call_id is not None:
                        conn.execute("UPDATE ai_calls SET status='failed',completed_ts=?,error=? WHERE id=?",
                                     (now_utc().isoformat(), type(exc).__name__, call_id))
                    conn.execute("UPDATE candidate_evaluations SET decision='failed',reason=? WHERE id=?",
                                 (type(exc).__name__, eid))
                log.error("Candidate %s failed: %s", coin["id"], type(exc).__name__)
        # Mirror the final V1.1 AI decision without changing its original record or dossier.
        conn.execute('''UPDATE experiment_evaluations SET
            decision=(SELECT decision FROM candidate_evaluations WHERE id=v11_evaluation_id),
            reason=(SELECT reason FROM candidate_evaluations WHERE id=v11_evaluation_id)
            WHERE scan_id=? AND signal_version='1.1' ''',(scan_id,))
        conn.commit()
        if detector in ('v12','all'):
            experiment.run_detector(conn,scan_id,markets,stats_by_coin,market_ts,cfg,summary,
                recent_news,investigate,validate_response,deliver_alerts,news_cache,social_provider,now_utc)
        summary['candidates_found'] = summary['v11_candidates'] + summary['v12_candidates']
    except Exception as exc:
        if conn:
            conn.rollback()
        summary["errors"] += 1
        log.error("Scan failed: %s", type(exc).__name__)
    finally:
        summary["duration_seconds"] = round(time.monotonic() - started, 3)
        if conn:
            try:
                if scan_id is not None:
                    conn.execute("UPDATE scan_runs SET completed_ts=?,status=?,summary_json=? WHERE id=?",
                        (now_utc().isoformat(), "errors" if summary["errors"] else "completed",
                         json.dumps(summary), scan_id))
                    conn.commit()
            finally:
                conn.close()
        log.info("Scan summary %s", json.dumps(summary))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument('--detector', choices=('v11','v12','all'))
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    for section, key in (("database", "path"), ("logging", "path")):
        if not Path(cfg[section][key]).is_absolute():
            cfg[section][key] = str(ROOT / cfg[section][key])
    setup_logging(cfg["logging"])
    with process_lock(cfg["database"]["path"]):
        # Only recover after acquiring the single-instance lock.
        conn = connect(cfg["database"]["path"])
        try:
            with conn:
                conn.execute("UPDATE ai_calls SET status='interrupted',error='Process interrupted' WHERE status='reserved'")
                conn.execute("UPDATE v12_ai_calls SET status='interrupted',error='Process interrupted' WHERE status='reserved'")
                conn.execute("UPDATE alert_events SET status='delivery_unknown' WHERE status='reserved'")
                conn.execute("UPDATE scan_runs SET status='interrupted' WHERE status='running'")
        finally:
            conn.close()
        if args.migrate_only:
            log.info("Database migration complete")
            return
        while True:
            try:
                summary = run_scan(cfg, args.detector)
                if args.once:
                    raise SystemExit(1 if summary["errors"] else 0)
                time.sleep(cfg["scan_interval_seconds"])
            except KeyboardInterrupt:
                break
            except Exception as exc:
                log.error("Operational failure: %s", type(exc).__name__)
                if args.once:
                    raise SystemExit(1)
                time.sleep(cfg["scan_interval_seconds"])


if __name__ == "__main__":
    main()
