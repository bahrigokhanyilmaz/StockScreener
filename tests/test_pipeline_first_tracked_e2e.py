"""
Integration/regression test reproducing the AUPH 'NEW' bug (2026-09-10):
a stock that was tracked yesterday (first_tracked=2026-09-09) reappeared as NEW
because the pipeline reset first_tracked to today.

This runs BOTH pipeline writers in sequence against a mocked DynamoDB:
  Step 7 score-calculator.persist_to_dynamodb  ->  Step 8 alert_checker.update_tracking_in_dynamodb
seeding AUPH with an existing first_tracked, and asserting it is preserved.
"""
from decimal import Decimal

import pytest

from conftest import load_handler

score_calc = load_handler("score-calculator")
alert_checker = load_handler("alert-checker")


@pytest.mark.regression
def test_first_tracked_preserved_across_full_pipeline(dynamodb_table, monkeypatch):
    # Point both handlers' module-level dynamodb resource at the mocked table.
    import boto3
    mocked = boto3.resource("dynamodb", region_name="us-east-2")
    monkeypatch.setattr(score_calc, "dynamodb", mocked)

    # Seed AUPH as tracked yesterday.
    dynamodb_table.put_item(Item={
        "PK": "STOCK#AUPH", "SK": "TRACKING", "symbol": "AUPH",
        "tracking_status": "ACTIVE", "first_tracked": "2026-09-09",
        "last_passed": "2026-09-09", "last_updated": "2026-09-09T20:25:00+00:00",
    })
    dynamodb_table.put_item(Item={
        "PK": "STOCK#AUPH", "SK": "LATEST", "symbol": "AUPH",
        "tracking_status": "ACTIVE", "first_tracked": "2026-09-09",
        "passes_screen": True, "price": Decimal("16.0"), "last_updated": "2026-09-09T20:25:00+00:00",
    })

    today = "2026-09-10"

    # --- Step 7: score-calculator persists (should preserve first_tracked) ---
    scored = [{
        "symbol": "AUPH", "passes_screen": True, "price": 16.08,
        "fundamental_score": 80, "investability_score": 76,
        "sentiment": {"sentiment_score": 0.1, "confidence": 0.5},
        "competition": {"competition_score": 2, "hhi_score": 2},
        "risk_ledger": [],
    }]
    score_calc.persist_to_dynamodb(scored, today)

    after_step7 = dynamodb_table.get_item(
        Key={"PK": "STOCK#AUPH", "SK": "TRACKING"}).get("Item", {})
    assert after_step7.get("first_tracked") == "2026-09-09", (
        f"Step 7 regressed first_tracked to {after_step7.get('first_tracked')}"
    )

    # --- Step 8: alert-checker rewrites TRACKING (should also preserve) ---
    updates = [{
        "symbol": "AUPH", "status": "ACTIVE",
        "last_passed": today, "first_tracked": today,  # partial-prev trap
    }]
    alert_checker.update_tracking_in_dynamodb(dynamodb_table, updates, today)

    after_step8 = dynamodb_table.get_item(
        Key={"PK": "STOCK#AUPH", "SK": "TRACKING"}).get("Item", {})
    assert after_step8.get("first_tracked") == "2026-09-09", (
        f"Step 8 regressed first_tracked to {after_step8.get('first_tracked')}"
    )


@pytest.mark.regression
def test_first_tracked_self_heals_from_score_history(dynamodb_table, monkeypatch):
    """
    AUPH bug: a continuously-tracked stock whose TRACKING item is missing
    first_tracked (transient read/write gap) must NOT reset to today. It must
    self-heal from the earliest immutable SCORE# snapshot.
    """
    import boto3
    mocked = boto3.resource("dynamodb", region_name="us-east-2")
    monkeypatch.setattr(score_calc, "dynamodb", mocked)

    # TRACKING item exists but has NO first_tracked (the failure condition).
    dynamodb_table.put_item(Item={
        "PK": "STOCK#AUPH", "SK": "TRACKING", "symbol": "AUPH",
        "tracking_status": "ACTIVE", "last_passed": "2026-09-09",
        "last_updated": "2026-09-09T20:25:00+00:00",
    })
    # But immutable history proves it was first tracked 2026-09-09.
    dynamodb_table.put_item(Item={
        "PK": "STOCK#AUPH", "SK": "SCORE#2026-09-09", "symbol": "AUPH", "date": "2026-09-09",
    })
    dynamodb_table.put_item(Item={
        "PK": "STOCK#AUPH", "SK": "SCORE#2026-09-10", "symbol": "AUPH", "date": "2026-09-10",
    })

    scored = [{
        "symbol": "AUPH", "passes_screen": True, "price": 16.08,
        "fundamental_score": 80, "investability_score": 76,
        "sentiment": {"sentiment_score": 0.1, "confidence": 0.5},
        "competition": {"competition_score": 2, "hhi_score": 2},
        "risk_ledger": [],
    }]
    score_calc.persist_to_dynamodb(scored, "2026-09-10")

    item = dynamodb_table.get_item(
        Key={"PK": "STOCK#AUPH", "SK": "TRACKING"}).get("Item", {})
    assert item.get("first_tracked") == "2026-09-09", (
        f"expected self-heal to 2026-09-09, got {item.get('first_tracked')}"
    )
