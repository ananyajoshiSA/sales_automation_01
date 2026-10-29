"""Fetch a team's leads and phone/payment/Zip activity for a date range into a JSON snapshot.

    python scripts/fetch_team_data.py "Team Elite Calling" [more groups...] 2026-10-02 2026-10-07 data/snapshot.json [previous_snapshot.json]

Dates are IST calendar days (end inclusive). Several groups (one leader's teams) are fetched as one team.
Activity days are read FETCH_WORKERS (default 8) at a time; every read still waits at the shared admission gate.
Calls are kept if made by a team
member or made to a lead the team owns, by when they started, including calls edited up to 3 days later.

Given an earlier snapshot of the same team that starts on or before ``start``, only activities edited since
shortly before that snapshot was taken are read; its other rows are reused. Leads and users are always read
in full. A lead that joined the team since then brings only its newer calls.

If LeadSquared refuses the company-wide activity endpoint (its firewall has answered 403 on and off since 13 Oct
2026), the read is tried again twice, 2 minutes apart, because the block comes and goes. If it is still refused,
the team leads active since the read start are read one by one instead. That route misses only calls a team member made to a
lead another team owns.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

from analytics.daily_plan import revival
from analytics.daily_plan.common import SALES_ONLY
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
    "mx_Report_Type", "mx_Lead_Request_type", "mx_Bootcamp_collections",
    "mx_Bot_Discovery_Done_Date", "mx_Bot_Call_Comments", "mx_Bot_Calling_Status", "mx_Conversation_Summary",
    "mx_Occupation", "mx_Education_Qualification",
]
BULK_RETRIES = 2        # further tries of the bulk read after a 403, before reading lead by lead
BULK_RETRY_WAIT = 120   # seconds between them
sleep = time.sleep
PAYMENT_SUCCESS = 213
ZIP_EVENTS = (236, 237)
ACTIVITY_DATES = ("ProspectActivityDate_Max", "mx_Last_Called_Date_DT", "ModifiedOn")
WORKERS = int(os.environ.get("FETCH_WORKERS", "8"))


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

    def mine(a):  # filtered inside each reading worker, so only the team's rows are held in memory
        call = parse_phone_call(a)
        return call["user_id"] in user_ids or call["lead_id"] in lead_ids

    def theirs(a):
        return a.get("RelatedProspectId") in lead_ids

    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        kept = 0
        for a in c.iter_activities_started(ev, d0, last, edited_since=since, workers=WORKERS, keep=mine):
            if mine(a):
                calls.append(parse_phone_call(a))
                kept += 1
        log(f"event {ev}: {kept} kept")
    for ev, sink in ((PAYMENT_SUCCESS, payments),) + tuple((z, zip_acts) for z in ZIP_EVENTS):
        to = last if ev == PAYMENT_SUCCESS else zip_to
        for a in c.iter_activities_started(ev, d0, to, edited_since=since, workers=WORKERS, keep=theirs):
            if a.get("RelatedProspectId") in lead_ids:
                a["note"] = parse_activity_note(a.get("ActivityEvent_Note"))
                sink.append(a)
    return calls, payments, zip_acts


def bulk_with_retries(c, *args):
    """bulk_reads, tried again after a pause while LeadSquared's firewall refuses it (403); the last refusal is raised."""
    for attempt in range(BULK_RETRIES + 1):
        try:
            return bulk_reads(c, *args)
        except LeadSquaredError as e:
            if e.status_code != 403 or attempt == BULK_RETRIES:
                raise
            log(f"bulk activity endpoint refused (403); trying again in {BULK_RETRY_WAIT} s ({attempt + 1} of {BULK_RETRIES})")
            sleep(BULK_RETRY_WAIT)


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


def outside_leads(c, calls, lead_ids, known: dict) -> dict:
    """Collection tag and stage of each lead the team called that another team owns, so a team in SALES_ONLY can leave
    out collection leads parked with other owners. Leads looked up by an earlier pull are not read again."""
    out = {lid: known[lid] for lid in {x.get("lead_id") for x in calls} - lead_ids if lid in known}
    for lid in sorted({x.get("lead_id") for x in calls if x.get("lead_id")} - lead_ids - set(out)):
        l = c.get_lead_by_id(lid) or {}
        out[lid] = {k: l.get(k) for k in ("OwnerIdName", "ProspectStage", "mx_Bootcamp_collections")}
    log(f"{len(out)} leads owned outside the team were called")
    return out


def main(group: str | list[str], start, end, out, prev=None):
    c = LeadSquaredClient()
    groups = [group] if isinstance(group, str) else list(group)
    team = " + ".join(groups)
    d0 = ist_day_start(start)
    d1 = min(ist_day_start(end) + timedelta(days=1), now_utc())
    base = json.load(open(prev)) if isinstance(prev, str) else prev
    since = reusable(base, team, start)
    if since:
        log(f"reusing {base['fetched_at']} snapshot; reading edits since {since.isoformat()}")

    users = list({u["ID"]: u for g in groups for u in c.get_users_in_group(g)}.values())
    user_ids = {u["ID"] for u in users}
    log(f"{len(users)} users in {', '.join(map(repr, groups))}")

    leads = list({l["ProspectID"]: l for g in groups for l in c.iter_leads_by_group(g, columns=LEAD_COLUMNS)}.values())
    lead_ids = {l["ProspectID"] for l in leads}
    log(f"{len(leads)} team-owned leads")

    last = d1 - timedelta(seconds=1)
    zip_to = min(last + ZIP_LATE_WINDOW, now_utc())  # a call ending after midnight
    try:
        calls, payments, zip_acts = bulk_with_retries(c, d0, last, zip_to, since, user_ids, lead_ids)
        source = "bulk"
    except LeadSquaredError as e:
        if e.status_code != 403:
            raise
        log("bulk activity endpoint still refused (403): reading active team leads one by one")
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
    outside = outside_leads(c, calls, lead_ids, (base or {}).get("outside_leads", {})) if team in SALES_ONLY else {}

    json.dump(
        {
            "group": team, "groups": groups, "start": start, "end": end,
            "fetched_at": now_utc().isoformat(),
            "users": users, "leads": leads, "calls": calls,
            "payments": payments, "zip_activities": zip_acts, "outside_leads": outside, "source": source,
        },
        open(out, "w"),
    )
    log(f"saved {out}: {len(calls)} calls, {len(payments)} payments, {len(zip_acts)} zip activities")


def cli(argv: list[str]) -> tuple:
    """GROUP [GROUP ...] START END OUT [PREVIOUS]: the groups run up to the first date."""
    i = next(k for k, x in enumerate(argv) if len(x) == 10 and x[4] == "-" and x[7] == "-" and x.replace("-", "").isdigit())
    groups = argv[:i]
    return (groups[0] if len(groups) == 1 else groups, *argv[i:i + 4])


if __name__ == "__main__":
    main(*cli(sys.argv[1:]))
