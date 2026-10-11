"""The team leader's focus list: the leads most likely to enrol in the next two days, from a call plan.

Takes the rows JSON that ``analytics.call_plan`` writes next to the workbook and keeps every Tier A lead,
every Tier B lead with a chance of FOCUS_MIN_B% or more, and every Tier B lead whose promised date has come
with PROMISE_MIN_B% or more. Writes a workbook (phone numbers, a row per lead in focus order, Monday and
Tuesday outcome columns, a per-caller tab) and an HTML page to print as PDF. Both hold lead details:
write them under exports/. The meta JSON is the one ``scripts/build_plan_pdf.py`` reads.

    python scripts/build_focus_list.py exports/plans/call_plan_X.json exports/plans/focus_X [--meta meta.json]
"""

from __future__ import annotations

import argparse
import html
import json

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from scripts.build_plan_pdf import CSS, esc, load_meta, name

FOCUS_MIN_B = 15
PROMISE_MIN_B = 12
OUTCOMES = ["Paid / enrolled", "Token or seat blocked", "Payment link sent", "Decision call fixed", "Spoke – thinking",
            "Not answered", "Not interested"]
FILL = {"A": "DCEFE6", "B": "E8F0FA"}
THIN = Side(style="thin", color="D0D5D2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def focus_rows(plan: dict[str, list[dict]], leader: str = "") -> list[dict]:
    """A first, then promises now due, then the highest chance; each row keeps its caller."""
    keep = [{**r, "owner": o} for o, rs in plan.items() if o != leader for r in rs
            if r["tier"] == "A" or (r["tier"] == "B" and (r["prob_3d"] >= FOCUS_MIN_B
                                                          or (r.get("month_end_due") and r["prob_3d"] >= PROMISE_MIN_B)))]
    return sorted(keep, key=lambda r: (r["tier"] != "A", not r.get("month_end_due"), -r["prob_3d"], r["owner"]))


def flags(r: dict) -> str:
    out = []
    if r.get("month_end_due"):
        out.append("Promise now due")
    if r.get("callback_missed"):
        out.append("Booked callback missed")
    if r.get("stage_fix"):
        out.append("Fix stage")
    return "; ".join(out)


def write_xlsx(rows: list[dict], M: dict, path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Focus list"
    ws["A1"] = f"{M['team']} — leads that can enrol {M['day']} or {M['next_day']}"
    ws["A1"].font = Font(name="Arial", bold=True, size=14)
    ws["A2"] = (f"{len(rows)} leads in call order: Tier A (can close now) first, then hot follow-ups whose promised date has "
                "come, then the highest chance. Chances are estimates from reading each lead's calls. Fill the yellow "
                f"cells after each call. Data: {M['window']}" + (f", pulled {M['pulled']}." if M["pulled"] else "."))
    ws["A2"].font = Font(name="Arial", italic=True, size=9, color="555555")
    cols = [("#", 5), ("Caller", 18), ("Team", 18), ("Lead", 22), ("Phone", 15), ("Tier", 6), ("Chance (3 days)", 9),
            ("Best time", 13), ("Flags", 18), ("Why", 50), ("Discovery so far", 50), ("Opening line", 45),
            ("Ask on this call", 45), ("Prepare for", 28), ("Stage", 18), ("Last conversation", 15),
            (f"{M['day']} outcome", 18), (f"{M['next_day']} outcome", 18), ("Notes", 30), ("Lead ID", 38)]
    for i, (h, w) in enumerate(cols, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = w
    dv = DataValidation(type="list", formula1='"' + ",".join(OUTCOMES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    inputs = {f"{M['day']} outcome", f"{M['next_day']} outcome", "Notes"}
    for n, r in enumerate(rows, 1):
        vals = [n, r["owner"], M["subteams"].get(r["owner"], ""), r.get("name", ""), r.get("phone") or "", r["tier"],
                r["prob_3d"] / 100, r.get("best_time") or "anytime", flags(r), r.get("why", ""), r.get("discovery", ""),
                r.get("opening_line", ""), r.get("next_action", ""), r.get("objection_to_prepare", ""), r.get("stage", ""),
                r.get("last_conv", ""), "", "", "", r["lead_id"]]
        for i, ((h, _), v) in enumerate(zip(cols, vals), 1):
            c = ws.cell(row=4 + n, column=i, value=v)
            c.font = Font(name="Arial", size=9, bold=h in ("Lead", "Tier"))
            c.alignment = Alignment(wrap_text=h in ("Why", "Discovery so far", "Opening line", "Ask on this call",
                                                    "Prepare for", "Flags", "Lead"), vertical="top")
            c.fill = PatternFill("solid", fgColor="FFF4CC" if h in inputs else FILL[r["tier"]])
            c.border = BORDER
        ws.cell(row=4 + n, column=7).number_format = "0%"
        for k in (17, 18):
            dv.add(f"{get_column_letter(k)}{4 + n}")
        ws.row_dimensions[4 + n].height = 90
    ws.freeze_panes = "E5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(cols))}{4 + len(rows)}"

    s = wb.create_sheet("By caller")
    heads = ["Caller", "Team", "Tier A", "Tier B", "Promises due", "Expected enrolments (3 days, estimate)"]
    for i, h in enumerate(heads, 1):
        c = s.cell(row=1, column=i, value=h)
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1F4E46")
        s.column_dimensions[get_column_letter(i)].width = [20, 26, 8, 8, 12, 16][i - 1]
    owners = sorted({r["owner"] for r in rows}, key=lambda o: -sum(r["prob_3d"] for r in rows if r["owner"] == o))
    for j, o in enumerate(owners, 2):
        rs = [r for r in rows if r["owner"] == o]
        for i, v in enumerate([o, M["subteams"].get(o, ""), sum(r["tier"] == "A" for r in rs), sum(r["tier"] == "B" for r in rs),
                               sum(bool(r.get("month_end_due")) for r in rs), round(sum(r["prob_3d"] for r in rs) / 100, 1)], 1):
            s.cell(row=j, column=i, value=v).font = Font(name="Arial", size=10)
    wb.save(path)


def write_html(rows: list[dict], M: dict, path: str) -> None:
    owners = sorted({r["owner"] for r in rows}, key=lambda o: -sum(r["prob_3d"] for r in rows if r["owner"] == o))
    by_caller = "".join(
        f"<tr><td><b>{html.escape(o)}</b></td><td>{html.escape(M['subteams'].get(o, ''))}</td>"
        f"<td>{sum(r['tier'] == 'A' for r in rows if r['owner'] == o)}</td><td>{sum(r['tier'] == 'B' for r in rows if r['owner'] == o)}</td>"
        f"<td>{sum(bool(r.get('month_end_due')) for r in rows if r['owner'] == o)}</td>"
        f"<td>{sum(r['prob_3d'] for r in rows if r['owner'] == o) / 100:.1f}</td></tr>" for o in owners)
    lead_rows = "".join(
        f"""<tr class="tier-{r['tier']}"><td class="num">{n}</td>
<td class="lead"><b>{name(r)}</b><div class="sub">{html.escape(r['owner'])} · {r['tier']} · {r['prob_3d']}% · Best: {esc(r.get('best_time') or 'anytime', 20)}</div>
<div class="sub">{esc(flags(r))}</div></td>
<td>{esc(r.get('why'), 260)}{('<div class="sub"><b>Discovery:</b> ' + esc(r.get('discovery'), 220) + '</div>') if r.get('discovery') else ''}</td>
<td>{esc(r.get('next_action'), 220)}{('<div class="sub"><b>Prepare:</b> ' + esc(r.get('objection_to_prepare'), 120) + '</div>') if r.get('objection_to_prepare') else ''}</td></tr>"""
        for n, r in enumerate(rows, 1))
    a = [r for r in rows if r["tier"] == "A"]
    due = [r for r in rows if r["tier"] == "B" and r.get("month_end_due")]
    expected = sum(r["prob_3d"] for r in rows) / 100
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(M['team'])} focus list – {html.escape(M['date_label'])}</title>
<style>{CSS} section {{ page-break-before: auto; }} .leads td.lead {{ width: 26%; }} .leads td:nth-child(3) {{ width: 40%; }}</style></head><body>
<div class="cover" style="height:auto; page-break-after:auto">
<div class="band"><div class="eyebrow">{html.escape(M['team'])} · for {html.escape(M['leader'] or 'the team leader')}</div>
<h1>Where {M['day']}'s and {M['next_day']}'s enrolments can come from</h1>
<p>{len(rows)} leads to watch personally, in the order to chase them. Everything else on the call sheets can wait until these are worked.</p></div>
<div class="kpis"><div><b>{len(a)}</b><span>Tier A: can close now (payment, EMI, documents or a decision under discussion)</span></div>
<div><b>{len(rows) - len(a)}</b><span>Hot follow-ups at {PROMISE_MIN_B}%+ ({len(due)} of them with a promised date that has come)</span></div>
<div><b>≈{expected:.0f}</b><span>enrolments over 3 days if every one is worked (estimate, not a forecast)</span></div></div>
</div>
<div class="callout"><p><b>How to use it.</b> By 10:00, check each caller has the payment link, EMI split and refund terms ready for their leads below. Ask for an update on every Tier A by 12:00 and again at 18:00. Join calls where a family member or a payment exception needs you. Anything not closed on {M['day']} stays on this list for {M['next_day']}; the outcome columns in the workbook track it.</p></div>
<h3>By caller</h3>
<table><thead><tr><th>Caller</th><th>Team</th><th>A</th><th>B</th><th>Promises due</th><th>Expected (3 days)</th></tr></thead><tbody>{by_caller}</tbody></table>
<section><h2>The leads, in focus order</h2>
<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Why, and the discovery so far</th><th>Ask on this call</th></tr></thead><tbody>{lead_rows}</tbody></table>
<p class="note">Chances are estimates from reading each lead's call notes, not measured rates. Phone numbers, opening lines and outcome columns are in the workbook. Data: {html.escape(M['window'])}{f", pulled {html.escape(M['pulled'])}" if M['pulled'] else ''}.</p></section>
</body></html>"""
    open(path, "w").write(doc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="rows JSON written next to the call-plan workbook")
    ap.add_argument("out", help="output path without extension: writes .xlsx and .html")
    ap.add_argument("--meta", help="the meta JSON that scripts/build_plan_pdf.py reads")
    a = ap.parse_args()
    M = load_meta(a.meta)
    rows = focus_rows(json.load(open(a.plan)), M["leader"])
    write_xlsx(rows, M, a.out + ".xlsx")
    write_html(rows, M, a.out + ".html")
    print(f"{len(rows)} focus leads ({sum(r['tier'] == 'A' for r in rows)} A), expected "
          f"{sum(r['prob_3d'] for r in rows) / 100:.1f} over 3 days -> {a.out}.xlsx, {a.out}.html")


if __name__ == "__main__":
    main()
