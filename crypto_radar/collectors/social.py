"""Provider contract: non-overlapping, completed five-minute count buckets.

Counts are interval counts, never rolling cumulative totals. Missing metrics stay
None; zero means measured zero. Providers must map assets to CoinGecko IDs.
"""
from datetime import datetime, timezone
from typing import Protocol
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SocialObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)
    coin_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    window_start: datetime
    window_end: datetime
    mentions: int = Field(ge=0)
    unique_authors: int | None = Field(default=None, ge=0)
    engagement: float | None = Field(default=None, ge=0)
    sentiment: float | None = Field(default=None, ge=-1, le=1)
    duplicate_fraction: float | None = Field(default=None, ge=0, le=1)
    top_author_fraction: float | None = Field(default=None, ge=0, le=1)
    # Only provider-specific non-secret evidence belongs here.
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def bucket(self):
        if any(t.tzinfo is None or t.utcoffset() is None for t in (self.window_start,self.window_end)):
            raise ValueError('UTC-aware timestamps required')
        self.window_start = self.window_start.astimezone(timezone.utc)
        self.window_end = self.window_end.astimezone(timezone.utc)
        if ((self.window_end-self.window_start).total_seconds() != 300
                or self.window_start.timestamp() % 300 != 0):
            raise ValueError('Aligned five-minute buckets required')
        if self.unique_authors is not None and self.unique_authors > self.mentions:
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
    return UnavailableSocialCollector()
