"""Render the caller-wise call plan as a detailed, shareable PDF for the team leader.

    python scripts/build_plan_pdf.py exports/call_plan_2026-10-08/Elite_call_plan_Thu_8_Oct.json out.html
    chrome --headless --print-to-pdf=out.pdf out.html
"""

import html
import json
import re
import sys

LEAD_NAME_FIXES = [(r"\b(\w+) \1\b", r"\1"), (r"none$", "")]


def name(r):
    n = (r.get("name") or "").strip()
    if not n or n.startswith("+") or re.fullmatch(r"[\d\- ]+", n):
        return "Unnamed lead (see workbook)"
    for pat, rep in LEAD_NAME_FIXES:
        n = re.sub(pat, rep, n, flags=re.I)
    return html.escape(n.strip())


def esc(s, n=None):
    s = (s or "").strip()
    if n and len(s) > n:
        s = s[: n - 1].rsplit(" ", 1)[0] + "…"
    return html.escape(s)


TOMORROW_SHARE = {"A": 0.6, "B": 0.35}


def tomorrow(rs):
    return sum(r["prob_3d"] * TOMORROW_SHARE.get(r["tier"], 0.3) for r in rs) / 100


def flags(r):
    out = []
    if r.get("missed_unreturned"):
        out.append('<span class="chip bad">Missed call from lead</span>')
    if r.get("month_end_due"):
        out.append('<span class="chip warn">Month-end promise due</span>')
    if (r.get("next_action") or "").startswith("[Wed evening"):
        out.append('<span class="chip warn">Wed evening callback missed</span>')
    if r.get("changed") and not r["changed"].lower().startswith("unchanged"):
        out.append('<span class="chip good">Updated from Wed night calls</span>')
    if r.get("stage") == "Not Interested":
        out.append('<span class="chip bad">Staged Not Interested – fix</span>')
    return " ".join(out)


def lead_rows(rs):
    rows = []
    for i, r in enumerate(rs, 1):
        action = r.get("next_action") or ""
        rows.append(f"""
<tr class="tier-{r['tier']}">
  <td class="num">{i}</td>
  <td class="lead"><b>{name(r)}</b><div class="sub">{esc(r.get('course'), 70)}</div>
      <div class="sub">Stage: {esc(r.get('stage'))} · {int(r['prob_3d'])}% · Best: {esc(r.get('best_time') or 'anytime', 30)}</div>
      <div>{flags(r)}</div></td>
  <td>{esc(r.get('why'), 300)}</td>
  <td><i>“{esc(r.get('opening_line'), 260)}”</i></td>
  <td>{esc(action, 300)}{('<div class="sub"><b>Prepare:</b> ' + esc(r.get('objection_to_prepare'), 160) + '</div>') if r.get('objection_to_prepare') else ''}</td>
</tr>""")
    return "".join(rows)


def caller_section(owner, rs):
    t = {k: [r for r in rs if r["tier"] == k] for k in "MPABFRC"}
    p3 = sum(r["prob_3d"] for r in rs) / 100
    tm = tomorrow(rs)
    missed = sum(1 for r in rs if r.get("missed_unreturned"))
    carried = sum(1 for r in rs if (r.get("next_action") or "").startswith("[Wed evening"))
    ni = sum(1 for r in rs if r["tier"] in "AB" and r.get("stage") == "Not Interested")
    first_names = lambda lst, n=12: ", ".join(name(r) for r in lst[:n]) + (f" and {len(lst) - n} more" if len(lst) > n else "")  # noqa: E731
    gap = "on track if every A and B is worked" if p3 >= 4 else "short of 4 even over 3 days – needs extra leads"
    return f"""
<section class="caller">
  <h2>{html.escape(owner)}</h2>
  <div class="stats">
    <div><b>{len(t['A'])}</b><span>Close today (A)</span></div>
    <div><b>{len(t['B'])}</b><span>Hot follow-up (B)</span></div>
    <div><b>{len(t['F'])}</b><span>New leads (F)</span></div>
    <div><b>{len(t['R'])}</b><span>Revive (R)</span></div>
    <div><b>{len(t['C'])}</b><span>Nurture (C)</span></div>
    <div><b>{tm:.1f}</b><span>Likely enrollments tomorrow</span></div>
    <div><b>{p3:.1f}</b><span>3-day pipeline ({gap})</span></div>
  </div>
  <p class="note">First 30 minutes: {missed} missed call(s) to return, {carried} Wednesday-evening callback(s) that were missed, {ni} stage(s) to correct. Then work the list below in order.</p>
  {f'<h3>First: return the lead\'s call (M) and unpaid links (P) ({len(t["M"]) + len(t["P"])})</h3><table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Why</th><th>Opening line</th><th>What to send / ask</th></tr></thead><tbody>' + lead_rows(t['M'] + t['P']) + '</tbody></table>' if t['M'] or t['P'] else ''}
  <h3>Tier A – close today ({len(t['A'])})</h3>
  {'<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Why</th><th>Opening line</th><th>What to send / ask</th></tr></thead><tbody>' + lead_rows(t['A']) + '</tbody></table>' if t['A'] else '<p class="note">No Tier A lead. Start with the B list at 10:30.</p>'}
  <h3>Tier B – hot follow-up ({len(t['B'])})</h3>
  {'<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Why</th><th>Opening line</th><th>What to send / ask</th></tr></thead><tbody>' + lead_rows(t['B']) + '</tbody></table>' if t['B'] else '<p class="note">No Tier B lead.</p>'}
  <h3>After 14:15</h3>
  <p><b>F · New leads, first contact ({len(t['F'])}):</b> {first_names(t['F']) or '–'}.</p>
  <p><b>R · Revive closure-stage ({len(t['R'])}):</b> {first_names(t['R']) or '–'}.</p>
  <p><b>C · Nurture ({len(t['C'])}):</b> {first_names(t['C'], 10) or '–'}.</p>
  <p class="note">Phone numbers, full notes and the attempt-tracking columns for every lead are on the "{html.escape(owner[:31])}" sheet of the workbook.</p>
</section>"""


CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; @bottom-right { content: counter(page); } }
* { box-sizing: border-box; }
body { font-family: Carlito, "Liberation Sans", Arial, sans-serif; color: #1d2321; font-size: 10.5pt; line-height: 1.45; margin: 0; }
h1, h2, h3 { font-family: Caladea, "Liberation Serif", Georgia, serif; color: #123f36; line-height: 1.2; margin: 0; }
h1 { font-size: 26pt; } h2 { font-size: 17pt; margin: 0 0 6pt; } h3 { font-size: 12.5pt; margin: 14pt 0 5pt; }
p { margin: 5pt 0; } ul, ol { margin: 4pt 0; padding-left: 16pt; } li { margin: 2.5pt 0; }
.cover { height: 260mm; display: flex; flex-direction: column; justify-content: space-between; page-break-after: always; }
.cover .band { background: #1f4e46; color: #fff; padding: 22pt 24pt; border-radius: 6pt; }
.cover .band h1 { color: #fff; } .cover .band p { color: #dcebe6; font-size: 12pt; }
.eyebrow { text-transform: uppercase; letter-spacing: 1.5pt; font-size: 9pt; color: #9fd0c3; }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8pt; margin-top: 14pt; }
.kpis div { border: 1px solid #cfd8d4; border-radius: 5pt; padding: 9pt 11pt; }
.kpis b { display: block; font-family: Caladea, serif; font-size: 20pt; color: #123f36; }
.kpis span { font-size: 9pt; color: #5c6763; }
.toc li { margin: 3pt 0; }
section { page-break-before: always; }
section.flow { page-break-before: auto; margin-top: 18pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 9.2pt; }
th, td { border: 1px solid #d5dcd8; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #eaf1ee; color: #123f36; font-weight: bold; }
tr { page-break-inside: avoid; }
td.t { white-space: nowrap; font-weight: bold; color: #123f36; }
.leads td { font-size: 8.6pt; } .leads td.num { width: 16pt; text-align: center; }
.leads td.lead { width: 24%; } .leads td:nth-child(3) { width: 22%; } .leads td:nth-child(4) { width: 25%; }
.tier-A td { background: #f1f8f4; } .tier-B td { background: #f4f7fc; }
.sub { color: #5c6763; font-size: 8pt; margin-top: 1.5pt; }
.chip { display: inline-block; padding: 0 5pt; border-radius: 8pt; font-size: 7.4pt; font-weight: bold; margin: 2pt 2pt 0 0; }
.chip.bad { background: #f8e3e0; color: #a3302a; } .chip.good { background: #e2f1e6; color: #2c6e3c; } .chip.warn { background: #fbefd6; color: #8a5a00; }
.callout { background: #e8f2ee; border-left: 4pt solid #1f6f5c; padding: 8pt 11pt; margin: 9pt 0; border-radius: 3pt; }
.warnbox { background: #fdf3e2; border-left: 4pt solid #b07800; padding: 8pt 11pt; margin: 9pt 0; border-radius: 3pt; }
.note { color: #5c6763; font-size: 9pt; }
.stats { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5pt; margin: 6pt 0 4pt; }
.stats div { border: 1px solid #d5dcd8; border-radius: 4pt; padding: 5pt; text-align: center; }
.stats b { display: block; font-family: Caladea, serif; font-size: 15pt; color: #123f36; }
.stats span { font-size: 7.6pt; color: #5c6763; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12pt; }
.script { border: 1px solid #d5dcd8; border-radius: 4pt; padding: 7pt 10pt; margin: 6pt 0; page-break-inside: avoid; }
.script h4 { margin: 0 0 3pt; font-size: 10.5pt; color: #123f36; }
.mono { font-family: "Liberation Mono", monospace; font-size: 9pt; }
"""


def build(plan_path, out_path):
    P = json.load(open(plan_path))
    owners = [o for o in P if o != "Shivangi Sahu"]
    all_rows = [r for o in owners for r in P[o]]
    T = {k: sum(1 for r in all_rows if r["tier"] == k) for k in "ABFRC"}
    team_tom = sum(tomorrow(P[o]) for o in owners)
    team_p3 = sum(r["prob_3d"] for r in all_rows) / 100
    missed = sum(1 for r in all_rows if r.get("missed_unreturned"))
    carried = sum(1 for r in all_rows if (r.get("next_action") or "").startswith("[Wed evening"))
    month_end = [(o, r) for o in owners for r in P[o] if r.get("month_end_due")]
    ni = [(o, r) for o in owners for r in P[o] if r["tier"] in "AB" and r.get("stage") == "Not Interested"]

    summary_rows = "".join(
        f"<tr><td><b>{html.escape(o)}</b></td>" + "".join(f"<td>{sum(1 for r in P[o] if r['tier'] == k)}</td>" for k in "ABFRC")
        + f"<td>{len(P[o])}</td><td><b>{tomorrow(P[o]):.1f}</b></td><td>{sum(r['prob_3d'] for r in P[o]) / 100:.1f}</td>"
        f"<td>{sum(1 for r in P[o] if r.get('missed_unreturned'))}</td></tr>" for o in owners)
    me_rows = "".join(f"<tr><td>{html.escape(o)}</td><td>{name(r)}</td><td>{esc(r.get('why'), 220)}</td></tr>" for o, r in month_end)
    ni_rows = "".join(f"<tr><td>{html.escape(o)}</td><td>{name(r)}</td><td>{r['tier']}</td><td>{esc(r.get('why'), 200)}</td></tr>" for o, r in ni)

    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Elite Calling Plan – Thu 8 Oct 2026</title><style>{CSS}</style></head><body>

<div class="cover">
  <div class="band">
    <div class="eyebrow">Team Elite Calling · for Shivangi Sahu, Team Leader</div>
    <h1>Call plan and workflow<br>Thursday 8 October 2026</h1>
    <p>Who each caller calls, in what order, what to say, what to ask for, how many attempts, and how you check through the day that it is happening.</p>
  </div>
  <div>
    <div class="kpis">
      <div><b>{sum(len(v) for v in P.values())}</b><span>leads on tomorrow's call sheets (11 callers + team leader)</span></div>
      <div><b>{T['A']}</b><span>Tier A: can close tomorrow</span></div>
      <div><b>{T['B']}</b><span>Tier B: hot follow-ups</span></div>
      <div><b>{T['F']}</b><span>New leads (1–7 Oct) still needing a real first conversation</span></div>
      <div><b>≈{team_tom:.0f}</b><span>enrollments likely tomorrow if every A and B is worked (target 44)</span></div>
      <div><b>≈{team_p3:.0f}</b><span>enrollments in the 3-day pipeline (Thu–Sat)</span></div>
    </div>
    <h3>Contents</h3>
    <ol class="toc">
      <li>The target and where enrollments will come from</li>
      <li>How the call lists were built</li>
      <li>Before 10:00 – team leader set-up</li>
      <li>The day, hour by hour</li>
      <li>How to work each tier: call flow and attempt rules</li>
      <li>After every call: logging and outcomes</li>
      <li>Objection playbook</li>
      <li>WhatsApp messages</li>
      <li>Checkpoints and monitoring</li>
      <li>End of day and carry-forward</li>
      <li>Stages to fix, month-end leads, escalations</li>
      <li>Caller-wise call lists (one section per caller)</li>
    </ol>
    <p class="note">Data: LeadSquared calls, stages and follow-ups plus Zipteams call notes, 22 Sep – 7 Oct 2026, complete through midnight (pulled 01:30 IST, Thu 8 Oct), including every Wednesday-evening call, Zip note and stage change. Companion file: <b>Elite_call_plan_Thu_8_Oct.xlsx</b> (phone numbers, attempt tracking, live team summary).</p>
  </div>
</div>

<section>
<h2>1. The target and where enrollments will come from</h2>
<p>The goal is <b>4 enrollments per caller</b> tomorrow, 44 across the 11 callers. Every lead on the sheets has an estimated chance of enrolling within 3 days, judged by reading its call summaries and checking them against LeadSquared activity. Adding these up gives a realistic picture:</p>
<table>
<thead><tr><th>Caller</th><th>A</th><th>B</th><th>F</th><th>R</th><th>C</th><th>Leads</th><th>Likely tomorrow</th><th>3-day pipeline</th><th>Missed calls to return</th></tr></thead>
<tbody>{summary_rows}
<tr><td><b>Team</b></td><td><b>{T['A']}</b></td><td><b>{T['B']}</b></td><td><b>{T['F']}</b></td><td><b>{T['R']}</b></td><td><b>{T['C']}</b></td><td><b>{len(all_rows)}</b></td><td><b>{team_tom:.1f}</b></td><td><b>{team_p3:.1f}</b></td><td><b>{missed}</b></td></tr>
</tbody></table>
<div class="warnbox"><p><b>What this means.</b> On current leads, about 1–2 enrollments per caller are likely tomorrow and about 4 per caller across Thursday to Saturday. Four tomorrow is possible only if every Tier A closes on the first or second call, and Tier B leads are pushed for a token or seat block on the call rather than "thinking about it".</p></div>
<h3>Where each caller's enrollments come from</h3>
<ol>
  <li><b>Tier A (2–5 per caller):</b> the main source. These leads are already discussing payment, EMI, documents or a seat block, or promised a decision now due. Each is roughly 25–35% likely to close within 3 days, and most of that can happen tomorrow if called at the right time with the payment link ready.</li>
  <li><b>The best 5–6 Tier B leads:</b> real interest, one or two steps from paying (fee or EMI clarity, family approval, a document). Each is roughly 10–20% likely. Two or three Bs closing makes the difference between 2 and 4.</li>
  <li><b>Leads who called us and weren't called back ({missed} on the sheets):</b> highest intent per minute spent. Return every one in the first 30 minutes.</li>
  <li><b>Month-end promises now due ({len(month_end)}):</b> leads who said they'd decide or pay in the new month (section 11).</li>
  <li><b>New leads (F) and revive (R):</b> lower chance per lead (about 4–6%), but there are many. They feed Friday and Saturday's closes.</li>
</ol>
<h3>Rebalance before 10:00</h3>
<p>Shaziya (23 B), Suhail (21 B) and Pratishta (21 B) cannot work every hot follow-up properly in one day. Swati has no Tier A, and Sonali has only 18 leads. Move 5–6 of Shaziya's and Suhail's <i>lower-ranked</i> B leads (ones not called for 7+ days, so no warm relationship is lost) to Swati and Sonali in LeadSquared, and tell both callers which leads moved.</p>
</section>

<section>
<h2>2. How the call lists were built</h2>
<ul>
  <li><b>Scope:</b> every open lead owned by the team that had a real conversation (a connected call of a minute or more, or a Zip note) between 22 Sep and 7 Oct, plus all new leads created 1–7 Oct, plus leads still parked at Follow Up For Closure or Counselled. Enrolled, irrelevant and invalid leads are excluded.</li>
  <li><b>Scoring:</b> each lead's timeline was assembled: every dial, answered call and talk time, calls from the lead to us, missed calls never returned, follow-up dates, stage, and Zipteams intent, reason and summary.</li>
  <li><b>Reading:</b> the strongest 32–35 leads per caller, plus every month-end and new lead, were read one by one and placed in a tier with an opening line, a specific ask, the objection to prepare for, and the best time to call based on when the lead answered before.</li>
  <li><b>Zip intent was cross-checked, not trusted alone.</b> It only sees one call. In the review it understated about a third of the hottest leads (labelling Neutral or Low leads who already had payment links, EMI paperwork or part-payments) and overstated about a sixth (High leads who then stopped answering or deferred for months).</li>
</ul>
<h3>The five tiers</h3>
<table><thead><tr><th>Tier</th><th>Meaning</th><th>Typical chance (3 days)</th><th>Attempts tomorrow</th></tr></thead><tbody>
<tr><td><b>A · Close today</b></td><td>Payment, EMI, documents or a seat block already under discussion; a decision made or due; still reachable</td><td>25–50%</td><td>4 + WhatsApp</td></tr>
<tr><td><b>B · Hot follow-up</b></td><td>Real interest, 1–2 steps from paying (fee clarity, family approval, a document)</td><td>8–25%</td><td>3 + WhatsApp</td></tr>
<tr><td><b>F · New lead</b></td><td>Created 1–7 Oct, no real conversation yet</td><td>~5%</td><td>3 + WhatsApp</td></tr>
<tr><td><b>R · Revive</b></td><td>Still at Follow Up For Closure or Counselled, last activity 16–45 days ago</td><td>~4%</td><td>2, WhatsApp first</td></tr>
<tr><td><b>C · Nurture</b></td><td>Interested but blocked or more than a week away, or gone quiet</td><td>2–8%</td><td>1, WhatsApp first</td></tr>
</tbody></table>
<p class="note">Within each tier, leads with an unreturned missed call come first, then month-end promises, then the highest chance.</p>
</section>

<section>
<h2>3. Before 10:00 – team leader set-up</h2>
<table><thead><tr><th>Time</th><th>Task</th><th>Done when</th></tr></thead><tbody>
<tr><td class="t">09:15</td><td>Open the workbook; check every caller has their sheet. Check the dialer: place one test call on each line used by Sonali and Suhail (their lists show calls failing at the same second).</td><td>Lines confirmed working, or callers told which line to use.</td></tr>
<tr><td class="t">09:25</td><td>Rebalance: move 5–6 lower-ranked B leads from Shaziya and Suhail to Swati and Sonali in LeadSquared.</td><td>Both callers know which leads they received.</td></tr>
<tr><td class="t">09:35</td><td>Check payment links, the EMI split sheet (3/6/12/18 months) and the refund / money-back terms are ready for every program on the A lists: Independent Director, contract drafting diploma, US accounting, US corporate law, HR bootcamp, NCA Canada, judiciary prep.</td><td>Each caller can send a link within 30 seconds on a call.</td></tr>
<tr><td class="t">09:45</td><td>Huddle (15 min). Each caller reads out their Tier A names, the exact ask for each, and the time they will call. Agree the lunch split so inbound is always covered. Remind the team: callbacks to missed calls within 15 minutes all day.</td><td>Everyone has said their A list aloud.</td></tr>
</tbody></table>
<div class="callout"><p><b>Huddle script:</b> "Today's target is four per person. Your sheet is in order. Do not skip ahead to easier calls. Every A gets a call before 11:30 with the link ready. Every connected call ends with a date: payment, seat block or a fixed callback. Log every attempt in your sheet as you go. I'll check at 12, 3, 6 and 7:30."</p></div>
</section>

<section>
<h2>4. The day, hour by hour</h2>
<table><thead><tr><th>Time</th><th>What callers do</th><th>What you do</th></tr></thead><tbody>
<tr><td class="t">10:00–10:30</td><td>Return every missed call from a lead ({missed} on sheets). Make the {carried} Wednesday-evening callbacks that didn't happen or weren't answered (flagged on the sheets).</td><td>Walk the floor; make sure nobody starts with C leads.</td></tr>
<tr><td class="t">10:30–11:30</td><td>Tier A first attempts, in order (honour each lead's best time). Send the payment link while the lead is on the call. If unanswered, WhatsApp immediately (template A1).</td><td>Be available for 3-way calls with parents or spouses and for EMI or discount approvals.</td></tr>
<tr><td class="t">11:30–12:00</td><td>Overnight new leads and any fresh missed calls. First call to a new lead within 5 minutes of it appearing.</td><td>Check new leads were assigned (not stuck with the distributor).</td></tr>
<tr><td class="t">12:00</td><td colspan="2"><b>Checkpoint 1</b> (section 9).</td></tr>
<tr><td class="t">12:00–13:30</td><td>Tier B first attempts, best-time leads first. Push for a token or seat block, not just "send details".</td><td>Listen to 2 live calls; coach on asking for the commitment.</td></tr>
<tr><td class="t">13:30–14:15</td><td>Lunch in two shifts (half the team at 13:30, half at 14:00).</td><td>Cover inbound during the changeover.</td></tr>
<tr><td class="t">14:15–15:00</td><td>Tier F: new leads from 1–7 Oct that never had a real conversation. Discovery → matching program → fixed callback or fee sheet.</td><td>Spot-check that F calls aren't 30-second "is this a good time" calls.</td></tr>
<tr><td class="t">15:00</td><td colspan="2"><b>Checkpoint 2.</b></td></tr>
<tr><td class="t">15:00–16:00</td><td>Second attempts on A and B; leads whose best time is the afternoon; month-end promise leads.</td><td>Chase any caller with Tier A still "Not started".</td></tr>
<tr><td class="t">16:00–17:00</td><td>Tier R: WhatsApp recap (template R1), then one call. If dead, move the stage out of Follow Up For Closure or Counselled.</td><td>Review stage corrections (section 11).</td></tr>
<tr><td class="t">17:00–18:30</td><td>Tier C (WhatsApp first, then one call); F second attempts; evening-preference B leads.</td><td></td></tr>
<tr><td class="t">18:00</td><td colspan="2"><b>Checkpoint 3.</b></td></tr>
<tr><td class="t">18:30–19:30</td><td>Final attempts on every A not yet closed (working professionals answer after office). Confirm payments received. Update LeadSquared stages and follow-up dates.</td><td>Join any final closing call that needs authority (discount, EMI exception).</td></tr>
<tr><td class="t">19:30</td><td colspan="2"><b>Checkpoint 4</b> and end-of-day review (section 10).</td></tr>
</tbody></table>
</section>

<section>
<h2>5. How to work each tier</h2>
<h3>Tier A – close today</h3>
<ol>
  <li><b>Before dialling:</b> read the "Why" and "What to send / ask" columns; open the payment link and EMI split for that program; check whether a callback time was agreed.</li>
  <li><b>Open</b> with the line on the sheet, which refers to the last conversation. Don't restart the pitch.</li>
  <li><b>Confirm the decision:</b> "Last time you said X. Are you ready to go ahead today?"</li>
  <li><b>Remove the last blocker:</b> EMI tenure, documents, family approval (offer a 3-way call), refund terms in writing.</li>
  <li><b>Close on the call:</b> send the link while talking, stay on the line until payment or the token is done, or fix the exact time today it will be done.</li>
  <li><b>If unanswered:</b> WhatsApp A1 immediately; retry at 15:00, at their best time, and at 18:30.</li>
</ol>
<h3>Tier B – hot follow-up</h3>
<ol>
  <li>Open with the sheet's line; recap what they liked last time.</li>
  <li>Answer the open question in full on the call (fee, EMI, refund, outcomes, batch date). Don't promise to "share details later".</li>
  <li>Ask for a small commitment: Rs 5,000 seat block, EMI application, or a fixed decision call within 24 hours.</li>
  <li>Unanswered: WhatsApp B1; retry at 16:00 and at their best time.</li>
</ol>
<h3>Tier F – new lead, first contact</h3>
<ol>
  <li>"You enquired on our website about … — is this a good time for two minutes?" If not, fix a time and log it.</li>
  <li><b>Discovery (3–5 questions):</b> background, current work, goal, timeline, who decides, budget comfort.</li>
  <li>Pitch <b>one</b> matching program, not a menu. Share the fee with EMI on the call.</li>
  <li>End with a fixed next step: a callback time, a masterclass or demo slot, or the payment link.</li>
  <li>Three attempts today (14:15, 17:30, 19:00) plus WhatsApp F1 after the first missed call.</li>
</ol>
<h3>Tier R – revive closure-stage</h3>
<ol>
  <li>WhatsApp R1 first (recap + current offer), then call once after 30–60 minutes.</li>
  <li>If alive: treat as B and fix a decision date. If not: move the stage to May buy later or Not Interested with a reason, so the closure pipeline shows only live deals.</li>
</ol>
<h3>Tier C – nurture</h3>
<p>WhatsApp C1, then one call. Log the outcome; don't over-dial (several C leads already had 15–30 dials with no conversation).</p>
<h3>Attempt rules (all tiers)</h3>
<ul>
  <li>Space attempts at least 90 minutes apart, and use each lead's "Best time" for one of them.</li>
  <li>Never mark "Call Not Picking Up" before <b>6 attempts across 3 days</b>. Never mark Not Interested without a real conversation and a reason.</li>
  <li>If calls fail at the same second across different leads, it's the dialer. Switch line or use WhatsApp; don't count it as the lead going dark.</li>
</ul>
</section>

<section>
<h2>6. After every call: logging and outcomes</h2>
<p>In the caller's sheet (yellow columns), straight after each dial:</p>
<ol>
  <li><b>Attempt time</b> (e.g. 10:42) and <b>outcome</b> from the dropdown.</li>
  <li><b>WhatsApp sent</b> Y/N, <b>Next step date/time</b> agreed, <b>Payment status</b>.</li>
  <li>In LeadSquared: the stage and next follow-up date, with a one-line note of what the lead said.</li>
</ol>
<table><thead><tr><th>Outcome (dropdown)</th><th>Do next</th><th>LeadSquared</th></tr></thead><tbody>
<tr><td>Enrolled / paid</td><td>Send confirmation and onboarding; tell the team leader</td><td>Course Enrolled</td></tr>
<tr><td>Seat blocked / token paid</td><td>Fix the balance-payment date; send the receipt</td><td>Follow Up For Closure + date</td></tr>
<tr><td>Payment link sent</td><td>Call back in 2–3 hours to confirm; WhatsApp a reminder</td><td>Follow Up For Closure + date</td></tr>
<tr><td>Callback fixed</td><td>Put the exact time in Next step; set a reminder</td><td>Call Back Later + date and time</td></tr>
<tr><td>Spoke – thinking</td><td>Ask what exactly they need to decide; fix a decision call within 24h</td><td>Follow-up date within 24h</td></tr>
<tr><td>Not answered</td><td>WhatsApp (template for the tier); next attempt per the rules</td><td>No stage change</td></tr>
<tr><td>Switched off / failed</td><td>Try another line; WhatsApp</td><td>No stage change</td></tr>
<tr><td>Busy – call later</td><td>Get a specific time, not "later"</td><td>Call Back Later + time</td></tr>
<tr><td>Not interested</td><td>Ask why (price, timing, course) and note it; offer an alternative program if relevant</td><td>Not Interested + reason</td></tr>
<tr><td>Wrong number</td><td>Confirm once; close</td><td>Invalid</td></tr>
</tbody></table>
<div class="callout"><p>"Attempts done" and "Status" (Not started, In progress, Done, Enrolled) fill in automatically. The Team summary tab totals them for you.</p></div>
</section>

<section>
<h2>7. Objection playbook</h2>
<p>These come up most in the reviewed calls. Callers should have the answer ready, not promise to "check and revert".</p>
<div class="two">
<div class="script"><h4>"The fee is too high"</h4><p>Break it into EMI (e.g. 12 or 18 months, no-cost where available) and compare it with one month's earning from the skill. Mention the money-back terms. Offer the seat block now to hold the current price or scholarship.</p></div>
<div class="script"><h4>"I need to ask my husband / parents"</h4><p>"Of course. Shall we do a quick 3-way call today so they can ask me directly?" Send the refund policy and EMI table in writing for the family. Fix the time.</p></div>
<div class="script"><h4>"I'll think about it"</h4><p>"What would you need to see to say yes?" Address that point now. Then: "Can I block your seat for Rs 5,000? It's refundable within the policy, and it keeps today's price."</p></div>
<div class="script"><h4>"I'll pay after salary / next month"</h4><p>It's the new month now. Remind them gently of what they said. Offer EMI so the first instalment fits this month, or a token now and the balance on salary day.</p></div>
<div class="script"><h4>"Will I get a job / clients?"</h4><p>Share 2 alumni outcomes from a similar background (freelancing, in-house, board roles). Explain the career support part of the program concretely.</p></div>
<div class="script"><h4>"No credit card / loan rejected"</h4><p>Offer the alternatives: NBFC no-cost EMI, debit card EMI, a 2–3 part split approved by the team leader, or a token now.</p></div>
<div class="script"><h4>"The course I asked about isn't available"</h4><p>Don't end the call. Recommend the nearest current program, or the next batch date with a seat block. Several leads were lost this way last week.</p></div>
<div class="script"><h4>"I'm busy, call later"</h4><p>"Sure, what time exactly works today?" Log it and call at that time. Never accept "later" without a time.</p></div>
</div>
</section>

<section>
<h2>8. WhatsApp messages</h2>
<p>Personalise the bracketed parts. Send from the official business number.</p>
<div class="script"><h4>A1 – Tier A, unanswered</h4><p class="mono">Hi [Name], [Caller] from LawSikho. As discussed about [program], here is your payment link: [link]. EMI options: [3/6/12 months: amounts]. Your seat at the current fee is held until [today 7 pm]. I'll call you at [time]. Reply here if another time suits you better.</p></div>
<div class="script"><h4>B1 – Tier B, unanswered</h4><p class="mono">Hi [Name], [Caller] from LawSikho. Following up on our chat about [program]. Fee [₹], or EMI from [₹/month]. Next batch starts [date]. Can I call you at [time] today to answer any questions?</p></div>
<div class="script"><h4>F1 – New lead, unanswered</h4><p class="mono">Hi [Name], this is [Caller] from LawSikho. You recently enquired about [course/area] on our website. When is a good time for a 5-minute call today? Meanwhile, here is a short overview: [link].</p></div>
<div class="script"><h4>R1 – Revive closure-stage</h4><p class="mono">Hi [Name], [Caller] from LawSikho. We spoke a few weeks ago about [program]. The current batch and offer close on [date]. Are you still planning to join? Happy to help with EMI or any questions.</p></div>
<div class="script"><h4>C1 – Nurture</h4><p class="mono">Hi [Name], [Caller] from LawSikho. Sharing a quick update on [program]: [one-line hook, e.g. upcoming free masterclass on (date)]. Would you like me to reserve a spot?</p></div>
</section>

<section>
<h2>9. Checkpoints and monitoring</h2>
<p>Open the <b>Team summary</b> tab. Each row updates as callers log attempts.</p>
<table><thead><tr><th>Time</th><th>Look at</th><th>Must be true</th><th>If not, do this</th></tr></thead><tbody>
<tr><td class="t">12:00</td><td>"Tier A not started"</td><td>0 for every caller</td><td>Sit with that caller; dial the A list together now.</td></tr>
<tr><td class="t">12:00</td><td>Missed calls</td><td>Every missed call from a lead returned</td><td>Return it yourself or assign it immediately.</td></tr>
<tr><td class="t">15:00</td><td>"Attempts logged" vs B count; "Enrolled so far"</td><td>Every B attempted once; at least 1 payment link or enrollment per caller</td><td>Find out why: failing calls (switch line), a slow caller (pair them up), or stuck on low tiers (redirect).</td></tr>
<tr><td class="t">18:00</td><td>Each caller's A rows</td><td>Every A attempted 3 times; every F twice</td><td>Reassign unattempted A leads to whoever is free.</td></tr>
<tr><td class="t">19:30</td><td>"Leads not started"; Next step column</td><td>0 not started; every connected call has a next-step date and time</td><td>Unfinished leads go to the top of Friday's list.</td></tr>
</tbody></table>
<h3>Quality spot-checks (30 minutes across the day)</h3>
<ul>
  <li>Listen to 2 calls per caller (live or recording). Score them on: discovery done, program matched, fee answered on the call, commitment asked for, dated next step.</li>
  <li>Check 5 random "Not Interested" or "Call Not Picking Up" stage changes per caller against the call log.</li>
</ul>
</section>

<section>
<h2>10. End of day and carry-forward</h2>
<ol>
  <li><b>19:30 review (10 min, whole team):</b> enrollments, tokens and links per caller; the A leads not closed and why; tomorrow's first calls.</li>
  <li><b>Carry-forward rule:</b> every A or B not closed, and every lead with a promised callback, goes to the top of Friday's sheet with the new note.</li>
  <li><b>LeadSquared hygiene:</b> every lead touched today has the correct stage and a follow-up date. Follow Up For Closure is only for leads spoken to in the last 7 days.</li>
  <li><b>Report to management:</b> enrollments and revenue per caller, payment links outstanding, the top blockers seen today.</li>
  <li>The lists are regenerated from fresh LeadSquared data each morning, so Friday's sheets will include tonight's updates.</li>
</ol>
</section>

<section>
<h2>11. Stages to fix, month-end leads, escalations</h2>
<h3>Leads marked "Not Interested" that are live ({len(ni)})</h3>
<p>These are Tier A or B tomorrow because recent calls show real interest (they still answer, called us, or discussed fees). Confirm on the call and correct the stage.</p>
<table><thead><tr><th>Caller</th><th>Lead</th><th>Tier</th><th>Evidence</th></tr></thead><tbody>{ni_rows}</tbody></table>
<h3>Month-end promises now due ({len(month_end)})</h3>
<p>Leads who said in late September that they would decide or pay in the new month, after salary, or after travel. Most "salary" mentions turned out to be HR-bootcamp talk about expected salary, and most other deferrals were pushed to November–January. These {len(month_end)} are the real ones.</p>
<table><thead><tr><th>Caller</th><th>Lead</th><th>What they said / why now</th></tr></thead><tbody>{me_rows}</tbody></table>
<h3>What only the team leader can unblock</h3>
<ul>
  <li><b>3-way calls</b> with parents or spouses for leads waiting on family approval.</li>
  <li><b>Payment exceptions:</b> split payments, card or NBFC EMI alternatives when finance is refused (age limits, card limits), and the invoice discrepancy on Aanniee Roy (Suhail), to be resolved before noon.</li>
  <li><b>Routing:</b> enrolled students raising support issues go to student support, not sales.</li>
  <li><b>Telephony:</b> raise call failures with the telephony team first thing.</li>
</ul>
</section>

<section>
<h2>12. Caller-wise call lists</h2>
<p>One section per caller. Tier A and B leads are listed in full and in call order, with the reason, the opening line and the ask. New, revive and nurture leads are named; their full details, and phone numbers for every lead, are in the caller's sheet in the workbook. Flags: <span class="chip bad">Missed call from lead</span> return first · <span class="chip warn">Month-end promise due</span> · <span class="chip warn">Wed evening callback missed</span> was due Wednesday evening and not completed – do it first · <span class="chip good">Updated from Wed night calls</span> re-judged using what the lead said on Wednesday evening · <span class="chip bad">Staged Not Interested – fix</span>.</p>
</section>
{''.join(caller_section(o, P[o]) for o in owners)}

<section>
<h2>Appendix – notes on the data</h2>
<ul>
  <li>Lead owner and stage are as in LeadSquared at 01:30 IST on 8 Oct. Leads moved to the team after that are not on the sheets.</li>
  <li>"Likely tomorrow" takes about 60% of each Tier A lead's 3-day chance, 35% for Tier B and 30% for the rest. The 3-day pipeline is the sum of the estimated chances. Both are judgement-based estimates, not guarantees.</li>
  <li>Wednesday-evening calls are included. Callbacks booked for Wednesday evening that didn't happen or weren't answered are flagged "Wed evening callback missed". Leads whose situation changed on Wednesday evening were re-judged on the new conversation.</li>
  <li>Revenue isn't recorded in LeadSquared, so these lists target enrollments. Please share the payment source so revenue per caller can be tracked.</li>
</ul>
</section>
</body></html>"""
    open(out_path, "w").write(doc)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
