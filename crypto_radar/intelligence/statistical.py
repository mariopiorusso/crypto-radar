from statistics import mean, pstdev

def _safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default

def score_candidate(row, hist, cfg):
    market_cap = _safe(row.get("market_cap"))
    volume = _safe(row.get("total_volume"))
    ch24 = _safe(row.get("price_change_percentage_24h"))

    if market_cap < cfg["min_market_cap_usd"] or volume < cfg["min_24h_volume_usd"]:
        return None
    if abs(ch24) > cfg["max_abs_24h_change_pct"]:
        return None

    vm = volume / market_cap if market_cap else 0
    score = min(30, vm * 100)

    vols = [_safe(h["total_volume"]) for h in hist if h["total_volume"]]
    z = 0.0
    ratio = 1.0
    if len(vols) >= cfg["min_history_points"]:
        mu = mean(vols)
        sd = pstdev(vols)
        ratio = volume / mu if mu else 1.0
        z = (volume - mu) / sd if sd > 0 else 0.0
        if z >= cfg["anomaly_threshold"]:
            score += min(35, z * 6)
        if ratio >= 1.25:
            score += min(20, (ratio - 1) * 20)

    # Prefer early signals; penalize assets already moving hard.
    score += max(0, 15 - abs(ch24) * 1.5)
    score = max(0, min(100, score))

    return {
        "pre_score": round(score, 1),
        "volume_marketcap_ratio": round(vm, 5),
        "volume_z": round(z, 2),
        "volume_vs_baseline": round(ratio, 2),
        "change_24h": round(ch24, 2),
    }
