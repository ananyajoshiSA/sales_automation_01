"""Fetch a team's leads and phone/payment/Zip activity for a date range into a JSON snapshot.

    python scripts/fetch_team_data.py "Team Elite Calling" [more groups...] 2026-10-02 2026-10-07 data/snapshot.json

Dates are IST calendar days (end inclusive). Several groups (one leader's teams) are fetched as one team.
Days and lead owners are read 8 at a time (FETCH_WORKERS), so a window of several weeks takes minutes, not hours. Calls are kept if made by a team
member or made to a lead the team owns, by when they started, including calls edited up to 3 days later.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from analytics.zip_calls import ZIP_LATE_WINDOW
from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_activity_note, parse_phone_call
from integrations.timeutil import ist_day_start, now_utc
LEAD_COLUMNS = [
    "ProspectID", "FirstName", "LastName", "Phone", "EmailAddress", "OwnerId", "OwnerIdName",
    "Source", "SourceCampaign", "mx_Campaign_Name", "ProspectStage", "CreatedOn", "mx_Assigned_On",
    "mx_Assigned_By", "mx_Lead_Capture_Date_and_time", "mx_Next_follow_up_date",
    "mx_Follow_up_date_and_time", "mx_Enquired_Course", "mx_Enrollment_date", "mx_Enrollment_Opted",
    "mx_Last_Called_Date_DT", "ProspectActivityName_Max", "ProspectActivityDate_Max", "ModifiedOn",
    "mx_Zip_Intent_Type", "mx_Zip_Intent_Score", "mx_Zip_Quality_Score", "mx_Zip_AI_Disposition",
    "mx_Zip_Objection_Category", "mx_Zip_Objection", "mx_Zip_Disposition", "mx_Zip_Intent",
    "mx_Zip_Intent_Justification", "mx_Zip_Talking_Points", "mx_Zip_Team_Follow_up_date_and_time",
    "mx_Bot_Discovery_Done_Date", "mx_Bot_Call_Comments", "mx_Bot_Calling_Status", "mx_Conversation_Summary",
    "mx_Occupation", "mx_Education_Qualification",
]
PAYMENT_SUCCESS = 213
ZIP_EVENTS = (236, 237)
WORKERS = int(os.environ.get("FETCH_WORKERS", "8"))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def main(group: str | list[str], start, end, out):
    c = LeadSquaredClient()
    d0 = ist_day_start(start)
    d1 = min(ist_day_start(end) + timedelta(days=1), now_utc())
    groups = [group] if isinstance(group, str) else list(group)

    users = list({u["ID"]: u for g in groups for u in c.get_users_in_group(g)}.values())
    user_ids = {u["ID"] for u in users}
    log(f"{len(users)} users in {', '.join(map(repr, groups))}")

    with ThreadPoolExecutor(WORKERS) as pool:
        owned = pool.map(lambda u: list(c.clone().iter_leads("OwnerId", u["ID"], page_size=1000, columns=LEAD_COLUMNS)), users)
        leads = [l for batch in owned for l in batch]
    lead_ids = {l["ProspectID"] for l in leads}
    log(f"{len(leads)} team-owned leads")

    calls, payments, zip_acts = [], [], []
    last = d1 - timedelta(seconds=1)
    mine = lambda call: call["user_id"] in user_ids or call["lead_id"] in lead_ids  # noqa: E731
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        kept = [parse_phone_call(a) for a in c.iter_activities_started(ev, d0, last, workers=WORKERS,
                                                                       keep=lambda a: mine(parse_phone_call(a)))]
        calls.extend(kept)
        log(f"event {ev}: {len(kept)} kept")
    for ev, sink in ((PAYMENT_SUCCESS, payments),) + tuple((z, zip_acts) for z in ZIP_EVENTS):
        to = last if ev == PAYMENT_SUCCESS else min(last + ZIP_LATE_WINDOW, now_utc())  # a call ending after midnight
        for a in c.iter_activities_started(ev, d0, to, workers=WORKERS, keep=lambda a: a.get("RelatedProspectId") in lead_ids):
            a["note"] = parse_activity_note(a.get("ActivityEvent_Note"))
            sink.append(a)
    log(f"payments {len(payments)}, zip {len(zip_acts)}")

    json.dump(
        {
            "group": " + ".join(groups), "groups": groups, "start": start, "end": end,
            "fetched_at": now_utc().isoformat(),
            "users": users, "leads": leads, "calls": calls,
            "payments": payments, "zip_activities": zip_acts,
        },
        open(out, "w"),
    )
    log(f"saved {out}: {len(calls)} calls, {len(payments)} payments, {len(zip_acts)} zip activities")


if __name__ == "__main__":
    *groups, start, end, out = sys.argv[1:]
    main(groups, start, end, out)
