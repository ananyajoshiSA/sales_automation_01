"""Community collection leads of chosen booking months, lead by lead: the facts the community check uses.

A community collection lead is a row of the Community Webinar Collection Sheet (one per booking-fee payment) matched
to its LeadSquared lead (``analytics.collections.match``). Unlike a bootcamp weekend, community leads book all week
long, so each lead carries its own booking date (the sheet's date) and its batch (community + group number, taken from
the sheet's "Course paid for", e.g. "Independent Director G88–89"). It is credited to the last collection-team caller
who held it in LeadSquared after booking (its owner if still in a team); a lead LeadSquared does not place with a team
falls back to the sheet's owner, and is flagged. Assignment, the 12-working-hour first call, enrolment and drop follow
``analytics.collection_cycle``. Money is the sheet's own: price, amount paid and balance per lead.

    python -m analytics.community_cycle <collections data dir> <groups meta.json> 2026-10,2026-11 Mayur out.json
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime

from analytics.bootcamp_collections import BOT, LOST, DEFERRED, _call, _data, _fields, _note_text
from analytics.collection_cycle import BOOKED, WINDOW_HOURS, add_working_hours, is_enrolment, state, team_member
from analytics.collection_scorecard import hierarchy
from analytics.collections.match import COMMUNITY_FAMILIES, LeadIndex, lead_name, match_students
from analytics.collections.sheets import read_community
from integrations.timeutil import IST, now_utc, utc

SHEET_ENROLLED = {"Collected", "Instalments"}
GROUP = re.compile(r"group[-\s]*([\d][\d\s,&and-]*)", re.I)
START = re.compile(r"\((\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)", re.I)


def family(course: str) -> str:
    c = course.lower()
    return next((name for key, name in COMMUNITY_FAMILIES if key in c), "") or re.sub(r"^community:\s*", "", course, flags=re.I).split(" - ")[0].strip()


def batch_of(course: str) -> tuple[str, str]:
    """("Independent Director G88–89", "18 Oct") from the sheet's "Course paid for"; Week_1/2/3 of a group is one batch."""
    fam = family(course or "") or "Community"
    if "diwali" in (course or "").lower():
        grp = " Diwali Special"
    else:
        m = GROUP.search(course or "")
        nums = sorted({int(n) for n in re.findall(r"\d+", m.group(1))}) if m else []
        grp = f" G{nums[0]}" + (f"–{nums[-1]}" if len(nums) > 1 else "") if nums else ""
    s = START.search(course or "")
    started = ""
    if s:
        try:
            started = datetime.strptime(f"{s.group(1)} {s.group(2)[:3].title()} 2000", "%d %b %Y").strftime("%-d %b")
        except ValueError:
            pass
    return f"{fam}{grp}", started


def load_hist(path: str) -> dict[str, list]:
    out = {}
    for line in open(path):
        d = json.loads(line)
        if d.get("activities") is not None and not d.get("error"):
            out[d["lead_id"]] = d["activities"]
    return out


def sheet_rows(data_dir: str, months: tuple[str, ...]) -> tuple[list[dict], dict]:
    """The sheet's rows for ``months`` (booking month, "2026-10"), each with its LeadSquared lead, plus the leads index."""
    from analytics.collections.__main__ import load_tagged
    rows, _ = read_community(os.path.join(data_dir, "community.xlsx"))
    rows = [r for r in rows if r["month"] in months]
    index = LeadIndex(load_tagged(data_dir))
    match_students(rows, index, "community")
    return rows, index.leads


