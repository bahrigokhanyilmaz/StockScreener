"""
Unit tests for the shared US-Eastern business-date helper.

The app keys business dates (first_tracked, SCORE#{date}, mark_date, Days column,
market-gate holiday check) on the US market timezone (America/New_York), NOT UTC.
Keying on UTC collapsed two same-UTC-day runs into one SCORE# snapshot and made
"NEW" stocks read "1" once UTC midnight passed.

The helper must be dependency-free (works in Lambdas that don't bundle tzdata)
and DST-correct: EDT = UTC-4 (2nd Sun Mar .. 1st Sun Nov), EST = UTC-5 otherwise.
"""
import datetime as dt

import pytest

from conftest import load_handler

# The helper lives in a shared module copied into each Lambda folder.
et = load_handler("score-calculator")  # score-calculator vendors et_date


def _utc(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc)


@pytest.mark.unit
class TestEasternDate:
    def test_edt_summer_afternoon(self):
        # 2026-07-01 18:00 UTC = 14:00 EDT -> 2026-07-01
        assert et.eastern_date(_utc(2026, 7, 1, 18)) == "2026-07-01"

    def test_edt_late_evening_still_same_et_day(self):
        # 2026-09-09 20:25 UTC = 16:25 EDT -> 2026-09-09 (today's scheduled run)
        assert et.eastern_date(_utc(2026, 9, 9, 20, 25)) == "2026-09-09"

    def test_edt_after_utc_midnight_is_previous_et_day(self):
        # 2026-09-09 01:40 UTC = 2026-09-08 21:40 EDT -> 2026-09-08
        # (this is the wipe+rerun; under ET it's a DIFFERENT day than the 20:25 run)
        assert et.eastern_date(_utc(2026, 9, 9, 1, 40)) == "2026-09-08"

    def test_est_winter(self):
        # 2026-01-15 02:00 UTC = 2026-01-14 21:00 EST -> 2026-01-14
        assert et.eastern_date(_utc(2026, 1, 15, 2)) == "2026-01-14"

    def test_dst_spring_forward_boundary(self):
        # DST begins 2026-03-08 (2nd Sunday of March). 03-08 07:00 UTC:
        # before 2am local it's still EST(-5) -> 02:00 -> but at 2am clocks jump.
        # At 07:00 UTC we're past the switch -> EDT(-4) -> 03:00 EDT -> 2026-03-08
        assert et.eastern_date(_utc(2026, 3, 8, 7)) == "2026-03-08"

    def test_dst_fall_back_boundary(self):
        # DST ends 2026-11-01 (1st Sunday of November).
        # 2026-11-01 05:00 UTC: after fall-back it's EST(-5) -> 00:00 -> 2026-11-01
        assert et.eastern_date(_utc(2026, 11, 1, 5)) == "2026-11-01"


@pytest.mark.unit
class TestEasternToday:
    def test_eastern_today_returns_iso_date(self):
        s = et.eastern_today()
        # Format YYYY-MM-DD
        dt.datetime.strptime(s, "%Y-%m-%d")

    def test_days_tracked_boundary_uses_et(self):
        # first_tracked ET 2026-09-09, "now" = 2026-09-09 23:00 UTC (19:00 EDT,
        # still 09-09 ET) -> 0 days -> NEW
        now = _utc(2026, 9, 9, 23)
        assert et.days_tracked("2026-09-09", now) == 0
        # next ET day
        now2 = _utc(2026, 9, 11, 3)  # 2026-09-10 23:00 EDT
        assert et.days_tracked("2026-09-09", now2) == 1
