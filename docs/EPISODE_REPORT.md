# Offline episode evidence report

From the checkout in PowerShell:

```powershell
& ./.venv/Scripts/python.exe -B -m crypto_radar.episode_report --db C:/snapshots/analysis.sqlite > episode-report.json
& ./.venv/Scripts/python.exe -B -m unittest discover -s tests -q
```

Supply an existing full schema-v2 SQLite snapshot or an extracted compact analysis
database, not a ZIP. The CLI opens it with `mode=ro`, enables `query_only`, and uses
one read transaction. It neither migrates nor writes the database and needs no
network, AI, credentials or scanner configuration. JSON goes to stdout; failures
go to stderr with exit status 2. No current timestamp enters the report, so the
same database yields the same output. Use a frozen snapshot for weekly review.

Original logical table/view names support the compact export's text dictionaries;
encoded `data_*` tables are not interpreted directly. Unsupported schemas fail
explicitly. Older aggregate-only history is not reconstructed into episodes.

For every signal version/group and 1/3/6/12/24-hour horizon, each metric first
averages its available evaluation values **within an episode**, then reports the
equal-weight mean and median of those episode means. A median is therefore not
the median of evaluation rows. `return_pct` is the signed unadjusted (absolute,
as opposed to benchmark-relative) percent return, not its mathematical magnitude.
BTC, ETH and broad excess returns use recorded values; missing benchmarks never
become zero. Each metric exposes measured/missing episode and evaluation counts,
plus episodes with only partial evaluation coverage. Measured means non-NULL;
late returns remain included and are explicitly counted. Available-case averages
can be biased by follow-up coverage. Counts of evaluation rows are diagnostics,
not sample sizes. All linked evaluations are included, even excluded/rejected
decisions; unlinked/orphan evaluations are counted and omitted from statistics.

Timing includes episodes and evaluations with each status, absent outcome rows,
and episode-weighted absolute timing error in seconds. Episode status counts can
overlap. Cohort dates include episode starts, last signals and group detections.
Chronology uses each episode's first recorded social/news/market/V1.1/V1.2 signal
once: positive minutes mean signal before significant move, negative means after.
Pairs missing either timestamp are excluded and counted. These summaries are
group-level, not horizon-specific; a missing move is not proof of no move.

Collector statuses, social observation rows, explicit observed zeros, feature
statuses and missing mention fields cover **all retained detail**, not just a
particular group. An unavailable collector with item_count=0 does not demonstrate
zero social activity. Real provider validity cannot be established from these
counts. Inspect warnings, coverage and timing before interpreting any means.

Synthetic example: one episode has evaluation returns 2% and 6%; another has
10%. The report's cohort mean and median are 7%, with two measured episodes,
not the evaluation-weighted 6%. If ETH benchmarks are absent, ETH excess has zero
measured episodes and a null mean, not 0%.

This adds analysis tooling without changing the evidence architecture or roadmap
status. Episodes can remain correlated across coins, groups and overlapping
horizons. No inferential, causal or predictive claims follow from this report.
The next gate remains validated coverage and a frozen forward cohort with
prespecified held-out controls: the protocol suggests an initial comparison
around four weeks and roughly 500 reasonably independent episodes, with 8–12
weeks before strong conclusions. Those guides do not automatically promote a
milestone. Tests validate mechanics using isolated synthetic data only.
