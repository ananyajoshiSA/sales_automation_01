"""What happened to every fresh lead: capture -> assignment -> dials -> conversation -> stage.

Uses each lead's full LeadSquared activity history (ProspectActivity.svc/Retrieve),
so calls made before the lead reached the team, assignment hops and stage
changes are all visible. Times are IST.

    python -m analytics.fresh_leads data/snapshot.json data/fresh_activities.json exports/fresh
"""

from __future__ import annotations

import csv
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from analytics.team_report import IST, utc, zip_score
from integrations.leadsquared import parse_activity_note

WORK_START, WORK_END = 10, 20
REAL_CONVERSATION_SECS = 120
WON = {"Course Enrolled"}
PIPELINE = {"Follow Up For Closure", "Counselled lead", "Discovery Call Done", "Roadmap\xa0Done", "May buy later",
            "Opportunity Created"}
DEAD = {"Not Interested", "Irrelevant lead", "Invalid"}


def _data(a: dict) -> dict:
    return {d.get("Key"): d.get("Value") for d in a.get("Data") or []}


def _call(a: dict) -> dict:
    note = parse_activity_note((a.get("ActivityFields") or {}).get("ActivityEvent_Note"))
    d = _data(a)
    try:
        dur = int(float(note.get("Duration") or d.get("Duration") or 0))
    except ValueError:
        dur = 0
    return {
        "t": utc(a["CreatedOn"]),
        "direction": "inbound" if a.get("EventCode") == 21 else "outbound",
        "status": note.get("Status") or (a.get("ActivityFields") or {}).get("Status"),
        "by": note.get("Caller") or note.get("Reciever") or d.get("Caller") or d.get("Receiver") or "",
        "duration": dur,
    }


def mins(a, b):
    return round((b - a).total_seconds() / 60) if a and b else None


def bucket(m):
    if m is None:
        return "never"
    for lim, lab in ((5, "≤5 min"), (30, "5–30 min"), (60, "30–60 min"), (240, "1–4 h"), (1440, "4–24 h")):
        if m <= lim:
            return lab
    return ">24 h"


BUCKETS = ["≤5 min", "5–30 min", "30–60 min", "1–4 h", "4–24 h", ">24 h", "never"]


