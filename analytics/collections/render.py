"""Write one collection report as a workbook (every row) and a short PDF (what to do first, then the numbers)."""

from __future__ import annotations

import glob
import html
import os
import subprocess

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

TITLES = {"community": "Community collections", "bootcamp": "Bootcamp collections"}
SUMMARY_COLS = [
    ("students", "Students"), ("collected", "Collected"), ("instalments", "On instalments"), ("pending", "Pending"),
    ("dropped", "Dropped"), ("course_value", "Course value (Rs)"), ("received", "Received (Rs)"),
    ("balance_open", "Still to collect (Rs)"), ("balance_dropped", "Lost to drops (Rs)"),
    ("recovered_%_amount", "Recovered % (amount)"), ("dropped_%_students", "Dropped % (students)"),
    ("subvention", "Loan subvention (Rs)"), ("discount", "Discount (Rs)"),
]
KEY_NAMES = {"month": "Month", "team": "Team (sheet)", "owner": "Collector (sheet)", "community_type": "Community type",
             "bootcamp_raw": "Bootcamp (sheet)", "start_date": "Start date", "bootcamp_tag": "LeadSquared tag"}
CALL_COLS = [("name", "Student"), ("phone", "Phone"), ("group", "Community / bootcamp"), ("month", "Month"),
             ("balance", "Balance (Rs)"), ("status", "Sheet status"), ("sheet_stage", "Last call status (sheet)"),
             ("collector", "Collector (sheet)"), ("lsq_owner", "Owner (LeadSquared)"), ("lsq_stage", "Stage (LeadSquared)"),
             ("expected_payment", "Expected payment"), ("next_step", "Next step"), ("flag", "Check")]
ROW_COLS = [("month", "Month"), ("name", "Student"), ("phone", "Phone"), ("email", "Email"), ("community_type", "Community type"),
            ("community_raw", "Community (sheet)"), ("bootcamp_raw", "Bootcamp (sheet)"), ("start_date", "Start date"),
            ("bootcamp_tag", "LeadSquared tag (paired)"), ("course", "Course paid for"), ("price", "Course price"),
            ("received", "Received"), ("subvention", "Subvention"), ("discount", "Discount"), ("balance", "Balance"),
            ("status", "Sheet status"), ("outcome", "Outcome"), ("sheet_stage", "Last call status (sheet)"),
            ("owner", "Collector (sheet)"), ("team", "Team (sheet)"), ("payment_mode", "Payment mode"),
            ("lsq_id", "LeadSquared lead ID"), ("matched_by", "Matched by"), ("match_note", "Why not matched"),
            ("lsq_owner", "Owner (LeadSquared)"), ("lsq_team", "Team (LeadSquared)"), ("lsq_stage", "Stage (LeadSquared)"),
            ("lsq_tag", "Bootcamp collections (LeadSquared)"), ("lsq_funnel", "Funnel name (LeadSquared)"),
            ("flag", "Check"), ("tab", "Sheet tab"), ("comment", "Comment (sheet)")]
HEAD_FILL = PatternFill("solid", fgColor="E8EFEC")


def _sheet(wb: Workbook, title: str, cols: list[tuple[str, str]], rows: list[dict], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    ws.append([c[1] for c in cols])
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True), HEAD_FILL
    for r in rows:
        ws.append([r.get(k, "") for k, _ in cols])
    for i, (k, label) in enumerate(cols, 1):
        width = max([len(label)] + [len(str(r.get(k, ""))) for r in rows[:200]])
        ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(10, width + 2), 50)
    ws.freeze_panes = "A2"
    return ws


def _group_cols(rows: list[dict]) -> list[tuple[str, str]]:
    keys = [k for k in (rows[0] if rows else {}) if k in KEY_NAMES]
    return [(k, KEY_NAMES[k]) for k in keys] + SUMMARY_COLS


def workbook(R: dict, path: str) -> str:
    wb = Workbook()
    ws = _sheet(wb, "Summary", [("k", "Measure"), ("v", "Value")],
                [{"k": label, "v": R["total"][k]} for k, label in SUMMARY_COLS], first=True)
    ws.append([])
    ws.append(["Sheet downloaded (IST)", R["sheet_read_ist"]])
    ws.append(["LeadSquared read (IST)", R["fetched_at_ist"]])
    ws.append(["Students matched to LeadSquared", f"{R['checks']['matched']:,} of {R['checks']['students']:,}"])
    _sheet(wb, "Who to call", CALL_COLS, R["call_list"])
    for key, title in (("by_month", "By month"), ("by_team", "By team"), ("by_collector", "By collector"),
                       ("by_group", "By community" if R["kind"] == "community" else "By bootcamp")):
        _sheet(wb, title, _group_cols(R[key]), R[key])
    _sheet(wb, "All students", ROW_COLS, R["rows"])
    _sheet(wb, "Not matched", [(k, k.replace("_", " ").title()) for k in
                               ("name", "phone", "email", "month", "match_note", "status", "balance", "owner")], R["unmatched"])
    _sheet(wb, "Sheet vs LeadSquared", [(k, k.replace("_", " ").title()) for k in
                                        ("name", "phone", "month", "status", "balance", "owner", "lsq_stage", "lsq_owner", "flag")],
           R["flagged"])
    _sheet(wb, "Missing from sheet", [(k, k.replace("_", " ").title()) for k in
                                      ("name", "tag", "lsq_stage", "lsq_owner", "funnel", "lsq_id")],
           R["checks"]["missing_from_sheet"])
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, float):
                    c.number_format = "#,##0"
                c.alignment = Alignment(vertical="top")
    wb.save(path)
    return path


