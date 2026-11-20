"""
Unit + regression tests for lambdas/news-fetcher/news_select.py using the real
HLIO TickerTick feed (tests/fixtures/hlio/hlio_tickertick_n100.json, one read-only
call to api.tickertick.com/feed?q=tt:hlio&lang=en&n=100).

Regression: TickerTick tags every story that mentions "Helios" with HLIO, so AMD
Helios, Helios Towers, Sanmina and Titan stores reached the sentiment step.
"""
import json
import os
import sys
from datetime import datetime, timezone
import pytest
from conftest import LAMBDAS_DIR
sys.path.insert(0, os.path.join(LAMBDAS_DIR, "news-fetcher"))
import news_select as ns  # noqa: E402
from news_text import published_utc  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")
HLIO_NAME = "Helios Technologies, Inc."


def _tt():
    out = []
    for s in json.load(open(os.path.join(FIXTURES, "hlio_tickertick_n100.json"))):
        out.append({"title": s["title"], "description": s["description"] or "", "source": s["site"],
                    "published_at": datetime.fromtimestamp(
                        s["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")})
    return out


def _titles(arts):
    return [a["title"] for a in arts]


# ---------- relevance filter ----------

@pytest.mark.regression
def test_filter_drops_wrong_company_noise_and_keeps_hlio_stories():
    kept = ns.filter_company_articles(_tt(), "HLIO", HLIO_NAME)
    kept_titles = _titles(kept)
    assert len(kept) == 39

    joined = " | ".join(kept_titles)
    for noise in ("Titan to expand", "Helios Towers", "AMD Stocks Rise", "Sanmina", "Supermicro",
                  "Helios Launches Revolutionary Blood", "Quantinuum", "Galaxy"):
        assert noise not in joined, noise

    for must in ("Releases Q4 2026 Earnings Guidance", "Releases FY 2026 Earnings Guidance",
                 "Given New $85.00 Price Target at KeyCorp", "Price Target Reduction from JPMorgan",
                 "Price Target Cut to $65.00 by Analysts at Stifel", "Stifel Nicolaus Issues Pessimistic",
                 "surprises with Q3 2026 sales", "HLIO Q3 2026 deep dive",
                 "Helios (HLIO) Q3 2026 Earnings Call Transcript",
                 "Earnings Beat Raises Questions After Softer Outlook"):
        assert must in joined, must


@pytest.mark.unit
def test_core_name_strips_legal_suffixes():
    assert ns.company_core_name("Helios Technologies, Inc.") == "helios technologies"
    assert ns.company_core_name("Acme Holdings Corp.") == "acme"
    assert ns.company_core_name("Foo N.V.") == "foo"
    assert ns.company_core_name("") == ""


@pytest.mark.unit
def test_short_single_word_name_needs_equity_cue():
    apple_fruit = {"title": "Apple pie recipe for autumn", "description": ""}
    apple_stock = {"title": "Apple shares rise after earnings", "description": ""}
    assert not ns.is_company_article(apple_fruit, "AAPL", "Apple Inc.")
    assert ns.is_company_article(apple_stock, "AAPL", "Apple Inc.")


@pytest.mark.unit
def test_empty_company_name_uses_ticker_patterns_only():
    assert ns.is_company_article({"title": "ACME (NASDAQ: ACME) beats", "description": ""}, "ACME", "")
    assert ns.is_company_article({"title": "Why $ACME jumped", "description": ""}, "ACME", "")
    assert not ns.is_company_article({"title": "Acme rockets explode", "description": ""}, "ACME", "")


# ---------- earnings event detection ----------

def _hlio_fmp():
    return json.load(open(os.path.join(FIXTURES, "hlio_fmp_step5.json")))


@pytest.mark.regression
def test_event_detected_on_hlio_release_day_and_scheduling_vetoed():
    arts = _hlio_fmp() + ns.filter_company_articles(_tt(), "HLIO", HLIO_NAME)
    ev = ns.detect_earnings_event(arts, "2026-11-09")
    assert ev["event_date"] == "2026-11-05"
    assert 1 <= len(ev["evidence_titles"]) <= 3
    assert not any("Webcast" in t or "what to expect" in t for t in ev["evidence_titles"])


@pytest.mark.unit
def test_scheduling_and_preview_titles_do_not_count():
    arts = [
        {"title": "Helios Technologies to Webcast Presentation at the Baird Conference", "published_at": "2026-11-04 16:15:00"},
        {"title": "Helios (HLIO) to report earnings tomorrow: here is what to expect", "published_at": "2026-11-04 12:00:00"},
        {"title": "Helios Technologies Schedules Third Quarter 2026 Financial Results Release", "published_at": "2026-11-04 10:00:00"},
        {"title": "Helios Reports Next Week: Wall Street Expects Earnings Growth", "published_at": "2026-11-04 09:00:00"},
    ]
    assert ns.detect_earnings_event(arts, "2026-11-05") is None


@pytest.mark.unit
def test_event_window_edges():
    def art(day):
        return [{"title": "Acme Q3 earnings beat", "published_at": f"2026-11-{day:02d} 12:00:00"}]
    assert ns.detect_earnings_event(art(2), "2026-11-09")["event_date"] == "2026-11-02"   # exactly 7 days back
    assert ns.detect_earnings_event(art(1), "2026-11-09") is None                          # 8 days back
    assert ns.detect_earnings_event(art(10), "2026-11-09")["event_date"] == "2026-11-10"   # one-day future skew
    assert ns.detect_earnings_event(art(11), "2026-11-09") is None


@pytest.mark.unit
def test_prior_event_date_anchors_event_key_when_close():
    arts = [{"title": "Acme Q3 earnings beat", "published_at": "2026-11-07 12:00:00"}]
    assert ns.detect_earnings_event(arts, "2026-11-09", prior_event_date="2026-11-05")["event_date"] == "2026-11-05"
    assert ns.detect_earnings_event(arts, "2026-11-09", prior_event_date="2026-08-05")["event_date"] == "2026-11-07"
    assert ns.detect_earnings_event(arts, "2026-11-09", prior_event_date="garbage")["event_date"] == "2026-11-07"


@pytest.mark.unit
def test_no_articles_or_undated_gives_none():
    assert ns.detect_earnings_event([], "2026-11-09") is None
    assert ns.detect_earnings_event([{"title": "Acme earnings", "published_at": ""}], "2026-11-09") is None


# ---------- call plan ----------

def _cand(sym, fmp=6, event=None, div=False):
    return {"symbol": sym, "fmp_count": fmp, "earnings_event": event, "divergence": div}


@pytest.mark.unit
def test_plan_orders_divergence_then_earnings_then_under_covered_and_skips_covered():
    cands = [
        _cand("UNDER", fmp=1),
        _cand("OLDEV", event={"event_date": "2026-11-03"}),
        _cand("NEWEV", event={"event_date": "2026-11-06"}),
        _cand("DIV", div=True),
        _cand("COVERED"),
    ]
    selected, deferred = ns.plan_tickertick_calls(cands, 10, "2026-11-09")
    assert selected == ["DIV", "NEWEV", "OLDEV", "UNDER"]
    assert deferred == []


@pytest.mark.unit
def test_plan_cap_defers_stragglers_and_rotates_under_covered_by_day():
    cands = [_cand("EV", event={"event_date": "2026-11-06"})] + [_cand(s, fmp=0) for s in "ABCD"]
    sel_a, def_a = ns.plan_tickertick_calls(cands, 3, "2026-11-09")
    sel_b, def_b = ns.plan_tickertick_calls(cands, 3, "2026-11-10")
    assert sel_a[0] == sel_b[0] == "EV"
    assert len(sel_a) == 3 and len(def_a) == 2
    assert sel_a != sel_b                      # a different day starts at a different stock
    assert set(sel_a) | set(def_a) == {"EV", "A", "B", "C", "D"}


@pytest.mark.unit
def test_plan_empty():
    assert ns.plan_tickertick_calls([], 45, "2026-11-09") == ([], [])


# ---------- cap ----------

@pytest.mark.unit
def test_cap_keeps_earnings_and_analyst_first_then_newest_output_newest_first():
    arts = [{"title": f"Generic story {i}", "published_at": f"2026-11-{i:02d} 10:00:00"} for i in range(1, 9)]
    arts.append({"title": "Stifel price target cut", "published_at": "2026-10-01 10:00:00"})
    arts.append({"title": "Q3 earnings call transcript", "published_at": "2026-10-02 10:00:00"})
    out = ns.cap_articles(arts, 4)
    assert len(out) == 4
    assert {"Stifel price target cut", "Q3 earnings call transcript"} <= set(_titles(out))
    stamps = [published_utc(a["published_at"]) for a in out]
    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.regression
def test_cap_keeps_long_transcript_and_stockstory_over_newer_short_snippets():
    """With 20+ HLIO earnings items and a cap of 12, the 9,999-char transcript was cut by recency."""
    kept = ns.filter_company_articles(_tt(), "HLIO", HLIO_NAME)
    out = ns.cap_articles(kept, 12)
    by_len = {len(a["description"]) for a in out}
    assert 9999 in by_len and 6234 in by_len
