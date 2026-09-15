from datetime import datetime, timezone, timedelta

HORIZONS = (1, 3, 6, 12, 24)

def create_outcomes(conn, assessment_id, ts, start_price):
    base = datetime.fromisoformat(ts)
    for h in HORIZONS:
        target = (base + timedelta(hours=h)).isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO outcomes
            (assessment_id, horizon_hours, target_ts, start_price)
            VALUES (?, ?, ?, ?)""",
            (assessment_id, h, target, start_price),
        )
    conn.commit()

def fill_due_outcomes(conn, markets_by_id):
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        """SELECT o.*, a.coin_id FROM outcomes o
           JOIN assessments a ON a.id=o.assessment_id
           WHERE o.later_price IS NULL"""
    ).fetchall()
    for r in rows:
        if datetime.fromisoformat(r["target_ts"]) <= now:
            m = markets_by_id.get(r["coin_id"])
            if not m or not m.get("current_price"):
                continue
            later = float(m["current_price"])
            start = float(r["start_price"])
            ret = (later / start - 1) * 100 if start else None
            conn.execute(
                """UPDATE outcomes SET observed_ts=?, later_price=?, return_pct=?
                   WHERE assessment_id=? AND horizon_hours=?""",
                (now.isoformat(), later, ret, r["assessment_id"], r["horizon_hours"])
            )
    conn.commit()
