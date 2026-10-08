"""Build tomorrow's caller-wise call plan workbook.

Inputs (JSON):
  reviewed   - list of {owner, lead_id, likelihood High|Medium, why, next_action_48h, risk, course, ...}
  tiered     - list of {owner, lead_id, tier A-D, prob_3d, month_end_due, why, opening_line, next_action,
                        best_time, objection_to_prepare, course}
  candidates - lead timelines from analytics.lead_priority (phone, stage, last conversation, ...)
  first      - fresh leads with no real conversation yet (analytics.fresh_leads rows)
  stages     - {lead_id: current stage} refreshed just before building (drops leads enrolled/closed since)
  tier_b_cap - optional: most Tier B leads per caller; extras move to the least-loaded callers
  team, date_label, targets, default_target - optional sheet titles and per-caller targets
  carry      - optional: earlier call-plan workbooks; leads marked "Link sent", "Token paid" or "EMI docs
               pending" there and not paid come back as Tier P ("link sent, not paid")

Leads with a missed call from the lead that nobody returned become Tier M and go first.

    python -m analytics.call_plan plan.json out.xlsx [--carry yesterday.xlsx ...]
"""

from __future__ import annotations

import argparse
import json

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from analytics.definitions import CLOSED_STAGES

REVIEWED_PROB = {"High": 35, "Medium": 15}  # calibration used for the earlier 48h review
TIERS = "MPABFRC"
TIER_ORDER = {t: i for i, t in enumerate(TIERS)}
TIER_LABEL = {"M": "M · Return the lead's call", "P": "P · Link sent, not paid", "A": "A · Close today",
              "B": "B · Hot follow-up", "F": "F · New lead, first contact", "R": "R · Revive closure-stage", "C": "C · Nurture"}
ATTEMPTS = {"M": 3, "P": 3, "A": 4, "B": 3, "F": 3, "R": 2, "C": 1}
SLOT = {"M": "First thing; any new missed call within 15 min", "P": "10:00–11:30 with Tier A, retry 15:00 & 18:00",
        "A": "10:00–11:30, retry 15:00 & 18:30", "B": "12:00–13:30, retry 16:00", "F": "14:15–15:00, retry 17:30",
        "R": "16:00–17:00 (WhatsApp first)", "C": "17:00–18:30 (WhatsApp first)"}
OUTCOMES = ["Enrolled / paid", "Payment link sent", "Seat blocked / token paid", "Callback fixed", "Spoke – thinking",
            "Not answered", "Switched off / failed", "Busy – call later", "Not interested", "Wrong number"]
PAYMENT = ["Paid", "Token paid", "Link sent", "EMI docs pending", "Not yet"]
UNPAID_STARTED = {"Token paid", "Link sent", "EMI docs pending"}
MISSED_CALL_PROB = 6  # same chance the plan gives a lead who rang in (F tier, inbound)
DEFAULT_TARGET = 4    # enrolments per caller per day, set by the business owner

