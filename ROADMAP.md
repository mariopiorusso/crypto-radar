# Crypto Radar: evidence-gated roadmap

This is the canonical roadmap. Status is based on repository source inspected at
`26f856ab9e66980617dfb82f963193161a80958c`, not on a production audit.
CURRENT means implemented control functionality; EXPERIMENTAL means research
infrastructure without established predictive value; PLANNED and FUTURE are not
implemented commitments. Code existing does not establish scientific success.
No production database, observations, credentials or runtime state were inspected.

## Thesis and scientific test

Seek early warning of surges through the hypothesized sequence:

**News/Social -> FOMO/attention acceleration -> Exchange activity -> Whale
confirmation -> Price surge.**

This ordering is a hypothesis to test, not an assumed causal chain. Focus on
acceleration relative to each coin's own baseline and signals received before
material price movement. Preserve event, receipt, detection and price timestamps;
late-arriving evidence cannot retrospectively become an early warning.

- **H0:** social/news acceleration adds no predictive information beyond
  market/volume anomalies.
- **H1:** it materially improves prediction, including useful lead time and
  performance relative to market-only baselines.

Compare market-only, social-only, news-only, social+news, market+social+news and
no-signal/matched controls. Start with simple deterministic baselines, including
unconditional event rates and market anomalies. Normalize returns against BTC,
ETH and a documented broad-market proxy; stratify by market regime and market cap.
Use episode-level evidence, account for repeated assets and overlapping horizons,
and use chronological held-out evaluation to avoid tuning on the test cohort.
Predefine target events, minimum useful improvement, acceptable false-positive
rates, timing tolerances and uncertainty criteria before examining results.

## V1.1 — CURRENT control

The repository supports a market anomaly -> news -> AI interpretation pipeline:

- CoinGecko observations, liquidity/price filters and per-coin rolling baselines;
  the volume signal uses rolling 24h totals, not exchange interval volume.
- Maintained stablecoin-ID filtering, candidate cooldown, material-score overrides,
  persistent per-channel alert deduplication and independent email/Telegram alerts.
- AI attempt budgets, strict response validation and preserved requests, evidence,
  configuration/version metadata, raw responses and available usage.
- Outcomes at **+1h/+3h/+6h/+12h/+24h**, with observed price anchors, timing
  tolerance and missing/late status. Late measurements are not exact-time returns.
- Versioned SQLite migrations with backups, scan summaries, rotating logging and
  a database-scoped process lock.

Evidence: [scanner](crypto_radar/main.py),
[market scoring](crypto_radar/intelligence/statistical.py),
[policies](crypto_radar/policies.py), [outcomes](crypto_radar/outcomes.py),
[migrations](crypto_radar/migrations.py), [operations guide](README.md) and
[offline tests](tests/test_v11.py). These establish code paths and validation
coverage, not live deployment health or a profitable predictive edge. The AI
Surge Score is not a calibrated probability. The `market_only` research label
describes candidate selection; the V1.1 downstream dossier still includes news.

## V1.2 — EXPERIMENTAL additive early warning

Research code exists for social/FOMO features, broader rotating news collection,
headline catalyst classification, novelty/deduplication, research episodes,
chronology, experimental groups and market-relative forward outcomes.
BTC/ETH comparisons use matching observation timestamps; the broad proxy is a
frozen equal-weight basket of other tracked non-stablecoins, not the entire market.
Missing endpoints remain missing. Benchmark measurements support regime analysis;
they do not establish a validated regime-adjusted predictive model.

**There is no production social provider in this repository.**
[The collector factory](crypto_radar/collectors/social.py) always returns an
unavailable collector; deterministic fixtures are injection-only test inputs.
Real production social observations are unavailable in this engineering checkout
and cannot be inferred from tests or empty collector results. Missing is not zero.
Social-vs-market ordering and the social hypothesis remain unvalidated.

The V1.1 detector remains the default; V1.2 is opt-in, with experimental AI and
alerts disabled by default. Its groups are descriptive strata, not randomized
arms or a complete suite of independent ablation detectors. A full controlled
comparison remains research work. Headline labels are hypotheses, not verified
catalysts; distinct publishers do not establish independent confirmation.

Evidence: [experiment](crypto_radar/experiment.py),
[social features](crypto_radar/intelligence/social.py),
[catalyst features](crypto_radar/intelligence/catalysts.py),
[measurements](crypto_radar/experiment_measurement.py),
[V1.2 tests](tests/test_v12.py) and [experiment protocol](docs/V1.2.md).

**Likely next research milestone:** select, integrate and validate a compliant
real social/FOMO provider. Review licensing, costs, rate limits, asset identity
mapping, historical coverage and provider bias first. Validate completed 5-minute
interval buckets, receipt latency, missingness, duplicate/concentration metrics,
source continuity and baseline coverage. Then freeze a forward cohort and measure
incremental predictive information. Provider integration is not part of this
documentation task; enabling a flag alone cannot supply social observations.

