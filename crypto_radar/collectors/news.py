from urllib.parse import quote_plus
import feedparser
import requests

def recent_news(name, symbol, limit=8):
    query = quote_plus(f'"{name}" cryptocurrency OR "{symbol}" crypto when:1d')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError("News feed could not be parsed")
    items = []
    for e in feed.entries[:limit]:
        items.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "published": e.get("published", ""),
            "source": (e.get("source") or {}).get("title", "") if isinstance(e.get("source"), dict) else "",
        })
    return items