FONT = "Arial"
HDR_FILL = PatternFill("solid", fgColor="1F4E46")
INPUT_FILL = PatternFill("solid", fgColor="FFF4CC")
TIER_FILL = {"M": "FBE0DE", "P": "FFF0C2", "A": "DCEFE6", "B": "E8F0FA", "F": "F3EAF7", "R": "FBEFE3", "C": "F2F2F2"}
THIN = Side(style="thin", color="D0D5D2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def build_rows(plan: dict) -> dict[str, list[dict]]:
    stages = plan.get("stages", {})
    chance = plan.get("chances", {})  # measured per-tier chances (analytics.tier_outcomes), when there are enough
    rows: dict[str, dict] = {}

    for t in plan["tiered"]:
        if t["tier"] == "D":
            continue
        t = dict(t)
        ch = (t.get("changed") or "").strip()
        if ch and not ch.lower().startswith("unchanged"):
            t["why"] = f"Updated since the last review: {ch}. {t.get('why', '')}"
        rows[t["lead_id"]] = {**t, "source_list": "tiered"}
    for r in plan["reviewed"]:  # the deeper 48h review wins over the broader tiering
        tier = "A" if r["likelihood"] == "High" else "B"
        prev = rows.get(r["lead_id"], {})
        rows[r["lead_id"]] = {
            **prev, "owner": r["owner"], "lead_id": r["lead_id"], "name": r.get("lsq_name") or r["name"],
            "tier": tier, "prob_3d": max(REVIEWED_PROB[r["likelihood"]], prev.get("prob_3d", 0)),
            "why": r["why"], "next_action": r["next_action_48h"], "course": r.get("course", ""),
            "objection_to_prepare": prev.get("objection_to_prepare") or r.get("risk", ""),
            "opening_line": prev.get("opening_line") or (
                f"Hi {(r.get('lsq_name') or r['name']).split()[0].title()}, this is {r['owner'].split()[0]} from LawSikho — "
                f"following up on our last conversation about {r.get('course') or 'the program'}. "
                "Have you been able to decide? I can help you complete it right now."),
            "best_time": prev.get("best_time", ""),
            "month_end_due": prev.get("month_end_due", False), "source_list": "reviewed",
        }
    for f in plan["first"]:
        if f["lead_id"] in rows:
            continue
        rows[f["lead_id"]] = {
            "owner": f["owner"], "lead_id": f["lead_id"], "name": f["name"], "tier": "F",
            "prob_3d": chance.get("F") or (5 if f["source"] != "Inbound Phone call" else 6), "month_end_due": False,
            "course": f.get("course", ""),
            "why": (f"New {f['source']} lead created {f['created_ist']}; {f['dials']} dial(s), "
                    f"{f['answered_calls']} answered, no real conversation yet."),
            "opening_line": "Hi, this is <name> from LawSikho — you enquired about our course on our website. "
                            "Is this a good time for 2 minutes?",
            "next_action": "Discovery: background, goal, timeline; pitch the matching program; book a fixed "
                           "callback or send the fee sheet on WhatsApp.",
            "best_time": "anytime", "objection_to_prepare": "", "phone": f.get("phone"), "stage": f["stage"],
            "last_conv": "", "source_list": "fresh",
        }

    per_owner_revive: dict[str, int] = {}
    for v in sorted(plan.get("revive", []), key=lambda v: v["days"]):
        if v["lead_id"] in rows or per_owner_revive.get(v["owner"], 0) >= plan.get("revive_cap", 8):
            continue
        per_owner_revive[v["owner"]] = per_owner_revive.get(v["owner"], 0) + 1
        rows[v["lead_id"]] = {
            "owner": v["owner"], "lead_id": v["lead_id"], "name": v["name"], "tier": "R", "prob_3d": chance.get("R") or 4,
            "month_end_due": False, "course": v.get("course", ""), "phone": v.get("phone"), "stage": v["stage"],
            "why": (f"Still at '{v['stage']}' but last activity was {v['days']} days ago "
                    f"({v.get('last_activity_name') or 'activity'} on {v['last_activity']}). Check if the deal is alive."),
            "opening_line": "Hi, this is <name> from LawSikho — we last spoke about the program a few weeks ago. "
                            "Have you had a chance to decide? The current batch/offer is closing soon.",
            "next_action": "WhatsApp a short recap + current offer first; call once. If not interested or no reply "
                           "after 2 attempts, move the stage out of the closure pipeline.",
            "best_time": "anytime", "objection_to_prepare": "", "last_conv": v["last_activity"], "source_list": "revive",
        }

    for p in plan.get("carry", []):  # payment started on an earlier sheet and not completed
        prev = rows.get(p["lead_id"], {})
        if prev.get("tier") == "A":
            prev["why"] = f"{p['payment_status']} on the {p['sheet_date']} sheet, not paid yet. {prev.get('why', '')}"
            continue
        rows[p["lead_id"]] = {
            **prev, "owner": prev.get("owner") or p["owner"], "lead_id": p["lead_id"], "name": prev.get("name") or p["name"],
            "tier": "P", "prob_3d": max(prev.get("prob_3d", 0), p.get("prob_3d") or 0), "phone": prev.get("phone") or p.get("phone"),
            "course": prev.get("course") or p.get("course", ""), "month_end_due": prev.get("month_end_due", False),
            "why": f"{p['payment_status']} on the {p['sheet_date']} sheet ({p.get('next_step') or 'no pay date logged'}), not paid yet.",
            "opening_line": f"Hi, this is {(prev.get('owner') or p['owner']).split()[0]} from LawSikho. I'm calling about the payment "
                            "link I sent you. Shall we complete it together now? It takes two minutes.",
            "next_action": "Stay on the line while they pay. If it fails, find out why (card limit, EMI approval, family) "
                           "and fix it on the call, or agree an exact time today.",
            "best_time": prev.get("best_time", ""), "objection_to_prepare": "Payment failure, EMI approval, card limit",
            "source_list": "carry",
        }

    cand = {c["lead_id"]: c for c in plan["candidates"]}
    for c in plan["candidates"]:  # a lead who rang us and was never called back goes first
        if not c.get("inbound_missed_unreturned"):
            continue
        prev = rows.get(c["lead_id"])
        if prev and prev["tier"] in ("A", "P"):
            continue
        rows[c["lead_id"]] = {
            **(prev or {}), "owner": (prev or {}).get("owner") or c["owner"], "lead_id": c["lead_id"],
            "name": (prev or {}).get("name") or c.get("name", ""), "tier": "M",
            "prob_3d": max((prev or {}).get("prob_3d", 0), chance.get("M") or MISSED_CALL_PROB), "course": (prev or {}).get("course") or c.get("course", ""),
            "month_end_due": (prev or {}).get("month_end_due", False),
            "why": f"Called us {c['inbound_missed_unreturned']} time(s) and nobody called back. "
                   + ((prev or {}).get("why") or ""),
            "opening_line": "Hi, this is <name> from LawSikho. You tried to reach us, sorry we missed you. How can I help?",
            "next_action": (prev or {}).get("next_action") or "Find out why they called; if it's the fee or the batch, answer "
                           "on the call and send the payment link.",
            "best_time": (prev or {}).get("best_time", ""), "objection_to_prepare": (prev or {}).get("objection_to_prepare", ""),
            "source_list": "missed_call",
        }

    by_owner: dict[str, list[dict]] = {}
    for r in rows.values():
        c = cand.get(r["lead_id"], {})
        r.setdefault("phone", c.get("phone"))
        r["phone"] = r.get("phone") or c.get("phone")
        r["stage"] = stages.get(r["lead_id"]) or c.get("stage") or r.get("stage", "")
        r["last_conv"] = c.get("last_conversation_ist") or r.get("last_conv", "")
        r["missed_unreturned"] = c.get("inbound_missed_unreturned", 0)
        if r["stage"] in CLOSED_STAGES:
            continue
        if not (r.get("opening_line") or "").strip():
            r["opening_line"] = (f"Hi, this is {r['owner'].split()[0]} from LawSikho — we spoke recently about "
                                 f"{r.get('course') or 'our programs'}. Is now a good time for two minutes?")
        # a missed call from the lead that nobody returned jumps the queue within its tier
        r["sort"] = (TIER_ORDER[r["tier"]], -int(bool(r["missed_unreturned"])), -int(r.get("month_end_due") or 0),
                     -int(r["prob_3d"]))
        by_owner.setdefault(r["owner"], []).append(r)
    for o in by_owner:
        by_owner[o].sort(key=lambda r: r["sort"])
    if plan.get("tier_b_cap"):
        targets, default = plan.get("targets", {}), plan.get("default_target", DEFAULT_TARGET)
        rebalance(by_owner, [o for o in plan.get("owner_order", by_owner) if targets.get(o, default)], plan["tier_b_cap"])
    return by_owner


def rebalance(by_owner: dict[str, list[dict]], callers: list[str], cap: int) -> list[tuple[str, str, str]]:
    """Cap Tier B per caller so hot follow-ups get worked; move the lowest-ranked extras to the callers with
    the fewest M/P/A/B leads. Tier M, P and A leads stay with their owner, so no warm relationship moves.
    The move is on the sheet only: change the owner in LeadSquared by hand. Returns (lead, from, to)."""
    load = lambda o: sum(1 for r in by_owner.get(o, []) if r["tier"] in "MPAB")  # noqa: E731
    moves = []
    for o in sorted(callers, key=load, reverse=True):
        bs = [r for r in by_owner.get(o, []) if r["tier"] == "B"]
        for r in sorted(bs, key=lambda r: r["sort"], reverse=True)[: max(len(bs) - cap, 0)]:
            to = min((c for c in callers if c != o and sum(1 for x in by_owner.get(c, []) if x["tier"] == "B") < cap),
                     key=load, default=None)
            if not to:
                break
            by_owner[o].remove(r)
            r["why"] = f"Moved from {o} (over {cap} Tier B leads); change the owner in LeadSquared. {r.get('why', '')}"
            r["owner"] = to
            by_owner.setdefault(to, []).append(r)
            moves.append((r["lead_id"], o, to))
    for o in by_owner:
        by_owner[o].sort(key=lambda r: r["sort"])
    return moves


def read_carry(paths: list[str]) -> list[dict]:
    """Leads whose payment was started on an earlier sheet (link sent, token, EMI docs) and not completed."""
    out: dict[str, dict] = {}
    for path in paths:
        wb = load_workbook(path, read_only=True)
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(min_row=4, values_only=True))
            if not rows or "Lead ID" not in rows[0]:
                continue
            col = {h: i for i, h in enumerate(rows[0]) if h}
            title = str(ws["A1"].value or "")
            sheet_date = title.split("call plan for ", 1)[-1] if "call plan for " in title else path
            for v in rows[1:]:
                g = lambda h: v[col[h]] if h in col and col[h] < len(v) else None  # noqa: E731
                status = g("Payment status")
                outcomes = {g(f"Attempt {k} outcome") for k in range(1, 5)}
                if not g("Lead ID") or status not in UNPAID_STARTED or "Enrolled / paid" in outcomes:
                    continue
                out[g("Lead ID")] = {  # a later workbook overrides an earlier one
                    "lead_id": g("Lead ID"), "owner": title.split(" — ")[0] or ws.title, "name": g("Lead") or "",
                    "phone": g("Phone"), "course": g("Course") or "", "payment_status": status,
                    "next_step": g("Next step date/time"), "sheet_date": sheet_date,
                    "prob_3d": round(100 * float(g("Est. chance (3 days)") or 0)),
                }
    return list(out.values())


