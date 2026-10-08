# CryptoSocial V0.1: offline evidence foundation

Task SOCIAL-OWN-001. **IMPLEMENTED: offline provider/source boundary, synthetic
normalization and persistence. WAIT: approved Reddit access and a suitable live
source adapter. VALIDATED predictive value: false.** No genuine Reddit data was
collected. There is no live Reddit HTTP implementation, service, scraper, or
configuration-selectable fixture. Setting flags or supplying a token cannot
enable a live feed in this version.

## Architecture and boundaries

The existing `SocialCollector` contract and provider registry now accept
`cryptosocial` alongside the unchanged `stockgeist` adapter. Zero, one, or multiple
providers work. External LunarCrush/Santiment adapters remain future work.
CryptoSocial owns first-party normalization; `FirstPartySource` supplies source
items and declared, finalized coverage. `RedditSource` currently returns
`approval_required` without any network operation. Synthetic sources can be
injected in tests only. Another source can implement this boundary; source naming
and configuration validation must also be explicitly extended when implemented.
No standalone HTTP service or paid infrastructure is needed.

`observation_only: true` is mandatory for CryptoSocial. The existing provider
collection path persists its normalized rows, but candidate scoring excludes it.
Offline research may inspect its provider-relative features separately. Existing
StockGeist selection and V1.1/V1.2 market/news scoring, budgets, alerts, outcomes,
episodes, market regimes and trading controls are preserved. Anomaly features are
not calibrated predictions. Neither H0 (no incremental information) nor H1
(useful upstream attention) is resolved by fixtures.

## Official access review, 2026-10-07

Reviewed current official sources:

