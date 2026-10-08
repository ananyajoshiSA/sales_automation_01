"""Calling-pattern and call-quality analysis for one sales team.

Input is a snapshot written by ``scripts/fetch_team_data.py``. All times are
reported in IST.

    python -m analytics.team_report data/snapshot.json 2026-10-02 2026-10-06 exports/report [data/hist.jsonl]

The optional history file (``scripts/fetch_lead_histories.py``) dates each enrolment by its first
stage change to Course Enrolled and finds the owner at that moment; without it the enrolment date
field and the current owner are used, and each row says which.
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from analytics.definitions import BUCKETS, ENROLLED, REAL_CONVERSATION_SECS, WORKING_DAY_DIALS
from analytics.definitions import speed_bucket as bucket_minutes

IST = timezone(timedelta(hours=5, minutes=30))
WORK_START, WORK_END = 10, 20  # IST hours used for "assigned during working hours"
MEANINGFUL_SECS = REAL_CONVERSATION_SECS  # a connected call shorter than this is not a real conversation

# Zipteams scores each skill pass/fail (0 or 100) per call, so the mean is "% of calls
# where the skill was shown". "Clear Call to Action" (mx_Custom_7) is defined but not
# populated by Zip as of Oct 2026, so it is left out.
ZIP_SCORES = {
    "mx_Custom_5": "probing_%",
    "mx_Custom_4": "product_pitch_%",
    "mx_Custom_6": "objection_handling_%",
}


# ------------------------------------------------------------------ helpers

def activity_data(a: dict) -> dict:
    return {d.get("Key"): d.get("Value") for d in a.get("Data") or []}


def enrolment_credit(lead: dict, acts: list[dict] | None, answered: list[tuple]) -> dict | None:
    """When a lead first enrolled, who owned it then, and who closed it (plan step 5).

    ``acts`` is the lead's activity history (or None); ``answered`` is its answered team calls as
    sorted (time, caller) pairs. The closer is the last answered caller at or before enrolment.
    """
    t = owner = None
    if acts:
        stages = sorted((utc(a["CreatedOn"]), activity_data(a)) for a in acts
                        if a.get("EventName") == "StageChange" and utc(a.get("CreatedOn")))
        t = next((ts for ts, d in stages if d.get("CurrentStage") == ENROLLED), None)
        for at, d in sorted((utc(a["CreatedOn"]), activity_data(a)) for a in acts
                            if a.get("EventName") == "LeadAssigned" and utc(a.get("CreatedOn"))):
            if t and at <= t:
                owner = d.get("CurrentOwner") or owner
    source = "stage history" if t else "enrolment date field"
    t = t or utc(lead.get("mx_Enrollment_date"))
    if not t:
        return None
    prior = [name for at, name in answered if at <= t]
    return {"lead_id": lead["ProspectID"], "enrolled_ist": t.astimezone(IST).strftime("%Y-%m-%d %H:%M"), "t": t,
            "owner_now": lead.get("OwnerIdName") or "",
            "owner_at_enrolment": owner or lead.get("OwnerIdName") or "",
            "owner_source": "assignment history" if owner else "current owner",
            "closer": prior[-1] if prior else "", "date_source": source}


def utc(s: str | None) -> datetime | None:
    """Parse LeadSquared's ``YYYY-MM-DD HH:MM:SS[.fff]`` UTC strings."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def ist_day(dt: datetime | None) -> str | None:
    return dt.astimezone(IST).strftime("%Y-%m-%d") if dt else None


def minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


