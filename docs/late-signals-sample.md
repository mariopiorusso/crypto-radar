# Late V1.1 signal micro-audit

This read-only audit uses the 23 November 2026 analysis snapshot (cutoff
`2026-11-23T19:54:18.610593+00:00`). Its [Drive manifest](https://drive.google.com/file/d/1iFe5mlm0QbEVcey7q5TYkzQ9aPJvzQqs/view)
identifies the four volumes and the reconstructed ZIP SHA-256
`7bb8ca6c95ec1a70607f3c5c18119055980ed068b2f48bc383f0c3d400ad8105`.
Volume, chunk and reconstructed archive hashes were checked before extraction.
The [CSV](late-signals-sample.csv) contains the timestamps and recorded features.

## Selection and definitions

The requested “move anchor” is interpreted, with user confirmation, as the first
sampled **significant-move crossing**, not the earlier fixed price anchor. The
existing `fixed-anchor-1.2` definition finds the first sampled price at least the
episode's configured percentage above its fixed anchor within its window. The
script recomputes each selected crossing from retained market observations and
fails if it differs from the recorded timestamp. Both timestamps appear in the CSV.

Of the snapshot episodes, 56 have a recorded first market anomaly strictly before
the crossing and a first V1.1 candidate strictly after it, with the candidate at
or before the snapshot cutoff. The literal alternative, anomaly before the fixed
anchor and candidate after it, yields zero. The first ten qualifying distinct
episode IDs are selected by fixed anchor UTC, asset ID, then episode ID. These are
existing episode units, not proof of statistically independent market events: the
sample includes two Sky episodes, and assets and windows can be correlated.
Positive anomaly lead and candidate delay in the CSV are minutes relative to the
crossing. Candidate means recorded numeric eligibility, not an alert.

## Observations and limits

All ten last usable pre-crossing evaluations were recorded as `below_threshold`;
their scores range from 6.9 to 39.5 against the recorded threshold of 45. Seven
have a zero volume-ratio score component. Nine record 287 historical observations;
one records 50. These are descriptions of saved features, not proof that any one
component caused lateness or that warmup, freshness, or sampling cadence had no
effect. Candidate delays range from 10.27 to 16,985.28 minutes (median 3,831.41).
Long episodes and outages can inflate these delays; the elapsed interval alone does
not distinguish threshold behavior, stale data, cadence, or collection gaps.

For attribution the script accepts only an evaluation whose market timestamp,
evidence cutoff, detection, scan start **and scan completion** precede the crossing.
It takes the latest such evaluation, rather than importing a later feature value.
The scan completion condition is conservative: recorded detection timestamps can
represent market receipt rather than the time computation finished. Source freshness
and precise candidate-computation times are not established here. Episode membership
and first-signal fields are retrospective; a later snapshot cannot recreate the
complete historical state of the database at each instant. These limitations preclude
causal claims and prospective performance estimates.

## Reproduce

Restore and hash-check the snapshot using its manifest and volume restore utility,
then from the repository root run:

```text
python scripts/late_signals_sample.py --db PATH_TO_SNAPSHOT.db --cutoff 2026-11-23T19:54:18.610593+00:00 --csv late-signals-sample.csv
python -m unittest tests.test_late_signals_sample
```

The script opens SQLite read-only and requires no provider access. It uses the
retained episode definitions, rather than creating new episodes or changing scoring.
