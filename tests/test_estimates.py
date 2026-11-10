"""Tests for lambdas/shared/estimates.py (snapshot builder + revision helper)."""
import os
import sys

import pytest
from conftest import LAMBDAS_DIR

sys.path.insert(0, os.path.join(LAMBDAS_DIR, "shared"))
import estimates as est  # noqa: E402

FETCHED = "2026-11-09T22:05:00Z"


def _ests():
    return [
        {"date": "2026-12-31", "epsAvg": 2.5, "numAnalystsEps": 4, "revenueAvg": 1000.0},
        {"date": "2027-12-31", "epsAvg": 3.0, "numAnalystsEps": 3, "revenueAvg": 1100.0},
        {"date": "2028-12-31", "epsAvg": 3.5, "numAnalystsEps": 2, "revenueAvg": 1200.0},
    ]


@pytest.mark.unit
def test_builder_full():
    s = est.build_estimates_snapshot(
        _ests(), {"lastQuarterAvgPriceTarget": 60.0, "lastQuarterCount": 3}, FETCHED)
    assert s["fetched_at"] == FETCHED
    assert (s["eps_fy1"], s["eps_fy1_period_end"], s["eps_fy1_analysts"]) == (2.5, "2026-12-31", 4)
    assert (s["eps_fy2"], s["eps_fy2_period_end"], s["eps_fy2_analysts"]) == (3.0, "2027-12-31", 3)
    assert s["revenue_fy1"] == 1000.0
    assert (s["target_price_avg"], s["target_price_count"], s["target_price_window"]) == (60.0, 3, "last_quarter")


@pytest.mark.unit
def test_builder_target_falls_back_to_year():
    s = est.build_estimates_snapshot(
        [], {"lastQuarterAvgPriceTarget": 0, "lastYearAvgPriceTarget": 55.5, "lastYearCount": 7}, FETCHED)
    assert (s["target_price_avg"], s["target_price_count"], s["target_price_window"]) == (55.5, 7, "last_year")


@pytest.mark.unit
def test_builder_empty_is_all_none_with_fetched_at():
    s = est.build_estimates_snapshot(None, None, FETCHED)
    assert s["fetched_at"] == FETCHED
    assert all(v is None for k, v in s.items() if k != "fetched_at")


@pytest.mark.unit
def test_builder_zero_eps_kept_and_non_numeric_none():
    s = est.build_estimates_snapshot(
        [{"date": "2026-12-31", "epsAvg": 0.0, "numAnalystsEps": "x", "revenueAvg": True}], {}, FETCHED)
    assert s["eps_fy1"] == 0.0
    assert s["eps_fy1_analysts"] is None
    assert s["revenue_fy1"] is None
    assert s["eps_fy2"] is None


def _snap(e1, p1, e2=None, p2=None):
    return {"eps_fy1": e1, "eps_fy1_period_end": p1, "eps_fy2": e2, "eps_fy2_period_end": p2}


@pytest.mark.unit
def test_revision_basic_and_identical():
    prev = _snap(2.0, "2026-12-31", 3.0, "2027-12-31")
    curr = _snap(2.2, "2026-12-31", 2.7, "2027-12-31")
    assert est.eps_revision_pct(prev, curr) == {"fy1": 10.0, "fy2": -10.0}
    assert est.eps_revision_pct(prev, prev) == {"fy1": 0.0, "fy2": 0.0}


@pytest.mark.unit
def test_revision_matches_by_period_after_rollover():
    prev = _snap(2.0, "2026-12-31", 3.0, "2027-12-31")
    curr = _snap(3.3, "2027-12-31", 4.0, "2028-12-31")
    assert est.eps_revision_pct(prev, curr) == {"fy1": 10.0, "fy2": None}


@pytest.mark.unit
@pytest.mark.parametrize("prev,curr", [
    (None, _snap(1, "2026-12-31")),
    (_snap(1, "2026-12-31"), None),
    (_snap(0.0, "2026-12-31"), _snap(1, "2026-12-31")),
    (_snap(-1.0, "2026-12-31"), _snap(1, "2026-12-31")),
    (_snap(None, "2026-12-31"), _snap(1, "2026-12-31")),
    (_snap(True, "2026-12-31"), _snap(1, "2026-12-31")),
    (_snap("x", "2026-12-31"), _snap(1, "2026-12-31")),
    (_snap(1, None), _snap(1, "2026-12-31")),
    (_snap(1, "2025-12-31"), _snap(1, "2026-12-31")),
    ("junk", {}),
])
def test_revision_undefined_cases_are_none(prev, curr):
    assert est.eps_revision_pct(prev, curr) == {"fy1": None, "fy2": None}


@pytest.mark.unit
def test_vendored_copies_identical():
    ref = open(os.path.join(LAMBDAS_DIR, "shared", "estimates.py"), "rb").read()
    for lam in ("enrichment", "score-calculator"):
        assert open(os.path.join(LAMBDAS_DIR, lam, "estimates.py"), "rb").read() == ref


