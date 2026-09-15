import sqlite3
from pathlib import Path
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_observations (
  ts TEXT NOT NULL,
  coin_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  price REAL,
  market_cap REAL,
  total_volume REAL,
  change_24h REAL,
  PRIMARY KEY (ts, coin_id)
);
CREATE INDEX IF NOT EXISTS idx_market_coin_ts
ON market_observations(coin_id, ts);

CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  coin_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  pre_score REAL NOT NULL,
  surge_score INTEGER,
  stage TEXT,
  catalyst_strength INTEGER,
  manipulation_risk INTEGER,
  confidence REAL,
  thesis TEXT,
  risks TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
  assessment_id INTEGER NOT NULL,
  horizon_hours INTEGER NOT NULL,
  target_ts TEXT NOT NULL,
  observed_ts TEXT,
  start_price REAL NOT NULL,
  later_price REAL,
  return_pct REAL,
  PRIMARY KEY (assessment_id, horizon_hours)
);
"""

def connect(path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn

def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def save_markets(conn, rows):
    ts = utc_now_iso()
    conn.executemany(
        """INSERT OR REPLACE INTO market_observations
        (ts, coin_id, symbol, name, price, market_cap, total_volume, change_24h)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(ts, r["id"], r["symbol"], r["name"], r.get("current_price"),
          r.get("market_cap"), r.get("total_volume"),
          r.get("price_change_percentage_24h")) for r in rows]
    )
    conn.commit()
    return ts

def history(conn, coin_id, limit=288):
    return conn.execute(
        """SELECT * FROM market_observations
           WHERE coin_id=? ORDER BY ts DESC LIMIT ?""",
        (coin_id, limit)
    ).fetchall()
