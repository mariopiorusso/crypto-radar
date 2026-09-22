"""Additive migrations. Never use executescript inside migration transactions."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = 2
STATEMENTS = [
    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_ts TEXT NOT NULL)",
    "ALTER TABLE market_observations ADD COLUMN source_updated_ts TEXT",
    "CREATE TABLE scan_runs (id INTEGER PRIMARY KEY, started_ts TEXT NOT NULL, completed_ts TEXT, status TEXT NOT NULL, config_json TEXT NOT NULL, config_hash TEXT NOT NULL, app_version TEXT NOT NULL, scoring_version TEXT NOT NULL, summary_json TEXT)",
    "CREATE TABLE candidate_evaluations (id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL REFERENCES scan_runs(id), coin_id TEXT NOT NULL, market_ts TEXT NOT NULL, pre_score REAL, features_json TEXT, decision TEXT NOT NULL, reason TEXT, UNIQUE(scan_id, coin_id))",
    "CREATE TABLE ai_calls (id INTEGER PRIMARY KEY, evaluation_id INTEGER NOT NULL UNIQUE REFERENCES candidate_evaluations(id), coin_id TEXT NOT NULL, budget_date TEXT NOT NULL, reserved_ts TEXT NOT NULL, completed_ts TEXT, status TEXT NOT NULL, pre_score REAL NOT NULL, requested_model TEXT NOT NULL, returned_model TEXT, request_json TEXT NOT NULL, response_json TEXT, usage_json TEXT, error TEXT)",
    "ALTER TABLE assessments ADD COLUMN ai_call_id INTEGER REFERENCES ai_calls(id)",
    "ALTER TABLE assessments ADD COLUMN evaluation_id INTEGER REFERENCES candidate_evaluations(id)",
    "ALTER TABLE assessments ADD COLUMN market_observed_ts TEXT",
    "ALTER TABLE assessments ADD COLUMN assessment_kind TEXT NOT NULL DEFAULT 'legacy'",
    "ALTER TABLE outcomes ADD COLUMN anchor_ts TEXT",
    "ALTER TABLE outcomes ADD COLUMN timing_error_seconds REAL",
    "ALTER TABLE outcomes ADD COLUMN timing_status TEXT NOT NULL DEFAULT 'legacy_unknown'",
    "ALTER TABLE outcomes ADD COLUMN measurement_version TEXT NOT NULL DEFAULT 'legacy'",
    "ALTER TABLE outcomes ADD COLUMN tolerance_seconds INTEGER",
    "CREATE TABLE signal_state (coin_id TEXT PRIMARY KEY, episode INTEGER NOT NULL DEFAULT 1, below_seconds REAL NOT NULL DEFAULT 0, last_ts TEXT NOT NULL, was_below INTEGER NOT NULL)",
    "CREATE TABLE alert_events (id INTEGER PRIMARY KEY, assessment_id INTEGER NOT NULL REFERENCES assessments(id), coin_id TEXT NOT NULL, episode INTEGER NOT NULL, channel TEXT NOT NULL, score INTEGER NOT NULL, fingerprint TEXT NOT NULL, attempted_ts TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT, error TEXT, UNIQUE(assessment_id, channel))",
    "CREATE INDEX idx_calls_coin_ts ON ai_calls(coin_id, reserved_ts)",
    "CREATE INDEX idx_calls_budget ON ai_calls(budget_date)",
    "CREATE INDEX idx_alert_coin_episode ON alert_events(coin_id, episode, channel)",
    "CREATE INDEX idx_outcomes_pending ON outcomes(timing_status, target_ts)",
    "CREATE INDEX idx_evaluations_coin ON candidate_evaluations(coin_id, market_ts)",
]


def migrate(conn, path):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > VERSION:
        raise RuntimeError("Database schema is newer than this application")
    if version == VERSION:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = Path(path).with_name(Path(path).name + f".before-schema-{VERSION}-{stamp}.bak")
    with closing(sqlite3.connect(backup_path)) as backup:
        conn.backup(backup)
    conn.execute("BEGIN IMMEDIATE")
    try:
        from .experiment_schema import STATEMENTS as V12_STATEMENTS
        for target, statements in ((1, STATEMENTS), (2, V12_STATEMENTS)):
            if version < target:
                for statement in statements:
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                             (target, datetime.now(timezone.utc).isoformat()))
        conn.execute(f"PRAGMA user_version={VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return str(backup_path)