def analyse_lead(lead: dict, acts: list[dict], team: set[str], now: datetime) -> dict:
    acts = sorted((a for a in acts if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
    created = utc(lead.get("CreatedOn"))
    captures = [utc(a["CreatedOn"]) for a in acts if a.get("EventName") == "Lead Capture"]
    calls = [_call(a) for a in acts if a.get("EventCode") in (21, 22)]
    outbound = [c for c in calls if c["direction"] == "outbound"]
    answered = [c for c in calls if c["status"] == "Answered"]
    real = [c for c in answered if c["duration"] >= REAL_CONVERSATION_SECS]
    inbound = [c for c in calls if c["direction"] == "inbound"]

    assigns = [(utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventName") == "LeadAssigned"]
    to_team = next(((t, d) for t, d in assigns if d.get("CurrentOwner") in team), None)
    team_at = to_team[0] if to_team else None
    holders = [d.get("PreviousOwner") for t, d in assigns if team_at and t <= team_at and d.get("PreviousOwner")]

    stages = [(utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventName") == "StageChange"]
    stage_path = [d.get("CurrentStage") for _, d in stages]
    stage = lead.get("ProspectStage") or ""
    dead_without_talk = stage in DEAD and not real
    dead_comment = next((d.get("Comment") for _, d in reversed(stages) if d.get("CurrentStage") == stage), "") if stage in DEAD else ""

    zips = [a for a in acts if a.get("EventCode") == 237]
    zip_intents = [(a.get("ActivityFields") or {}).get("mx_Custom_1") or "" for a in zips]

    first_dial = outbound[0]["t"] if outbound else None
    first_team_dial = next((c["t"] for c in outbound if c["by"] in team), None)
    first_answer = answered[0]["t"] if answered else None
    first_real = real[0]["t"] if real else None
    ch = created.astimezone(IST)
    inbound_first = bool(inbound and (not outbound or inbound[0]["t"] <= outbound[0]["t"]))
    last_dial = outbound[-1]["t"] if outbound else None

    return {
        "lead_id": lead["ProspectID"],
        "name": f"{lead.get('FirstName') or ''} {lead.get('LastName') or ''}".strip(),
        "phone": lead.get("Phone"),
        "owner": lead.get("OwnerIdName"),
        "source": lead.get("Source") or "(blank)",
        "course": lead.get("mx_Enquired_Course") or "",
        "created_ist": ch.strftime("%Y-%m-%d %H:%M"),
        "created_day": ch.strftime("%a %d %b"),
        "created_in_hours": WORK_START <= ch.hour < WORK_END,
        "captures": len(captures),
        "holders_before_team": " > ".join(dict.fromkeys(h for h in holders if h not in team)),
        "assigned_by": lead.get("mx_Assigned_By") or "",
        "reassigned_within_team": sum(1 for t, d in assigns if team_at and t > team_at and d.get("CurrentOwner") in team),
        "mins_create_to_team": mins(created, team_at),
        "mins_create_to_first_dial": mins(created, first_dial),
        "mins_team_to_first_dial": mins(team_at, first_team_dial) if team_at and first_team_dial and first_team_dial >= team_at else None,
        "dialled_before_team": bool(team_at and first_dial and first_dial < team_at),
        "mins_create_to_first_answer": mins(created, first_answer),
        "mins_create_to_real_conversation": mins(created, first_real),
        "lead_called_us_first": inbound_first,
        "inbound_calls": len(inbound),
        "inbound_missed": sum(1 for c in inbound if c["status"] != "Answered"),
        "dials": len(outbound),
        "dials_not_answered": sum(1 for c in outbound if c["status"] != "Answered"),
        "answered_calls": len(answered),
        "real_conversations": len(real),
        "longest_call_min": round(max((c["duration"] for c in answered), default=0) / 60, 1),
        "distinct_dial_days": len({c["t"].astimezone(IST).date() for c in outbound}),
        "hours_since_last_dial": round((now - last_dial).total_seconds() / 3600, 1) if last_dial else None,
        "zip_notes": len(zips),
        "zip_best_intent": max(zip_intents, key=lambda i: {"HIGH": 4, "MODERATE": 3, "NEUTRAL": 2, "LOW": 1}.get(i.upper(), 0), default=""),
        "zip_pitch_done": any(zip_score((a.get("ActivityFields") or {}).get("mx_Custom_4")) == 100 for a in zips),
        "stage": stage,
        "stage_path": " > ".join(stage_path),
        "dead_without_real_conversation": dead_without_talk,
        "dead_comment": dead_comment or "",
        "hours_old": round((now - created).total_seconds() / 3600, 1),
    }


def pct(a, b):
    return round(100 * a / b) if b else 0


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs)) if xs else None


def fmt_mins(m):
    if m is None:
        return "–"
    return f"{m} min" if m < 60 else f"{m / 60:.1f} h"


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    funnel = [
        ("Created", n),
        ("Assigned to a team caller", sum(1 for r in rows if r["mins_create_to_team"] is not None)),
        ("Dialled at least once", sum(1 for r in rows if r["dials"])),
        ("Answered at least once", sum(1 for r in rows if r["answered_calls"])),
        ("Real conversation (2 min+)", sum(1 for r in rows if r["real_conversations"])),
        ("Zip intent High/Moderate", sum(1 for r in rows if r["zip_best_intent"].upper() in ("HIGH", "MODERATE"))),
        ("In pipeline stage now", sum(1 for r in rows if r["stage"] in PIPELINE)),
        ("Course Enrolled", sum(1 for r in rows if r["stage"] in WON)),
    ]
    return {"n": n, "funnel": funnel}


def group_table(rows, key, order=None):
    groups = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    keys = order or sorted(groups, key=lambda k: -len(groups[k]))
    out = []
    for k in keys:
        g = groups.get(k, [])
        if not g:
            continue
        out.append({
            "group": k, "leads": len(g),
            "median_create_to_team": fmt_mins(med(r["mins_create_to_team"] for r in g)),
            "median_create_to_first_dial": fmt_mins(med(r["mins_create_to_first_dial"] for r in g)),
            "dialled_%": pct(sum(1 for r in g if r["dials"]), len(g)),
            "answered_%": pct(sum(1 for r in g if r["answered_calls"]), len(g)),
            "real_conv_%": pct(sum(1 for r in g if r["real_conversations"]), len(g)),
            "pipeline_or_won_%": pct(sum(1 for r in g if r["stage"] in PIPELINE | WON), len(g)),
            "enrolled": sum(1 for r in g if r["stage"] in WON),
            "dead_no_talk": sum(1 for r in g if r["dead_without_real_conversation"]),
        })
    return out


def main(snapshot, activities, out_dir, start="2026-10-01"):
    snap = json.load(open(snapshot))
    acts = json.load(open(activities))
    team = {f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip() for u in snap["users"]}
    now = datetime.fromisoformat(snap["fetched_at"])
    d0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    leads = [l for l in snap["leads"] if l["ProspectID"] in acts and utc(l.get("CreatedOn")) and utc(l["CreatedOn"]) >= d0]
    rows = [analyse_lead(l, acts[l["ProspectID"]], team, now) for l in leads]
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "fresh_leads.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["created_ist"]))
    json.dump(rows, open(os.path.join(out_dir, "fresh_leads.json"), "w"), indent=1)
    print(json.dumps(summarise(rows), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
