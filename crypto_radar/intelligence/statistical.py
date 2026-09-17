from statistics import mean, pstdev
from math import isfinite

SCORING_VERSION = "1.1"

def _safe(v, default=0.0):
    try:
        value = float(v) if v is not None else default
        return value if isfinite(value) else default
    except (TypeError, ValueError):
        return default

def score_candidate(row, hist, cfg):
    for key in ("market_cap", "total_volume", "price_change_percentage_24h", "current_price"):
        if not isfinite(_safe(row.get(key), float("nan"))):
            return None
    if _safe(row.get("current_price")) <= 0:
        return None
    market_cap = _safe(row.get("market_cap"))
    volume = _safe(row.get("total_volume"))
    ch24 = _safe(row.get("price_change_percentage_24h"))

    if market_cap < cfg["min_market_cap_usd"] or volume < cfg["min_24h_volume_usd"]:
        return None
    if abs(ch24) > cfg["max_abs_24h_change_pct"]:
        return None

    vm = volume / market_cap if market_cap else 0
    score = min(30, vm * 100)
    components = {"volume_marketcap": score, "volume_z": 0, "volume_ratio": 0}

    vols = [_safe(h["total_volume"]) for h in hist if h["total_volume"]]
    z = 0.0
    ratio = 1.0
    mu = sd = None
    if len(vols) >= cfg["min_history_points"]:
        mu = mean(vols)
        sd = pstdev(vols)
        ratio = volume / mu if mu else 1.0
        z = (volume - mu) / sd if sd > 0 else 0.0
        if z >= cfg["anomaly_threshold"]:
            components["volume_z"] = min(35, z * 6)
            score += components["volume_z"]
        if ratio >= 1.25:
            components["volume_ratio"] = min(20, (ratio - 1) * 20)
            score += components["volume_ratio"]

    # Prefer early signals; penalize assets already moving hard.
    components["early_move"] = max(0, 15 - abs(ch24) * 1.5)
    score += components["early_move"]
    score = max(0, min(100, score))

    return {
        "scoring_version": SCORING_VERSION,
        "history_count": len(vols), "baseline_mean": mu, "baseline_stddev": sd,
        "components": components,
        "pre_score": round(score, 1),
        "volume_marketcap_ratio": round(vm, 5),
        "volume_z": round(z, 2),
        "volume_vs_baseline": round(ratio, 2),
        "change_24h": round(ch24, 2),
    }
