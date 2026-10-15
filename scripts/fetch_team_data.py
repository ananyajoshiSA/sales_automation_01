"""Fetch a team's leads and phone/payment/Zip activity for a date range into a JSON snapshot.

    python scripts/fetch_team_data.py "Team Elite Calling" 2026-10-02 2026-10-07 data/snapshot.json [previous_snapshot.json]

Dates are IST calendar days (end inclusive). Calls are kept if made by a team
member or made to a lead the team owns, by when they started, including calls edited up to 3 days later.

Given an earlier snapshot of the same team that starts on or before ``start``, only activities edited since
shortly before that snapshot was taken are read; its other rows are reused. Leads and users are always read
in full. A lead that joined the team since then brings only its newer calls.

If LeadSquared refuses the company-wide activity endpoint (it answered 403 from 13 Oct 2026), the team leads
active since the read start are read one by one instead. That route misses only calls a team member made to a
lead another team owns.
"""

import json
import os
import sys
from datetime import datetime, timedelta

from analytics.daily_plan import revival
from analytics.zip_calls import ZIP_LATE_WINDOW
from integrations.leadsquared import (
    PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, LeadSquaredError, parse_activity_note, parse_phone_call,
)
from integrations.timeutil import ist_day_start, now_utc, utc

REFRESH_OVERLAP = timedelta(hours=2)  # re-read edits from a little before the earlier snapshot was taken
LEAD_COLUMNS = [
    "ProspectID", "FirstName", "LastName", "Phone", "EmailAddress", "OwnerId", "OwnerIdName",
    "Source", "SourceCampaign", "mx_Campaign_Name", "ProspectStage", "CreatedOn", "mx_Assigned_On",
    "mx_Assigned_By", "mx_Lead_Capture_Date_and_time", "mx_Next_follow_up_date",
    "mx_Follow_up_date_and_time", "mx_Enquired_Course", "mx_Enrollment_date", "mx_Enrollment_Opted",
    "mx_Last_Called_Date_DT", "ProspectActivityName_Max", "ProspectActivityDate_Max", "ModifiedOn",
    "mx_Zip_Intent_Type", "mx_Zip_Intent_Score", "mx_Zip_Quality_Score", "mx_Zip_AI_Disposition",
    "mx_Zip_Objection_Category", "mx_Zip_Objection", "mx_Zip_Disposition", "mx_Zip_Intent",
    "mx_Zip_Intent_Justification", "mx_Zip_Talking_Points", "mx_Zip_Team_Follow_up_date_and_time",
    "mx_Report_Type", "mx_Lead_Request_type",
]
PAYMENT_SUCCESS = 213
ZIP_EVENTS = (236, 237)
ACTIVITY_DATES = ("ProspectActivityDate_Max", "mx_Last_Called_Date_DT", "ModifiedOn")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def reusable(prev: dict | None, group: str, start: str):
    """When ``prev`` can seed this fetch: the earliest edit time to re-read, else None."""
    if not prev or prev.get("group") != group or prev.get("start", "9999") > start or not prev.get("fetched_at"):
        return None
    fetched = datetime.fromisoformat(prev["fetched_at"])  # written by now_utc().isoformat(), so it carries its zone
    covered = min(ist_day_start(prev["end"]) + timedelta(days=1), fetched)  # rows started after this were never read
    return min(fetched, covered) - REFRESH_OVERLAP


def _merge(old: list[dict], new: list[dict], key: str, keep) -> list[dict]:
    rows = {r[key]: r for r in old if keep(r)}
    rows.update((r[key], r) for r in new)
    return list(rows.values())


def bulk_reads(c, d0, last, zip_to, since, user_ids, lead_ids):
    """Calls, payments and Zip notes from the company-wide activity endpoint."""
    calls, payments, zip_acts = [], [], []
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        n = kept = 0
        for a in c.iter_activities_started(ev, d0, last, edited_since=since):
            n += 1
            call = parse_phone_call(a)
            if call["user_id"] in user_ids or call["lead_id"] in lead_ids:
                calls.append(call)
                kept += 1
        log(f"event {ev}: {n} total, {kept} kept")
    for ev, sink in ((PAYMENT_SUCCESS, payments),) + tuple((z, zip_acts) for z in ZIP_EVENTS):
        to = last if ev == PAYMENT_SUCCESS else zip_to
        for a in c.iter_activities_started(ev, d0, to, edited_since=since):
            if a.get("RelatedProspectId") in lead_ids:
                a["note"] = parse_activity_note(a.get("ActivityEvent_Note"))
                sink.append(a)
    return calls, payments, zip_acts


