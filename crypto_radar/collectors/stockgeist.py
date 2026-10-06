"""StockGeist historical five-minute message metrics, normalized at the boundary."""
import os
from datetime import datetime, timedelta, timezone
import requests
from .social import SocialObservation
from .social_providers import ProviderError

URL='https://api.stockgeist.ai/crypto/global/hist/message-metrics'
METRICS=('total_count','pos_total_count','neg_total_count','neu_total_count',
         'pos_em_count','pos_nem_count','neg_em_count','neg_nem_count',
         'neu_em_count','neu_nem_count','em_total_count','nem_total_count')


def timestamp(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None: raise ValueError('Provider timestamps must include timezone')
    return value.astimezone(timezone.utc)


class StockGeistCollector:
    def __init__(self,settings,session=None):
        self.settings=settings
        self.session=session or requests

    def collect(self,assets,now):
        token=os.getenv(self.settings.token_env,'').strip()
        if not token: raise ProviderError('missing_credentials')
        mapping={a['id']:self.settings.asset_map[a['id']] for a in assets if a['id'] in self.settings.asset_map}
        if not mapping: raise ProviderError('unmapped')
        end=now-timedelta(seconds=self.settings.finalization_lag_seconds)
        end=end.replace(second=0,microsecond=0)-timedelta(minutes=end.minute%5)
        start=end-timedelta(hours=self.settings.history_hours)
        params=dict(symbols=','.join(sorted(mapping.values())),start=start.strftime('%Y-%m-%dT%H:%M:%S'),
                    end=end.strftime('%Y-%m-%dT%H:%M:%S'),timeframe='5m',metrics=','.join(METRICS))
        if self.settings.sources: params['sources']=','.join(sorted(self.settings.sources))
        response=self.session.get(URL,params=params,headers={'token':token},timeout=self.settings.timeout_seconds,
                                  allow_redirects=False)
        if response.status_code==429:
            try: retry=max(60,min(86400,int(response.headers.get('Retry-After',self.settings.retry_seconds))))
            except ValueError: retry=self.settings.retry_seconds
            raise ProviderError('rate_limited',retry)
        if response.status_code in (401,403): raise ProviderError('unauthorized')
        if response.status_code!=200: raise ProviderError('failed')
        if len(response.content)>8_000_000: raise ProviderError('malformed')
        try:
            return self.normalize(response.json(),mapping,assets,start,end)
        except (ValueError,KeyError,TypeError,AttributeError):
            raise ProviderError('malformed') from None

    def normalize(self,payload,mapping,assets,start,end):
        server=timestamp(payload['server_timestamp'])
        data=payload['data']
        if not isinstance(data,dict): raise ValueError('Expected symbol series')
        reverse={v:k for k,v in mapping.items()}
        symbols={a['id']:a.get('symbol') for a in assets}
        result=[]
        for provider_asset,points in data.items():
            if provider_asset not in reverse or not isinstance(points,list): raise ValueError('Unexpected symbol')
            for point in points:
                if point.get('symbol',provider_asset)!=provider_asset or point.get('asset_class','crypto')!='crypto':
                    raise ValueError('Mismatched symbol/asset class')
                bucket=timestamp(point['timestamp'])
                finish=bucket+timedelta(minutes=5) if self.settings.timestamp_convention=='start' else bucket
                begin=finish-timedelta(minutes=5)
                if begin<start or finish>end: continue  # no unfinished intervals
                native={k:point[k] for k in METRICS if k in point}
                if any(v is not None and (type(v) is not int or v<0) for v in native.values()):
                    raise ValueError('Invalid counts')
                positive=native.get('pos_total_count'); negative=native.get('neg_total_count'); neutral=native.get('neu_total_count')
                total=native.get('total_count')
                if total is not None and any(v is not None and v>total for v in (positive,negative,neutral)):
                    raise ValueError('Category count exceeds total')
                if total is not None and all(v is not None for v in (positive,negative,neutral)) and positive+negative+neutral!=total:
                    raise ValueError('Inconsistent counts')
                sources=point.get('sources',self.settings.sources)
                if not isinstance(sources,list) or any(s not in ('reddit','twitter','stocktwits') for s in sources):
                    raise ValueError('Unknown source coverage')
                # Different coverage must never share a baseline. These are already
                # aggregated provider counts, not three independent community rows.
                source='messages:'+','.join(sorted(set(sources))) if sources else 'messages:unspecified'
                result.append(SocialObservation(provider='stockgeist',provider_asset_id=provider_asset,
                    coin_id=reverse[provider_asset],symbol=symbols[reverse[provider_asset]],source=source,
                    window_start=begin,window_end=finish,provider_timestamp=server,
                    mentions=total,positive=positive,negative=negative,neutral=neutral,
                    sentiment=(positive-negative)/total if total and positive is not None and negative is not None else None,
                    native=native,metadata={'sources':sources,'timestamp_convention':self.settings.timestamp_convention,
                                          'normalization_version':'stockgeist-1','resolution':'5m'}))
        return result
