"""Provider contract: non-overlapping, completed five-minute count buckets.

Counts are interval counts, never rolling cumulative totals. Missing metrics stay
None; zero means measured zero. Providers must map assets to CoinGecko IDs.
"""
from datetime import datetime, timezone
from typing import Protocol, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceEvidence(BaseModel):
    """Minimum item provenance; no text, author, URLs, or source payload."""
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)
    item_id: str = Field(pattern=r'^[A-Za-z0-9_:-]{1,100}$')
    event_timestamp: datetime
    community: str = Field(pattern=r'^[A-Za-z0-9_]{1,50}$')
    assets: list[str] = Field(max_length=50)
    engagement: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def timestamp(self):
        if self.event_timestamp.tzinfo is None or self.event_timestamp.utcoffset() is None:
            raise ValueError('Aware event timestamp required')
        self.event_timestamp = self.event_timestamp.astimezone(timezone.utc)
        if any(not 1 <= len(a) <= 200 for a in self.assets):
            raise ValueError('Bounded canonical asset IDs required')
        return self


class SocialObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False, revalidate_instances='always')
    coin_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(default='legacy', pattern=r'^[a-z][a-z0-9_]{0,49}$')
    provider_asset_id: str | None = Field(default=None,min_length=1,max_length=200)
    symbol: str | None = Field(default=None,max_length=100)
    provider_timestamp: datetime | None = None
    receipt_timestamp: datetime | None = None
    availability: Literal['available','unavailable','stale'] = 'available'
    positive: int | None = Field(default=None, ge=0)
    negative: int | None = Field(default=None, ge=0)
    neutral: int | None = Field(default=None, ge=0)
    native: dict = Field(default_factory=dict)
    source: str = Field(min_length=1, max_length=100)
    window_start: datetime
    window_end: datetime
    mentions: int | None = Field(default=None, ge=0)
    unique_authors: int | None = Field(default=None, ge=0)
    engagement: float | None = Field(default=None, ge=0)
    sentiment: float | None = Field(default=None, ge=-1, le=1)
    duplicate_fraction: float | None = Field(default=None, ge=0, le=1)
    top_author_fraction: float | None = Field(default=None, ge=0, le=1)
    # Only provider-specific non-secret evidence belongs here.
    metadata: dict = Field(default_factory=dict)
    evidence: list[SourceEvidence] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def bucket(self):
        if any(t.tzinfo is None or t.utcoffset() is None for t in (self.window_start,self.window_end)):
            raise ValueError('UTC-aware timestamps required')
        self.window_start = self.window_start.astimezone(timezone.utc)
        self.window_end = self.window_end.astimezone(timezone.utc)
        if ((self.window_end-self.window_start).total_seconds() != 300
                or self.window_start.timestamp() % 300 != 0):
            raise ValueError('Aligned five-minute buckets required')
        for name in ('provider_timestamp','receipt_timestamp'):
            value = getattr(self,name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError('UTC-aware timestamps required')
                setattr(self,name,value.astimezone(timezone.utc))
        if self.unique_authors is not None and self.mentions is not None and self.unique_authors > self.mentions:
            raise ValueError('Unique authors cannot exceed mentions')
        return self


class SocialCollector(Protocol):
    def collect(self, assets: list[dict], now: datetime) -> list[SocialObservation]: ...


class UnavailableSocialCollector:
    def collect(self, assets, now):
        return []


class FixtureSocialCollector:
    """Injection-only deterministic provider for offline tests, never config-selectable."""
    def __init__(self, observations):
        self.observations = observations

    def collect(self, assets, now):
        ids = {a['id'] for a in assets}
        return [r for r in self.observations if r.coin_id in ids and r.window_end <= now]


def make_collector(cfg):
    from .social_providers import ProviderCollection
    return ProviderCollection.from_config(cfg)