def active_since(leads: list[dict], t: datetime) -> list[str]:
    """Team leads with any activity or edit at or after ``t``."""
    def touched(l):
        return any((u := utc(l.get(k))) is not None and u >= t for k in ACTIVITY_DATES)

    return [l["ProspectID"] for l in leads if touched(l)]


def per_lead_reads(c, leads, users, d0, last, zip_to, since, cache):
    """The same rows read lead by lead, for the team leads active since the read start."""
    ids = active_since(leads, since or d0)
    log(f"{len(ids)} team leads active since {(since or d0).isoformat()}")
    if os.path.exists(cache):
        os.remove(cache)  # histories change between runs: never reuse an earlier run's
    hist = revival.history(ids, cache, client=c, codes=(PHONE_OUTBOUND, PHONE_INBOUND, PAYMENT_SUCCESS) + ZIP_EVENTS)
    os.remove(cache)
    names = {u["ID"]: f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip() for u in users}
    calls, payments, zip_acts = [], [], []
    for lid, acts in hist.items():
        for a in acts:
            a["RelatedProspectId"] = a.get("RelatedProspectId") or lid
            t, ev = utc(a.get("CreatedOn")), a["ActivityEvent"]
            if t is None or t < d0:
                continue
            if ev in (PHONE_OUTBOUND, PHONE_INBOUND) and t <= last:
                call = parse_phone_call(a)
                call["caller"] = call["caller"] or names.get(call["user_id"])  # these rows carry no creator name
                calls.append(call)
            elif ev == PAYMENT_SUCCESS and t <= last:
                payments.append({**a, "note": parse_activity_note(a.get("ActivityEvent_Note"))})
            elif ev in ZIP_EVENTS and t <= zip_to:
                zip_acts.append({**a, "note": parse_activity_note(a.get("ActivityEvent_Note"))})
    log(f"per-lead: {len(calls)} calls")
    return calls, payments, zip_acts


def main(group, start, end, out, prev=None):
    c = LeadSquaredClient()
    d0 = ist_day_start(start)
    d1 = min(ist_day_start(end) + timedelta(days=1), now_utc())
    base = json.load(open(prev)) if isinstance(prev, str) else prev
    since = reusable(base, group, start)
    if since:
        log(f"reusing {base['fetched_at']} snapshot; reading edits since {since.isoformat()}")

    users = c.get_users_in_group(group)
    user_ids = {u["ID"] for u in users}
    log(f"{len(users)} users in {group!r}")

    leads = list(c.iter_leads_by_group(group, columns=LEAD_COLUMNS))
    lead_ids = {l["ProspectID"] for l in leads}
    log(f"{len(leads)} team-owned leads")

    last = d1 - timedelta(seconds=1)
    zip_to = min(last + ZIP_LATE_WINDOW, now_utc())  # a call ending after midnight
    try:
        calls, payments, zip_acts = bulk_reads(c, d0, last, zip_to, since, user_ids, lead_ids)
        source = "bulk"
    except LeadSquaredError as e:
        if e.status_code != 403:
            raise
        log("bulk activity endpoint refused (403): reading active team leads one by one")
        calls, payments, zip_acts = per_lead_reads(c, leads, users, d0, last, zip_to, since, out + ".hist.jsonl")
        source = "per-lead fallback"
    if since:
        def started(t, hi):
            u = utc(t)
            return u is not None and d0 <= u <= hi

        calls = _merge(base["calls"], calls, "activity_id",
                       lambda r: started(r.get("start_utc"), last) and (r.get("user_id") in user_ids or r.get("lead_id") in lead_ids))
        payments = _merge(base.get("payments", []), payments, "ProspectActivityId",
                          lambda r: started(r.get("CreatedOn"), last) and r.get("RelatedProspectId") in lead_ids)
        zip_acts = _merge(base.get("zip_activities", []), zip_acts, "ProspectActivityId",
                          lambda r: started(r.get("CreatedOn"), zip_to) and r.get("RelatedProspectId") in lead_ids)
    log(f"payments {len(payments)}, zip {len(zip_acts)}")

    json.dump(
        {
            "group": group, "start": start, "end": end,
            "fetched_at": now_utc().isoformat(),
            "users": users, "leads": leads, "calls": calls,
            "payments": payments, "zip_activities": zip_acts, "source": source,
        },
        open(out, "w"),
    )
    log(f"saved {out}: {len(calls)} calls, {len(payments)} payments, {len(zip_acts)} zip activities")


if __name__ == "__main__":
    main(*sys.argv[1:6])
