"""Asset-relative social acceleration with explicit coverage and missingness."""
from datetime import datetime, timedelta, timezone
from statistics import mean
import math
import json


def social_features(rows, now, cfg):
    groups={}
    for row in rows:
        row=dict(row)
        groups.setdefault(row.get('provider','legacy'),[]).append(row)
    if not groups or set(groups)=={'legacy'}:
        result=_provider_features(groups.get('legacy',[]),now,cfg)
        result.update(selected_provider='legacy' if groups else None,providers={})
        if groups: result['providers']['legacy']=dict(result,providers={})
        return result
    features={}
    for provider,items in sorted(groups.items()):
        if provider=='legacy':
            features[provider]=_provider_features(items,now,cfg); continue
        eligible=[r for r in items if datetime.fromisoformat(r['observed_ts'])<=now
                  and datetime.fromisoformat(r['window_end'])<=now and r.get('availability')!='unavailable']
        latest=max((datetime.fromisoformat(r['window_end']) for r in eligible),default=None)
        # Provider asset mapping and source coverage are part of the series identity.
        def series(row):
            metadata=json.loads(row['metadata_json']) if 'metadata_json' in row else row.get('metadata',{})
            return json.dumps([row.get('provider_asset_id'),row['source'],metadata.get('normalization_version'),
                               metadata.get('timestamp_convention')])
        eligible=[dict(r,source=series(r)) for r in eligible]
        features[provider]=_provider_features(eligible,now,cfg,end=latest)
        features[provider]['baseline_version']='provider_relative_v1'
        max_age=cfg.get('provider_options',{}).get(provider,{}).get('max_age_seconds',900)
        if latest and (now-latest).total_seconds()>max_age:
            features[provider].update(status='stale',score=0.0)
        features[provider]['freshness_seconds']=(now-latest).total_seconds() if latest else None
    # Select one provider's evidence; never sum incompatible absolute counts or
    # average provider baselines. Preserve all features for later comparisons.
    selected=max(features,key=lambda name:(features[name]['score'],name))
    result=dict(features[selected],selected_provider=selected,providers=features,
                provider_selection='max_provider_score_v1')
    return result


def _provider_features(rows, now, cfg, end=None):
    end = end or datetime.fromtimestamp(math.floor(now.timestamp()/300)*300, timezone.utc)
    start = end-timedelta(hours=1)
    rows = [dict(r) for r in rows if datetime.fromisoformat(r['observed_ts']) <= now
            and datetime.fromisoformat(r['window_end']) <= end
            and datetime.fromisoformat(r['window_end']) > end-timedelta(hours=cfg['baseline_hours']+1)]
    recent = [r for r in rows if datetime.fromisoformat(r['window_end']) > start]
    sources = {r['source'] for r in recent}
    by_time = {}
    for r in rows:
        by_time.setdefault(datetime.fromisoformat(r['window_end']), {})[r['source']] = r
    def bucket(t):
        found = by_time.get(t, {})
        return [found[s] for s in sorted(sources)] if sources and sources <= found.keys() else None
    def count(n):
        buckets = [bucket(end-timedelta(minutes=5*i)) for i in range(n)]
        return sum(r['mentions'] for b in buckets for r in b) if all(buckets) and all(r['mentions'] is not None for b in buckets for r in b) else None
    baseline = [bucket(t) for t in sorted(by_time) if end-timedelta(hours=cfg['baseline_hours']+1) < t <= start]
    baseline = [b for b in baseline if b]
    result = dict(status='insufficient_history', mentions_5m=count(1), mentions_15m=count(3),
        mentions_1h=count(12), baseline_windows=len(baseline), mention_acceleration=None,
        author_acceleration=None, engagement_acceleration=None, sentiment_change=None,
        source_count=len({r['source'] for r in recent if r['mentions'] is not None and r['mentions'] > 0}),
        duplicate_fraction=None, top_author_fraction=None, score=0.0,
        window_end=end.isoformat(), sources=sorted(sources), baseline_version='1.2')
    result['observation_ids']=[r['id'] for r in rows if 'id' in r]
    for key in ('duplicate_fraction','top_author_fraction'):
        values = [r[key] for r in recent if r[key] is not None]
        result[key] = max(values) if values else None
    if not rows:
        result['status'] = 'unavailable'
        return result
    coverage = len(baseline)/(cfg['baseline_hours']*12)
    result['baseline_coverage'] = coverage
    # A complete latest 15m window is mandatory; gaps never mean measured zero.
    if len(baseline) < cfg['min_baseline_windows'] or coverage < cfg['min_coverage'] or count(3) is None:
        return result
    current = [r for i in range(3) for r in bucket(end-timedelta(minutes=5*i))]
    def ratio(key):
        if any(r[key] is None for r in current) or any(r[key] is None for b in baseline for r in b):
            return None
        base = mean(sum(r[key] for r in b) for b in baseline)
        # Zero baseline is not enough evidence to estimate a meaningful ratio.
        return (sum(r[key] for r in current)/3)/base if base > 0 else None
    result['mention_acceleration'] = ratio('mentions')
    result['author_acceleration'] = ratio('unique_authors')
    result['engagement_acceleration'] = ratio('engagement')
    if all(r['sentiment'] is not None for r in current) and all(r['sentiment'] is not None for b in baseline for r in b):
        result['sentiment_change'] = mean(r['sentiment'] for r in current)-mean(r['sentiment'] for b in baseline for r in b)
    if result['mention_acceleration'] is None:
        result['status'] = 'missing_metrics' if any(r['mentions'] is None for b in baseline for r in b) else 'zero_baseline'
        return result
    result['status'] = 'ok'
    components = {}
    for feature, weight in (('mention_acceleration',60),('author_acceleration',25),('engagement_acceleration',15)):
        value = result[feature]
        components[feature] = min(weight, max(0, (value-1)*weight/3)) if value is not None else 0
    result['components'] = components
    result['score'] = round(sum(components.values()),2)
    if any(result[key] is not None and result[key] > cfg['max_'+key]
           for key in ('duplicate_fraction','top_author_fraction')):
        result['status'] = 'suspected_promotion'
        result['score'] = 0.0
    return result