CALLER_COLS = [
    ("#", 5), ("Tier", 22), ("When to call", 24), ("Lead", 24), ("Phone", 15), ("Course", 28),
    ("Est. chance (3 days)", 11), ("Month-end promise due", 11), ("Missed call not returned", 11),
    ("Why this lead", 50), ("Opening line", 50), ("What to send / ask", 50), ("Prepare for", 30),
    ("Best time (past pick-ups)", 14), ("Last conversation", 16), ("Stage", 18), ("Attempts required", 10),
    ("Attempt 1 time", 11), ("Attempt 1 outcome", 20), ("Attempt 2 time", 11), ("Attempt 2 outcome", 20),
    ("Attempt 3 time", 11), ("Attempt 3 outcome", 20), ("Attempt 4 time", 11), ("Attempt 4 outcome", 20),
    ("WhatsApp sent (Y/N)", 10), ("Next step date/time", 16), ("Payment status", 16), ("Attempts done", 10),
    ("Status", 14), ("Notes", 30), ("Lead ID", 38),
]
COL = {name: i + 1 for i, (name, _) in enumerate(CALLER_COLS)}
INPUT_COLS = [n for n, _ in CALLER_COLS if n.startswith("Attempt ") and "required" not in n] + [
    "WhatsApp sent (Y/N)", "Next step date/time", "Payment status", "Notes"]


