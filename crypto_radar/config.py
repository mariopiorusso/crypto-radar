import copy
import hashlib
import json
from pathlib import Path

import yaml

from .filters import DEFAULT_STABLECOINS

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "scan_interval_seconds": 300, "coin_count": 100, "currency": "usd",
    "filters": {"min_market_cap_usd": 50000000, "min_24h_volume_usd": 5000000,
                "max_abs_24h_change_pct": 35, "min_history_points": 6,
                "anomaly_threshold": 2.0, "stablecoin_ids": DEFAULT_STABLECOINS},
    "ai": {"enabled": True, "min_pre_score": 45, "alert_score": 80,
           "max_news_items": 8, "max_candidates_per_scan": 8, "daily_call_budget": 50,
           "cooldown_minutes": 60, "material_score_increase": 10,
           "timeout_seconds": 60, "max_output_tokens": 2000},
    "alerts": {"telegram_enabled": True, "email_enabled": False,
               "material_score_increase": 10, "episode_reset_hours": 24},
    "outcomes": {"tolerance_seconds": 600},
    "database": {"path": "data/crypto_radar.db"},
    "logging": {"path": "logs/crypto_radar.log", "max_bytes": 5000000, "backup_count": 3},
}


def normalize(raw):
    cfg = copy.deepcopy(DEFAULTS)
    for key, value in raw.items():
        if key not in cfg:
            raise ValueError(f"Unknown configuration section: {key}")
        if isinstance(cfg[key], dict):
            for name, setting in value.items():
                if key == "filters" and name == "candidate_limit":
                    continue
                if name not in cfg[key]:
                    raise ValueError(f"Unknown configuration setting: {key}.{name}")
                cfg[key][name] = setting
        else:
            cfg[key] = value
    if "max_candidates_per_scan" not in raw.get("ai", {}):
        cfg["ai"]["max_candidates_per_scan"] = raw.get("filters", {}).get("candidate_limit", 8)
    for section, values in DEFAULTS.items():
        items = values.items() if isinstance(values, dict) else [(None, values)]
        for key, default in items:
            val = cfg[section][key] if key else cfg[section]
            if isinstance(default, bool):
                valid = type(val) is bool
            elif isinstance(default, (int, float)):
                import math
                valid = type(val) in (int, float) and math.isfinite(val) and val >= 0
                if isinstance(default, int):
                    valid = valid and type(val) is int
            elif isinstance(default, list):
                valid = isinstance(val, list) and all(isinstance(x, str) for x in val)
            else:
                valid = isinstance(val, str) and bool(val)
            if not valid:
                raise ValueError(f"Invalid configuration: {section}.{key}")
    if cfg["scan_interval_seconds"] <= 0 or not 1 <= cfg["coin_count"] <= 250:
        raise ValueError("Scan interval must be positive; coin_count must be 1..250")
    if cfg["ai"]["timeout_seconds"] <= 0 or cfg["ai"]["max_output_tokens"] <= 0:
        raise ValueError("AI timeout and token limit must be positive")
    if cfg["filters"]["min_history_points"] < 2:
        raise ValueError("At least two history points are required")
    if cfg["currency"] != "usd":
        raise ValueError("V1.1 market-cap and volume thresholds require currency: usd")
    for section in ("ai", "alerts"):
        if not 0 < cfg[section]["material_score_increase"] <= 100:
            raise ValueError("Material score increase must be greater than zero and at most 100")
    if cfg["alerts"]["episode_reset_hours"] <= 0:
        raise ValueError("Episode reset period must be positive")
    if not all(0 <= cfg["ai"][key] <= 100 for key in ("min_pre_score", "alert_score")):
        raise ValueError("Score thresholds must be 0..100")
    if cfg["logging"]["max_bytes"] <= 0 or cfg["logging"]["backup_count"] < 1:
        raise ValueError("Log rotation requires a positive size and backup count")
    return cfg


def load_config(path=None):
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return normalize(yaml.safe_load(f) or {})


def snapshot(cfg):
    # Only the explicitly supported configuration is persisted; never environment variables.
    text = json.dumps(normalize(cfg), sort_keys=True, allow_nan=False)
    return text, hashlib.sha256(text.encode()).hexdigest()
