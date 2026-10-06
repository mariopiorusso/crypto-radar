"""Schema v3: additive provider evidence, no rewrites of historical observations."""
STATEMENTS = [
    '''CREATE TABLE provider_social_observations (
       id INTEGER PRIMARY KEY, provider TEXT NOT NULL, provider_asset_id TEXT NOT NULL,
       coin_id TEXT NOT NULL, symbol TEXT, source TEXT NOT NULL,
       window_start TEXT NOT NULL, window_end TEXT NOT NULL, observed_ts TEXT NOT NULL,
       provider_ts TEXT, receipt_ts TEXT NOT NULL, availability TEXT NOT NULL,
       mentions INTEGER, unique_authors INTEGER, engagement REAL, sentiment REAL,
       positive INTEGER, negative INTEGER, neutral INTEGER,
       duplicate_fraction REAL, top_author_fraction REAL,
       metadata_json TEXT NOT NULL, native_json TEXT NOT NULL,
       UNIQUE(provider,provider_asset_id,coin_id,source,window_start,window_end))''',
    'CREATE INDEX idx_provider_social_time ON provider_social_observations(coin_id,provider,window_end)',
]
