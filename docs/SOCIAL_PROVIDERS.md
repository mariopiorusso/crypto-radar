# Provider-independent social collection

StockGeist remains the external API adapter. CryptoSocial is an additive first-party
offline evidence adapter with a blocked Reddit source; see [CryptoSocial](CRYPTOSOCIAL.md)
for the access review, canary and limitations. LunarCrush and Santiment
are future adapters. No provider is enabled by default, and this change does not
enable production collection, experimental AI, alerts, or trading.

## Configuration

These settings belong under the existing `v12` section of `config.yaml`:

```yaml
v12:
  social:
    enabled: false
    providers: [stockgeist]  # [] also works; future registered providers can coexist
    provider_options:
      stockgeist:
        enabled: false
        token_env: STOCKGEIST_API_TOKEN
        asset_map:
          bitcoin: BTC
          ethereum: ETH
        poll_seconds: 300
        retry_seconds: 900
        timeout_seconds: 20
        history_hours: 25
        max_age_seconds: 900
        finalization_lag_seconds: 300
        timestamp_convention: start
        sources: []
```

Set `STOCKGEIST_API_TOKEN` through the environment or the existing `.env` loader;
never put its value in YAML. Set both enable switches to true only after checking
account entitlements, asset mappings, source coverage and timing semantics.
The legacy `social.provider: none` setting remains accepted. Use `providers` for
the new layer. V1.2 must also be enabled through the existing experiment controls.

Each provider has separate settings, enablement, environment-variable name, asset
mapping, timeout, rate-limit retry and freshness limit. A bounded request failure
does not prevent another provider from collecting during the same scan. Calls are
sequential with bounded timeouts, not background collection threads.
`collector_runs` stores `social:<provider>` status and last attempt. Poll spacing
and numeric Retry-After cooldowns survive restarts; skipped polls do not reset the
cooldown. No immediate retry is made against a failing paid API.

## StockGeist adapter

Implemented against the [official OpenAPI specification](https://docs.stockgeist.ai/openapi.json),
retrieved on 2026-10-06. It uses the historical JSON endpoint
`GET /crypto/global/hist/message-metrics`, a `token` header, explicit symbols,
UTC start/end and `timeframe=5m`. This is not an SSE streaming client.

The adapter retains the documented numeric message metrics separately as native
evidence. Total, positive, negative and neutral counts map to nullable normalized
fields. Sentiment is `(positive-negative)/total` when defined; it is a derived
Crypto Radar measure, not a claim that other providers use an equivalent model.
Unique authors and engagement remain NULL because this endpoint does not document
them. Zero totals remain measured zero and have undefined/NULL sentiment.
Unexpected symbols, malformed counts and inconsistent totals are rejected.

The specification gives timestamped series, but does not establish bucket
finalization guarantees. `timestamp_convention: start` is an explicit deployment
assumption; `end` is supported. The configured lag excludes unfinished intervals
under that convention. Validate these assumptions before using live evidence.
Prices, licensing, plan entitlements, backfill limits and current coverage were
not verified by this implementation. No authenticated provider request was made.
Repeated historical queries can consume credits: begin with a small explicit
asset map (maximum 50 unique symbols), review plan costs, and choose an adequate
history window for the baseline. No symbol-only guessing or silent asset mapping.

## Evidence and research

Schema v3 adds `provider_social_observations`. Migration uses the existing SQLite
backup and transaction mechanism. It does not rebuild or delete the legacy
`social_observations` table. The implementation was tested on temporary databases;
the live database was not migrated as part of development.

Every new row retains provider, provider asset ID, canonical coin/symbol, source,
bucket bounds, provider response timestamp, locally measured receipt/observation
timestamp, availability, nullable normalized metrics, safe metadata and separate
native numeric JSON. Unknown/raw authentication fields are never copied. First
receipt wins for a provider/asset/source/bucket; revisions do not overwrite history.
Historical backfill is marked with its actual receipt time, not made available
retroactively. A historical row may be stale on receipt and still supply a baseline
for a later evaluation; the latest bucket must satisfy the freshness limit.

Features are calculated independently per provider and canonical asset. Provider
asset mapping, source coverage and normalization conventions distinguish series.
No sums or averages cross provider boundaries. `features_json.providers` retains
every provider's metrics, baseline version and observation IDs. The existing
research interface selects the highest provider score (provider-name tie break)
and records `selected_provider` plus `max_provider_score_v1`. This is an explicit
research selection rule, not calibrated consensus. Existing V1.1 is unchanged.
No new provider-specific experiment groups or predictive-performance claims are
introduced. Changing the selection rule requires a new research/scoring version.

Schema-v2 and v3 compact exports remain supported. V3 exports retain the new table
losslessly with the existing history/anchor policy and expose
`analysis_social_providers`. Episode reports include per-provider availability and
NULL-versus-zero counts; existing outcomes, benchmarks and market regimes are
unchanged. Provider-level lead/lag or consensus cohorts can later use the saved
per-provider features and their original receipt timestamps.

## Adding another provider

1. Implement the `SocialCollector.collect(assets, now)` interface.
2. Map canonical IDs explicitly and emit `SocialObservation` rows with the provider
   identity, normalized nullable values and safe native numeric evidence.
3. Supply a `ProviderSettings` subclass as the adapter's `settings_model` if it
   needs additional validated configuration. Keep credentials in environment variables.
4. Register its factory in `social_providers.registry()`.
5. Add mocked response, timing, missingness and rate-limit tests; enable via config.

The collection orchestrator, provider baselines, storage, snapshots and reporting
remain provider-neutral. A fixture second provider verifies the integration path.

## Offline checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

Tests cover documented StockGeist-shaped responses, missingness and genuine zeros,
malformed data, asset mapping, independent providers/baselines, failure isolation,
persistent cooldowns, disabled providers, receipt chronology, staleness, migration
rollback/backup, and compact/full report equivalence. Tests make no paid calls.
