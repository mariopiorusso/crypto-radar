# Candidate shadow experiment: bounded historical feasibility

The frozen [protocol](candidate-shadow-protocol.md) and [episode evidence](candidate-shadow.csv) compare the recorded V1.1 candidate with an exploratory score-35 shadow on the 26 November snapshot. The result is **no-go for production or a claim of improvement**: the early-recall difference is accompanied by a higher observed candidate rate in controls, the sample is small and event-conditioned, and the shadow is not an exact V1.1 replay.

## Source and baseline

The [26 November manifest](https://drive.google.com/file/d/16LCnofHrA4GnyACBvK9C-kodKl0N68gJ/view) describes cutoff `2026-11-26T19:47:11.507850Z`. All four downloaded volumes matched their manifest SHA-256 values, and the reconstructed ZIP matched `afa96325bd9f365a466d62fac8220d6814107e7141be2f14122b6232b5592262`. A read-only SQLite query of the extracted snapshot counts 210 stored crossings; of those, 122 had a recorded candidate by cutoff: 26 strictly early, five simultaneous, 91 late. These match the earlier [snapshot assessment](https://drive.google.com/file/d/1A-Vkp02RcN3brAt3NCmGQ_I9tpYRFrJN/view). This baseline uses stored crossing timestamps; it does not independently recompute all 210 prices.

## Frozen holdout

The protocol fixes the old-data cutoff, 24-hour embargo, holdout dates, score 35 and completeness rules before evaluation. Among 26 distinct episode IDs and 26 assets, 14 had a recomputed sampled +5% crossing, 12 had no sampled crossing with the required coverage, and none were unknown under that coverage rule. All 14 recomputed crossings match stored timestamps. Each episode has at least one linked V1.1 evaluation; 7,981 linked rows were inspected in total. No same-asset holdout windows overlap within 24 hours, though cross-asset market events can be correlated.

| Measure | Recorded V1.1 | Score-35 shadow |
| --- | ---: | ---: |
| Movers with candidate strictly before crossing | 0 / 14 | 8 / 14 |
| Controls with candidate in the 24-hour window | 3 / 12 | 7 / 12 |
| All holdout episodes with a candidate in window | 6 / 26 | 19 / 26 |
| Median lead among early movers | Not applicable | 151.369 minutes (8 episodes; range 5.005–781.837) |

The shadow candidate counts include candidates after a crossing; early recall counts only those strictly before it. A narrow early-mover versus candidate-positive-control proxy is 0/(0+3) for recorded and 8/(8+7) for shadow. It is not population precision: late mover candidates are excluded from that proxy, the controls are selected from existing research episodes rather than all eligible asset-days, and the sample is too small to establish an operational false-alert rate. No statistically independent lift, profitability, or live recovery follows from these counts.

For no-crossing controls, the 15-minute boundary and gap rules establish only sampled coverage, not the absence of an intervening price move. Unknown or missing data is not treated as a negative outcome. The historical episode itself is created by prior signals and may introduce selection and survivorship bias. The recorded first-candidate timestamp is not reconstructed from scan completion; shadow timestamps use scan completion and require inputs to be available by then, so their timing provenance differs. Provider event/receipt semantics, asset universe and delistings are not fully established. Holdout selection is chronological and mature, but no separate untouched prospective holdout remains after this analysis.

Reproduce from the repository root with the hash-verified extracted database:

```text
python -m scripts.candidate_shadow --db PATH_TO_SNAPSHOT.db --csv candidate-shadow.csv
python -m unittest discover -s tests -p test_candidate_shadow.py
```

The script opens the database read-only. Tests cover strict crossing time, missing coverage and gaps, unavailable inputs, suppression, missing timestamps and the score boundary. No collection, scanner restart, scoring, alert or trading changes were made. A later evaluation should freeze a prospective universe and comparable timestamped baseline, retain missingness and controls, and use a fresh holdout before deciding on any operational change.
