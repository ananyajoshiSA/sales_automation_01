"""One weekend's bootcamp collection cycle, lead by lead, as of a cut-off: the facts the Wednesday and Friday reports use.

A cycle lead is a lead tagged for one of the cycle's bootcamps and held by one of the team's callers (its owner now, or
the last team caller who owned it). It is assigned at its first owner change to a team member after the bootcamp
started (or, without one, when its stage became "Booking fees received"), and must be dialled within 12 working
hours (10:00–20:00 IST). Enrolled = the stage reached Course Enrolled or Collections done.

    python -m analytics.collection_cycle data/nov_w1 2026-10-24 Mayur "2026-10-28 20:00" out.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from analytics.bootcamp_collections import BOT, COLLECTED, LOST, DEFERRED, _call, _data, _fields, _note_text, course_family, parse_tag
from analytics.collection_scorecard import hierarchy, load
from integrations.timeutil import IST, utc

WORK_START, WORK_END = 10, 20
WINDOW_HOURS = 12
REAL_SECS = 120
BOOKED = "booking fees received"


def add_working_hours(t: datetime, hours: float) -> datetime:
    """``t`` plus ``hours`` counted only between 10:00 and 20:00 IST (every day)."""
    t = t.astimezone(IST)
    while hours > 1e-9:
        if t.hour >= WORK_END:
            t = (t + timedelta(days=1)).replace(hour=WORK_START, minute=0, second=0, microsecond=0)
            continue
        if t.hour < WORK_START:
            t = t.replace(hour=WORK_START, minute=0, second=0, microsecond=0)
        left = (t.replace(hour=WORK_END, minute=0, second=0, microsecond=0) - t).total_seconds() / 3600
        step = min(left, hours)
        t += timedelta(hours=step)
        hours -= step
    return t


def working_hours_between(a: datetime, b: datetime) -> float:
    """Working hours (10:00–20:00 IST) from ``a`` to ``b``; 0 if ``b`` is not after ``a``."""
    a, b = a.astimezone(IST), b.astimezone(IST)
    if b <= a:
        return 0.0
    total, day = 0.0, a.date()
    while day <= b.date():
        lo = datetime(day.year, day.month, day.day, WORK_START, tzinfo=IST)
        hi = datetime(day.year, day.month, day.day, WORK_END, tzinfo=IST)
        s, e = max(lo, a), min(hi, b)
        if e > s:
            total += (e - s).total_seconds() / 3600
        day += timedelta(days=1)
    return round(total, 2)


def short_bootcamp(tag: str) -> str:
    course, day = parse_tag(tag)
    d = datetime.strptime(day, "%Y-%m-%d").strftime("%-d %b") if day else ""
    return f"{course_family(course)} {d}".strip()


def phone_of(lead: dict) -> str:
    return (lead.get("Mobile") or lead.get("Phone") or "").strip()


def team_member(team: dict, name: str, side: str) -> bool:
    return team.get(name, ("",))[0] == side


def cycle_leads(leads: list[dict], hist: dict[str, list], meta: dict, bootcamp_day: str, side: str) -> list[dict]:
    """The team's leads for the cycle, with assignment, first dial and the events needed for any cut-off."""
    team = hierarchy(meta.get("member_groups") or {})
    start = datetime.strptime(bootcamp_day, "%Y-%m-%d").replace(tzinfo=IST)
    out = []
    for l in leads:
        if parse_tag(l.get("mx_Bootcamp_collections"))[1] != bootcamp_day:
            continue
        acts = sorted((a for a in hist.get(l["ProspectID"]) or [] if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
        owners = [(utc(a["CreatedOn"]), (_data(a).get("CurrentOwner") or "").strip()) for a in acts
                  if a.get("EventCode") == 3001 and utc(a["CreatedOn"]) >= start]
        current = (l.get("OwnerIdName") or "").strip()
        held = [o for _, o in owners if team_member(team, o, side) and team[o][2] not in ("(AD)",)]
        caller = current if team_member(team, current, side) else (held[-1] if held else None)
        if not caller:
            continue
        stages = [(utc(a["CreatedOn"]), (_data(a).get("CurrentStage") or "").strip(), _data(a).get("Comment") or "") for a in acts
                  if a.get("EventCode") == 3002 and utc(a["CreatedOn"]) >= start]
        to_team = [t for t, o in owners if team_member(team, o, side)]
        booked = [t for t, s, _ in stages if s.lower() == BOOKED]
        assigned = to_team[0] if to_team else (booked[0] if booked else (stages[0][0] if stages else None))
        if not assigned:
            continue
        calls = [c for c in (_call(a) | {"id": a.get("Id")} for a in acts if a.get("EventCode") in (21, 22))
                 if c["t"] and c["t"] >= start and not BOT.search(c["by"])]
        enrolled = next((t for t, s, _ in stages if s in COLLECTED and t >= start), None)
        lost = next((t for t, s, _ in stages if s in LOST | DEFERRED), None)
        forms = [{"t": utc(a["CreatedOn"]), "status": (_fields(a).get("Status") or "").replace("\xa0", " "),
                  "due": utc(_fields(a).get("mx_Custom_1")), "note": _note_text(a)} for a in acts
                 if a.get("EventCode") == 103 and utc(a["CreatedOn"]) >= start]
        tl, ad = team[caller][2], team[caller][1]
        out.append({"lead_id": l["ProspectID"], "name": f"{l.get('FirstName') or ''} {l.get('LastName') or ''}".strip(),
                    "phone": phone_of(l), "tag": l.get("mx_Bootcamp_collections"), "bootcamp": short_bootcamp(l.get("mx_Bootcamp_collections")),
                    "caller": caller, "current_owner": current, "tl": tl, "ad": ad, "assigned": assigned,
                    "day": 1 if assigned.astimezone(IST).date() <= (start + timedelta(days=2)).date() else 2,
                    "due": add_working_hours(assigned, WINDOW_HOURS), "calls": calls, "stages": stages, "forms": forms,
                    "enrolled_at": enrolled, "lost_at": lost, "stage_now": l.get("ProspectStage") or ""})
    return out


def state(lead: dict, cut: datetime) -> dict:
    """What was true of the lead at ``cut``: calls, talks, first-dial timing, enrolment or drop."""
    calls = [c for c in lead["calls"] if c["t"] <= cut]
    dials = [c for c in calls if c["out"] and c["t"] >= lead["assigned"]]
    real = [c for c in calls if c["answered"] and c["dur"] >= REAL_SECS]
    first = dials[0]["t"] if dials else None
    delay = working_hours_between(lead["assigned"], first) if first else None
    enrolled = bool(lead["enrolled_at"] and lead["enrolled_at"] <= cut)
    lost = bool(not enrolled and lead["lost_at"] and lead["lost_at"] <= cut)
    unanswered_since = sum(1 for c in dials if not c["answered"] and (not real or c["t"] > real[-1]["t"]))
    missed_in = [c for c in calls if not c["out"] and not c["answered"]]
    return {"first_dial": first, "delay_wh": delay, "on_time": bool(first and delay is not None and delay <= WINDOW_HOURS),
            "overdue_wh": working_hours_between(lead["due"], cut) if not first and cut > lead["due"] else 0.0,
            "dials": len(dials), "dial_days": len({c["t"].astimezone(IST).date() for c in dials}), "real_talks": len(real),
            "last_real": real[-1]["t"] if real else None, "unanswered_since_talk": unanswered_since,
            "missed_inbound": len(missed_in), "enrolled": enrolled, "dropped": lost,
            "enrolled_at": lead["enrolled_at"] if enrolled else None, "stage": next((s for t, s, _ in reversed(lead["stages"]) if t <= cut), "")}


def main(data_dir: str, bootcamp_day: str, side: str, cut: str, out_path: str) -> None:
    leads, hist, meta = load(data_dir)
    cutoff = datetime.strptime(cut, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    rows = [{**{k: v for k, v in l.items() if k not in ("calls", "stages", "forms")}, **state(l, cutoff)}
            for l in cycle_leads(leads, hist, meta, bootcamp_day, side)]
    json.dump(rows, open(out_path, "w"), indent=1, default=str)
    print(f"{len(rows)} leads")


if __name__ == "__main__":
    main(*sys.argv[1:6])
