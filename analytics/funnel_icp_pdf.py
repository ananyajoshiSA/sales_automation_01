"""One-page PDF of the funnel ICP report for managers: the per-ICP table, General split by segment, a short
note on the best and worst ICPs and the funnels behind them, and the caller-list counts.

Every figure comes from the stage 1 result; the notes only compare those figures. Best and worst are
judged on lead → sale among ICPs with at least ``MIN_LEADS`` leads, so a tiny ICP can't top the list by
chance.

Written by ``python -m analytics.funnel_icp report ...`` as ``ICP_summary_<leadtype>.pdf``.
"""

from __future__ import annotations

import html
from collections import Counter

from analytics.daily_plan.pdf import render

MIN_LEADS = 100


def _pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f}%" if b else "–"


def _rs(x: float) -> str:
    return f"₹{x / 1e5:.1f}L" if x >= 1e5 else f"₹{x:,.0f}"


def notes(result: dict) -> list[str]:
    icp = {k: v for k, v in result["icp"].items() if v["leads"]}
    total = sum(v["leads"] for v in icp.values()) or 1
    sales = sum(v["sales"] for v in icp.values())
    big = {k: v for k, v in icp.items() if v["leads"] >= MIN_LEADS}
    out = [f"Campaign average: {sales} sales from {total:,} leads ({_pct(sales, total)})."]
    if big:
        names = sorted(big)  # ties go to more sales, then more leads, then alphabetical
        best = max(names, key=lambda k: (big[k]["sales"] / big[k]["leads"], big[k]["sales"], big[k]["leads"]))
        worst = min(names, key=lambda k: (big[k]["sales"] / big[k]["leads"], -big[k]["leads"]))
        out.append(f"<b>Best:</b> {html.escape(best)}, {big[best]['sales']} sales from {big[best]['leads']:,} leads "
                   f"({_pct(big[best]['sales'], big[best]['leads'])}).")
        out.append(f"<b>Worst:</b> {html.escape(worst)}, {big[worst]['sales']} sales from {big[worst]['leads']:,} "
                   f"leads ({_pct(big[worst]['sales'], big[worst]['leads'])}), "
                   f"{_pct(big[worst]['leads'], total)} of all leads.")
        for name in (best, worst):
            fs = sorted(((f, a) for f, a in result["funnels"].items() if f[2] == name and a["leads"]),
                        key=lambda x: (-x[1]["sales"], -x[1]["leads"]))[:2]
            if fs:
                out.append(f"{html.escape(name)} came mostly from: " + "; ".join(
                    f"{html.escape(f[0][:60])} ({a['leads']:,} leads, {a['sales']} sales)" for f, a in fs) + ".")
    unmatched = result["matched"].get("not in GrowthX", 0)
    if unmatched:
        out.append(f"{unmatched} of {result['sales_total']} sales did not match a GrowthX lead, so they have no ICP "
                   f"and are not in the table.")
    return out


def html_doc(result: dict, chk: list, title: str, fresh: str, callers: list[dict] | None = None) -> str:
    tp = result["timepay"]
    head = ["ICP", "Leads", "Share"] + (["Connected", "Positive", "Pos. % of conn."] if tp else []) + \
        ["Sales", "Lead → sale", "Money"]

    def row(name: str, a: dict, sub: bool = False) -> str:
        cells = [("&nbsp;&nbsp;↳ " if sub else "") + html.escape(name), f"{a['leads']:,}",
                 _pct(a["leads"], result["people"])]
        if tp:
            cells += [f"{a['connected']:,}", f"{a['positive']:,}", _pct(a["positive"], a["connected"])]
        cells += [str(a["sales"]), _pct(a["sales"], a["leads"]), _rs(a["paid"])]
        return f"<tr class='{'sub' if sub else ''}'>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    body = []
    for name, a in sorted(result["icp"].items(), key=lambda x: -(x[1]["sales"] / x[1]["leads"] if x[1]["leads"] else 0)):
        if not a["leads"]:
            continue
        body.append(row(name, a))
        if name == "General":
            body += [row(seg, s, True) for seg, s in sorted(result["segments"].items(), key=lambda x: -x[1]["leads"])
                     if s["leads"]]
    fails = sum(1 for c in chk if c[0] == "FAIL")
    warns = sum(1 for c in chk if c[0] == "WARN")
    check_line = (f"Checks: {len(chk) - fails - warns} passed, {warns} to review, {fails} failed"
                  + (" — <b>not ready to share</b>" if fails else ""))
    call = ""
    if callers:
        c = Counter(r["Priority"] for r in callers if r["tab"] == "Call list")
        enrolled = sum(1 for r in callers if r["tab"] == "Already enrolled")
        call = ("<p class='n'><b>Caller list:</b> " + " · ".join(f"{k}: {v:,}" for k, v in sorted(c.items()))
                + f" · Already enrolled: {enrolled:,}. Every lead is on it; see the Call list CSV.</p>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4 landscape; margin: 12mm; }}
body {{ font: 10.5px -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: #1a1a1a; }}
h1 {{ font-size: 17px; margin: 0 0 2px; }} .f {{ color: #555; margin: 0 0 8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0 10px; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 4px 6px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }} th {{ background: #eef3fa; }}
tr.sub td {{ color: #555; font-size: 9.5px; }} .n {{ margin: 3px 0; }} ul {{ margin: 4px 0 6px 16px; padding: 0; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="f">{html.escape(fresh)} · {result['people']:,} unique leads, each credited to their first funnel ·
Positive = Full Conversion, Attendance Confirmed, Confirmed–Discovery Incomplete or Discovery Successful ·
Sales from the enrolment sheet (Disbursed, One shot, Installments, Booking fees received) · {check_line}</p>
<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{''.join(body)}</table>
<ul>{''.join(f'<li>{n}</li>' for n in notes(result))}</ul>
{call}
</body></html>"""


def write_pdf(result: dict, chk: list, title: str, fresh: str, pdf_path: str,
              callers: list[dict] | None = None) -> str:
    return render(html_doc(result, chk, title, fresh, callers), pdf_path[:-4] + ".html", pdf_path)
