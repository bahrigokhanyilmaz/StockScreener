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
    s = _valid_stock()
    bars = [{"c": 39.0}, {"c": 40.0}, {"c": 41.0}, {"c": 40.5}, {"c": 39.5}]
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
    bars = [{"c": 39.0}, {"c": 40.0}, {"c": 41.0}, {"c": 40.5}, {"c": 39.5}]
    assert _odd(audit.audit_tracked_stocks([s], today=TODAY, price_bars_by_symbol={"GOOD": bars})) == []


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
