"""Nightly enrollment pipeline: every lead with a real chance of enrolling by month end, highest chance first.

Candidates are last night's pipeline, every lead with a real conversation today, and today's plan leads (P/M/A/B and the
team leader's list); leads already enrolled are dropped. Claude reads each candidate (docs/pipeline_brief.md) and the
report is built from those reads. Runbook: docs/daily_plan.md, section "Nightly pipeline"."""

from __future__ import annotations

import calendar
import glob
import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

from analytics.daily_plan import state
from analytics.daily_plan.common import CLOSED, REAL_SECS, Snap
from analytics.daily_plan.pdf import render

OVER_FORECAST = 2.4  # Fri 9 Oct: about 19 forecast, 8 enrolled on the agreed rule; the high end of the range applies it
WEEKS = 3


def horizon(today: str) -> str:
    """Month end; in the last 3 days of a month, the end of next month."""
    d = datetime.strptime(today, "%Y-%m-%d")
    end = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    if (end - d).days < 3:
        nxt = end + timedelta(days=1)
        end = nxt.replace(day=calendar.monthrange(nxt.year, nxt.month)[1])
    return end.strftime("%Y-%m-%d")


def candidates(snap: Snap, today: str, last_night: dict, plan: dict) -> set[str]:
    sel = set(last_night)
    sel |= {c["lead_id"] for c in snap.day(today) if c.get("user_id") in snap.users and c["status"] == "Answered" and c["duration"] >= REAL_SECS}
    sel |= {lid for lid, e in plan.items() if e.get("tier") in ("P", "M", "A", "B") or e.get("group") is not None}
    keep = set()
    for lid in sel:
        l = snap.leads.get(lid)
        if not l:
            continue
        bootcamp = (last_night.get(lid) or {}).get("bootcamp") or (plan.get(lid) or {}).get("bootcamp")
        if l.get("ProspectStage") in CLOSED and not bootcamp:
            continue
        keep.add(lid)
    return keep


def numbers(snap: Snap, lead_ids) -> list[str]:
    """Normalized phone numbers of the candidates (from their calls, else the lead record), for the transcript search."""
    from integrations.transcripts import normalize_phone

    out = []
    for lid in sorted(lead_ids):
        ph = next((snap.phone(c) for c in snap.by_lead.get(lid, []) if snap.phone(c)), None) or (snap.leads.get(lid) or {}).get("Phone")
        n = normalize_phone(ph)
        if n and n not in out:
            out.append(n)
    return out