def _hdr(ws, row, values, widths=None):
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        c.fill = HDR_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = BORDER
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]


def caller_sheet(wb, owner, rows, day="tomorrow"):
    ws = wb.create_sheet(owner[:31])
    ws["A1"] = f"{owner} — call plan for {day}"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = ("Work top to bottom. Yellow cells are for the caller to fill after every attempt. "
                "M (return the lead's call) and P (link sent, not paid) go first. Tier A leads get up to 4 attempts, "
                "M, P, B and F 3, C 1 call plus WhatsApp.")
    ws["A2"].font = Font(name=FONT, italic=True, size=9, color="555555")
    n = len(rows)
    first, last = 5, 4 + n
    ws["A3"] = "Attempts logged"
    ws["C3"] = f"=SUM({get_column_letter(COL['Attempts done'])}{first}:{get_column_letter(COL['Attempts done'])}{last})"
    ws["D3"] = "Enrolled / paid"
    ws["E3"] = f'=COUNTIF({get_column_letter(COL["Status"])}{first}:{get_column_letter(COL["Status"])}{last},"Enrolled")'
    ws["F3"] = "Expected enrollments (sum of chances)"
    ws["G3"] = f"=SUM({get_column_letter(COL['Est. chance (3 days)'])}{first}:{get_column_letter(COL['Est. chance (3 days)'])}{last})"
    ws["G3"].number_format = "0.0"
    for ref in ("A3", "D3", "F3"):
        ws[ref].font = Font(name=FONT, bold=True, size=10)
    for ref in ("C3", "E3", "G3"):
        ws[ref].font = Font(name=FONT, bold=True, size=11)
    _hdr(ws, 4, [c for c, _ in CALLER_COLS], [w for _, w in CALLER_COLS])
    ws.row_dimensions[4].height = 42

    dv_out = DataValidation(type="list", formula1='"' + ",".join(OUTCOMES) + '"', allow_blank=True)
    dv_pay = DataValidation(type="list", formula1='"' + ",".join(PAYMENT) + '"', allow_blank=True)
    dv_yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    for dv in (dv_out, dv_pay, dv_yn):
        ws.add_data_validation(dv)

    L = lambda name: get_column_letter(COL[name])  # noqa: E731
    for i, r in enumerate(rows):
        rr = first + i
        vals = {
            "#": i + 1, "Tier": TIER_LABEL[r["tier"]], "When to call": SLOT[r["tier"]], "Lead": r["name"],
            "Phone": r.get("phone") or "", "Course": r.get("course", ""),
            "Est. chance (3 days)": round(r["prob_3d"] / 100, 2),
            "Month-end promise due": "Yes" if r.get("month_end_due") else "",
            "Missed call not returned": "Yes" if r.get("missed_unreturned") else "",
            "Why this lead": r.get("why", ""), "Opening line": r.get("opening_line", ""),
            "What to send / ask": r.get("next_action", ""), "Prepare for": r.get("objection_to_prepare", ""),
            "Best time (past pick-ups)": r.get("best_time", ""), "Last conversation": r.get("last_conv", ""),
            "Stage": r.get("stage", ""), "Attempts required": ATTEMPTS[r["tier"]], "Lead ID": r["lead_id"],
        }
        for name, v in vals.items():
            c = ws.cell(row=rr, column=COL[name], value=v)
            c.font = Font(name=FONT, size=9, bold=name in ("Lead", "Tier"))
            c.alignment = Alignment(wrap_text=name in ("Why this lead", "Opening line", "What to send / ask",
                                                       "Prepare for", "Course", "Lead"), vertical="top")
            c.fill = PatternFill("solid", fgColor=TIER_FILL[r["tier"]])
            c.border = BORDER
        ws.cell(row=rr, column=COL["Est. chance (3 days)"]).number_format = "0%"
        times = ",".join(f"{L(f'Attempt {k} time')}{rr}" for k in range(1, 5))
        ws.cell(row=rr, column=COL["Attempts done"], value=f"=COUNTA({times})")
        outs = [f"{L(f'Attempt {k} outcome')}{rr}" for k in range(1, 5)]
        enrolled = "+".join(f'({o}="Enrolled / paid")' for o in outs)
        ws.cell(row=rr, column=COL["Status"], value=(
            f'=IF({enrolled}+({L("Payment status")}{rr}="Paid")>0,"Enrolled",'
            f'IF({L("Attempts done")}{rr}>={L("Attempts required")}{rr},"Done",'
            f'IF({L("Attempts done")}{rr}=0,"Not started","In progress")))'))
        for name in ("Attempts done", "Status"):
            c = ws.cell(row=rr, column=COL[name])
            c.font = Font(name=FONT, size=9, bold=True)
            c.border = BORDER
            c.alignment = Alignment(vertical="top")
        for name in INPUT_COLS:
            c = ws.cell(row=rr, column=COL[name])
            c.fill = INPUT_FILL
            c.border = BORDER
            c.font = Font(name=FONT, size=9)
        for k in range(1, 5):
            dv_out.add(f"{L(f'Attempt {k} outcome')}{rr}")
        dv_pay.add(f"{L('Payment status')}{rr}")
        dv_yn.add(f"{L('WhatsApp sent (Y/N)')}{rr}")
        ws.row_dimensions[rr].height = 75

    status_rng = f"{L('Status')}{first}:{L('Status')}{last}"
    for text, color in (("Enrolled", "B7E1C1"), ("Not started", "F8D7D3"), ("Done", "E2E2E2")):
        ws.conditional_formatting.add(status_rng, FormulaRule(
            formula=[f'{L("Status")}{first}="{text}"'], fill=PatternFill("solid", fgColor=color)))
    ws.freeze_panes = ws.cell(row=first, column=COL["Phone"])
    ws.auto_filter.ref = f"A4:{get_column_letter(len(CALLER_COLS))}{last}"
    return ws, first, last


