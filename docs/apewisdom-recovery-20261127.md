# ApeWisdom duplicate-ticker recovery investigation

Investigation as of 2026-11-27 11:40 UTC. This is a read-only assessment, not a deployment or a claim of live recovery.

## Evidence

| Evidence | Observation | Interpretation |
| --- | --- | --- |
| [26 November snapshot assessment](https://drive.google.com/file/d/1A-Vkp02RcN3brAt3NCmGQ_I9tpYRFrJN/view), cutoff 19:47:11.507850 UTC | 120 complete changed runs and 27 duplicate-ticker failures since its preceding cutoff; latest failures at 19:17 and 19:33 UTC. The assessment describes hash-verified snapshot reconstruction. | Historical baseline, not proof of the executing code. |
| Live SQLite `attention_runs`, read-only, 2026-11-26 19:47 to 2026-11-27 11:40 UTC | 48 runs: 36 failed `duplicate_ticker` (all first page 1, current page 2), 11 available/complete, one failed `request_failed` on page 1. Latest duplicate was 11:34:02 UTC. | Persistent cross-page signature; the complete runs show intermittent successful collection, not recovery. No same-page duplicates were observed in this window. |
| Live SQLite `apewisdom_snapshots` joined to those failed runs | Zero associated snapshot rows for failed runs in that window. | No persisted observations from those failed runs; this does not prove every downstream condition globally. |
| Merged PR [#12](https://github.com/mariopiorusso/crypto-radar/pull/12), commit `68fae80426e27913eca405616e125b611864b281` | Earlier code emits `duplicate_ticker` and raises a failed run for either duplicate. The merged code emits `ambiguous_ticker` and a partial run for cross-page collisions, and `same_page_duplicate_ticker` for same-page collisions. | The recent cross-page signature matches the earlier code path, not the expected diagnostic of the merged path. Rank movement versus genuinely distinct assets remains unknown. |
| Local scanner checkout file `crypto_radar/collectors/attention.py` | Git blob `459dd570e116b4a4acb4934f6ea2c819b2a25146`, identical to the merged `main` file and the checkout's HEAD; file last modified 2026-11-24 16:11:14 UTC. | The fixed source is on disk. A file hash is not proof of the code loaded by a running process, which could retain an older module or run elsewhere. Process command lines and start times were unavailable under the current permissions. Runtime revision is unverified. |
| [Snapshot manifest](https://drive.google.com/file/d/16LCnofHrA4GnyACBvK9C-kodKl0N68gJ/view) and assessment | Export metadata identifies Git head `db647c0f3f67fb2bb61551ea67d0214320d6dfeb` and dirty checkout. | Exporter Git metadata is not a runtime revision or proof of deployment. |

The live counts use `observed_ts >= '2026-11-26T19:47:00' AND observed_ts < '2026-11-27T11:40:00'`, provider `apewisdom`; the first matching run was 19:51:15 UTC, after the precise snapshot cutoff. Error JSON was grouped by status, completeness, reason, first page and page. Associated snapshot rows were counted by joining `attention_runs.id = apewisdom_snapshots.run_id` for failed runs in the same window. The live database can evolve, so these counts are a bounded historical observation.

## Decision and owner follow-up

The data does not establish a remaining defect in the merged collector, so no speculative code change is justified. Existing offline regression tests cover cross-page ambiguity, same-page duplicates, distinct identities, and no social observation from incomplete data. `python -m unittest discover -s tests -p test_attention.py` passed 34 tests on the merged code.

An operator with appropriate access should identify the actual scanner process and its start time, working directory and loaded collector revision, then decide separately whether to deploy or restart. After any separately authorized action, inspect fresh receipts for the expected partial/ambiguous or same-page diagnostics and verify that incomplete runs produce no observations. Retain the distinction between intermittent complete collections and sustained recovery; do not claim recovery without fresh evidence. No restart, deployment, configuration change, live request, scoring change or database mutation was made in this investigation.
