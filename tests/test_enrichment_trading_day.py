"""
Regression + unit tests for enrichment's trading-day resolution.

PRODUCTION BUG (2026-09-08): get_last_trading_day() skipped weekends but not
market holidays, and only checked the HTTP status code. On Labor Day Polygon
returns HTTP 200 with resultsCount:0 (empty). The pipeline picked the holiday
as the trading date, matched 0 prices, and cascaded into every stock failing
the screen (all GRACE), null P/E 50th, and flat "Since Mark".

These tests lock in the fix: the function must walk back to the most recent day
that ACTUALLY has price data (resultsCount > 0), skipping weekends AND holidays.
"""
import datetime as real_datetime
import re

import pytest

from conftest import load_handler

enrichment = load_handler("enrichment")

GROUPED_RE = re.compile(
    r"https://api\.polygon\.io/v2/aggs/grouped/locale/us/market/stocks/(\d{4}-\d{2}-\d{2})"
)


def _freeze_today(monkeypatch, y, m, d):
    """
    Freeze enrichment's `datetime.now(tz).date()` to a fixed date without
    breaking timedelta arithmetic. We replace the `datetime` name bound in the
    handler module with a stub whose .now() returns a real datetime at the
    fixed date; timedelta/date remain the real ones.
    """
    fixed = real_datetime.datetime(y, m, d, 21, 0, 0, tzinfo=real_datetime.timezone.utc)

    class _StubDateTime:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(enrichment, "datetime", _StubDateTime)


def _polygon_callback(trading_days_with_data):
    """
    requests_mock JSON callback: dates in `trading_days_with_data` return
    resultsCount>0; all other dates return HTTP 200 with resultsCount:0
    (Polygon's real holiday / no-data behavior).
    """
    def _cb(request, context):
        context.status_code = 200
        date = GROUPED_RE.match(request.url.split("?")[0]).group(1)
        if date in trading_days_with_data:
            return {
                "status": "OK",
                "resultsCount": 3,
                "results": [{"T": "AAPL", "c": 1.0}, {"T": "MSFT", "c": 2.0}, {"T": "F", "c": 3.0}],
            }
        return {"status": "OK", "resultsCount": 0, "results": []}
    return _cb


@pytest.mark.regression
def test_skips_holiday_returns_last_real_trading_day(aws_env, monkeypatch, requests_mock):
    # 'today' = Tue 2026-09-08. T-1 = Mon 09-07 (Labor Day, no data);
    # 09-06/09-05 weekend; 09-04 Fri has data. Must return 2026-09-04.
    _freeze_today(monkeypatch, 2026, 9, 8)
    requests_mock.get(GROUPED_RE, json=_polygon_callback({"2026-09-04"}))

    result = enrichment.get_last_trading_day("FAKE_KEY")
    assert result == "2026-09-04", f"expected Friday 09-04, got {result}"


@pytest.mark.regression
def test_never_returns_a_zero_result_weekday(aws_env, monkeypatch, requests_mock):
    _freeze_today(monkeypatch, 2026, 9, 8)
    requests_mock.get(GROUPED_RE, json=_polygon_callback({"2026-09-04"}))

    result = enrichment.get_last_trading_day("FAKE_KEY")
    assert result not in ("2026-09-07", "2026-09-08"), "must skip holiday/no-data weekdays"


@pytest.mark.unit
def test_returns_t_minus_1_when_it_has_data(aws_env, monkeypatch, requests_mock):
    # Wed 2026-09-09 -> T-1 = Tue 09-08, which has data.
    _freeze_today(monkeypatch, 2026, 9, 9)
    requests_mock.get(GROUPED_RE, json=_polygon_callback({"2026-09-08", "2026-09-04"}))

    result = enrichment.get_last_trading_day("FAKE_KEY")
    assert result == "2026-09-08"


@pytest.mark.unit
def test_no_key_falls_back_to_weekday_heuristic(aws_env, monkeypatch):
    # Sun 2026-09-06 -> T-1 = Sat 09-05 -> skip weekend to Fri 09-04.
    _freeze_today(monkeypatch, 2026, 9, 6)
    result = enrichment.get_last_trading_day(None)
    assert result == "2026-09-04"
