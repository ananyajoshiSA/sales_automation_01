"""Read-only LeadSquared inputs for the module: users, calls, leads, Zipteams notes, payments, first enrolments.

Activities are read with ``iter_activities_started`` (3 days of later edits, kept by ``CreatedOn``, single-page
windows), as the project's time-zone rules require. Nothing here writes to LeadSquared. Results that take many
requests (lead details, first enrolments) are cached under data/convintel/sources/ (git-ignored: lead ids and
owners), and a cache is reused while it is younger than ``max_age``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Callable, Iterable, Iterator

from analytics.definitions import ENROLLED
from analytics.zip_calls import ZIP_LATE_WINDOW
from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND
from integrations.leadsquared.client import format_datetime
from integrations.timeutil import ist_day, ist_day_start, now_utc, utc

ZIP_NOTES, PAYMENT_SUCCESS, STAGE_CHANGE = 237, 213, 3002
SOURCES_DIR = os.path.join("data", "convintel", "sources")
LEAD_COLUMNS = ("ProspectID", "ProspectStage", "mx_Enquired_Course", "OwnerId", "OwnerIdName", "Score",
                "mx_Next_follow_up_date")
LEADS_PER_REQUEST = 200
CONVERSION_DAYS = 3        # a lead enrolled within 3 days of a call still counts for the period (report P3)


def day_window(day_from: str, day_to: str) -> tuple[datetime, datetime]:
    """[00:00 IST of day_from, 23:59:59 IST of day_to]."""
    return ist_day_start(day_from), ist_day_start(day_to) + timedelta(days=1) - timedelta(seconds=1)


def days(day_from: str, day_to: str) -> list[str]:
    d, out = ist_day_start(day_from), []
    while d <= ist_day_start(day_to):
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def cached(name: str, fetch: Callable[[], object], max_age: timedelta | None, refresh: bool = False,
           base: str = SOURCES_DIR):
    """``fetch()``'s result, kept in ``base/name.json`` and reused while younger than ``max_age``."""
    path = os.path.join(base, f"{name}.json")
    if not refresh and max_age is not None and os.path.exists(path):
        doc = json.load(open(path, encoding="utf-8"))
        if (t := utc(doc.get("fetched_utc"))) and now_utc() - t < max_age:
            return doc["data"]
    data = fetch()
    os.makedirs(base, exist_ok=True)
    json.dump({"fetched_utc": format_datetime(now_utc()), "data": data}, open(path, "w", encoding="utf-8"))
    return data


def calls(client, d0: datetime, d1: datetime, now: datetime | None = None) -> Iterator[dict]:
    """Raw outbound and inbound call activities that started in [d0, d1]."""
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        yield from client.iter_activities_started(ev, d0, d1, now=now)


def zip_notes(client, d0: datetime, d1: datetime) -> list[dict]:
    return list(client.iter_activities_started(ZIP_NOTES, d0, d1 + ZIP_LATE_WINDOW))  # a call ending after midnight


def payments(client, d0: datetime, d1: datetime) -> list[dict]:
    return list(client.iter_activities_started(PAYMENT_SUCCESS, d0, d1 + timedelta(days=CONVERSION_DAYS)))


def leads(client, ids: Iterable[str], columns: Iterable[str] = LEAD_COLUMNS) -> dict[str, dict]:
    """lead_id -> {"stage", "course", "owner_id", "owner_name", "score", "next_follow_up_utc"} for the given
    leads, LEADS_PER_REQUEST at a time (Leads/Retrieve/ByIds)."""
    ids, out = list(dict.fromkeys(i for i in ids if i)), {}
    for i in range(0, len(ids), LEADS_PER_REQUEST):
        chunk, page = ids[i:i + LEADS_PER_REQUEST], 1
        while True:
            r = client.request("POST", "LeadManagement.svc/Leads/Retrieve/ByIds", json={
                "SearchParameters": {"LeadIds": chunk}, "Columns": {"Include_CSV": ",".join(columns)},
                "Paging": {"PageIndex": page, "PageSize": LEADS_PER_REQUEST}}) or {}
            rows = r.get("Leads") or []
            for l in rows:
                score = l.get("Score")
                out[l["ProspectID"]] = {
                    "stage": l.get("ProspectStage"), "course": (l.get("mx_Enquired_Course") or "").strip() or None,
                    "owner_id": l.get("OwnerId"), "owner_name": (l.get("OwnerIdName") or "").strip() or None,
                    "score": int(float(score)) if score not in (None, "") else None,
                    "next_follow_up_utc": l.get("mx_Next_follow_up_date") or None}
            if len(rows) < LEADS_PER_REQUEST or page * LEADS_PER_REQUEST >= int(r.get("RecordCount") or 0):
                break
            page += 1
    return out


def first_enrolments(client, d0: datetime, d1: datetime) -> list[dict]:
    """First-ever "Course Enrolled" stage changes in [d0, d1]: {"lead_id", "at_utc", "ist_day", "owner_id",
    "owner_name", "set_by"}. Leads now in the stage are read newest edit first; a lead first enrolled in the
    window was edited then too, so the scan stops at the first lead last edited before d0."""
    out = []
    for l in client.iter_leads("ProspectStage", ENROLLED, columns=["ProspectID", "ModifiedOn", "OwnerId", "OwnerIdName"],
                               sort_by="ModifiedOn", page_size=1000):
        mod = utc(l.get("ModifiedOn"))
        if mod and mod < d0:
            break
        hist = (client.get_lead_activities(l["ProspectID"], activity_event=STAGE_CHANGE, row_count=100) or {}).get(
            "ProspectActivities") or []
        firsts = sorted(((t, {d.get("Key"): d.get("Value") for d in h.get("Data") or []})
                         for h in hist if (t := utc(h.get("CreatedOn")))), key=lambda x: x[0])
        first = next(((t, d) for t, d in firsts if d.get("CurrentStage") == ENROLLED), None)
        if first and d0 <= first[0] <= d1:
            out.append({"lead_id": l["ProspectID"], "at_utc": format_datetime(first[0]), "ist_day": ist_day(first[0]),
                        "owner_id": l.get("OwnerId"), "owner_name": (l.get("OwnerIdName") or "").strip() or None,
                        "set_by": (first[1].get("CreatedBy") or "").strip() or None})
    return out
