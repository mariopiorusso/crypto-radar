# ApeWisdom malformed collection investigation

## Evidence and limits

Read-only inspection on 14 November 2026 found 26 failed ApeWisdom runs on
13 November, all with `error=malformed` and `pages=1`. There were also 32 available
runs that day (6 completing one page and 26 completing two). The snapshot request's
approximately 11:41 UTC cutoff covered only part of that day, so its reported ten
failures is not the full-day count.

`pages=1` means page one was processed successfully; the subsequent request or its
validation failed before page two completed. It does not distinguish a bad page
envelope, an oversized/non-object response, an invalid ticker, or a duplicate
ticker within/across pages. Invalid integer fields raise `ValueError` separately.
The previous `ApeWisdomCollector.collect_persist` exception handler collapsed
`ProviderError` to its status and other exceptions to the class name. Historical
responses, headers and precise exception locations were not retained.

The collector uses the URL shape documented in the
[official ApeWisdom API](https://apewisdom.io/api/). One bounded public read of
pages one and two during this investigation returned HTTP 200 JSON with 100 and
45 rows, matching page numbers, and no duplicate or invalid tickers/counts. This
does not reproduce the historical failure or establish uninterrupted production
health. In particular, rank movement between requests could cause a duplicate,
but the historical records cannot prove that hypothesis.

**The exact historical causal input remains unknown. This is a diagnostics PR,
not a claimed fix for the intermittent provider failure.** No URL, retry policy,
row acceptance, deduplication policy, scoring, configuration or schema change is
justified by the retained evidence. Existing unmerged PRs #5 (social foundation)
and #9 (snapshot ZIP volumes) are not dependencies; this branch starts at main
`042370e` and contains only this investigation.

## Diagnostics change

New failed ApeWisdom `attention_runs.error` values are bounded JSON with a
category, phase, reason, attempted page and attempt number. Depending on the
failure they also include HTTP status, a fixed media classification, body byte
count, reported pagination values, row index, field name and first-seen page for a duplicate. Existing records
are untouched; readers must continue accepting legacy plain strings. The run
status, completed-page counter and external provider batch status are unchanged.
X collector errors retain their existing format.

The values contain no URLs, query parameters, raw headers, response bodies,
tickers, credentials or exception messages. Retries reset request diagnostics so
a timeout cannot inherit a previous response's HTTP status. Failures still roll
back all snapshot rows; malformed evidence is never accepted or replaced by zero.

Example cross-page ambiguity diagnostic:

```json
{"category":"malformed","phase":"row_validation","reason":"ambiguous_ticker","page":2,"attempt":1,"row_index":0,"field":"ticker","first_page":1,"duplicate_scope":"cross_page","http_status":200,"media":"json","body_bytes":1234}
```

## Cross-page duplicate handling

The 23 November snapshot now supplies the immediate failure trigger: row validation
on page two found a ticker already present on page one in an HTTP 200 JSON response.
That rules out a malformed URL as the observed trigger, but the historical response
body was not retained, so it cannot establish whether rankings moved between page
requests or whether separate assets shared a ticker. The
[documented API example](https://apewisdom.io/api/) provides ticker and name fields,
not a stable asset ID that can resolve that ambiguity.

The collector therefore does not merge duplicate tickers. A cross-page collision
records a bounded `ambiguous_ticker` diagnostic and an incomplete (`partial`) run,
with zero resources and no snapshot rows; consumers requiring complete available
runs cannot turn the discarded pages into attention evidence. A same-page duplicate
remains a malformed failed run with a distinct `same_page_duplicate_ticker` reason.
This contains the failure safely without claiming which upstream behavior caused
the overlap or that live collection has recovered.

## Regression coverage and next step

Offline tests reproduce the old `malformed`/one-completed-page signature with
different causal inputs and assert distinct diagnostics, rollback and privacy.
They cover duplicates, mismatched pagination, results shape, invalid counts,
invalid URL, HTML/JSON decoding, oversized and non-object responses, throttling,
authentication, server errors, timeout/connection failures, retry cleanup and
successful paginated collection with negative net votes and missing values. New
fixtures cover rank movement, repeated tickers with both shared and distinct IDs,
same-page duplicates, and the zero-row incomplete result.

Validation: Python compilation passed; all 184 application tests and 82 worker
tests passed. The fresh Windows worktree initially had two unrelated worker-test
failures because Git converted the sample email to CRLF while those tests assume
LF input. Reusing the exact committed LF fixture (no Git diff) resolves both;
this PR does not modify the worker tests or its publication preflight parser.

Validation for the 24 November duplicate handling change: all 187 application
tests passed, including the 34 attention tests; Python compilation and
`git diff --check` passed.

After review and an independently authorized deployment, inspect the next failed
run's JSON reason and phase before proposing a behavior change. No scanner
restart, deployment, scoring change, paid request, alert or historical-data write
was performed during this investigation.
