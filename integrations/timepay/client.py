"""Read-only TimePay (AI voice agent) client: campaigns, agents, dispositions and call logs.

Credentials come from the environment (root ``.env``):

    TIMEPAY_BASE_URL     e.g. https://api.agents.timepay.ai/api/v1
    TIMEPAY_TOKEN        API token
    TIMEPAY_ORG_ID       organisation, sent as the ``x-org-id`` header (the account has 3 orgs)
    TIMEPAY_AUTH_HEADER  optional; header that carries the token. Default ``Authorization``
                         (sent as ``Bearer <token>``); set e.g. ``x-api-key`` to send it bare.

List endpoints answer ``{"success", "data", "pagination": {...}}``; ``/logs`` spells the
next-page flag ``has_next``, others may say ``hasNext``. ``/logs`` returns 10 rows a page (no
page-size parameter works), so ``iter_logs`` stops at ``max_pages`` unless told otherwise and
``count_logs`` asks for the total only. Log times are IST (``2026-10-08T00:00:00``). The
``start_time``/``end_time`` filter is not on a call's own ``start_time``: a 10:10–10:15 window
returned calls that started 10:12–10:28 (checked 9 Oct 2026), so keep calls by their ``start_time``.

Deliberately read-only: starting calls, WhatsApp or SMS, and editing campaigns or
customers, reach real people and need the user's go-ahead for each run, so they are not here.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import requests

from integrations.http import ApiError, request_json


class TimePayError(ApiError):
    pass


class TimePayClient:
    def __init__(
        self,
        token: str | None = None,
        org_id: str | None = None,
        base_url: str | None = None,
        auth_header: str | None = None,
        timeout: float = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self.token = token or os.environ.get("TIMEPAY_TOKEN")
        self.base_url = (base_url or os.environ.get("TIMEPAY_BASE_URL") or "").rstrip("/")
        if not self.token or not self.base_url:
            raise TimePayError("Missing credentials: set TIMEPAY_TOKEN and TIMEPAY_BASE_URL")
        self.org_id = org_id or os.environ.get("TIMEPAY_ORG_ID")
        self.auth_header = auth_header or os.environ.get("TIMEPAY_AUTH_HEADER") or "Authorization"
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ core

    def _headers(self) -> dict[str, str]:
        value = f"Bearer {self.token}" if self.auth_header.lower() == "authorization" else self.token
        h = {self.auth_header: value}
        if self.org_id:
            h["x-org-id"] = self.org_id
        return h

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        data = request_json(
            self.session, "GET", f"{self.base_url}/{path.lstrip('/')}", label=f"GET {path}", error=TimePayError,
            max_retries=self.max_retries, timeout=self.timeout, headers=self._headers(),
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        if isinstance(data, dict) and data.get("success") is False:
            raise TimePayError(f"GET {path} failed: {data.get('message') or data.get('error') or data}", payload=data)
        return data

    def paged(self, path: str, params: dict[str, Any] | None = None, max_pages: int | None = None) -> Iterator[dict]:
        params, page = dict(params or {}), int((params or {}).get("page") or 1)
        while True:
            data = self.get(path, {**params, "page": page}) or {}
            yield from data.get("data", [])
            more = data.get("pagination") or {}
            if not (more.get("has_next") or more.get("hasNext")):
                return
            page += 1
            if max_pages is not None and page - int(params.get("page") or 1) >= max_pages:
                return

    # ------------------------------------------------------------------ reads

    def campaigns(self, status: str | None = None, search: str | None = None,
                  max_pages: int | None = None) -> list[dict]:
        return list(self.paged("campaigns", {"status": status, "search": search}, max_pages))

    def agents(self) -> list[dict]:
        return list(self.paged("agents"))

    def dispositions(self) -> list[dict]:
        return list(self.paged("dispositions"))

    def logs_params(self, start_time: str | None = None, end_time: str | None = None, **filters: Any) -> dict:
        """Filters: type (call/whatsapp/sms), campaign_id, agent_id, status, disposition, phone, from_phone."""
        return {"start_time": start_time, "end_time": end_time, **filters}

    def count_logs(self, start_time: str | None = None, end_time: str | None = None, **filters: Any) -> int:
        data = self.get("logs", {**self.logs_params(start_time, end_time, **filters), "count_only": "true"}) or {}
        return int(data.get("total") or (data.get("pagination") or {}).get("total") or 0)

    def iter_logs(self, start_time: str | None = None, end_time: str | None = None, max_pages: int | None = 100,
                  **filters: Any) -> Iterator[dict]:
        """Ten logs a page; ``max_pages=None`` reads everything (a busy day is thousands of pages)."""
        return self.paged("logs", self.logs_params(start_time, end_time, **filters), max_pages)
