"""Validated, secret-free experimental settings. No production provider by default."""
import copy
import math

DEFAULTS = {
    "enabled": False,
    "max_market_age_seconds": 600,
    "social": {"enabled": False, "provider": "none", "baseline_hours": 24,
               "min_baseline_windows": 12, "min_coverage": 0.8,
               "max_duplicate_fraction": 0.5, "max_top_author_fraction": 0.5},
    "news": {"enabled": True, "assets_per_scan": 10, "poll_minutes": 30,
             "max_age_hours": 24, "novelty_hours": 6, "max_items": 8,
             "trusted_sources": []},
    "scoring": {"social_threshold": 40, "news_threshold": 35,
                "candidate_threshold": 35},
    "ai": {"enabled": False, "max_candidates_per_scan": 2, "daily_call_budget": 10},
    "alerts": {"enabled": False},
    "market_regime": {"broad_enabled": True, "min_broad_assets": 10},
    "significant_move": {"threshold_pct": 5.0, "window_hours": 24,
                         "lookback_hours": 1, "anchor_tolerance_seconds": 600},
    "episodes": {"quiet_hours": 24},
}


def normalize(raw):
    def merge(default, supplied, path):
        if not isinstance(supplied, dict) or set(supplied) - set(default):
            raise ValueError(f"Unknown or invalid configuration: {path}")
        result = copy.deepcopy(default)
        for key, value in supplied.items():
            template = default[key]
            if isinstance(template, dict):
                result[key] = merge(template, value, path + '.' + key)
                continue
            valid = type(value) is type(template)
            if type(template) is float:
                valid = type(value) in (float, int)
            if isinstance(template, (float, int)) and not isinstance(template, bool):
                valid = valid and math.isfinite(value) and value >= 0
            if isinstance(template, list):
                valid = valid and all(isinstance(v, str) for v in value)
            if not valid:
                raise ValueError(f"Invalid configuration: {path}.{key}")
            result[key] = value
        return result
    cfg = merge(DEFAULTS, raw, 'v12')
    if cfg['social']['provider'] != 'none':
        raise ValueError('No production social adapter installed; use provider: none')
    for key in ('min_coverage', 'max_duplicate_fraction', 'max_top_author_fraction'):
        if not 0 < cfg['social'][key] <= 1:
            raise ValueError('Social fractions must be in (0,1]')
    for key in ('baseline_hours', 'min_baseline_windows'):
        if cfg['social'][key] < 1:
            raise ValueError('Social baseline must be positive')
    for value in cfg['scoring'].values():
        if not 0 < value <= 100:
            raise ValueError('Experimental score thresholds must be in (0,100]')
    if any(cfg[s][k] <= 0 for s, k in (
        ('news','poll_minutes'), ('news','max_age_hours'), ('news','novelty_hours'),
        ('significant_move','threshold_pct'), ('significant_move','window_hours'),
        ('significant_move','lookback_hours'), ('episodes','quiet_hours'))):
        raise ValueError('Experimental time windows and move threshold must be positive')
    if cfg['max_market_age_seconds'] <= 0 or cfg['news']['max_items'] < 1 or cfg['market_regime']['min_broad_assets'] < 1:
        raise ValueError('News and benchmark counts must be positive')
    return cfg