# ---------------------------------------------------------------------------
# Enrichment: shares_implied, estimates snapshot, LATEST persistence
# ---------------------------------------------------------------------------
from decimal import Decimal  # noqa: E402
from conftest import load_handler  # noqa: E402

enrichment = load_handler("enrichment")


def _enrich(profile, estimates=None, targets=None, price=47.44):
    stock = {"symbol": "HLIO", "price": price, "market_cap": 1_640_280_868}
    enrichment.enrich_with_fmp(stock, {}, {}, estimates if estimates is not None else _ests(),
                               targets or {"lastQuarterAvgPriceTarget": 60.0, "lastQuarterCount": 3},
                               profile, None)
    return stock


@pytest.mark.unit
def test_enrich_sets_shares_implied_and_market_cap_from_profile():
    s = _enrich({"marketCap": 1_700_000_000, "price": 50.0})
    assert s["shares_implied"] == pytest.approx(34_000_000)
    assert s["market_cap"] == 1_700_000_000


@pytest.mark.unit
@pytest.mark.parametrize("profile", [
    {"marketCap": 1_700_000_000},                     # no price: never fall back to Polygon T-1
    {"marketCap": 1_700_000_000, "price": 0},
    {"marketCap": 1_700_000_000, "price": -3},
    {"price": 50.0},                                  # no market cap
    {},
])
def test_enrich_shares_implied_none_when_profile_unusable(profile):
    s = _enrich(profile)
    assert s["shares_implied"] is None
    # No Polygon-price fallback: market_cap is whatever the profile/EDGAR gave, never recomputed here.
    assert s["market_cap"] == profile.get("marketCap", 1_640_280_868)


@pytest.mark.unit
def test_enrich_attaches_estimates_snapshot_without_new_calls():
    s = _enrich({"marketCap": 1, "price": 1})
    snap = s["estimates"]
    assert snap["eps_fy1"] == 2.5 and snap["eps_fy2"] == 3.0
    assert snap["target_price_avg"] == 60.0
    assert snap["fetched_at"].endswith("Z")


def _seed_latest(table, symbol="HLIO", **extra):
    item = {"PK": f"STOCK#{symbol}", "SK": "LATEST", "symbol": symbol, "price": Decimal("49.48")}
    item.update(extra)
    table.put_item(Item=item)


def _latest(table, symbol="HLIO"):
    return table.get_item(Key={"PK": f"STOCK#{symbol}", "SK": "LATEST"}).get("Item")


@pytest.fixture
def ddb(dynamodb_table, monkeypatch):
    return dynamodb_table


@pytest.mark.unit
def test_persist_snapshot_sets_estimates_and_shares(ddb):
    _seed_latest(ddb)
    stock = _enrich({"marketCap": 1_700_000_000, "price": 50.0})
    enrichment._persist_enrichment_snapshot_to_latest(stock)
    item = _latest(ddb)
    assert item["estimates"]["eps_fy1"] == Decimal("2.5")
    assert item["shares_implied"] == Decimal("34000000.0")
    assert item["price"] == Decimal("49.48")  # untouched


@pytest.mark.unit
def test_persist_snapshot_removes_stale_shares_implied(ddb):
    _seed_latest(ddb, shares_implied=Decimal("34000000"))
    stock = _enrich({})  # profile lacks price -> shares_implied None
    enrichment._persist_enrichment_snapshot_to_latest(stock)
    item = _latest(ddb)
    assert "shares_implied" not in item
    assert "estimates" in item


@pytest.mark.unit
def test_persist_snapshot_does_not_create_latest_for_untracked(ddb):
    stock = _enrich({"marketCap": 1, "price": 1})
    enrichment._persist_enrichment_snapshot_to_latest(stock)  # must not raise
    assert _latest(ddb) is None


