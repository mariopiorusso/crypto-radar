from datetime import datetime, timezone, timedelta

HORIZONS = (1, 3, 6, 12, 24)


def create_outcomes(conn, assessment_id, ts, start_price, tolerance=600):
    base = datetime.fromisoformat(ts)
    if start_price <= 0:
        raise ValueError("Outcome start price must be positive")
    for h in HORIZONS:
        conn.execute("""INSERT OR IGNORE INTO outcomes
            (assessment_id,horizon_hours,target_ts,start_price,anchor_ts,timing_status,
             measurement_version,tolerance_seconds) VALUES (?,?,?,?,?,'pending','1.1',?)""",
            (assessment_id, h, (base + timedelta(hours=h)).isoformat(), start_price, ts, tolerance))
    # Caller commits assessment and outcomes together.


def fill_due_outcomes(conn, now=None, tolerance=600):
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("""SELECT o.*,a.coin_id FROM outcomes o
        JOIN assessments a ON a.id=o.assessment_id WHERE o.later_price IS NULL""").fetchall()
    for row in rows:
        target = datetime.fromisoformat(row["target_ts"])
        if target > now:
            continue
        allowed = row["tolerance_seconds"] if row["tolerance_seconds"] is not None else tolerance
        point = conn.execute("""SELECT ts,price FROM market_observations WHERE coin_id=?
            AND julianday(ts)>=julianday(?) AND julianday(ts)<=julianday(?) AND price>0
            ORDER BY julianday(ts) ASC LIMIT 1""", (row["coin_id"], row["target_ts"], now.isoformat())).fetchone()
        if not point:
            if (now - target).total_seconds() > allowed:
                conn.execute("UPDATE outcomes SET timing_status='missing' WHERE assessment_id=? AND horizon_hours=?",
                             (row["assessment_id"], row["horizon_hours"]))
            continue
        error = (datetime.fromisoformat(point["ts"]) - target).total_seconds()
        status = "within_tolerance" if error <= allowed else "late"
        ret = (point["price"] / row["start_price"] - 1) * 100 if row["start_price"] > 0 else None
        conn.execute("""UPDATE outcomes SET observed_ts=?,later_price=?,return_pct=?,
            timing_error_seconds=?,timing_status=?,tolerance_seconds=?
            WHERE assessment_id=? AND horizon_hours=?""", (point["ts"], point["price"], ret,
            error, status, allowed, row["assessment_id"], row["horizon_hours"]))
    conn.commit()
