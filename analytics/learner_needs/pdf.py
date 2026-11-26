"""Trainer PDF for one bootcamp: who is coming, what each group needs, what to answer on which day.

Numbers come from ``summary.json`` (made by ``aggregate``); words come from ``findings.json``, which
Claude writes after reading every reader line. A findings item that names a ``theme`` gets its count
from the summary, so no count is typed by hand.
"""

from __future__ import annotations

import html
import os

from analytics.daily_plan.pdf import render

CSS = """
@page{size:A4;margin:14mm 13mm 14mm 13mm}
*{box-sizing:border-box}body{font-family:Inter,Arial,sans-serif;color:#2b3445;font-size:9.4pt;line-height:1.45;margin:0}
h1{font-size:19pt;color:#0b2e59;margin:0 0 1mm}h2{font-size:12.5pt;color:#0b2e59;border-bottom:2px solid #0e7c86;
padding-bottom:1mm;margin:6mm 0 2.5mm;page-break-after:avoid}h3{font-size:10.5pt;color:#14213d;margin:3.5mm 0 1mm;page-break-after:avoid}
.k{font-size:8pt;letter-spacing:.14em;text-transform:uppercase;color:#0e7c86;font-weight:700}
.sub{color:#667085;font-size:8.6pt;margin-bottom:3mm}
.box{background:#e6f4f5;border-left:3px solid #0e7c86;padding:2.5mm 3.5mm;margin:2mm 0 3mm}
table{width:100%;border-collapse:collapse;margin:1mm 0 3mm}th{background:#0b2e59;color:#fff;text-align:left;
padding:1.3mm 1.8mm;font-size:8.2pt}td{padding:1.2mm 1.8mm;border-bottom:1px solid #e4e7ec;vertical-align:top}
tr{page-break-inside:avoid}td.n{text-align:right;white-space:nowrap;width:22mm}
.q{font-style:italic;color:#14213d}.small{font-size:7.6pt}.mut{color:#667085}
.pb{page-break-before:always}ul{margin:1mm 0 2mm;padding-left:5mm}li{margin:.6mm 0}
"""


def esc(x: object) -> str:
    return html.escape(str(x if x is not None else ""))


def pct(a: int, b: int) -> str:
    return f"{a} ({round(100 * a / b)}%)" if b else str(a)


def theme_n(summary: dict, kind: str, theme: str | None) -> int | None:
    if not theme:
        return None
    return (summary.get("themes", {}).get(kind) or {}).get(theme)


def _items(summary: dict, kind: str, items: list[dict], cols: list[tuple[str, str]], base: int) -> str:
    rows = ""
    for it in items:
        n = theme_n(summary, kind, it.get("theme"))
        cells = "".join(f"<td>{esc(it.get(k, ''))}</td>" for k, _ in cols)
        rows += f"<tr>{cells}<td class='n'>{pct(n, base) if n is not None else '-'}</td></tr>"
    head = "".join(f"<th>{esc(t)}</th>" for _, t in cols) + "<th>Leads</th>"
    return f"<table><tr>{head}</tr>{rows}</table>" if rows else ""


