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

Example future error:

```json
{"category":"malformed","phase":"row_validation","reason":"duplicate_ticker","page":2,"attempt":1,"row_index":0,"field":"ticker","first_page":1,"http_status":200,"media":"json","body_bytes":1234}
```

## Regression coverage and next step

Offline tests reproduce the old `malformed`/one-completed-page signature with
different causal inputs and assert distinct diagnostics, rollback and privacy.
They cover duplicates, mismatched pagination, results shape, invalid counts,
invalid URL, HTML/JSON decoding, oversized and non-object responses, throttling,
authentication, server errors, timeout/connection failures, retry cleanup and
successful paginated collection with negative net votes and missing values.

Validation: Python compilation passed; all 184 application tests and 82 worker
tests passed. The fresh Windows worktree initially had two unrelated worker-test
failures because Git converted the sample email to CRLF while those tests assume
LF input. Reusing the exact committed LF fixture (no Git diff) resolves both;
this PR does not modify the worker tests or its publication preflight parser.

After review and an independently authorized deployment, inspect the next failed
run's JSON reason and phase before proposing a behavior change. No scanner
restart, deployment, scoring change, paid request, alert or historical-data write
was performed during this investigation.
