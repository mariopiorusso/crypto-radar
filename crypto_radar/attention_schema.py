"""Schema v4: rolling attention, sampled posts, coverage and durable usage."""
STATEMENTS = [
    '''CREATE TABLE attention_runs (
       id TEXT PRIMARY KEY, provider TEXT NOT NULL, query_key TEXT NOT NULL,
       observed_ts TEXT NOT NULL, window_start TEXT, window_end TEXT,
       status TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
       pages INTEGER NOT NULL DEFAULT 0, resources INTEGER NOT NULL DEFAULT 0,
       fingerprint TEXT, error TEXT, query_hash TEXT, gap_seconds REAL NOT NULL DEFAULT 0)''',
    'CREATE INDEX idx_attention_runs ON attention_runs(provider,query_key,observed_ts)',
    '''CREATE TABLE apewisdom_snapshots (
       run_id TEXT NOT NULL REFERENCES attention_runs(id), ticker TEXT NOT NULL,
       coin_id TEXT, observed_ts TEXT NOT NULL, provider_ts TEXT,
       mentions INTEGER, upvotes INTEGER, rank INTEGER,
       mentions_24h_ago INTEGER, rank_24h_ago INTEGER,
       PRIMARY KEY(run_id,ticker))''',
    'CREATE INDEX idx_ape_asset_time ON apewisdom_snapshots(coin_id,observed_ts)',
    '''CREATE TABLE x_posts (
       post_id TEXT PRIMARY KEY, event_ts TEXT NOT NULL, first_seen_ts TEXT NOT NULL,
       expires_ts TEXT NOT NULL)''',
    '''CREATE TABLE x_post_assets (
       post_id TEXT NOT NULL REFERENCES x_posts(post_id) ON DELETE CASCADE,
       coin_id TEXT NOT NULL, first_seen_ts TEXT NOT NULL,
       PRIMARY KEY(post_id,coin_id))''',
    'CREATE INDEX idx_x_asset_time ON x_post_assets(coin_id,first_seen_ts)',
    '''CREATE TABLE x_post_metrics (
       run_id TEXT NOT NULL REFERENCES attention_runs(id),
       post_id TEXT NOT NULL REFERENCES x_posts(post_id) ON DELETE CASCADE,
       observed_ts TEXT NOT NULL, likes INTEGER, reposts INTEGER, replies INTEGER,
       quotes INTEGER, PRIMARY KEY(run_id,post_id))''',
    '''CREATE TABLE provider_usage (
       id TEXT PRIMARY KEY, provider TEXT NOT NULL, billing_period TEXT NOT NULL,
       reserved_ts TEXT NOT NULL, requests INTEGER NOT NULL,
       resources_reserved INTEGER NOT NULL, resources INTEGER,
       reserved_microusd INTEGER NOT NULL, estimated_microusd INTEGER,
       currency TEXT NOT NULL, pricing_json TEXT NOT NULL, status TEXT NOT NULL)''',
    'CREATE INDEX idx_usage_period ON provider_usage(provider,billing_period,reserved_ts)',
]