@pytest.mark.unit
def test_persist_snapshot_failure_only_warns(monkeypatch, capsys):
    monkeypatch.setenv("DATA_TABLE_NAME", "no-such-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    stock = _enrich({"marketCap": 1, "price": 1})
    enrichment._persist_enrichment_snapshot_to_latest(stock)
    assert "WARN" in capsys.readouterr().out


@pytest.mark.regression
def test_refresh_latest_valuation_no_longer_writes_price(ddb):
    _seed_latest(ddb)
    enrichment._refresh_latest_valuation(
        {"symbol": "HLIO", "price": 47.44, "pe_ratio": 12.5, "price_to_fcf": 9.0})
    item = _latest(ddb)
    assert item["price"] == Decimal("49.48")
    assert item["pe_ratio"] == Decimal("12.5")
    assert item["price_to_fcf"] == Decimal("9.0")


# ---------------------------------------------------------------------------
# Step 7: persistence of price_as_of / estimates, bypass backfill from LATEST
# ---------------------------------------------------------------------------
from datetime import datetime, timezone  # noqa: E402

score_calc = load_handler("score-calculator")


@pytest.fixture
def sc7(dynamodb_table, monkeypatch):
    import boto3
    monkeypatch.setattr(score_calc, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
    monkeypatch.setattr(score_calc, "get_fmp_key", lambda: "K")
    monkeypatch.setattr(score_calc.time, "sleep", lambda *_: None)
    return dynamodb_table


def _scored(**kw):
    s = {
        "symbol": "HLIO", "passes_screen": True, "price": 49.48, "price_as_of": "2026-11-09",
        "fundamental_score": 80, "investability_score": 76, "pe_ratio": 12.0, "debt_to_equity": 0.4,
        "sentiment": {"sentiment_score": 0.1, "confidence": 0.5},
        "competition": {"competition_score": 2, "hhi_score": 2}, "risk_ledger": [],
        "estimates": est.build_estimates_snapshot(_ests(), {"lastQuarterAvgPriceTarget": 60.0},
                                                  "2026-11-09T22:05:00Z"),
        "shares_implied": 34_000_000.0,
    }
    s.update(kw)
    return s


@pytest.mark.unit
def test_persist_writes_score_row_with_new_fields_and_existing_unchanged(sc7):
    score_calc.persist_to_dynamodb([_scored()], "2026-11-09")
    row = sc7.get_item(Key={"PK": "STOCK#HLIO", "SK": "SCORE#2026-11-09"})["Item"]
    assert row["price_as_of"] == "2026-11-09"
    assert row["estimates_as_of"] == "2026-11-09"
    assert row["estimates"]["eps_fy1"] == Decimal("2.5")
    # existing fields unchanged
    assert row["price"] == Decimal("49.48") and row["investability_score"] == 76
    assert row["pe_ratio"] == Decimal("12.0") and row["debt_to_equity"] == Decimal("0.4")
    latest = sc7.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    assert latest["price_as_of"] == "2026-11-09"
    assert latest["shares_implied"] == Decimal("34000000.0")
    assert latest["estimates"]["eps_fy2"] == Decimal("3.0")


@pytest.mark.unit
def test_estimates_as_of_uses_new_york_date(sc7):
    # 2026-11-10T02:30Z is 21:30 EST on 11-09
    snap = est.build_estimates_snapshot(_ests(), {}, "2026-11-10T02:30:00Z")
    score_calc.persist_to_dynamodb([_scored(estimates=snap)], "2026-11-09")
    row = sc7.get_item(Key={"PK": "STOCK#HLIO", "SK": "SCORE#2026-11-09"})["Item"]
    assert row["estimates_as_of"] == "2026-11-09"


@pytest.mark.unit
def test_none_snapshot_still_written(sc7):
    score_calc.persist_to_dynamodb([_scored(estimates=None)], "2026-11-09")
    row = sc7.get_item(Key={"PK": "STOCK#HLIO", "SK": "SCORE#2026-11-09"})["Item"]
    assert row["estimates"]["eps_fy1"] is None
    assert "estimates_as_of" not in row


@pytest.mark.regression
def test_grace_enriched_but_failed_stock_gets_recomputed_market_cap(sc7):
    """GRACE stock: enrichment stored estimates/shares_implied on LATEST; Step 7 bypass
    loads them (no Decimals left) and the backfill recomputes market_cap on the new price."""
    sc7.put_item(Item={
        "PK": "STOCK#HLIO", "SK": "LATEST", "symbol": "HLIO",
        "market_cap": Decimal("1640280868"), "shares_implied": Decimal("34000000"),
        "estimates": {"fetched_at": "2026-11-05T22:05:00Z", "eps_fy1": Decimal("2.5"),
                      "eps_fy1_period_end": "2026-12-31"},
    })
    sc7.put_item(Item={"PK": "PRICE_HISTORY#HLIO", "SK": "DAILY", "symbol": "HLIO",
                       "last_final_date": "2026-11-09", "fetched_at": "2026-11-09T22:05:00Z",
                       "bars": [{"d": "2026-11-09", "c": Decimal("49.48")}]})
    stocks = [{"symbol": "HLIO"}]  # bypassed Steps 2-4: no fundamental_score
    score_calc.backfill_from_latest(stocks)
    score_calc.backfill_price_history(
        stocks, "2026-11-09", now_utc=datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc))
    assert stocks[0]["market_cap"] == round(34_000_000.0 * 49.48)
    assert stocks[0]["estimates"]["eps_fy1"] == 2.5 and isinstance(stocks[0]["estimates"]["eps_fy1"], float)
    score_calc.persist_to_dynamodb(stocks, "2026-11-09")
    row = sc7.get_item(Key={"PK": "STOCK#HLIO", "SK": "SCORE#2026-11-09"})["Item"]
    assert row["estimates_as_of"] == "2026-11-05"  # visibly stale
