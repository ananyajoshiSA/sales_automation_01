"""The plan PDF: HTML in the agreed order, printed with headless Chromium."""

from __future__ import annotations

import html
import os
import subprocess

CHROME = os.environ.get("CHROME_BIN", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
e = lambda s: html.escape(str(s or ""))
def cut(s, n):
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"
LIKELY = {"A": 0.6, "B": 0.35}
def likely(rs): return sum(r["chance"] / 100 * LIKELY.get(r["tier"], 0.3) for r in rs)
def pipe(rs): return sum(r["chance"] / 100 for r in rs)
css = """
@page { size: A4; margin: 14mm 12mm 16mm 12mm; @bottom-right { content: counter(page); } }
body { font-family: 'Lato','Helvetica Neue',Arial,sans-serif; color:#1d2b28; font-size:10.5px; line-height:1.42; }
h1,h2,h3 { font-family: 'DejaVu Serif', Georgia, serif; color:#1F4E46; }
h2 { font-size:19px; margin:0 0 8px; page-break-before: always; } h3 { font-size:14px; margin:14px 0 6px; }
.cover { background:#1F4E46; color:#fff; border-radius:8px; padding:26px 30px; }
.cover .k { letter-spacing:.14em; font-size:10px; opacity:.85; text-transform:uppercase; }
.cover h1 { color:#fff; font-size:30px; margin:8px 0; line-height:1.15; } .cover p { font-size:12.5px; opacity:.95; }
.tiles { display:grid; grid-template-columns: repeat(3,1fr); gap:10px; margin:18px 0; }
.tile { border:1px solid #d6dcda; border-radius:6px; padding:12px; } .tile b { font-family: Georgia,serif; font-size:24px; color:#1F4E46; display:block; }
.tile span { color:#555; font-size:10px; }
table { border-collapse:collapse; width:100%; margin:6px 0 10px; font-size:9.5px; table-layout:fixed; } td,th { word-wrap:break-word; } th { background:#E8EFEC; color:#1F4E46; text-align:left; font-size:10px; }
th,td { border:1px solid #d6dcda; padding:5px 6px; vertical-align:top; } tr { page-break-inside: avoid; }
.ph { font-family: 'DejaVu Sans Mono', monospace; font-weight:bold; color:#1F4E46; white-space:nowrap; }
.sub { color:#555; font-size:9.5px; } .role { font-weight:bold; }
.box { border-left:4px solid #1F4E46; background:#F4F7F6; padding:8px 12px; margin:8px 0; }
.warn { border-left-color:#B8860B; background:#FFF8E6; } .bad { border-left-color:#B03A2E; background:#FBEDEB; }
.chip { display:inline-block; font-size:8.5px; padding:1px 5px; border-radius:8px; margin:1px 2px 0 0; background:#eee; }
.chip.bad { background:#F8D7D3; } .chip.warn { background:#FFF0C2; } .chip.good { background:#DCEFE6; }
.fb { border:1px solid #d6dcda; border-radius:6px; padding:8px 10px; margin:8px 0; page-break-inside: avoid; }
.fb h4 { margin:0 0 4px; font-size:12px; color:#1F4E46; }
.ctiles { display:grid; grid-template-columns: repeat(7,1fr); gap:6px; margin:6px 0 10px; } .ctiles div { border:1px solid #d6dcda; border-radius:5px; padding:6px; text-align:center; }
.ctiles b { font-size:16px; color:#1F4E46; display:block; } .ctiles span { font-size:8.5px; color:#555; }
ol li, ul li { margin-bottom:3px; } .small { font-size:9px; color:#555; }
"""


def html_doc(R: dict, C: dict) -> str:
    LN = C.get("leader", "Team leader").split()[0]
    PL = C["review"]["day_label"].split()[0]
    H = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(C['pdf_title'])}</title><style>{css}</style></head><body>"]
    H.append(f"<div class='cover'><div class='k'>{e(C['cover_kicker'])}</div><h1>{e(C['cover_h1'])}</h1><p>{e(C['cover_sub'])}</p></div>")
    H.append("<div class='tiles'>" + "".join(f"<div class='tile'><b>{e(v)}</b><span>{e(k)}</span></div>" for v, k in C["tiles"]) + "</div>")
    H.append("<h3>Contents</h3><ol>" + "".join(f"<li>{e(x)}</li>" for x in C["contents"]) + "</ol>")
    H.append(f"<p class='small'>{e(C['data_note'])}</p>")

    # 1 previous-day review
    RV = C["review"]
    H.append(f"<h2>1. {e(RV['title'])}</h2>")
    H.append(f"<div class='box'>{RV['headline']}</div>")
    H.append(f"<h3>Scorecard: {e(RV['before_label'])} vs {e(RV['day_label'])}</h3><table><tr><th style='width:34%'>Measure</th><th>{e(RV['before_label'])}</th><th>{e(RV['day_label'])}</th><th>Note</th></tr>" +
             "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td><b>{e(c)}</b></td><td class='sub'>{e(d)}</td></tr>" for a, b, c, d in RV["scorecard"]) + "</table>")
    H.append("<h3>What the plan asked vs what happened</h3><table><tr><th style='width:30%'>The plan said</th><th style='width:8%'>Done?</th><th>What happened</th></tr>" +
             "".join(f"<tr><td>{e(a)}</td><td style='text-align:center;font-size:10px;color:{'#1F7A4D' if b=='Yes' else '#B8860B' if b=='Partly' else '#B03A2E'}'><b>{b}</b></td><td>{e(c)}</td></tr>" for a, b, c in RV["followed"]) + "</table>")
    H.append(f"<h3>Team performance by caller ({e(RV['day_label'])})</h3><table><tr><th style='width:13%'>Caller</th><th>Dials</th><th>Ans.</th><th>Real conv.</th><th>Talk min</th><th>On sheet</th><th>Tier A tried</th><th>Full ask</th><th style='width:12%'>Paid</th><th style='width:28%'>In one line</th></tr>" +
             "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in row) + "</tr>" for row in RV["callers"]) + "</table>")
    H.append(f"<h3>{LN}'s priority leads from {PL}: what happened</h3><table><tr><th style='width:26%'>Lead</th><th>What happened</th><th style='width:13%'>Result</th></tr>" +
             "".join(f"<tr><td><b>{e(a)}</b></td><td>{e(b)}</td><td>{e(c)}</td></tr>" for a, b, c in RV["prev_priority"]) + "</table>" if RV["prev_priority"] else "<p class='small'>No saved priority list for that day.</p>")
    H.append("<h3>Missed opportunities</h3>" + "".join(f"<div class='box bad'><b>{e(a)}.</b> {e(b)}</div>" for a, b in RV["missed"]))
    H.append(f"<h3>Points for your conversation with {LN}</h3><ol>" + "".join(f"<li>{e(x)}</li>" for x in RV["talking"]) + "</ol>")
    # 2 priority
    H.append(f"<h2>2. {LN}: your priority leads</h2><p>{C['priority_intro']}</p><table><tr><th style='width:3%'>#</th><th style='width:21%'>Lead</th><th style='width:28%'>Why this lead</th><th style='width:24%'>What the caller must do</th><th style='width:24%'>Your role · check by</th></tr>")
    cur = None
    for i, p in enumerate(C["priority"], 1):
        if p.get("group") != cur:
            cur = p.get("group"); n = sum(1 for q in C["priority"] if q.get("group") == cur)
            H.append(f"<tr><td colspan='5' style='background:#1F4E46;color:#fff;font-weight:bold'>{e(C['priority_groups'][cur])} ({n})</td></tr>")
        H.append(f"<tr><td>{i}</td><td><b>{e(p['name'])}</b><br><span class='ph'>☎ +91-{e(p['phone'])}</span><div class='sub'>{e(p['owner'])} · {p['chance']}%<br>{e(cut(p.get('course'),80))}<br>Decides: {e(p.get('who_decides') or '?')} · Pays: {e(cut(p.get('how_pay') or '?',60))}</div></td>"
                 f"<td>{e(cut(p['why'],330))}</td><td>{e(cut(p['ask'],280))}</td><td><span class='role'>{e(p['role'])}</span><div class='sub'>Check by {e(p['check_by'])}</div></td></tr>")
    H.append("</table>")
    # 2 checks
    H.append(f"<h2>3. {LN}: what you check, and when</h2><table><tr><th style='width:12%'>Time</th><th>Check</th><th>If it fails</th></tr>" +
             "".join(f"<tr><td><b>{e(a)}</b></td><td>{e(b)}</td><td>{e(c)}</td></tr>" for a, b, c in C["checks"]) + "</table>")
    H.append(f"<div class='box'>{e(C['huddle'])}</div>")
    # 3 feedback
    H.append(f"<h2>4. {LN}: feedback to give each caller</h2><p>From {PL}'s calls and transcripts. Give it at the next huddle, one caller at a time, two minutes each.</p>")
    for fb in C["feedback"]:
        H.append(f"<div class='fb'><h4>{e(fb['caller'])}</h4><div class='sub'>{e(fb['numbers'])}</div><p><b>Went well:</b> {e(fb['good'])}<br><b>Fix:</b> {e(fb['fix'])}</p>"
                 "<b>Their 3 rules today:</b><ol>" + "".join(f"<li>{e(x)}</li>" for x in fb["rules"]) + "</ol></div>")
    # 4 lessons
    L4 = C["lessons"]
    H.append(f"<h2>5. What {PL} taught us</h2><h3>What worked</h3>" + "".join(f"<div class='box'>{x}</div>" for x in L4["worked"]))
    H.append(f"<div class='box warn'>{L4['count']}</div>")
    H.append("<h3>What did not work</h3>" + "".join(f"<div class='box bad'>{x}</div>" for x in L4["didnt"]))
    H.append("<h3>What the plan got wrong (fixed in today's sheets)</h3>" + "".join(f"<div class='box warn'>{x}</div>" for x in L4["plan_wrong"]))
    # 5 dialling changes
    H.append(f"<h2>6. Dialling pattern: what changes today</h2><p>These apply to every caller. Each comes from {PL}'s numbers.</p><ol>" +
             "".join(f"<li>{e(x)}</li>" for x in C["changes"]) + f"</ol><table><tr><th>{PL}'s numbers</th><th>Value</th><th>So today</th></tr>" +
             "".join(f"<tr><td>{e(a)}</td><td><b>{e(b)}</b></td><td>{e(c)}</td></tr>" for a, b, c in C["numbers_table"]) + "</table>")
    # 6 hour by hour
    H.append(f"<h2>7. The day, hour by hour</h2><p>{e(C['hours_note'])}</p><table><tr><th style='width:12%'>Time</th><th>What callers do</th><th>What {LN} does</th></tr>" +
             "".join(f"<tr><td><b>{e(a)}</b></td><td>{e(b)}</td><td>{e(c)}</td></tr>" for a, b, c in C["hours"]) + "</table>")
    # 7 AB rules
    H.append("<h2>8. On every A/B call</h2><ol>" + "".join(f"<li>{e(x)}</li>" for x in C["ab_rules"]) + f"</ol><div class='box'>{e(C['excluded_note'])}</div>")
    # 8 overview
    H.append("<h2>9. Team overview</h2><table><tr><th>Caller</th><th>M</th><th>P</th><th>A</th><th>B</th><th>F</th><th>R</th><th>C</th><th>Leads</th><th>Likely today</th><th>3-day pipeline</th><th>Missed calls to return</th></tr>")
    for o in C["owner_order"]:
        rs = R["by_owner"].get(o, [])
        H.append(f"<tr><td><b>{e(o)}</b></td>" + "".join(f"<td>{sum(r['tier']==t for r in rs)}</td>" for t in "MPABFRC") +
                 f"<td>{len(rs)}</td><td>{likely(rs):.1f}</td><td>{pipe(rs):.1f}</td><td>{sum(1 for r in rs if r.get('missed'))}</td></tr>")
    H.append(f"</table><p class='small'>{e(C['overview_note'])}</p>")
    # 9 caller lists
    first_caller = True
    TN = {"P": "P – paid booking, collect the balance", "M": "M – return the lead's call", "A": "Tier A – close today", "B": "Tier B – hot follow-up", "F": "F · New leads, first contact", "R": "R · Revive closure-stage", "C": "C · Nurture"}
    for o in C["owner_order"]:
        rs = R["by_owner"].get(o, []); fb = next((f for f in C["feedback"] if f["caller"] == o), None)
        H.append(f"<h2>{'10. Caller-wise lists · ' if first_caller else ''}{e(o)}</h2><div class='ctiles'>" + "".join(f"<div><b>{sum(r['tier']==t for r in rs)}</b><span>{t}</span></div>" for t in "MPABFRC") +
                 f"<div><b>{likely(rs):.1f}</b><span>likely today</span></div></div>")
        first_caller = False
        if fb:
            H.append(f"<div class='fb'><div class='sub'>{e(fb['numbers'])}</div><b>Went well:</b> {e(fb['good'])}<br><b>Fix:</b> {e(fb['fix'])}<br><b>Your 3 rules today:</b><ol>" + "".join(f"<li>{e(x)}</li>" for x in fb["rules"]) + "</ol></div>")
        for t in "MPABFRC":
            tr = [r for r in rs if r["tier"] == t]
            if not tr: continue
            H.append(f"<h3>{TN[t]} ({len(tr)}) · {e(C['slots'][t])}</h3>")
            if t in "PAB" or (t == "M" and any(r.get('tier_orig') in ('A','B') for r in tr)):
                H.append("<table><tr><th style='width:3%'>#</th><th style='width:23%'>Lead</th><th style='width:28%'>Why</th><th style='width:20%'>Opening line</th><th style='width:26%'>Exact ask · prepare for</th></tr>")
                for i, r in enumerate(tr, 1):
                    chips = []
                    if r.get("missed"): chips.append(f"<span class='chip bad'>Missed call {e(r['missed_last'])}</span>")
                    if r.get("callback_requested"): chips.append(f"<span class='chip warn'>{e(cut(r['callback_requested'],40))}</span>")
                    if r.get("whatsapp_only"): chips.append("<span class='chip warn'>WhatsApp only</span>")
                    if r.get("today"): chips.append(f"<span class='chip good'>{e(cut(r['today'],60))}</span>")
                    H.append(f"<tr><td>{i}</td><td><b>{e(r.get('name') or 'Unnamed lead')}</b><br><span class='ph'>☎ +91-{e(r.get('phone'))}</span><div class='sub'>{e(cut(r.get('course'),70))}<br>Stage: {e(r.get('stage'))} · {r['chance']}% · Best: {e(cut(r.get('best_time'),40))}<br>Decides: {e(cut(r.get('who_decides') or '?',40))} · Pays: {e(cut(r.get('how_pay') or '?',50))}</div>{''.join(chips)}</td>"
                             f"<td>{e(cut(r.get('why'),420))}{('<br><i>Blocker: ' + e(cut(r.get('real_blocker'),120)) + '</i>') if r.get('real_blocker') else ''}</td><td>{e(cut(r.get('opening_line'),260))}</td><td>{e(cut(r.get('exact_ask'),300))}<br><span class='sub'>Prepare: {e(cut(r.get('objection'),200))}</span></td></tr>")
                H.append("</table>")
            else:
                H.append("<table><tr><th style='width:4%'>#</th><th style='width:20%'>Lead</th><th style='width:15%'>Phone</th><th style='width:25%'>Course</th><th style='width:14%'>Best time</th><th style='width:22%'>Note</th></tr>")
                for i, r in enumerate(tr, 1):
                    note = " · ".join(x for x in [f"Missed call {r['missed_last']}" if r.get("missed") else "", r.get("callback_requested") or "", "WhatsApp only" if r.get("whatsapp_only") else "", r.get("today") or ""] if x)
                    H.append(f"<tr><td>{i}</td><td>{e(r.get('name') or 'Unnamed lead')}</td><td class='ph'>+91-{e(r.get('phone'))}</td><td>{e(cut(r.get('course'),60))}</td><td>{e(cut(r.get('best_time'),30))}</td><td class='sub'>{e(cut(note,110))}</td></tr>")
                H.append("</table>")
    H.append("</body></html>")
    return "\n".join(H)



def render(html_text: str, html_path: str, pdf_path: str) -> str:
    open(html_path, "w").write(html_text)
    subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(pdf_path)}", os.path.abspath(html_path)],
                   check=True, capture_output=True, timeout=300)
    return pdf_path
