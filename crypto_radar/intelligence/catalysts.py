"""Conservative headline heuristics: labels are hypotheses, not verified facts."""
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

CATEGORIES = (
    ('hack/security incident', r'\bhack(?:ed)?\b|\bexploit\b|\bsecurity breach\b'),
    ('exchange listing', r'\blist(?:ing|ed|s)\b'),
    ('partnership', r'\bpartner(?:ship|s)?\b'),
    ('product/project launch', r'\blaunch(?:es|ed)?\b'),
    ('protocol upgrade', r'\bupgrade\b|\bhard fork\b'),
    ('regulatory event', r'\bregulat|\bsec\b|\blawsuit\b'),
    ('funding/investment', r'\bfunding\b|\binvestment\b|\braises\b'),
    ('tokenomics event', r'\btoken burn\b|\bunlock\b|\btokenomics\b'),
    ('influencer/high-profile mention', r'\binfluencer\b'),
)


def normalize_news(item, coin, observed):
    title = str(item.get('title','')).strip()[:2000]
    if not title:
        raise ValueError('Missing headline')
    source = str(item.get('source','')).strip()[:300]
    link = str(item.get('link','')).strip()[:4000]
    # Remove the publisher suffix for exact headline deduplication across feeds.
    headline = title.rsplit(' - ',1)[0].lower()
    normalized = ' '.join(re.findall(r'\w+',headline))
    published = None
    try:
        parsed = parsedate_to_datetime(item.get('published',''))
        if parsed.tzinfo is not None:
            published = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        pass
    category = next((name for name, pattern in CATEGORIES if re.search(pattern,headline)), 'unknown')
    # Ambiguous bare tickers never establish relevance. Require the asset name.
    relevance = float(bool(re.search(r'(?<!\w)'+re.escape(coin['name'].lower())+r'(?!\w)',headline)))
    key = hashlib.sha256(normalized.encode()).hexdigest()
    fingerprint = hashlib.sha256((normalized+'|'+source.lower()).encode()).hexdigest()
    return dict(fingerprint=fingerprint, first_observed_ts=observed.isoformat(),
        publication_ts=published, title=title, link=link, source=source,
        catalyst_type=category, relevance=relevance, event_key=key)


def news_features(rows, now, cfg):
    evidence = []
    for raw in rows:
        r = dict(raw)
        first = datetime.fromisoformat(r['first_observed_ts'])
        if first > now:
            continue
        publication = datetime.fromisoformat(r['publication_ts']) if r['publication_ts'] else None
        age = (now-publication).total_seconds() if publication else None
        if age is not None and (age < 0 or age > cfg['max_age_hours']*3600):
            continue
        event_first = datetime.fromisoformat(r.get('event_first_observed_ts') or r['first_observed_ts'])
        novelty = max(0, 1-(now-event_first).total_seconds()/(cfg['novelty_hours']*3600))
        if novelty <= 0 or not r['relevance']:
            continue
        r.update(age_seconds=age, novelty=novelty,
                 independent_confirmations=None)  # RSS publishers do not prove independence.
        evidence.append(r)
    # Syndicated headlines count once; do not claim distinct publishers independently confirmed an event.
    groups = {}
    for r in evidence:
        groups.setdefault(r['event_key'], []).append(r)
    scores = []
    for group in groups.values():
        scores.append(max((15 + 20*(r['catalyst_type'] != 'unknown')
                          + 10*(r['source_quality'] is not None and r['source_quality'] >= 0.8))
                         * r['novelty'] * (1 if r['age_seconds'] is not None else 0.5)
                         for r in group))
    return dict(score=round(min(100,sum(scores)),2), evidence=evidence,
                unique_headlines=len(groups), classifier_version='headline-rules-1.2',
                status='available' if evidence else 'no_recent_relevant_evidence')
