"""LeadSquared REST API client.

Docs: https://apidocs.leadsquared.com/
Auth is via ``accessKey`` / ``secretKey`` query parameters on every request.
Credentials are read from the environment, never hard-coded:

    LEADSQUARED_HOST        e.g. api-in21.leadsquared.com or https://api-in21.leadsquared.com/v2/
    LEADSQUARED_ACCESS_KEY
    LEADSQUARED_SECRET_KEY
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Mapping

import requests

from integrations.timeutil import EDIT_MARGIN, now_utc, utc

DEFAULT_HOST = "https://api-in21.leadsquared.com/v2/"


def normalize_host(host: str) -> str:
    """``https://<host>/v2/`` from a bare host (``api-in21.leadsquared.com``) or a full base URL."""
    host = host.strip().rstrip("/")
    if "://" not in host:
        host = "https://" + host
    if not host.endswith("/v2"):
        host += "/v2"
    return host + "/"


class LeadSquaredError(Exception):
    """Raised when the API returns an error or an unexpected response."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def to_attributes(fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert ``{"FirstName": "A"}`` into LeadSquared's ``[{"Attribute":..., "Value":...}]`` form."""
    return [{"Attribute": k, "Value": "" if v is None else v} for k, v in fields.items()]


def parse_activity_note(note: str | None) -> dict[str, str]:
    """Split LeadSquared's ``Key{=}Value{next}...`` activity note into a dict.

    Repeated keys keep the last non-empty value.
    """
    out: dict[str, str] = {}
    for part in (note or "").split("{next}"):
        if "{=}" not in part:
            continue
        key, _, value = part.partition("{=}")
        if value or key not in out:
            out[key] = value
    return out


PHONE_INBOUND = 21
PHONE_OUTBOUND = 22


def parse_phone_call(activity: dict) -> dict[str, Any]:
    """Flatten an inbound/outbound phone call activity into one record.

    ``start_utc`` is the call start in UTC (``CreatedOn``); ``modified_utc`` is the last edit, which is what
    the activity API filters on. ``status`` is e.g. Answered / NotAnswered / CallFailure (outbound) or
    Answered / Missed (inbound). The note's ``StartTime`` fields are not used: one is UTC, the other IST,
    and neither says which (``integrations/timeutil.py``).
    """
    import json as _json

    note = parse_activity_note(activity.get("ActivityEvent_Note"))
    src: dict = {}
    if note.get("SourceData"):
        try:
            src = _json.loads(note["SourceData"])
        except ValueError:
            src = {}
    try:
        duration = int(float(note.get("Duration") or 0))
    except ValueError:
        duration = 0
    event = int(activity.get("ActivityEvent") or 0)
    return {
        "activity_id": activity.get("ProspectActivityId") or activity.get("Id"),
        "lead_id": activity.get("RelatedProspectId"),
        "direction": "inbound" if event == PHONE_INBOUND else "outbound",
        "start_utc": activity.get("CreatedOn"),
        "modified_utc": activity.get("ModifiedOn"),
        "user_id": note.get("UserId") or activity.get("Owner") or activity.get("CreatedBy"),
        "caller": note.get("Caller") or activity.get("CreatedByName"),
        "status": note.get("Status") or activity.get("Status"),
        "duration": duration,
        "call_notes": note.get("CallNotes"),
        "recording_url": note.get("ResourceURL"),
        "lead_number": src.get("DestinationNumber") if event == PHONE_OUTBOUND else src.get("SourceNumber"),
        "display_number": note.get("DisplayNumber"),
    }


def format_datetime(dt: datetime) -> str:
    """LeadSquared expects UTC timestamps as ``YYYY-MM-DD HH:MM:SS``."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _is_api_error(resp: requests.Response) -> bool:
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("Status") == "Error"


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
        self.host = normalize_host(host)
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

            # 429 = rate limited, 5xx = transient server error. LeadSquared also uses
            # 500 for validation errors (JSON body with Status=Error) — don't retry those.
            if (resp.status_code == 429 or (resp.status_code >= 500 and not _is_api_error(resp))) \
                    and attempt < self.max_retries:
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

    def iter_activities_by_event(
        self,
        activity_event: int,
        from_dt: datetime,
        to_dt: datetime,
        page_size: int = 1000,
    ) -> Iterator[dict]:
        """Every activity of one type created in ``[from_dt, to_dt]`` across all leads.

        Datetimes are converted to UTC. ``page_size`` is capped at 1000 by the API.
        """
        page = 1
        while True:
            data = self.request(
                "POST",
                "ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent",
                json={
                    "Parameter": {
                        "FromDate": format_datetime(from_dt),
                        "ToDate": format_datetime(to_dt),
                        "ActivityEvent": activity_event,
                    },
                    "Paging": {"PageIndex": page, "PageSize": page_size},
                },
            ) or {}
            batch = data.get("List") or []
            yield from batch
            if len(batch) < page_size:
                return
            page += 1

    def iter_activities_started(
        self,
        activity_event: int,
        from_dt: datetime,
        to_dt: datetime,
        margin: timedelta = EDIT_MARGIN,
        now: datetime | None = None,
    ) -> Iterator[dict]:
        """Every activity of one type created (for a call: started) in ``[from_dt, to_dt]``.

        The API's date filter is on ``ModifiedOn``, so a plain query misses activities edited after
        ``to_dt`` and returns older ones edited inside it. This reads edits from ``from_dt`` to
        ``to_dt + margin`` (capped at now), one day per query, keeps those whose ``CreatedOn`` is in the
        window and drops repeats. An activity with a missing or unreadable ``CreatedOn`` is kept if it was
        edited in the window, so the caller can count it.
        """
        stop = min(to_dt + margin, now or now_utc())
        seen: set = set()
        day = from_dt
        while day <= stop:
            end = min(day + timedelta(days=1) - timedelta(seconds=1), stop)
            for a in self.iter_activities_by_event(activity_event, day, end):
                key = a.get("ProspectActivityId") or a.get("Id")
                if key and key in seen:
                    continue
                t = utc(a.get("CreatedOn")) or utc(a.get("ModifiedOn"))
                if t and from_dt <= t <= to_dt:
                    seen.add(key)
                    yield a
            day = end + timedelta(seconds=1)

    # ----------------------------------------------------------------- users

    def get_users(self) -> list[dict]:
        """Sales users / lead owners in the account."""
        return self.request("GET", "UserManagement.svc/Users.Get")

    def get_users_in_group(self, group: str) -> list[dict]:
        """Users whose ``MemberOfGroups`` contains ``group`` (case-insensitive)."""
        g = group.strip().lower()
        return [
            u for u in self.get_users()
            if any(m.strip().lower() == g for m in (u.get("MemberOfGroups") or []))
        ]

    def iter_leads_by_group(self, group: str, columns: Iterable[str] | None = None, **kwargs) -> Iterator[dict]:
        """Yield every lead owned by a member of the given user group."""
        for user in self.get_users_in_group(group):
            yield from self.iter_leads("OwnerId", user["ID"], page_size=1000, columns=columns, **kwargs)
