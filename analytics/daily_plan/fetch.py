"""Read-only LeadSquared pulls for the pipeline: the team snapshot and recently enrolled leads with stage history."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

from analytics.daily_plan.common import TEAM
from analytics.team_performance import ENROLLED, first_enrollment
from integrations.leadsquared import LeadSquaredClient, format_datetime
from integrations.timeutil import ist_day_start, utc

STAGE_CHANGE = 3002


def snapshot(date: str, days_back: int, out: str, team: str = TEAM) -> str:
    """fetch_team_data for [date - days_back, date] (capped at now)."""
    from scripts.fetch_team_data import main as fetch_team

    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fetch_team(team, start, date, out)
    return out


def enrolled(since: str, out: str, client: LeadSquaredClient | None = None) -> str:
    """Leads now in Course Enrolled and edited since ``since`` (IST day), each with its stage history and first enrolment."""
    c = client or LeadSquaredClient()
    d0 = ist_day_start(since)
    rows = []
    for l in c.iter_leads("ProspectStage", ENROLLED, columns=["ProspectID", "FirstName", "LastName", "Phone", "ModifiedOn", "OwnerId",
                                                              "OwnerIdName", "mx_Enquired_Course", "CreatedOn"], sort_by="ModifiedOn", page_size=1000):
        mod = utc(l.get("ModifiedOn"))
        if mod and mod < d0:
            break
        hist = (c.get_lead_activities(l["ProspectID"], activity_event=STAGE_CHANGE, row_count=100) or {}).get("ProspectActivities") or []
        first = first_enrollment(hist)
        rows.append({**l, "first_enrolled": format_datetime(first) if first else None, "history": hist})
    json.dump(rows, open(out, "w"))
    print(f"enrolled leads edited since {since}: {len(rows)}", file=sys.stderr)
    return out
