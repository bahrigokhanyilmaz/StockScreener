"""Unit tests for lambdas/shared/market_time.py (bar finality, NY clock)."""
import os
from datetime import datetime, timezone

import pytest
from conftest import LAMBDAS_DIR, load_handler  # noqa: F401  (conftest import sets path)

import sys
sys.path.insert(0, os.path.join(LAMBDAS_DIR, "shared"))
import market_time as mt  # noqa: E402


def utc(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


@pytest.mark.unit
def test_new_york_date_edt_and_est():
    # 2026-07-01 03:30 UTC = 23:30 EDT on 06-30
    assert mt.new_york_date(utc(2026, 7, 1, 3, 30)) == "2026-06-30"
    # 2026-11-10 04:30 UTC = 23:30 EST on 11-09
    assert mt.new_york_date(utc(2026, 11, 10, 4, 30)) == "2026-11-09"


@pytest.mark.unit
def test_bar_final_boundary_edt():
    # Mon 2026-06-01: 16:15 EDT == 20:15 UTC
    assert not mt.is_bar_final("2026-06-01", utc(2026, 6, 1, 20, 14, 59))
    assert mt.is_bar_final("2026-06-01", utc(2026, 6, 1, 20, 15, 0))


@pytest.mark.unit
def test_bar_final_boundary_est():
    # Mon 2026-11-09: 16:15 EST == 21:15 UTC
    assert not mt.is_bar_final("2026-11-09", utc(2026, 11, 9, 21, 14, 59))
    assert mt.is_bar_final("2026-11-09", utc(2026, 11, 9, 21, 15, 0))


@pytest.mark.unit
def test_hlio_scenario_20_25_utc_in_winter_is_not_final():
    # 20:25 UTC on 2026-11-09 is 15:25 EST -> provisional
    assert not mt.is_bar_final("2026-11-09", utc(2026, 11, 9, 20, 25))


@pytest.mark.unit
def test_past_future_weekend_invalid():
    now = utc(2026, 11, 9, 15, 0)
    assert mt.is_bar_final("2026-11-06", now)
    assert not mt.is_bar_final("2026-11-10", now)
    assert not mt.is_bar_final("garbage", now)
    assert not mt.is_bar_final(None, now)
    assert not mt.is_bar_final("2026-13-45", now)


@pytest.mark.unit
def test_weekend_today_not_final_but_friday_is():
    sat = utc(2026, 11, 7, 18, 0)
    assert mt.is_bar_final("2026-11-06", sat)
    assert not mt.is_bar_final("2026-11-07", sat)


@pytest.mark.unit
def test_latest_final_session_date():
    # Mon morning -> previous Friday
    assert mt.latest_final_session_date(utc(2026, 11, 9, 15, 0)) == "2026-11-06"
    # Mon after close -> Mon
    assert mt.latest_final_session_date(utc(2026, 11, 9, 22, 0)) == "2026-11-09"
    # Sunday -> Friday
    assert mt.latest_final_session_date(utc(2026, 11, 8, 18, 0)) == "2026-11-06"


@pytest.mark.unit
def test_holiday_sequence_latest_is_last_real_bar():
    # Thanksgiving Thu 2026-11-26; Fri morning: latest final session names Thu (a holiday).
    # Safe by design: no bar exists for it, so the newest stored final bar stays Wed.
    assert mt.latest_final_session_date(utc(2026, 11, 27, 15, 0)) == "2026-11-26"
    bars = ["2026-11-24", "2026-11-25"]  # vendor returns no 11-26 bar
    now = utc(2026, 11, 27, 15, 0)
    final = [b for b in bars if mt.is_bar_final(b, now)]
    assert final[-1] == "2026-11-25"


@pytest.mark.unit
def test_dst_transition_days():
    # Spring forward Sun 2026-03-08 at 07:00 UTC
    assert mt.new_york_dt(utc(2026, 3, 8, 6, 59)).hour == 1   # EST
    assert mt.new_york_dt(utc(2026, 3, 8, 7, 0)).hour == 3    # EDT
    # Fall back Sun 2026-11-01 at 06:00 UTC
    assert mt.new_york_dt(utc(2026, 11, 1, 5, 59)).hour == 1  # EDT
    assert mt.new_york_dt(utc(2026, 11, 1, 6, 0)).hour == 1   # EST (repeated hour)


@pytest.mark.unit
def test_previous_weekday():
    assert mt.previous_weekday("2026-11-09") == "2026-11-06"
    assert mt.previous_weekday("2026-11-10") == "2026-11-09"
    assert mt.previous_weekday("2026-11-08") == "2026-11-06"


@pytest.mark.unit
def test_parse_utc_iso():
    assert mt.parse_utc_iso("2026-11-09T20:25:42Z") == utc(2026, 11, 9, 20, 25, 42)
    assert mt.parse_utc_iso("2026-11-09T20:25:42+00:00") == utc(2026, 11, 9, 20, 25, 42)
    assert mt.parse_utc_iso("nope") is None
    assert mt.parse_utc_iso(None) is None
    assert mt.parse_utc_iso("") is None


@pytest.mark.unit
def test_vendored_copy_is_identical():
    a = open(os.path.join(LAMBDAS_DIR, "shared", "market_time.py"), "rb").read()
    b = open(os.path.join(LAMBDAS_DIR, "score-calculator", "market_time.py"), "rb").read()
    assert a == b
