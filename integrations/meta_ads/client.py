"""Read-only Meta (Facebook) Marketing API client: ad accounts, campaigns and daily insights.

Credentials come from the environment (root ``.env``):

    META_TOKEN           System User token with ads_read
    META_GRAPH_VERSION   default v21.0

Budgets (``daily_budget``, ``lifetime_budget``) come back in the currency's minor unit,
so paise for INR; ``campaigns`` converts them to rupees. Insights ``spend`` is already in
rupees. ``effective_status`` ACTIVE does not mean the campaign delivered: check spend.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Iterator

import requests

from integrations.http import ApiError, request_json

GRAPH = "https://graph.facebook.com"
LEAD_ACTIONS = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
INSIGHT_FIELDS = "campaign_id,campaign_name,spend,impressions,clicks,actions,date_start,date_stop"


class MetaAdsError(ApiError):
    pass


def act_id(account_id: str) -> str:
    a = str(account_id).strip()
    return a if a.startswith("act_") else f"act_{a}"


def lead_count(actions: list[dict] | None) -> int:
    """Leads from an insights ``actions`` list. Meta repeats one lead under several action
    types, so take the largest of the lead types rather than their sum."""
    vals = [int(float(a.get("value", 0))) for a in actions or [] if a.get("action_type") in LEAD_ACTIONS]
    return max(vals, default=0)


def _rupees(minor: Any) -> float | None:
    return None if minor in (None, "") else round(int(minor) / 100, 2)


class MetaAdsClient:
    def __init__(
        self,
        token: str | None = None,
        version: str | None = None,
        timeout: float = 60,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self.token = token or os.environ.get("META_TOKEN")
        if not self.token:
            raise MetaAdsError("Missing credentials: set META_TOKEN")
        self.version = version or os.environ.get("META_GRAPH_VERSION") or "v21.0"
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ core

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        q = {k: v for k, v in (params or {}).items() if v is not None}
        q["access_token"] = self.token
        return request_json(
            self.session, "GET", f"{GRAPH}/{self.version}/{path.lstrip('/')}", label=f"GET {path}",
            error=MetaAdsError, max_retries=self.max_retries, timeout=self.timeout, params=q,
        )

    def paged(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Yield every ``data`` item, following ``paging.cursors.after``."""
        params = dict(params or {})
        while True:
            page = self.get(path, params) or {}
            yield from page.get("data", [])
            after = (page.get("paging") or {}).get("cursors", {}).get("after")
            if not after or not (page.get("paging") or {}).get("next"):
                return
            params["after"] = after

    # ------------------------------------------------------------------ reads

    def ad_accounts(self) -> list[dict]:
        return list(self.paged("me/adaccounts", {"fields": "id,account_id,name,account_status,currency",
                                                 "limit": 100}))

    def campaigns(self, account_id: str) -> list[dict]:
        rows = self.paged(f"{act_id(account_id)}/campaigns", {
            "fields": "id,name,objective,status,effective_status,daily_budget,lifetime_budget,start_time,stop_time",
            "limit": 200,
        })
        return [{**r, "daily_budget_inr": _rupees(r.get("daily_budget")),
                 "lifetime_budget_inr": _rupees(r.get("lifetime_budget"))} for r in rows]

    def campaign_daily(self, account_id: str, start: str | date, end: str | date) -> list[dict]:
        """One row per campaign and day: spend (rupees), impressions, clicks, leads."""
        rows = self.paged(f"{act_id(account_id)}/insights", {
            "level": "campaign", "fields": INSIGHT_FIELDS, "time_increment": 1, "limit": 500,
            "time_range": f'{{"since":"{start}","until":"{end}"}}',
        })
        return [{
            "account_id": act_id(account_id),
            "date": r.get("date_start"),
            "campaign_id": r.get("campaign_id"),
            "campaign": r.get("campaign_name"),
            "spend_inr": float(r.get("spend", 0) or 0),
            "impressions": int(r.get("impressions", 0) or 0),
            "clicks": int(r.get("clicks", 0) or 0),
            "leads": lead_count(r.get("actions")),
        } for r in rows]
