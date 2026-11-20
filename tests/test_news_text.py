"""Unit tests for lambdas/shared/news_text.py (normalize, date parsing, dedupe, vendoring)."""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

from conftest import LAMBDAS_DIR

sys.path.insert(0, os.path.join(LAMBDAS_DIR, "shared"))
import news_text as nt  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")


@pytest.mark.unit
def test_normalize_title_strips_company_ticker_exchange_punctuation():
    a = nt.normalize_title("Helios Technologies, Inc. (NASDAQ: HLIO) Tops Q3 Earnings!", "Helios Technologies", "HLIO")
    assert a == "inc tops q3 earnings"
    assert nt.normalize_title("  $HLIO   Shares SOLD by Aberdeen ", "", "HLIO") == "shares sold by aberdeen"
    assert nt.normalize_title(None) == ""


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    ("2026-11-07 09:21:06", datetime(2026, 11, 7, 9, 21, 6, tzinfo=timezone.utc)),
    ("2026-11-07T09:21:06Z", datetime(2026, 11, 7, 9, 21, 6, tzinfo=timezone.utc)),
    ("2026-11-07T04:21:06-05:00", datetime(2026, 11, 7, 9, 21, 6, tzinfo=timezone.utc)),
    (1794302466, datetime(2026, 11, 10, 9, 21, 6, tzinfo=timezone.utc)),
    (1794302466000, datetime(2026, 11, 10, 9, 21, 6, tzinfo=timezone.utc)),
])
def test_published_utc_formats(value, expected):
    assert nt.published_utc(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "garbage", 0, -5, True, [], {}])
def test_published_utc_bad_values(value):
    assert nt.published_utc(value) is None


@pytest.mark.unit
def test_exact_dedupe_keeps_longest_description():
    arts = [
        {"title": "Helios Technologies Q3 Transcript", "description": "x" * 75},
        {"title": "Helios Technologies Q3 transcript!", "description": "y" * 9999},
        {"title": "Helios Technologies Q3 Transcript", "description": "z" * 200},
        {"title": "Other story", "description": "o"},
    ]
    kept, dropped = nt.dedupe_articles(arts)
    assert [len(a["description"]) for a in kept] == [9999, 1]
    assert len(dropped) >= 1


@pytest.mark.unit
def test_near_dedupe_needs_close_dates_and_keeps_more_confident():
    def art(title, day, conf):
        return {"title": title, "published_at": f"2026-11-{day:02d} 10:00:00", "analysis": {"confidence": conf}}
    a = art("Helios Technologies Q3 Earnings Call Highlights", 7, 0.6)
    # Near-identical wording 3 days later: outside the 2-day window, separate event
    b = art("Helios Technologies Q3 Earnings Call Highlights Revisited", 10, 0.9)
    kept, _ = nt.dedupe_articles([a, b], near_duplicates=True)
    assert len(kept) == 2
    # An EXACT title match merges at any date (HLIO: MarketBeat Nov 7, Defense World Nov 10)
    e = art("Helios Technologies Q3 Earnings Call Highlights", 10, 0.9)
    kept, _ = nt.dedupe_articles([a, e], near_duplicates=True)
    assert kept == [e]
    c = art("Helios Technologies Q3 Earnings Call Highlights", 8, 0.9)     # within 2 days: same event
    kept, dropped = nt.dedupe_articles([a, c], near_duplicates=True)
    assert kept == [c] and dropped == {a["title"]: c["title"]}
    d = art("Helios Technologies Q3 Earnings Call Highlights", 6, 0.6)     # tie: earliest wins
    kept, _ = nt.dedupe_articles([a, d], near_duplicates=True)
    assert kept == [d]


@pytest.mark.unit
def test_near_dedupe_does_not_merge_different_stories():
    arts = json.load(open(os.path.join(FIXTURES, "hlio_articles_stored.json")))
    kept, _ = nt.dedupe_articles([a for a in arts if a["relevant"]], near_duplicates=True)
    titles = [a["title"] for a in kept]
    assert len(kept) == 2
    assert any("Tops Q3 Earnings" in t for t in titles)


@pytest.mark.unit
def test_vendored_copies_identical():
    ref = open(os.path.join(LAMBDAS_DIR, "shared", "news_text.py"), "rb").read()
    for lam in ("sentiment-analyzer", "score-calculator"):
        assert open(os.path.join(LAMBDAS_DIR, lam, "news_text.py"), "rb").read() == ref
