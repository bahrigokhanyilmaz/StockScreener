"""
US Pacific (America/Los_Angeles) business-date helper — dependency-free.

The app keys business dates on the USER's timezone (Pacific), not UTC:
  - first_tracked, SCORE#{date}, mark_date, the "Days" column, and the
    market-gate holiday check are all calendar dates as the user experiences them.

Why Pacific: the user is in Pacific. The market close (1pm PT) and the scheduled
run (1pm PT) both fall well within a single PT calendar day, so PT avoids the
late-evening rollover that ET caused (a stock first seen ~9pm PT was dated the
next day under ET).

Why not zoneinfo/ZoneInfo? The AWS Lambda Python runtime does not ship the IANA
tz database, so ZoneInfo(...) raises unless `tzdata` is bundled — and some of our
Lambdas (stock-screener, api) don't bundle dependencies. This helper implements
the fixed US DST rules with pure stdlib so it works identically everywhere.

US Pacific DST (post-2007 rules, stable):
  - PDT (UTC-7): from 2nd Sunday of March 02:00 local .. 1st Sunday of November 02:00 local
  - PST (UTC-8): otherwise

Function names are kept as eastern_date/eastern_today for backwards-compat with
existing callers, but they now return PACIFIC dates.

This is the copy of record. It is vendored into each Lambda folder (like
pipeline_io.py). Keep the copies in sync.
"""
from datetime import datetime, timezone, timedelta


def _nth_sunday(year: int, month: int, n: int):
    """Return the date of the nth Sunday (1-based) of a given month/year."""
    from datetime import date
    d = date(year, month, 1)
    # weekday(): Mon=0..Sun=6
    first_sunday_offset = (6 - d.weekday()) % 7
    day = 1 + first_sunday_offset + (n - 1) * 7
    return date(year, month, day)


def _is_pdt(utc_dt: datetime) -> bool:
    """
    True if US Pacific is on daylight time (PDT, UTC-7) at this UTC instant.

    DST starts 2nd Sunday of March at 02:00 LOCAL (10:00 UTC while still PST),
    ends 1st Sunday of November at 02:00 LOCAL (09:00 UTC while PDT).
    """
    year = utc_dt.year
    dst_start = _nth_sunday(year, 3, 2)   # 2nd Sunday March
    dst_end = _nth_sunday(year, 11, 1)    # 1st Sunday November

    # Transition instants in UTC:
    #   spring: 02:00 PST = 10:00 UTC on dst_start
    #   fall:   02:00 PDT = 09:00 UTC on dst_end
    start_utc = datetime(dst_start.year, dst_start.month, dst_start.day, 10, 0, tzinfo=timezone.utc)
    end_utc = datetime(dst_end.year, dst_end.month, dst_end.day, 9, 0, tzinfo=timezone.utc)
    return start_utc <= utc_dt < end_utc


def eastern_date(utc_dt: datetime = None) -> str:
    """Return the US Pacific calendar date (YYYY-MM-DD) for a UTC instant."""
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc)
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    offset = timedelta(hours=-7) if _is_pdt(utc_dt) else timedelta(hours=-8)
    return (utc_dt + offset).strftime("%Y-%m-%d")


def eastern_today() -> str:
    """Return today's US Pacific calendar date (YYYY-MM-DD)."""
    return eastern_date(datetime.now(timezone.utc))


def days_tracked(first_tracked: str, now_utc: datetime = None) -> int:
    """
    Whole Pacific-calendar days elapsed between first_tracked (YYYY-MM-DD, PT)
    and now. 0 means "first tracked today (PT)" -> UI shows NEW.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    today_pt = eastern_date(now_utc)
    from datetime import date
    a = date.fromisoformat(first_tracked)
    b = date.fromisoformat(today_pt)
    return (b - a).days
