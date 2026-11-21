"""Morning call list: one sheet per caller with every open November bootcamp collection lead in the agreed order, a team
summary and a 48-hour tracker for the team leaders, and the plan saved for the evening update.

Order of work (agreed 17 Nov 2026): 1 follow-ups due today and anything carried over (a follow-up not kept, the lead's
missed call not called back), 2 the 48-hour clock (no substantial conversation yet; due today or later, or missed yesterday),
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
    dialled_since = lambda t: lead.get("last_dial") and lead["last_dial"] >= t - timedelta(minutes=15)
    times = [t for t in (lead["next_follow_up"], agreed) if t and start <= t < end and not dialled_since(t)]  # a refresh drops kept ones
    missed = [f for f in lead["follow_ups"] if f["kept"] is False and (not lead["last_real"] or f["due"] > lead["last_real"])]
    why = []
    if times:
        why.append(f"Follow-up at {min(times).astimezone(IST):%H:%M}")
    if missed:
        why.append(f"Follow-up of {hm(missed[-1]['due'])} not kept")
    if lead["unreturned_calls"]:
        why.append(f"Missed call from lead {hm(lead['unreturned_calls'][-1])}, not called back")
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


COLS = [("#", 4), ("Priority group", 16), ("Due / when", 13), ("Number", 15), ("Lead", 16), ("Lead details", 60),
        ("IIT Pitch", 30), ("Calls so far (calls / days)", 10)]  # nothing for callers to fill: LeadSquared records every attempt (the user, 18 Nov)
# short names for the team summary's columns, one per group
GROUP_COLS = {1: "Follow-ups due today or missed", 2: "New leads: first real talk due", 3: "Hot: payment plan agreed",
              4: "Objection: needs counselling", 5: "Asked to call back", 6: "Talked, payment not discussed yet",
              7: "Never reached (48 hours missed earlier)", 8: "Cold", 9: "Waiting until an agreed date"}


def details(l: dict, j: dict, p: dict) -> str:
    """Bootcamp, status, why on the list, the 48-hour deadline, the last real talk, payment and objection in one cell,
    one per line (the user's layout, 18 Nov 2026); empty parts are left out."""
    deadline = hm(l["deadline"]) if l["due48"] else "done in time" if l["met48"] else "done, late"
    said = j.get("last_talk") if j.get("last_talk") not in (None, "", "—") else ""
    talk = said or (hm(l["last_real"], "%d %b") if l["last_real"] else "")  # the reader's text carries its own date
    where = (("Community batch", f"{l['bootcamp']} · booked {l['booked_on']:%d %b}") if l.get("community")
             else ("Bootcamp", f"{l['bootcamp']} · D{l['day']}"))
    parts = [where, ("Status", STATUS[j["status"]]), ("Why on the list", p["why"]),
             ("48-hour deadline", deadline), ("Last real talk", talk), ("Payment", j.get("payment")), ("Objection", j.get("objection"))]
    return "\n".join(f"{k}: {v}" for k, v in parts if v and v != "—")


def iit(j: dict) -> str:
    """Whether the IIT add-on was pitched and what was said about it (the user's column, 18 Nov 2026). A grade made before
    the column existed has no answer: say so rather than "not pitched"."""
    return j.get("iit_pitch") or "Not read yet"


def caller_sheet(wb: Workbook, caller: str, rows: list, day: date, cut: datetime) -> None:
    ws = wb.create_sheet(caller[:31])
    l0 = rows[0][0]
    title(ws, 1, f"{caller} — call list for {day:%a %d %b %Y}", 13)
    g = Counter(p["group"] for _, _, p in rows)
    put(ws, 2, 1, f"TL {l0['tl']} · {len(rows)} open leads · " + " · ".join(f"{GROUPS[k].split(':')[0]} {g[k]}" for k in sorted(g)), wrap=False)
    put(ws, 3, 1, "Work top to bottom. Save every call's disposition, and after a talk the next call-back time, in LeadSquared. "
                  f"LeadSquared read {cut.astimezone(IST):%d %b %H:%M} IST.", italic=True, wrap=False)
    hdr(ws, 5, [c for c, _ in COLS], [w for _, w in COLS])
    for i, (l, j, p) in enumerate(rows, 1):
        r = 5 + i
        vals = [i, GROUPS[p["group"]], hm(p["due"], "%d %b %H:%M") if p["due"] else "", l["phone"], l["name"], details(l, j, p),
                iit(j), f"{l['dials']} / {l['dial_days']}"]
        for c, v in enumerate(vals, 1):
            put(ws, r, c, v, fill=FILL[p["group"]] if c <= 3 else None)
        ws.row_dimensions[r].height = 12.5 * max(2, vals[5].count("\n") + 1)  # Excel does not grow rows for written text
    ws.freeze_panes = "F6"


def team_sheet(wb: Workbook, rows: list, leads: list[dict], day: date, cut: datetime, team: str) -> None:
    ws = wb.active
    ws.title = "Team summary"
    title(ws, 1, f"Bootcamp collections — {team} — team summary for {day:%a %d %b %Y}", 13)
    put(ws, 2, 1, f"November bootcamp leads. LeadSquared read {cut.astimezone(IST):%d %b %H:%M} IST. Statuses read from each lead's "
                  "calls, transcripts, comments and Notes.", italic=True, wrap=False)
    title(ws, 4, "Open leads to call, by priority group (each lead counted once)")
    cols = ["AD", "Team leader", "Caller", "Open leads (total)"] + [f"{k}. {GROUP_COLS[k]}" for k in GROUPS]
    hdr(ws, 5, cols, [16, 18, 22, 10] + [13] * len(GROUPS))
    by = {}
    for l, j, p in rows:
        by.setdefault((l["ad"], l["tl"], l["caller"]), Counter())[p["group"]] += 1
    r = 6
    for (ad, tl, caller), g in sorted(by.items()):
        for c, v in enumerate([ad, tl, caller, sum(g.values())] + [g.get(k, 0) for k in GROUPS], 1):
            put(ws, r, c, v)
        r += 1
    tot = Counter(p["group"] for _, _, p in rows)
    for c, v in enumerate(["Total", "", "", len(rows)] + [tot.get(k, 0) for k in GROUPS], 1):
        put(ws, r, c, v, bold=True)
    r += 2
    title(ws, r, "48-hour rule: every lead needs one real talk (2 minutes or more) by 8 pm on the second day after it reaches the caller. "
                 "Target 90%.")
    r += 1
    put(ws, r, 1, "Only leads whose 48 hours are over are scored. Example: 7 real talks in time out of 11 leads past their deadline = 64%. "
                  "Red = below 90%.", italic=True, wrap=False)
    r += 1
    hdr(ws, r, ["AD", "Team leader", "Caller", "Leads given to the caller", "Leads whose 48 hours are over", "Real talk within 48 hours",
                "Score: real talk within 48 hours (%)", "Answered within 48 hours (any length)", "No real talk yet (leads still open)"])
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
             "Nothing is filled in here: save every call's disposition, and after a talk the next call-back time, in LeadSquared. "
             "Write anything the lead says outside a call (WhatsApp, email) as a LeadSquared Note with the date and channel.",
             "The evening update compares the day with this list: what was done, what was missed, and the next steps."]
    for i, t in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=t)
        c.font = Font(name=F, bold=i == 1, size=12 if i == 1 else 10)
    ws.column_dimensions["A"].width = 150


