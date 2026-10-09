"""Read-only Google Ads API client (REST, no SDK): campaign spend, clicks and conversions.

Credentials come from the environment (root ``.env``):

    GOOGLE_DEV_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
    GOOGLE_LOGIN_CUSTOMER_ID   manager (MCC) account the token signs in through
    GOOGLE_CUSTOMER_IDS        comma-separated ad accounts to report on
    GOOGLE_API_VERSION         default v22

Costs come back in micros of the account currency (INR here); ``campaign_daily`` converts
to rupees. Dates are the account's own time zone.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Iterator

import requests

from integrations.http import ApiError, request_json

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://googleads.googleapis.com"

CAMPAIGN_DAILY_GAQL = (
    "SELECT segments.date, campaign.id, campaign.name, campaign.status, "
    "campaign.advertising_channel_type, metrics.cost_micros, metrics.impressions, "
    "metrics.clicks, metrics.conversions "
    "FROM campaign WHERE segments.date BETWEEN '{start}' AND '{end}' AND metrics.impressions > 0"
)


class GoogleAdsError(ApiError):
    pass


def _digits(customer_id: str) -> str:
    return str(customer_id).replace("-", "").strip()


class GoogleAdsClient:
    def __init__(
        self,
        developer_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        login_customer_id: str | None = None,
        customer_ids: list[str] | None = None,
        version: str | None = None,
        timeout: float = 60,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        env = os.environ.get
        self.developer_token = developer_token or env("GOOGLE_DEV_TOKEN")
        self.client_id = client_id or env("GOOGLE_CLIENT_ID")
        self.client_secret = client_secret or env("GOOGLE_CLIENT_SECRET")
        self.refresh_token = refresh_token or env("GOOGLE_REFRESH_TOKEN")
        missing = [n for n, v in (("GOOGLE_DEV_TOKEN", self.developer_token), ("GOOGLE_CLIENT_ID", self.client_id),
                                  ("GOOGLE_CLIENT_SECRET", self.client_secret),
                                  ("GOOGLE_REFRESH_TOKEN", self.refresh_token)) if not v]
        if missing:
            raise GoogleAdsError("Missing credentials: set " + ", ".join(missing))
        login = login_customer_id or env("GOOGLE_LOGIN_CUSTOMER_ID")
        self.login_customer_id = _digits(login) if login else None
        ids = customer_ids if customer_ids is not None else (env("GOOGLE_CUSTOMER_IDS") or "").split(",")
        self.customer_ids = [_digits(c) for c in ids if c and c.strip()]
        self.version = version or env("GOOGLE_API_VERSION") or "v22"
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
            self.session, "POST", TOKEN_URL, label="POST oauth2 token", error=GoogleAdsError,
            max_retries=self.max_retries, timeout=self.timeout,
            data={"client_id": self.client_id, "client_secret": self.client_secret,
                  "refresh_token": self.refresh_token, "grant_type": "refresh_token"},
        )
        self._token = data["access_token"]
        self._token_expiry = time.time() + float(data.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.access_token()}", "developer-token": self.developer_token}
        if self.login_customer_id:
            h["login-customer-id"] = self.login_customer_id
        return h

    def request(self, method: str, path: str, json: Any = None) -> Any:
        return request_json(
            self.session, method, f"{API_BASE}/{self.version}/{path.lstrip('/')}", label=f"{method} {path}",
            error=GoogleAdsError, max_retries=self.max_retries, timeout=self.timeout,
            headers=self._headers(), json=json,
        )

    # ------------------------------------------------------------------ reads

    def list_accessible_customers(self) -> list[str]:
        names = (self.request("GET", "customers:listAccessibleCustomers") or {}).get("resourceNames", [])
        return [n.split("/")[-1] for n in names]

    def search(self, customer_id: str, query: str) -> Iterator[dict]:
        """Yield GAQL result rows, following ``nextPageToken``."""
        body: dict[str, Any] = {"query": query}
        while True:
            data = self.request("POST", f"customers/{_digits(customer_id)}/googleAds:search", json=body) or {}
            yield from data.get("results", [])
            token = data.get("nextPageToken")
            if not token:
                return
            body = {"query": query, "pageToken": token}

    def campaign_daily(self, start: str | date, end: str | date, customer_ids: list[str] | None = None) -> list[dict]:
        """One row per account, campaign and day with spend in rupees."""
        query = CAMPAIGN_DAILY_GAQL.format(start=str(start), end=str(end))
        rows = []
        for cid in customer_ids or self.customer_ids:
            for r in self.search(cid, query):
                c, m = r.get("campaign", {}), r.get("metrics", {})
                rows.append({
                    "customer_id": _digits(cid),
                    "date": r.get("segments", {}).get("date"),
                    "campaign_id": c.get("id"),
                    "campaign": c.get("name"),
                    "status": c.get("status"),
                    "channel": c.get("advertisingChannelType"),
                    "cost_inr": round(int(m.get("costMicros", 0)) / 1_000_000, 2),
                    "impressions": int(m.get("impressions", 0)),
                    "clicks": int(m.get("clicks", 0)),
                    "conversions": float(m.get("conversions", 0)),
                })
        return rows
