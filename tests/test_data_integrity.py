"""
Tests for the data-integrity audit logic (scripts/audit_data_integrity.py).

Verifies the audit itself has teeth: a fully-valid stock produces no issues,
and each invariant violation is flagged.
"""
import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "audit_data_integrity", os.path.join(REPO, "scripts", "audit_data_integrity.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _valid_stock():
    # A stock that passes every documented invariant.
    return {
        "symbol": "GOOD", "peg_ratio": 0.5, "pe_ratio": 12.0, "forward_pe": 15.0,
        "debt_to_equity": 0.4, "interest_coverage_ratio": 8.0, "quick_ratio": 1.5,
        "operating_margin": 0.18, "revenue_growth_yoy": 0.10, "est_lt_revenue_growth": 0.08,
        "est_lt_growth": 0.30, "price": 40.0, "market_cap": 2_000_000_000, "price_to_fcf": 12.0,
        "investability_score": 70, "fundamental_score": 65, "sentiment_score": 0.2,
        "competition_score": 2, "first_tracked": "2026-09-09",
        "last_updated": "2026-09-15T20:00:00+00:00",
        "tracking_status": "ACTIVE",
    }


TODAY = __import__("datetime").date(2026, 9, 15)


@pytest.mark.unit
def test_valid_stock_has_no_issues():
    assert audit.audit_tracked_stocks([_valid_stock()], today=TODAY) == []


@pytest.mark.unit
@pytest.mark.parametrize("field,value,sev", [
    ("peg_ratio", None, "HIGH"),
    ("peg_ratio", 1.5, "HIGH"),
    ("est_lt_revenue_growth", -0.05, "HIGH"),
    ("est_lt_revenue_growth", None, "HIGH"),
    ("quick_ratio", 0.8, "HIGH"),
    ("price_to_fcf", 25.0, "HIGH"),
    ("price", 0, "HIGH"),
    ("market_cap", 100_000_000, "MED"),
    ("investability_score", 130, "MED"),
    ("competition_score", 9, "MED"),
])
def test_violation_is_flagged(field, value, sev):
    s = _valid_stock()
    s[field] = value
    issues = audit.audit_tracked_stocks([s], today=TODAY)
    assert any(i["severity"] == sev for i in issues), f"{field}={value} not flagged as {sev}"


@pytest.mark.unit
def test_de_override_by_icr_passes():
    s = _valid_stock(); s["debt_to_equity"] = 1.5; s["interest_coverage_ratio"] = 5.0
    assert audit.audit_tracked_stocks([s], today=TODAY) == []


@pytest.mark.unit
def test_de_high_without_icr_flagged():
    s = _valid_stock(); s["debt_to_equity"] = 1.5; s["interest_coverage_ratio"] = 1.0
    assert any("D/E" in i["message"] for i in audit.audit_tracked_stocks([s], today=TODAY))


@pytest.mark.unit
def test_opmargin_override_by_revgrowth_passes():
    s = _valid_stock(); s["operating_margin"] = -0.05; s["revenue_growth_yoy"] = 0.25
    assert audit.audit_tracked_stocks([s], today=TODAY) == []


@pytest.mark.unit
def test_stale_last_updated_flagged():
    s = _valid_stock(); s["last_updated"] = "2026-09-01T20:00:00+00:00"
    assert any("stale" in i["message"] for i in audit.audit_tracked_stocks([s], today=TODAY))


@pytest.mark.unit
def test_peg_inconsistent_with_forward_inputs_flagged():
    s = _valid_stock(); s["peg_ratio"] = 0.5; s["forward_pe"] = 30.0; s["est_lt_growth"] = 0.10
    # real = 30/(0.10*100)=3.0, stored 0.5 -> inconsistent
    assert any("inconsistent" in i["message"] for i in audit.audit_tracked_stocks([s], today=TODAY))


# --- ODD (advisory plausibility) checks ---

def _odd(issues):
    return [i for i in issues if i["severity"] == "ODD"]


@pytest.mark.unit
def test_odd_never_makes_valid_stock_dirty():
    # A valid stock with sane price history produces no issues at all.
    # Latest bar close must equal the stock price (same series).
    s = _valid_stock()
    bars = [{"c": 39.0}, {"c": 41.0}, {"c": 40.5}, {"c": 39.5}, {"c": 40.0}]
    issues = audit.audit_tracked_stocks([s], today=TODAY, price_bars_by_symbol={"GOOD": bars})
    assert issues == []


@pytest.mark.unit
def test_odd_divergent_trailing_vs_forward_pe():
    # 5x apart; keep PEG consistent (fpe 12 / lt 0.30 = 0.4) so only ODD fires.
    s = _valid_stock(); s["pe_ratio"] = 60.0; s["forward_pe"] = 12.0; s["peg_ratio"] = 0.4
    issues = audit.audit_tracked_stocks([s], today=TODAY)
    assert any("differ" in i["message"] for i in _odd(issues))


@pytest.mark.unit
def test_odd_price_outside_30d_range():
    s = _valid_stock(); s["price"] = 80.0  # way above the ~40 range
    bars = [{"c": 39.0}, {"c": 40.0}, {"c": 41.0}, {"c": 40.5}, {"c": 39.5}]
    issues = audit.audit_tracked_stocks([s], today=TODAY, price_bars_by_symbol={"GOOD": bars})
    assert any("outside 30d range" in i["message"] for i in _odd(issues))


@pytest.mark.unit
def test_odd_price_within_range_ok():
    s = _valid_stock(); s["price"] = 40.5
    bars = [{"c": 39.0}, {"c": 40.0}, {"c": 41.0}, {"c": 39.5}, {"c": 40.5}]
    assert _odd(audit.audit_tracked_stocks([s], today=TODAY, price_bars_by_symbol={"GOOD": bars})) == []


@pytest.mark.regression
def test_price_mismatch_with_latest_bar_flagged():
    # MU bug: stock price stale vs latest price-history bar close.
    s = _valid_stock(); s["price"] = 855.10
    bars = [{"c": 884.74}, {"c": 833.59}, {"c": 855.10}, {"c": 797.64}]  # latest 797.64
    issues = audit.audit_tracked_stocks([s], today=TODAY, price_bars_by_symbol={"GOOD": bars})
    assert any("out of sync with Daily" in i["message"] and i["severity"] == "MED" for i in issues)


@pytest.mark.unit
def test_grace_stock_exempt_from_hard_filter_checks():
    # A GRACE stock is EXPECTED to fail a hard filter — must not be flagged HIGH.
    s = _valid_stock(); s["tracking_status"] = "GRACE"; s["price_to_fcf"] = 25.0; s["peg_ratio"] = 1.8
    issues = audit.audit_tracked_stocks([s], today=TODAY)
    assert not any(i["severity"] == "HIGH" for i in issues)


@pytest.mark.unit
def test_active_stock_still_flagged_for_hard_filter():
    s = _valid_stock(); s["tracking_status"] = "ACTIVE"; s["price_to_fcf"] = 25.0
    issues = audit.audit_tracked_stocks([s], today=TODAY)
    assert any(i["severity"] == "HIGH" and "price_to_fcf" in i["message"] for i in issues)


@pytest.mark.unit
def test_price_matches_latest_bar_ok():
    s = _valid_stock(); s["price"] = 797.64
    bars = [{"c": 884.74}, {"c": 833.59}, {"c": 855.10}, {"c": 797.64}]
    issues = audit.audit_tracked_stocks([s], today=TODAY, price_bars_by_symbol={"GOOD": bars})
    assert not any("out of sync with Daily" in i["message"] for i in issues)


@pytest.mark.unit
def test_odd_does_not_gate_exit_semantics():
    # An ODD-only issue list should not be treated as gating. Keep PEG consistent
    # with forward_pe/(lt*100) so only the divergent-P/E ODD fires.
    # forward_pe=12, lt=0.30 -> PEG should be 0.4.
    s = _valid_stock(); s["pe_ratio"] = 60.0; s["forward_pe"] = 12.0; s["peg_ratio"] = 0.4
    issues = audit.audit_tracked_stocks([s], today=TODAY)
    assert any("differ" in i["message"] for i in issues if i["severity"] == "ODD")
    gating = [i for i in issues if i["severity"] in ("HIGH", "MED")]
    assert gating == []


# --- Bar finality / canonical price provenance ---
from datetime import datetime as _dt, timezone as _tz  # noqa: E402


def _bar(d, c=40.0):
    return {"d": d, "o": c, "h": c, "l": c, "c": c, "v": 1}


def _bars_ending(d, c=40.0):
    return [_bar("2026-11-03", c), _bar("2026-11-04", c), _bar("2026-11-05", c),
            _bar("2026-11-06", c), _bar(d, c)]


NOW_AFTER = _dt(2026, 11, 9, 22, 30, tzinfo=_tz.utc)  # 17:30 EST Monday


def _audit(s, bars, now=NOW_AFTER, meta=None):
    return audit.audit_tracked_stocks([s], today=_dt(2026, 11, 9).date(),
                                      price_bars_by_symbol={"GOOD": bars}, now_utc=now,
                                      price_meta_by_symbol={"GOOD": meta} if meta else None)


def _msgs(issues, sev):
    return [i["message"] for i in issues if i["severity"] == sev]


@pytest.mark.regression
def test_provisional_latest_bar_flagged_med_hlio_scenario():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T20:25:42Z"   # 15:25 EST, before the close
    s["price_as_of"] = "2026-11-09"
    meta = {"fetched_at": "2026-11-09T20:25:40Z"}  # fetched before finality, no last_final_date
    assert any("provisional" in m for m in _msgs(_audit(s, _bars_ending("2026-11-09"), meta=meta), "MED"))


@pytest.mark.regression
def test_repaired_series_not_flagged_despite_old_last_updated():
    # Repair replaced the bar with a final one but left LATEST.last_updated untouched.
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T20:25:42Z"
    s["price_as_of"] = "2026-11-09"
    meta = {"fetched_at": "2026-11-10T03:00:00Z", "last_final_date": "2026-11-09"}
    assert _audit(s, _bars_ending("2026-11-09"), meta=meta) == []


@pytest.mark.unit
def test_legacy_series_without_provenance_never_med():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T20:25:42Z"
    s["price_as_of"] = "2026-11-09"
    issues = _audit(s, _bars_ending("2026-11-09"), meta={"fetched_at": None, "last_final_date": None})
    assert not _msgs(issues, "MED")
    assert any("legacy" in m for m in _msgs(issues, "ODD"))


@pytest.mark.unit
def test_normal_post_close_pipeline_write_not_flagged():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T22:15:00Z"
    s["price_as_of"] = "2026-11-09"
    meta = {"fetched_at": "2026-11-09T22:14:50Z", "last_final_date": "2026-11-09"}
    assert _audit(s, _bars_ending("2026-11-09"), meta=meta) == []


@pytest.mark.unit
def test_final_latest_bar_not_flagged():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T22:15:00Z"   # 17:15 EST
    s["price_as_of"] = "2026-11-09"
    assert _audit(s, _bars_ending("2026-11-09")) == []


@pytest.mark.unit
def test_unparseable_last_updated_skips_provisional_rule():
    s = _valid_stock()
    s["last_updated"] = "garbage"
    s["price_as_of"] = "2026-11-09"
    assert not any("provisional" in m for m in _msgs(_audit(s, _bars_ending("2026-11-09")), "MED"))


@pytest.mark.unit
def test_price_as_of_mismatch_flagged_med():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T22:15:00Z"
    s["price_as_of"] = "2026-11-06"
    assert any("price_as_of" in m for m in _msgs(_audit(s, _bars_ending("2026-11-09")), "MED"))


@pytest.mark.unit
def test_price_as_of_missing_is_odd_not_gating():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T22:15:00Z"
    issues = _audit(s, _bars_ending("2026-11-09"))
    assert any("price_as_of missing" in m for m in _msgs(issues, "ODD"))
    assert not [i for i in issues if i["severity"] in ("HIGH", "MED")]


@pytest.mark.unit
def test_stale_bars_flagged_odd():
    s = _valid_stock()
    s["last_updated"] = "2026-11-09T22:15:00Z"
    s["price_as_of"] = "2026-11-05"
    issues = _audit(s, [_bar(d) for d in ("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05")])
    assert any("stale" in m for m in _msgs(issues, "ODD"))
