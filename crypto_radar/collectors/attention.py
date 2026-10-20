"""Native attention evidence. Never coerce rolling totals into interval counts."""
import hashlib
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING

import requests
from pydantic import Field, model_validator

from .social_providers import ProviderSettings, ProviderError


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Aware timestamp required')
    return result.astimezone(timezone.utc)


def integer(value):
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r'\d+', value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= 2**63-1:
        raise ValueError('Invalid provider count')
    return value


def resolve(assets, mapping):
    """Use the existing canonical-ID -> provider-symbol contract; no fuzzy matching."""
    ids = {a['id'] for a in assets}
    return {symbol.upper(): coin for coin, symbol in mapping.items() if coin in ids}


class AttentionSettings(ProviderSettings):
    access_confirmed: bool = False
    max_pages: int = Field(default=3, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode='after')
    def unambiguous_symbols(self):
        if len({v.upper() for v in self.asset_map.values()})!=len(self.asset_map):
            raise ValueError('Ambiguous case-insensitive symbol mapping')
        return self


class ApeSettings(AttentionSettings):
    poll_seconds: int = Field(default=1800, ge=60)


class XSettings(AttentionSettings):
    token_env: str = Field(default='X_BEARER_TOKEN', pattern=r'^[A-Z][A-Z0-9_]*$')
    queries: dict[str, str] = Field(default_factory=dict)
    exclude_reposts: bool = True
    max_posts_per_poll: int = Field(default=100, ge=10, le=10000)
    max_posts_per_day: int = Field(default=1000, ge=10)
    lookback_seconds: int = Field(default=900, ge=60, le=604740)
    overlap_seconds: int = Field(default=60, ge=0, le=3600)
    retention_hours: int = Field(default=24, ge=1, le=168)
    pricing_confirmed: bool = False
    request_usd: float | None = Field(default=None, ge=0)
    post_usd: float | None = Field(default=None, ge=0)
    monthly_limit_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def pricing(self):
        for value in (self.request_usd, self.post_usd, self.monthly_limit_usd):
            if value is not None and not math.isfinite(value):
                raise ValueError('Finite pricing required')
        if len(self.queries) > 50 or any(not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', k)
                or not 1 <= len(v) <= 450 for k, v in self.queries.items()):
            raise ValueError('Bounded canonical asset queries required')
        return self


def micro(value):
    return int((Decimal(str(value))*1000000).to_integral_value(rounding=ROUND_CEILING))


def reserve(conn, settings, now, quantity):
    """Commit a conservative reservation BEFORE network I/O, including retries.

    In-flight/uncertain reservations are never released automatically after restart.
    SQLite's writer lock serializes allowance checks across scanner processes.
    """
    if not settings.pricing_confirmed or settings.request_usd is None or settings.post_usd is None:
        raise ProviderError('pricing_unknown')
    price = micro(settings.request_usd) + quantity*micro(settings.post_usd)
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        spent = conn.execute('''SELECT coalesce(sum(coalesce(estimated_microusd,reserved_microusd)),0)
            FROM provider_usage WHERE provider='x' AND billing_period=?''', (now.strftime('%Y-%m'),)).fetchone()[0]
        daily = conn.execute('''SELECT coalesce(sum(coalesce(resources,resources_reserved)),0)
            FROM provider_usage WHERE provider='x' AND substr(reserved_ts,1,10)=?''', (now.date().isoformat(),)).fetchone()[0]
        if daily+quantity > settings.max_posts_per_day:
            raise ProviderError('daily_limit')
        if settings.monthly_limit_usd is not None and Decimal(spent+price)/1000000 > Decimal(str(settings.monthly_limit_usd)):
            raise ProviderError('budget_exhausted')
        key = uuid.uuid4().hex
        conn.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (key,'x',now.strftime('%Y-%m'),now.isoformat(),1,quantity,None,price,None,'USD',
             json.dumps({'request_usd':settings.request_usd,'post_usd':settings.post_usd}), 'reserved'))
        conn.commit()
        return key
    except BaseException:
        conn.rollback()
        raise


def finish_usage(conn, key, state, quantity=None, settings=None):
    cost = None if quantity is None else micro(settings.request_usd)+quantity*micro(settings.post_usd)
    conn.execute('UPDATE provider_usage SET status=?,resources=?,estimated_microusd=? WHERE id=?',
                 (state,quantity,cost,key))
    conn.commit()


