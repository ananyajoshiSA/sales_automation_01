"""Morning call list: one sheet per caller with every open November bootcamp collection lead in the agreed order, a team
summary and a 48-hour tracker for the team leaders, and the plan saved for the evening update.

Order of work (agreed 17 Nov 2026): 1 follow-ups due today and anything carried over (a follow-up not kept, the lead's
own call not returned), 2 the 48-hour clock (no substantial conversation yet; due today or later, or missed yesterday),
3 Hot, 4 To be counselled, 5 Call-back requested, 6 Connected – payment not discussed, 7 Not connected (48 hours missed
before yesterday), 8 Cold. Leads that asked us to wait until a later date are listed last and not called before it. Each lead appears once, in its first group.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

from analytics.collection_cycle import silent_since
from analytics.daily_plan.workbook import F, hdr, put, title
from integrations.timeutil import IST

STATUS = {"notconnected": "Not connected", "callback": "Call-back requested", "connected": "Connected – payment not discussed",
          "hot": "Hot", "counsel": "To be counselled", "cold": "Cold"}
NO_TALK, TALK = {"notconnected", "callback", "cold"}, {"connected", "hot", "counsel", "cold"}
GROUPS = {1: "Follow-up due today / carried over", 2: "48-hour clock: no substantial conversation yet", 3: "Hot", 4: "To be counselled",
          5: "Call-back requested", 6: "Connected – payment not discussed", 7: "Not connected – 48 h missed earlier", 8: "Cold",
          9: "Waiting – do not call before the date"}
FILL = {1: "FBE0DE", 2: "FFF0C2", 3: "DCEFE6", 4: "F3EAF7", 5: "E8F0FA", 6: "F2F2F2", 7: "FDF3E7", 8: "EDEDED", 9: "FFFFFF"}
BY_STATUS = {"hot": 3, "counsel": 4, "callback": 5, "connected": 6, "notconnected": 7, "cold": 8}
OUTCOMES = ["Payment done", "Payment date fixed", "Loan documents shared", "Objection – counselled", "Call-back requested",
            "Spoke – payment not discussed", "Not answered", "Switched off / failed", "Refused / wants refund", "Wrong number"]


def hm(t: datetime | None, fmt: str = "%a %d %b %H:%M") -> str:
    return t.astimezone(IST).strftime(fmt) if t else "—"


def agreed_at(s: str | None) -> datetime | None:
    """A follow-up time a grader read from a transcript or Note, written "YYYY-MM-DD HH:MM" in IST."""
    try:
        return datetime.strptime((s or "")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    except ValueError:
        return None


def check(lead: dict, j: dict) -> None:
    st = j.get("status")
    if st not in STATUS:
        raise ValueError(f"{lead['phone']}: status {st!r} is not one of {sorted(STATUS)}")
    if st not in (NO_TALK if lead["real_talks"] == 0 else TALK):
        raise ValueError(f"{lead['phone']}: judged {st} with {lead['real_talks']} real talks")


def place(lead: dict, j: dict, day: date) -> dict:
    """The lead's group, due time and why it is there, for the morning of ``day``."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST)
    end = start + timedelta(days=1)
    waiting = (j.get("waiting_until") or "") > day.isoformat()
    agreed = agreed_at(j.get("followup_at"))
    times = [t for t in (lead["next_follow_up"], agreed) if t and start <= t < end]
    missed = [f for f in lead["follow_ups"] if f["kept"] is False and (not lead["last_real"] or f["due"] > lead["last_real"])]
    why = []
    if times:
        why.append(f"Follow-up at {min(times).astimezone(IST):%H:%M}")
    if missed:
        why.append(f"Follow-up of {hm(missed[-1]['due'])} not kept")
    if lead["unreturned_calls"]:
        why.append(f"Lead called {hm(lead['unreturned_calls'][-1])}, not returned")
    if waiting and not lead["unreturned_calls"]:
        return {"group": 9, "due": None, "why": f"Asked to wait until {date.fromisoformat(j['waiting_until'][:10]):%a %d %b}"}
    if why:
        return {"group": 1, "due": min(times) if times else None, "why": "; ".join(why)}
    if lead["due48"] and lead["deadline"] >= start - timedelta(days=1):  # the race: due today or later, or missed yesterday
        late = start > lead["deadline"]
        return {"group": 2, "due": lead["deadline"], "why": ("48 h missed " if late else "Needs a real talk by ") + hm(lead["deadline"])}
    if lead["due48"] and j["status"] != "cold":
        return {"group": BY_STATUS[j["status"]], "due": None, "why": f"No real talk yet; 48 h missed {hm(lead['deadline'], '%d %b')}"}
    return {"group": BY_STATUS[j["status"]], "due": None, "why": ""}


