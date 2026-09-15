import os, time, requests

BASE = "https://api.coingecko.com/api/v3"

def fetch_markets(currency="usd", count=100):
    params = {
        "vs_currency": currency,
        "order": "market_cap_desc",
        "per_page": min(count, 250),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    headers = {"accept": "application/json", "User-Agent": "crypto-radar/0.1"}
    key = os.getenv("COINGECKO_API_KEY", "").strip()
    if key:
        headers["x-cg-demo-api-key"] = key

    for attempt in range(5):
        r = requests.get(f"{BASE}/coins/markets", params=params, headers=headers, timeout=30)
        if r.status_code == 429:
            time.sleep(min(60, 2 ** attempt * 5))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("CoinGecko rate limit persisted after retries")
