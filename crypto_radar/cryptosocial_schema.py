"""Schema v4: additive, minimal first-party source item provenance."""
STATEMENTS = [
    '''CREATE TABLE social_source_items (
        provider TEXT NOT NULL, source TEXT NOT NULL, item_id TEXT NOT NULL,
        event_ts TEXT NOT NULL, receipt_ts TEXT NOT NULL, community TEXT NOT NULL,
        assets_json TEXT NOT NULL, engagement REAL, normalization_version TEXT NOT NULL,
        evidence_kind TEXT NOT NULL,
        PRIMARY KEY(provider,source,item_id,normalization_version))''',
    'CREATE INDEX idx_source_items_receipt ON social_source_items(provider,source,receipt_ts)',
    'CREATE INDEX idx_source_items_event ON social_source_items(provider,source,event_ts)',
]
