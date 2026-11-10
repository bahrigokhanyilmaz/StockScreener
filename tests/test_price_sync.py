"""
Regression: Price / "Since Mark" must use the SAME latest close as "Daily".

Bug: LATEST.price came from Polygon (lagging) while the price-history bars came
from FMP (fresher), so Price/Since-Mark showed an older price than Daily (e.g.
MU: price 855.10 but latest bar 797.64). Fix: backfill_price_history syncs each
stock's price to the most recent bar close.

This tests the already-backfilled-today path, which reads existing bars from the
table and syncs price without any HTTP call.
"""
import os
from datetime import datetime, timezone

import pytest
from decimal import Decimal
from conftest import load_handler

sc = load_handler("score-calculator")


@pytest.mark.regression
def test_price_synced_to_latest_bar_close(dynamodb_table, monkeypatch):
    import boto3
    mocked = boto3.resource("dynamodb", region_name="us-east-2")
    monkeypatch.setattr(sc, "dynamodb", mocked)
    monkeypatch.setattr(sc, "get_fmp_key", lambda: "TEST_KEY")
    monkeypatch.setenv("DATA_TABLE_NAME", "stock-screener-data")

    today = "2026-11-04"
    # Existing price history already backfilled today, latest close 797.64.
    dynamodb_table.put_item(Item={
        "PK": "PRICE_HISTORY#MU", "SK": "DAILY", "symbol": "MU",
        "last_final_date": today,
        "fetched_at": "2026-11-04T22:30:00Z",
        "bars": [
            {"d": "2026-11-03", "c": Decimal("855.10")},
            {"d": "2026-11-04", "c": Decimal("797.64")},
        ],
    })

    # Stock still carries the stale Polygon price.
    stocks = [{"symbol": "MU", "price": 855.10}]
    latest_close = sc.backfill_price_history(
        stocks, today, now_utc=datetime(2026, 11, 4, 23, 0, tzinfo=timezone.utc))  # 18:00 EST

    assert latest_close["MU"] == 797.64
    assert stocks[0]["price"] == 797.64  # synced to the latest bar
