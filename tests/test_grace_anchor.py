"""
Grace anchor (grace_start) tests.

Root-cause bug: score-calculator wrote last_passed=None and no grace_start for
GRACE stocks every run, so the removal clock had no durable anchor and GRACE
stocks never expired. These tests lock in the fix via the alert-checker expiry
math (the pure, testable part): expiry counts from grace_start.
"""
import datetime
import pytest
from conftest import load_handler

ac = load_handler("alert-checker")


def _days_in_grace(grace_start, today):
    gs = datetime.datetime.strptime(grace_start, "%Y-%m-%d")
    t = datetime.datetime.strptime(today, "%Y-%m-%d")
    return (t - gs).days


@pytest.mark.regression
def test_check_tracking_changes_sets_grace_start_on_entry():
    # ACTIVE -> fails this run -> enters GRACE, grace_start = today
    scored = [{"symbol": "X", "passes_screen": False, "investability_score": 40}]
    prev = {"X": {"tracking_status": "ACTIVE", "last_passed": "2026-10-01", "first_tracked": "2026-09-01"}}
    _alerts, updates = ac.check_tracking_changes(scored, prev, "2026-10-15")
    u = next(u for u in updates if u["symbol"] == "X")
    assert u["status"] == "GRACE"
    assert u["grace_start"] == "2026-10-15"


@pytest.mark.regression
def test_grace_expires_after_30_days_from_grace_start():
    # In GRACE 31 days -> EXPIRED
    scored = [{"symbol": "X", "passes_screen": False, "investability_score": 40}]
    prev = {"X": {"tracking_status": "GRACE", "last_passed": "", "grace_start": "2026-09-14",
                  "first_tracked": "2026-08-01"}}
    _alerts, updates = ac.check_tracking_changes(scored, prev, "2026-10-15")  # 31 days later
    u = next(u for u in updates if u["symbol"] == "X")
    assert u["status"] == "EXPIRED"


@pytest.mark.regression
def test_grace_not_expired_before_30_days():
    scored = [{"symbol": "X", "passes_screen": False, "investability_score": 40}]
    prev = {"X": {"tracking_status": "GRACE", "last_passed": "", "grace_start": "2026-10-01",
                  "first_tracked": "2026-08-01"}}
    _alerts, updates = ac.check_tracking_changes(scored, prev, "2026-10-15")  # 14 days
    u = next(u for u in updates if u["symbol"] == "X")
    assert u["status"] == "GRACE"


@pytest.mark.unit
def test_grace_start_anchor_counts_correctly():
    assert _days_in_grace("2026-09-15", "2026-10-15") == 30
