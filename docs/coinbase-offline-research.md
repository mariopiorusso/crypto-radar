# Coinbase order-flow research: offline gate

This component accepts **synthetic fixtures only**, never opens a network connection, and is disabled unless explicitly enabled. It has no trading, production database, scanner, or alert integration. Output is exclusively created at the supplied isolated path; event count is capped at 10,000. An example from the repository root:

```powershell
python -m research.coinbase_offline --enable-offline --fixture research/coinbase_synthetic.jsonl --output ./coinbase-synthetic-output.jsonl --snapshot-sequence 1 --max-events 100
```

The fixture has a made-up sequence and receipt timestamp. The offline command has a 60-second default deadline, configurable with `--max-seconds` from 1 to 3600, and a separate event limit. It publishes the output exclusively after reaching EOF successfully; malformed input, a limit, or interruption leaves no final output. An abrupt process kill can leave a hidden `.coinbase-incomplete-*` staging file, which must never be used as completed output. Publication requires hard-link support in the output directory. Each result carries the original maker side, inverted taker side, event and receipt UTC, product, sequence, trade ID, decimal price/size, duplicate and validity/completeness flags. Unknown side, bad timestamps, identifiers, or amounts invalidate the current state. Disconnects must call `invalidate`; sequence gaps make all subsequent records invalid until an independently validated new snapshot is supplied. A repeated trade is never valid. Nothing fills a missing interval with zero. The snapshot sequence is a trust boundary, not proof that a live stream is complete; upstream channel sequence semantics and snapshot alignment must be established before live use. JSONL results marked invalid must not enter flow aggregates. The bounded in-memory duplicate set lasts for the process lifetime.

## Identity and access gates

The earlier [venue study](venue-coverage-investigation-draft.md) documents a historical `venice-token` lead, Coinbase's Venice Token / VVV asset page and a 26 November 2026 VVV-USD listing observation. This is a name-backed lead, not verified contract-level identity at historical anchors. Require a timestamped canonical asset/product crosswalk and listing interval before any join; never join on ticker alone.

Coinbase's [market data terms](https://www.coinbase.com/legal/market_data) restrict use and redistribution; the applicable entity, jurisdiction, internal retention duration and derived-data rights remain unresolved. The [Exchange feed limits](https://docs.cdp.coinbase.com/exchange/websocket-feed/rate-limits) and [connection guidance](https://help.coinbase.com/en/exchange/managing-my-account/market-data-connections) must be rechecked for the actual account and host before activation. No permission to redistribute or retain raw data is assumed. Product identity, rights, limits, channel continuity, snapshot/backfill and storage retention are blocking gates; no live collector or pilot is authorized or implemented here.

## Separately authorized pilot design

After owner approval and resolution of every gate, implement and independently test a live read-only connector with disconnect detection, fresh verified snapshot/backfill and isolated bounded retention. Freeze one product and a short runtime before connecting; log observed disconnects, time gaps, duplicate rate, total and valid events, event-to-receipt latency percentiles, bytes and incomplete intervals. Compute imbalance only on verified complete intervals; report missing periods rather than zeros. Compare the measured completeness to a prespecified threshold. This is a feasibility measurement, not predictive lift or a production recommendation.
