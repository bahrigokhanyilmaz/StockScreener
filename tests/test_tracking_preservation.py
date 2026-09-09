"""
Integration + regression tests for TRACKING-item field preservation.

PRODUCTION BUGS:
  1. Mark wipe — the daily pipeline rewrites the TRACKING item in BOTH the
     score-calculator (Step 7) and the alert-checker (Step 8). Step 8 dropped
     mark_price/mark_date, wiping the user's manual mark overnight so
     "Since Mark" reset.
  2. Days reset — the same Step 8 rewrite reset first_tracked to today when the
     in-memory `prev` was partial, resetting the "Days" column to 1.

These tests exercise the REAL alert-checker update_tracking_in_dynamodb against
a mocked DynamoDB table and assert that mark_price, mark_date, and first_tracked
survive the rewrite for a still-ACTIVE stock (the exact scenario that failed).
"""
from decimal import Decimal

import pytest

from conftest import load_handler

alert_checker = load_handler("alert-checker")


def _seed_marked_tracking(table, symbol, mark_price, mark_date, first_tracked):
    table.put_item(Item={
        "PK": f"STOCK#{symbol}",
        "SK": "TRACKING",
        "symbol": symbol,
        "tracking_status": "ACTIVE",
        "first_tracked": first_tracked,
        "mark_price": Decimal(str(mark_price)),
        "mark_date": mark_date,
        "last_updated": "2026-09-05T20:00:00+00:00",
    })


def _get_tracking(table, symbol):
    return table.get_item(Key={"PK": f"STOCK#{symbol}", "SK": "TRACKING"}).get("Item", {})


@pytest.mark.regression
def test_mark_survives_alert_checker_rewrite(dynamodb_table):
    """A marked, still-ACTIVE stock keeps mark_price/mark_date after Step 8."""
    _seed_marked_tracking(dynamodb_table, "CRUS", 113.13, "2026-09-05", "2026-09-05")

    # Step 8 rewrites the TRACKING item for a stock that still passes (ACTIVE).
    updates = [{
        "symbol": "CRUS", "status": "ACTIVE",
        "last_passed": "2026-09-09", "first_tracked": "2026-09-09",  # note: partial prev -> today
    }]
    alert_checker.update_tracking_in_dynamodb(dynamodb_table, updates, "2026-09-09")

    item = _get_tracking(dynamodb_table, "CRUS")
    assert item.get("mark_price") == Decimal("113.13"), "mark_price must survive Step 8"
    assert item.get("mark_date") == "2026-09-05", "mark_date must survive Step 8"


@pytest.mark.regression
def test_first_tracked_not_regressed_to_today(dynamodb_table):
    """
    first_tracked must keep its original date even when the update dict carries
    a defaulted 'today' (the partial-prev scenario that reset the Days column).
    """
    _seed_marked_tracking(dynamodb_table, "AUPH", 16.29, "2026-09-05", "2026-09-02")

    updates = [{
        "symbol": "AUPH", "status": "ACTIVE",
        "last_passed": "2026-09-09", "first_tracked": "2026-09-09",  # regression trap
    }]
    alert_checker.update_tracking_in_dynamodb(dynamodb_table, updates, "2026-09-09")

    item = _get_tracking(dynamodb_table, "AUPH")
    assert item.get("first_tracked") == "2026-09-02", "original first_tracked must not regress to today"


@pytest.mark.integration
def test_unmarked_stock_has_no_mark_fields(dynamodb_table):
    """An unmarked ACTIVE stock should simply not carry mark fields."""
    dynamodb_table.put_item(Item={
        "PK": "STOCK#XYZ", "SK": "TRACKING", "symbol": "XYZ",
        "tracking_status": "ACTIVE", "first_tracked": "2026-09-02",
        "last_updated": "2026-09-05T20:00:00+00:00",
    })
    updates = [{"symbol": "XYZ", "status": "ACTIVE",
                "last_passed": "2026-09-09", "first_tracked": "2026-09-02"}]
    alert_checker.update_tracking_in_dynamodb(dynamodb_table, updates, "2026-09-09")

    item = _get_tracking(dynamodb_table, "XYZ")
    assert item.get("mark_price") is None
    assert item.get("first_tracked") == "2026-09-02"
