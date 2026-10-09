"""Read-only GrowthX client: leads and bootcamp checkouts captured by the landing-page funnels.

Credentials come from the environment (root ``.env``):

    GROWTHX_URL     the public leads endpoint, e.g. https://growthx.skillarbitra.ge/api/public/leads
    GROWTHX_TOKEN   sent as ``Authorization: Bearer <token>``

Facts checked against the live API (9 Oct 2026):

* ``GET`` answers ``{"meta": {"page", "pageSize", "total", "hasNextPage", ...}, "leads": [...]}``,
  newest first, 10,000 leads a page whatever ``pageSize``/``limit`` say. Rows added while paging
  push older rows onto the next page, so ``leads`` drops repeats by ``id``.
* ``from``/``to`` take ``YYYY-MM-DD`` only and are **UTC** days, inclusive: ``from=to=2026-10-08``
  returned 8 Oct 05:34 to 9 Oct 05:29 IST. ``leads_for_ist_days`` asks for one day either side
  and keeps rows by their IST capture time.
* ``capturedAt`` is IST text to the minute (``9 Oct 2026, 6:25 pm``; September is ``Sept``).
* ``funnelType`` ``paid`` rows are bootcamp checkouts: ``status`` Paid / Checkout started /
  Expired / Failed / Abandoned at 2FA, ``amount`` the ticket price in rupees as text (10, 100,
  200). These are bootcamp tickets, not course fees. ``lead`` rows are free form fills.
* ``leadtype`` filters server-side; ``status`` and ``funnelType`` do not.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterator

import requests

from integrations.http import ApiError, request_json
from integrations.timeutil import IST

_CAPTURED = re.compile(r"^\s*(\d{1,2}) ([A-Za-z]+) (\d{4}), (\d{1,2}):(\d{2}) ?([ap]m)\s*$", re.I)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), 1)}


class GrowthXError(ApiError):
    pass


def captured_ist(text: str | None) -> datetime | None:
    """Parse ``capturedAt`` (IST, e.g. ``26 Sept 2026, 8:25 pm``); None if empty or unreadable."""
    m = _CAPTURED.match(text or "")
    if not m:
        return None
    day, month, year, hour, minute, ampm = m.groups()
    mon = _MONTHS.get(month[:3].lower())
    h = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    if not mon or int(hour) > 12:
        return None
    try:
        return datetime(int(year), mon, int(day), h, int(minute), tzinfo=IST)
    except ValueError:
        return None


class GrowthXClient:
    def __init__(
        self,
        token: str | None = None,
        url: str | None = None,
        timeout: float = 120,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self.token = token or os.environ.get("GROWTHX_TOKEN")
        self.url = url or os.environ.get("GROWTHX_URL")
        if not self.token or not self.url:
            raise GrowthXError("Missing credentials: set GROWTHX_TOKEN and GROWTHX_URL")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    def get(self, params: dict[str, Any] | None = None) -> dict:
        data = request_json(
            self.session, "GET", self.url, label="GET growthx leads", error=GrowthXError,
            max_retries=self.max_retries, timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.token}"},
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        if not isinstance(data, dict) or "leads" not in data:
            raise GrowthXError(f"GET growthx leads: unexpected answer {str(data)[:200]}", payload=data)
        return data

    def total(self, utc_from: str | date | None = None, utc_to: str | date | None = None, **filters: Any) -> int:
        return int(self.get({"from": utc_from, "to": utc_to, **filters, "page": 1})["meta"].get("total") or 0)

    def leads(self, utc_from: str | date | None = None, utc_to: str | date | None = None,
              max_pages: int | None = None, **filters: Any) -> Iterator[dict]:
        """Every lead captured on the UTC days ``utc_from``..``utc_to`` (inclusive), each ``id`` once."""
        seen: set[str] = set()
        page = 1
        while True:
            data = self.get({"from": utc_from and str(utc_from), "to": utc_to and str(utc_to), **filters,
                             "page": page})
            for row in data["leads"]:
                key = str(row.get("id"))
                if key not in seen:
                    seen.add(key)
                    yield row
            if not data["meta"].get("hasNextPage") or (max_pages is not None and page >= max_pages):
                return
            page += 1

    def leads_for_ist_days(self, start: str, end: str, **filters: Any) -> tuple[list[dict], int]:
        """Leads captured from 00:00 IST on ``start`` to 23:59 IST on ``end``, each with ``captured_ist``
        added. Also returns how many rows in the fetched window had an unreadable ``capturedAt``."""
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
        rows, bad = [], 0
        for row in self.leads(first - timedelta(days=1), last, **filters):
            at = captured_ist(row.get("capturedAt"))
            if at is None:
                bad += 1
            elif first <= at.date() <= last:
                rows.append({**row, "captured_ist": at.strftime("%Y-%m-%dT%H:%M")})
        return rows, bad
