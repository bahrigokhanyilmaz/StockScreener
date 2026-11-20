"""
Unit + regression tests for the price-vs-news divergence flag (Layer 1).

HLIO (Nov 2026): fell 30.7% in 3 sessions after Q3 earnings while every stored
article scored positive (stock sentiment +0.72). Fixture bars are the real
stored PRICE_HISTORY#HLIO series (read-only copy).
"""
import json
import os
import sys

import pytest

from conftest import LAMBDAS_DIR

sys.path.insert(0, os.path.join(LAMBDAS_DIR, "score-calculator"))
import price_signals as ps  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")


def hlio_bars(through=None):
    bars = json.load(open(os.path.join(FIXTURES, "hlio_price_bars.json")))
    return [b for b in bars if through is None or b["d"] <= through]


def flat(n, close=100.0, vol=1000):
    return [{"d": f"2026-10-{i + 1:02d}", "c": close, "v": vol} for i in range(n)]


@pytest.mark.regression
def test_hlio_flag_triggers_on_real_bars_nov_5_and_6():
    nov5 = ps.news_price_divergence(hlio_bars("2026-11-05"), 0.727)
    assert nov5["tier"] == "strong" and nov5["flag"] is True
    assert nov5["return_3d"] == pytest.approx(-0.1095, abs=1e-4)
    assert nov5["volume_ratio"] == pytest.approx(2.5, abs=0.1)

    nov6 = ps.news_price_divergence(hlio_bars("2026-11-06"), 0.727)
    assert nov6["return_3d"] == pytest.approx(-0.3067, abs=1e-4)
    assert nov6["return_1d"] == pytest.approx(-0.2105, abs=1e-4)
    assert nov6["trigger_date"] == "2026-11-06" and nov6["as_of"] == "2026-11-06"
    assert nov6["sentiment"] == 0.727


@pytest.mark.regression
def test_hlio_not_flagged_before_drop():
    assert ps.news_price_divergence(hlio_bars("2026-11-04"), 0.727) is None


@pytest.mark.unit
def test_flag_stays_lit_for_lookback_then_clears():
    lit = [d for d in ("2026-11-09", "2026-11-12", "2026-11-13", "2026-11-16")
           if ps.news_price_divergence(hlio_bars(d), 0.727)]
    assert lit == ["2026-11-09", "2026-11-12", "2026-11-13", "2026-11-16"]
    # trigger date stays the worst window, as_of moves
    r = ps.news_price_divergence(hlio_bars("2026-11-12"), 0.727)
    assert r["trigger_date"] == "2026-11-06" and r["as_of"] == "2026-11-12"
    assert ps.news_price_divergence(hlio_bars("2026-11-17"), 0.727) is None


@pytest.mark.unit
@pytest.mark.parametrize("sentiment", [0.3, 0.1, 0.0, -0.5, None, "x", True])
def test_sentiment_gate(sentiment):
    assert ps.news_price_divergence(hlio_bars("2026-11-06"), sentiment) is None


@pytest.mark.unit
def test_sentiment_just_above_gate_flags():
    assert ps.news_price_divergence(hlio_bars("2026-11-06"), 0.301)["flag"] is True


@pytest.mark.unit
def test_mild_tier_needs_volume():
    bars = flat(25)
    bars[-1]["c"] = 92.0                     # -8% over 3 sessions (c[i-3]=100)
    bars[-1]["v"] = 1000                     # no volume spike
    assert ps.news_price_divergence(bars, 0.5) is None
    bars[-1]["v"] = 2500                     # 2.5x baseline
    r = ps.news_price_divergence(bars, 0.5)
    assert r["tier"] == "mild" and r["volume_ratio"] == 2.5


@pytest.mark.unit
def test_mild_tier_not_reached_above_seven_percent_drop():
    bars = flat(25)
    bars[-1]["c"] = 94.0                     # -6%
    bars[-1]["v"] = 9000
    assert ps.news_price_divergence(bars, 0.5) is None


@pytest.mark.unit
def test_strong_tier_without_volume_data():
    bars = [{"d": f"2026-10-{i + 1:02d}", "c": c} for i, c in enumerate([100, 100, 100, 100, 88])]
    r = ps.news_price_divergence(bars, 0.5)
    assert r["tier"] == "strong" and r["volume_ratio"] is None


@pytest.mark.unit
def test_exactly_ten_percent_is_strong_and_nine_is_not():
    assert ps.news_price_divergence(
        [{"d": f"2026-10-0{i}", "c": c, "v": 1} for i, c in enumerate([100, 100, 100, 90], 1)], 0.5
    )["tier"] == "strong"
    assert ps.news_price_divergence(
        [{"d": f"2026-10-0{i}", "c": c, "v": 1} for i, c in enumerate([100, 100, 100, 91], 1)], 0.5
    ) is None


@pytest.mark.unit
def test_too_few_or_bad_bars_returns_none():
    assert ps.news_price_divergence([], 0.5) is None
    assert ps.news_price_divergence(None, 0.5) is None
    assert ps.news_price_divergence(flat(3), 0.5) is None
    bad = [{"d": "2026-10-01", "c": 100}, {"d": "2026-10-02"}, {"d": "2026-10-03", "c": float("nan")},
           {"d": "2026-10-04", "c": 0}]
    assert ps.news_price_divergence(bad, 0.5) is None


@pytest.mark.unit
def test_rising_stock_never_flags():
    bars = [{"d": f"2026-10-{i + 1:02d}", "c": 100 + i, "v": 1000} for i in range(25)]
    assert ps.news_price_divergence(bars, 0.9) is None