def brief(template: str, today: str, end: str) -> str:
    t, h = datetime.strptime(today, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
    return (template.replace("{today_long}", t.strftime("%A %-d %b %Y")).replace("{horizon_long}", h.strftime("%A %-d %b %Y"))
            .replace("{horizon_short}", h.strftime("%-d %b")).replace("{days_left}", str((h - t).days)))


def load(reads_dir: str) -> list[dict]:
    seen = {}
    for f in sorted(glob.glob(os.path.join(reads_dir, "out_*.jsonl"))):
        for ln in open(f):
            if ln.strip():
                try:
                    r = json.loads(ln)
                    seen[r["lead_id"]] = r
                except (ValueError, KeyError):
                    pass
    return list(seen.values())


def included(rows: list[dict]) -> list[dict]:
    inc = [r for r in rows if r.get("include") and int(r.get("month_chance") or 0) >= 8]
    return sorted(inc, key=lambda r: (-int(r["month_chance"]), r.get("stage_reached") or ""))


def to_state(date: str, inc: list[dict]) -> dict:
    return {"date": date, "version": 1, "kind": "pipeline",
            "leads": {state.h(r["lead_id"]): {"owner": r.get("owner"), "month_chance": int(r["month_chance"]),
                                              "stage": (r.get("stage_reached") or "")[:1], "bootcamp": r.get("status") == "bootcamp"} for r in inc}}


def _ph(x) -> str:
    d = re.sub(r"\D", "", str(x or ""))
    return f"+91-{d[-10:]}" if len(d) >= 10 else ""


def _blocks(r) -> str:
    b = r.get("blockers")
    return "; ".join(b) if isinstance(b, list) else (b or "")


def _week(window: str, today: str) -> int:
    """0, 1 or 2+: how many weeks from today the expected window falls (read from its first 'd Mon' date)."""
    m = re.search(r"(\d{1,2})\s*([A-Z][a-z]{2})", window or "")
    t = datetime.strptime(today, "%Y-%m-%d")
    if not m:
        return WEEKS - 1
    try:
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {t.year}", "%d %b %Y")
    except ValueError:
        return WEEKS - 1
    return min(max((d - t).days, 0) // 7, WEEKS - 1)


def summary(inc: list[dict], today: str) -> dict:
    exp = sum(int(r["month_chance"]) for r in inc) / 100
    stages, callers, weeks = defaultdict(int), defaultdict(lambda: [0, 0, 0, 0, 0.0]), defaultdict(lambda: [0, 0.0])
    for r in inc:
        s = (r.get("stage_reached") or "3")[:1]
        stages[s] += 1
        c = callers[r.get("owner") or "?"]
        c[0] += 1
        c[{"1": 1, "2": 2}.get(s, 3)] += 1
        c[4] += int(r["month_chance"]) / 100
        w = weeks[_week(r.get("expected_window"), today)]
        w[0] += 1
        w[1] += int(r["month_chance"]) / 100
    return {"leads": len(inc), "expected_low": exp, "expected_high": exp * OVER_FORECAST, "stages": dict(stages),
            "callers": dict(callers), "weeks": dict(weeks)}


CSS = """@page { size: A4 landscape; margin: 10mm; @bottom-right { content: counter(page); } }
body { font-family: Arial, sans-serif; font-size: 9.5px; color: #1d2b28; } h1 { color:#fff; font-size: 24px; margin: 4px 0; }
h2 { color: #1F4E46; font-size: 15px; margin: 12px 0 6px; } .cover { background:#1F4E46; color:#fff; padding: 18px 22px; border-radius: 8px; }
.tiles { display:grid; grid-template-columns: repeat(4,1fr); gap:8px; margin: 12px 0; } .tile { border:1px solid #d6dcda; border-radius:6px; padding:10px; }
.tile b { font-size: 22px; color:#1F4E46; display:block; } table { border-collapse: collapse; width:100%; table-layout: fixed; }
th { background:#E8EFEC; text-align:left; color:#1F4E46; } th, td { border:1px solid #d6dcda; padding:4px 5px; vertical-align: top; word-wrap: break-word; }
tr { page-break-inside: avoid; } .ph { font-family: monospace; font-weight: bold; color:#1F4E46; white-space: nowrap; } .sub { color:#555; font-size: 8.5px; }
.s1 { background:#DCEFE6; } .s2 { background:#EEF6F1; } .s3 { background:#fff; } .small { color:#555; font-size: 8.5px; }
.up { color:#1F7A4D; font-weight:bold; } .down { color:#B03A2E; font-weight:bold; }"""


def html_doc(rows: list[dict], today: str, end: str, built: str, last_night: dict, team: str) -> str:
    e = html.escape
    inc = included(rows)
    S = summary(inc, today)
    h = datetime.strptime(end, "%Y-%m-%d")
    hs = h.strftime("%-d %b")
    t = datetime.strptime(today, "%Y-%m-%d")
    wk = [f"{(t + timedelta(days=7 * k)).strftime('%-d %b')}–{min(t + timedelta(days=7 * k + 6), h).strftime('%-d %b')}" for k in range(WEEKS)]
    wk[-1] = f"{(t + timedelta(days=7 * (WEEKS - 1))).strftime('%-d %b')}–{hs}"
    new = [r for r in inc if state.h(r["lead_id"]) not in last_night] if last_night else []
    gone = len(last_night) - sum(1 for r in inc if state.h(r["lead_id"]) in last_night) if last_night else 0
    H = [f"<!doctype html><html><head><meta charset='utf-8'><title>Pipeline to {e(hs)}</title><style>{CSS}</style></head><body>",
         f"<div class='cover'><div style='letter-spacing:.12em;font-size:10px'>{e(team.upper())} · NIGHTLY PIPELINE</div><h1>Enrollment pipeline to {e(h.strftime('%-d %B'))}</h1>"
         f"<div>Every lead with a real chance of enrolling by {e(hs)}, highest chance first. Built {e(built)} IST from LeadSquared calls, Zipteams notes and call transcripts; "
         "each lead read by Claude.</div></div>",
         "<div class='tiles'>" + "".join(f"<div class='tile'><b>{v}</b>{e(k)}</div>" for v, k in [
             (S["leads"], "leads in the pipeline" + (f" ({len(new)} new tonight, {gone} dropped since last night)" if last_night else "")),
             (f"{S['expected_low']:.0f}–{S['expected_high']:.0f}", f"expected enrollments by {hs} from these leads (estimate; see note)"),
             (S["stages"].get("1", 0), "at payment stage (link/EMI/documents moving)"),
             (S["stages"].get("2", 0), "agreed, one blocker left")]) + "</div>",
         "<h2>By caller</h2><table><tr><th>Caller</th><th>Leads</th><th>Payment in progress</th><th>Agreed, one blocker</th><th>Fee and plan discussed</th><th>Expected (estimate)</th></tr>"
         + "".join(f"<tr><td><b>{e(k)}</b></td><td>{v[0]}</td><td>{v[1]}</td><td>{v[2]}</td><td>{v[3]}</td><td>{v[4]:.1f}</td></tr>"
                   for k, v in sorted(S["callers"].items(), key=lambda kv: -kv[1][4])) + "</table>",
         "<h2>By expected week</h2><table><tr><th>Window</th><th>Leads</th><th>Expected (estimate)</th></tr>"
         + "".join(f"<tr><td>{e(wk[k])}</td><td>{S['weeks'].get(k, [0, 0])[0]}</td><td>{S['weeks'].get(k, [0, 0.0])[1]:.1f}</td></tr>" for k in range(WEEKS)) + "</table>",
         f"<p class='small'><b>About the estimate.</b> Chances are judgement-based estimates from each lead's calls and transcripts, not targets. The low end is the sum of the "
         f"per-lead chances; the high end multiplies it by {OVER_FORECAST} (on Fri 9 Oct the plan forecast about 19 and 8 enrolled, so per-lead chances may run low). "
         "Only leads whose conversation reached fee, payment or a decision are here; new leads who enroll fast add to it. Leads already enrolled (Course Enrolled, or said on a "
         "call they paid, including part-payers with a balance) are left out; Rs 10 bootcamp registrations are judged as prospects.</p>",
         f"<h2 style='page-break-before:always'>The pipeline, highest chance first</h2><table><tr><th style='width:3%'>#</th><th style='width:15%'>Lead · caller</th>"
         "<th style='width:13%'>Course · fee</th><th style='width:9%'>Stage · chance · when</th><th style='width:24%'>Where it stands</th>"
         "<th style='width:17%'>Possible blocks</th><th style='width:19%'>Next step</th></tr>"]
    for i, r in enumerate(inc, 1):
        s = (r.get("stage_reached") or "3")[:1]
        prev = last_night.get(state.h(r["lead_id"])) if last_night else None
        delta = ""
        if last_night and not prev:
            delta = "<span class='up'>new</span>"
        elif prev and prev["month_chance"] != int(r["month_chance"]):
            up = int(r["month_chance"]) > prev["month_chance"]
            delta = f"<span class='{'up' if up else 'down'}'>{'▲' if up else '▼'} from {prev['month_chance']}%</span>"
        H.append(f"<tr class='s{s}'><td>{i}</td><td><b>{e(r.get('name') or 'Unnamed lead')}</b><br><span class='ph'>{e(_ph(r.get('phone')))}</span>"
                 f"<div class='sub'>{e(r.get('owner') or '')}<br>Decides: {e(r.get('who_decides') or '?')}</div></td>"
                 f"<td>{e(r.get('course') or '')}<div class='sub'>{e(r.get('fee_quoted') or '')}<br>Pays: {e(r.get('how_pay') or '?')}</div></td>"
                 f"<td><b>{int(r['month_chance'])}%</b> {delta}<div class='sub'>{e((r.get('stage_reached') or '')[2:])}<br>{e(r.get('expected_window') or '')}"
                 f"<br>Last talk: {e(r.get('last_real_conversation') or '?')}</div></td><td>{e(r.get('summary') or '')}</td>"
                 f"<td>{e(_blocks(r))}<div class='sub'>{e(r.get('risk_note') or '')}</div></td><td>{e(r.get('next_step') or '')}</td></tr>")
    H.append("</table>")
    reasons = defaultdict(int)
    for r in rows:
        if r in inc:
            continue
        x = (r.get("exclude_reason") or "").lower()
        k = ("already enrolled" if "enrol" in x else "later than the horizon" if re.search(r"nov|dec|jan|next month|next year|after exam|horizon", x)
             else "no fee/plan conversation yet" if re.search(r"discovery|no fee|not discussed|early|first", x)
             else "gone quiet / low interest" if re.search(r"quiet|silent|unreach|not reach|no answer", x) else "low chance or other")
        reasons[k] += 1
    H.append(f"<h2>Read but left out</h2><p>{len(rows) - len(inc)} of {len(rows)} leads read were left out: "
             + ", ".join(f"{v} {e(k)}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])) + ".</p></body></html>")
    return "\n".join(H)


def workbook(rows: list[dict], out: str) -> str:
    inc = included(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Pipeline"
    cols = [("#", 5), ("Chance by horizon", 10), ("Stage", 20), ("Expected window", 16), ("Lead", 22), ("Phone", 15), ("Caller", 18), ("Course", 28),
            ("Fee quoted", 22), ("Where it stands", 60), ("Possible blocks", 40), ("Who decides", 18), ("How they pay", 22), ("Last real conversation", 14),
            ("Next step", 45), ("Risk", 35), ("Outcome (fill)", 18), ("Enrolled on (fill)", 14), ("Lead ID", 36)]
    side = Side(style="thin", color="D0D5D2")
    border = Border(left=side, right=side, top=side, bottom=side)
    for j, (n, w) in enumerate(cols, 1):
        c = ws.cell(row=1, column=j, value=n)
        c.font, c.fill = Font(bold=True, color="FFFFFF", name="Arial", size=10), PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[L(j)].width = w
    fill = {"1": "DCEFE6", "2": "EEF6F1", "3": "FFFFFF"}
    for i, r in enumerate(inc, 1):
        vals = [i, int(r["month_chance"]) / 100, (r.get("stage_reached") or "")[2:], r.get("expected_window"), r.get("name"), _ph(r.get("phone")),
                r.get("owner"), r.get("course"), r.get("fee_quoted"), r.get("summary"), _blocks(r), r.get("who_decides"), r.get("how_pay"),
                r.get("last_real_conversation"), r.get("next_step"), r.get("risk_note"), None, None, r["lead_id"]]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=i + 1, column=j, value=v)
            c.font, c.alignment, c.border = Font(name="Arial", size=9, bold=j in (2, 5)), Alignment(wrap_text=True, vertical="top"), border
            c.fill = PatternFill("solid", fgColor="FFF4CC" if j in (17, 18) else fill.get((r.get("stage_reached") or "3")[:1], "FFFFFF"))
        ws.cell(row=i + 1, column=2).number_format = "0%"
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = f"A1:{L(len(cols))}{len(inc) + 1}"
    s2 = wb.create_sheet("By caller")
    for j, n in enumerate(["Caller", "Leads", "Expected (sum of chances)"], 1):
        c = s2.cell(row=1, column=j, value=n)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E46")
        s2.column_dimensions[L(j)].width = 24
    for k, name in enumerate(sorted({r.get("owner") or "?" for r in inc}), 2):
        s2.cell(row=k, column=1, value=name)
        s2.cell(row=k, column=2, value=f"=COUNTIF(Pipeline!G:G,A{k})")
        s2.cell(row=k, column=3, value=f"=SUMIF(Pipeline!G:G,A{k},Pipeline!B:B)").number_format = "0.0"
    wb.save(out)
    return out


def build(rows: list[dict], today: str, built: str, last_night: dict, out_dir: str, team: str) -> dict:
    end = horizon(today)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"Elite_pipeline_to_{datetime.strptime(end, '%Y-%m-%d'):%-d_%b}")
    render(html_doc(rows, today, end, built, last_night, team), stem + ".html", stem + ".pdf")
    workbook(rows, stem + ".xlsx")
    inc = included(rows)
    return {"pdf": stem + ".pdf", "xlsx": stem + ".xlsx", "horizon": end, "summary": summary(inc, today), "top": inc[:10]}
