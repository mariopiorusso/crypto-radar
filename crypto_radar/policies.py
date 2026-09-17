import hashlib
from datetime import datetime


def cooldown_allowed(conn, coin_id, score, now, cfg):
    last = conn.execute("SELECT reserved_ts, pre_score FROM ai_calls WHERE coin_id=? ORDER BY id DESC LIMIT 1", (coin_id,)).fetchone()
    if not last:
        return True
    elapsed = (now - datetime.fromisoformat(last["reserved_ts"])).total_seconds()
    return elapsed >= cfg["cooldown_minutes"] * 60 or score >= last["pre_score"] + cfg["material_score_increase"]


def reserve_call(conn, evaluation_id, coin_id, score, now, cfg, model, dossier):
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not cooldown_allowed(conn, coin_id, score, now, cfg):
            conn.rollback()
            return None, "cooldown"
        count = conn.execute("SELECT COUNT(*) FROM ai_calls WHERE budget_date=?", (now.date().isoformat(),)).fetchone()[0]
        if count >= cfg["daily_call_budget"]:
            conn.rollback()
            return None, "daily_budget"
        cur = conn.execute("""INSERT INTO ai_calls
            (evaluation_id, coin_id, budget_date, reserved_ts, status, pre_score, requested_model, request_json)
            VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)""",
            (evaluation_id, coin_id, now.date().isoformat(), now.isoformat(), score, model, dossier))
        conn.commit()
        return cur.lastrowid, None
    except BaseException:
        conn.rollback()
        raise


def update_episode(conn, coin_id, eligible, now, cfg):
    row = conn.execute("SELECT * FROM signal_state WHERE coin_id=?", (coin_id,)).fetchone()
    episode, below = (row["episode"], row["below_seconds"]) if row else (1, 0)
    if row and row["was_below"]:
        gap = (now - datetime.fromisoformat(row["last_ts"])).total_seconds()
        if 0 <= gap <= cfg["scan_interval_seconds"] * 2:
            below += gap
        else:
            below = 0
    if below >= cfg["alerts"]["episode_reset_hours"] * 3600 and row and row["was_below"]:
        episode += 1
        below = 0
    if eligible:
        below = 0
    conn.execute("""INSERT INTO signal_state VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(coin_id) DO UPDATE SET episode=excluded.episode,
        below_seconds=excluded.below_seconds, last_ts=excluded.last_ts, was_below=excluded.was_below""",
        (coin_id, episode, below, now.isoformat(), int(not eligible)))
    return episode


def reserve_alert(conn, assessment_id, coin_id, episode, channel, score, text, now, increase):
    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute("""SELECT MAX(score) FROM alert_events WHERE coin_id=? AND episode=?
            AND channel=? AND status IN ('reserved','sent','delivery_unknown','console')""",
            (coin_id, episode, channel)).fetchone()[0]
        if last is not None and score < last + increase:
            conn.rollback()
            return None
        cur = conn.execute("""INSERT OR IGNORE INTO alert_events
            (assessment_id,coin_id,episode,channel,score,fingerprint,attempted_ts,status)
            VALUES (?,?,?,?,?,?,?,'reserved')""", (assessment_id, coin_id, episode, channel,
            score, hashlib.sha256(text.encode()).hexdigest(), now.isoformat()))
        conn.commit()
        return cur.lastrowid if cur.rowcount else None
    except BaseException:
        conn.rollback()
        raise
