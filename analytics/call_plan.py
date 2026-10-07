"""Build tomorrow's caller-wise call plan workbook.

Inputs (JSON):
  reviewed   - list of {owner, lead_id, likelihood High|Medium, why, next_action_48h, risk, course, ...}
  tiered     - list of {owner, lead_id, tier A-D, prob_3d, month_end_due, why, opening_line, next_action,
                        best_time, objection_to_prepare, course}
  candidates - lead timelines from analytics.lead_priority (phone, stage, last conversation, ...)
  first      - fresh leads with no real conversation yet (analytics.fresh_leads rows)
  stages     - {lead_id: current stage} refreshed just before building (drops leads enrolled/closed since)

    python -m analytics.call_plan plan.json out.xlsx
"""

from __future__ import annotations

import json
import sys

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

CLOSED = {"Course Enrolled", "Irrelevant lead", "Invalid", "Duplicate"}
REVIEWED_PROB = {"High": 35, "Medium": 15}  # calibration used for the earlier 48h review
TIER_ORDER = {"A": 0, "B": 1, "F": 2, "R": 3, "C": 4}
TIER_LABEL = {"A": "A · Close today", "B": "B · Hot follow-up", "F": "F · New lead, first contact", "R": "R · Revive closure-stage", "C": "C · Nurture"}
ATTEMPTS = {"A": 4, "B": 3, "F": 3, "R": 2, "C": 1}
SLOT = {"A": "10:00–11:30, retry 15:00 & 18:30", "B": "12:00–13:30, retry 16:00", "F": "14:15–15:00, retry 17:30",
        "R": "16:00–17:00 (WhatsApp first)", "C": "17:00–18:30 (WhatsApp first)"}
OUTCOMES = ["Enrolled / paid", "Payment link sent", "Seat blocked / token paid", "Callback fixed", "Spoke – thinking",
            "Not answered", "Switched off / failed", "Busy – call later", "Not interested", "Wrong number"]
PAYMENT = ["Paid", "Token paid", "Link sent", "EMI docs pending", "Not yet"]