## V1.3 — PLANNED exchange microstructure

Add exchange-native **1m/5m/15m volume acceleration**, trade-count acceleration,
buy/sell imbalance, order-book imbalance, spread changes and volatility/price
acceleration. These collectors/features are not implemented in the inspected
source. Gate research on venue coverage, synchronized timestamps, interval
semantics, missing-data handling and resilience to spoofing or venue artifacts.
Advance only if held-out episode comparisons show incremental value beyond
V1.1/V1.2, after accounting for market regime and whether price already moved.

## V1.4 — PLANNED smart money and whales

Evaluate Nansen, Arkham and alternatives for accumulation, exchange inflows and
outflows, large transfers, dormant-wallet activity and concentration changes.
No whale collector or confirmation signal is implemented here. Require licensed
access, chain/asset coverage, defensible wallet labels and event/receipt timestamps.
Separate internal transfers and uncertain entity attribution from accumulation;
advance only on incremental, timely out-of-sample evidence.

## V2 — PLANNED calibrated prediction

Estimate calibrated probabilities such as **P(+5% within 6h)** and
**P(+10% within 24h)** from validated data and models. Define whether a target is
an any-time threshold crossing or an endpoint return; existing endpoint outcomes
cannot alone establish every within-window crossing. AI interprets evidence
rather than inventing probabilities.

Evaluate precision, recall, false-positive rate, calibration/reliability, returns,
adverse moves, market-cap strata, signal combinations and market-relative
performance. Report denominators, missingness and uncertainty. Additional path
measurements and analysis are needed for metrics not supported by current sampled
outcomes. Gate release on held-out calibration and improvement over simple
baselines across regimes, with monitoring for drift and abstention when uncertain.

## FUTURE trading progression — no trading implementation in this task

- **V3: paper trading.** Only after predictive evidence warrants it, simulate
  realistic fees, spreads, slippage, latency, liquidity, execution failures and
  deterministic portfolio/risk limits. Require reproducible net performance and
  adverse-move/drawdown analysis before considering live capital.
- **V4: small-capital autonomous trading.** Requires sufficient evidence and
  explicit owner authorization. Require deterministic position/exposure/loss
  limits, no leverage, a tested kill switch and no withdrawal permission.
  Passing research gates never grants trading authority.
- **V5: full learning loop.** Sense -> Predict -> Decide -> Risk controls ->
  Execute -> Monitor -> Exit -> Learn. This is a future design, not an existing
  autonomous trading system. Learning cannot bypass deterministic safety limits
  or authorization gates.

## Evidence gates and decisions

1. **Engineering/data gate:** offline tests, valid schemas, preserved evidence,
   timestamp integrity, adequate coverage, bounded cost and reliable outcomes.
   Fixtures validate mechanics, not provider quality or predictive power.
2. **Research gate:** freeze source mappings, universe, features, thresholds,
   horizons, episode definitions and model/prompt versions. Collect independent
   episodes and matched controls; record material changes as new cohorts.
3. **Decision gate:** compare prespecified baselines on held-out data with
   uncertainty and regime breakdowns. Advance, revise, reject, or **WAIT / collect
   more data**. Insufficient coverage or power is not evidence of success.

The [V1.2 protocol](docs/V1.2.md) proposes first-day operational checks, a first
week of data-quality validation, frozen weeks 2–4, an initial comparison around
four weeks and preferably approximately 500 reasonably independent episodes, and
8–12 weeks before strong conclusions. These are planning guides, not automatic
promotion thresholds; correlated assessments cannot substitute for independent
episodes. Preserve historical evidence rather than deleting inconvenient results.

## Autonomous engineering and safety

The intended engineering loop is:

**Roadmap -> evidence -> analysis -> next objective -> coding agent -> test ->
commit/push -> new evidence -> reassess.**

The owner-described current orchestration is **ChatGPT -> Gmail -> deterministic
Windows worker -> Codex -> GitHub/Drive/evidence -> completion email -> ChatGPT
assessment**. That deployment context is supplied by the task; this checkout does
not contain the worker implementation or prove its scheduler provenance. Existing
reporting code is not proof that any particular delivery succeeded. Final worker
exit codes, completion timestamps and scheduler provenance are supervisor-owned
checks. Snapshot uploads use the separate fixed snapshot action.

Process one trusted-protocol task at a time. Inspect changes, preserve unrelated
work, run relevant offline tests for code changes and provide structured reports
with actual results and limitations. Develop on `worker/*`, stage only intended
safe files, and push only when authorized; deployment is a separate owner action.
Do not expose or change secrets, credentials, worker policy or permissions. Do not
send live test alerts, spend on live AI tests, delete historical data or change
live-capital, risk or withdrawal permissions without explicit authorization.
Back up existing data before an authorized schema migration. No roadmap milestone
itself authorizes deployment, trading or changes to the live scanner.