def plan(leads: list[dict], judged: dict, day: date, cut: datetime | None = None) -> list[tuple[dict, dict, dict]]:
    rows = []
    for l in leads:
        if l["enrolled"] or l["dropped"]:
            continue
        j = judged.get(l["lead_id"])
        if not j:
            raise ValueError(f"no grade for open lead {l['phone']} ({l['caller']})")
        check(l, j)
        if cut and j["status"] != "cold" and l.get("calls") and (quiet := silent_since(l, cut)) and not l["unreturned_calls"]:
            # the user's rule (10 Nov): no pick-up for a week despite 2+ dials a day makes a lead cold, whatever was said before
            j = {**j, "status": "cold", "silent": f"Was {STATUS[j['status']]}; no pick-up since {quiet.astimezone(IST):%d %b} despite 2+ dials a day"}
        p = place(l, j, day)
        if j.get("silent") and p["group"] == 8:
            p["why"] = j["silent"]
        rows.append((l, j, p))
    far = datetime.max.replace(tzinfo=IST)
    rows.sort(key=lambda r: (r[0]["caller"], r[2]["group"], r[2]["due"] or far, r[0]["last_real"] or far))
    return rows


COLS = [("#", 4), ("Group", 16), ("Due / when", 13), ("Number", 15), ("Lead", 16), ("Bootcamp · day", 18), ("Status", 14),
        ("Why on the list", 22), ("48-hour deadline", 13), ("Last real talk", 12), ("What was discussed", 34),
        ("Payment mode · timeline", 20), ("Objection", 20), ("Dials / days", 7), ("Attempt 1 time", 9), ("Attempt 1 outcome", 15),
        ("Attempt 2 time", 9), ("Attempt 2 outcome", 15), ("Attempt 3 time", 9), ("Attempt 3 outcome", 15),
        ("Next follow-up (date, time)", 14), ("Outcome", 18), ("Notes", 24), ("Lead ID", 10)]
FILL_FROM = COLS.index(("Attempt 1 time", 9)) + 1


