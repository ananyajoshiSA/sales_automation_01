"""A collection team's plan for the next three days: a workbook and a PDF per team, caller by caller.

Built from the collection audit (``analytics.collection_audit``: every open lead with its history-based chance
and Claude's reading of its notes) and fresh stages, names and phones taken just before building
(``scripts/fetch_collection_contacts.py``); a lead paid or dropped since the audit leaves the sheets.

Each lead gets one call group, in calling order:

    P  Check payment first   notes say paid, loan done or UTR while the stage is open: the team leader checks
    M  Return the lead's call the lead rang and nobody called back the same day
    A  Ready to close        only a last step is left (link sent, KYC left, a date the lead gave)
    L  Loan / EMI file       paperwork with the loan desk: chase the exact pending step
    B  Workable, recent      booked in the last 14 days, when most collections happen
    C  Workable, older       booked 15+ days ago; spread over the three days
    R  Long shot             WhatsApp first, one call on day 3
    X  Likely lost           asked for a refund or to drop: the team leader decides

Day 2 is the morning the weekend's new bookings arrive, so its first half goes to them and the backlog to
the afternoon. "Chance in 3 days" is the share of past leads in the same position collected within 3 days
(``collection_audit``), not a promise.

    python -m analytics.collection_plan exports/collection_audit data/coll_now/contacts.json exports/collection_plan 2026-10-11
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from analytics.call_plan import BORDER, FONT, INPUT_FILL, _hdr
from analytics.collection_audit import simulate
from scripts.build_plan_pdf import CSS

CLOSED = {"Collections done", "Course Enrolled", "Not Interested", "Invalid", "Invalid lead", "Irrelevant lead",
          "Invalid Number", "Duplicate"}
TIERS = "PMALBCRX"
TIER_LABEL = {"P": "P · Check payment first", "M": "M · Return the lead's call", "A": "A · Ready to close",
              "L": "L · Loan / EMI file to push", "B": "B · Workable, booked in last 14 days",
              "C": "C · Workable, older", "R": "R · Long shot", "X": "X · Likely lost – team leader"}
TIER_SHORT = {"P": "Payment checks", "M": "Return calls", "A": "Ready to close", "L": "Loan files", "B": "Workable, recent",
              "C": "Workable, older", "R": "Long shots", "X": "Likely lost"}
TIER_FILL = {"P": "FFF0C2", "M": "FBE0DE", "A": "DCEFE6", "L": "E3F1F4", "B": "E8F0FA", "C": "F3EAF7", "R": "F2F2F2",
             "X": "EDE4E1"}
ATTEMPTS = {"P": 1, "M": 3, "A": 4, "L": 3, "B": 3, "C": 2, "R": 1, "X": 1}
RECENT_DAYS = 14
DAY1_LOAD = 30      # plan choice: most first attempts a caller makes on day 1; older leads beyond it move on
DAY2_BACKLOG = 10   # plan choice: day 2 mornings go to new bookings, so fewer backlog first attempts
OUTCOMES = ["Paid in full", "Part paid / EMI started", "Loan approved / disbursed", "Payment link sent", "Docs received",
            "Callback fixed", "Spoke – thinking", "Not answered", "Switched off / failed", "Wants refund / drop",
            "Wrong number"]
PAYMENT = ["Paid", "Part paid", "Loan in process", "Link sent", "Not yet"]
TEAM_LEADERS = {"Elite Changemakers": "Mayur Sachdeva",
                "Team Puja Malik (DSV+Women AI)": "Puja Malik and Anmol Gakhar"}
TEAM_TITLE = {"Elite Changemakers": "Elite Changemakers (Mayur's team)",
              "Team Puja Malik (DSV+Women AI)": "Team Puja Malik – DSV + Women AI (Pooja's team)"}
BOOTCAMP_BALANCE = (55000, 60000)  # the owner's figure for a bootcamp balance (10 Oct 2026), until payment data comes in


def _f(x) -> float | None:
    return float(x) if x not in (None, "") else None


def clean_name(name: str) -> str:
    """LeadSquared often holds the surname twice ("Jain Jain")."""
    return re.sub(r"\b(\w+) \1\b", r"\1", (name or "").strip(), flags=re.I)


def tier_of(r: dict) -> str:
    if r.get("outlook") == "Check payment first":
        return "P"
    if r.get("outlook") == "Likely lost":
        return "X"
    if "not called back" in (r.get("flags") or ""):
        return "M"
    if r.get("outlook") == "Ready to close":
        return "A"
    if r.get("blocker") == "Loan or EMI paperwork in progress":
        return "L"
    if r.get("outlook") == "Long shot":
        return "R"
    return "B" if (_f(r.get("days_open")) or 0) <= RECENT_DAYS else "C"


def day_names(first: date) -> list[str]:
    return [(first + timedelta(days=i)).strftime("%a %d %b").replace(" 0", " ") for i in range(3)]


def when(tier: str, slot: int, days: list[str]) -> str:
    d1, d2, d3 = days
    return {
        "P": f"{d1} 10:00: team leader checks the payment; call only if it has not come in",
        "M": f"{d1} 10:00–11:00, first thing; retry {d1} 15:00, then {d2} 15:00",
        "A": f"{d1} 11:00–12:00 with the link ready; retry {d1} 18:00 and {d2} 15:00",
        "L": f"{d1} 12:00 after the loan-desk review; retry {d2} 15:00 and {d3}",
        "B": f"{d1} 12:00–14:00; retry {d1} 17:30 and {d3} 11:00",
        "C": [f"{d1} 15:00–17:00; retry {d3}", f"{d2} 15:00–17:00; retry {d3}", f"{d3} 11:00–13:00; retry {d3} 17:00"][slot],
        "R": f"{d1}: WhatsApp only; {d3} 15:00–17:00: one call",
        "X": f"{d1}: team leader confirms with the lead, then fixes the stage",
    }[tier]


def plan_rows(audit_rows: list[dict], contacts: dict[str, dict], first: date) -> tuple[dict[str, dict[str, list]], Counter]:
    """{team: {caller: [rows in calling order]}} and how many audited leads left because they closed since."""
    days = day_names(first)
    gone = Counter()
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in audit_rows:
        c = contacts.get(r["lead_id"], {})
        stage = c.get("stage") or r["stage"]
        if stage in CLOSED:
            gone[stage] += 1
            continue
        t = tier_of(r)
        by[r["team"]][r["caller"]].append({**r, "tier": t, "name": clean_name(c.get("name") or ""), "phone": c.get("phone") or "",
                                           "stage": stage, "chance_3d": _f(r.get("chance_3d")) or 0.0,
                                           "chance_3d_low": _f(r.get("chance_3d_low")) or 0.0,
                                           "chance_3d_high": _f(r.get("chance_3d_high")) or 0.0,
                                           "chance_14d": _f(r.get("chance_14d")) or 0.0})
    for team in by.values():
        for caller, rows in team.items():
            rows.sort(key=lambda x: (TIERS.index(x["tier"]), -x["chance_3d"], _f(x["days_open"]) or 0))
            load = sum(1 for x in rows if x["tier"] in "MALB")
            day2 = 0
            for x in rows:
                slot = 0
                if x["tier"] == "C":
                    if load < DAY1_LOAD:
                        load += 1
                    elif day2 < DAY2_BACKLOG:
                        slot, day2 = 1, day2 + 1
                    else:
                        slot = 2
                x["when"] = when(x["tier"], slot, days)
                x["first_day"] = days[slot] if x["tier"] not in "RX" else days[0]
    return {t: dict(c) for t, c in by.items()}, gone


# ----------------------------------------------------------------------------------------------------- workbook

CALLER_COLS = [
    ("#", 5), ("Call group", 24), ("When (next 3 days)", 30), ("Lead", 22), ("Phone", 15), ("Kind", 10), ("Course", 20),
    ("Booked (IST)", 15), ("Days open", 8), ("Stage", 16), ("Situation", 46), ("Next step", 46), ("What to say", 46),
    ("Blocker", 22), ("Problems seen", 34), ("Chance in 3 days (history)", 10), ("Attempts required", 9),
    ("Attempt 1 time", 11), ("Attempt 1 outcome", 20), ("Attempt 2 time", 11), ("Attempt 2 outcome", 20),
    ("Attempt 3 time", 11), ("Attempt 3 outcome", 20), ("Attempt 4 time", 11), ("Attempt 4 outcome", 20),
    ("WhatsApp sent (Y/N)", 10), ("Next step date/time", 16), ("Payment status", 16), ("Attempts done", 10),
    ("Status", 13), ("Notes", 30), ("Lead ID", 38),
]
COL = {name: i + 1 for i, (name, _) in enumerate(CALLER_COLS)}
INPUT_COLS = [n for n, _ in CALLER_COLS if n.startswith("Attempt ") and "required" not in n] + [
    "WhatsApp sent (Y/N)", "Next step date/time", "Payment status", "Notes"]
WRAP = {"Situation", "Next step", "What to say", "Problems seen", "When (next 3 days)", "Lead", "Course", "Blocker"}


def caller_sheet(wb: Workbook, caller: str, rows: list[dict], days: list[str]) -> tuple[int, int]:
    ws = wb.create_sheet(caller[:31])
    ws["A1"] = f"{caller}: collection plan, {days[0]} to {days[2]}"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = ("Work top to bottom. Fill the yellow cells after every attempt. P is checked by the team leader first; "
                "M (the lead rang you) goes before everything else. Every real conversation ends with a dated next step.")
    ws["A2"].font = Font(name=FONT, italic=True, size=9, color="555555")
    first, last = 5, 4 + len(rows)
    L = lambda name: get_column_letter(COL[name])  # noqa: E731
    ws["A3"], ws["C3"] = "Attempts logged", f"=SUM({L('Attempts done')}{first}:{L('Attempts done')}{last})"
    ws["D3"], ws["E3"] = "Collected", f'=COUNTIF({L("Status")}{first}:{L("Status")}{last},"Collected")'
    ws["F3"], ws["G3"] = "Expected in 3 days (history)", f"=SUM({L('Chance in 3 days (history)')}{first}:{L('Chance in 3 days (history)')}{last})"
    ws["G3"].number_format = "0.0"
    for ref in ("A3", "C3", "D3", "E3", "F3", "G3"):
        ws[ref].font = Font(name=FONT, bold=True, size=10)
    _hdr(ws, 4, [c for c, _ in CALLER_COLS], [w for _, w in CALLER_COLS])
    ws.row_dimensions[4].height = 42
    dv_out = DataValidation(type="list", formula1='"' + ",".join(OUTCOMES) + '"', allow_blank=True)
    dv_pay = DataValidation(type="list", formula1='"' + ",".join(PAYMENT) + '"', allow_blank=True)
    dv_yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    for dv in (dv_out, dv_pay, dv_yn):
        ws.add_data_validation(dv)
    for i, r in enumerate(rows):
        rr = first + i
        vals = {"#": i + 1, "Call group": TIER_LABEL[r["tier"]], "When (next 3 days)": r["when"], "Lead": r["name"],
                "Phone": r["phone"], "Kind": r["kind"], "Course": r["course"], "Booked (IST)": r["booked_ist"],
                "Days open": round(_f(r["days_open"]) or 0), "Stage": r["stage"], "Situation": r.get("situation", ""),
                "Next step": r.get("next_action", ""), "What to say": r.get("what_to_say", ""), "Blocker": r.get("blocker", ""),
                "Problems seen": r.get("flags", ""), "Chance in 3 days (history)": r["chance_3d"],
                "Attempts required": ATTEMPTS[r["tier"]], "Lead ID": r["lead_id"]}
        for name, v in vals.items():
            c = ws.cell(row=rr, column=COL[name], value=v)
            c.font = Font(name=FONT, size=9, bold=name in ("Lead", "Call group"))
            c.alignment = Alignment(wrap_text=name in WRAP, vertical="top")
            c.fill = PatternFill("solid", fgColor=TIER_FILL[r["tier"]])
            c.border = BORDER
        ws.cell(row=rr, column=COL["Chance in 3 days (history)"]).number_format = "0%"
        times = ",".join(f"{L(f'Attempt {k} time')}{rr}" for k in range(1, 5))
        ws.cell(row=rr, column=COL["Attempts done"], value=f"=COUNTA({times})")
        paid = "+".join(f'({L(f"Attempt {k} outcome")}{rr}="Paid in full")' for k in range(1, 5))
        ws.cell(row=rr, column=COL["Status"], value=(
            f'=IF({paid}+({L("Payment status")}{rr}="Paid")>0,"Collected",'
            f'IF({L("Attempts done")}{rr}>={L("Attempts required")}{rr},"Done",'
            f'IF({L("Attempts done")}{rr}=0,"Not started","In progress")))'))
        for name in ("Attempts done", "Status"):
            c = ws.cell(row=rr, column=COL[name])
            c.font = Font(name=FONT, size=9, bold=True)
            c.border = BORDER
        for name in INPUT_COLS:
            c = ws.cell(row=rr, column=COL[name])
            c.fill, c.border, c.font = INPUT_FILL, BORDER, Font(name=FONT, size=9)
        for k in range(1, 5):
            dv_out.add(f"{L(f'Attempt {k} outcome')}{rr}")
        dv_pay.add(f"{L('Payment status')}{rr}")
        dv_yn.add(f"{L('WhatsApp sent (Y/N)')}{rr}")
        ws.row_dimensions[rr].height = 90
    rng = f"{L('Status')}{first}:{L('Status')}{last}"
    for text, color in (("Collected", "B7E1C1"), ("Not started", "F8D7D3"), ("Done", "E2E2E2")):
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'{L("Status")}{first}="{text}"'],
                                                       fill=PatternFill("solid", fgColor=color)))
    ws.freeze_panes = ws.cell(row=first, column=COL["Phone"])
    ws.auto_filter.ref = f"A4:{get_column_letter(len(CALLER_COLS))}{last}"
    return first, last


def summary_sheet(wb: Workbook, callers: list[str], ranges: dict[str, tuple[int, int]], title: str, days: list[str]) -> None:
    ws = wb.create_sheet("Team summary", 1)
    ws["A1"] = f"{title}: live progress, {days[0]} to {days[2]}"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = ("Updates by itself as callers fill their sheets. 'Expected in 3 days' sums each lead's chance from past "
                "leads in the same position; it is what usually happens, not a target.")
    ws["A2"].font = Font(name=FONT, italic=True, size=9, color="555555")
    heads = (["Caller"] + [TIER_SHORT[t] for t in TIERS] +
             ["Leads", "Expected in 3 days (history)", "Attempts required", "Attempts logged", "Not started",
              "P, M or A not started", "Collected so far"])
    C = {h: i + 1 for i, h in enumerate(heads)}
    _hdr(ws, 4, heads, [22] + [9] * len(TIERS) + [8, 12, 10, 10, 10, 11, 11])
    L = lambda name: get_column_letter(COL[name])  # noqa: E731
    for i, o in enumerate(callers):
        r = 5 + i
        sh = f"'{o[:31]}'"
        f, l = ranges[o]
        grp = f"{sh}!${L('Call group')}${f}:${L('Call group')}${l}"
        st = f"{sh}!${L('Status')}${f}:${L('Status')}${l}"
        vals = {"Caller": o, "Leads": f"=COUNTA({grp})",
                "Expected in 3 days (history)": f"=SUM({sh}!${L('Chance in 3 days (history)')}${f}:${L('Chance in 3 days (history)')}${l})",
                "Attempts required": f"=SUM({sh}!${L('Attempts required')}${f}:${L('Attempts required')}${l})",
                "Attempts logged": f"=SUM({sh}!${L('Attempts done')}${f}:${L('Attempts done')}${l})",
                "Not started": f'=COUNTIF({st},"Not started")',
                "P, M or A not started": "=" + "+".join(f'COUNTIFS({grp},"{k} ·*",{st},"Not started")' for k in "PMA"),
                "Collected so far": f'=COUNTIF({st},"Collected")'}
        vals.update({TIER_SHORT[t]: f'=COUNTIF({grp},"{t} ·*")' for t in TIERS})
        for h, v in vals.items():
            c = ws.cell(row=r, column=C[h], value=v)
            c.font = Font(name=FONT, size=10, bold=h == "Caller")
            c.border = BORDER
        ws.cell(row=r, column=C["Expected in 3 days (history)"]).number_format = "0.0"
    tr = 5 + len(callers)
    ws.cell(row=tr, column=1, value="Team")
    for c in range(2, len(heads) + 1):
        col = get_column_letter(c)
        ws.cell(row=tr, column=c, value=f"=SUM({col}5:{col}{tr - 1})").number_format = (
            "0.0" if c == C["Expected in 3 days (history)"] else "0")
    for c in range(1, len(heads) + 1):
        ws.cell(row=tr, column=c).font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=tr, column=c).border = BORDER
    ws.freeze_panes = "B5"


def leader_sheet(wb: Workbook, rows: list[dict]) -> None:
    ws = wb.create_sheet("Team leader actions", 2)
    ws["A1"] = "For the team leader: payment checks, loan files and likely-lost leads"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    heads = ["Call group", "Caller", "Lead", "Phone", "Kind", "Days open", "Stage", "Situation", "Next step", "Lead ID"]
    _hdr(ws, 3, heads, [24, 18, 22, 15, 10, 8, 16, 60, 60, 38])
    r = 4
    for x in sorted((x for x in rows if x["tier"] in "PLX"), key=lambda x: ("PLX".index(x["tier"]), x["caller"])):
        for i, v in enumerate([TIER_LABEL[x["tier"]], x["caller"], x["name"], x["phone"], x["kind"],
                               round(_f(x["days_open"]) or 0), x["stage"], x.get("situation", ""), x.get("next_action", ""),
                               x["lead_id"]], 1):
            c = ws.cell(row=r, column=i, value=v)
            c.font = Font(name=FONT, size=9)
            c.alignment = Alignment(wrap_text=i in (3, 8, 9), vertical="top")
            c.fill = PatternFill("solid", fgColor=TIER_FILL[x["tier"]])
            c.border = BORDER
        r += 1
    ws.freeze_panes = "A4"


def guide_sheet(wb: Workbook, title: str, days: list[str], as_of: str) -> None:
    ws = wb.active
    ws.title = "How to use"
    d1, d2, d3 = days
    lines = [
        (f"{title}: collection plan, {d1} to {d3}", "title"),
        (f"Built from LeadSquared data as of {as_of} IST, stages re-checked just before building.", None),
        ("", None),
        ("What is in this file", "h"),
        ("One sheet per caller, leads in calling order, every open collection lead booked since 1 Sep. Each row says "
         "where the lead stands, what is blocking payment, the next step, what to say and when, read lead by lead "
         "from the LeadSquared history.", None),
        ("Call groups: P check payment first (team leader), M return the lead's call, A ready to close, L loan / EMI "
         "file to push, B workable and booked in the last 14 days, C workable and older, R long shot (WhatsApp first), "
         "X likely lost (team leader).", None),
        ("'Chance in 3 days' is the share of past leads in the same position (bootcamp or community, days since "
         "booking, stage, last real talk) that paid within 3 days. It ranks the list; it is not a promise.", None),
        ("", None),
        ("What the caller fills (yellow cells)", "h"),
        ("After every dial: the time and the outcome from the dropdown. Then WhatsApp Y/N, the next step date and time "
         "agreed, and payment status. Attempts done and Status fill in by themselves.", None),
        ("In LeadSquared, after every call over 2 minutes: one line on what is stopping payment and a dated next step. "
         "A note of '.' or nothing is not allowed.", None),
        ("", None),
        ("The three days", "h"),
        (f"{d1}: payment checks 10:00, return calls 10:00–11:00, ready to close 11:00–12:00, loan-desk review 12:00, "
         "recent workable 12:00–14:00, older workable 15:00–17:00, second attempts 17:30–19:30. WhatsApp every long shot.", None),
        (f"{d2}: the weekend's new bookings come first (first call within 2 hours of hand-over, counselling before "
         "payment). Backlog from 15:00: second attempts on A, L and B, then the older leads moved to this day.", None),
        (f"{d3}: remaining older leads 11:00–13:00, long shots 15:00–17:00, third attempts on anything not closed.", None),
        ("", None),
        ("Rules", "h"),
        ("Return every call from a lead within 15 minutes, before anything else.", None),
        ("When the Next step names a time the lead asked for (an evening slot, a date), that time wins over the 'When' column.", None),
        ("A lead who does not answer gets one WhatsApp asking for a time, then a call at that time; no more than 3 dials a day.", None),
        ("Never mark Not Interested without a real conversation and the reason in the note.", None),
        ("If calls fail at the same second on different leads, it is the dialer: switch line and tell the team leader.", None),
    ]
    for i, (text, kind) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(name=FONT, size={"title": 15, "h": 12}.get(kind, 10), bold=kind in ("title", "h"))
    ws.column_dimensions["A"].width = 130


def write_workbook(path: str, team: str, callers: dict[str, list[dict]], days: list[str], as_of: str) -> None:
    wb = Workbook()
    title = TEAM_TITLE.get(team, team)
    guide_sheet(wb, title, days, as_of)
    order = sorted(callers, key=lambda c: -len(callers[c]))
    ranges = {c: caller_sheet(wb, c, callers[c], days) for c in order}
    summary_sheet(wb, order, ranges, title, days)
    leader_sheet(wb, [x for c in order for x in callers[c]])
    wb.save(path)


# ----------------------------------------------------------------------------------------------------- PDF

def esc(s, n: int | None = None) -> str:
    s = str(s or "").strip()
    if n and len(s) > n:
        s = s[: n - 1].rsplit(" ", 1)[0] + "…"
    return html.escape(s)


EXTRA_CSS = """
.tier-P td { background: #fff7df; } .tier-M td { background: #fdeceb; } .tier-A td { background: #f1f8f4; }
.tier-L td { background: #eef6f8; } .tier-B td { background: #f4f7fc; }
.leads td.lead { width: 20%; } .leads td:nth-child(3) { width: 27%; } .leads td:nth-child(4) { width: 27%; }
.day { border: 1px solid #d5dcd8; border-radius: 4pt; padding: 6pt 9pt; margin: 5pt 0; page-break-inside: avoid; }
.day b.d { color: #123f36; }
.stats { grid-template-columns: repeat(8, 1fr); }
"""


def _issue_counts(rows: list[dict]) -> Counter:
    return Counter(i for r in rows for i in (r.get("process_issues") or "").split("; ") if i)


def _flag_count(rows: list[dict], text: str) -> int:
    return sum(1 for r in rows if text in (r.get("flags") or ""))


def decay(history: list[dict], kind: str, age: str) -> str:
    """The range of 14-day collection rates across stages for leads of ``kind`` at ``age``, e.g. "41–86%"."""
    rates = [h["collected_14d_%"] for h in history
             if h["key"].startswith(f"{kind} / {age} /") and h["snapshots"] >= 30 and "collected_14d_%" in h]
    return f"{min(rates):.0f}–{max(rates):.0f}%" if rates else "not enough history"


def title_days(first: date) -> str:
    last = first + timedelta(days=2)
    return f"{first:%a} {first.day} to {last:%a} {last.day} {last:%B %Y}"


def caller_days(rows: list[dict], days: list[str]) -> str:
    d1, d2, d3 = days
    n = Counter(r["tier"] for r in rows)
    c_by = Counter(r["first_day"] for r in rows if r["tier"] == "C")
    parts1 = [f"{n[t]} {TIER_SHORT[t].lower()}" for t in "MAL" if n[t]]
    if n["B"]:
        parts1.append(f"{n['B']} recent workable")
    if c_by[d1]:
        parts1.append(f"{c_by[d1]} older workable")
    d1_text = ", ".join(parts1) or "no backlog first attempts"
    p = f" The team leader first checks {n['P']} payment(s) for you." if n["P"] else ""
    x = f" {n['X']} likely-lost lead(s) go to the team leader." if n["X"] else ""
    return (f'<div class="day"><b class="d">{d1}:</b> {d1_text}.{p}{x} WhatsApp your {n["R"]} long shot(s) asking for a time.</div>'
            f'<div class="day"><b class="d">{d2}:</b> new bookings first. From 15:00: second attempts on ready-to-close, loan '
            f'and recent leads{f", plus {c_by[d2]} older workable" if c_by[d2] else ""}.</div>'
            f'<div class="day"><b class="d">{d3}:</b> {f"{c_by[d3]} older workable 11:00–13:00, " if c_by[d3] else ""}'
            f'{n["R"]} long-shot call(s) 15:00–17:00, third attempts on anything not closed.</div>')


def caller_section(caller: str, rows: list[dict], days: list[str]) -> str:
    n = Counter(r["tier"] for r in rows)
    exp, lo, hi = simulate(rows, "chance_3d")
    stale = _flag_count(rows, "No dial for") + _flag_count(rows, "Never dialled")
    issues = _issue_counts(rows)
    top = [f"{k.lower()} ({v})" for k, v in issues.most_common(4) if k != "No dated next step"][:3]
    nd = issues.get("No dated next step", 0)
    coach = (f"On your list: {stale} lead(s) not dialled for 3+ days, {_flag_count(rows, 'not called back')} lead(s) whose call "
             f"was not returned, {nd} with no dated next step. Most common handling issues: {', '.join(top) or 'none'}.")
    stats = "".join(f"<div><b>{n[t]}</b><span>{TIER_SHORT[t]}</span></div>" for t in "PMALBCR") + \
        f"<div><b>{exp:.1f}</b><span>expected in 3 days ({lo}–{hi})</span></div>"
    body = []
    for i, r in enumerate(rows, 1):
        chips = []
        if "not called back" in (r.get("flags") or ""):
            chips.append('<span class="chip bad">Lead rang, not returned</span>')
        if "No dial for" in (r.get("flags") or "") or "Never dialled" in (r.get("flags") or ""):
            chips.append('<span class="chip warn">No dial 3+ days</span>')
        if "paid or loan done" in (r.get("flags") or ""):
            chips.append('<span class="chip good">Notes say paid</span>')
        body.append(f"""<tr class="tier-{r['tier']}"><td class="num">{i}</td>
<td class="lead"><b>{esc(r['name']) or 'Unnamed lead'}</b><div class="sub">{esc(TIER_LABEL[r['tier']])}</div>
<div class="sub">{esc(r['kind'])} · {esc(r['course'], 30)} · {round(_f(r['days_open']) or 0)} days · {esc(r['stage'])}</div>{' '.join(chips)}</td>
<td>{esc(r.get('situation'), 260)}<div class="sub"><b>Blocker:</b> {esc(r.get('blocker'))}</div></td>
<td><i>“{esc(r.get('what_to_say'), 280)}”</i></td>
<td>{esc(r.get('next_action'), 230)}<div class="sub"><b>When:</b> {esc(r['when'])}</div></td></tr>""")
    return f"""<section class="caller"><h2>{esc(caller)}</h2>
<div class="stats">{stats}</div>
<p class="note">{esc(coach)}</p>
{caller_days(rows, days)}
<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Where it stands</th><th>What to say</th><th>Next step and when</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>
<p class="note">Phone numbers and the tracking columns are on the "{esc(caller[:31])}" sheet of the workbook.</p></section>"""


def standing_rows(team_rows: list[dict]) -> str:
    out = []
    for x in team_rows:
        out.append(f"<tr><td><b>{esc(x['kind'])}</b></td><td>{x['pool']}</td><td>{x['collected']} ({x['collected_%_now']}%)</td>"
                   f"<td>{x['lost']}</td><td>{x['open']}</td><td>{x['expected_next_3d']} ({x['range_3d']})</td>"
                   f"<td>{x['expected_next_45d']} ({x['range_45d']})</td><td>{x['projected_%_45d']}%</td>"
                   f"<td>{x['needed_for_75%']} ({x['needed_for_75%_share_of_open']} of open)</td>"
                   f"<td>{x['needed_for_80%']} ({x['needed_for_80%_share_of_open']} of open)</td></tr>")
    return "".join(out)


def build_html(team: str, callers: dict[str, list[dict]], report: dict, first: date, as_of: str, gone: int) -> str:
    days = day_names(first)
    title = TEAM_TITLE.get(team, team)
    leader = TEAM_LEADERS.get(team, "the team leader")
    d1, d2, d3 = days
    rows = [r for c in callers.values() for r in c]
    order = sorted(callers, key=lambda c: -len(callers[c]))
    n = Counter(r["tier"] for r in rows)
    exp3, lo3, hi3 = simulate(rows, "chance_3d")
    boot = [r for r in rows if r["kind"] == "Bootcamp"]
    eb, lb, hb = simulate(boot, "chance_3d")
    team_rows = [x for x in report["by_kind_team"] if x["team"] == team]
    need75 = sum(x["needed_for_75%"] for x in team_rows)
    blockers = Counter(r.get("blocker") for r in rows).most_common(6)
    issues = _issue_counts(rows).most_common(6)
    outlook = Counter(r.get("outlook") for r in rows)
    lo_r, hi_r = BOOTCAMP_BALANCE
    hist = report.get("history", [])
    early_b, late_b = decay(hist, "Bootcamp", "0-3 days"), decay(hist, "Bootcamp", "22-30 days")
    early_c, late_c = decay(hist, "Community", "0-3 days"), decay(hist, "Community", "22-30 days")
    summary = "".join(
        f"<tr><td><b>{esc(c)}</b></td>" + "".join(f"<td>{sum(1 for r in callers[c] if r['tier'] == t)}</td>" for t in TIERS)
        + f"<td>{len(callers[c])}</td><td><b>{simulate(callers[c], 'chance_3d')[0]:.1f}</b></td>"
        f"<td>{_flag_count(callers[c], 'No dial for') + _flag_count(callers[c], 'Never dialled')}</td></tr>" for c in order)
    leader_rows = "".join(
        f"<tr><td>{esc(TIER_SHORT[r['tier']])}</td><td>{esc(r['caller'])}</td><td>{esc(r['name'])}</td>"
        f"<td>{esc(r.get('situation'), 200)}</td><td>{esc(r.get('next_action'), 180)}</td></tr>"
        for r in sorted((r for r in rows if r["tier"] in "PX"), key=lambda r: ("PX".index(r["tier"]), r["caller"])))
    loan_rows = "".join(
        f"<tr><td>{esc(r['caller'])}</td><td>{esc(r['name'])}</td><td>{round(_f(r['days_open']) or 0)}</td>"
        f"<td>{esc(r.get('situation'), 220)}</td></tr>"
        for r in sorted((r for r in rows if r["tier"] == "L"), key=lambda r: -(_f(r["days_open"]) or 0)))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)} – collection plan {esc(d1)}–{esc(d3)}</title>
<style>{CSS}{EXTRA_CSS}</style></head><body>
<div class="cover">
  <div class="band">
    <div class="eyebrow">{esc(title)} · for {esc(leader)}</div>
    <h1>Collection plan<br>{esc(title_days(first))}</h1>
    <p>Every open collection lead booked since 1 September, read one by one: who each caller calls over the next three days,
    in what order, what to say, and how you check it is happening.</p>
  </div>
  <div>
    <div class="kpis">
      <div><b>{len(rows)}</b><span>open collection leads on the sheets ({len(order)} callers)</span></div>
      <div><b>{n['P']}</b><span>payment checks first: notes say paid or loan done</span></div>
      <div><b>{n['M']}</b><span>leads who rang and were not called back</span></div>
      <div><b>{n['A'] + n['L']}</b><span>ready to close ({n['A']}) or loan / EMI files to push ({n['L']})</span></div>
      <div><b>≈{exp3:.0f}</b><span>collections in 3 days at past rates ({lo3}–{hi3}); more if this plan is worked</span></div>
      <div><b>{need75}</b><span>more collections needed for 75% of the pool booked since 1 Sep</span></div>
    </div>
    <h3>Contents</h3>
    <ol class="toc">
      <li>Where the team stands</li><li>What the lead-by-lead audit found</li><li>The three days, hour by hour</li>
      <li>How to work each call group</li><li>After every call</li><li>Objection playbook</li><li>WhatsApp messages</li>
      <li>Team leader: checks, payment and loan files</li><li>Caller summary</li><li>Caller-wise lists</li>
    </ol>
    <p class="note">Data: LeadSquared stages, calls, notes and owner changes for every lead tagged for bootcamp or community
    collections, as of {esc(as_of)} IST; stages re-checked just before building ({gone} lead(s) closed since and left the sheets).
    Collected means the stage reached Collections done or Course Enrolled; LeadSquared holds no payment amounts yet.</p>
  </div>
</div>

<section><h2>1. Where the team stands</h2>
<p>Pool = leads that paid a booking amount from 1 September, counted by the caller they settled with in the first 24 hours.</p>
<table><thead><tr><th></th><th>Pool</th><th>Collected</th><th>Lost</th><th>Open</th><th>Expected in 3 days</th>
<th>Expected in 45 days</th><th>Projected</th><th>Needed for 75%</th><th>Needed for 80%</th></tr></thead>
<tbody>{standing_rows(team_rows)}</tbody></table>
<div class="warnbox"><p><b>What this means.</b> "Expected" is what usually happens to leads in the same position (from June–August
leads), with a 90% range. At past rates the team ends this pool well short of 75%. A lead's chance falls quickly with time.
Of past leads still open in their first 3 days, {early_b} of bootcamp leads and {early_c} of community leads paid within the
next 14 days, depending on their stage. Three to four weeks after booking, it was {late_b} for bootcamp and {late_c} for
community. So the older open leads close only with a real change in how they are worked, and the 75–80% target is won mostly
on new bookings in their first week.</p></div>
<p><b>In rupees (estimate):</b> at the bootcamp balance of about ₹{lo_r // 1000}–{hi_r // 1000}k, the ≈{eb:.0f} bootcamp
collections expected in 3 days are worth about ₹{lb * lo_r / 1e5:.1f}–{hb * hi_r / 1e5:.1f} lakh. Community balances are not
in LeadSquared, so community is counted in leads only.</p></section>

<section><h2>2. What the lead-by-lead audit found</h2>
<p>Each open lead's LeadSquared history since booking was read in full: stage changes and their notes, every call and its length,
calls from the lead, owner changes, forms and Zipteams summaries.</p>
<div class="two"><div><h3>Outlook</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in outlook.most_common())}</tbody></table></div>
<div><h3>Main blocker</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in blockers)}</tbody></table></div></div>
<h3>How the leads were handled</h3>
<table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v} of {len(rows)} leads</td></tr>" for k, v in issues)}</tbody></table>
<ul>
  <li><b>Long calls, no notes.</b> Calls of 10–50 minutes often end with a note of "." or nothing, so the reason the lead has not paid
  is unknown and the next caller starts again. Every call over 2 minutes needs one line on the blocker and a dated next step.</li>
  <li><b>Loan files stall.</b> Leads share documents with the loan desk and then nothing is logged for 1–3 weeks. One person reviews
  every Loan pending file with the loan desk each day (section 8).</li>
  <li><b>The lead rings, nobody calls back.</b> Leads who call in are the most ready to pay; many were not called back that day.</li>
  <li><b>Over-dialling, short calls.</b> Some leads get 5–15 dials a day, or dozens of answered calls under a minute from several
  callers. One WhatsApp asking for a time works better. Strings of identical short "answered" calls should be checked with
  the dialer team before any caller is judged on them.</li>
</ul></section>

<section><h2>3. The three days, hour by hour</h2>
<table><thead><tr><th>When</th><th>Callers</th><th>Team leader</th></tr></thead><tbody>
<tr><td class="t">{esc(d1)} 09:45</td><td>Huddle: each caller reads out their A and L leads and the exact ask for each.</td><td>Hand finance / the loan desk the P list ({n['P']} leads).</td></tr>
<tr><td class="t">10:00–11:00</td><td>Return every call from a lead (M, {n['M']} leads).</td><td>Payment checks (P); tell each caller which are paid.</td></tr>
<tr><td class="t">11:00–12:00</td><td>Ready to close (A): link or KYC step ready before dialling; stay on the line until it is done.</td><td>Listen in on 2 A calls.</td></tr>
<tr><td class="t">12:00–12:30</td><td>Loan files (L): call the lead with the exact pending step agreed with the loan desk.</td><td>Loan-desk review of all {n['L']} L files.</td></tr>
<tr><td class="t">12:30–14:00</td><td>Recent workable (B): counsel first where the lead never had a real conversation.</td><td></td></tr>
<tr><td class="t">15:00–17:00</td><td>Older workable (C) on the sheet for today; second attempts on M and A.</td><td>Checkpoint (section 8).</td></tr>
<tr><td class="t">17:30–19:30</td><td>Evening slots: leads who asked for after-office calls; second attempts on B; WhatsApp every long shot (R).</td><td>Every connected lead has a dated next step.</td></tr>
<tr><td class="t">{esc(d2)}</td><td><b>New bookings from the weekend first</b> (first call within 2 hours of hand-over, counselling before payment). From 15:00: second attempts on A, L and B, then C moved to this day.</td><td>Hand new bookings out by 10:00; check every one is dialled by 13:00.</td></tr>
<tr><td class="t">{esc(d3)}</td><td>11:00–13:00 remaining C leads; 15:00–17:00 one call to each long shot; third attempts on anything not closed.</td><td>X leads decided; stages fixed; carry-forward list for the next plan.</td></tr>
</tbody></table></section>

<section><h2>4. How to work each call group</h2>
<h3>P · Check payment first</h3><p>The notes say paid, loan done, disbursed or UTR while the stage is open. The team leader checks with
finance or the loan desk before anyone calls. Paid: move the stage and start onboarding. Not paid: the caller calls with the one pending step.</p>
<h3>M · Return the lead's call</h3><p>"Sorry I missed your call. How can I help?" Let the lead talk first; they rang for a reason.
Three attempts over the three days, WhatsApp after the first.</p>
<h3>A · Ready to close</h3><ol><li>Have the link, the EMI split or the KYC step open before dialling.</li>
<li>Refer to the last conversation; don't restart the pitch.</li><li>Stay on the line until the payment or the step is done, or fix the exact time today.</li>
<li>Unanswered: WhatsApp A1, retry at 18:00 and the next afternoon.</li></ol>
<h3>L · Loan / EMI file to push</h3><p>Agree with the loan desk what exactly is pending (a document, KYC, a co-applicant, a rejection).
Call the lead with that one step and a deadline. A rejected loan needs another route the same day: another partner, card EMI, or an
instalment split approved by the team leader.</p>
<h3>B · Workable, booked in the last 14 days</h3><p>The lead's best window. Counsel first where there was never a real conversation:
the lead's goal, what the course gives them, then the balance and EMI options. End with a dated next step.</p>
<h3>C · Workable, older</h3><p>Open with what the lead said last time. Find the real blocker and answer it. Two attempts across the three days.</p>
<h3>R · Long shot</h3><p>WhatsApp R1 asking for a convenient time; one call on day 3 at that time. No more than one dial a day.</p>
<h3>X · Likely lost</h3><p>The lead asked for a refund or to drop. The team leader calls once: confirm, offer a later batch if the reason is
timing or health, otherwise follow the refund process and close the stage with the reason.</p>
<h3>Attempt rules</h3><ul><li>When the next step names a time the lead asked for (an evening slot, a date), that time wins over the group's slot.</li>
<li>At most 3 dials a day per lead, at least 90 minutes apart, one at the time the lead asked for.</li>
<li>Never mark Not Interested without a real conversation and the reason in the note.</li>
<li>If calls fail at the same second across leads, it is the dialer: switch line and tell the team leader.</li></ul></section>

<section><h2>5. After every call</h2>
<ol><li>In the workbook: attempt time and outcome (dropdown), WhatsApp Y/N, next step date and time, payment status.</li>
<li>In LeadSquared, after every call over 2 minutes: one line on what is stopping payment, and the stage with a dated follow-up.</li></ol>
<table><thead><tr><th>Outcome</th><th>Do next</th><th>LeadSquared stage</th></tr></thead><tbody>
<tr><td>Paid in full</td><td>Confirm, start onboarding, tell the team leader</td><td>Collections done</td></tr>
<tr><td>Part paid / EMI started</td><td>Fix the date of the next part; send the schedule</td><td>Follow Up For Closure + date</td></tr>
<tr><td>Loan approved / disbursed</td><td>Confirm with the loan desk, then close</td><td>Collections done once confirmed</td></tr>
<tr><td>Payment link sent</td><td>Call back in 2–3 hours to confirm</td><td>Follow Up For Closure + date</td></tr>
<tr><td>Docs received</td><td>Hand to the loan desk the same hour; tell the lead the next step</td><td>Loan pending + date</td></tr>
<tr><td>Callback fixed</td><td>Exact time in Next step</td><td>Call Back Later + date and time</td></tr>
<tr><td>Spoke – thinking</td><td>Ask what exactly they need to decide; decision call within 24 hours</td><td>Follow-up within 24 h</td></tr>
<tr><td>Not answered / switched off</td><td>WhatsApp; next attempt per the rules</td><td>No change</td></tr>
<tr><td>Wants refund / drop</td><td>Pass to the team leader with the reason</td><td>No change until the team leader decides</td></tr>
</tbody></table></section>

<section><h2>6. Objection playbook</h2><div class="two">
<div class="script"><h4>"My loan is stuck / documents pending"</h4><p>Name the exact pending item from the loan desk. Offer to stay on a call while
they upload it. If rejected, offer card EMI or an instalment split approved by the team leader the same day.</p></div>
<div class="script"><h4>"I'll pay after salary / on a date"</h4><p>Agree the exact date and amount; send the link that day. Offer EMI so the first
part fits this month. Put the date in LeadSquared.</p></div>
<div class="script"><h4>"I need to ask my spouse / parents"</h4><p>Offer a 3-way call today at a time that suits them; send the fee, EMI table and
refund terms in writing for the family.</p></div>
<div class="script"><h4>"Not sure the course is worth it"</h4><p>Go back to the goal they gave at booking. Explain what they get and how past
learners used it. Don't push payment until the doubt is answered.</p></div>
<div class="script"><h4>"I'm busy / travelling"</h4><p>"What time exactly works?" Log it and call at that time. Never accept "later" without a time.</p></div>
<div class="script"><h4>"I want a refund / to drop"</h4><p>Ask why. Timing or health: offer a later batch. Otherwise pass to the team leader with
the reason; don't argue.</p></div></div></section>

<section><h2>7. WhatsApp messages</h2>
<div class="script"><h4>A1 – ready to close, unanswered</h4><p class="mono">Hi [Name], [Caller] from LawSikho. As discussed, here is the link to complete
your [program] enrolment: [link]. EMI options: [months: amounts]. I'll call you at [time] to help you finish it.</p></div>
<div class="script"><h4>L1 – loan / EMI file</h4><p class="mono">Hi [Name], [Caller] from LawSikho. Your loan file for [program] needs only [pending item].
Could you share it today? Call me on this number if anything is unclear.</p></div>
<div class="script"><h4>M1 – the lead rang</h4><p class="mono">Hi [Name], [Caller] from LawSikho. Sorry I missed your call. When is a good time to call you back today?</p></div>
<div class="script"><h4>R1 – long shot</h4><p class="mono">Hi [Name], [Caller] from LawSikho. Your seat in [program] is booked. I'd like 10 minutes
to go over the course and the easiest way to pay the balance. What time suits you this week?</p></div>
</section>

<section><h2>8. Team leader: checks, payment and loan files</h2>
<table><thead><tr><th>When</th><th>Look at (Team summary sheet)</th><th>Must be true</th></tr></thead><tbody>
<tr><td class="t">{esc(d1)} 12:00</td><td>"P, M or A not started"</td><td>0 for every caller</td></tr>
<tr><td class="t">{esc(d1)} 17:00</td><td>Attempts logged; collected so far</td><td>Every A and B attempted once</td></tr>
<tr><td class="t">{esc(d1)} 19:30</td><td>"Not started" for day-1 leads; Next step column</td><td>Every connected lead has a dated next step</td></tr>
<tr><td class="t">{esc(d2)} 13:00</td><td>New bookings</td><td>Every new booking dialled; counselling calls logged</td></tr>
<tr><td class="t">{esc(d3)} 19:30</td><td>"Not started"</td><td>0; unfinished leads go to the top of the next plan</td></tr>
</tbody></table>
<h3>Payment checks and likely-lost leads ({n['P'] + n['X']})</h3>
<table><thead><tr><th>Group</th><th>Caller</th><th>Lead</th><th>Situation</th><th>Next step</th></tr></thead><tbody>{leader_rows}</tbody></table>
<h3>Loan / EMI files for the daily loan-desk review ({n['L']}, oldest first)</h3>
<table><thead><tr><th>Caller</th><th>Lead</th><th>Days open</th><th>Situation</th></tr></thead><tbody>{loan_rows}</tbody></table></section>

<section><h2>9. Caller summary</h2>
<table><thead><tr><th>Caller</th>{''.join(f"<th>{t}</th>" for t in TIERS)}<th>Leads</th><th>Expected in 3 days</th><th>No dial 3+ days</th></tr></thead>
<tbody>{summary}<tr><td><b>Team</b></td>{''.join(f"<td><b>{n[t]}</b></td>" for t in TIERS)}<td><b>{len(rows)}</b></td><td><b>{exp3:.1f}</b></td>
<td><b>{sum(_flag_count(callers[c], 'No dial for') + _flag_count(callers[c], 'Never dialled') for c in order)}</b></td></tr></tbody></table>
<p class="note">P payment check · M return call · A ready to close · L loan file · B workable, recent · C workable, older · R long shot · X likely lost.
"Expected in 3 days" adds each lead's chance from past leads in the same position.</p></section>

<section><h2>10. Caller-wise lists</h2><p>One section per caller, leads in calling order: where each stands, what to say, and the next step with its day.</p></section>
{''.join(caller_section(c, callers[c], days) for c in order)}

<section><h2>Appendix – notes on the data</h2><ul>
<li>A lead belongs to the caller it settled with in the first 24 hours after booking (allocation often passes through a team leader).</li>
<li>Each lead's situation, blocker, next step and what to say come from reading its full LeadSquared history; 10 readings were
checked against the histories and all matched.</li>
<li>"Chance in 3 days" is the share of June–August leads in the same position that were collected within 3 days, with thin
positions merged; it ranks the list and is not a forecast for any single lead.</li>
<li>Community leads are handed over by the community team at the moment of allocation; the time between their payment and the
hand-over is not visible until payment data is connected.</li>
<li>Rupee figures use the bootcamp balance of about ₹55–60k given by the business owner; they are estimates until payment data comes in.</li>
</ul></section>
</body></html>"""


def find_chrome() -> str | None:
    for p in (os.environ.get("CHROME"), "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", shutil.which("chromium"),
              shutil.which("chromium-browser"), shutil.which("google-chrome")):
        if p and os.path.exists(p):
            return p
    return None


def render_pdf(html_path: str, pdf_path: str) -> bool:
    chrome = find_chrome()
    if not chrome:
        return False
    subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", "file://" + os.path.abspath(html_path)],
                   check=True, capture_output=True, timeout=180)
    return os.path.exists(pdf_path)


def main(audit_dir: str, contacts_path: str, out_dir: str, first_day: str) -> None:
    report = json.load(open(os.path.join(audit_dir, "report.json")))
    audit_rows = list(csv.DictReader(open(os.path.join(audit_dir, "open_leads.csv"), encoding="utf-8")))
    if any(not r.get("outlook") for r in audit_rows):
        sys.exit("Some open leads have no reading yet: finish the reading round of the collection audit first.")
    contacts = json.load(open(contacts_path))["leads"]
    first = date.fromisoformat(first_day)
    days = day_names(first)
    teams, gone = plan_rows(audit_rows, contacts, first)
    os.makedirs(out_dir, exist_ok=True)
    stamp = first.strftime("%d%b")
    for team, callers in sorted(teams.items()):
        if team not in TEAM_LEADERS:
            continue
        slug = "Elite" if team.startswith("Elite") else "Puja_Malik"
        xlsx = os.path.join(out_dir, f"{slug}_collection_plan_{stamp}.xlsx")
        write_workbook(xlsx, team, callers, days, report["data_as_of_ist"])
        page = os.path.join(out_dir, f"{slug}_collection_plan_{stamp}.html")
        open(page, "w", encoding="utf-8").write(build_html(team, callers, report, first, report["data_as_of_ist"], sum(gone.values())))
        pdf = page.replace(".html", ".pdf")
        made = render_pdf(page, pdf)
        rows = [r for c in callers.values() for r in c]
        print(f"{team}: {len(rows)} leads, {len(callers)} callers, {dict(Counter(r['tier'] for r in rows))} -> {xlsx}"
              f"{', ' + pdf if made else ' (no Chrome found: open the HTML and print to PDF)'}")
    left = [t for t in teams if t not in TEAM_LEADERS]
    if left:
        print(f"Not planned (outside the two teams): {', '.join(left)}")
    if gone:
        print(f"Left the sheets since the audit: {dict(gone)}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
