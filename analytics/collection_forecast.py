"""Pending collection leads per team, most to least likely to pay, with a month-end forecast for setting targets.

Built from the collection audit after a round-2 reading (``analytics.collection_audit`` with transcripts), so
every open lead has its objection, where it is stuck and Claude's judgement of how likely it is to pay by month
end, read from its LeadSquared history, Zipteams summaries and call transcripts. Fresh names come from
``scripts/fetch_collection_contacts.py``; a lead paid or dropped since leaves the list.

The forecast puts two views side by side and never blends them:

* **Past rates:** the sum of each lead's chance of paying by month end, measured on past leads in the same
  position (``chance_21d``), with a 90% range.
* **Reading:** how many leads were judged very likely or likely to pay by month end. A count of judgements,
  not a probability.

New bookings still to come this month are estimated apart (``report.json`` "new_bookings").

    python -m analytics.collection_forecast exports/collection_audit data/coll_eve/contacts.json exports/collection_forecast
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict

from analytics.collection_audit import MONTH_END, STUCK_AT, simulate
from analytics.collection_plan import (BOOTCAMP_BALANCE, CLOSED, EXTRA_CSS, TEAM_LEADERS, TEAM_TITLE, _f, clean_name, esc,
                                       render_pdf)
from scripts.build_plan_pdf import CSS

HORIZON = "chance_21d"
BAND_CLASS = {"Very likely": "A", "Likely": "B", "Possible": "F", "Unlikely": "R", "Very unlikely": "C"}
FORECAST_CSS = """
.band-h { margin-top: 14pt; padding: 5pt 9pt; border-radius: 4pt; color: #fff; background: #1f4e46; font-weight: bold; }
.leads td.num { width: 16pt; } .leads td.lead { width: 21%; } .leads td:nth-child(3) { width: 24%; }
.leads td:nth-child(4) { width: 20%; } .leads td:nth-child(6) { width: 7%; text-align: center; }
.tier-A td { background: #f1f8f4; } .tier-B td { background: #f4f7fc; } .tier-F td { background: #fbf8f0; }
"""


def order_key(r: dict) -> tuple:
    """Month-end band first, then how close the lead is to paying, then the past-rate chance."""
    return (MONTH_END.index(r["month_end"]), STUCK_AT.index(r["stuck_at"]), -(_f(r.get(HORIZON)) or 0))


def load_rows(audit_dir: str, contacts: dict[str, dict]) -> tuple[list[dict], Counter]:
    rows, gone = [], Counter()
    for r in csv.DictReader(open(os.path.join(audit_dir, "open_leads.csv"), encoding="utf-8")):
        if r.get("month_end") not in MONTH_END:
            sys.exit("Some open leads have no round-2 reading yet: finish the transcript reading round first.")
        c = contacts.get(r["lead_id"], {})
        if (c.get("stage") or r["stage"]) in CLOSED:
            gone[c.get("stage")] += 1
            continue
        for k in ("chance_21d", "chance_21d_low", "chance_21d_high"):
            r[k] = _f(r.get(k)) or 0.0
        rows.append({**r, "name": clean_name(c.get("name") or ""), "stage": c.get("stage") or r["stage"]})
    return rows, gone


def forecast(rows: list[dict]) -> dict:
    exp, lo, hi = simulate(rows, HORIZON)
    bands = Counter(r["month_end"] for r in rows)
    return {"open": len(rows), "bands": bands, "expected": exp, "low": lo, "high": hi,
            "reading": bands["Very likely"] + bands["Likely"]}


def _band_row(label: str, f: dict, pool: dict | None) -> str:
    b = f["bands"]
    cells = "".join(f"<td>{b[m]}</td>" for m in MONTH_END)
    extra = ""
    if pool:
        n, done = pool["pool"], pool["collected"]
        extra = (f"<td>{done} of {n} ({100 * done / n:.0f}%)</td>"
                 f"<td>{100 * (done + f['expected']) / n:.0f}% ({100 * (done + f['low']) / n:.0f}–{100 * (done + f['high']) / n:.0f}%)</td>"
                 f"<td>{100 * (done + f['reading']) / n:.0f}%</td>")
    return (f"<tr><td><b>{esc(label)}</b></td><td>{f['open']}</td>{cells}<td><b>{f['expected']:.0f}</b> ({f['low']}–{f['high']})</td>"
            f"<td><b>{f['reading']}</b></td>{extra}</tr>")


def lead_table(rows: list[dict]) -> str:
    out = []
    for band in MONTH_END:
        group = sorted((r for r in rows if r["month_end"] == band), key=order_key)
        if not group:
            continue
        out.append(f'<div class="band-h">{esc(band)} to pay by 31 Oct · {len(group)} leads</div>'
                   '<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>Where it is stuck</th><th>Objection</th>'
                   '<th>Why, and the next step</th><th>Past-rate chance by 31 Oct</th></tr></thead><tbody>')
        for i, r in enumerate(group, 1):
            out.append(f"""<tr class="tier-{BAND_CLASS[band]}"><td class="num">{i}</td>
<td class="lead"><b>{esc(r['name']) or 'Unnamed lead'}</b><div class="sub">{esc(r['caller'])}</div>
<div class="sub">{esc(r['kind'])} · {esc(r['course'], 28)} · {round(_f(r['days_open']) or 0)} days · {esc(r['stage'])}</div></td>
<td><b>{esc(r['stuck_at'])}</b><div class="sub">{esc(r.get('situation'), 230)}</div></td>
<td>{esc(r.get('objection'), 200)}</td>
<td>{esc(r.get('month_end_reason'), 220)}<div class="sub"><b>Next:</b> {esc(r.get('next_action'), 200)}</div></td>
<td>{100 * r[HORIZON]:.0f}%</td></tr>""")
        out.append("</tbody></table>")
    return "".join(out)


def build_html(team: str, rows: list[dict], report: dict, as_of: str, gone: int) -> str:
    title, leader = TEAM_TITLE.get(team, team), TEAM_LEADERS.get(team, "the team leader")
    pools = {x["kind"]: x for x in report["by_kind_team"] if x["team"] == team}
    kinds = [k for k in ("Bootcamp", "Community") if any(r["kind"] == k for r in rows)]
    f_all = forecast(rows)
    f_kind = {k: forecast([r for r in rows if r["kind"] == k]) for k in kinds}
    new = {x["kind"]: x for x in report.get("new_bookings", []) if x["team"] == team}
    stuck = Counter(r["stuck_at"] for r in rows)
    blockers = Counter(r["blocker"] for r in rows).most_common(8)
    lo_r, hi_r = BOOTCAMP_BALANCE
    fb = f_kind.get("Bootcamp")
    rupees = (f"<p><b>In rupees (estimate):</b> at the bootcamp balance of about ₹{lo_r // 1000}–{hi_r // 1000}k, the "
              f"{fb['expected']:.0f} bootcamp collections expected at past rates are about ₹{fb['low'] * lo_r / 1e5:.1f}–"
              f"{fb['high'] * hi_r / 1e5:.1f} lakh; the {fb['reading']} judged very likely or likely would be about "
              f"₹{fb['reading'] * lo_r / 1e5:.1f}–{fb['reading'] * hi_r / 1e5:.1f} lakh. Community balances are not in "
              "LeadSquared, so community is counted in leads.</p>") if fb else ""
    by_caller = defaultdict(list)
    for r in rows:
        by_caller[r["caller"]].append(r)
    caller_rows = []
    for c in sorted(by_caller, key=lambda c: -len(by_caller[c])):
        f = forecast(by_caller[c])
        caller_rows.append(f"<tr><td><b>{esc(c)}</b></td><td>{f['open']}</td>"
                           + "".join(f"<td>{f['bands'][m]}</td>" for m in MONTH_END)
                           + f"<td>{f['expected']:.1f}</td><td>{f['reading']}</td>"
                           f"<td><b>{round(f['expected'])}–{max(round(f['expected']), f['reading'])}</b></td></tr>")
    mean_by_band = {m: [r[HORIZON] for r in rows if r["month_end"] == m] for m in MONTH_END}
    consistency = ", ".join(f"{m.lower()} {100 * sum(v) / len(v):.0f}%" for m, v in mean_by_band.items() if v)
    new_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{', '.join(map(str, new[k]['weekly_bookings']))}</td><td><b>{new[k]['expected']:.0f}</b> "
        f"({new[k]['range']})</td><td class='note'>{esc('; '.join(new[k]['weeks']))}</td></tr>" for k in kinds if k in new)
    targets = []
    for k in kinds:
        f, p = f_kind[k], pools.get(k)
        nb = new.get(k, {}).get("expected", 0.0)
        if not p:
            continue
        targets.append(f"<tr><td><b>{esc(k)}</b></td><td>{p['collected']} of {p['pool']}</td>"
                       f"<td>{f['low']}</td><td><b>{f['expected']:.0f}</b></td><td><b>{f['reading']}</b></td>"
                       f"<td>{nb:.0f}</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)} – pending collections and month-end forecast</title>
<style>{CSS}{EXTRA_CSS}{FORECAST_CSS}</style></head><body>
<div class="cover">
  <div class="band">
    <div class="eyebrow">{esc(title)} · for {esc(leader)}</div>
    <h1>Pending collections<br>and the month-end forecast</h1>
    <p>Every open collection lead booked since 1 September, from the ones that can be collected now to the least likely,
    with each lead's objection and where it is stuck, and how many are likely to pay by 31 October.</p>
  </div>
  <div>
    <div class="kpis">
      <div><b>{f_all['open']}</b><span>pending collection leads</span></div>
      <div><b>{f_all['bands']['Very likely']}</b><span>very likely to pay by 31 Oct (from their conversations)</span></div>
      <div><b>{f_all['bands']['Likely']}</b><span>likely to pay by 31 Oct</span></div>
      <div><b>≈{f_all['expected']:.0f}</b><span>expected to pay by 31 Oct at past rates ({f_all['low']}–{f_all['high']})</span></div>
      <div><b>{f_all['reading']}</b><span>would pay if every very likely and likely lead closes</span></div>
      <div><b>{f_all['bands']['Unlikely'] + f_all['bands']['Very unlikely']}</b><span>unlikely or very unlikely this month</span></div>
    </div>
    <h3>Contents</h3>
    <ol class="toc"><li>The month-end forecast and targets</li><li>Where leads are stuck and what they object to</li>
    <li>Caller-wise forecast</li><li>Every pending lead, most to least likely</li></ol>
    <p class="note">Read from: LeadSquared stages, stage notes, forms, calls and owner changes as of {esc(as_of)} IST; Zipteams
    summaries; and up to three recent call transcripts per lead from the transcript system. Stages re-checked just before
    building ({gone} lead(s) paid or dropped since and left the list). Collected means the stage reached Collections done or
    Course Enrolled; LeadSquared holds no payment amounts yet.</p>
  </div>
</div>

<section><h2>1. The month-end forecast and targets</h2>
<p>Two independent views. <b>Past rates</b>: what usually happens to leads in the same position (bootcamp or community,
days since booking, stage, last real conversation), from June–August leads, with a 90% range. <b>Reading</b>: how many
leads were judged very likely or likely to pay by 31 October from their own words in calls and notes; a count of
judgements, not a probability.</p>
<table><thead><tr><th></th><th>Open</th>{''.join(f"<th>{esc(m)}</th>" for m in MONTH_END)}<th>Past rates: expected by 31 Oct</th>
<th>Reading: very likely + likely</th><th>Collected so far</th><th>Projected % at past rates</th><th>% if all likely pay</th></tr></thead>
<tbody>{''.join(_band_row(k, f_kind[k], pools.get(k)) for k in kinds)}</tbody></table>
<h3>For setting targets</h3>
<table><thead><tr><th></th><th>Collected so far (pool since 1 Sep)</th><th>Floor: past rates, low end</th>
<th>Expected: past rates</th><th>Stretch: every likely lead pays</th><th>Plus new bookings by 31 Oct (estimate)</th></tr></thead>
<tbody>{''.join(targets)}</tbody></table>
<div class="warnbox"><p><b>How to read this.</b> A target at the past-rate figure is what normally happens with the team working
as it has. The stretch figure needs every lead judged very likely or likely to close; it is reachable only if the plan
is worked (calls returned, loan files chased daily, dated next steps). A fair target sits between the two. New bookings
add to both; they are estimated from September's weekly bookings and how fast past bookings paid.</p></div>
<p class="note">Check on the two views: the average past-rate chance of the leads in each reading band is {esc(consistency)}.</p>
{rupees}
<h3>New bookings still to come this month (estimate)</h3>
<table><thead><tr><th></th><th>Bookings per week, last 5 weeks</th><th>Expected to pay by 31 Oct</th><th>How</th></tr></thead>
<tbody>{new_rows}</tbody></table></section>

<section><h2>2. Where leads are stuck and what they object to</h2>
<div class="two"><div><h3>Where it is stuck</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{stuck[k]}</td></tr>" for k in STUCK_AT if stuck[k])}</tbody></table></div>
<div><h3>Main blocker</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in blockers)}</tbody></table></div></div>
<p>Each lead's own objection, in plain words, is in the list in section 4.</p></section>

<section><h2>3. Caller-wise forecast</h2>
<table><thead><tr><th>Caller</th><th>Open</th>{''.join(f"<th>{esc(m)}</th>" for m in MONTH_END)}<th>Past rates: expected by 31 Oct</th>
<th>Reading: very likely + likely</th><th>Target range (expected to stretch)</th></tr></thead><tbody>{''.join(caller_rows)}</tbody></table>
<p class="note">Callers with few leads have wide ranges; judge them on the leads, not the number.</p></section>

<section><h2>4. Every pending lead, most to least likely</h2>
<p>Grouped by how likely the lead is to pay by 31 October. Within each group, leads closest to paying come first (may have paid,
link sent, a promised date, loan approval, loan documents, choosing how to pay, deciding, not counselled, unreachable, deferring),
then by past-rate chance.</p>
{lead_table(rows)}</section>
</body></html>"""


def main(audit_dir: str, contacts_path: str, out_dir: str) -> None:
    report = json.load(open(os.path.join(audit_dir, "report.json")))
    contacts = json.load(open(contacts_path))["leads"]
    rows, gone = load_rows(audit_dir, contacts)
    os.makedirs(out_dir, exist_ok=True)
    for team in TEAM_LEADERS:
        team_rows = [r for r in rows if r["team"] == team]
        if not team_rows:
            continue
        slug = "Elite" if team.startswith("Elite") else "Puja_Malik"
        page = os.path.join(out_dir, f"{slug}_pending_collections.html")
        open(page, "w", encoding="utf-8").write(build_html(team, team_rows, report, report["data_as_of_ist"], sum(gone.values())))
        pdf = page.replace(".html", ".pdf")
        made = render_pdf(page, pdf)
        f = forecast(team_rows)
        print(f"{team}: {f['open']} open, {dict(f['bands'])}, past rates {f['expected']:.1f} ({f['low']}-{f['high']}), "
              f"reading {f['reading']} -> {pdf if made else page}")
    if gone:
        print(f"Left the list since the audit: {dict(gone)}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
