"""First-party evidence adapter. No Reddit networking until approved access exists.

Sources supply finalized, explicitly covered buckets, not an incomplete listing
that could be mistaken for measured zero. Injection is for synthetic tests only.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .social import SocialObservation, SourceEvidence
from .social_providers import ProviderSettings, ProviderError


class CryptoSocialSettings(ProviderSettings):
    token_env: str = 'REDDIT_ACCESS_TOKEN'
    sources: list[Literal['reddit']] = Field(default_factory=lambda: ['reddit'])
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    communities: list[str] = Field(default_factory=list, max_length=10)
    mapping_version: str = Field(default='aliases-v1', pattern=r'^[a-zA-Z0-9_.-]{1,50}$')
    observation_only: Literal[True] = True
    max_items: int = Field(default=1000, ge=1, le=1000)

    @model_validator(mode='after')
    def validate_aliases(self):
        if len(self.sources)!=len(set(self.sources)):
            raise ValueError('Duplicate source')
        if set(self.aliases) != set(self.asset_map):
            raise ValueError('Each configured canonical asset requires explicit aliases')
        for aliases in self.aliases.values():
            if not 1 <= len(aliases) <= 20 or any(not re.fullmatch(r'\$?[A-Za-z0-9][A-Za-z0-9 -]{0,49}', a) for a in aliases):
                raise ValueError('Bounded token aliases required')
            if any(a.lower() in {'one','link','near','arb'} for a in aliases):
                raise ValueError('Ambiguous bare alias: use a cashtag or full asset name')
        owners={}
        for asset, aliases in self.aliases.items():
            for alias in aliases:
                if alias.lower() in owners and owners[alias.lower()]!=asset:
                    raise ValueError('Alias shared by different assets')
                owners[alias.lower()]=asset
        if len(set(c.lower() for c in self.communities)) != len(self.communities) or any(not re.fullmatch(r'[A-Za-z0-9_]{1,50}', c) for c in self.communities):
            raise ValueError('Explicit unique community names required')
        return self


@dataclass(frozen=True)
class SourceItem:
    item_id: str
    event_time: datetime
    community: str
    text: str
    author: str | None = None  # transient source-local identifier; never persisted
    score: int | None = None
    comments: int | None = None


@dataclass
class SourceResult:
    items: list[SourceItem] = field(default_factory=list)
    # Inclusive start, exclusive end; explicit completed UTC bucket coverage.
    windows: list[datetime] = field(default_factory=list)
    complete: bool = True


class FirstPartySource(Protocol):
    def collect(self, communities: list[str], start: datetime, end: datetime) -> SourceResult: ...


class RedditSource:
    """Blocked transport boundary, not a scraper or production API adapter."""
    def collect(self, communities, start, end):
        raise ProviderError('approval_required')


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware event time required')
    return value.astimezone(timezone.utc)


def bucket_start(value):
    value = utc(value)
    return datetime.fromtimestamp(int(value.timestamp()) // 300 * 300, timezone.utc)


class AssetResolver:
    def __init__(self, settings):
        self.patterns = {asset: [re.compile(r'(?<![\w$])' + re.escape(alias) + r'(?!\w)', re.I)
                                for alias in aliases] for asset, aliases in settings.aliases.items()}

    def resolve(self, text):
        return sorted(asset for asset, patterns in self.patterns.items() if any(p.search(text) for p in patterns))


class CryptoSocialCollector:
    settings_model = CryptoSocialSettings

    def __init__(self, settings, sources=None):
        self.settings = settings
        self.sources = sources if sources is not None else {'reddit': RedditSource()}
        self.resolver = AssetResolver(settings)
        definition = json.dumps({'aliases':settings.aliases, 'communities':sorted(c.lower() for c in settings.communities),
                                 'mapping':settings.asset_map, 'version':settings.mapping_version}, sort_keys=True)
        self.version = 'cryptosocial-v1-' + hashlib.sha256(definition.encode()).hexdigest()[:16]

    def collect(self, assets, now):
        if not self.settings.sources:
            raise ProviderError('unavailable')
        mapping = {a['id']:a for a in assets if a['id'] in self.settings.asset_map}
        if not mapping:
            raise ProviderError('unmapped')
        if not self.settings.communities:
            raise ProviderError('unavailable')
        end = bucket_start(utc(now) - timedelta(seconds=self.settings.finalization_lag_seconds))
        start = end - timedelta(hours=self.settings.history_hours)
        rows = []
        for name in self.settings.sources:
            try:
                result = self.sources[name].collect(self.settings.communities, start, end)
                rows.extend(self.normalize(name, result, mapping, start, end, utc(now)))
            except ProviderError:
                raise
            except TimeoutError:
                raise ProviderError('failed') from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ProviderError('malformed') from None
        return rows

    def normalize(self, source, result, mapping, start, end, now):
        if not isinstance(result, SourceResult) or type(result.complete) is not bool:
            raise ValueError('Source contract required')
        if not result.complete:
            raise ProviderError('partial_error')  # never turn truncated listings into zeros
        if not isinstance(result.items,list) or len(result.items) > self.settings.max_items:
            raise ValueError('Bounded items required')
        if not isinstance(result.windows,list) or len(result.windows) > self.settings.history_hours*12:
            raise ValueError('Bounded coverage required')
        windows = {utc(w) for w in result.windows}
        if any(w != bucket_start(w) or not start <= w < end for w in windows):
            raise ValueError('Invalid finalized coverage')
        communities = {c.lower() for c in self.settings.communities}
        seen = {}; groups = {}
        for item in result.items:
            if not isinstance(item,SourceItem) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,100}',item.item_id):
                raise ValueError('Stable bounded item identifier required')
            event = utc(item.event_time)
            if event > now:
                raise ValueError('Future source evidence')
            if not isinstance(item.community,str) or item.community.lower() not in communities or not isinstance(item.text,str) or len(item.text)>40000:
                raise ValueError('Invalid community/content')
            if item.author is not None and (not isinstance(item.author,str) or not 1 <= len(item.author) <= 100):
                raise ValueError('Invalid transient author')
            if any(v is not None and type(v) is not int for v in (item.score,item.comments)) or (item.comments is not None and item.comments<0):
                raise ValueError('Invalid engagement')
            if item.item_id in seen:
                if seen[item.item_id] != item:
                    raise ValueError('Conflicting duplicate item')
                continue
            seen[item.item_id] = item
            w = bucket_start(event)
            if w < start: continue  # stale/out-of-horizon evidence is not current coverage
            if w >= end: continue  # not finalized
            if w not in windows: raise ValueError('Item lacks declared bucket coverage')
            if item.text in ('[deleted]','[removed]'): continue
            matched = self.resolver.resolve(item.text)
            engagement = float(max(0,item.score) + item.comments) if item.score is not None and item.comments is not None else None
            evidence = SourceEvidence(item_id=item.item_id, event_timestamp=event,
                community=item.community.lower(), assets=matched, engagement=engagement)
            for asset in matched:
                groups.setdefault((asset,w),[]).append((item,evidence))
        rows = []
        for asset, coin in mapping.items():
            for w in sorted(windows):
                items = groups.get((asset,w),[])
                authors = [i.author for i,e in items]
                measured_authors = all(a is not None and a != '[deleted]' for a in authors)
                engagements = [e.engagement for i,e in items]
                measured_engagement = all(e is not None for e in engagements)
                rows.append(SocialObservation(provider='cryptosocial',provider_asset_id=self.settings.asset_map[asset],
                    coin_id=asset,symbol=coin.get('symbol'),source=source,window_start=w,window_end=w+timedelta(minutes=5),
                    provider_timestamp=max((e.event_timestamp for i,e in items),default=None),
                    mentions=len(items),unique_authors=len(set(authors)) if measured_authors else None,
                    engagement=float(sum(engagements)) if measured_engagement else None,
                    evidence=[e for i,e in items],
                    metadata={'normalization_version':self.version,'mapping_version':self.settings.mapping_version,
                              'engagement_definition':'reddit_nonnegative_score_plus_comments_v1',
                              'communities':sorted(communities),'resolution':'5m','timestamp_convention':'start',
                              'observation_only':True,'coverage':'source_declared_complete',
                              'evidence_kind':'synthetic_offline_only'}))
        return rows
