"""
News text helpers shared by the news pipeline Lambdas (pure stdlib).

Copy of record lives in lambdas/shared/. It is vendored byte-identical into
each Lambda that uses it (sentiment-analyzer, score-calculator); a test
asserts the copies match.
"""

import re
from datetime import datetime, timezone

_EXCHANGE_TAG = re.compile(r"\(\s*(?:nyse|nasdaq|nysemkt|amex|otc)\w*\s*:\s*[a-z.]+\s*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Near-duplicate rule: same event if title token sets overlap this much and the
# articles were published within NEAR_DUPLICATE_DAYS of each other.
NEAR_DUPLICATE_JACCARD = 0.75
NEAR_DUPLICATE_DAYS = 2


def normalize_title(title: str, company_name: str = "", ticker: str = "") -> str:
    """Lowercase title with exchange tags, company name, ticker and punctuation removed."""
    text = (title or "").lower()
    text = _EXCHANGE_TAG.sub(" ", text)
    if company_name:
        text = text.replace(company_name.lower(), " ")
    if ticker:
        text = re.sub(r"\$?\b" + re.escape(ticker.lower()) + r"\b", " ", text)
    return _NON_ALNUM.sub(" ", text).strip()


def published_utc(value):
    """
    Parse an article timestamp into an aware UTC datetime, or None.

    Accepts "YYYY-MM-DD HH:MM:SS" (FMP, taken as UTC), ISO-8601, and epoch
    seconds or milliseconds (int/float).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            if value <= 0:
                return None
            ts = value / 1000 if value > 1e12 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _confidence(article: dict) -> float:
    c = (article.get("analysis") or {}).get("confidence")
    return float(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else 0.0


def _is_near_duplicate(tokens_a: set, dt_a, tokens_b: set, dt_b) -> bool:
    if not tokens_a or not tokens_b:
        return False
    if len(tokens_a & tokens_b) / len(tokens_a | tokens_b) < NEAR_DUPLICATE_JACCARD:
        return False
    if dt_a is None or dt_b is None:
        return False
    return abs((dt_a - dt_b).total_seconds()) <= NEAR_DUPLICATE_DAYS * 86400


def dedupe_articles(articles: list, company_name: str = "", ticker: str = "",
                    near_duplicates: bool = False) -> tuple:
    """
    Collapse articles that describe the same item.

    Exact mode (default): equal normalized titles; the longest description wins
    (the same title can arrive with 9,999, 200 and 75 chars). Ties keep the first.
    Near mode: equal titles, or token-set Jaccard >= 0.75 with publication
    within 2 days; the article with the higher analysis confidence wins, ties
    keep the earliest published (then first listed).

    Returns (kept, {dropped_title: kept_title}); kept preserves input order.
    """
    kept = []      # list of [article, tokens, published_dt]
    dropped = {}

    for art in articles:
        norm = normalize_title(art.get("title", ""), company_name, ticker)
        tokens = set(norm.split())
        dt = published_utc(art.get("published_at"))
        match = None
        for slot in kept:
            if slot[3] == norm and norm:
                match = slot
                break
            if near_duplicates and _is_near_duplicate(tokens, dt, slot[1], slot[2]):
                match = slot
                break
        if match is None:
            kept.append([art, tokens, dt, norm])
            continue

        current = match[0]
        if near_duplicates:
            new_wins = _confidence(art) > _confidence(current)
            if _confidence(art) == _confidence(current):
                new_wins = dt is not None and (match[2] is None or dt < match[2])
        else:
            new_wins = len(art.get("description") or "") > len(current.get("description") or "")
        if new_wins:
            dropped[current.get("title", "")] = art.get("title", "")
            match[0], match[1], match[2], match[3] = art, tokens, dt, norm
        else:
            dropped[art.get("title", "")] = current.get("title", "")

    return [slot[0] for slot in kept], dropped
