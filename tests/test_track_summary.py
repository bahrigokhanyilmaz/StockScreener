"""
Unit tests for cumulative tracking gain/loss — EQUAL-WEIGHTED:
    cumulative % = average( (end - mark) / mark × 100 )
Every idea weighs the same regardless of share price. Open marks use current
price; closed stints use unmark price. Each stint counts once.
"""
import pytest
from conftest import load_handler

api = load_handler("api")
compute = api.compute_cumulative_tracking


@pytest.mark.unit
def test_single_open_gain():
    r = compute([{"mark_price": 100, "current_price": 110}], [])
    assert r["cumulative_pct"] == 10.0
    assert r["open_count"] == 1 and r["closed_count"] == 0


@pytest.mark.unit
def test_open_and_closed_averaged():
    # 100->110 (+10%), 50->45 (-10%) -> average 0%
    r = compute([{"mark_price": 100, "current_price": 110}],
                [{"mark_price": 50, "unmark_price": 45}])
    assert r["cumulative_pct"] == 0.0
    assert r["open_count"] == 1 and r["closed_count"] == 1


@pytest.mark.unit
def test_equal_weight_not_dollar_weighted():
    # 1000->1100 (+10%), 10->20 (+100%). Equal-weight average = 55%,
    # NOT the dollar-weighted ~10.89%.
    r = compute([{"mark_price": 1000, "current_price": 1100},
                 {"mark_price": 10, "current_price": 20}], [])
    assert r["cumulative_pct"] == 55.0


@pytest.mark.unit
def test_loss():
    r = compute([{"mark_price": 100, "current_price": 80}], [])
    assert r["cumulative_pct"] == -20.0


@pytest.mark.unit
def test_empty_is_none():
    r = compute([], [])
    assert r["cumulative_pct"] is None


@pytest.mark.unit
def test_skips_entries_missing_prices():
    r = compute([{"mark_price": 100, "current_price": None},
                 {"mark_price": None, "current_price": 50},
                 {"mark_price": 100, "current_price": 120}], [])
    assert r["cumulative_pct"] == 20.0
    assert r["open_count"] == 1


@pytest.mark.unit
def test_remarked_ticker_counts_each_stint():
    # +10% and -10% stints + a flat open -> average of (10, -10, 0) = 0
    r = compute([{"mark_price": 100, "current_price": 100}],
                [{"mark_price": 100, "unmark_price": 110},
                 {"mark_price": 100, "unmark_price": 90}])
    assert r["cumulative_pct"] == 0.0
    assert r["open_count"] == 1 and r["closed_count"] == 2