def caller_sheet(wb: Workbook, caller: str, rows: list, day: date, cut: datetime) -> None:
    ws = wb.create_sheet(caller[:31])
    l0 = rows[0][0]
    title(ws, 1, f"{caller} — call list for {day:%a %d %b %Y}", 13)
    g = Counter(p["group"] for _, _, p in rows)
    put(ws, 2, 1, f"TL {l0['tl']} · {len(rows)} open leads · " + " · ".join(f"{GROUPS[k].split(':')[0]} {g[k]}" for k in sorted(g)), wrap=False)
    put(ws, 3, 1, "Work top to bottom. After every attempt fill its time and outcome; after a talk set the next follow-up date and time. "
                  f"LeadSquared read {cut.astimezone(IST):%d %b %H:%M} IST.", italic=True, wrap=False)
    hdr(ws, 5, [c for c, _ in COLS], [w for _, w in COLS])
    dv = DataValidation(type="list", formula1='"' + ",".join(OUTCOMES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    for i, (l, j, p) in enumerate(rows, 1):
        r = 5 + i
        vals = [i, GROUPS[p["group"]], hm(p["due"], "%d %b %H:%M") if p["due"] else "", l["phone"], l["name"], f"{l['bootcamp']} · D{l['day']}",
                STATUS[j["status"]], p["why"], hm(l["deadline"]) if l["due48"] else "met" if l["met48"] else "—",
                hm(l["last_real"], "%d %b"), j.get("last_talk") or "", j.get("payment") or "", j.get("objection") or "",
                f"{l['dials']} / {l['dial_days']}"] + [""] * 9 + [l["lead_id"]]
        for c, v in enumerate(vals, 1):
            put(ws, r, c, v, fill=FILL[p["group"]] if c <= 3 else ("FFF4CC" if FILL_FROM <= c < len(COLS) else None))
        for c in (FILL_FROM + 1, FILL_FROM + 3, FILL_FROM + 5, FILL_FROM + 7):
            dv.add(ws.cell(row=r, column=c))
    ws.freeze_panes = "F6"


def team_sheet(wb: Workbook, rows: list, leads: list[dict], day: date, cut: datetime, team: str) -> None:
    ws = wb.active
    ws.title = "Team summary"
    title(ws, 1, f"Bootcamp collections — {team} — team summary for {day:%a %d %b %Y}", 13)
    put(ws, 2, 1, f"November bootcamp leads. LeadSquared read {cut.astimezone(IST):%d %b %H:%M} IST. Statuses read from each lead's "
                  "calls, transcripts, comments and Notes.", italic=True, wrap=False)
    cols = ["AD", "TL", "Caller", "Open leads"] + [GROUPS[k].split(":")[0] for k in GROUPS]
    hdr(ws, 4, cols, [16, 18, 22, 8] + [11] * len(GROUPS))
    by = {}
    for l, j, p in rows:
        by.setdefault((l["ad"], l["tl"], l["caller"]), Counter())[p["group"]] += 1
    r = 5
    for (ad, tl, caller), g in sorted(by.items()):
        for c, v in enumerate([ad, tl, caller, sum(g.values())] + [g.get(k, 0) for k in GROUPS], 1):
            put(ws, r, c, v)
        r += 1
    tot = Counter(p["group"] for _, _, p in rows)
    for c, v in enumerate(["Total", "", "", len(rows)] + [tot.get(k, 0) for k in GROUPS], 1):
        put(ws, r, c, v, bold=True)
    r += 2
    title(ws, r, "48-hour rule: one substantial conversation (2 min+) by 20:00 IST two days after allocation")
    r += 1
    hdr(ws, r, ["AD", "TL", "Caller", "Leads allocated", "Deadline passed", "Talked in 48 h", "%", "Picked up in 48 h", "Still due (open)"])
    r += 1
    by48 = {}
    for l in leads:
        k = (l["ad"], l["tl"], l["caller"])
        a = by48.setdefault(k, Counter())
        a["allocated"] += 1
        if cut > l["deadline"]:
            a["passed"] += 1
            a["met"] += l["met48"]
            a["picked"] += bool(l["first_pickup"] and l["first_pickup"] <= l["deadline"])
        a["due"] += bool(l["due48"] and not l["enrolled"] and not l["dropped"])
    for k, a in sorted(by48.items()):
        pc = round(100 * a["met"] / a["passed"]) if a["passed"] else ""
        for c, v in enumerate([*k, a["allocated"], a["passed"], a["met"], pc, a["picked"], a["due"]], 1):
            put(ws, r, c, v, fill="F8D7D3" if pc != "" and pc < 90 and c == 7 else None)
        r += 1


def how_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("How to use")
    lines = ["How to use this workbook",
             "Each caller works their own sheet top to bottom. Team leaders use Team summary for who is stuck.",
             "Groups: " + "; ".join(f"{k} {v}" for k, v in GROUPS.items()) + ".",
             "Statuses: Hot = payment mode and timeline discussed, or loan documents shared or promised. To be counselled = the lead raised an "
             "objection (the caller counsels; it wins over Hot until resolved). Call-back requested = a short pick-up where the lead asked to be "
             "called back. Connected – payment not discussed = a real talk without payment talk yet. Cold = refused, wants a refund, or silent "
             "a week despite calls. Not connected = no real talk yet.",
             "48-hour rule: a lead allocated on day D needs one substantial conversation (answered, 2 min or more) by 20:00 IST on D+2. If a "
             "lead moves to another caller, the clock restarts.",
             "Follow-ups: call-back times saved in LeadSquared after a call, and times agreed on calls or in Notes.",
             "After every attempt fill its time and outcome; after a talk set the next follow-up date and time in LeadSquared as well.",
             "The evening update compares the day with this list: what was done, what was missed, and the next steps."]
    for i, t in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=t)
        c.font = Font(name=F, bold=i == 1, size=12 if i == 1 else 10)
    ws.column_dimensions["A"].width = 150


TEAMS = {"Mayur": "Mayur's team", "Puja / Anmol": "Anmol's team"}


def build(leads: list[dict], judged: dict, day: date, cut: datetime, out_dir: str) -> tuple[list[str], str]:
    """One workbook per collection team (Mayur's, Anmol's) and the plan of both for the evening update."""
    rows = plan(leads, judged, day, cut)
    os.makedirs(out_dir, exist_ok=True)
    books = []
    for side, team in TEAMS.items():
        mine = [r for r in rows if r[0]["side"] == side]
        if not mine:
            continue
        wb = Workbook()
        team_sheet(wb, mine, [l for l in leads if l["side"] == side], day, cut, team)
        how_sheet(wb)
        for caller in sorted({l["caller"] for l, _, _ in mine}, key=lambda c: (next(l["tl"] for l, _, _ in mine if l["caller"] == c), c)):
            caller_sheet(wb, caller, [r for r in mine if r[0]["caller"] == caller], day, cut)
        books.append(os.path.join(out_dir, f"Bootcamp collections call list - {team} - {day:%a %d %b %Y}.xlsx"))
        wb.save(books[-1])
    saved = os.path.join(out_dir, f"plan_{day.isoformat()}.json")
    json.dump({"day": day.isoformat(), "cut": cut.isoformat(),
               "leads": {l["lead_id"]: {"caller": l["caller"], "side": l["side"], "group": p["group"], "status": j["status"],
                                        "why": p["why"], "due": p["due"].isoformat() if p["due"] else None} for l, j, p in rows}},
              open(saved, "w"), indent=1)
    return books, saved