- [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy), updated 2026-10-05.
- [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), updated 2026-05-11.
- [Data API Terms](https://redditinc.com/policies/data-api-terms).
- [API endpoint reference](https://www.reddit.com/dev/api/).

The policy requires explicit approval before API access. Research outside the
Reddit for Researchers (RFR) program is prohibited by the current policy. RFR
access is non-commercial; this project's financial attention research has not
been confirmed eligible. Personal use, public content and a token do not establish
approval. Commercial usage needs written approval. The access terms, retention,
derived-data export rights and ability to obtain a timely, complete five-minute
stream all need confirmation before a live adapter can be implemented.

The Data API technical guidance requires a registered OAuth token and a truthful,
descriptive User-Agent. Eligible free clients have a stated 100 queries/minute per
OAuth client ID averaged over ten minutes; that technical allowance does not grant
research access. Monitor `X-Ratelimit-Used`, `X-Ratelimit-Remaining` and
`X-Ratelimit-Reset`. No anonymous `.json`, HTML scraping, impersonation, alternate
account, CAPTCHA bypass, or feed fallback is implemented. A listing page cannot
establish complete coverage of posts/comments across selected communities.

**Blocker:** approval, eligible access mechanism and deletion/retention handling
are unverified. Consequently the live network implementation stops here. Even
after obtaining approval, source-specific work and tests remain necessary; this
is not a dormant fully implemented HTTP client.

## Evidence and metric definitions

`SourceItem` contains a stable source item ID, aware event timestamp, community,
transient text, optional transient source-local author, score and comment count.
Only synthetic items have exercised this model. Text is used in memory for asset
resolution and never persisted. No usernames, author hashes, profiles, URLs,
private content, or arbitrary JSON payloads enter the database. Removed/deleted
text markers are ignored; missing/deleted authors make authorship unmeasurable.
This is not a live deletion-compliance implementation.

`social_source_items` (schema v4) keeps minimal item ID, event time, first local
receipt time, community, matched canonical assets, optional engagement,
normalization version and `synthetic_offline_only` evidence kind. A multi-asset
item stays one record per provider/source/version; each normalized asset bucket
can be traced using time, mapping and matched assets. This allows source/community
chronology without duplicating raw text. Author bursts and repeated-text detection
cannot be reconstructed from this minimized persisted evidence; they require a
separately approved retention design. Transient author diversity is measurable.

Per asset/source/completed five-minute bucket:

- Mentions: distinct matched source item IDs, one mention per asset/item regardless
  of how many aliases occur. Multiple assets in one post are supported.
- Unique authors: distinct transient identifiers, NULL if any contributing item
  has missing/deleted author identity. Empty measured coverage gives zero.
- Engagement `reddit_nonnegative_score_plus_comments_v1`: sum of
  `max(score, 0) + comment_count` per distinct item. This measures observed thread
  engagement at collection, not activity newly generated during the event bucket.
  It is NULL if any contributing item lacks either component. Negative scores
  clamp to zero; score is net voting, not a count of upvotes. Posts and their
  comments must not be double-counted by a future source.
- Sentiment, positive/negative/neutral counts, duplicate-text and concentration
  measures: NULL, unsupported. No LLM or paid sentiment calls.

SourceResult coverage must explicitly declare complete finalized buckets for the
configured communities. An incomplete/truncated response is `partial_error` and
produces no observations; an absent coverage window produces no row, never zero.
An empty *covered* bucket has measured zeros for mentions/authors/engagement.
There is no claim that an ordinary Reddit `/new` listing meets this contract.
Partial community success is not mislabeled complete aggregate coverage.

## Time, idempotence and baselines

Times normalize to UTC. Windows are `[start, end)` with aligned 300-second
boundaries. Only completed intervals before the configured finalization lag are
accepted. Future event timestamps are rejected, unfinalized items excluded and
out-of-horizon stale items excluded. Valid historical buckets may be stored as
stale; receipt is always assigned locally after collection, never supplied by a
source. Event timestamps and receipt timestamps are separate. As-of research
excludes rows received later, including delayed/backfilled evidence.

Duplicate item IDs in a source response collapse; conflicting copies fail
validation. Item persistence uses a provider/source/item/version primary key.
Existing provider bucket uniqueness and INSERT OR IGNORE preserve first receipt
and prevent repeated polls or restarts from increasing counts. Finalized buckets
are immutable, not incrementally updated. Later discoveries do not revise a
previously accepted complete bucket: a future live source must establish genuine
finalization or explicitly redesign revision semantics before deployment.

Aliases are configured per canonical CoinGecko asset ID. Matching is
case-insensitive and token-aware, with explicit cashtags; substrings such as ETH
in an unrelated word do not match. Ambiguous bare ONE/LINK/NEAR/ARB aliases are
rejected; use full names/cashtags. Other ambiguities still need operator review.
Shared aliases across different assets are rejected. No universal five-asset
business-logic universe is hard-coded. The configured asset map is the canary
universe, further intersected with assets available in the scanner's market data.

The normalization identity hashes aliases, asset map, community coverage and
mapping version. Provider/source/identity/metric versions isolate baselines;
definition changes start a new cohort and require new history. CryptoSocial and
StockGeist absolute counts never share baselines. Five-minute bucket history
supports 5m/15m/1h features without introducing a new social/consensus score.

## Persistence, export, reports and resilience

Migration v4 is additive and uses the existing pre-migration backup and atomic
transaction. Existing rows are preserved; failed DDL rolls back. No live database
was migrated during this task. `provider_social_observations` remains the shared
normalized research table, not a parallel first-party experiment pipeline.

Compact snapshots support v2/v3/v4 and export normalized observations, metadata,
NULLs, event windows and receipt times. They deliberately omit
`social_source_items`; `excluded_tables` records why. Minimal item evidence stays
in the source DB. No live Reddit retention period or deletion policy is authorized
by this prototype; approval must settle even derived aggregate retention/export,
and the future adapter must implement deletion reconciliation before activation.
Do not upload real Reddit-derived snapshots to AI analysis before confirming rights.

Episode reports show provider availability/counts and per-source missing vs zero
metrics, event window and receipt bounds. First positive mention receipt is named
`first_attention_receipt`; it is not an anomaly, independent episode, or proof of
lead time. Existing actual episode signal chronology is preserved unchanged.

Provider errors do not block other providers. There are no immediate retries.
Existing persistent `collector_runs` history enforces poll spacing and retry
cooldowns, including bounded numeric Retry-After for rate limits. Approval and
partial-response statuses also back off. A replacement collector cannot bypass
a recorded cooldown. Exception text is not logged or stored as provider error.

## Recommended canary (not activated)

Merge these settings into the existing `v12` config only after review; keep other
sections and current production switches intact. These are illustrative settings,
not evidence that live access is available:

```yaml
v12:
  social:
    enabled: false
    providers: [cryptosocial, stockgeist]
    provider_options:
      cryptosocial:
        enabled: false
        sources: [reddit]
        token_env: REDDIT_ACCESS_TOKEN
        communities: [CryptoCurrency, Bitcoin]
        asset_map:
          bitcoin: BTC
          ethereum: ETH
          solana: SOL
          ripple: XRP
          dogecoin: DOGE
        aliases:
          bitcoin: [bitcoin, btc, "$btc"]
          ethereum: [ethereum, ether, eth, "$eth"]
          solana: [solana, "$sol"]
          ripple: [ripple, xrp, "$xrp"]
          dogecoin: [dogecoin, doge, "$doge"]
        mapping_version: aliases-v1
        observation_only: true
        poll_seconds: 300
        retry_seconds: 900
        timeout_seconds: 20
        history_hours: 25
        finalization_lag_seconds: 300
        max_age_seconds: 900
        max_items: 1000
      stockgeist:
        enabled: false
        token_env: STOCKGEIST_API_TOKEN
        asset_map: {bitcoin: BTC, ethereum: ETH}
```

StockGeist stays independently available and disabled pending credentials/access
validation. Eventually both may be enabled without merging their evidence.

## Exact Windows operator workflow

1. Review/merge separately; this branch is not deployed. Obtain Reddit approval
   for this specific research and confirm RFR/commercial classification, permitted
   interface, retention/deletion/export rights and complete timely coverage.
2. Implement and review the approved source adapter. There is **no working live
   credential setup or live collection command in V0.1**. The reserved
   `REDDIT_ACCESS_TOKEN` name is future configuration, not a functional token
   exchange. Use environment variables/the existing `.env` loader when the approved
   adapter specifies its requirements; never put secret values in YAML or command
   history. No client credentials or token values were inspected in this task.
3. Verify offline mechanics now, using the existing virtual environment:

   ```powershell
   Set-Location C:\crypto-radar\worktrees\social-own-001
   & C:\crypto-radar\.venv\Scripts\python.exe -B -m unittest discover -s tests
   ```

4. After approved live source implementation/review, select the five canary
   mappings above, enable `v12.enabled`, `v12.social.enabled` and CryptoSocial's
   `enabled`. Preserve AI/alert settings and thresholds; keep StockGeist disabled
   pending its own validation. Ensure selected coins are in scanner market coverage.
5. Only then run one manual scan from the reviewed deployment checkout:

   ```powershell
   Set-Location C:\crypto-radar
   .\run_once.ps1 -Detector all
   ```

   Running this before source implementation records `approval_required`, not
   real social evidence. This command also invokes existing market/news collection
   and any already-configured alerts/AI; it was not run during development.
6. Query an existing full DB read-only (after an authorized scan/migration):

   ```powershell
   @'
   import sqlite3
   from pathlib import Path
   p = Path('data/crypto_radar.db').resolve()
   with sqlite3.connect(p.as_uri()+'?mode=ro', uri=True) as db:
       for sql in (
           "SELECT collector,status,observed_ts,error FROM collector_runs WHERE collector LIKE 'social:%' ORDER BY observed_ts DESC LIMIT 20",
           "SELECT provider,source,coin_id,window_start,window_end,provider_ts,receipt_ts,availability,mentions,unique_authors,engagement,metadata_json FROM provider_social_observations WHERE provider='cryptosocial' ORDER BY receipt_ts DESC LIMIT 20",
           "SELECT count(*),sum(mentions IS NULL),sum(mentions=0),sum(unique_authors IS NULL),sum(engagement IS NULL) FROM provider_social_observations WHERE provider='cryptosocial'",
           "SELECT item_id,event_ts,receipt_ts,community,evidence_kind FROM social_source_items WHERE provider='cryptosocial' ORDER BY receipt_ts DESC LIMIT 20"
       ):
           print(sql)
           for row in db.execute(sql): print(row)
   '@ | .\.venv\Scripts\python.exe -B -
   .\.venv\Scripts\python.exe -B -m crypto_radar.episode_report --db data/crypto_radar.db
   ```

7. Confirm versioned metric definitions, coverage, NULL vs zero, first receipt,
   delayed items and configured market universe. Run offline tests proving
   CryptoSocial never changes candidate scores, AI calls or alerts. Check scanner
   config thresholds/budgets against the predeployment config separately.
8. After successful approved canary validation, continuous operation uses:

   ```powershell
   .\run_forever.ps1 -Detector all
   ```

   This task does not start continuous operation or modify scheduled tasks.

## Scientific limitations

Social attention does not imply future price movement; sentiment is not causality.
Reddit is not representative of all crypto participants. Bots/promotion distort
metrics and are not detected here. Popularity and community coverage vary by asset
and over time; provider metrics are not directly comparable. Backfills are available
only at receipt, not event time. Frozen finalized windows can miss later discoveries.
Synthetic complete coverage demonstrates code mechanics, not real completeness.
Missingness, availability, independent episodes and held-out market/news controls
must govern research; many correlated five-minute rows are not independent
successful predictions. No live prediction, calibrated probability, capital
allocation or trading behavior is implemented.
