"""Read-only Zoom client (Server-to-Server OAuth): hosts, past meetings and webinars, attendance.

Three Zoom apps are configured; pick one by environment prefix:

    ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET                      main account (default)
    ZOOM_MKT_ACCOUNT_ID / ZOOM_MKT_CLIENT_ID / ZOOM_MKT_CLIENT_SECRET          marketing account
    ZOOM_WEBINAR_ACCOUNT_ID / ZOOM_WEBINAR_CLIENT_ID / ZOOM_WEBINAR_CLIENT_SECRET
                                     webinars@ account; no report scope yet, so attendance reports fail

Participant reports need the ``report:read:admin`` scope. Zoom returns times in UTC
(``...Z``); convert before showing them to people.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Iterator
from urllib.parse import quote

import requests

from integrations.http import ApiError, request_json

TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE = "https://api.zoom.us/v2"
ACCOUNTS = {"main": "ZOOM", "marketing": "ZOOM_MKT", "webinar": "ZOOM_WEBINAR"}


class ZoomError(ApiError):
    pass


def meeting_path_id(uuid_or_id: str | int) -> str:
    """Zoom wants a UUID that starts with ``/`` or contains ``//`` URL-encoded twice."""
    s = str(uuid_or_id)
    if s.startswith("/") or "//" in s:
        return quote(quote(s, safe=""), safe="")
    return quote(s, safe="")


def attendance(participants: list[dict]) -> list[dict]:
    """One row per person (email, else name): total minutes, first join, last leave, sessions.
    Zoom lists a person again each time they rejoin, so durations are summed."""
    people: dict[str, dict] = {}
    for p in participants:
        email = (p.get("user_email") or p.get("email") or "").strip().lower()
        key = email or (p.get("name") or "").strip().lower()
        if not key:
            continue
        row = people.setdefault(key, {"email": email or None, "name": p.get("name"), "seconds": 0,
                                      "first_join": p.get("join_time"), "last_leave": p.get("leave_time"),
                                      "sessions": 0})
        row["seconds"] += int(p.get("duration") or 0)
        row["sessions"] += 1
        if p.get("join_time") and (not row["first_join"] or p["join_time"] < row["first_join"]):
            row["first_join"] = p["join_time"]
        if p.get("leave_time") and (not row["last_leave"] or p["leave_time"] > row["last_leave"]):
            row["last_leave"] = p["leave_time"]
    out = [{**{k: v for k, v in r.items() if k != "seconds"}, "minutes": round(r["seconds"] / 60, 1)}
           for r in people.values()]
    return sorted(out, key=lambda r: -r["minutes"])


class ZoomClient:
    def __init__(
        self,
        account: str = "main",
        account_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        if account not in ACCOUNTS:
            raise ZoomError(f"Unknown Zoom account {account!r}; use one of {', '.join(ACCOUNTS)}")
        prefix = ACCOUNTS[account]
        self.account = account
        self.account_id = account_id or os.environ.get(f"{prefix}_ACCOUNT_ID")
        self.client_id = client_id or os.environ.get(f"{prefix}_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get(f"{prefix}_CLIENT_SECRET")
        if not (self.account_id and self.client_id and self.client_secret):
            raise ZoomError(f"Missing credentials: set {prefix}_ACCOUNT_ID, {prefix}_CLIENT_ID, {prefix}_CLIENT_SECRET")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._token: str | None = None
        self._token_expiry = 0.0

    # ------------------------------------------------------------------ core

    def access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        data = request_json(
            self.session, "POST", TOKEN_URL, label="POST zoom oauth token", error=ZoomError,
            max_retries=self.max_retries, timeout=self.timeout,
            params={"grant_type": "account_credentials", "account_id": self.account_id},
            auth=(self.client_id, self.client_secret),
        )
        self._token = data["access_token"]
        self._token_expiry = time.time() + float(data.get("expires_in", 3600))
        return self._token

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return request_json(
            self.session, "GET", f"{API_BASE}/{path.lstrip('/')}", label=f"GET {path}", error=ZoomError,
            max_retries=self.max_retries, timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.access_token()}"},
            params={k: v for k, v in (params or {}).items() if v is not None},
        )

    def paged(self, path: str, key: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Yield items under ``key``, following ``next_page_token``."""
        params = {"page_size": 300, **(params or {})}
        while True:
            page = self.get(path, params) or {}
            yield from page.get(key, [])
            token = page.get("next_page_token")
            if not token:
                return
            params["next_page_token"] = token

    # ------------------------------------------------------------------ reads

    def users(self, status: str = "active") -> list[dict]:
        return list(self.paged("users", "users", {"status": status}))

    def past_meetings(self, user_id: str, start: str | date, end: str | date) -> list[dict]:
        """Ended meetings a host ran between two dates (at most one month per call, Zoom's limit)."""
        return list(self.paged(f"report/users/{quote(user_id, safe='')}/meetings", "meetings",
                               {"from": str(start), "to": str(end), "type": "past"}))

    def webinars(self, user_id: str) -> list[dict]:
        return list(self.paged(f"users/{quote(user_id, safe='')}/webinars", "webinars"))

    def past_webinar_instances(self, webinar_id: str | int) -> list[dict]:
        return (self.get(f"past_webinars/{meeting_path_id(webinar_id)}/instances") or {}).get("webinars", [])

    def meeting_participants(self, meeting_uuid_or_id: str | int) -> list[dict]:
        return list(self.paged(f"report/meetings/{meeting_path_id(meeting_uuid_or_id)}/participants", "participants"))

    def webinar_participants(self, webinar_uuid_or_id: str | int) -> list[dict]:
        return list(self.paged(f"report/webinars/{meeting_path_id(webinar_uuid_or_id)}/participants", "participants"))

    def webinar_registrants(self, webinar_id: str | int, status: str = "approved") -> list[dict]:
        """Registrants carry the phone number, when the form asks for it; participants do not."""
        return list(self.paged(f"webinars/{meeting_path_id(webinar_id)}/registrants", "registrants",
                               {"status": status}))