FONT = "Arial"
HDR_FILL = PatternFill("solid", fgColor="1F4E46")
INPUT_FILL = PatternFill("solid", fgColor="FFF4CC")
TIER_FILL = {"A": "DCEFE6", "B": "E8F0FA", "F": "F3EAF7", "R": "FBEFE3", "C": "F2F2F2"}
THIN = Side(style="thin", color="D0D5D2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def build_rows(plan: dict) -> dict[str, list[dict]]:
    cand = {c["lead_id"]: c for c in plan["candidates"]}
    stages = plan.get("stages", {})
    rows: dict[str, dict] = {}

    for t in plan["tiered"]:
        if t["tier"] == "D":
            continue
        rows[t["lead_id"]] = {**t, "source_list": "tiered"}
    for r in plan["reviewed"]:  # the deeper 48h review wins over the broader tiering
        tier = "A" if r["likelihood"] == "High" else "B"
        prev = rows.get(r["lead_id"], {})
        rows[r["lead_id"]] = {
            **prev, "owner": r["owner"], "lead_id": r["lead_id"], "name": r.get("lsq_name") or r["name"],
            "tier": tier, "prob_3d": max(REVIEWED_PROB[r["likelihood"]], prev.get("prob_3d", 0)),
            "why": r["why"], "next_action": r["next_action_48h"], "course": r.get("course", ""),
            "objection_to_prepare": prev.get("objection_to_prepare") or r.get("risk", ""),
            "opening_line": prev.get("opening_line", ""), "best_time": prev.get("best_time", ""),
            "month_end_due": prev.get("month_end_due", False), "source_list": "reviewed",
        }
    for f in plan["first"]:
        if f["lead_id"] in rows:
            continue
        rows[f["lead_id"]] = {
            "owner": f["owner"], "lead_id": f["lead_id"], "name": f["name"], "tier": "F",
            "prob_3d": 5 if f["source"] != "Inbound Phone call" else 6, "month_end_due": False,
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
            "owner": v["owner"], "lead_id": v["lead_id"], "name": v["name"], "tier": "R", "prob_3d": 4,
            "month_end_due": False, "course": v.get("course", ""), "phone": v.get("phone"), "stage": v["stage"],
            "why": (f"Still at '{v['stage']}' but last activity was {v['days']} days ago "
                    f"({v.get('last_activity_name') or 'activity'} on {v['last_activity']}). Check if the deal is alive."),
            "opening_line": "Hi, this is <name> from LawSikho — we last spoke about the program a few weeks ago. "
                            "Have you had a chance to decide? The current batch/offer is closing soon.",
            "next_action": "WhatsApp a short recap + current offer first; call once. If not interested or no reply "
                           "after 2 attempts, move the stage out of the closure pipeline.",
            "best_time": "anytime", "objection_to_prepare": "", "last_conv": v["last_activity"], "source_list": "revive",
        }

    by_owner: dict[str, list[dict]] = {}
    for r in rows.values():
        c = cand.get(r["lead_id"], {})
        r.setdefault("phone", c.get("phone"))
        r["phone"] = r.get("phone") or c.get("phone")
        r["stage"] = stages.get(r["lead_id"]) or c.get("stage") or r.get("stage", "")
        r["last_conv"] = c.get("last_conversation_ist") or r.get("last_conv", "")
        r["missed_unreturned"] = c.get("inbound_missed_unreturned", 0)
        if r["stage"] in CLOSED:
            continue
        # a missed call from the lead that nobody returned jumps the queue within its tier
        r["sort"] = (TIER_ORDER[r["tier"]], -int(bool(r["missed_unreturned"])), -int(r.get("month_end_due") or 0),
                     -int(r["prob_3d"]))
        by_owner.setdefault(r["owner"], []).append(r)
    for o in by_owner:
        by_owner[o].sort(key=lambda r: r["sort"])
    return by_owner


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


def caller_sheet(wb, owner, rows):
    ws = wb.create_sheet(owner[:31])
    ws["A1"] = f"{owner} — call plan for Thu 8 Oct 2026"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = ("Work top to bottom. Yellow cells are for the caller to fill after every attempt. "
                "Tier A leads get up to 4 attempts, B 3, F 3, C 1 call plus WhatsApp.")
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


TARGETS = {"Shivangi Sahu": 0}  # team leader: monitors the team, no personal target


def summary_sheet(wb, owners, ranges):
    ws = wb.create_sheet("Team summary", 1)
    ws["A1"] = "Team Elite Calling — Thu 8 Oct targets and live progress"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = ("Updates automatically as callers fill their sheets. Target: 4 enrollments per caller tomorrow. "
                "The pipeline column is the 3-day expectation from the lead review; a negative gap means even the "
                "3-day pipeline is short of tomorrow's target.")
    ws["A2"].font = Font(name=FONT, italic=True, size=9, color="555555")
    heads = ["Caller", "Tier A", "Tier B", "New leads (F)", "Revive + nurture (R, C)", "Leads on sheet",
             "Pipeline: expected enrollments over 3 days", "Target tomorrow", "3-day pipeline minus target", "Attempts required", "Attempts logged",
             "Leads not started", "Tier A not started", "Enrolled so far"]
    _hdr(ws, 4, heads, [20, 8, 8, 10, 10, 10, 12, 8, 10, 11, 11, 11, 11, 11])
    for i, o in enumerate(owners):
        r = 5 + i
        sh = f"'{o[:31]}'"
        f, l = ranges[o]
        L = lambda name: get_column_letter(COL[name])  # noqa: E731
        tier = f"{sh}!${L('Tier')}${f}:${L('Tier')}${l}"
        ws.cell(row=r, column=1, value=o)
        for col, key in ((2, "A ·*"), (3, "B ·*"), (4, "F ·*")):
            ws.cell(row=r, column=col, value=f'=COUNTIF({tier},"{key}")')
        ws.cell(row=r, column=5, value=f'=COUNTIF({tier},"R ·*")+COUNTIF({tier},"C ·*")')
        ws.cell(row=r, column=6, value=f"=COUNTA({tier})")
        ws.cell(row=r, column=7, value=f"=SUM({sh}!${L('Est. chance (3 days)')}${f}:${L('Est. chance (3 days)')}${l})")
        ws.cell(row=r, column=8, value=TARGETS.get(o, 4))
        ws.cell(row=r, column=9, value=f"=G{r}-H{r}")
        ws.cell(row=r, column=10, value=f"=SUM({sh}!${L('Attempts required')}${f}:${L('Attempts required')}${l})")
        ws.cell(row=r, column=11, value=f"=SUM({sh}!${L('Attempts done')}${f}:${L('Attempts done')}${l})")
        st = f"{sh}!${L('Status')}${f}:${L('Status')}${l}"
        ws.cell(row=r, column=12, value=f'=COUNTIF({st},"Not started")')
        ws.cell(row=r, column=13, value=f'=COUNTIFS({tier},"A ·*",{st},"Not started")')
        ws.cell(row=r, column=14, value=f'=COUNTIF({st},"Enrolled")')
        for c in range(1, 15):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(name=FONT, size=10, bold=c == 1, color="0000FF" if c == 8 else "000000")
            cell.border = BORDER
        ws.cell(row=r, column=7).number_format = "0.0"
        ws.cell(row=r, column=9).number_format = "0.0"
    tr = 5 + len(owners)
    ws.cell(row=tr, column=1, value="Team")
    for c in range(2, 15):
        col = get_column_letter(c)
        ws.cell(row=tr, column=c, value=f"=SUM({col}5:{col}{tr - 1})")
        ws.cell(row=tr, column=c).number_format = "0.0" if c in (7, 9) else "0"
    for c in range(1, 15):
        ws.cell(row=tr, column=c).font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=tr, column=c).border = BORDER
    ws.cell(row=4, column=8).comment = Comment("Set by the business owner: 4 enrollments per caller. "
                                               "Team leader has no personal target.", "plan")
    ws.cell(row=4, column=7).comment = Comment(
        "Sum of each lead's estimated chance of enrolling within 3 days, from the lead review. "
        "Calibration: A 25–50%, B 8–25%, F ~5%, C 2–8%. An estimate, not a forecast guarantee.", "plan")
    ws.freeze_panes = "B5"


