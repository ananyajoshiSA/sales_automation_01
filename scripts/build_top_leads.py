"""The team's top N leads by conversion potential, with each lead's current LeadSquared owner.

Ranks every lead in a call plan (the caller sheets and the backlog) by its estimated chance of enrolling
within 3 days, then Tier A before B, a promise now due, a missed booked callback, and the lead score.
Each row names the owner in LeadSquared now and who should call: the same person, or for a counsellor's
lead, the closer it was handed to (``call_plan.handover``). Writes a workbook and an HTML page to print as
PDF; both hold lead details, so write them under exports/. The meta JSON is the one
``scripts/build_plan_pdf.py`` reads; its ``counsellors`` list marks the discovery-only callers.

    python scripts/build_top_leads.py exports/plans/call_plan_X.json data/snapshot.json exports/plans/top_X
        [--backlog exports/plans/call_plan_X_backlog.json] [--meta meta.json] [--top 100]
"""

from __future__ import annotations

import argparse
import html
import json

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from scripts.build_focus_list import OUTCOMES, flags
from scripts.build_plan_pdf import CSS, esc, load_meta, name

TIER_ORDER = {t: i for i, t in enumerate("MPABFRC")}
THIN = Side(style="thin", color="D0D5D2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL = {"A": "DCEFE6", "B": "E8F0FA"}


def top_leads(plan: dict[str, list[dict]], owners_now: dict[str, str], n: int = 100, leader: str = "") -> list[dict]:
    """Every lead in the plan, best first, with the LeadSquared owner now ("owner_now") and who calls ("call_by")."""
    rows = [{**r, "call_by": o, "owner_now": owners_now.get(r["lead_id"]) or r.get("handover_from") or o}
            for o, rs in plan.items() if o != leader for r in rs]
    rows.sort(key=lambda r: (-r["prob_3d"], TIER_ORDER.get(r["tier"], 9), not r.get("month_end_due"),
                             not r.get("callback_missed"), -float(r.get("rank") or 0)))
    return rows[:n]


def write_xlsx(rows: list[dict], M: dict, path: str) -> None:
    counsellors = set(M.get("counsellors") or [])
    wb = Workbook()
    ws = wb.active
    ws.title = f"Top {len(rows)}"
    ws["A1"] = f"{M['team']} — top {len(rows)} leads by conversion potential, {M['date_label']}"
    ws["A1"].font = Font(name="Arial", bold=True, size=14)
    ws["A2"] = ("Highest chance first. 'Owner in LeadSquared' is who owns the lead now; 'Call by' is who should call it today "
                "(a counsellor's lead goes to a closer: change the owner in LeadSquared). Chances are estimates from reading "
                f"each lead's calls. Data: {M['window']}" + (f", pulled {M['pulled']}." if M["pulled"] else "."))
    ws["A2"].font = Font(name="Arial", italic=True, size=9, color="555555")
    cols = [("Rank", 6), ("Lead", 22), ("Phone", 15), ("Owner in LeadSquared", 20), ("Owner's role", 12), ("Call by", 18),
            ("Tier", 6), ("Chance (3 days)", 9), ("Flags", 20), ("Stage", 18), ("Last conversation", 15), ("Best time", 13),
            ("Why", 50), ("Discovery so far", 45), ("Opening line", 45), ("Ask on this call", 45), ("Prepare for", 28),
            ("Today's outcome", 18), ("Notes", 30), ("Lead ID", 38)]
    for i, (h, w) in enumerate(cols, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = w
    dv = DataValidation(type="list", formula1='"' + ",".join(OUTCOMES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    for n, r in enumerate(rows, 1):
        role = "Counsellor" if r["owner_now"] in counsellors else ("Team leader" if r["owner_now"] == M["leader"] else "Closer")
        vals = [n, r.get("name", ""), r.get("phone") or "", r["owner_now"], role, r["call_by"], r["tier"], r["prob_3d"] / 100,
                flags(r), r.get("stage", ""), r.get("last_conv", ""), r.get("best_time") or "anytime", r.get("why", ""),
                r.get("discovery", ""), r.get("opening_line", ""), r.get("next_action", ""), r.get("objection_to_prepare", ""),
                "", "", r["lead_id"]]
        for i, ((h, _), v) in enumerate(zip(cols, vals), 1):
            c = ws.cell(row=4 + n, column=i, value=v)
            c.font = Font(name="Arial", size=9, bold=h in ("Lead", "Call by", "Rank"))
            c.alignment = Alignment(wrap_text=h in ("Why", "Discovery so far", "Opening line", "Ask on this call",
                                                    "Prepare for", "Flags", "Lead"), vertical="top")
            c.fill = PatternFill("solid", fgColor="FFF4CC" if h in ("Today's outcome", "Notes") else FILL.get(r["tier"], "F2F2F2"))
            c.border = BORDER
        ws.cell(row=4 + n, column=8).number_format = "0%"
        dv.add(f"R{4 + n}")
        ws.row_dimensions[4 + n].height = 80
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(cols))}{4 + len(rows)}"

    s = wb.create_sheet("By caller")
    heads = ["Call by", "Leads in top list", "Tier A", "Owned by a counsellor now (change owner)", "Expected enrolments (3 days, estimate)"]
    for i, h in enumerate(heads, 1):
        c = s.cell(row=1, column=i, value=h)
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(wrap_text=True)
        s.column_dimensions[get_column_letter(i)].width = [20, 12, 9, 20, 16][i - 1]
    callers = sorted({r["call_by"] for r in rows}, key=lambda o: -sum(r["prob_3d"] for r in rows if r["call_by"] == o))
    for j, o in enumerate(callers, 2):
        rs = [r for r in rows if r["call_by"] == o]
        for i, v in enumerate([o, len(rs), sum(r["tier"] == "A" for r in rs), sum(r["owner_now"] != o for r in rs),
                               round(sum(r["prob_3d"] for r in rs) / 100, 1)], 1):
            s.cell(row=j, column=i, value=v).font = Font(name="Arial", size=10)
    wb.save(path)


def write_html(rows: list[dict], M: dict, path: str) -> None:
    callers = sorted({r["call_by"] for r in rows}, key=lambda o: -sum(r["prob_3d"] for r in rows if r["call_by"] == o))
    by = "".join(f"<tr><td><b>{html.escape(o)}</b></td><td>{sum(r['call_by'] == o for r in rows)}</td>"
                 f"<td>{sum(r['call_by'] == o and r['tier'] == 'A' for r in rows)}</td>"
                 f"<td>{sum(r['call_by'] == o and r['owner_now'] != o for r in rows)}</td>"
                 f"<td>{sum(r['prob_3d'] for r in rows if r['call_by'] == o) / 100:.1f}</td></tr>" for o in callers)
    body = "".join(
        f"""<tr class="tier-{r['tier']}"><td class="num">{n}</td>
<td class="lead"><b>{name(r)}</b><div class="sub">{r['tier']} · {r['prob_3d']}% · {esc(r.get('stage'))} · Best: {esc(r.get('best_time') or 'anytime', 20)}</div>
<div class="sub">{esc(flags(r))}</div></td>
<td><b>{html.escape(r['call_by'])}</b>{f'<div class="sub">Owner now: {html.escape(r["owner_now"])} – change it</div>' if r['owner_now'] != r['call_by'] else ''}</td>
<td>{esc(r.get('why'), 240)}</td><td>{esc(r.get('next_action'), 200)}</td></tr>""" for n, r in enumerate(rows, 1))
    moved = sum(r["owner_now"] != r["call_by"] for r in rows)
    open(path, "w").write(f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(M['team'])} top {len(rows)} – {html.escape(M['date_label'])}</title>
<style>{CSS} .leads td.lead {{ width: 24%; }} .leads td:nth-child(3) {{ width: 14%; }}</style></head><body>
<div class="band" style="background:#1f4e46;color:#fff;padding:18pt 22pt;border-radius:6pt"><div class="eyebrow">{html.escape(M['team'])} · for {html.escape(M['leader'] or 'the team leader')}</div>
<h1 style="color:#fff">Top {len(rows)} leads by conversion potential<br>{html.escape(M['date_label'])}</h1>
<p style="color:#dcebe6">Highest chance of enrolling first, with who owns each lead in LeadSquared now and who should call it today.</p></div>
<div class="kpis"><div><b>{sum(r['tier'] == 'A' for r in rows)}</b><span>Tier A: can close now</span></div>
<div><b>{moved}</b><span>owned by a counsellor now: change the owner to the closer named</span></div>
<div><b>≈{sum(r['prob_3d'] for r in rows) / 100:.0f}</b><span>enrolments over 3 days if every one is worked (estimate)</span></div></div>
<h3>By caller</h3><table><thead><tr><th>Call by</th><th>Leads</th><th>Tier A</th><th>Owner to change</th><th>Expected (3 days)</th></tr></thead><tbody>{by}</tbody></table>
<h3>The {len(rows)} leads, highest chance first</h3>
<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Call by</th><th>Why</th><th>Ask on this call</th></tr></thead><tbody>{body}</tbody></table>
<p class="note">Chances are estimates from reading each lead's call notes, not measured rates. Phone numbers, opening lines and the discovery so far are in the workbook. Data: {html.escape(M['window'])}{f", pulled {html.escape(M['pulled'])}" if M['pulled'] else ''}.</p>
</body></html>""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="rows JSON written next to the call-plan workbook")
    ap.add_argument("snapshot", help="the fetch_team_data snapshot the plan was built from (for the owner now)")
    ap.add_argument("out", help="output path without extension: writes .xlsx and .html")
    ap.add_argument("--backlog", help="the plan's _backlog.json, so leads that did not fit on a sheet are ranked too")
    ap.add_argument("--meta")
    ap.add_argument("--top", type=int, default=100)
    a = ap.parse_args()
    M = load_meta(a.meta)
    plan = json.load(open(a.plan))
    for o, rs in (json.load(open(a.backlog)) if a.backlog else {}).items():
        plan.setdefault(o, []).extend(rs)
    owners_now = {l["ProspectID"]: l.get("OwnerIdName") or "" for l in json.load(open(a.snapshot))["leads"]}
    rows = top_leads(plan, owners_now, a.top, M["leader"])
    write_xlsx(rows, M, a.out + ".xlsx")
    write_html(rows, M, a.out + ".html")
    print(f"top {len(rows)}: {sum(r['tier'] == 'A' for r in rows)} A, chance {rows[-1]['prob_3d'] if rows else 0}%+, "
          f"{sum(r['owner_now'] != r['call_by'] for r in rows)} to change owner -> {a.out}.xlsx, {a.out}.html")


if __name__ == "__main__":
    main()
