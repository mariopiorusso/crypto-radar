# Compact analysis SQLite

This is a standalone research export, not a replacement scanner database. It does
not migrate, prune, vacuum, stop, or write to the production database. It does not
upload files, send alerts, change spending, or change the existing weekly task.
Deployment into that workflow requires owner review of this PR.

## Run

From the development checkout, with its Python environment:

```powershell
.\.venv\Scripts\python.exe -B -m crypto_radar.analysis_snapshot `
  --source C:\crypto-radar\data\crypto_radar.db `
  --history-days 84 --zip
```

The default source is `data/crypto_radar.db` under the checkout. The default output
is `analysis-snapshots/crypto-radar-analysis-<UTC timestamp>.db` next to the source.
`--output C:\somewhere\crypto-radar-analysis-<timestamp>.db` selects an explicit
destination. `--zip` also creates a ZIP containing that one analysis database.
The ZIP uses standard ZIP-LZMA compression, readable by Python zipfile and 7-Zip;
legacy ZIP readers may not support this method. The SQLite DB itself requires no
extensions. The command prints a JSON diagnostics report. It never overwrites an existing DB
or ZIP; an exclusive `.db.lock` prevents concurrent exports to the same name.
After a crashed process, inspect its outputs before removing its stale lock.

`--as-of 2026-09-28T00:00:00+00:00` fixes the cutoff reference for reproducible
selection. It is **not** a time-travel query: the backup contains the current
source state, including later observations and outcomes. Equal source contents,
history window and cutoff reference yield equal logical evidence; file bytes and
export timestamps are not promised identical.

The exporter uses the SQLite backup API through a `mode=ro` source connection,
then reads a frozen local copy. The scanner can continue writing. Large busy
databases can take several minutes. Allow temporary disk space for a full backup,
the encoded destination and its vacuum workspace, plus the final DB/ZIP.
Only the new destination is vacuumed. Backups and temporary dictionaries are
removed on normal completion/failure. No environment file or credentials are read.

## Evidence and retention policy

Schema version **2** and its explicit table set are supported. A new/unknown table
or migration version fails closed for policy review instead of silently omitting
data. IDs, NULLs, numerical values, original JSON spelling, and timestamps survive.

1. Seed rows at/after `as-of - history-days`, using the timestamp documented in
   `analysis_snapshot.TIMES`. Preserve schema migrations and current signal state.
2. Follow foreign-key parents and ownership children to a fixed point. This keeps
   complete episodes, their evaluations and outcomes, original candidate controls,
   scans, AI assessments/calls, linked news and social features, collector records,
   alert state and benchmark baskets/context. Include implicit legacy outcome and
   benchmark outcome relationships absent from the source foreign-key DDL.
3. Preserve **every asset's raw market path**, not just selected candidates, from
   the oldest retained anchor/context minus seven days onward. Preserve social
   observations over that same range. This permits exact timestamp-matched BTC,
   ETH and frozen broad-basket verification and chronology checks without changing
   missing endpoints into zeros or discarding the control universe.
4. Summarize omitted older rows in `snapshot_history` by day and available asset,
   detector/group, decision, collector/status and horizon dimensions. Older omitted
   market rows additionally have daily count, priced count, low/high/mean and time
   bounds in `market_daily_history`. `outcome_daily_history` adds omitted experimental
   return/benchmark means, measured denominators and episode counts by day, group,
   horizon and timing status. These are descriptive history, not substitutes
   for individual outcome distributions or within-day chronology.

Ownership closure can extend the detailed interval substantially, particularly
for long episodes and shared scans. This is intentional: size is secondary to
relational evidence. The metadata gives per-table actual source/export counts and
time ranges. All source history is retained in production regardless of export.

## Querying the artifact

Original table names are **read-only SQL views** over `data_*` tables. Repeated
text values are interned in `snapshot_text`. Identical measurement payloads for
benchmark context/outcomes and experimental outcomes are stored once, with every
original evaluation/horizon key preserved in mapping tables. Nothing is averaged,
sampled or rounded by this representation; NULLs and original JSON spelling stay
intact. No Python extension or custom SQLite function is needed. Use a SQLite tool
that lists views as well as tables.

Do not directly interpret integer IDs in encoded TEXT columns in `data_*`.
Query original names (`experiment_evaluations`, `experiment_outcomes`, etc.).
Select only necessary columns: reconstructing all large dossiers is slower.
Existing source views (`experiment_lead_times`, `experiment_ai_usage`) are retained.

```sql
SELECT * FROM analysis_groups;
SELECT * FROM analysis_outcomes;
SELECT * FROM analysis_collectors;
SELECT * FROM analysis_social;
SELECT * FROM analysis_news;
SELECT * FROM analysis_benchmarks;
SELECT * FROM analysis_candidates;
SELECT * FROM analysis_ai;
SELECT * FROM analysis_scans;
SELECT * FROM analysis_episodes WHERE evaluations > 1;
SELECT key, value_json FROM snapshot_metadata;
```

The `analysis_*` views supplement detailed rows; no averages are presented as
proof of predictive value. `analysis_outcomes` separates timing statuses and
horizons, includes observed/return denominators and maximum absolute timing error.
`analysis_episodes` exposes repeated observations within episode/coin/group;
evaluations are not independent experimental samples. Missing social observations,
unavailable features, and measured zero activity remain distinct.

## Validation and diagnostics