class NativeCollector:
    native_evidence = True

    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or requests.Session()

    def get(self, conn, url, params, now, quantity=0):
        for attempt in range(self.settings.max_attempts):
            try:
                return self._get(conn,url,params,datetime.now(timezone.utc) if attempt else now,quantity)
            except (requests.Timeout,requests.ConnectionError,ProviderError) as exc:
                if isinstance(exc,ProviderError) and exc.status!='failed':
                    raise
                if attempt+1==self.settings.max_attempts:
                    raise
                time.sleep(min(8,2**attempt))

    def _get(self, conn, url, params, now, quantity=0):
        # Each paid retry has its own durable conservative reservation.
        headers = {}
        key = None
        if self.provider == 'x':
            token = os.getenv(self.settings.token_env)
            if not token:
                raise ProviderError('unauthorized')
            headers['Authorization'] = 'Bearer '+token
            key = reserve(conn,self.settings,now,quantity)
        try:
            response = self.session.get(url,params=params,headers=headers,
                timeout=self.settings.timeout_seconds,allow_redirects=False)
            if response.status_code == 429:
                retry = response.headers.get('Retry-After','')
                raise ProviderError('rate_limited', min(86400,int(retry)) if str(retry).isdigit() else self.settings.retry_seconds)
            if response.status_code in (401,403):
                raise ProviderError('unauthorized')
            if response.status_code != 200:
                raise ProviderError('failed')
            if len(response.content) > 8*1024*1024:
                raise ProviderError('malformed')
            payload = response.json()
            if not isinstance(payload,dict):
                raise ProviderError('malformed')
            if key:
                data = payload.get('data',[])
                if not isinstance(data,list) or len(data)>quantity or payload.get('errors'):
                    raise ProviderError('malformed')
                finish_usage(conn,key,'received',len(data),self.settings)
            return payload
        except BaseException:
            if key:
                # A timeout or failed response does not establish zero billing.
                finish_usage(conn,key,'uncertain')
            raise

    def run(self, conn, query, now, start=None, end=None):
        key = uuid.uuid4().hex
        conn.execute('INSERT INTO attention_runs(id,provider,query_key,observed_ts,window_start,window_end,status) VALUES (?,?,?,?,?,?,?)',
            (key,self.provider,query,now.isoformat(),start.isoformat() if start else None,
             end.isoformat() if end else None,'collecting'))
        conn.commit()
        return key