e = lambda s: html.escape(str(s if s is not None else ""))


def rs(v) -> str:
    v = float(v or 0)
    if abs(v) >= 1e7:
        return f"Rs {v / 1e7:.2f} cr"
    if abs(v) >= 1e5:
        return f"Rs {v / 1e5:.1f} lakh"
    return f"Rs {v:,.0f}"


CSS = """
@page { size: A4; margin: 13mm 12mm 15mm 12mm; }
body { font-family: 'Lato','Helvetica Neue',Arial,sans-serif; color:#1d2b28; font-size:10.5px; line-height:1.42; }
h1,h2 { font-family: 'DejaVu Serif', Georgia, serif; color:#1F4E46; } h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:15px; margin:16px 0 6px; } .muted { color:#5b6b67; font-size:9.5px; }
.tiles { display:grid; grid-template-columns: repeat(3,1fr); gap:8px; margin:12px 0; }
.tile { border:1px solid #d6dcda; border-radius:6px; padding:9px 10px; }
.tile b { font-family: Georgia,serif; font-size:19px; color:#1F4E46; display:block; } .tile span { color:#555; font-size:9.5px; }
table { border-collapse:collapse; width:100%; margin:4px 0 8px; font-size:9px; } th { background:#E8EFEC; color:#1F4E46; text-align:left; }
th,td { border:1px solid #d6dcda; padding:3px 5px; vertical-align:top; } tr { page-break-inside: avoid; }
td.n, th.n { text-align:right; white-space:nowrap; } .ph { font-family: 'DejaVu Sans Mono', monospace; white-space:nowrap; }
.lead { background:#F3F7F5; border-left:4px solid #1F4E46; padding:8px 10px; margin:8px 0; font-size:11px; }
.flag { color:#9a3412; }
"""


def _table(rows: list[dict], cols: list[tuple[str, str]], money_keys=(), limit: int | None = None) -> str:
    H = ["<table><tr>" + "".join(f"<th{' class=n' if k in money_keys else ''}>{e(l)}</th>" for k, l in cols) + "</tr>"]
    for r in rows[:limit]:
        cells = []
        for k, _ in cols:
            v = r.get(k, "")
            if k in money_keys:
                cells.append(f"<td class=n>{e(rs(v) if 'balance' in k or 'value' in k or 'received' in k else v)}</td>")
            elif k == "phone":
                cells.append(f"<td class=ph>{e(v)}</td>")
            elif k == "flag" and v:
                cells.append(f"<td class=flag>{e(v)}</td>")
            else:
                cells.append(f"<td>{e(v)}</td>")
        H.append("<tr>" + "".join(cells) + "</tr>")
    H.append("</table>")
    return "".join(H)


GROUP_COLS = [("students", "Students"), ("collected", "Collected"), ("pending", "Pending"), ("instalments", "Instal."),
              ("dropped", "Dropped"), ("received", "Received"), ("balance_open", "Still to collect"),
              ("recovered_%_amount", "Recovered %")]
NUM = {"students", "collected", "pending", "instalments", "dropped", "received", "balance_open", "recovered_%_amount",
       "balance"}


