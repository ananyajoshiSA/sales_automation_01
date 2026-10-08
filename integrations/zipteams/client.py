"""Client for the Zipteams Customer and Partner APIs (push side).

Credentials are read from the environment, never hard-coded:

    ZIPTEAMS_API_KEY             Customer API key (x-zip-api-key header)
    ZIPTEAMS_API_SECRET, ZIPTEAMS_TENANT_ID, ZIPTEAMS_SUB_TENANT_ID
                                 set all three (with the key) to switch to the Partner API
                                 (x-api-key + x-api-secret + x-tenant-id + x-sub-tenant-id)

    Optional endpoint overrides:
    ZIPTEAMS_INGEST_URL          default https://mixu6sd8i0.execute-api.ap-south-1.amazonaws.com/calls-webhook-ingestion-handler
    ZIPTEAMS_CUSTOMER_SYNC_URL   default https://api.zipteams.com/api/v1/client/conversation/webhook/customer-sync
    ZIPTEAMS_PARTNER_BASE        default https://api.zipteams.com/api/v1/partner

Docs: https://zipteams.github.io/customer-api/introduction

Every endpoint writes to Zipteams: sending a call queues it for analysis, and the
analysis comes back to the call's ``callback_url`` (Zipteams also writes its notes
into LeadSquared as "Zipteams Notes" activities, which is what analytics/ reads).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from integrations.transcripts.client import normalize_phone

DEFAULT_INGEST_URL = "https://mixu6sd8i0.execute-api.ap-south-1.amazonaws.com/calls-webhook-ingestion-handler"
DEFAULT_CUSTOMER_SYNC_URL = "https://api.zipteams.com/api/v1/client/conversation/webhook/customer-sync"
DEFAULT_PARTNER_BASE = "https://api.zipteams.com/api/v1/partner"


class ZipteamsError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def to_e164(phone: str | None) -> str | None:
    """``+91XXXXXXXXXX`` for an Indian mobile in any common format (Zipteams wants E.164)."""
    n = normalize_phone(phone)
    return "+" + n if n else None


def to_utc_z(value: str | datetime) -> str:
    """ISO 8601 in UTC with a trailing ``Z`` (Partner API format). Naive datetimes are rejected."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ZipteamsError(f"Timestamp needs a timezone: {value!r}")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(value: Any) -> Any:
    """Recursively drop ``None`` and empty-string values (Zipteams rejects ``""``)."""
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if v is not None and v != ""}
    return value


class ZipteamsClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_secret: str | None = None,
        tenant_id: str | None = None,
        sub_tenant_id: str | None = None,
        ingest_url: str | None = None,
        customer_sync_url: str | None = None,
        partner_base: str | None = None,
        timeout: float = 60,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        env = os.environ.get
        self.api_key = api_key or env("ZIPTEAMS_API_KEY")
        if not self.api_key:
            raise ZipteamsError("Missing credentials: set ZIPTEAMS_API_KEY")
        self.partner = {
            "x-api-key": self.api_key,
            "x-api-secret": api_secret or env("ZIPTEAMS_API_SECRET"),
            "x-tenant-id": tenant_id or env("ZIPTEAMS_TENANT_ID"),
            "x-sub-tenant-id": sub_tenant_id or env("ZIPTEAMS_SUB_TENANT_ID"),
        }
        self.mode = "partner" if all(self.partner.values()) else "customer"
        self.ingest_url = ingest_url or env("ZIPTEAMS_INGEST_URL") or DEFAULT_INGEST_URL
        self.customer_sync_url = customer_sync_url or env("ZIPTEAMS_CUSTOMER_SYNC_URL") or DEFAULT_CUSTOMER_SYNC_URL
        self.partner_base = (partner_base or env("ZIPTEAMS_PARTNER_BASE") or DEFAULT_PARTNER_BASE).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        if self.mode == "partner":
            return dict(self.partner)
        return {"x-zip-api-key": self.api_key}

    def request(self, method: str, url: str, json: Any = None) -> Any:
        """One API call. Retries on 429/5xx and connection errors."""
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, json=json, headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise ZipteamsError(f"{method} {url} failed: {exc}") from exc
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            break

        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = resp.text
        if not resp.ok:
            raise ZipteamsError(f"{method} {url} -> HTTP {resp.status_code}: {data}", status_code=resp.status_code, payload=data)
        return data

    # -------------------------------------------------------------- calls

    def sync_calls(self, records: Iterable[dict]) -> Any:
        """Send calls for analysis.

        Each record: ``{"call": {"id", "recording_url", "start_time", "end_time"?, "phone_number"?},
        "agent": {"id", "email", "name"?}, "customer"?: {"id"?, "name"?, "email"?, "disposition_status"?},
        "custom_fields"?: [{"internal_name", "value"}], "callback_url"?, "metadata"?}``.
        The Partner API additionally needs ``call.end_time``, ``call.phone_number`` and ``customer.id``.
        """
        records = list(records)
        if not records:
            return None
        if self.mode == "partner":
            data = []
            for r in records:
                call, customer = r["call"], r.get("customer") or {}
                missing = [k for k, v in (("call.end_time", call.get("end_time")),
                                          ("call.phone_number", call.get("phone_number")),
                                          ("customer.id", customer.get("id"))) if not v]
                if missing:
                    raise ZipteamsError(f"Partner API needs {', '.join(missing)} for call {call.get('id')}")
                data.append(clean({
                    "call": {
                        "id": call["id"],
                        "recording_url": call["recording_url"],
                        "start_time": to_utc_z(call["start_time"]),
                        "end_time": to_utc_z(call["end_time"]),
                        "contact_number": to_e164(call["phone_number"]),
                    },
                    "agent": r["agent"],
                    "customer": customer,
                    "callback_url": r.get("callback_url"),
                    "metadata": r.get("metadata"),
                    "custom_fields": r.get("custom_fields"),
                }))
            return self.request("POST", f"{self.partner_base}/ingest/batch-call", json={"data": data})

        data = []
        for r in records:
            call = {**r["call"], "end_time": None, "phone_number": to_e164(r["call"].get("phone_number"))}
            data.append(clean({**r, "call": call}))
        return self.request("POST", self.ingest_url, json={"data": data})

    # -------------------------------------------------------- disposition

    def update_dispositions(self, records: Iterable[dict]) -> Any:
        """Update customers' disposition status.

        Each record: ``{"agent": {"id", "email"}, "customer": {"id"?, "phone_number"?, "email"?,
        "disposition_status"}, "custom_fields"?}``. The Customer API matches by phone/email; the
        Partner API keys on ``customer.id`` only (the id used at ingestion).
        """
        records = list(records)
        if not records:
            return None
        if self.mode == "partner":
            results = []
            for r in records:
                customer = r["customer"]
                if not customer.get("id"):
                    raise ZipteamsError("Partner API disposition update needs customer.id (the id used at ingestion)")
                results.append(self.request("PUT", f"{self.partner_base}/ingest/disposition-status", json={
                    "customer_id": customer["id"],
                    "disposition_status": customer.get("disposition_status"),
                }))
            return results
        data = [clean({**r, "customer": {**r["customer"], "phone_number": to_e164(r["customer"].get("phone_number"))}})
                for r in records]
        return self.request("POST", self.ingest_url, json={"type": "disposition-status", "data": data})

    # ----------------------------------------------------------- customer

    def upsert_customer(
        self,
        agent_email: str,
        *,
        name: str | None = None,
        email: str | None = None,
        phone_number: str | None = None,
        disposition_status: str | None = None,
        custom_fields: list[dict] | None = None,
    ) -> Any:
        """Customer API only: create or update a customer without a call."""
        if self.mode == "partner":
            raise ZipteamsError("Customer upsert is a Customer API endpoint; with Partner credentials, ingest a call instead")
        return self.request("POST", self.customer_sync_url, json=clean({
            "agent_email": agent_email,
            "name": name,
            "email": email,
            "phone_number": to_e164(phone_number),
            "disposition_status": disposition_status,
            "custom_fields": custom_fields,
        }))