def summary_sheet(wb, owners, ranges, team="Team", day="tomorrow", targets=None, default_target=DEFAULT_TARGET):
    """``targets`` overrides the per-caller target, e.g. 0 for a team leader who only monitors."""
    targets = targets or {}
    ws = wb.create_sheet("Team summary", 1)
    ws["A1"] = f"{team} — {day} targets and live progress"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = (f"Updates automatically as callers fill their sheets. Target: {default_target} enrollments per caller. "
                "The pipeline column is the 3-day expectation from the lead review; a negative gap means even the "
                "3-day pipeline is short of tomorrow's target.")
    ws["A2"].font = Font(name=FONT, italic=True, size=9, color="555555")
    heads = ["Caller", "Return calls (M)", "Unpaid links (P)", "Tier A", "Tier B", "New leads (F)", "Revive + nurture (R, C)",
             "Leads on sheet", "Pipeline: expected enrollments over 3 days", "Target tomorrow", "3-day pipeline minus target",
             "Attempts required", "Attempts logged", "Leads not started", "M, P or A not started", "Enrolled so far"]
    C = {h: i + 1 for i, h in enumerate(heads)}
    X = lambda h: get_column_letter(C[h])  # noqa: E731
    _hdr(ws, 4, heads, [20, 9, 9, 8, 8, 10, 10, 10, 12, 8, 10, 11, 11, 11, 11, 11])
    L = lambda name: get_column_letter(COL[name])  # noqa: E731
    for i, o in enumerate(owners):
        r = 5 + i
        sh = f"'{o[:31]}'"
        f, l = ranges[o]
        tier = f"{sh}!${L('Tier')}${f}:${L('Tier')}${l}"
        st = f"{sh}!${L('Status')}${f}:${L('Status')}${l}"
        vals = {
            "Caller": o, "Leads on sheet": f"=COUNTA({tier})",
            "Revive + nurture (R, C)": f'=COUNTIF({tier},"R ·*")+COUNTIF({tier},"C ·*")',
            "Pipeline: expected enrollments over 3 days": f"=SUM({sh}!${L('Est. chance (3 days)')}${f}:${L('Est. chance (3 days)')}${l})",
            "Target tomorrow": targets.get(o, default_target),
            "3-day pipeline minus target": f"={X('Pipeline: expected enrollments over 3 days')}{r}-{X('Target tomorrow')}{r}",
            "Attempts required": f"=SUM({sh}!${L('Attempts required')}${f}:${L('Attempts required')}${l})",
            "Attempts logged": f"=SUM({sh}!${L('Attempts done')}${f}:${L('Attempts done')}${l})",
            "Leads not started": f'=COUNTIF({st},"Not started")',
            "M, P or A not started": "=" + "+".join(f'COUNTIFS({tier},"{k} ·*",{st},"Not started")' for k in "MPA"),
            "Enrolled so far": f'=COUNTIF({st},"Enrolled")',
        }
        for h, k in (("Return calls (M)", "M"), ("Unpaid links (P)", "P"), ("Tier A", "A"), ("Tier B", "B"), ("New leads (F)", "F")):
            vals[h] = f'=COUNTIF({tier},"{k} ·*")'
        for h, v in vals.items():
            cell = ws.cell(row=r, column=C[h], value=v)
            cell.font = Font(name=FONT, size=10, bold=h == "Caller", color="0000FF" if h == "Target tomorrow" else "000000")
            cell.border = BORDER
        for h in ("Pipeline: expected enrollments over 3 days", "3-day pipeline minus target"):
            ws.cell(row=r, column=C[h]).number_format = "0.0"
    tr = 5 + len(owners)
    ws.cell(row=tr, column=1, value="Team")
    decimals = {C["Pipeline: expected enrollments over 3 days"], C["3-day pipeline minus target"]}
    for c in range(2, len(heads) + 1):
        col = get_column_letter(c)
        ws.cell(row=tr, column=c, value=f"=SUM({col}5:{col}{tr - 1})")
        ws.cell(row=tr, column=c).number_format = "0.0" if c in decimals else "0"
    for c in range(1, len(heads) + 1):
        ws.cell(row=tr, column=c).font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=tr, column=c).border = BORDER
    ws.cell(row=4, column=C["Target tomorrow"]).comment = Comment(
        f"Set by the business owner: {default_target} enrollments per caller. A team leader has no personal target.", "plan")
    ws.cell(row=4, column=C["Pipeline: expected enrollments over 3 days"]).comment = Comment(
        "Sum of each lead's estimated chance of enrolling within 3 days, from the lead review. "
        "Calibration: A 25–50%, B 8–25%, F ~5%, C 2–8%. An estimate, not a forecast guarantee.", "plan")
    ws.freeze_panes = "B5"


