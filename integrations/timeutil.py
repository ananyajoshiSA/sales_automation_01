"""The one place for time zones in this repo.

Facts checked against live data (9 Oct 2026, docs/timezone_issues.md):

* LeadSquared ``CreatedOn``/``ModifiedOn`` and lead date fields are UTC strings with no zone.
  A call activity's ``CreatedOn`` is the call start. Its note also carries ``StartTime`` twice:
  in UTC (``10/5/2026 2:29:02 PM``) and, inside ``SourceData``, in IST (two formats). Neither is
  labelled, so never read a ``StartTime`` as a call time.
* LeadSquared's activity date filter is on ``ModifiedOn``, so a call edited after its day comes back
  with the day it was edited. Fetch with ``EDIT_MARGIN`` and keep by ``CreatedOn``.
* Everything shown to people is IST.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
IST_OFFSET = timedelta(hours=5, minutes=30)
EDIT_MARGIN = timedelta(days=3)  # calls edited up to this long after their day are still found


def utc(s: str | None) -> datetime | None:
    """Parse LeadSquared's ``YYYY-MM-DD HH:MM:SS[.fff]`` UTC strings; None if empty or unreadable."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def unreadable(s: str | None) -> bool:
    """A time that is present but can't be parsed: count these, never treat them as another day."""
    return bool(s) and utc(s) is None


def ist_day(dt: datetime | None) -> str | None:
    return dt.astimezone(IST).strftime("%Y-%m-%d") if dt else None


def ist_day_start(date: str) -> datetime:
    """00:00 IST on ``YYYY-MM-DD``."""
    return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=IST)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ist() -> datetime:
    return datetime.now(IST)
