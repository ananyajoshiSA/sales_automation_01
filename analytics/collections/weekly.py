"""One-page weekly bootcamp collection report per team member (collector): what each person booked, collected and still owes.

A student belongs to the week of the sheet's own date column (the day the booking or payment was entered), Monday to
Sunday IST. Money is rupees from the Bootcamp Pool sheet, joined to LeadSquared exactly as the full report does.
Reads files only.

    python -m analytics.collections.weekly data/collections/2026-11-02 exports/collections/2026-11-02 [week_start]
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta

from analytics.collections import render
from analytics.collections.__main__ import reports
from analytics.collections.report import group, summarise
from integrations.timeutil import now_ist

e, rs = render.e, render.rs


def last_week(today: date) -> date:
    """Monday of the last complete Mon-Sun week before ``today``."""
    return today - timedelta(days=today.weekday() + 7)


def in_week(rows: list[dict], start: date) -> list[dict]:
    lo, hi = start.isoformat(), (start + timedelta(days=6)).isoformat()
    return [r for r in rows if lo <= str(r.get("date") or "")[:10] <= hi]


def html_doc(R: dict, rows: list[dict], start: date) -> str:
    end = start + timedelta(days=6)
    T = R["total"]
    people = sorted(group(rows, "team", "owner"), key=lambda g: -g["received"])
    camps = group(rows, "bootcamp_raw")
    flagged = [r for r in rows if r.get("flag")]
    tiles = [(T["students"], "Students"), (rs(T["course_value"]), "Course value"), (rs(T["received"]), "Received"),
             (rs(T["balance_open"]), "Still to collect"), (f"{T['collected']} / {T['pending']} / {T['dropped']}",
                                                         "Collected / Pending / Dropped")]
    tiles_html = "".join(f"<div class=tile><b>{e(v)}</b><span>{e(l)}</span></div>" for v, l in tiles)
    pcols = [("owner", "Team member"), ("team", "Team leader"), ("students", "Students"), ("collected", "Collected"),
             ("pending", "Pending"), ("instalments", "Instal."), ("dropped", "Dropped"), ("received", "Received"),
             ("balance_open", "Still to collect")]
    tot = {"owner": "Total", "team": "", **T}
    ccols = [("bootcamp_raw", "Bootcamp"), ("students", "Students"), ("received", "Received"),
             ("balance_open", "Still to collect")]
    top = max((p for p in people if p["balance_open"] > 0), key=lambda p: p["balance_open"], default=None)
    lead = (f"{T['students']} bootcamp students were entered, {rs(T['received'])} received, {rs(T['balance_open'])} still to "
            f"collect. " + (f"Biggest balance to chase: {e(top['owner'])} ({rs(top['balance_open'])}, {top['pending']} pending)."
                            if top else ""))
    fl = ""
    if flagged:
        fl = "<h2>Sheet and LeadSquared disagree</h2>" + render._table(
            [{**r} for r in flagged[:5]], [("owner", "Team member"), ("name", "Student"), ("status", "Sheet status"),
                                            ("lsq_stage", "LeadSquared stage"), ("flag", "Why")])
    return (f"<html><head><meta charset=utf-8><style>{render.CSS}</style></head><body>"
            f"<h1>Bootcamp collections: weekly by team member</h1>"
            f"<div class=muted>{start:%a %d %b} to {end:%a %d %b %Y} (IST) &middot; data read {e(R.get('fetched_at_ist', ''))} "
            f"&middot; students grouped by the sheet's date column</div>"
            f"<div class=lead>{lead}</div><div class=tiles>{tiles_html}</div>"
            f"<h2>By team member</h2>{render._table(people + [tot], pcols, money_keys={'received', 'balance_open'})}"
            f"<h2>By bootcamp</h2>{render._table(camps, ccols, limit=8, money_keys={'received', 'balance_open'})}{fl}"
            f"<div class=muted>Pending = booking fee paid, balance not yet collected. Still to collect covers Pending and "
            f"Instalments students. Students the sheet marks Drop are excluded from it. Report built "
            f"{now_ist():%d %b %Y %H:%M} IST.</div></body></html>")


def main(data_dir: str, out_dir: str, week_start: str = "") -> str:
    start = date.fromisoformat(week_start) if week_start else last_week(now_ist().date())
    R = reports(data_dir, ("bootcamp",))["bootcamp"]
    rows = in_week(R["rows"], start)
    R["total"] = summarise(rows)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"Bootcamp_weekly_{start.isoformat()}")
    open(base + ".html", "w", encoding="utf-8").write(html_doc(R, rows, start))
    subprocess.run([render.chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(base + '.pdf')}", os.path.abspath(base + ".html")],
                   check=True, capture_output=True, timeout=300)
    print(f"{len(rows)} students in week from {start}", file=sys.stderr)
    return base + ".pdf"


if __name__ == "__main__":
    print(main(*sys.argv[1:4]))