def guide_sheet(wb):
    ws = wb.active
    ws.title = "How to use"
    lines = [
        ("Team Elite Calling — daily call plan, Thu 8 Oct 2026", "title"),
        ("", None),
        ("What is in this file", "h"),
        ("One sheet per caller, leads in call order. Tier A = can close today, B = hot follow-up, "
         "F = new lead (1–7 Oct) still needing a first real conversation, R = older lead still parked at Follow Up For "
         "Closure / Counselled (check if alive, else move it out), C = nurture (WhatsApp + one call).", None),
        ("Leads come from: every open lead with a real conversation 22 Sep–7 Oct, month-end leads who said "
         "'next month / after salary / after Navratri', new leads from 1–7 Oct, and missed calls never returned.", None),
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
        ("14:15–15:00  Tier F: new leads from 1–7 Oct that never had a real conversation.", None),
        ("15:00–17:00  Second attempts on A and B; month-end promise leads; leads whose 'Best time' is afternoon.", None),
        ("16:00–17:00  Tier R: WhatsApp recap, then one call; re-stage dead deals.", None),
        ("17:00–18:30  Tier C (WhatsApp first, then one call); F second attempts.", None),
        ("18:30–19:30  Final attempts on every A not yet closed (working professionals pick up after office). Update LeadSquared.", None),
        ("", None),
        ("Checkpoints for the team leader", "h"),
        ("12:00  Every Tier A attempted once ('Tier A not started' = 0 on Team summary).", None),
        ("15:00  Every Tier B attempted once; at least 1 enrollment or payment link per caller.", None),
        ("18:00  Every Tier A attempted 3 times; every F attempted twice.", None),
        ("19:30  'Leads not started' = 0 for every caller; every connected call has a next step date/time.", None),
        ("", None),
        ("Rules", "h"),
        ("Never mark a lead 'Call Not Picking Up' before 6 attempts across 3 days. Never mark Not Interested without a real conversation.", None),
        ("Every connected call ends with a dated next step in LeadSquared: a callback time, a payment link or a seat block.", None),
        ("Answer fee questions on the call with the EMI split; don't push them to 'later'.", None),
        ("If 2+ attempts fail at the same second as other leads (CallFailure), switch line or WhatsApp — it's the dialer, not the lead.", None),
    ]
    for i, (text, kind) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(name=FONT, size={"title": 15, "h": 12}.get(kind, 10), bold=kind in ("title", "h"))
    ws.column_dimensions["A"].width = 130


def main(plan_path, out_path):
    plan = json.load(open(plan_path))
    by_owner = build_rows(plan)
    owners = [o for o in plan["owner_order"] if by_owner.get(o)]
    wb = Workbook()
    guide_sheet(wb)
    ranges = {}
    for o in owners:
        _, f, l = caller_sheet(wb, o, by_owner[o])
        ranges[o] = (f, l)
    summary_sheet(wb, owners, ranges)
    wb.save(out_path)
    json.dump({o: by_owner[o] for o in owners}, open(out_path.replace(".xlsx", ".json"), "w"), indent=1, default=str)
    for o in owners:
        rs = by_owner[o]
        tiers = {t: sum(1 for r in rs if r["tier"] == t) for t in "ABFRC"}
        print(f"{o:20} {len(rs):3} leads {tiers} expected {sum(r['prob_3d'] for r in rs) / 100:.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
