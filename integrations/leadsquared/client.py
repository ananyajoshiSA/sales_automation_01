"""LeadSquared REST API client.

Docs: https://apidocs.leadsquared.com/
Auth is via ``accessKey`` / ``secretKey`` query parameters on every request.
Credentials are read from the environment, never hard-coded:

    LEADSQUARED_HOST        e.g. https://api-in21.leadsquared.com/v2/
    LEADSQUARED_ACCESS_KEY
    LEADSQUARED_SECRET_KEY
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping

import requests

DEFAULT_HOST = "https://api-in21.leadsquared.com/v2/"


class LeadSquaredError(Exception):
    """Raised when the API returns an error or an unexpected response."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def to_attributes(fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert ``{"FirstName": "A"}`` into LeadSquared's ``[{"Attribute":..., "Value":...}]`` form."""
    return [{"Attribute": k, "Value": "" if v is None else v} for k, v in fields.items()]


def format_datetime(dt: datetime) -> str:
    """LeadSquared expects UTC timestamps as ``YYYY-MM-DD HH:MM:SS``."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class LeadSquaredClient:
    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        timeout: float = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self.access_key = access_key or os.environ.get("LEADSQUARED_ACCESS_KEY")
        self.secret_key = secret_key or os.environ.get("LEADSQUARED_SECRET_KEY")
        if not self.access_key or not self.secret_key:
            raise LeadSquaredError(
                "Missing credentials: set LEADSQUARED_ACCESS_KEY and LEADSQUARED_SECRET_KEY"
            )
        host = host or os.environ.get("LEADSQUARED_HOST") or DEFAULT_HOST
        self.host = host.rstrip("/") + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ core

    def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        query = {"accessKey": self.access_key, "secretKey": self.secret_key}
        if params:
            query.update({k: v for k, v in params.items() if v is not None})
        url = self.host + path.lstrip("/")

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(
                    method, url, params=query, json=json, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise LeadSquaredError(f"{method} {path} failed: {exc}") from exc

            # 429 = rate limited, 5xx = transient server error
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt)
                continue
            break

        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = resp.text

        if not resp.ok or (isinstance(data, dict) and data.get("Status") == "Error"):
            message = data.get("ExceptionMessage") if isinstance(data, dict) else None
            raise LeadSquaredError(
                f"{method} {path} -> HTTP {resp.status_code}: {message or data}",
                status_code=resp.status_code,
                payload=data,
            )
        return data

    # ----------------------------------------------------------------- leads

    def get_lead_metadata(self) -> list[dict]:
        """All lead fields (standard + custom ``mx_*``) with their schema names and types."""
        return self.request("GET", "LeadManagement.svc/LeadsMetaData.Get")

    def get_lead_by_id(self, lead_id: str) -> dict | None:
        data = self.request("GET", "LeadManagement.svc/Leads.GetById", params={"id": lead_id})
        return data[0] if data else None

    def get_lead_by_email(self, email: str) -> dict | None:
        data = self.request(
            "GET", "LeadManagement.svc/Leads.GetByEmailaddress", params={"emailaddress": email}
        )
        return data[0] if data else None

    def get_lead_by_phone(self, phone: str) -> list[dict]:
        return self.request(
            "GET", "LeadManagement.svc/RetrieveLeadByPhoneNumber", params={"phone": phone}
        ) or []

    def search_leads(
        self,
        lookup_name: str,
        lookup_value: str,
        operator: str = "=",
        columns: Iterable[str] | None = None,
        page_index: int = 1,
        page_size: int = 100,
        sort_by: str = "ModifiedOn",
        descending: bool = True,
    ) -> list[dict]:
        """Search leads by a single field, e.g. ``search_leads("mx_City", "Delhi")``."""
        body: dict[str, Any] = {
            "Parameter": {
                "LookupName": lookup_name,
                "LookupValue": lookup_value,
                "SqlOperator": operator,
            },
            "Paging": {"PageIndex": page_index, "PageSize": page_size},
            "Sorting": {"ColumnName": sort_by, "Direction": "1" if descending else "0"},
        }
        if columns:
            body["Columns"] = {"Include_CSV": ",".join(columns)}
        return self.request("POST", "LeadManagement.svc/Leads.Get", json=body) or []

    def iter_leads(self, lookup_name: str, lookup_value: str, page_size: int = 500, **kwargs) -> Iterator[dict]:
        """Yield every matching lead, paging automatically."""
        page = 1
        while True:
            batch = self.search_leads(
                lookup_name, lookup_value, page_index=page, page_size=page_size, **kwargs
            )
            yield from batch
            if len(batch) < page_size:
                return
            page += 1

    def create_lead(self, fields: Mapping[str, Any]) -> str:
        """Create a lead; returns the new lead (prospect) ID."""
        data = self.request("POST", "LeadManagement.svc/Lead.Create", json=to_attributes(fields))
        return data["Message"]["Id"]

    def update_lead(self, lead_id: str, fields: Mapping[str, Any]) -> None:
        self.request(
            "POST",
            "LeadManagement.svc/Lead.Update",
            params={"leadId": lead_id},
            json=to_attributes(fields),
        )

    def upsert_lead(self, fields: Mapping[str, Any], search_by: str = "EmailAddress") -> str:
        """Create the lead, or update it if one already matches ``search_by``. Returns the lead ID."""
        body = to_attributes(fields) + [{"Attribute": "SearchBy", "Value": search_by}]
        data = self.request("POST", "LeadManagement.svc/Lead.CreateOrUpdate", json=body)
        return data["Message"]["Id"]

    # ------------------------------------------------------------ activities

    def get_activity_types(self) -> list[dict]:
        """Activity types configured in the account (use ``ActivityEvent`` codes from here)."""
        return self.request("GET", "ProspectActivity.svc/ActivityTypes.Get")

    def get_lead_activities(
        self,
        lead_id: str,
        activity_event: int | None = None,
        offset: int = 0,
        row_count: int = 100,
    ) -> dict:
        body: dict[str, Any] = {"Paging": {"Offset": offset, "RowCount": row_count}}
        if activity_event is not None:
            body["Parameter"] = {"ActivityEvent": activity_event}
        return self.request(
            "POST", "ProspectActivity.svc/Retrieve", params={"leadId": lead_id}, json=body
        )

    def post_activity(
        self,
        lead_id: str,
        activity_event: int,
        note: str = "",
        when: datetime | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> str:
        """Log an activity (call, note, custom event) on a lead. Returns the activity ID."""
        body: dict[str, Any] = {
            "RelatedProspectId": lead_id,
            "ActivityEvent": activity_event,
            "ActivityNote": note,
            "ActivityDateTime": format_datetime(when or datetime.now(timezone.utc)),
        }
        if fields:
            body["Fields"] = [{"SchemaName": k, "Value": v} for k, v in fields.items()]
        data = self.request("POST", "ProspectActivity.svc/Create", json=body)
        return data["Message"]["Id"]

    # ----------------------------------------------------------------- users

    def get_users(self) -> list[dict]:
        """Sales users / lead owners in the account."""
        return self.request("GET", "UserManagement.svc/Users.Get")