TEAMS = {"Mayur": "Mayur's team", "Puja / Anmol": "Anmol's team"}


def build(leads: list[dict], judged: dict, day: date, cut: datetime, out_dir: str,
          caller: str | None = None) -> tuple[list[str], str | None]:
    """One workbook per collection team (Mayur's, Anmol's) and the plan of both for the evening update; with ``caller``,
    one workbook for that caller alone and no plan (a sample, which must not replace the day's plan)."""
    if caller:
        leads = [l for l in leads if l["caller"] == caller]
        if not leads:
            raise ValueError(f"no leads for caller {caller!r}")
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
        for who in sorted({l["caller"] for l, _, _ in mine}, key=lambda c: (next(l["tl"] for l, _, _ in mine if l["caller"] == c), c)):
            caller_sheet(wb, who, [r for r in mine if r[0]["caller"] == who], day, cut)
        books.append(os.path.join(out_dir, f"Bootcamp collections call list - {caller or team} - {day:%a %d %b %Y}.xlsx"))
        wb.save(books[-1])
    if caller:
        return books, None
    saved = os.path.join(out_dir, f"plan_{day.isoformat()}.json")
    json.dump({"day": day.isoformat(), "cut": cut.isoformat(),
               "leads": {l["lead_id"]: {"caller": l["caller"], "side": l["side"], "group": p["group"], "status": j["status"],
                                        "why": p["why"], "due": p["due"].isoformat() if p["due"] else None} for l, j, p in rows}},
              open(saved, "w"), indent=1)
    return books, saved
