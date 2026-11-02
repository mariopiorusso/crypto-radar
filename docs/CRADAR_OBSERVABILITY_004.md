# Daily collection health and V1.1 timing instrumentation

The daily checker reuses the merged validation report's health calculation and
opens SQLite read-only in a short, consistent transaction. It does not run episode
analysis, fetch provider data, send alerts, or modify the database. Freshness is
measured against the actual check time, not the last successful scan, so a stopped
scanner cannot make old evidence look current.

```powershell
.\.venv\Scripts\python.exe -B -m crypto_radar.daily_health --db C:\crypto-radar\data\crypto_radar.db --output-dir C:\crypto-radar\data\provider-health
# Run once in an administrator PowerShell:
.\install_daily_health_task.ps1 -At 09:00
```

Each successful check writes a timestamped JSON archive and atomically replaces
`data/provider-health/latest.json`. Failed checks leave the previous report intact;
inspect its `checked_at` and the scheduled task result to detect a failed checker.
The report exposes scanner age, last successful collection, changed-response age,
resolved coin coverage, repeated/stale responses, failures and polling gaps.
Archives are retained locally; no automatic deletion or external notification is
configured. The task runs as Local Service with three retries, daily at 09:00 in
the Windows local timezone. On this installation, `GMT Standard Time` follows
Dublin's seasonal clock changes. Confirm the host timezone when installing elsewhere.

Operational review: inspect collection health daily and review the initial week
on **9 November 2026**. A week is a data-quality checkpoint, not statistical
readiness for a predictive claim. Verify genuine changed, resolved samples and
baseline/follow-up maturity before making performance comparisons. Four mapped
coins do not establish coverage of all 100 configured assets.

The separate V1.1 instrumentation changes are review-only until deployed:

- `candidate_evaluations.features_json.observation_timing` records market receipt
  and actual numeric candidate-computation completion timestamps. It is a copy of
  the scored features; timing metadata never goes into score calculation.
- The validation report joins the original V1.1 evaluation and shows
  `candidate_computed_ts` / `market_to_computed_seconds`. Older rows are NULL;
  historical timestamps are never synthesized or backfilled.
- Existing `ai_calls.completed_ts` continues to record post-response validation
  completion. Candidate eligibility, AI completion and alerting remain distinct.
- Structured `Observation timing` log events record each alert sender's return
  time and alert-event ID, including `delivery_unknown` failures and console output.
  A successful sender return is not proof of final delivery or recipient reading.
  No message text, recipients, credentials or exception messages enter these events.

No schema migration or additional table is introduced. Existing analysis exports
already preserve candidate feature JSON and AI timestamps. Sender-return timestamps
are operational log evidence only: current rotating-log retention applies and they
are not in database snapshots. A durable delivery ledger would require a separately
reviewed schema change. V1.2 candidate computation is not instrumented by this change.
There are no scoring, detector-threshold, paid-provider or trading changes.

Validation: 168 offline application tests passed, including daily archive/freshness
tests and timing metadata/privacy tests. Existing scanner tests exercise the
instrumented path, candidate decisions and independent alert-channel failures.
