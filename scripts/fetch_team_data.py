"""Fetch a team's leads and phone/payment/Zip activity for a date range into a JSON snapshot.

    python scripts/fetch_team_data.py "Team Elite Calling" 2026-10-02 2026-10-07 data/snapshot.json

Dates are IST calendar days (end inclusive). Calls are kept if made by a team
member or made to a lead the team owns.
"""

import json
import sys
from datetime import datetime, timedelta, timezone

from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_activity_note, parse_phone_call

IST = timezone(timedelta(hours=5, minutes=30))
LEAD_COLUMNS = [
    "ProspectID", "FirstName", "LastName", "Phone", "EmailAddress", "OwnerId", "OwnerIdName",
    "Source", "SourceCampaign", "mx_Campaign_Name", "ProspectStage", "CreatedOn", "mx_Assigned_On",
    "mx_Assigned_By", "mx_Lead_Capture_Date_and_time", "mx_Next_follow_up_date",
    "mx_Follow_up_date_and_time", "mx_Enquired_Course", "mx_Enrollment_date", "mx_Enrollment_Opted",
    "mx_Last_Called_Date_DT", "ProspectActivityName_Max", "ProspectActivityDate_Max", "ModifiedOn",
    "mx_Zip_Intent_Type", "mx_Zip_Intent_Score", "mx_Zip_Quality_Score", "mx_Zip_AI_Disposition",
    "mx_Zip_Objection_Category", "mx_Zip_Objection", "mx_Zip_Disposition", "mx_Zip_Intent",
    "mx_Zip_Intent_Justification", "mx_Zip_Talking_Points", "mx_Zip_Team_Follow_up_date_and_time",
]
PAYMENT_SUCCESS = 213
ZIP_EVENTS = (236, 237)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def main(group, start, end, out):
    c = LeadSquaredClient()
    d0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    d1 = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)
    d1 = min(d1, datetime.now(timezone.utc))

    users = c.get_users_in_group(group)
    user_ids = {u["ID"] for u in users}
    log(f"{len(users)} users in {group!r}")

    leads = list(c.iter_leads_by_group(group, columns=LEAD_COLUMNS))
    lead_ids = {l["ProspectID"] for l in leads}
    log(f"{len(leads)} team-owned leads")

    calls, payments, zip_acts = [], [], []
    day = d0
    while day < d1:
        nxt = min(day + timedelta(days=1), d1)
        for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
            n = kept = 0
            for a in c.iter_activities_by_event(ev, day, nxt - timedelta(seconds=1)):
                n += 1
                call = parse_phone_call(a)
                if call["user_id"] in user_ids or call["lead_id"] in lead_ids:
                    calls.append(call)
                    kept += 1
            log(f"{day:%Y-%m-%d} event {ev}: {n} total, {kept} kept")
        for ev, sink in ((PAYMENT_SUCCESS, payments),) + tuple((z, zip_acts) for z in ZIP_EVENTS):
            for a in c.iter_activities_by_event(ev, day, nxt - timedelta(seconds=1)):
                if a.get("RelatedProspectId") in lead_ids:
                    a["note"] = parse_activity_note(a.get("ActivityEvent_Note"))
                    sink.append(a)
        log(f"{day:%Y-%m-%d} payments so far {len(payments)}, zip {len(zip_acts)}")
        day = nxt

    json.dump(
        {
            "group": group, "start": start, "end": end,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "users": users, "leads": leads, "calls": calls,
            "payments": payments, "zip_activities": zip_acts,
        },
        open(out, "w"),
    )
    log(f"saved {out}: {len(calls)} calls, {len(payments)} payments, {len(zip_acts)} zip activities")


if __name__ == "__main__":
    main(*sys.argv[1:5])
