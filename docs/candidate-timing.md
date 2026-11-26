# Candidate timing: historical diagnosis and shadow proposal

The 23 November 2026 snapshot reproduces the review's timing counts. The latest saved evaluations before the price crossing were below the recorded threshold in all 108 episodes with an early market anomaly and a late or unrecorded candidate. This is an association, not proof that a lower threshold would improve trading results.

## Cohort and timing

The [snapshot manifest](https://drive.google.com/file/d/1iFe5mlm0QbEVcey7q5TYkzQ9aPJvzQqs/view) identifies the source, cutoff 2026-11-23T19:54:18.610593Z and reconstructed ZIP SHA-256 `7bb8ca6c95ec1a70607f3c5c18119055980ed068b2f48bc383f0c3d400ad8105`. Count each of 195 recorded episode IDs with a significant move once, not evaluation rows. Compare the first signal to the sampled significant-move crossing, strictly before, equal or after. This is the crossing definition used in the earlier timing counts, not the earlier fixed anchor; both are in the [episode evidence](candidate-timing.csv).

| Signal | Present | Early | Simultaneous | Late |
|---|---:|---:|---:|---:|
| News | 152 | 89 | 0 | 63 |
| Market anomaly | 183 | 139 | 9 | 35 |
| V1.1 candidate | 119 | 26 | 5 | 88 |

These episode units are not guaranteed independent: overlapping windows and repeated assets can correlate. Retrospective selection of episodes with observed crossings and retained signals introduces selection and survivorship bias. An unrecorded candidate does not prove no candidate existed outside retained evidence or after cutoff.

## Feature and timing evidence

Of 139 early-market episodes, 108 across 48 assets had a late candidate (56) or no recorded candidate (52). For each, the CSV records the latest V1.1 evaluation whose detection, evidence cutoff, market timestamp, scan start and scan completion are all strictly before the crossing. All 108 exist, were rejected as `below_threshold`, and have scores 1.5–42 (median 27.3) versus recorded threshold 45. The saved 24-hour volume/baseline ratio ranges 0.91–1.68 (median 1.19); the volume-ratio component is zero in 73. History count ranges 29–287 (median 287). These components cannot establish that changing one would create an eligible signal.

The latest completed evaluation precedes the sampled crossing by 5.002–5.012 minutes; its market timestamp precedes the crossing by 5.077–5.803 minutes. All recorded configurations specify a 300-second scan interval. This is consistent with roughly one evaluation interval, but is not a measurement of source age, outage duration, processing latency, or when a candidate became computable. The crossing is sampled and tied to retained observations. Signal fields and episode membership are retrospective, and scan completion is a conservative availability boundary; provider timestamps and historical state reconstruction are not established. These limits prevent separating cadence, source freshness, threshold choice and feature availability into causal fractions. Missing evidence is not zero activity.

## Prespecified offline shadow experiment

Freeze a new prospective chronological cohort and data schema before observing outcomes. Record event time, receipt time and scan completion for each source; retain gaps, asset universe membership and delistings. Compute short-interval returns and volume changes only from data received by each decision time. If a licensed source supplies it, separately test signed trade flow or order-book imbalance with sequence-gap and resynchronization flags; missing or ambiguous aggressor side stays missing. Do not infer order flow from 24-hour volume.

Prespecify a small baseline and shadow rule family, compute both at identical scheduled decision times, and match controls on asset, time, liquidity and prior volatility using only earlier data. Exclude overlapping windows or cluster by asset and time, and report dependence. Split chronologically into development, validation, and untouched final holdout with an embargo at least as long as the outcome window; tune only on development, freeze choices before validation and holdout, and report every trial. Re-run without unavailable feeds to measure coverage selection. If history or identities are insufficient, report the test as infeasible rather than filling missing values.

Evaluate lead time to a prespecified move, event recall, precision and false alerts per asset-day, including all eligible non-move controls. Report coverage, missingness and uncertainty by time and liquidity. Simulate executable entries only after receipt and computation, including spread, fees, slippage, latency, and capacity sensitivity; compare against the unchanged baseline and a no-trade reference. This is a research design, not a production threshold, alert, trade, or deployment recommendation.

## Reproduction

Restore and hash-check the manifest's snapshot, then run from the repository root:

```text
python -m scripts.candidate_timing --db PATH_TO_SNAPSHOT.db --csv candidate-timing.csv
python -m unittest tests.test_candidate_timing tests.test_late_signals_sample
```

The script opens SQLite read-only and uses a consistent read transaction. It queries no providers and does not change production scoring or alerts.