def html_doc(cfg: dict, summary: dict, findings: dict, leads: list[dict] | None = None) -> str:
    total, heard = summary["total"], summary["heard"]
    name = cfg.get("name", "")
    p = [f"<div class='k'>Bootcamp delivery team · learner needs</div><h1>{esc(name)}</h1>",
         f"<div class='sub'>{esc(cfg.get('dates', ''))} · {total} calls read, one per lead "
         f"({heard} said enough to tell who they are) · {esc(findings.get('fresh', ''))}</div>"]
    if findings.get("headline"):
        p.append("<div class='box'><b>What to do with this</b><ul>" +
                 "".join(f"<li>{esc(x)}</li>" for x in findings["headline"]) + "</ul></div>")

    p.append("<h2>1. Who is coming</h2><table><tr><th>Group</th><th>Who they are</th><th>What they want</th>"
             "<th>Leads</th></tr>")
    gtxt = findings.get("groups", {})
    for g, n in summary["by_group"].items():
        t = gtxt.get(g, {})
        p.append(f"<tr><td><b>{esc(g)}</b></td><td>{esc(t.get('who', ''))}</td><td>{esc(t.get('wants', ''))}</td>"
                 f"<td class='n'>{pct(n, total)}</td></tr>")
    p.append("</table>")
    if findings.get("groups_note"):
        p.append(f"<p class='small mut'>{esc(findings['groups_note'])}</p>")

    days = findings.get("days", [])
    if days:
        p.append("<h2>2. What to answer, and when</h2>")
        for d in days:
            p.append(f"<h3>{esc(d.get('day', ''))} · {esc(d.get('segment', ''))}</h3>")
            p.append(_items(summary, "question", d.get("items", []),
                            [("ask", "What they ask"), ("answer", "Answer in the room")], heard))

    for title, kind, key, cols in [
        ("3. Wrong ideas to correct early", "misconception", "misconceptions",
         [("belief", "What they think"), ("correct", "Say this")]),
        ("4. Doubts that stop people", "doubt", "doubts", [("doubt", "Doubt"), ("answer", "How to handle it")]),
        ("5. What they hope to get", "hope", "hopes", [("hope", "Hope"), ("note", "Use it in the session")]),
        ("6. What landed on the call", "resonated", "resonated", [("what", "What landed"), ("note", "Where to use it")]),
    ]:
        if findings.get(key):
            p.append(f"<h2>{title}</h2>" + _items(summary, kind, findings[key], cols, heard))

    if findings.get("per_group"):
        p.append("<h2>7. Group by group</h2>")
        for g in findings["per_group"]:
            p.append(f"<h3>{esc(g.get('group', ''))}</h3><ul>" +
                     "".join(f"<li>{esc(x)}</li>" for x in g.get("points", [])) + "</ul>")
    if findings.get("quotes"):
        p.append("<h2>8. In their words</h2><ul>" + "".join(
            f"<li><span class='q'>“{esc(q.get('q', ''))}”</span> <span class='mut'>— {esc(q.get('who', ''))}</span></li>"
            for q in findings["quotes"]) + "</ul>")
    if findings.get("sales"):
        p.append("<h2>9. For the sales team</h2><ul>" + "".join(f"<li>{esc(x)}</li>" for x in findings["sales"]) + "</ul>")

    p.append("<h2 class='pb'>Appendix A. Groups by landing page</h2><table><tr><th>Landing page</th>"
             "<th>Calls</th><th>Main groups</th></tr>")
    for f, c in summary["by_funnel"].items():
        top = ", ".join(f"{esc(g)} {n}" for g, n in list(c.items())[:3])
        p.append(f"<tr><td>{esc(f)}</td><td class='n'>{sum(c.values())}</td><td>{top}</td></tr>")
    p.append("</table>")
    if leads:
        p.append("<h2 class='pb'>Appendix B. Lead by lead</h2><table class='small'><tr><th>#</th><th>Group</th>"
                 "<th>Who</th><th>Why registered</th><th>Doubts</th><th>Their words</th></tr>")
        for r in leads:
            p.append(f"<tr><td>{esc(r['key'])}</td><td>{esc(r.get('group', ''))}</td><td>{esc(r['who'])}</td>"
                     f"<td>{esc(r['why_registered'])}</td><td>{esc(r['doubts'])}</td>"
                     f"<td class='q'>{esc(r['their_words'])}</td></tr>")
        p.append("</table>")
    p.append("<p class='small mut'>Method: calls of 60+ seconds, the longest call per lead, spread across landing "
             "pages. Every call was read in full by Claude; counts are leads who raised each point. Names and phone "
             "numbers are left out.</p>")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(name)} learner needs</title>"
            f"<style>{CSS}</style></head><body>{''.join(p)}</body></html>")


def write_pdf(cfg: dict, summary: dict, findings: dict, leads: list[dict] | None, out_pdf: str) -> str:
    return render(html_doc(cfg, summary, findings, leads), os.path.splitext(out_pdf)[0] + ".html", out_pdf)
