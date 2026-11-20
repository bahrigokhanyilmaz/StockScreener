"""
US market-clock helpers (America/New_York) — dependency-free.

Used to decide whether a daily price bar is FINAL (the session has closed and the
vendor has had time to publish the full OHLCV). Separate from et_date.py on
purpose: et_date returns PACIFIC business dates (the user's calendar) and must
not change. This module is real New York time.

Why not zoneinfo? The Lambda runtime has no IANA tz database (see et_date.py), so
the fixed US DST rules are implemented with pure stdlib:
  - EDT (UTC-4): 2nd Sunday of March 07:00 UTC .. 1st Sunday of November 06:00 UTC
  - EST (UTC-5): otherwise

Holidays are handled as data: the vendor returns bars only for real sessions, so
no holiday list is kept here. latest_final_session_date() may name a holiday; the
consequence is at most one extra vendor call.

This is the copy of record. It is vendored byte-identical into lambdas/score-calculator/.
"""
from datetime import date, datetime, time, timedelta, timezone

# A bar dated today is eligible to be STORED once NY time reaches this.
BAR_FINAL_AFTER = time(16, 15)
# A stored series is trusted enough to SKIP a refetch only if it was fetched at
# or after this time on the session date (vendor settle-time guard).
SKIP_SETTLED_AFTER = time(17, 0)


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    return date(year, month, 1 + (6 - d.weekday()) % 7 + (n - 1) * 7)


def _is_edt(utc_dt: datetime) -> bool:
    year = utc_dt.year
    s = _nth_sunday(year, 3, 2)
    e = _nth_sunday(year, 11, 1)
    start_utc = datetime(s.year, s.month, s.day, 7, 0, tzinfo=timezone.utc)
    end_utc = datetime(e.year, e.month, e.day, 6, 0, tzinfo=timezone.utc)
    return start_utc <= utc_dt < end_utc


def _aware(now_utc: datetime = None) -> datetime:
    if now_utc is None:
        return datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def new_york_dt(now_utc: datetime = None) -> datetime:
    """Naive New York wall-clock datetime for a UTC instant."""
    u = _aware(now_utc)
    offset = timedelta(hours=-4) if _is_edt(u) else timedelta(hours=-5)
    return (u + offset).replace(tzinfo=None)


def new_york_date(now_utc: datetime = None) -> str:
    """New York calendar date (YYYY-MM-DD) for a UTC instant."""
    return new_york_dt(now_utc).strftime("%Y-%m-%d")


def _parse_date(s):
    try:
        return date.fromisoformat(s) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def is_bar_final(bar_date: str, now_utc: datetime = None) -> bool:
    """True if the daily bar for bar_date is complete as of now_utc."""
    bd = _parse_date(bar_date)
    if bd is None:
        return False
    ny = new_york_dt(now_utc)
    today = ny.date()
    if bd < today:
        return True
    if bd > today:
        return False
    return today.weekday() < 5 and ny.time() >= BAR_FINAL_AFTER


def previous_weekday(date_str: str) -> str:
    """The weekday strictly before date_str (walks back over Sat/Sun)."""
    d = date.fromisoformat(date_str) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def latest_final_session_date(now_utc: datetime = None) -> str:
    """Newest weekday whose bar is final by now (may be a holiday)."""
    ny = new_york_dt(now_utc)
    today = ny.date()
    if today.weekday() < 5 and ny.time() >= BAR_FINAL_AFTER:
        return today.isoformat()
    return previous_weekday(today.isoformat())


def parse_utc_iso(s):
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' / '+00:00' (optional fraction) to aware UTC; None if invalid."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