The generated database passes `quick_check`, `integrity_check`, physical text
foreign-key checks, and explicit checks of all logical source relationships,
including the two implicit ones. An orphan aborts publication. Original schema
SQL is in `snapshot_source_schema`; the original migration records are queryable.
Do not treat physical `foreign_key_check` alone as evidence of logical integrity.

Metadata includes UTC export time, cutoff reference, source path/version, revision
and dirty-worktree status if available, policies, counts/time ranges, source sizes,
allocated pages/free pages, and integrity results. Missing metadata is JSON null.
CLI output adds final DB/ZIP bytes and DB-to-ZIP compression ratio.

Storage diagnostics use `dbstat` when available. Otherwise the exporter counts
B-tree and overflow pages per table/index on the frozen file using SQLite's
documented file format. Per-table logical payload lengths are a separate measure,
not an estimate of allocated index space. A small ZIP cannot be guaranteed for
every data population or window; the report exposes actual sizes rather than
silently sampling evidence to meet a connector limit.

## Analytical limitations / next gate

- This preserves recorded evidence, not evidence the collectors never gathered.
  Unavailable social collection does not support social predictive claims.
- Existing market sampling gaps remain; endpoint returns do not prove every
  within-window threshold crossing. Broad-market context is the project's frozen
  equal-weight basket, not the entire crypto market.
- Older aggregate-only data supports descriptive history, not full individual
  return distributions, causal ordering, or independent held-out cohort recovery.
- Seven days of raw baseline context may not reproduce a customized longer
  baseline; recorded features and configurations remain available. Increase the
  history window for longer raw lookbacks or older cohort comparisons.
- The snapshot represents backup-time source state, not final outcomes for still
  pending horizons. Inspect completeness/timing before statistical comparisons.
- Neither export success nor offline test success establishes predictive value.
  The next gates remain source quality/coverage, a frozen forward cohort, and
  prespecified chronological held-out comparisons against control baselines.

Run offline tests with `python -B -m unittest discover -s tests -q`.

## Live-source validation, 28 September 2026

The read-only validation used the live source while preserving its operation.
The generated artifact passed quick_check, integrity_check, physical foreign-key
checks, explicit logical relationship checks, and ZIP CRC verification. All **99
application tests** passed (including 16 exporter tests), with no live network or
alert calls in tests. Production data was not committed or uploaded to GitHub.

- Frozen source: **1,533,186,048 bytes**.
- Analysis database: **613,142,528 bytes**.
- ZIP-LZMA: **70,587,782 bytes** (DB/ZIP 8.69:1; source/ZIP 21.72:1).
- Requested detailed window: 84 days. All existing source data fell within
  retained detail/closure, so **no source rows were omitted in this run**.
- Market observation range: 2026-09-15T20:12:24+00:00 through
  2026-09-28T19:43:50.560913+00:00. Future target horizons remain pending where
  unmeasured; source/export timestamp bounds for every table are in metadata.
- Source evidence included zero social observations and 167,000 unavailable
  social-feature records. This is not evidence for social predictive value.
- Validation occurred before the implementation commit; metadata correctly records
  the base revision e23d4a73e277ebdd106ad4d96dba84eeaa98661a and a dirty working tree.
  The validated exporter was subsequently committed as 695ee95a63612c8424531ce343ec0a0cadfeb260.

| Table | Source rows | Exported rows |
| --- | ---: | ---: |
| market_observations | 315,300 | 315,300 |
| assessments | 467 | 467 |
| outcomes | 2,335 | 2,335 |
| schema_migrations | 2 | 2 |
| scan_runs | 3,153 | 3,153 |
| candidate_evaluations | 315,200 | 315,200 |
| ai_calls | 506 | 506 |
| signal_state | 107 | 107 |
| alert_events | 0 | 0 |
| research_episodes | 127 | 127 |
| social_observations | 0 | 0 |
| social_features | 167,000 | 167,000 |
| news_evidence | 10,798 | 10,798 |
| collector_runs | 18,624 | 18,624 |
| experiment_evaluations | 334,700 | 334,700 |
| evaluation_news | 275,520 | 275,520 |
| benchmark_members | 149,253 | 149,253 |
| benchmark_context | 502,758 | 502,758 |
| experiment_outcomes | 1,256,895 | 1,256,895 |
| benchmark_outcomes | 2,349,786 | 2,349,786 |
| v12_ai_calls | 0 | 0 |

Largest allocated source storage consumers (not just logical payload):

| Table/index | Bytes |
| --- | ---: |
| experiment_evaluations | 329,854,976 |
| experiment_outcomes | 250,159,104 |
| benchmark_outcomes | 195,604,480 |
| candidate_evaluations | 122,048,512 |
| idx_experiment_outcome_observed | 102,342,656 |
| social_features | 85,721,088 |
| idx_experiment_outcome_pending | 82,341,888 |
| sqlite_autoindex_benchmark_outcomes_1 | 67,776,512 |
| ai_calls | 46,907,392 |
| market_observations | 38,830,080 |

The growth is dominated by repeated evaluation/outcome records and their indexes;
raw market observations occupy about 38.8 MB here. Export deduplication does not
fix or alter production retention. No free-page waste was observed in this source.
The 70.6 MB ZIP meets the under-100 MB target for this sample, but not the preferred
20–50 MB range. Future 12-week datasets can be larger; preserving scientific
evidence takes priority over a fixed attachment/connector size limit.
