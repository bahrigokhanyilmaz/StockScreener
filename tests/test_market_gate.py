"""
Unit tests for the market-gate (Step 0) open/closed decision.

Guards the frugality feature: the pipeline must skip weekends and market
holidays (data-driven via Polygon's upcoming-holidays calendar) but must NOT
skip a real trading day, and must fail OPEN on API errors.
"""
import datetime as real_datetime

import pytest

from conftest import load_handler

market_gate = load_handler("market-gate")

UPCOMING = "https://api.polygon.io/v1/marketstatus/upcoming"

HOLIDAYS = [
    {"date": "2026-09-07", "exchange": "NYSE", "name": "Labor Day", "status": "closed"},
    {"date": "2026-09-07", "exchange": "NASDAQ", "name": "Labor Day", "status": "closed"},
    {"date": "2026-11-26", "exchange": "NYSE", "name": "Thanksgiving", "status": "closed"},
]


def _freeze_now(monkeypatch, y, m, d):
    fixed = real_datetime.datetime(y, m, d, 20, 0, 0, tzinfo=real_datetime.timezone.utc)

    class _StubDateTime(real_datetime.datetime):
        # Subclass real datetime so strptime/etc. still work; only override now().
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(market_gate, "datetime", _StubDateTime)


@pytest.mark.unit
def test_is_holiday_true_for_labor_day(aws_env, requests_mock):
    requests_mock.get(UPCOMING, json=HOLIDAYS)
    holiday, name = market_gate.is_market_holiday("2026-09-07", "FAKE_KEY")
    assert holiday is True
    assert "Labor Day" in name


@pytest.mark.unit
def test_is_holiday_false_for_regular_weekday(aws_env, requests_mock):
    requests_mock.get(UPCOMING, json=HOLIDAYS)
    holiday, _ = market_gate.is_market_holiday("2026-09-08", "FAKE_KEY")
    assert holiday is False


@pytest.mark.unit
def test_is_holiday_fails_open_on_http_error(aws_env, requests_mock):
    requests_mock.get(UPCOMING, status_code=500)
    holiday, _ = market_gate.is_market_holiday("2026-09-08", "FAKE_KEY")
    assert holiday is False, "must fail open (never skip a real day) on API error"


@pytest.mark.unit
def test_handler_skips_weekend(aws_env, monkeypatch):
    # Saturday 2026-09-05
    _freeze_now(monkeypatch, 2026, 9, 5)
    result = market_gate.handler({}, None)
    assert result["market_open"] is False
    assert "weekend" in result["reason"].lower()


@pytest.mark.regression
def test_handler_skips_labor_day(aws_env, monkeypatch, requests_mock):
    # Monday 2026-09-07 = Labor Day
    _freeze_now(monkeypatch, 2026, 9, 7)
    monkeypatch.setattr(market_gate, "get_polygon_key", lambda: "FAKE_KEY")
    requests_mock.get(UPCOMING, json=HOLIDAYS)

    result = market_gate.handler({}, None)
    assert result["market_open"] is False
    assert "holiday" in result["reason"].lower()


@pytest.mark.unit
def test_handler_opens_on_trading_day(aws_env, monkeypatch, requests_mock):
    # Tuesday 2026-09-08, not a holiday
    _freeze_now(monkeypatch, 2026, 9, 8)
    monkeypatch.setattr(market_gate, "get_polygon_key", lambda: "FAKE_KEY")
    requests_mock.get(UPCOMING, json=HOLIDAYS)

    result = market_gate.handler({}, None)
    assert result["market_open"] is True