def html_doc(R: dict, call_limit: int = 40) -> str:
    T, C = R["total"], R["checks"]
    kind = R["kind"]
    title = TITLES[kind]
    H = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"]
    H.append(f"<h1>{title}</h1><div class=muted>Collection sheet downloaded {e(R['sheet_read_ist'])} IST; LeadSquared read "
             f"{e(R['fetched_at_ist'])} IST. Amounts come from the sheet; LeadSquared stages and owners are shown beside them.</div>")
    top = R["call_list"][:10]
    H.append(f"<div class=lead><b>Do first:</b> {len(R['call_list']):,} students still owe {e(rs(T['balance_open']))}. "
             f"The 10 largest balances add up to {e(rs(sum(x['balance'] for x in top)))}; the list below starts with them. "
             + (f"{sum(C['flags'].values()):,} students have the sheet and LeadSquared disagreeing: check those before calling."
                if C["flags"] else "") + "</div>")
    H.append("<div class=tiles>" + "".join(f"<div class=tile><b>{v}</b><span>{l}</span></div>" for v, l in (
        (f"{T['students']:,}", "students in the pool"), (e(rs(T["course_value"])), "course value"),
        (e(rs(T["received"])), f"received ({T['recovered_%_amount'] or 0}% of value)"),
        (e(rs(T["balance_open"])), f"still to collect ({T['pending'] + T['instalments']:,} students)"),
        (e(rs(T["balance_dropped"])), f"lost to drops ({T['dropped']:,} students)"),
        (f"{C['matched_%'] or 0}%", "students matched to LeadSquared"))) + "</div>")
    H.append(f"<h2>Who to call first (largest balances)</h2>")
    H.append(_table(R["call_list"], [("name", "Student"), ("phone", "Phone"), ("group", "Community" if kind == "community" else "Bootcamp"),
                                     ("balance", "Balance"), ("sheet_stage", "Last status"), ("collector", "Collector"),
                                     ("next_step", "Next step"), ("flag", "Check")], {"balance"}, call_limit))
    if len(R["call_list"]) > call_limit:
        H.append(f"<div class=muted>The other {len(R['call_list']) - call_limit:,} are in the workbook, sheet 'Who to call'.</div>")
    H.append("<h2>By month</h2>" + _table(R["by_month"], [("month", "Month")] + GROUP_COLS, NUM))
    H.append("<h2>By team</h2>" + _table(R["by_team"], [("team", "Team")] + GROUP_COLS, NUM))
    H.append("<h2>By collector (top 25 by amount still to collect)</h2>"
             + _table(R["by_collector"], [("owner", "Collector"), ("team", "Team")] + GROUP_COLS, NUM, 25))
    if kind == "community":
        H.append("<h2>By community type</h2>" + _table(R["by_group"], [("community_type", "Community")] + GROUP_COLS, NUM))
    else:
        H.append("<h2>By bootcamp (top 30 by amount still to collect)</h2>"
                 + _table(R["by_group"], [("bootcamp_raw", "Bootcamp"), ("start_date", "Start")] + GROUP_COLS, NUM, 30))
    H.append("<h2>Data checks</h2><ul>")
    H.append(f"<li><b>Matching:</b> {C['matched']:,} of {C['students']:,} students found in LeadSquared "
             f"({', '.join(f'{v:,} by {k}' for k, v in C['matched_by'].items())}). "
             f"{C['students'] - C['matched']:,} not matched ({', '.join(f'{v:,} {k}' for k, v in C['not_matched_reasons'].items()) or 'none'}); "
             f"their amounts are still in every total.</li>")
    if kind == "community":
        H.append(f"<li><b>Community type:</b> taken from LeadSquared's Funnel name for {C['type_from'].get('LeadSquared Funnel name', 0):,} students, "
                 f"from the sheet's Community Name for {C['type_from'].get('sheet Community Name', 0):,}; {C['type_other']:,} are Other. "
                 f"For {C['type_differs']:,} the two name different communities (LeadSquared is used).</li>")
    else:
        H.append(f"<li><b>Bootcamps:</b> {C['bootcamps'] - C['bootcamps_not_paired']:,} of {C['bootcamps']:,} paired to a "
                 f"LeadSquared tag ({', '.join(f'{v:,} by {k}' for k, v in C['paired_by'].items())}); "
                 f"{C['bootcamps_not_paired']:,} not paired.</li>")
    flags = "; ".join(f"{v:,} {k.lower()}" for k, v in C["flags"].items()) or "none"
    H.append(f"<li><b>Sheet and LeadSquared disagree:</b> {flags}. Listed in the workbook, sheet 'Sheet vs LeadSquared'.</li>")
    H.append(f"<li><b>In LeadSquared, not in the sheet:</b> {len(C['missing_from_sheet']):,} tagged leads still being worked "
             f"(booking received, loan pending, follow-up) have no row in the sheet. Workbook sheet 'Missing from sheet'.</li>")
    sk = R["sheet"].get("skipped", {})
    H.append(f"<li><b>Sheet rows:</b> {R['sheet']['rows']:,} students used"
             + (f"; left out: {', '.join(f'{v:,} {k}' for k, v in sk.items())}" if sk else "") + ".</li></ul>")
    H.append("<div class=muted>Received = money that reached us after loan subvention. Still to collect = balance on students "
             "the sheet marks Pending or Instalments. Lost to drops = balance on students marked Drop. Collected, Pending, "
             "Instalments and Dropped come from the sheet's own status column.</div></body></html>")
    return "".join(H)


def chrome() -> str:
    for c in [os.environ.get("CHROME_BIN", "")] + sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"), reverse=True):
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError("no Chromium found: set CHROME_BIN")


def pdf(R: dict, html_path: str, pdf_path: str) -> str:
    open(html_path, "w", encoding="utf-8").write(html_doc(R))
    subprocess.run([chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(pdf_path)}", os.path.abspath(html_path)],
                   check=True, capture_output=True, timeout=300)
    return pdf_path
