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