def zip_score(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        v = json.loads(raw).get("mx_CustomObject_1")
        return float(v) if v not in (None, "") else None
    except (ValueError, AttributeError):
        m = re.search(r"\d+(\.\d+)?", str(raw))
        return float(m.group()) if m else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def pct(a, b):
    return round(100 * a / b) if b else None


# ----------------------------------------------------------------- analysis

def analyse(snap: dict, start: str, end: str) -> dict:
    users = {u["ID"]: f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip() for u in snap["users"]}
    leads = {l["ProspectID"]: l for l in snap["leads"]}
    d0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    d1 = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)
    now = utc(snap["fetched_at"][:19].replace("T", " "))
    in_window = lambda dt: dt is not None and d0 <= dt < d1  # noqa: E731

    calls = []
    for c in snap["calls"]:
        c = dict(c)
        c["t"] = utc(c["start_utc"])
        c["answered"] = (c.get("status") or "").lower() == "answered"
        c["team"] = c.get("user_id") in users
        calls.append(c)
    calls.sort(key=lambda c: c["t"] or datetime.min.replace(tzinfo=timezone.utc))
    by_lead = defaultdict(list)
    for c in calls:
        by_lead[c["lead_id"]].append(c)

    # ---- 1. activity per caller per day
    per_day = defaultdict(lambda: defaultdict(Counter))
    first_last = defaultdict(lambda: defaultdict(list))
    leads_dialed = defaultdict(lambda: defaultdict(set))
    for c in calls:
        if not (c["team"] and in_window(c["t"])):
            continue
        name, day = users[c["user_id"]], ist_day(c["t"])
        k = per_day[name][day]
        k["dials" if c["direction"] == "outbound" else "inbound"] += 1
        if c["answered"]:
            k["connected"] += 1
            k["talk_secs"] += c["duration"]
            if c["duration"] >= MEANINGFUL_SECS:
                k["meaningful"] += 1
        leads_dialed[name][day].add(c["lead_id"])
        first_last[name][day].append(c["t"])

    caller_rows = []
    for name in sorted(users.values()):
        days = per_day.get(name, {})
        # working days (20+ dials) only; a caller with none is still shown, averaged over the days they called
        active = [d for d in days if days[d]["dials"] >= WORKING_DAY_DIALS] or [d for d in days if days[d]["dials"] + days[d]["inbound"]]
        tot = Counter()
        for d in active:
            tot.update(days[d])
        starts = [min(first_last[name][d]).astimezone(IST) for d in active]
        ends = [max(first_last[name][d]).astimezone(IST) for d in active]
        n = len(active) or 1
        attempts = tot["dials"] + tot["inbound"]
        caller_rows.append({
            "caller": name,
            "active_days": len(active),
            "below_working_day": not any(days[d]["dials"] >= WORKING_DAY_DIALS for d in days),
            "dials_per_day": round(tot["dials"] / n),
            "connected_per_day": round(tot["connected"] / n),
            "connect_rate_%": pct(tot["connected"], attempts),
            "meaningful_per_day": round(tot["meaningful"] / n),
            "talk_hrs_per_day": round(tot["talk_secs"] / 3600 / n, 1),
            "unique_leads_per_day": round(sum(len(leads_dialed[name][d]) for d in active) / n),
            "typical_first_call": min(starts).strftime("%H:%M") if starts else "",
            "median_first_call": _median_time(starts),
            "median_last_call": _median_time(ends),
        })

    daily_rows = []
    for name in sorted(per_day):
        for day in sorted(per_day[name]):
            k = per_day[name][day]
            daily_rows.append({
                "caller": name, "day": day, "dials": k["dials"], "inbound": k["inbound"],
                "connected": k["connected"], "meaningful_2min+": k["meaningful"],
                "talk_min": round(k["talk_secs"] / 60),
                "first_call": min(first_last[name][day]).astimezone(IST).strftime("%H:%M"),
                "last_call": max(first_last[name][day]).astimezone(IST).strftime("%H:%M"),
            })

    # ---- 2. speed to lead (leads assigned to the team in the window)
    new_leads = [l for l in leads.values() if in_window(utc(l.get("mx_Assigned_On")))]
    stl_rows = []
    for l in new_leads:
        at = utc(l["mx_Assigned_On"])
        lead_calls = [c for c in by_lead.get(l["ProspectID"], []) if c["t"] and c["t"] >= at]
        after = [c for c in lead_calls if c["direction"] == "outbound"]
        # first contact attempt: an outbound dial, or an inbound call the team answered
        contacts = [c for c in lead_calls if c["direction"] == "outbound" or c["answered"]]
        first = contacts[0]["t"] if contacts else None
        connect = next((c["t"] for c in lead_calls if c["answered"]), None)
        h = at.astimezone(IST).hour
        first_24h = [c for c in after if c["t"] <= at + timedelta(hours=24)]
        stl_rows.append({
            "lead_id": l["ProspectID"],
            "name": f"{l.get('FirstName') or ''} {l.get('LastName') or ''}".strip(),
            "phone": l.get("Phone"),
            "owner": l.get("OwnerIdName"),
            "source": l.get("Source") or "(blank)",
            "stage": l.get("ProspectStage"),
            "assigned_ist": at.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
            # Assigned On also moves when an old lead is reassigned; fresh = created within a day
            "fresh_lead": bool(utc(l.get("CreatedOn")) and at - utc(l["CreatedOn"]) <= timedelta(days=1)),
            "working_hours": WORK_START <= h < WORK_END,
            "mins_to_first_dial": round(minutes(at, first)) if first else None,
            "mins_to_first_connect": round(minutes(at, connect)) if connect else None,
            "dials_first_24h": len(first_24h),
            "connected_ever": connect is not None,
            "total_dials": len(after),
            "hours_since_assigned": round(minutes(at, now) / 60, 1),
        })

    def stl_summary(rows):
        out = Counter(bucket_minutes(r["mins_to_first_dial"]) for r in rows)
        return {b: out.get(b, 0) for b in BUCKETS}

    wh = [r for r in stl_rows if r["working_hours"]]
    stl_by_owner = []
    for owner in sorted({r["owner"] for r in wh}):
        rs = [r for r in wh if r["owner"] == owner]
        stl_by_owner.append({
            "owner": owner, "leads": len(rs),
            "median_mins_to_dial": median([r["mins_to_first_dial"] for r in rs]),
            "within_5m_%": pct(sum(1 for r in rs if r["mins_to_first_dial"] is not None and r["mins_to_first_dial"] <= 5), len(rs)),
            "within_1h_%": pct(sum(1 for r in rs if r["mins_to_first_dial"] is not None and r["mins_to_first_dial"] <= 60), len(rs)),
            "never_dialed": sum(1 for r in rs if r["mins_to_first_dial"] is None),
            "never_connected": sum(1 for r in rs if not r["connected_ever"]),
            "avg_dials_24h": round(sum(r["dials_first_24h"] for r in rs) / len(rs), 1),
        })
    stl_by_source = []
    for src, n in Counter(r["source"] for r in wh).most_common(8):
        rs = [r for r in wh if r["source"] == src]
        stl_by_source.append({
            "source": src, "leads": n,
            "median_mins_to_dial": median([r["mins_to_first_dial"] for r in rs]),
            "within_5m_%": pct(sum(1 for r in rs if r["mins_to_first_dial"] is not None and r["mins_to_first_dial"] <= 5), n),
            "never_dialed": sum(1 for r in rs if r["mins_to_first_dial"] is None),
        })

    # ---- 3. missed inbound calls and callbacks
    missed_rows = []
    for c in calls:
        if c["direction"] != "inbound" or c["answered"] or not in_window(c["t"]):
            continue
        lead = leads.get(c["lead_id"])
        if not lead and not c["team"]:
            continue
        later = [x for x in by_lead.get(c["lead_id"], []) if x["t"] and x["t"] > c["t"]]
        cb = next((x for x in later if x["direction"] == "outbound"), None)
        cb_connect = next((x for x in later if x["answered"]), None)
        missed_rows.append({
            "lead_id": c["lead_id"],
            "owner": (lead or {}).get("OwnerIdName") or users.get(c["user_id"], c.get("caller")),
            "missed_at_ist": c["t"].astimezone(IST).strftime("%Y-%m-%d %H:%M"),
            "status": c["status"],
            "mins_to_callback": round(minutes(c["t"], cb["t"])) if cb else None,
            "callback_by": cb["caller"] if cb else "",
            "reconnected": cb_connect is not None,
        })
    # one row per lead per day: repeated misses from the same lead are one problem
    seen, missed_unique = set(), []
    for r in missed_rows:
        key = (r["lead_id"], r["missed_at_ist"][:10])
        if key not in seen:
            seen.add(key)
            missed_unique.append(r)

    # ---- 4. follow-ups that were due in the window
    fu_rows = []
    for l in leads.values():
        due = utc(l.get("mx_Next_follow_up_date")) or utc(l.get("mx_Follow_up_date_and_time"))
        if not in_window(due) or due > now:
            continue
        dials = [c for c in by_lead.get(l["ProspectID"], []) if c["t"] and c["direction"] == "outbound"]
        near = [c for c in dials if abs(minutes(due, c["t"])) <= 120]
        same_day = [c for c in dials if ist_day(c["t"]) == ist_day(due)]
        fu_rows.append({
            "lead_id": l["ProspectID"], "owner": l.get("OwnerIdName"), "stage": l.get("ProspectStage"),
            "due_ist": due.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
            "called_within_2h": bool(near), "called_same_day": bool(same_day),
        })

    # ---- 5. call quality (Zipteams notes)
    q_by_owner = defaultdict(lambda: defaultdict(list))
    intents = defaultdict(Counter)
    for a in snap.get("zip_activities", []):
        if str(a.get("ActivityEvent")) != "237" or not in_window(utc(a.get("CreatedOn"))):
            continue
        t = utc(a["CreatedOn"])
        # attribute to the team member whose connected call on this lead most recently preceded the note
        prior = [c for c in by_lead.get(a["RelatedProspectId"], []) if c["answered"] and c["t"] and c["t"] <= t]
        if prior and prior[-1]["team"]:
            owner = users[prior[-1]["user_id"]]
        else:
            owner = (leads.get(a["RelatedProspectId"]) or {}).get("OwnerIdName")
        if not owner:
            continue
        for field, label in ZIP_SCORES.items():
            s = zip_score(a.get(field))
            if s is not None:
                q_by_owner[owner][label].append(s)
        q_by_owner[owner]["_n"].append(1)
        intents[owner][(a.get("mx_Custom_1") or "UNKNOWN").upper()] += 1

    quality_rows = []
    for owner in sorted(q_by_owner):
        q = q_by_owner[owner]
        ic = intents[owner]
        n = len(q["_n"])
        row = {"caller": owner, "analysed_calls": n}
        for label in ZIP_SCORES.values():
            row[label] = round(statistics.mean(q[label])) if q[label] else None
        rated = n - ic.get("NOT_AVAILABLE", 0) - ic.get("UNKNOWN", 0)
        row["intent_rated_calls"] = rated
        row["high_or_moderate_intent_%"] = pct(ic.get("HIGH", 0) + ic.get("MODERATE", 0), rated)
        row["low_intent_%"] = pct(ic.get("LOW", 0), rated)
        quality_rows.append(row)

    # ---- 6. outcomes
    outcome_rows = []
    for owner in sorted({l.get("OwnerIdName") for l in new_leads}):
        ls = [l for l in new_leads if l.get("OwnerIdName") == owner]
        st = Counter(l.get("ProspectStage") or "(blank)" for l in ls)
        outcome_rows.append({
            "owner": owner, "assigned": len(ls),
            "enrolled": st.get("Course Enrolled", 0),
            "follow_up_for_closure": st.get("Follow Up For Closure", 0),
            "not_interested": st.get("Not Interested", 0),
            "call_not_picking": st.get("Call Not Picking Up", 0),
            "still_new": st.get("New Lead", 0),
        })
    histories = snap.get("histories") or {}
    answered = defaultdict(list)
    for c in calls:
        if c["answered"] and c["team"] and c["t"]:
            answered[c["lead_id"]].append((c["t"], users[c["user_id"]]))
    enrolment_rows = []
    for lid, l in leads.items():
        e = enrolment_credit(l, histories.get(lid), answered.get(lid, []))
        if e and in_window(e.pop("t")):
            enrolment_rows.append(e)
    enrolment_rows.sort(key=lambda e: e["enrolled_ist"])

    return {
        "window": f"{start} to {end}",
        "fetched_at_ist": now.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
        "callers": caller_rows,
        "daily": daily_rows,
        "speed_to_lead_all": stl_summary(stl_rows),
        "speed_to_lead_working_hours": stl_summary(wh),
        "speed_to_lead_by_owner": stl_by_owner,
        "speed_to_lead_by_source": stl_by_source,
        "speed_to_lead_rows": stl_rows,
        "missed_inbound": missed_unique,
        "follow_ups": fu_rows,
        "quality": quality_rows,
        "outcomes": outcome_rows,
        # credited to the owner at enrolment and to the last answered caller, never the owner now
        "enrolment_rows": enrolment_rows,
        "enrolled_by_owner": Counter(e["owner_at_enrolment"] for e in enrolment_rows),
        "enrolled_by_closer": Counter(e["closer"] or "(no answered call)" for e in enrolment_rows),
    }


def _median_time(times):
    if not times:
        return ""
    mins = sorted(t.hour * 60 + t.minute for t in times)
    m = mins[len(mins) // 2]
    return f"{m // 60:02d}:{m % 60:02d}"


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def load_histories(path: str) -> dict[str, list]:
    """lead_id -> activities from a fetch_lead_histories.py file, skipping leads whose fetch errored."""
    out = {}
    for line in open(path):
        d = json.loads(line)
        if d.get("activities") is not None and not d.get("error"):
            out[d["lead_id"]] = d["activities"]
    return out


def main(snapshot, start, end, out_dir, histories=None):
    snap = json.load(open(snapshot))
    if histories:
        snap["histories"] = load_histories(histories)
    r = analyse(snap, start, end)
    os.makedirs(out_dir, exist_ok=True)
    for key in ("callers", "daily", "speed_to_lead_rows", "speed_to_lead_by_owner", "missed_inbound",
                "follow_ups", "quality", "outcomes", "enrolment_rows"):
        write_csv(os.path.join(out_dir, f"{key}.csv"), r[key])
    json.dump(r, open(os.path.join(out_dir, "report.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in r.items() if not k.endswith("_rows") and k not in ("daily", "missed_inbound", "follow_ups")},
                     indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:6])
