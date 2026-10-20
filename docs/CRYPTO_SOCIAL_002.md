# ApeWisdom and X attention research

Both providers are optional, disabled by default, and independent of StockGeist.
They reuse `v12.social.providers`, validated provider options, the shared scanner
cycle, canonical asset IDs and `collector_runs` cooldown history. No additional
scheduler, scoring input, combined social score, alert threshold or trading action
is introduced. Existing five-minute StockGeist evidence retains its original path.
No CryptoSocial adapter exists in the checked-in provider registry at this revision;
the legacy injected `SocialCollector` interface remains supported and unchanged.

## Access verification (20 October 2026)

Sources checked:

- [ApeWisdom API](https://apewisdom.io/api/) documents `all-crypto`, paginated
  responses and the rank, mentions, upvotes and historical-comparison fields.
- [ApeWisdom methodology](https://apewisdom.io/methodology/) describes twice-hourly
  upstream scans and conservative ticker semantics. Neither page establishes a
  numeric request quota or continuous-collection/storage licence. Obtain those
  conditions from the provider before setting `access_confirmed: true`. A public
  endpoint alone is not proof of unrestricted continuous access.
- [X recent search](https://docs.x.com/x-api/posts/search-recent-posts) documents
  `GET https://api.x.com/2/tweets/search/recent`, bearer authentication, time
  bounds within seven days, pagination, and 10–100 results per page. The current
  reference calls the optional field selector `post.fields`; that is used here.
- [X pricing](https://docs.x.com/x-api/getting-started/pricing) describes usage
  billing and points to the Developer Console for current endpoint prices.
  Enter rates applicable to your account; no historical price is hardcoded.
  The estimator deliberately does not assume a duplicate-read billing discount.
- [X policy](https://docs.x.com/developer-terms/policy) requires offline content to
  track deletions/modifications, including action within 24 hours of a valid
  removal request. Local TTL alone does not establish compliance. Confirm an
  operational deletion/update process and applicable research/storage permissions
  before enabling access. No author identities or post text are stored.

Only documentation retrieval and offline fixture validation were performed.
Authenticated X collection, actual account entitlements, billing reconciliation
and ApeWisdom continuous collection are **not live verified**. Neither provider is
enabled by this change. No paid request or credit purchase was made.

## Configuration

Use the existing YAML configuration mechanism, not new environment switches.
The bearer token alone comes from the environment (`X_BEARER_TOKEN` by default).
This example is deliberately disabled; `null` prices pause X collection even if
its other enable switches are subsequently turned on.

```yaml
v12:
  enabled: true
  social:
    enabled: true
    providers: [apewisdom, x]
    attention_max_age_seconds: 900
    provider_options:
      apewisdom:
        enabled: false
        access_confirmed: false
        asset_map: {bitcoin: BTC, ethereum: ETH, solana: SOL}
        poll_seconds: 1800
        retry_seconds: 900
        timeout_seconds: 20
        max_pages: 3
        max_attempts: 3
      x:
        enabled: false
        access_confirmed: false
        token_env: X_BEARER_TOKEN
        queries:
          bitcoin: '$BTC OR bitcoin'
          ethereum: '$ETH OR ethereum'
          solana: '$SOL OR solana'
        exclude_reposts: true
        poll_seconds: 300
        retry_seconds: 900
        timeout_seconds: 20
        max_pages: 3
        max_attempts: 3
        max_posts_per_poll: 100
        max_posts_per_day: 1000
        lookback_seconds: 900
        overlap_seconds: 60
        retention_hours: 24
        pricing_confirmed: false
        request_usd: null
        post_usd: null
        monthly_limit_usd: null
```

Set the token in your local environment or ignored `.env`; never in YAML. Do not
paste tokens into queries. `access_confirmed` records an operator's external
access/permissions review; it does not obtain permission from the provider.
X additionally needs `pricing_confirmed` and both nonnegative unit prices. Set a
zero request price only if that matches the applicable account's pricing.
`monthly_limit_usd` is optional and per provider; `null` means no monthly cap, not
free access. There is no project-wide dollar budget. Unknown configuration fields,
ambiguous case-insensitive explicit mappings, invalid counters and nonfinite
prices are rejected.

Enable each provider independently after verification. Collection runs on scanner
cycles in `v12`/`all` mode; `v11` mode does not collect social evidence. Effective
cadence cannot be faster than the scanner interval. The shared cooldown survives
restarts through `collector_runs`. To disable requests, set that provider's
`enabled: false`; remove the token to revoke local credential availability too.
Existing providers can be listed alongside these two with their existing options.

## Evidence semantics and completeness

**ApeWisdom:** snapshots retain provider ticker, resolved canonical ID (nullable),
collection time, rank, mentions, upvotes and documented prior-day fields. Explicit
canonical-ID-to-symbol mappings follow the existing provider contract; unknown
tickers are retained unresolved, and no fuzzy matching is attempted. No update
timestamp is documented in the response, so `provider_ts` remains null. Fingerprints
identify repeated whole responses. Repetition is freshness-unknown, not proof that
activity is zero or that data are current. Pages are bounded; incomplete page
coverage is recorded and excluded from available report evidence. Unreturned
assets are unavailable, never assigned zero.

Successive changed snapshots provide rolling mention/rank/upvote deltas. A change
of +3 rolling mentions does **not** mean three new posts in five minutes. Prior-day
comparison fields are known only at the snapshot's receipt time; they never create
historical observations. Reports require a fresh changed snapshot and a preceding
fresh snapshot for a delta; otherwise deltas are null.

**X:** each configured canonical asset has an explicit query. References mean
membership in that asset's search results, not verified semantic tagging of the
post. A shared post may belong to multiple assets. Each post ID is stored once;
its first receipt is immutable. Asset membership has its own first-seen timestamp.
Engagement samples retain their observation times. No text, author/profile data,
raw response, bearer token or pagination token is persisted.

Searches end 30 seconds before the current poll. After a complete search, the next
search overlaps the prior end; the maximum lookback bounds recovery after outages.
The run records window bounds, query fingerprint, page/result counts, completeness
and known gap seconds. Query changes start a new bounded search. First-run coverage
before the search start is unknown. A result/page cap or remaining pagination token
means partial coverage, even if some posts were stored. A failed later page keeps
previously committed evidence but cannot establish complete coverage. Search results
are observed activity only, never an estimate of all X posts.

Daily/poll caps count returned resources conservatively across queries, including
duplicate reads; the database/report deduplicates post activity separately. Assets
are processed in configured query order. If the shared poll allowance is exhausted,
later queries receive partial runs with no implied zero. Reorder/reduce queries or
raise an explicitly configured allowance when coverage is persistently unequal.

## Costs, failure recovery and retention

Each paid HTTP attempt reserves one request plus its maximum possible returned
posts in `provider_usage` before any network I/O. An immediate SQLite transaction
serializes monthly and daily allowance checks across processes. Estimates are
integer micro-USD rounded upward. Successful responses reconcile to resources
received; failed, malformed or timed-out requests retain conservative reservations.
Retries are separate potentially billable attempts. In-flight reservations survive
crashes and are never automatically released or reissued as free work.

Bounded exponential retries handle timeouts, connection failures and server errors.
Authentication and 429 responses stop the current poll; the common scheduler
persists cooldown, including a bounded numeric `Retry-After` where provided.
No redirects, scraping, account recharge or purchase operations occur. Provider
failures are isolated by the existing provider loop. Pause on unknown pricing,
missing credentials or exhausted allowance. An uncertain charge must be reconciled
against provider billing before an operator adjusts its ledger entry; do not delete
the ledger simply to restart collection.

The ledger's UTC month, request/resource counts, rate configuration, USD estimate,
reservation and status survive restarts. Local estimates exclude unconfigured fees
and are **not a substitute for provider-side account billing limits**. Other
applications using the same credentials are outside this ledger's scope.

Schema v4 is additive. The existing migration runner backs up the database before
DDL, uses a transaction and retains all older observations. Do not migrate a live
production database merely to run offline tests. New relations are
`attention_runs`, `apewisdom_snapshots`, `x_posts`, `x_post_assets`,
`x_post_metrics`, and `provider_usage`, with time/asset/period indexes and unique
keys. Full and compact analysis snapshots support v2/v3/v4. Compact snapshots are
research exports, not operational billing-ledger backups.

X retention deadlines are fixed at first receipt and do not extend on rereads.
Expired posts and their metrics/memberships are removed each scanner cycle even
with collection disabled. For an inactive scanner, schedule local maintenance:

```powershell
.\.venv\Scripts\python.exe -m crypto_radar.attention_maintenance --db PATH_TO_DB
.\.venv\Scripts\python.exe -m crypto_radar.attention_maintenance --db PATH_TO_DB --delete-post 12345
```

Process provider/account-owner deletion requests promptly and handle changed or
protected content through your approved compliance process. This implementation
does not subscribe to X compliance events or make paid revalidation requests.
Keep collection disabled if that process is unavailable. Apply deletions/retention
to existing backups and exported snapshots too; scanner maintenance cannot recall
copies shared elsewhere. Do not redistribute X evidence without reviewing the
current agreement. Retention removes evidence needed by historical recomputation;
record approved aggregate research outputs when permitted. New scanner evaluations
freeze numeric attention features in the existing `features_json`, without post
identifiers, so subsequent +24-hour outcomes can use the original information set
after raw post expiry. These aggregates do not enter scoring. Confirm that your
agreement permits retaining them; deletion obligations may also apply to derived
data. Re-analysis with a different freshness window still needs raw evidence.

## Experimental reporting and offline validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m crypto_radar.attention_report --db PATH_TO_SNAPSHOT
.\.venv\Scripts\python.exe -m crypto_radar.episode_report --db PATH_TO_SNAPSHOT
```

The episode report includes an `attention` section. The standalone report accepts
`--max-age-seconds` (default 900) for the information freshness window. For ApeWisdom's
slower upstream cadence, choose and document a suitable analysis window; this is
independent of the provider's five-minute-bucket freshness setting. Set
`v12.social.attention_max_age_seconds` to freeze that window during collection;
reporting with the same window reuses the saved as-of features.

Reports compare ApeWisdom-only, X-only, both and neither observed cohorts. Neither
means unavailable evidence, not no social activity. Features include rolling
deltas, observed X post density and observed engagement changes. Engagement needs
two distinct receipt times; absence is null. Evaluations use only receipts no later
than their detection/evidence cutoff. Posts first received after their event time
remain retrospective; delayed posts are flagged and excluded from X early-attention
timing triggers. The timing output compares first available attention with the
episode's market-volume anomaly. News evidence counts accompany every cohort.

For +1/+3/+6/+12/+24 hours, reports use existing measured absolute, BTC-, ETH- and
broad-market-relative outcomes within timing tolerance. Evaluations are averaged
within episodes before equally weighting episodes. Missing outcomes/benchmarks
stay null. Cohorts are descriptive, overlapping and selection-dependent; these
statistics are not significance tests or evidence of predictive value.

Fixtures are `tests/fixtures/apewisdom.json` and `tests/fixtures/x_recent.json`.
Example: EX has 12 rolling mentions and 20 upvotes; UNKNOWN remains unresolved.
The X fixture has post `12345`, event time 11:55 UTC and first receipt 12:00 UTC.
Its text and author are deliberately discarded. The synthetic report fixture
advances EX to 15 mentions and has two evaluations returning 2%/6% in one episode
and 10% in another. Equal episode weighting gives 7%, BTC-relative 6% and
broad-relative 5%; ETH-relative remains null. Both sources are observed, and the
attention-to-volume-anomaly lead is 10 minutes in each of the two episodes.
These are invented test outcomes, not live market findings.

The generated [example report](examples/attention-report.json) includes all four
cohorts, horizons, coverage, features and fixture usage. Tests block external
sockets and use mocked responses for schema changes, missing metrics, ambiguity,
deduplication, pagination, outages, auth, rate limits, billing reservations,
restart recovery, retention/deletion, migrations, temporal cutoffs, independent
provider operation and full/compact report equivalence.

Troubleshooting: inspect `collector_runs` for access/pricing/budget/auth/cooldown
states, `attention_runs` for search coverage, and `provider_usage` for reservations.
An unavailable provider is not proof of zero activity. No credentials should be
added to logs or reports when diagnosing failures.