def guide_sheet(wb, team="Team", day="tomorrow"):
    ws = wb.active
    ws.title = "How to use"
    lines = [
        (f"{team} — daily call plan, {day}", "title"),
        ("", None),
        ("What is in this file", "h"),
        ("One sheet per caller, leads in call order. M = return the lead's missed call, P = payment link sent and not "
         "paid, A = can close today, B = hot follow-up, F = new lead still needing a first real conversation, "
         "R = open-stage lead gone quiet (check if alive, else move it out), C = nurture (WhatsApp + one call).", None),
        ("Leads come from: every open lead with a real conversation in the last 15 days, unpaid links from earlier "
         "sheets, new leads from the last 7 days, open-stage leads gone quiet, and missed calls never returned.", None),
        ("'Est. chance' is the estimated chance of enrollment within 3 days from reading each lead's call "
         "summaries and LeadSquared history. Zip intent was cross-checked, not trusted on its own.", None),
        ("", None),
        ("What the caller fills (yellow cells)", "h"),
        ("After every dial: the time (e.g. 10:42) and the outcome from the dropdown. Then WhatsApp Y/N, "
         "the next step date/time agreed, and payment status. Attempts done and Status fill in by themselves.", None),
        ("Example row: Attempt 1 time 10:42 · Attempt 1 outcome 'Not answered' · Attempt 2 time 12:15 · "
         "Attempt 2 outcome 'Payment link sent' · WhatsApp Y · Next step 'Thu 18:00 confirm payment' · "
         "Payment status 'Link sent'.", None),
        ("", None),
        ("The day", "h"),
        ("09:45–10:00  Huddle: each caller reads out their Tier A leads and the exact ask for each.", None),
        ("10:00–11:30  Tier A first attempts (payment links ready before dialling). Send WhatsApp right after any unanswered A.", None),
        ("11:30–12:00  Overnight new leads and every missed call from a lead (callback within 15 min all day).", None),
        ("12:00–13:30  Tier B first attempts.", None),
        ("13:30–14:15  Lunch (stagger so inbound is always covered).", None),
        ("14:15–15:00  Tier F: new leads that never had a real conversation.", None),
        ("15:00–17:00  Second attempts on A and B; month-end promise leads; leads whose 'Best time' is afternoon.", None),
        ("16:00–17:00  Tier R: WhatsApp recap, then one call; re-stage dead deals.", None),
        ("17:00–18:30  Tier C (WhatsApp first, then one call); F second attempts.", None),
        ("18:30–19:30  Final attempts on every A not yet closed (working professionals pick up after office). Update LeadSquared.", None),
        ("", None),
        ("Checkpoints for the team leader", "h"),
        ("12:00  Every M, P and A lead attempted once ('M, P or A not started' = 0 on Team summary).", None),
        ("15:00  Every Tier B attempted once; at least 1 enrollment or payment link per caller.", None),
        ("18:00  Every Tier A attempted 3 times; every F attempted twice.", None),
        ("19:30  'Leads not started' = 0 for every caller; every connected call has a next step date/time.", None),
        ("", None),
        ("Rules", "h"),
        ("Never mark a lead 'Call Not Picking Up' before 6 attempts across 3 days. Never mark Not Interested without a real conversation.", None),
        ("Close every real conversation (2 min+) with a payment step: say the exact amount, offer the EMI split, send the "
         "payment link on WhatsApp during the call, and agree a date and time to pay (docs/call_playbook.md).", None),
        ("Every connected call ends with a dated next step in LeadSquared: a callback time, a payment link or a seat block.", None),
        ("Answer fee questions on the call with the EMI split; don't push them to 'later'.", None),
        ("If 2+ attempts fail at the same second as other leads (CallFailure), switch line or WhatsApp — it's the dialer, not the lead.", None),
    ]
    for i, (text, kind) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(name=FONT, size={"title": 15, "h": 12}.get(kind, 10), bold=kind in ("title", "h"))
    ws.column_dimensions["A"].width = 130


