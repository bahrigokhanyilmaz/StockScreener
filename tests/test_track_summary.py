"""
Unit tests for cumulative tracking gain/loss (share-dollar basis):
    cumulative % = Σ(end - mark) / Σ(mark) × 100
Open marks use current price; closed stints use unmark price. Each stint = 1 share.
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
def test_open_and_closed_combined():
    # marks: 100->110 (open, +10), 50->45 (closed, -5)
    # Σend=155, Σmark=150 -> (155-150)/150 = 3.33%
    r = compute([{"mark_price": 100, "current_price": 110}],
                [{"mark_price": 50, "unmark_price": 45}])
    assert r["cumulative_pct"] == 3.33
    assert r["open_count"] == 1 and r["closed_count"] == 1
    assert r["total_cost"] == 150.0 and r["total_value"] == 155.0


@pytest.mark.unit
def test_dollar_weighted_not_simple_average():
    # A big-$ small-% and small-$ big-% should weight by dollars, not average %.
    # 1000->1100 (+10%), 10->20 (+100%). Σmark=1010, Σend=1120 -> 10.89%
    r = compute([{"mark_price": 1000, "current_price": 1100},
                 {"mark_price": 10, "current_price": 20}], [])
    assert r["cumulative_pct"] == 10.89  # NOT (10+100)/2 = 55


@pytest.mark.unit
def test_loss():
    r = compute([{"mark_price": 100, "current_price": 80}], [])
    assert r["cumulative_pct"] == -20.0


@pytest.mark.unit
def test_empty_is_none():
    r = compute([], [])
    assert r["cumulative_pct"] is None
    assert r["total_cost"] == 0.0


@pytest.mark.unit
def test_skips_entries_missing_prices():
    r = compute([{"mark_price": 100, "current_price": None},
                 {"mark_price": None, "current_price": 50},
                 {"mark_price": 100, "current_price": 120}], [])
    # only the third counts
    assert r["cumulative_pct"] == 20.0
    assert r["open_count"] == 1


@pytest.mark.unit
def test_remarked_ticker_counts_each_stint():
    # same ticker, two closed stints + one open — all count
    r = compute([{"mark_price": 100, "current_price": 100}],
                [{"mark_price": 100, "unmark_price": 110},
                 {"mark_price": 100, "unmark_price": 90}])
    # Σmark=300 Σend=300 -> 0%
    assert r["cumulative_pct"] == 0.0
    assert r["open_count"] == 1 and r["closed_count"] == 2