def community_leads(rows: list[dict], leads: dict[str, dict], hist: dict[str, list], member_groups: dict, side: str,
                    months: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    """(the side's leads, sheet rows of this side that could not be built: not in LeadSquared or no history)."""
    team = hierarchy(member_groups)
    out, left = [], []
    seen = set()
    for r in rows:
        sheet_side = {"Team Mayur": "Mayur", "Team Anmol": "Puja / Anmol"}.get(r["vertical"], "")
        lid = r.get("lsq_id")
        if not lid or lid not in hist:
            if sheet_side == side:
                left.append({**r, "why_left": r.get("match_note") or "no LeadSquared history"})
            continue
        if lid in seen:  # the same lead booked twice in these months: keep the first row
            continue
        l = leads.get(lid) or {}
        booked_on = date.fromisoformat(r["date"][:10])
        start = datetime(booked_on.year, booked_on.month, booked_on.day, tzinfo=IST)
        acts = sorted((a for a in hist[lid] if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
        owners = [(utc(a["CreatedOn"]), (_data(a).get("CurrentOwner") or "").strip()) for a in acts
                  if a.get("EventCode") == 3001 and utc(a["CreatedOn"]) >= start]
        current = (l.get("OwnerIdName") or "").strip()
        # the last collection caller of either team who held it after booking decides the team, so no lead counts twice
        held = [o for _, o in owners if o in team and team[o][2] != "(AD)"]
        last = current if current in team and team[current][2] != "(AD)" else (held[-1] if held else None)
        caller, credited_by = None, "lsq"
        if last:
            caller = last if team[last][0] == side else None
        elif current not in team and not any(o in team for _, o in owners) and r["owner"] in team and team[r["owner"]][0] == side:
            # only when no collection caller of either team held it in LeadSquared after booking
            caller, credited_by = r["owner"], "sheet"
        if not caller:
            continue
        seen.add(lid)
        stages = [(utc(a["CreatedOn"]), (_data(a).get("CurrentStage") or "").strip(), _data(a).get("Comment") or "") for a in acts
                  if a.get("EventCode") == 3002 and utc(a["CreatedOn"]) >= start]
        to_team = [t for t, o in owners if team_member(team, o, side)]
        booked = [t for t, s, _ in stages if s.lower() == BOOKED]
        assigned = to_team[0] if to_team else (booked[0] if booked else start.replace(hour=10))
        calls = [c | {"team": team_member(team, c["by"], side)} for c in (_call(a) | {"id": a.get("Id")} for a in acts
                 if a.get("EventCode") in (21, 22)) if c["t"] and c["t"] >= start and not BOT.search(c["by"])]
        forms = [{"t": utc(a["CreatedOn"]), "status": (_fields(a).get("Status") or "").replace("\xa0", " "),
                  "due": utc(_fields(a).get("mx_Custom_1")), "note": _note_text(a)} for a in acts
                 if a.get("EventCode") == 103 and utc(a["CreatedOn"]) >= start]
        batch, started = batch_of(r["course"])
        tl, ad = team[caller][2], team[caller][1]
        out.append({"lead_id": lid, "name": r["name"] or lead_name(l), "phone": r["phone"] and f"+91-{r['phone']}" or (l.get("Mobile") or l.get("Phone") or ""),
                    "bootcamp": batch, "batch_start": started, "booked_on": booked_on, "month": r["month"],
                    "label": f"{batch} · booked {booked_on:%-d %b}", "day": 1,
                    "caller": caller, "credited_by": credited_by, "sheet_owner": r["owner"], "current_owner": current, "tl": tl, "ad": ad,
                    "assigned": assigned, "due": add_working_hours(assigned, WINDOW_HOURS), "calls": calls, "stages": stages, "forms": forms,
                    "enrolled_at": next((t for t, s, c in stages if is_enrolment(s, c)), None),
                    "lost_at": next((t for t, s, _ in stages if s in LOST | DEFERRED), None), "stage_now": l.get("ProspectStage") or "",
                    "price": r["price"] or 0, "received": r["received"] or 0, "balance": r["balance"] or 0,
                    "sheet_outcome": r["outcome"], "sheet_stage": r["sheet_stage"], "sheet_status": r["status"]})
    return out, left


def main(data_dir: str, groups_meta: str, months: str, side: str, out_path: str) -> None:
    ms = tuple(months.split(","))
    rows, leads = sheet_rows(data_dir, ms)
    hist = load_hist(os.path.join(data_dir, "hist.jsonl"))
    member_groups = json.load(open(groups_meta))["member_groups"]
    got, left = community_leads(rows, leads, hist, member_groups, side, ms)
    cut = now_utc()
    json.dump([{**{k: v for k, v in l.items() if k not in ("calls", "stages", "forms")}, **state(l, cut)} for l in got],
              open(out_path, "w"), indent=1, default=str)
    print(f"{len(got)} leads, {len(left)} not built")


if __name__ == "__main__":
    main(*sys.argv[1:6])
