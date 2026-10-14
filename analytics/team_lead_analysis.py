"""Lead analysis for one manager's teams over a date range: how leads come in and are used, how they churn,
which bootcamps and communities produce enrolments, and the week-by-week enrolment trend.

Inputs come from ``scripts/fetch_team_lead_analysis.py`` (one directory). All times are IST. Rules:

- **Lead received**: a lead's owner changes to a team member from someone outside the team (owner-change log,
  event 3001). A lead the team owns now with no such record is received at its Assigned On, and says so.
- **Churned out**: the owner changes from a team member to someone outside the team (the pool or another team).
- **First-time enrolment**: the lead's first ever stage change to Course Enrolled (CLAUDE.md). A later stage
  change to Course Enrolled on a lead that had enrolled before is a *repeat* enrolment, counted apart.
  An enrolment is the team's when the owner at that moment, the login that set the stage, or the last caller
  with a real conversation in the 30 days before it is a team member. It is credited to the owner at that
  moment if a team member, else to the team member who set the stage, else to that last caller.
- **Bootcamp** of an enrolment: the latest "Bootcamp attended" opportunity (event 12001) before it, else the
  lead's Enquired Course when that names a bootcamp. **Community**: the latest GrowthX community checkout
  (a ``leadtype`` with "community" in it, or ``COMMUNITY_TYPES``) before the enrolment, matched by phone or email, else a LeadSquared
  community opportunity (event 12000, "Community Enquiry"), whose community name LeadSquared does not record.
- Working day, real conversation and dialer issue follow ``analytics/definitions.py``.

    python -m analytics.team_lead_analysis data/inayat 2026-08-14 2026-10-13 exports/inayat
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.bootcamp_collections import parse_tag
from analytics.definitions import (DEAD_STAGES, DIALER_FAILURE_SHARE, ENROLLED, REAL_CONVERSATION_SECS,
                                   WORKING_DAY_DIALS, speed_bucket, BUCKETS)
from integrations.timeutil import IST, ist_day, ist_day_start, utc

POOL = frozenset({"rinku jhala", "admin"})
NEW_LEAD_DAYS = 7               # a lead received within this many days of its creation is a new lead
CLOSER_WINDOW = timedelta(days=30)
EARLY = timedelta(minutes=5)    # a call logged a few minutes before the owner change still follows it
LS_COMMUNITY = "Community (name not in LeadSquared)"


def norm(name: str | None) -> str:
    return " ".join((name or "").split()).lower()


def _fields(a: dict) -> dict:
    return a.get("ActivityFields") or {}


def _data(a: dict) -> dict:
    return {d.get("Key"): d.get("Value") for d in a.get("Data") or []}


def owner_changes(acts: list[dict]) -> list[tuple[datetime, str, str, str]]:
    """(time, previous owner, new owner, changed by) from the owner-change log, oldest first."""
    out = []
    for a in acts:
        if a.get("EventCode") == 3001 and (t := utc(a.get("CreatedOn"))):
            d = _data(a)
            out.append((t, d.get("PreviousOwner") or "", d.get("CurrentOwner") or "", d.get("CreatedBy") or ""))
    return sorted(out)


def stage_changes(acts: list[dict]) -> list[tuple[datetime, str, str, str]]:
    """(time, previous stage, new stage, set by), oldest first."""
    out = []
    for a in acts:
        if a.get("EventCode") == 3002 and (t := utc(a.get("CreatedOn"))):
            d = _data(a)
            out.append((t, d.get("PreviousStage") or "", d.get("CurrentStage") or "", d.get("CreatedBy") or ""))
    return sorted(out)


def owner_at(changes: list[tuple], t: datetime, owner_now: str) -> str:
    before = [c for c in changes if c[0] <= t]
    if before:
        return before[-1][2]
    return changes[0][1] if changes else owner_now


def bootcamp_family(name: str) -> tuple[str, str]:
    """("IP Law Career", "2026-09-26") from "IP Law Career Bootcamp - 26th Sep'26"; family in a fixed spelling."""
    family, day = parse_tag(name)
    return canon(family), day


ACRONYMS = frozenset({"US", "UK", "IP", "HR", "AI", "ID", "SQE", "SEBI", "CLAT", "LLM", "NCA", "M&A", "GST", "CA", "CS"})


def canon(name: str) -> str:
    """One spelling per name: "US corporate law" and "US Corporate Law" -> "US Corporate Law"."""
    words = re.sub(r"\s+", " ", name or "").strip().split(" ")
    small = {"and", "of", "for", "with", "in", "the", "to"}
    out = [w.upper() if w.upper() in ACRONYMS else w.lower() if i and w.lower() in small else w[:1].upper() + w[1:].lower()
           for i, w in enumerate(words) if w]
    return " ".join(out)


def is_bootcamp(text: str | None) -> bool:
    return bool(re.search(r"\bboot\s?camp\b", text or "", re.I))


def bootcamp_of(acts: list[dict], lead: dict, before: datetime | None) -> tuple[str, str]:
    """The latest bootcamp the lead attended or registered for before ``before`` (any time if None)."""
    opps = []
    for a in acts:
        if a.get("EventCode") == 12001 and (t := utc(a.get("CreatedOn"))) and (before is None or t <= before):
            name = _fields(a).get("mx_Custom_13") or ""
            if name:
                opps.append((t, name))
    if opps:
        return bootcamp_family(max(opps)[1])
    course = lead.get("mx_Enquired_Course") or ""
    return bootcamp_family(course) if is_bootcamp(course) else ("", "")


def community_name(leadtype: str) -> str:
    """"Criminal Community" -> "Criminal", "legal-ai-community" -> "Legal AI", "Community sqe" -> "SQE"."""
    words = [w for w in re.split(r"[\s_-]+", leadtype or "") if w and w.lower() != "community"]
    name = canon(" ".join(words))
    return {"ID": "Independent Director", "M&A": "M&A (Mergers & Acquisitions)"}.get(name, name) or leadtype


# GrowthX lead types that are communities without saying so: "contract drafting" funnels are its 21-day community.
COMMUNITY_TYPES = frozenset({"contract drafting"})


def is_community(leadtype: str | None) -> bool:
    lt = (leadtype or "").strip().lower()
    return "community" in lt or lt in COMMUNITY_TYPES


def phone10(p: str | None) -> str:
    digits = re.sub(r"\D", "", p or "")
    return digits[-10:] if len(digits) >= 10 else ""


def growthx_index(rows: list[dict]) -> dict[str, list[tuple[datetime, dict]]]:
    """GrowthX community checkouts and form fills by phone and email key."""
    from integrations.growthx.client import captured_ist
    idx: dict[str, list] = defaultdict(list)
    for r in rows:
        if not is_community(r.get("leadtype")):
            continue
        t = captured_ist(r.get("capturedAt"))
        if not t:
            continue
        for key in (phone10(r.get("whatsapp")), (r.get("email") or "").strip().lower()):
            if key:
                idx[key].append((t, r))
    return idx


def community_of(lead_id: str, acts: list[dict], before: datetime | None, contacts: dict, gx: dict) -> str:
    c = contacts.get(lead_id) or {}
    hits = []
    for key in {phone10(c.get("Phone")), phone10(c.get("Mobile")), (c.get("EmailAddress") or "").strip().lower()}:
        hits += [(t, r) for t, r in gx.get(key, []) if before is None or t <= before]
    paid = [h for h in hits if (h[1].get("status") or "") == "Paid"] or hits
    if paid:
        return community_name(max(paid, key=lambda h: h[0])[1].get("leadtype"))
    for a in acts:
        f = _fields(a)
        if a.get("EventCode") == 12000 and (t := utc(a.get("CreatedOn"))) and (before is None or t <= before) \
                and ("Community Enquiry" in (f.get("mx_Custom_2") or "")
                     or "Community Enrollment Opted" in (f.get("mx_Custom_20") or "")):
            return LS_COMMUNITY
    return ""


def week_starts(start: str, end: str) -> list[datetime]:
    """7-day blocks ending on ``end`` (IST); the first block may be shorter."""
    first, last = ist_day_start(start), ist_day_start(end) + timedelta(days=1)
    out, s = [], last - timedelta(days=7)
    while s > first:
        out.append(s)
        s -= timedelta(days=7)
    out.append(first)
    return sorted(out)


def week_of(t: datetime, starts: list[datetime]) -> datetime | None:
    found = None
    for s in starts:
        if t >= s:
            found = s
    return found


def week_label(s: datetime, starts: list[datetime], end: str) -> str:
    i = starts.index(s)
    stop = starts[i + 1] if i + 1 < len(starts) else ist_day_start(end) + timedelta(days=1)
    return f"{s:%d %b}–{stop - timedelta(days=1):%d %b}"


# ------------------------------------------------------------------ build

def load(data_dir: str) -> dict:
    j = lambda name, default=None: json.load(open(os.path.join(data_dir, name))) \
        if os.path.exists(os.path.join(data_dir, name)) else default  # noqa: E731
    hist = {}
    for line in open(os.path.join(data_dir, "hist.jsonl")):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("activities") is not None:
            hist[d["lead_id"]] = d["activities"]
    calls = []
    for f in sorted(glob.glob(os.path.join(data_dir, "calls", "*.json"))):
        calls += json.load(open(f))
    gx = []
    for f in sorted(glob.glob(os.path.join(data_dir, "growthx*.jsonl"))):
        gx += [json.loads(line) for line in open(f) if line.strip()]
    leads = {l["ProspectID"]: l for l in (j("extra_leads.json", []) + j("leads.json", []))}
    return {"team": j("team_users.json"), "leads": leads, "hist": hist, "calls": calls, "growthx": gx,
            "contacts": j("contacts.json", {}), "meta": j("meta.json", {})}


def build(src: dict, start: str, end: str) -> dict:
    team = src["team"]
    sub = {norm(n): m["team"] for n, m in team.items()}
    by_id = {m["id"]: n for n, m in team.items()}
    display = {norm(n): n for n in team}
    w0, w1 = ist_day_start(start), ist_day_start(end) + timedelta(days=1)
    starts = week_starts(start, end)
    in_team = lambda name: norm(name) in sub  # noqa: E731

    # ---- calls by team members
    calls_by_lead: dict[str, list] = defaultdict(list)
    caller_day: dict[tuple, Counter] = defaultdict(Counter)
    seen = set()
    for k in src["calls"]:
        t = utc(k.get("start_utc"))
        if not t or not (w0 <= t < w1) or k.get("activity_id") in seen:
            continue
        seen.add(k.get("activity_id"))
        name = by_id.get(k.get("user_id")) or display.get(norm(k.get("caller")))
        if not name:
            continue
        ans = k.get("status") == "Answered"
        dur = int(k.get("duration") or 0)
        rec = {"t": t, "by": name, "out": k.get("direction") == "outbound", "answered": ans, "dur": dur,
               "failed": k.get("status") == "CallFailure"}
        if k.get("lead_id"):
            calls_by_lead[k["lead_id"]].append(rec)
        c = caller_day[(name, ist_day(t))]
        if rec["out"]:
            c["dials"] += 1
            c["failed"] += rec["failed"]
            c["answered"] += ans
        c["real"] += ans and dur >= REAL_CONVERSATION_SECS
        c["talk_secs"] += dur if ans else 0
        c["inbound"] += not rec["out"]
        c["missed_in"] += (not rec["out"]) and not ans
    for v in calls_by_lead.values():
        v.sort(key=lambda r: r["t"])

    # ---- leads: arrivals, departures, enrolments, closures
    gx = growthx_index(src["growthx"])
    arrivals, departures, moves, enrols, closures = [], [], [], [], []
    no_history = 0
    lead_ids = set(src["leads"]) | set(src["hist"])
    for lid in lead_ids:
        lead = src["leads"].get(lid) or {"ProspectID": lid}
        acts = src["hist"].get(lid)
        if acts is None:
            no_history += 1
            continue
        created = utc(lead.get("CreatedOn")) or min((utc(a.get("CreatedOn")) for a in acts if utc(a.get("CreatedOn"))),
                                                   default=None)
        changes = owner_changes(acts)
        stages = stage_changes(acts)
        lead_calls = calls_by_lead.get(lid, [])
        got = []
        for t, prev, cur, by in changes:
            if not (w0 <= t < w1):
                continue
            if in_team(cur) and in_team(prev):
                moves.append({"lead_id": lid, "t": t, "from": display[norm(prev)], "to": display[norm(cur)]})
            elif in_team(cur):
                got.append((t, cur, origin(prev)))
            elif in_team(prev):
                held = [g for g in got if g[0] <= t]
                dialled = any(c["out"] and (not held or c["t"] >= held[-1][0] - EARLY) and c["t"] <= t for c in lead_calls)
                talked = any(c["answered"] and c["dur"] >= REAL_CONVERSATION_SECS and c["t"] <= t for c in lead_calls)
                departures.append({"lead_id": lid, "t": t, "from": display[norm(prev)], "team": sub[norm(prev)],
                                   "to": origin(cur), "dialled": dialled, "talked": talked,
                                   "held_days": round((t - held[-1][0]).total_seconds() / 86400, 1) if held else None})
        if not got and not any(in_team(c[2]) for c in changes) and in_team(lead.get("OwnerIdName")):
            t = utc(lead.get("mx_Assigned_On"))
            if t and w0 <= t < w1:
                got.append((t, lead["OwnerIdName"], "Unknown (no owner-change record)"))
        first_enrol = next((s[0] for s in stages if s[2] == ENROLLED), None)
        for t, cur, frm in got:
            leave = next((d[0] for d in changes if d[0] > t and in_team(d[1]) and not in_team(d[2])), None)
            mine = [c for c in lead_calls if c["t"] >= t - EARLY and (leave is None or c["t"] <= leave)]
            dials = [c for c in mine if c["out"]]
            first_dial = dials[0]["t"] if dials else None
            conn = next((c for c in mine if c["answered"]), None)
            real = next((c for c in mine if c["answered"] and c["dur"] >= REAL_CONVERSATION_SECS), None)
            enrolled = first_enrol is not None and first_enrol >= t - EARLY and (leave is None or first_enrol <= leave)
            boot, boot_day = bootcamp_of(acts, lead, t + timedelta(days=1))
            arrivals.append({
                "lead_id": lid, "t": t, "owner": display[norm(cur)], "team": sub[norm(cur)], "origin": frm,
                "new_lead": bool(created and t - created <= timedelta(days=NEW_LEAD_DAYS)),
                "first_dial_mins": (first_dial - t).total_seconds() / 60 if first_dial else None,
                "dials": len(dials), "dials_to_connect": (sum(1 for c in dials if c["t"] <= conn["t"]) if conn else None),
                "connected": conn is not None, "real": real is not None, "enrolled": enrolled,
                "left": leave is not None, "bootcamp": boot, "bootcamp_day": boot_day,
                "stage_now": lead.get("ProspectStage") or "",
                "owner_now_in_team": in_team(lead.get("OwnerIdName")),
            })
        for i, (t, prev_stage, cur_stage, by) in enumerate(stages):
            if not (w0 <= t < w1):
                continue
            owner = owner_at(changes, t, lead.get("OwnerIdName") or "")
            if cur_stage == ENROLLED:
                talks = [c for c in lead_calls if c["answered"] and c["dur"] >= REAL_CONVERSATION_SECS
                         and t - CLOSER_WINDOW <= c["t"] <= t]
                closer = talks[-1]["by"] if talks else ""
                credit = (display[norm(owner)] if in_team(owner) else display[norm(by)] if in_team(by) else closer)
                if not credit:
                    continue
                boot, boot_day = bootcamp_of(acts, lead, t)
                enrols.append({
                    "lead_id": lid, "t": t, "credited_to": credit, "team": sub[norm(credit)],
                    "owner_at_enrolment": owner, "set_by": by, "last_team_talk": closer,
                    "first_time": first_enrol == t, "bootcamp": boot, "bootcamp_day": boot_day,
                    "community": community_of(lid, acts, t, src["contacts"], gx),
                    "campaign": lead.get("mx_Campaign_Name") or "", "source": lead.get("Source") or "",
                    "fee": lead.get("mx_Course_Fees") or "",
                    "days_since_bootcamp": (t.astimezone(IST).date() - datetime.strptime(boot_day, "%Y-%m-%d").date()).days
                    if boot_day else None,
                })
            elif cur_stage in DEAD_STAGES and in_team(owner):
                talked = any(c["answered"] and c["dur"] >= REAL_CONVERSATION_SECS and c["t"] <= t for c in lead_calls)
                answered = any(c["answered"] and c["t"] <= t for c in lead_calls)
                closures.append({"lead_id": lid, "t": t, "owner": display[norm(owner)], "team": sub[norm(owner)],
                                 "stage": cur_stage, "real_conversation": talked, "any_answer": answered,
                                 "set_by": by})
    # Backlog: leads the team owns now, still open, never dialled by the team since they arrived
    last_arrival = {}
    for a in arrivals:
        if a["lead_id"] not in last_arrival or a["t"] > last_arrival[a["lead_id"]]["t"]:
            last_arrival[a["lead_id"]] = a
    backlog = [a for a in last_arrival.values() if a["owner_now_in_team"] and not a["left"] and a["dials"] == 0
               and a["stage_now"] not in DEAD_STAGES and a["stage_now"] != ENROLLED]
    return {"arrivals": arrivals, "departures": departures, "moves": moves, "enrols": enrols, "closures": closures,
            "caller_day": caller_day, "starts": starts, "backlog": backlog, "no_history": no_history,
            "leads": len(lead_ids), "team": team, "sub": sub}


def origin(owner: str) -> str:
    if not norm(owner):
        return "New (no owner before)"
    if norm(owner) in POOL:
        return f"Pool ({owner.strip()})"
    return "Another caller or team"


# ------------------------------------------------------------------ summarise

def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.0f}%" if b else "–"


def _rate(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


def caller_rows(b: dict, end: str) -> list[dict]:
    team, cd = b["team"], b["caller_day"]
    last7 = ist_day(ist_day_start(end) - timedelta(days=6))
    rows = []
    for name, m in sorted(team.items(), key=lambda kv: (kv[1]["team"], kv[0])):
        days = {d: c for (n, d), c in cd.items() if n == name}
        work = {d: c for d, c in days.items() if c["dials"] >= WORKING_DAY_DIALS}
        dialer = [d for d, c in work.items() if c["failed"] >= DIALER_FAILURE_SHARE * c["dials"]]
        tot = Counter()
        for c in days.values():
            tot.update(c)
        arr = [a for a in b["arrivals"] if a["owner"] == name]
        enr = [e for e in b["enrols"] if e["credited_to"] == name and e["first_time"]]
        active = sorted(d for d, c in days.items() if c["dials"])
        rows.append({
            "caller": name, "team": m["team"], "working_days": len(work), "dialer_issue_days": len(dialer),
            "dials": tot["dials"], "dials_per_working_day": round(sum(c["dials"] for c in work.values()) / len(work))
            if work else 0,
            "connect_%": _rate(tot["answered"], tot["dials"]),
            "real_conversations": tot["real"],
            "real_per_working_day": round(sum(c["real"] for c in work.values()) / len(work), 1) if work else 0,
            "talk_hours": round(tot["talk_secs"] / 3600, 1), "missed_inbound": tot["missed_in"],
            "leads_received": len(arr), "never_dialled_%": _rate(sum(1 for a in arr if not a["dials"]), len(arr)),
            "first_time_enrolments": len(enr),
            "first_active": active[0] if active else "", "last_active": active[-1] if active else "",
            "worked_last_7_days": sum(1 for d in work if d >= last7),
        })
    return rows


def weekly_rows(b: dict, end: str) -> list[dict]:
    starts = b["starts"]
    rows = []
    for s in starts:
        lab = week_label(s, starts, end)
        i = starts.index(s)
        stop = starts[i + 1] if i + 1 < len(starts) else ist_day_start(end) + timedelta(days=1)
        inw = lambda t: s <= t < stop  # noqa: E731
        days = {(n, d): c for (n, d), c in b["caller_day"].items() if s <= ist_day_start(d) < stop}
        work = {k: c for k, c in days.items() if c["dials"] >= WORKING_DAY_DIALS}
        tot = Counter()
        for c in days.values():
            tot.update(c)
        arr = [a for a in b["arrivals"] if inw(a["t"])]
        enr = [e for e in b["enrols"] if inw(e["t"])]
        first = [e for e in enr if e["first_time"]]
        clo = [c for c in b["closures"] if inw(c["t"])]
        rows.append({
            "week": lab, "days": (stop - s).days,
            "callers_working": len({n for n, _ in work}), "caller_days_worked": len(work),
            "dials": tot["dials"],
            "dials_per_caller_day": round(sum(c["dials"] for c in work.values()) / len(work)) if work else 0,
            "connect_%": _rate(tot["answered"], tot["dials"]), "real_conversations": tot["real"],
            "real_per_caller_day": round(sum(c["real"] for c in work.values()) / len(work), 1) if work else 0,
            "talk_hours": round(tot["talk_secs"] / 3600), "missed_inbound": tot["missed_in"],
            "leads_received": len(arr), "new_leads_received": sum(a["new_lead"] for a in arr),
            "churned_out": sum(1 for d in b["departures"] if inw(d["t"])),
            "closed_dead": len(clo), "closed_dead_without_talk": sum(1 for c in clo if not c["real_conversation"]),
            "first_time_enrolments": len(first), "repeat_enrolments": len(enr) - len(first),
            "enrolments_per_100_real": _rate(len(first), tot["real"]),
        })
    return rows


def team_rows(b: dict) -> list[dict]:
    rows = []
    for t in sorted(set(b["sub"].values())):
        arr = [a for a in b["arrivals"] if a["team"] == t]
        n = len(arr)
        dep = [d for d in b["departures"] if d["team"] == t]
        clo = [c for c in b["closures"] if c["team"] == t]
        enr = [e for e in b["enrols"] if e["team"] == t and e["first_time"]]
        rows.append({
            "team": t, "callers": sum(1 for m in b["team"].values() if m["team"] == t), "leads_received": n,
            "new_leads_%": _rate(sum(a["new_lead"] for a in arr), n),
            "dialled_within_24h_%": _rate(sum(1 for a in arr if a["first_dial_mins"] is not None
                                              and a["first_dial_mins"] <= 1440), n),
            "never_dialled_%": _rate(sum(1 for a in arr if not a["dials"]), n),
            "connected_%": _rate(sum(a["connected"] for a in arr), n),
            "real_conversation_%": _rate(sum(a["real"] for a in arr), n),
            "enrolled_from_received": sum(a["enrolled"] for a in arr),
            "first_time_enrolments": len(enr),
            "churned_out": len(dep), "churned_out_never_dialled": sum(1 for d in dep if not d["dialled"]),
            "closed_dead": len(clo), "closed_dead_without_talk_%": _rate(sum(1 for c in clo if not c["real_conversation"]),
                                                                       len(clo)),
        })
    return rows


def group_rows(enrols: list[dict], arrivals: list[dict], key: str, label: str) -> list[dict]:
    first = [e for e in enrols if e["first_time"]]
    got = Counter(a[key] or "None recorded" for a in arrivals) if key in ("bootcamp",) else Counter()
    enr = Counter(e[key] or "None recorded" for e in first)
    rep = Counter(e[key] or "None recorded" for e in enrols if not e["first_time"])
    rows = []
    for name in sorted(set(enr) | set(rep), key=lambda k: (-enr[k], -rep[k], k)):
        row = {label: name, "first_time_enrolments": enr[name], "repeat_enrolments": rep[name],
               "share_of_first_time_%": _rate(enr[name], len(first))}
        if got:
            row["leads_received"] = got[name]
            row["enrolments_per_100_leads_received"] = _rate(sum(1 for a in arrivals if (a[key] or "None recorded") == name
                                                                 and a["enrolled"]), got[name])
        rows.append(row)
    return rows


def edition_rows(enrols: list[dict], arrivals: list[dict]) -> list[dict]:
    first = [e for e in enrols if e["first_time"] and e["bootcamp"]]
    enr = Counter((e["bootcamp"], e["bootcamp_day"]) for e in first)
    got = Counter((a["bootcamp"], a["bootcamp_day"]) for a in arrivals if a["bootcamp"])
    won = Counter((a["bootcamp"], a["bootcamp_day"]) for a in arrivals if a["bootcamp"] and a["enrolled"])
    rows = []
    for k in sorted(set(enr) | set(got), key=lambda k: (-enr[k], -got[k])):
        if not enr[k] and got[k] < 50:
            continue
        rows.append({"bootcamp": k[0], "date": k[1], "first_time_enrolments": enr[k], "leads_received": got[k],
                     "enrolled_from_received": won[k], "per_100_received": _rate(won[k], got[k])})
    return rows


def origin_rows(b: dict) -> dict:
    return {
        "received_from": Counter(a["origin"] for a in b["arrivals"]).most_common(),
        "churned_to": Counter(d["to"] for d in b["departures"]).most_common(),
    }


def speed_rows(arrivals: list[dict]) -> list[dict]:
    c = Counter(speed_bucket(a["first_dial_mins"]) for a in arrivals)
    n = len(arrivals)
    return [{"first_dial_after_received": k, "leads": c[k], "share_%": _rate(c[k], n)} for k in BUCKETS]


def compare(weeks: list[dict], keys: list[str], n_prior: int = 4) -> list[dict]:
    """Last full week against the average of the ``n_prior`` weeks before it."""
    last, prior = weeks[-1], [w for w in weeks[-1 - n_prior:-1] if w["days"] == 7]
    rows = []
    for k in keys:
        vals = [w[k] for w in prior if w[k] is not None]
        avg = statistics.mean(vals) if vals else None
        rows.append({"measure": k, "last_week": last[k], "prior_avg": round(avg, 1) if avg is not None else None,
                     "change_%": round(100 * (last[k] - avg) / avg) if avg and last[k] is not None else None})
    return rows


def daily_enrolments(enrols: list[dict], end: str, days: int = 28) -> list[dict]:
    first = Counter(ist_day(e["t"]) for e in enrols if e["first_time"])
    rep = Counter(ist_day(e["t"]) for e in enrols if not e["first_time"])
    d0 = ist_day_start(end) - timedelta(days=days - 1)
    return [{"day": f"{d:%a %d %b}", "first_time": first[ist_day(d)], "repeat": rep[ist_day(d)]}
            for d in (d0 + timedelta(days=i) for i in range(days))]


def summarise(b: dict, start: str, end: str) -> dict:
    weeks = weekly_rows(b, end)
    arrivals, enrols = b["arrivals"], b["enrols"]
    return {
        "range": [start, end], "leads_analysed": b["leads"], "no_history": b["no_history"],
        "weeks": weeks, "teams": team_rows(b), "callers": caller_rows(b, end),
        "bootcamps": group_rows(enrols, arrivals, "bootcamp", "bootcamp"),
        "editions": edition_rows(enrols, arrivals),
        "communities": group_rows(enrols, arrivals, "community", "community"),
        "origins": origin_rows(b), "speed": speed_rows(arrivals),
        "within_team_moves": len(b["moves"]),
        "backlog": {"leads": len(b["backlog"]),
                    "by_team": Counter(a["team"] for a in b["backlog"]).most_common(),
                    "by_age": Counter("received in last 7 days" if a["t"] >= ist_day_start(end) - timedelta(days=6)
                                      else "older" for a in b["backlog"]).most_common()},
        "drop": compare(weeks, ["first_time_enrolments", "leads_received", "new_leads_received", "callers_working",
                                "caller_days_worked", "dials", "dials_per_caller_day", "connect_%",
                                "real_conversations", "real_per_caller_day", "enrolments_per_100_real",
                                "churned_out", "closed_dead_without_talk"]),
        "daily": daily_enrolments(enrols, end),
        "days_since_bootcamp": Counter(
            "no bootcamp" if e["days_since_bootcamp"] is None else "0–7 days" if e["days_since_bootcamp"] <= 7
            else "8–14 days" if e["days_since_bootcamp"] <= 14 else "15–30 days" if e["days_since_bootcamp"] <= 30
            else "31+ days" for e in enrols if e["first_time"]).most_common(),
    }


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(data_dir: str, start: str, end: str, out_dir: str) -> None:
    src = load(data_dir)
    b = build(src, start, end)
    s = summarise(b, start, end)
    s["fetched_at_utc"] = (src.get("meta") or {}).get("fetched_at_utc")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(s, open(os.path.join(out_dir, "summary.json"), "w"), indent=1, default=str)
    for name in ("weeks", "teams", "callers", "bootcamps", "editions", "communities", "speed", "drop", "daily"):
        write_csv(os.path.join(out_dir, f"{name}.csv"), s[name])
    enrol_rows = [{**e, "t": e["t"].astimezone(IST).strftime("%Y-%m-%d %H:%M")} for e in b["enrols"]]
    write_csv(os.path.join(out_dir, "enrolments.csv"), sorted(enrol_rows, key=lambda r: r["t"]))
    print(f"{b['leads']} leads ({b['no_history']} without history), {len(b['arrivals'])} received, "
          f"{len(b['departures'])} churned out, {sum(e['first_time'] for e in b['enrols'])} first-time enrolments "
          f"-> {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:5])
