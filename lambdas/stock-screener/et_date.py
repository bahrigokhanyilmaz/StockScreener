"""
US Eastern (America/New_York) business-date helper — dependency-free.

The app keys business dates on the US MARKET timezone, not UTC:
  - first_tracked, SCORE#{date}, mark_date, the "Days" column, and the
    market-gate holiday check are all calendar dates as a US market participant
    experiences them.

Why not zoneinfo/ZoneInfo? The AWS Lambda Python runtime does not ship the IANA
tz database, so ZoneInfo('America/New_York') raises unless `tzdata` is bundled —
and some of our Lambdas (stock-screener, api) don't bundle dependencies. This
helper implements the fixed, well-known US DST rules with pure stdlib so it works
identically everywhere.

US Eastern DST (post-2007 rules, stable):
  - EDT (UTC-4): from 2nd Sunday of March 02:00 local .. 1st Sunday of November 02:00 local
  - EST (UTC-5): otherwise

This is the copy of record. It is vendored into each Lambda folder (like
pipeline_io.py). Keep the copies in sync.
"""
from datetime import datetime, timezone, timedelta


def _nth_sunday(year: int, month: int, n: int) -> datetime.date:
    """Return the date of the nth Sunday (1-based) of a given month/year."""
    from datetime import date
    d = date(year, month, 1)
    # weekday(): Mon=0..Sun=6
    first_sunday_offset = (6 - d.weekday()) % 7
    day = 1 + first_sunday_offset + (n - 1) * 7
    return date(year, month, day)


def _is_edt(utc_dt: datetime) -> bool:
    """
    True if US Eastern is on daylight time (EDT, UTC-4) at this UTC instant.

    DST starts 2nd Sunday of March at 02:00 LOCAL (which is 07:00 UTC while still
    EST), ends 1st Sunday of November at 02:00 LOCAL (06:00 UTC while EDT).
    """
    year = utc_dt.year
    dst_start = _nth_sunday(year, 3, 2)   # 2nd Sunday March
    dst_end = _nth_sunday(year, 11, 1)    # 1st Sunday November

    # Transition instants in UTC:
    #   spring: 02:00 EST = 07:00 UTC on dst_start
    #   fall:   02:00 EDT = 06:00 UTC on dst_end
    start_utc = datetime(dst_start.year, dst_start.month, dst_start.day, 7, 0, tzinfo=timezone.utc)
    end_utc = datetime(dst_end.year, dst_end.month, dst_end.day, 6, 0, tzinfo=timezone.utc)
    return start_utc <= utc_dt < end_utc


def eastern_date(utc_dt: datetime = None) -> str:
    """Return the US Eastern calendar date (YYYY-MM-DD) for a UTC instant."""
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc)
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    offset = timedelta(hours=-4) if _is_edt(utc_dt) else timedelta(hours=-5)
    return (utc_dt + offset).strftime("%Y-%m-%d")


def eastern_today() -> str:
    """Return today's US Eastern calendar date (YYYY-MM-DD)."""
    return eastern_date(datetime.now(timezone.utc))


def days_tracked(first_tracked: str, now_utc: datetime = None) -> int:
    """
    Whole ET-calendar days elapsed between first_tracked (YYYY-MM-DD, ET) and now.
    0 means "first tracked today (ET)" -> UI shows NEW.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    today_et = eastern_date(now_utc)
    from datetime import date
    a = date.fromisoformat(first_tracked)
    b = date.fromisoformat(today_et)
    return (b - a).days
