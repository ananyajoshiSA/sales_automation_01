"""Read-only LeadSquared pulls for the pipeline: the team snapshot and recently enrolled leads with stage history."""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timedelta

from analytics.daily_plan.common import TEAM
from analytics.team_performance import ENROLLED, first_enrollment
from integrations.leadsquared import LeadSquaredClient, format_datetime
from integrations.timeutil import ist_day_start, utc

STAGE_CHANGE = 3002


def snapshot(date: str, days_back: int, out: str, team: str = TEAM, prev: str | None = None) -> str:
    """fetch_team_data for [date - days_back, date] (capped at now), seeded from ``prev`` when it can be."""
    from scripts.fetch_team_data import main as fetch_team

    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fetch_team(team, start, date, out, prev)
    return out


def _header(path: str) -> dict:
    """A snapshot's ``start`` and ``fetched_at`` without parsing 30 MB of JSON: fetch_team_data writes them first."""
    with open(path) as f:
        head = f.read(4096)
    out = {}
    for k in ("start", "fetched_at"):
        i = head.find(f'"{k}": "')
        if i >= 0:
            j = i + len(k) + 5
            out[k] = head[j:head.index('"', j)]
    return out


def latest_snapshot(root: str = "data/daily", start: str | None = None) -> str | None:
    """The most recently fetched team snapshot under ``root`` (morning, status or nightly) that starts on or before ``start``."""
    paths = [p for p in glob.glob(os.path.join(root, "*", "snap.json")) + glob.glob(os.path.join(root, "*", "status_*.json"))
             if not p.endswith("_enrolled.json")]
    stamped = [(h["fetched_at"], p) for p in paths if (h := _header(p)).get("fetched_at") and (not start or h.get("start", "9999") <= start)]
    return max(stamped)[1] if stamped else None


def cached_enrolled(root: str = "data/daily", days: int = 3) -> dict:
    """Enrolled leads from every saved pull in the latest few day folders, by lead ID (newest file wins).

    All files count, not just the newest few: a status pull covers one day and would hide the morning's full list."""
    dirs = sorted(d for d in glob.glob(os.path.join(root, "2*")) if os.path.isdir(d))[-days:]
    paths = sorted((p for d in dirs for p in glob.glob(os.path.join(d, "enrolled.json")) + glob.glob(os.path.join(d, "status_*_enrolled.json"))),
                   key=os.path.getmtime)
    out = {}
    for p in paths:
        try:
            out.update((r["ProspectID"], r) for r in json.load(open(p)))
        except (OSError, ValueError):
            continue
    return out


def enrolled(since: str, out: str, client: LeadSquaredClient | None = None, cache: dict | None = None) -> str:
    """Leads now in Course Enrolled and edited since ``since`` (IST day), each with its stage history and first enrolment.

    A lead whose first enrolment is already in ``cache`` keeps its cached history: a first enrolment never changes.
    """
    c = client or LeadSquaredClient()
    cache = cache or {}
    d0 = ist_day_start(since)
    rows, fetched = [], 0
    for l in c.iter_leads("ProspectStage", ENROLLED, columns=["ProspectID", "FirstName", "LastName", "Phone", "ModifiedOn", "OwnerId",
                                                              "OwnerIdName", "mx_Enquired_Course", "CreatedOn"], sort_by="ModifiedOn", page_size=1000):
        mod = utc(l.get("ModifiedOn"))
        if mod and mod < d0:
            break
        old = cache.get(l["ProspectID"])
        if old and old.get("first_enrolled"):
            rows.append({**l, "first_enrolled": old["first_enrolled"], "history": old.get("history") or []})
            continue
        hist = (c.get_lead_activities(l["ProspectID"], activity_event=STAGE_CHANGE, row_count=100) or {}).get("ProspectActivities") or []
        fetched += 1
        first = first_enrollment(hist)
        rows.append({**l, "first_enrolled": format_datetime(first) if first else None, "history": hist})
    json.dump(rows, open(out, "w"))
    print(f"enrolled leads edited since {since}: {len(rows)} ({fetched} histories read, {len(rows) - fetched} from cache)", file=sys.stderr)
    return out
