"""
Pure selection helpers for the news fetcher (Step 5), pure stdlib.

- is_company_article / filter_company_articles: drop TickerTick stories about
  other companies (the feed tags every story that mentions "Helios" with HLIO).
- detect_earnings_event: find an earnings event in the last 7 days from titles.
- plan_tickertick_calls: decide which stocks get a (paced, capped) TickerTick call.
- cap_articles: keep the newest articles, earnings/analyst items first.
"""

import re
from datetime import date, timedelta

from news_text import published_utc

EVENT_WINDOW_DAYS = 7

_LEGAL_SUFFIXES = {
    "inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "llc", "lp", "holdings", "nv", "sa", "ag",
}
# A short one-word company name ("Apple", "Visa") is also an everyday word, so
# it only counts when the text has an equity cue.
SHORT_NAME_MAX_LEN = 7
_EQUITY_CUE = re.compile(
    r"\b(stock|shares|earnings|analyst|price target|quarter|guidance|nyse|nasdaq|revenue|eps)\b")

_EARNINGS_TITLE = re.compile(
    r"earnings|results|guidance|outlook|\bq[1-4]\b.{0,40}(sales|revenue|eps)"
    r"|(first|second|third|fourth)[- ]quarter|earnings call|transcript|\bform 10-[qk]\b"
    r"|\bbeats?\b|\btops\b|\bmisses\b", re.IGNORECASE)
_SCHEDULING_TITLE = re.compile(
    r"\b(to|will)\s+(report|release|announce|host|webcast|present)\b"
    r"|earnings (date|preview|calendar)|what to expect|ahead of (earnings|results)"
    r"|\breports? next week\b|\bconference\b|\bwebcast\b|\bschedul", re.IGNORECASE)
_ANALYST_TITLE = re.compile(
    r"price target|downgrade|upgrade|guidance|outlook|\bcuts?\b|\braises?\b|reiterat", re.IGNORECASE)


def _plain(text: str) -> str:
    """Lowercase, drop periods (n.v. -> nv), turn other punctuation into spaces."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace(".", "")).split())


def company_core_name(company_name: str) -> str:
    """Company name without legal suffixes, in `_plain` form ("Helios Technologies, Inc." -> "helios technologies")."""
    tokens = _plain(company_name).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def is_company_article(article: dict, ticker: str, company_name: str) -> bool:
    """True if the article is about this company: core name in title/description, or an explicit ticker reference."""
    title = article.get("title") or ""
    body = title + " " + (article.get("description") or "")[:600]
    if ticker:
        sym = re.escape(ticker)
        ticker_ref = (
            r"(nyse|nasdaq|nysemkt|nysearca|amex|otc)\w*\s*:\s*" + sym + r"\b",
            r"\(" + sym + r"\)",
            r"\$" + sym + r"\b",
        )
        if any(re.search(p, body, re.IGNORECASE) for p in ticker_ref):
            return True
    core = company_core_name(company_name)
    if not core:
        return False
    plain_body = _plain(body)
    if not re.search(r"\b" + re.escape(core) + r"\b", plain_body):
        return False
    if " " not in core and len(core) < SHORT_NAME_MAX_LEN:
        return bool(_EQUITY_CUE.search(plain_body))
    return True


def filter_company_articles(articles: list, ticker: str, company_name: str) -> list:
    return [a for a in articles if is_company_article(a, ticker, company_name)]


def _published_date(article: dict):
    dt = published_utc(article.get("published_at"))
    return dt.date() if dt else None


def detect_earnings_event(articles: list, today: str, window_days: int = EVENT_WINDOW_DAYS,
                          prior_event_date: str = None):
    """
    Earnings event from article titles published in the last `window_days` days.

    A title counts when it matches the earnings regex and not the scheduling /
    preview regex. `event_date` is the earliest matching publish date. If a
    previously stored event date lies within `window_days` of it, that stored
    date is kept so the event key stays stable as old articles age out.
    Returns {event_date, evidence_titles[:3]} or None. The caller owns the symbol
    and builds the event key.
    """
    today_d = date.fromisoformat(today)
    oldest = today_d - timedelta(days=window_days)
    newest = today_d + timedelta(days=1)  # one-day skew for timestamps just past midnight
    hits = []
    for art in articles:
        d = _published_date(art)
        title = art.get("title") or ""
        if d is None or not (oldest <= d <= newest):
            continue
        if _EARNINGS_TITLE.search(title) and not _SCHEDULING_TITLE.search(title):
            hits.append((d, title))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    event_date = hits[0][0]
    if prior_event_date:
        try:
            prior = date.fromisoformat(prior_event_date)
            if abs((event_date - prior).days) <= window_days:
                event_date = prior
        except ValueError:
            pass
    return {"event_date": event_date.isoformat(), "evidence_titles": [t for _, t in hits[:3]]}


def plan_tickertick_calls(candidates: list, max_calls: int, today: str) -> tuple:
    """
    Choose which stocks get a TickerTick call. Candidate:
    {symbol, fmp_count, earnings_event, divergence}. Eligible when divergence is
    set, an earnings event is present, or fewer than 4 FMP articles.

    Order: divergence first, then earnings events (newest first), then stocks
    that are only under-covered, rotated by the day number so every stock gets
    a turn when the cap binds. Returns (selected, deferred) symbol lists.
    """
    priority, under_covered = [], []
    for c in candidates:
        if c.get("divergence") or c.get("earnings_event"):
            priority.append(c)
        elif c.get("fmp_count", 0) < 4:
            under_covered.append(c)

    def priority_key(c):
        event = c.get("earnings_event") or {}
        return (not c.get("divergence"), not event, _neg_date(event.get("event_date")), c["symbol"])

    priority.sort(key=priority_key)
    under_covered.sort(key=lambda c: c["symbol"])
    if under_covered:
        shift = date.fromisoformat(today).toordinal() % len(under_covered)
        under_covered = under_covered[shift:] + under_covered[:shift]

    ordered = [c["symbol"] for c in priority + under_covered]
    return ordered[:max_calls], ordered[max_calls:]


def _neg_date(value):
    """Sort key putting newer ISO dates first (missing last)."""
    return -date.fromisoformat(value).toordinal() if value else 0


LONG_TEXT_CHARS = 2000   # transcripts / full releases; the guidance step needs them


def cap_articles(articles: list, limit: int = 12) -> list:
    """
    Keep at most `limit` articles. Earnings/analyst titles rank first; within
    them, long texts (transcripts, full releases) before short snippets, then
    newest. Output newest first.
    """
    def ts(a):
        dt = published_utc(a.get("published_at"))
        return dt.timestamp() if dt else 0.0

    def priority(a):
        title = a.get("title") or ""
        return bool(_EARNINGS_TITLE.search(title) or _ANALYST_TITLE.search(title))

    def long_text(a):
        return len(a.get("description") or "") >= LONG_TEXT_CHARS

    chosen = sorted(articles, key=lambda a: (not priority(a), not long_text(a), -ts(a)))[:limit]
    return sorted(chosen, key=ts, reverse=True)
