"""
Unit tests for the shared US-Pacific business-date helper.

The app keys business dates (first_tracked, SCORE#{date}, mark_date, Days column,
market-gate holiday check) on the USER's timezone (America/Los_Angeles), NOT UTC
and NOT Eastern. Function names remain eastern_date/eastern_today for backwards
compat but return PACIFIC dates.

Must be dependency-free (works in Lambdas without tzdata) and DST-correct:
PDT = UTC-7 (2nd Sun Mar .. 1st Sun Nov), PST = UTC-8 otherwise.
"""
import datetime as dt

import pytest

from conftest import load_handler

et = load_handler("score-calculator")  # vendors et_date


def _utc(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc)


@pytest.mark.unit
class TestPacificDate:
    def test_pdt_summer_afternoon(self):
        # 2026-07-01 20:00 UTC = 13:00 PDT -> 2026-07-01
        assert et.eastern_date(_utc(2026, 7, 1, 20)) == "2026-07-01"

    def test_scheduled_run_1pm_pt(self):
        # Scheduled run 20:00 UTC = 13:00 PDT -> 2026-09-18
        assert et.eastern_date(_utc(2026, 9, 18, 20)) == "2026-09-18"

    def test_9pm_pt_is_still_same_pt_day(self):
        # THE BUG FIX: 2026-09-18 04:20 UTC = 2026-09-17 21:20 PDT -> 2026-09-17
        # (under ET this was 09-18; under PT it's correctly 09-17, matching what
        # the Pacific user saw that evening)
        assert et.eastern_date(_utc(2026, 9, 18, 4, 20)) == "2026-09-17"

    def test_pst_winter(self):
        # 2026-01-15 03:00 UTC = 2026-01-14 19:00 PST -> 2026-01-14
        assert et.eastern_date(_utc(2026, 1, 15, 3)) == "2026-01-14"

    def test_dst_spring_forward_boundary(self):
        # DST begins 2026-03-08. At 10:00 UTC we're past 2am local switch ->
        # PDT(-7) -> 03:00 PDT -> 2026-03-08
        assert et.eastern_date(_utc(2026, 3, 8, 10)) == "2026-03-08"

    def test_dst_fall_back_boundary(self):
        # DST ends 2026-11-01. 09:00 UTC after fall-back = PST(-8) -> 01:00 -> 2026-11-01
        assert et.eastern_date(_utc(2026, 11, 1, 9)) == "2026-11-01"


@pytest.mark.unit
class TestPacificToday:
    def test_today_returns_iso_date(self):
        dt.datetime.strptime(et.eastern_today(), "%Y-%m-%d")

    def test_days_tracked_boundary_uses_pt(self):
        # first_tracked PT 2026-09-18, now = 2026-09-19 04:00 UTC (21:00 PDT 09-18,
        # still 09-18 PT) -> 0 days -> NEW
        now = _utc(2026, 9, 19, 4)
        assert et.days_tracked("2026-09-18", now) == 0
        # next PT day: 2026-09-19 20:00 UTC = 13:00 PDT 09-19 -> 1 day
        now2 = _utc(2026, 9, 19, 20)
        assert et.days_tracked("2026-09-18", now2) == 1
