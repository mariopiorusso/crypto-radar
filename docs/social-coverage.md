# Historical social coverage at episode anchors

The [matrix](social-coverage.csv) describes 195 recorded significant-move episodes
across 66 canonical assets in the 23 November 2026 snapshot. The
[gap table](social-coverage-gaps.csv) ranks assets by episodes with no mapped
prior row and shows one pre-anchor market identity and candidate ticker per asset.
The [source manifest](https://drive.google.com/file/d/1iFe5mlm0QbEVcey7q5TYkzQ9aPJvzQqs/view)
identifies the four volumes and reconstructed ZIP SHA-256
`7bb8ca6c95ec1a70607f3c5c18119055980ed068b2f48bc383f0c3d400ad8105`.
The volume and reconstructed archive hashes were checked. Cutoff is
2026-11-23T19:54:18.610593Z; this is historical evidence, not live recovery.

## Definition and findings

The anchor here is the recorded fixed `anchor_ts`, not the later sampled
`significant_move_ts` crossing. Each recorded episode ID is counted once, not
each evaluation; correlated or overlapping episodes are not necessarily
statistically independent. Both the snapshot row receipt and its collection run
timestamp must be strictly earlier than the anchor. Mapped means the stored
canonical `coin_id`, not a guessed ticker. `available` complete collections are
counted as changed; `repeated` and incomplete responses do not add changed
history. A collection fingerprint is global to the response: a changed run does
not prove an individual ticker's values changed. Provider timestamps are absent,
so receipt freshness cannot establish freshness at the upstream source.

Twenty episodes across 16 assets have at least one mapped prior row. Under a
conservative descriptive rule requiring at least 12 distinct complete changed
runs before the anchor, a latest changed receipt no older than the threshold,
and a latest mapped response that is complete and changed, usable episodes are
8 at 30 minutes, 11 at 60 minutes and 11 at 120 minutes. The 12 runs include the
latest run and have no additional lookback window. This is an explicit audit
definition, not a reconstruction of a production feature. The earlier review's
12 of 195 used a differently specified criterion; the precise origin of the
one-episode difference is not established. Do not compare the numbers as if the
definitions were identical. The episode-level sum of mapped rows is 4,534,
including 4,274 changed-run counts and 260 repeated rows; these sums reuse a
collection across episode anchors and are not unique collection counts.

The remaining 175 episodes lack a mapped prior row. At least 120 of all 195
anchors precede the earliest retained ApeWisdom receipt, so collection timing
alone limits retrospective coverage. An absent mapped row is not evidence of
zero mentions, provider absence, a failed entity match, or complete provider
coverage. Snapshot retention, scan-universe selection, retrospective episode
construction, correlated episodes, incomplete pages, and receipt timing can
bias the denominator. Nulls and missing data remain missing.

Across the entire retained snapshot, rather than the episode-weighted sums above,
the run table records 543 `available`, 130 `repeated`, and 611 `failed`
ApeWisdom runs. It contains 72,244 provider rows, with 30 distinct mapped
canonical assets and 338 distinct tickers. These totals include observations
outside the episode cohort and do not establish provider-wide asset coverage.

## Mapping priorities

The largest uncovered counts are Bitway (11), Midnight (9), Lighter (8), Venice
Token (7), and Ethena, NEAR and Pearl (6 each). The gap table provides the
example anchor and pre-anchor market symbol/name when retained; Midnight has no
prior market identity for its first example. Candidate `.X` tickers are only
search leads, never mappings: the collector uses explicit canonical-ID mappings,
and the saved provider rows retain tickers but not provider entity names or
stable IDs. Symbol collisions therefore remain ambiguous. Zero candidate ticker
rows before an example anchor can reflect the collection starting later or
limited fetched pages; it does not prove that the asset was absent from the
provider. Prioritize identity verification from an independently dated provider
entity source, then collect a prospective cohort before changing mappings.

## Reproduce

Restore the manifest's volumes and validate the archive hash, then run:

```text
python scripts/social_coverage.py --db PATH_TO_SNAPSHOT.db --csv social-coverage.csv --gaps social-coverage-gaps.csv
python -m unittest tests.test_social_coverage
```

The script opens SQLite read-only and makes no provider calls. It uses the
retained views and does not modify scanner settings or scoring. The gap table's
candidate-ticker search is evidence of retained rows, not entity verification.