def write_workbook(plan: dict, out: str) -> dict[str, list[dict]]:
    """Build the workbook (and a JSON copy of the rows next to it); returns the rows per caller."""
    by_owner = build_rows(plan)
    owners = [o for o in plan["owner_order"] if by_owner.get(o)]
    wb = Workbook()
    team, day = plan.get("team", "Team"), plan.get("date_label", "tomorrow")
    guide_sheet(wb, team, day)
    ranges = {}
    for o in owners:
        _, f, l = caller_sheet(wb, o, by_owner[o], day)
        ranges[o] = (f, l)
    summary_sheet(wb, owners, ranges, team, day, plan.get("targets"), plan.get("default_target", DEFAULT_TARGET))
    wb.save(out)
    by_owner = {o: by_owner[o] for o in owners}
    json.dump(by_owner, open(out.replace(".xlsx", ".json"), "w"), indent=1, default=str)
    return by_owner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--carry", nargs="*", default=[], help="earlier call-plan workbooks to carry unpaid payment links from")
    a = ap.parse_args()
    plan = json.load(open(a.plan))
    plan["carry"] = plan.get("carry", []) + read_carry(a.carry)
    by_owner = write_workbook(plan, a.out)
    for o, rs in by_owner.items():
        tiers = {t: sum(1 for r in rs if r["tier"] == t) for t in TIERS}
        print(f"{o:20} {len(rs):3} leads {tiers} expected {sum(r['prob_3d'] for r in rs) / 100:.1f}")


if __name__ == "__main__":
    main()
