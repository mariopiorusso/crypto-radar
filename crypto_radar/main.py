import argparse, json, os, time, traceback
from pathlib import Path
import yaml
from dotenv import load_dotenv

from .collectors.market import fetch_markets
from .collectors.news import recent_news
from .intelligence.statistical import score_candidate
from .intelligence.ai import investigate
from .alerts.telegram import send_alert
from .db import connect, save_markets, history
from .outcomes import create_outcomes, fill_due_outcomes

def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_scan(cfg):
    conn = connect(cfg["database"]["path"])
    markets = fetch_markets(cfg["currency"], cfg["coin_count"])
    save_markets(conn, markets)
    by_id = {m["id"]: m for m in markets}
    fill_due_outcomes(conn, by_id)

    candidates = []
    for m in markets:
        hist = history(conn, m["id"])
        stats = score_candidate(m, hist[1:], cfg["filters"])  # exclude just-saved point
        if stats and stats["pre_score"] >= cfg["ai"]["min_pre_score"]:
            candidates.append((m, stats))

    candidates.sort(key=lambda x: x[1]["pre_score"], reverse=True)
    candidates = candidates[:cfg["filters"]["candidate_limit"]]

    print(f"Scanned {len(markets)} coins; {len(candidates)} AI candidate(s).")
    if not candidates:
        return

    if cfg["ai"]["enabled"] and not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY missing: printing statistical candidates only.")
        for m, s in candidates:
            print(m["symbol"].upper(), s)
        return

    for m, stats in candidates:
        try:
            news = recent_news(m["name"], m["symbol"], cfg["ai"]["max_news_items"])
            ai = investigate(m, stats, news) if cfg["ai"]["enabled"] else {
                "surge_score": int(stats["pre_score"]), "stage": "statistical_only",
                "catalyst_strength": 0, "manipulation_risk": 5,
                "confidence": 0.2, "thesis": "AI disabled", "risks": "Incomplete V1 evidence"
            }

            cur = conn.execute(
                """INSERT INTO assessments
                (ts, coin_id, symbol, pre_score, surge_score, stage,
                 catalyst_strength, manipulation_risk, confidence, thesis, risks, raw_json)
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (m["id"], m["symbol"], stats["pre_score"], ai["surge_score"], ai["stage"],
                 ai["catalyst_strength"], ai["manipulation_risk"], ai["confidence"],
                 ai["thesis"], ai["risks"], json.dumps(ai))
            )
            conn.commit()
            aid = cur.lastrowid
            ts = conn.execute("SELECT ts FROM assessments WHERE id=?", (aid,)).fetchone()["ts"] + "+00:00"
            create_outcomes(conn, aid, ts, float(m["current_price"]))

            print(f'{m["symbol"].upper():8} pre={stats["pre_score"]:5.1f} AI={ai["surge_score"]:3} stage={ai["stage"]}')
            if ai["surge_score"] >= cfg["ai"]["alert_score"]:
                text = (
                    f'🔥 CRYPTO RADAR — EXPERIMENTAL EARLY SIGNAL\n\n'
                    f'{m["name"]} ({m["symbol"].upper()}) — {ai["surge_score"]}/100\n'
                    f'Price: ${m["current_price"]}\n'
                    f'24h change: {stats["change_24h"]}%\n'
                    f'Volume vs local baseline: {stats["volume_vs_baseline"]}x\n'
                    f'Volume Z-score: {stats["volume_z"]}\n'
                    f'Stage: {ai["stage"]}\n'
                    f'Catalyst: {ai["catalyst_strength"]}/10\n'
                    f'Manipulation risk: {ai["manipulation_risk"]}/10\n'
                    f'Confidence: {ai["confidence"]}\n\n'
                    f'{ai["thesis"]}\n\nRisks: {ai["risks"]}\n\n'
                    f'Experimental research signal — not investment advice.'
                )
                send_alert(text)
        except Exception as e:
            print(f'Candidate {m["symbol"]} failed: {e}')
            traceback.print_exc()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    cfg = load_config()

    while True:
        try:
            run_scan(cfg)
        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc()

        if args.once:
            break
        time.sleep(int(cfg["scan_interval_seconds"]))

if __name__ == "__main__":
    main()