class ApeWisdomCollector(NativeCollector):
    provider = 'apewisdom'
    settings_model = ApeSettings

    def collect_persist(self, conn, assets, now, clock):
        if not self.settings.access_confirmed:
            raise ProviderError('access_unverified')
        mapping = resolve(assets,self.settings.asset_map)
        key = self.run(conn,'all-crypto',now)
        rows = {}; complete = False; pages = 0
        try:
            for page in range(1,self.settings.max_pages+1):
                payload = self.get(conn,'https://apewisdom.io/api/v1.0/filter/all-crypto/page/'+str(page),{},now)
                results = payload.get('results')
                total_pages = integer(payload.get('pages'))
                if not isinstance(results,list) or len(results)>100 or not total_pages or integer(payload.get('current_page')) != page:
                    raise ProviderError('malformed')
                for item in results:
                    ticker = item['ticker']
                    if not isinstance(ticker,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',ticker):
                        raise ProviderError('malformed')
                    ticker=ticker.upper()
                    values = [integer(item.get(k)) for k in ('mentions','upvotes','rank','mentions_24h_ago','rank_24h_ago')]
                    if ticker in rows:
                        raise ProviderError('malformed')
                    rows[ticker]=values
                pages=page
                if page>=total_pages:
                    complete=True; break
            receipt=clock()
            fingerprint=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
            previous=conn.execute("SELECT fingerprint FROM attention_runs WHERE provider='apewisdom' AND id!=? AND fingerprint IS NOT NULL ORDER BY observed_ts DESC LIMIT 1",(key,)).fetchone()
            state='repeated' if previous and previous[0]==fingerprint else 'available' if complete else 'partial'
            for ticker, values in rows.items():
                conn.execute('INSERT INTO apewisdom_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (key,ticker,mapping.get(ticker),receipt.isoformat(),None,*values))
            conn.execute('UPDATE attention_runs SET observed_ts=?,status=?,complete=?,pages=?,resources=?,fingerprint=? WHERE id=?',
                (receipt.isoformat(),state,int(complete),pages,len(rows),fingerprint,key))
            conn.commit()
            return state,len(rows)
        except Exception:
            conn.rollback()
            conn.execute("UPDATE attention_runs SET status='failed',pages=? WHERE id=?",(pages,key))
            conn.commit()
            raise


class XCollector(NativeCollector):
    provider = 'x'
    settings_model = XSettings

    def collect_persist(self, conn, assets, now, clock):
        if not self.settings.access_confirmed:
            raise ProviderError('access_unverified')
        s=self.settings
        # Data minimization: no text, author IDs, profiles or raw response storage.
        conn.execute('DELETE FROM x_posts WHERE expires_ts<=?',(now.isoformat(),))
        conn.commit()
        ids={a['id'] for a in assets}; remaining=s.max_posts_per_poll
        states=[]; count=0
        for coin, query in s.queries.items():
            if coin not in ids:
                continue
            end=now-timedelta(seconds=30)
            start=end-timedelta(seconds=s.lookback_seconds)
            query_hash=hashlib.sha256((query+'|exclude_reposts='+str(s.exclude_reposts)).encode()).hexdigest()
            last=conn.execute("SELECT window_end FROM attention_runs WHERE provider='x' AND query_key=? AND query_hash=? AND complete=1 ORDER BY window_end DESC LIMIT 1",(coin,query_hash)).fetchone()
            if last:
                start=max(start,stamp(last[0])-timedelta(seconds=s.overlap_seconds))
            if start>=end:
                continue
            key=self.run(conn,coin,clock(),start,end)
            gap=max(0,(start-stamp(last[0])).total_seconds()) if last else None
            conn.execute('UPDATE attention_runs SET query_hash=?,gap_seconds=? WHERE id=?',(query_hash,gap or 0,key))
            conn.commit()
            token=None; pages=0; observed=0; complete=False
            try:
                for _ in range(s.max_pages):
                    if remaining<10:
                        break
                    cap=min(100,remaining)
                    params={'query':'('+query+')'+(' -is:retweet' if s.exclude_reposts else ''),
                        'start_time':start.isoformat(),'end_time':end.isoformat(),
                        'max_results':cap,'post.fields':'id,created_at,public_metrics'}
                    if token:
                        params['next_token']=token
                    payload=self.get(conn,'https://api.x.com/2/tweets/search/recent',params,clock(),cap)
                    receipt=clock(); data=payload.get('data',[])
                    parsed=[]
                    for item in data:
                        post_id=item['id']; event=stamp(item['created_at'])
                        if not isinstance(post_id,str) or not re.fullmatch(r'\d{1,30}',post_id) or not start<=event<end or event>receipt:
                            raise ProviderError('malformed')
                        metrics=item.get('public_metrics',{})
                        values=[integer(metrics.get(k)) for k in ('like_count','retweet_count','reply_count','quote_count')]
                        parsed.append((post_id,event,values))
                    for post_id,event,values in parsed:
                        conn.execute('INSERT OR IGNORE INTO x_posts VALUES (?,?,?,?)',
                            (post_id,event.isoformat(),receipt.isoformat(),(receipt+timedelta(hours=s.retention_hours)).isoformat()))
                        conn.execute('INSERT OR IGNORE INTO x_post_assets VALUES (?,?,?)',(post_id,coin,receipt.isoformat()))
                        conn.execute('INSERT OR IGNORE INTO x_post_metrics VALUES (?,?,?,?,?,?,?)',(key,post_id,receipt.isoformat(),*values))
                    observed+=len(data); remaining-=len(data); pages+=1
                    meta=payload.get('meta')
                    if not isinstance(meta,dict) or 'result_count' not in meta or integer(meta['result_count'])!=len(data):
                        raise ProviderError('malformed')
                    next_token=meta.get('next_token')
                    if next_token is not None and (not isinstance(next_token,str) or not 1<=len(next_token)<=4096 or next_token==token):
                        raise ProviderError('malformed')
                    token=next_token
                    conn.execute('UPDATE attention_runs SET pages=?,resources=?,observed_ts=? WHERE id=?',(pages,observed,receipt.isoformat(),key))
                    conn.commit()
                    if not token:
                        complete=True; break
                state='available' if complete else 'partial'
                conn.execute('UPDATE attention_runs SET status=?,complete=? WHERE id=?',(state,int(complete),key))
                conn.commit(); states.append(state); count+=observed
            except Exception:
                conn.rollback()
                conn.execute("UPDATE attention_runs SET status='failed',pages=?,resources=? WHERE id=?",(pages,observed,key))
                conn.commit()
                raise
        return ('partial' if 'partial' in states else 'available' if states else 'unavailable'),count
