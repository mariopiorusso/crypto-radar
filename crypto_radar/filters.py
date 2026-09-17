"""Maintain stablecoin exclusions in configuration, keyed by CoinGecko ID."""
DEFAULT_STABLECOINS = [
    "tether", "usd-coin", "dai", "ethena-usde", "usds", "usdd", "true-usd",
    "first-digital-usd", "paypal-usd", "frax", "pax-dollar", "gemini-dollar",
    "binance-usd", "eurc", "euro-coin", "staked-ethena-usde", "savings-usds",
    "usual-usd", "usd0", "usdx-money-usdx", "ripple-usd", "global-dollar",
]


def is_stablecoin(coin, config):
    return coin["id"].lower() in {s.lower() for s in config["filters"]["stablecoin_ids"]}
