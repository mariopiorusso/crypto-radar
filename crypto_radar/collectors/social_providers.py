"""Provider registry and isolated collection. No provider payload enters research."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .social import SocialObservation


class ProviderSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    token_env: str = Field(default='STOCKGEIST_API_TOKEN', pattern=r'^[A-Z][A-Z0-9_]*$')
    asset_map: dict[str, str] = Field(default_factory=dict)
    poll_seconds: int = Field(default=300, ge=60)
    retry_seconds: int = Field(default=900, ge=60, le=86400)
    max_age_seconds: int = Field(default=900, ge=300)
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    history_hours: int = Field(default=25, ge=1, le=168)
    finalization_lag_seconds: int = Field(default=300, ge=0, le=86400)
    timestamp_convention: Literal['start','end'] = 'start'
    sources: list[Literal['reddit','twitter','stocktwits']] = Field(default_factory=list)

    @model_validator(mode='after')
    def mapping(self):
        import re
        if len(self.asset_map)>50 or len(set(self.asset_map.values()))!=len(self.asset_map):
            raise ValueError('At most 50 uniquely mapped provider symbols per provider')
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',k) or
               not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',v) for k,v in self.asset_map.items()):
            raise ValueError('Explicit canonical ID to provider-symbol mapping required')
        return self


class ProviderError(Exception):
    def __init__(self, status, retry_seconds=None):
        self.status=status
        self.retry_seconds=retry_seconds
        super().__init__(status)


@dataclass
class ProviderBatch:
    provider: str
    status: str
    observations: list = field(default_factory=list)
    error: str | None = None


def registry():
    from .stockgeist import StockGeistCollector
    from .cryptosocial import CryptoSocialCollector
    return {'stockgeist': StockGeistCollector, 'cryptosocial': CryptoSocialCollector}


def settings_for(factory, values):
    model=getattr(factory,'settings_model',ProviderSettings)
    if not isinstance(model,type) or not issubclass(model,ProviderSettings):
        model=ProviderSettings
    return model(**values)


class ProviderCollection:
    def __init__(self, collectors, settings):
        self.collectors=collectors
        self.settings=settings

    def scoring_providers(self):
        return {name for name, options in self.settings.items()
                if options.enabled and not getattr(options,'observation_only',False)}

    @classmethod
    def from_config(cls,cfg):
        factories=registry()
        settings={name:settings_for(factories[name],cfg.get('provider_options',{}).get(name,{}))
                  for name in cfg.get('providers',[])}
        return cls({name:factories[name](setting) for name,setting in settings.items()},settings)

    def batches(self,assets,now,clock,history=None):
        history=history or {}
        for name,collector in self.collectors.items():
            settings=self.settings[name]
            if not settings.enabled:
                yield ProviderBatch(name,'disabled'); continue
            previous=history.get(name)
            if previous:
                timestamp,previous_status,error=previous
                delay=settings.poll_seconds
                if previous_status in ('rate_limited','failed','malformed','unauthorized','partial_error','approval_required'):
                    delay=settings.retry_seconds
                    if error and error.startswith('retry_after:'):
                        delay=max(delay,min(86400,int(error.split(':')[1])))
                if now < datetime.fromisoformat(timestamp)+timedelta(seconds=delay):
                    yield ProviderBatch(name,'cooldown'); continue
            try:
                rows=collector.collect(assets,now)
                if not isinstance(rows,list) or len(rows)>100000:
                    raise ValueError('Bounded observation list required')
                receipt=clock()
                valid=[]
                for row in rows:
                    item=SocialObservation.model_validate(row)
                    if item.provider!=name or not item.provider_asset_id or item.coin_id not in {a['id'] for a in assets}:
                        raise ValueError('Provider identity or asset mapping mismatch')
                    if item.window_end>now or (item.provider_timestamp and item.provider_timestamp>receipt):
                        raise ValueError('Future provider evidence')
                    if any(e.event_timestamp > receipt or not item.window_start <= e.event_timestamp < item.window_end
                           or item.coin_id not in e.assets for e in item.evidence):
                        raise ValueError('Invalid source evidence chronology or mapping')
                    # Receipt is measured locally, never accepted from a provider.
                    item.receipt_timestamp=receipt
                    if (receipt-item.window_end).total_seconds()>settings.max_age_seconds:
                        item.availability='stale'
                    valid.append(item)
                state='available' if valid else 'unavailable'
                if valid and all(r.availability=='stale' for r in valid): state='stale'
                yield ProviderBatch(name,state,valid)
            except ProviderError as exc:
                yield ProviderBatch(name,exc.status,error=f'retry_after:{exc.retry_seconds}' if exc.retry_seconds else exc.status)
            except Exception as exc:
                # Never persist exception text: HTTP errors can include credentials.
                yield ProviderBatch(name,'failed',error=type(exc).__name__)
