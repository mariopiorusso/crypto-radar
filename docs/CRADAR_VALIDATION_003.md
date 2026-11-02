# CRADAR-VALIDATION-003: ApeWisdom and signal timing

## Decision

**B — correct and monitor data quality, then continue observation.** The evidence
does not justify a detector change. ApeWisdom has one genuine collection but no
resolved historical observations at the research cutoff. V1.1 usually crosses its
score threshold after the recorded move. Test a detector hypothesis separately,
with a frozen forward cohort and controls, after instrumentation is trustworthy.

## Scope and reproducibility

Analysis cutoff: **2 November 2026, 11:06:35.402851 UTC**. All dates below use UTC.
The read-only transaction saw schema v4, 577 retained episode IDs and 120 upward
significant-move episodes. No scanner, production scoring, alert thresholds, paid
provider, deployment or production database content was changed by this task.
The already-enabled scanner was left running; no extra provider request was made.

Repository inspection: main is `d00bbf6`; open [PR #6](https://github.com/mariopiorusso/crypto-radar/pull/6)
contains ApeWisdom/X on `f135c4a`. This change is stacked on that branch. Open
[PR #5](https://github.com/mariopiorusso/crypto-radar/pull/5) contains a separate
CryptoSocial foundation with conflicting schema-v4 work; it is not merged here.
StockGeist, the legacy injected interface and that separate branch are preserved.
The previous 100-asset mapping-limit correction is included with regression tests;
the live enabled `config.yaml` and unrelated local worker/Drive edits are excluded.

From this feature checkout, using the existing environment:

```powershell
C:/crypto-radar/.venv/Scripts/python.exe -B -m crypto_radar.validation_report --db C:/crypto-radar/data/crypto_radar.db --as-of 2026-11-02T11:06:35.402851+00:00 > validation.json
C:/crypto-radar/.venv/Scripts/python.exe -B -m crypto_radar.validation_report --db C:/crypto-radar/data/crypto_radar.db
C:/crypto-radar/.venv/Scripts/python.exe -B -m unittest discover -s tests -q
```

The second command is the collection-health check, using the latest completed
scan as cutoff. The report opens with `mode=ro`, sets `query_only`, and holds one
read transaction; no migrations, networking or credentials are required. Missing
relations fail explicitly. Existing full/compact analysis databases with the
original relation names are supported. Use a frozen snapshot for exact repeated
analysis: a cutoff on a changing database cannot restore pruned rows or reverse
retrospective episode membership. The committed
[evidence extract](examples/cradar-validation-003-evidence.json) preserves aggregate
results and representative episodes, with the full report's canonical SHA-256.

## Integration and collection health

Implemented: official `all-crypto` pagination, bounded retries, persistent scanner
cooldowns, explicit canonical-ID resolution, receipt timestamps, native metric
storage, repeated-response fingerprints, additive schema v4, as-of research
features, episode reports and full/compact exports. There is no five-minute
ApeWisdom normalization and no ApeWisdom input to production scoring.

Verified live, distinct from synthetic tests:

| Observation | Verified result at cutoff |
| --- | --- |
| Runtime | V1.2/social/ApeWisdom enabled; `all` scanner; 100 configured aliases |
| Poll configuration | 1,800 seconds, effective cadence bounded by scanner cycles |
| Successful collections | 1 complete response, 1 page, 5 snapshot rows |
| First and last receipt | 2026-11-02 10:46:10.818550 |
| Distinct resolved assets | **0** |
| Unresolved tickers | BTC.X, XRP.X, LINK.X, XMR.X, ID.X; one row each |
| Failures / rate limits | None recorded; four later scanner cooldown records |
| Duplicated primary keys / timestamp errors | 0 / 0 |
| Observed refresh cadence / polling gaps | Not estimable from a single response |
| Provider update timestamps | None; every `provider_ts` is NULL |
| Usable social research episodes | 0 |

The first collection preceded the correction from `BTC` to `BTC.X`. The current
configuration has the corrected 100 aliases, but **configuration is not measured
coverage**. Historical unresolved rows are not retroactively linked or backdated.
Four returned ticker/name pairs match our current scan universe; Everest (ID.X)
does not. Future observations must demonstrate actual resolution. Prospective
aliases for absent coins, especially renames and ticker reuse, still need review.

Official [API documentation](https://apewisdom.io/api/) describes filters and
pagination. [Methodology](https://apewisdom.io/methodology/) describes twice-hourly
scanning; it does not verify actual API response-refresh timing. Neither reviewed
page establishes a numerical request quota or continuous-storage permission.
`access_confirmed=true` is an operator assertion, not independent permission
evidence. This validation does not enable or expand continuous access. Obtain and
retain provider confirmation from the published contact, hello@8marketcap.com.

## Metric semantics and experimental baseline

All definitions are research-only. Never interpret rolling totals or deltas as
interval post counts. Negative deltas can reflect expiring mentions or corrections.
An absent asset/metric is NULL/unavailable, never measured zero. Explicit duplicate
symbols, including case-insensitive duplicates, are rejected; no fuzzy matching.

| Indicator | Definition and minimum history | Missing behavior |
| --- | --- | --- |
| Rolling mention delta | Current minus preceding accepted count; 2 samples, positive gap at most 3,600 s | NULL if no fresh pair |
| Relative rolling change | Delta / preceding rolling count; a fraction | NULL for preceding zero or absent count |
| Rank / upvote delta | Current minus preceding native value; rank decline means improved rank | NULL if either value missing |
| Attention level | `(count - prior mean) / prior population SD` over trailing 24 h | NULL before 12 prior samples or with zero SD |
| Rate excess | Current rolling-count delta/hour minus mean historical adjacent delta/hour | NULL without 12 prior samples and 11 valid historical intervals |
| Elevated persistence | Consecutive accepted counts above their respective prior means; gap breaks streak | NULL before baseline maturity; zero means measured non-elevation |
| Freshness / coverage | Receipt age and complete, changed-response availability per configured asset | Repeated/partial/stale snapshots are unavailable evidence |

Accept at most one complete changed response per elapsed polling interval (default
30 minutes), using only samples received by each cutoff. Repeated responses add
no baseline samples or artificial spikes. Historical baseline excludes the current
sample and all later receipts. Freshness defaults to 3,600 seconds, independent of
the upstream rolling window. Counts are serially correlated rolling observations:
12 samples permit descriptive features, **not statistical significance**. Rate
excess is change in rolling-count slope, not acceleration in independent post arrivals.
The report records all accepted samples for inspection. Prior-day API fields are
retained but never used to fabricate observations at prior timestamps.

## V1.1 timing: reproduced and clarified

The existing event definition is `fixed-anchor-1.2`: the first sampled price at
least 5% above a fixed anchor, within 24 hours of that anchor. The anchor normally
looks back one hour with a tolerance/fallback. This is an upward sampled crossing,
not the continuous-price onset or a symmetric absolute move. All **120 recorded
crossings reproduce exactly** from retained market prices and episode parameters.

| Population | Move episodes | With V1.1 candidate | Strictly early | Simultaneous | Late |
| --- | ---: | ---: | ---: | ---: | ---: |
| Crossings before 30 October | 117 | 73 | 16 | 2 | 55 |
| Current retained database | 120 | 73 | 16 | 2 | 55 |

Strictly early median lead is **159.93 minutes**. Combining early and simultaneous
cases gives **18** and median **120.32 minutes**, reproducing both supplied October
29 figures if their definition included equality. The original report/query was
not supplied, so that equality convention is an explanation supported by the data,
not proof of the original implementation. Candidate means numeric eligibility,
including candidates later suppressed by AI budget/cooldown; it is not alert delivery.

Concrete evidence about the 55 late episodes:

- **49** have a last pre-move V1.1 evaluation recorded as `below_threshold`.
  Median shortfall is **18 score points** below 45 (range 3–43.5). Their median
  history count is 287; only one has fewer than six points. History warmup is
  therefore not the general explanation.
- Those 49 have median preceding scanner/market interval **307.19 seconds**,
  source age **153.84 seconds**, and scan duration **7.00 seconds**. V1.1 detection
  timestamps equal market receipt timestamps by design; zero recorded delay does
  not prove instantaneous computation. Seconds/minutes of sampling delay cannot
  alone explain many hours of score-threshold delay.
- **41/49** have no volume-ratio score contribution at the last pre-move sample;
  **13/49** have zero early-move score contribution. The implementation uses
  rolling 24-hour volume and 24-hour price change, so a fresh short move can be
  diluted by its volume baseline or already penalized by the price-change term.
  These are supported design explanations, not a causal threshold experiment.
- **Six** have no pre-move evaluation within the linked episode. Three crossings
  precede episode start and three coincide with it. These are left-truncated
  episode/event comparisons; do not classify them as purely scanner latency.
- News is not an input to V1.1 numeric candidate eligibility. Delayed news and
  AI limits affect downstream investigation/alerts, not the first candidate flag.
  Report receipt time separately from publication time; publication never backdates
  information availability.

Representative episode IDs (full timestamps/components are in the evidence extract):

| Episode | Evidence |
| --- | --- |
| 1, Bitcoin Cash | Candidate precedes crossing by 558.33 min; later suppressed by daily AI budget. Candidate and alert are distinct. |
| 5, Sky | Last pre-move score 22.5/45 despite volume z=2.18 and 287 historical points; candidate arrives 638.30 min after crossing. |
| 7, Uniswap | Candidate and crossing share the exact receipt timestamp; zero lead, not early. |
| 20, Ether.fi | Pre-move score 39.5/45; the first linked candidate is days later, after a 75,677.58-second market gap. Long episodes/outages distort naive lag summaries. |
| 85, Akedo | Crossing is before episode start; no linked pre-move evaluation exists. |

Episode IDs are the primary sampling unit, not guaranteed independent events.
Long episodes, same-asset overlapping horizons and cross-asset market moves remain
correlated. The report exposes same-asset overlapping 24-hour anchor pairs.

## Comparisons and forward returns

The report fixes one anchor: the **earliest retained V1.1 evaluation per episode**.
It omits invalid/excluded first evaluations rather than choosing favorable later
ones. All 577 current first evaluations qualify. At that evaluation's genuine
cutoff, classify news received within 24 hours, market volume z above the frozen
scan threshold, and positive fresh rolling mention delta. The eight combinations
are mutually exclusive; no episode is counted in multiple groups. Missing social
coverage is counted separately and never asserted to be no social activity.

Current groups: **204 news-only**, **33 market-only**, **340 news+market**, and
zero in the other five groups. There is no no-observed-upstream control and no
usable ApeWisdom group under this anchor rule. Exact asset/day matching has **zero
mixed-group strata**. These selection-dependent aggregates cannot isolate signal
strength from asset selection or regime. Broad relative returns are a market
benchmark, not a substitute for a matched experimental control.

The JSON reports 1/3/6/12/24-hour mean, median, positive frequency, per-metric
measured/missing episode counts and signal lead time. Only recorded within-tolerance
outcomes received by the report cutoff are included. Recomputed absolute returns
must agree with start/later prices; horizon targets and tolerance are checked.
BTC/ETH/broad-relative values reuse the established aligned-window measurements;
missing benchmarks remain NULL. There are no inferred or interpolated endpoints.

Illustrative unadjusted +1-hour absolute means: news-only **0.207%** (200/204
measured), market-only **0.079%** (32/33), news+market **0.034%** (339/340).
These are descriptive available-case results, **not evidence that one signal
outperforms another**. Full five-horizon statistics are in the evidence JSON.
ApeWisdom performance estimates are suppressed below 20 signal-positive episodes;
that guard is a display rule, not a power calculation or readiness criterion.
No confidence intervals are reported: no validated independent-block model or
matched common support exists. Prospective asset/time-block evaluation and a
prespecified analysis plan are required before inferential claims.

## Recommendations

| Priority | Action | Potential benefit | Effort | Risk |
| --- | --- | --- | --- | --- |
| 1 | Verify subsequent `.X` mappings and genuine per-coin coverage; review renamed/reused symbols; retain permission evidence | Restores usable social evidence | Low | Low; do not backfill first-observed times |
| 2 | Run this health report at each research review; investigate gaps and stale/repeated responses | Detects silent data failures | Low | Low; reporting only |
| 3 | Add separate market-received/candidate-computed/AI-completed/alert-delivered instrumentation in a follow-up | Separates implementation from design latency | Medium | Low, with timestamp tests |
| 4 | Prespecify forward cohorts, no-signal sampling and asset/regime/time-matched controls | Makes comparisons interpretable | Medium | Research selection risk |
| 5 | Propose an independent short-window-volume/threshold experiment, after baseline validation | May improve early detection | Medium/high | High overfitting risk; separate review required |

No detector improvement is implemented. Existing production thresholds are unchanged.

## Validation and unresolved requirements

**163 offline application tests passed**, including 25 attention tests and 12 new
validation-report tests. Fixtures cover rolling declines, zero denominators,
partial/repeated/stale snapshots, insufficient and sampled baselines, ambiguity,
future receipts/news/outcomes, episode anchoring, signed/equal lead time,
recomputed forward returns, missing benchmarks, health gaps/failures/rate limits,
source immutability, deterministic output and missing-file errors. Synthetic
collector-to-report tests establish mechanics, not live coverage or predictive value.

Open limitations: provider permitted usage/rate limits; prospective ticker-name
verification; real resolved history and refresh cadence; independent social
episodes with mature follow-up; explicit market-regime controls and a meaningful
uncertainty model; original October 29 report; separate CryptoSocial schema-v4
reconciliation before combining PRs. AC-01/02 and AC-07 explicitly record live
pipeline limitations rather than claiming end-to-end research success. AC-08
finds no matched support and therefore refuses an adjusted signal-strength claim.
