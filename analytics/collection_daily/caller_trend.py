"""Callers who fall short over a period (asked 26 Nov 2026), as a PDF for both collection teams: who calls too few
collection leads, who misses follow-ups, and who does not call back leads whose calls we missed. One set of numbers
for the whole period (the user, 26 Nov: no week-by-week split).

Per caller (the lead's current owner for follow-ups and missed calls; the person who dialled for calls):
- calls a day on collection leads, and different leads called a day, on the days the caller made calls; short when
  below 70% of the team's middle caller (the lagging rule in CLAUDE.md);
- follow-ups: the next call times saved in LeadSquared after a talk that fell due in the period, and how many were
  made on time (a call from 15 minutes before to 2 hours after); short when under half;
- next call time saved after a talk: a caller who saves it after under 1 in 10 real talks has no follow-ups the system
  can check;
- missed calls: a lead called and we did not answer; called back the same day by anyone, or not; short when under half.
A caller with half or more of their calls ending in CallFailure has a dialer problem and is not judged on calls.
Days a caller is on leave (``data/coll_daily/leave.json``: {"name": [["first day", "last day"], ...]}) are left out of
their numbers, and a caller with fewer than 5 working days in the period is not judged.

    python -m analytics.collection_daily.caller_trend <run_dir> <first day YYYY-MM-DD> <last day YYYY-MM-DD> <out.pdf>
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from analytics.collection_daily import facts
from analytics.collection_daily.facts import SIDES
from analytics.collection_daily.pdf import EXTRA_CSS, TEAM_ADS, table
from analytics.collection_reports import CSS, e, write_pdf
from integrations.timeutil import IST

LAG = 0.7               # below 70% of the team's middle caller (CLAUDE.md)
MIN_DUE, KEPT_BAR = 5, 0.5
MIN_MISSED, RETURN_BAR = 5, 0.5
MIN_TALKS, SAVE_BAR = 20, 0.1
MIN_DAYS = 5            # fewer working days than this in the period: too little to judge
LEAVE = "data/coll_daily/leave.json"


def load_leave(path: str = LEAVE) -> dict[str, list[tuple[date, date]]]:
    """caller -> leave periods (inclusive), from a git-ignored file; empty when there is none."""
    if not os.path.exists(path):
        return {}
    return {c: [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in v] for c, v in json.load(open(path)).items()}


def on_leave(leave: dict, caller: str, day: date) -> bool:
    return any(a <= day <= b for a, b in leave.get(caller, []))
PAGE_CSS = """
body { font-size:9.6px; line-height:1.4; } h2 { font-size:12.5px; margin:10px 0 4px; } table { font-size:8.8px; }
td, th { padding:3px 5px; } tr { page-break-inside: avoid; } h2 { page-break-after: avoid; }
ul.notes { margin:2px 0 2px 16px; padding:0; color:#52514e; font-size:8.4px; } ul.short { margin:2px 0 4px 16px; } ul.short li { margin:2px 0; }
.amber { padding:5px 9px; margin:4px 0; } .band { padding:10px 14px; margin-bottom:6px; } .band h1 { font-size:17px; }
"""


def day_of(t: datetime) -> date:
    return t.astimezone(IST).date()


def period_stats(leads: list[dict], first: date, last: date, leave: dict | None = None) -> dict[str, Counter]:
    """caller -> counts between ``first`` and ``last`` (IST days, inclusive) on these leads, leaving out each caller's
    days on leave (their leads' follow-ups and missed calls on those days are not held against them)."""
    leave = leave or {}
    callers = {l["caller"] for l in leads}
    out: dict[str, Counter] = {c: Counter() for c in callers}
    called: dict[tuple, set] = defaultdict(set)  # (caller, day) -> leads dialled
    for c in callers:
        out[c]["leave_days"] = sum(1 for k in range((last - first).days + 1) if on_leave(leave, c, first + timedelta(days=k)))
    working = lambda who, t: first <= day_of(t) <= last and not on_leave(leave, who, day_of(t))
    for l in leads:
        for c in l["calls"]:
            if c["by"] not in callers or not working(c["by"], c["t"]):
                continue
            s = out[c["by"]]
            if c["out"]:
                s["dials"] += 1
                s["failed"] += (c.get("status") or "") == "CallFailure"
                s["answered"] += c["answered"]
                called[(c["by"], day_of(c["t"]))].add(l["lead_id"])
            if c["answered"] and c["dur"] >= facts.REAL_SECS:
                s["real"] += 1
                # the next call time saved in LeadSquared within 30 minutes of this talk
                s["talks_saved"] += any(timedelta(0) <= f["set_at"] - c["t"] <= facts.FORM_AFTER_CALL for f in l["follow_ups"])
        s = out[l["caller"]]
        for f in l["follow_ups"]:
            if working(l["caller"], f["set_at"]):
                s["fu_saved"] += 1
            if working(l["caller"], f["due"]) and f["kept"] is not None:
                s["fu_due"] += 1
                s["fu_kept"] += bool(f["kept"])
        for c in l["calls"]:
            if c["out"] or c["answered"] or not working(l["caller"], c["t"]):
                continue
            s["missed"] += 1
            s["returned"] += any(d["out"] and d["t"] > c["t"] and day_of(d["t"]) == day_of(c["t"]) for d in l["calls"])
    for (c, _), ids in called.items():
        out[c]["days"] += 1
        out[c]["lead_days"] += len(ids)
    return out


def per_day(s: Counter) -> tuple[float, float]:
    """(calls a day, different leads called a day) on the days with calls."""
    return (s["dials"] / s["days"], s["lead_days"] / s["days"]) if s["days"] else (0.0, 0.0)


def shortfalls(s: Counter, median: float, median_leads: float) -> list[str]:
    """Plain sentences for each measure where the caller falls short over the period (empty when none)."""
    if s["leave_days"] and s["days"] < MIN_DAYS:
        return []  # on leave for most of the period: too few working days to judge
    if s["dials"] and s["failed"] * 2 >= s["dials"]:
        return [f"Dialer problem: {s['failed']} of {s['dials']} calls failed. Fix the dialer before judging the caller."]
    out = []
    calls, leads = per_day(s)
    if s["days"] and median and calls < LAG * median:
        out.append(f"Made {calls:.0f} calls a day to {leads:.0f} different leads; the team's middle caller made {median:.0f} calls "
                   f"to {median_leads:.0f} leads.")
    if s["fu_due"] >= MIN_DUE and s["fu_kept"] < KEPT_BAR * s["fu_due"]:
        out.append(f"Made {s['fu_kept']} of {s['fu_due']} follow-ups at the time saved in LeadSquared.")
    if s["missed"] >= MIN_MISSED and s["returned"] < RETURN_BAR * s["missed"]:
        out.append(f"Called back {s['returned']} of {s['missed']} missed calls from leads the same day.")
    return out


def saves_none(s: Counter) -> bool:
    """Saves the next call time after under 1 in 10 real talks (with enough talks to tell): no follow-ups to check."""
    return s["real"] >= MIN_TALKS and s["talks_saved"] < SAVE_BAR * s["real"]


def review(leads: list[dict], first: date, last: date, leave: dict | None = None) -> dict:
    """side -> {"median": (calls, leads), "rows": caller -> {"tl", "s", "short"}}."""
    result = {}
    for side in SIDES:
        ls = [l for l in leads if l["side"] == side]
        tl = {l["caller"]: l["tl"] for l in ls}
        st = period_stats(ls, first, last, leave)
        fair = [per_day(s) for s in st.values() if s["days"] >= MIN_DAYS and s["failed"] * 2 < s["dials"]]
        med = (statistics.median(x for x, _ in fair), statistics.median(y for _, y in fair)) if fair else (0.0, 0.0)
        result[side] = {"median": med, "rows": {c: {"tl": tl[c], "s": s, "short": shortfalls(s, *med)} for c, s in st.items()}}
    return result


def html(result: dict, first: date, last: date, cut: datetime) -> str:
    pages = []
    for side, r in result.items():
        rows, (mc, ml) = r["rows"], r["median"]
        short = sorted(((c, x) for c, x in rows.items() if x["short"]), key=lambda kv: (-len(kv[1]["short"]), kv[1]["tl"], kv[0]))
        nosave = sorted(c for c, x in rows.items() if saves_none(x["s"]))
        summary = "".join(f"<li><b>{e(c)}</b> <span class=muted>(TL {e(x['tl'])})</span><br>" + "<br>".join(e(y) for y in x["short"]) + "</li>"
                          for c, x in short) or "<li>No caller falls short on these measures.</li>"
        body = []
        for c, x in sorted(rows.items(), key=lambda kv: (kv[1]["tl"], kv[0])):
            s = x["s"]
            calls, leads = per_day(s)
            away = f'<br><span class="muted">on leave {s["leave_days"]} of these days</span>' if s["leave_days"] else ""
            body.append([f"<b>{e(c)}</b>{away}", e(x["tl"]), str(s["days"]), f"{calls:.0f}", f"{leads:.0f}",
                         f"{s['fu_kept']} of {s['fu_due']}" if s["fu_due"] else "none due",
                         f"{s['talks_saved']} of {s['real']}", f"{s['returned']} of {s['missed']}" if s["missed"] else "none",
                         '<span class="bad">Yes</span>' if x["short"] else ("On leave" if s["leave_days"] and s["days"] < MIN_DAYS else "No")])
        pages.append(
            f'<div class="page"><div class="band"><small>Collections · {e(TEAM_ADS[side])}</small>'
            f'<h1>Callers who fall short · {first:%d %b} to {last:%d %b %Y}</h1>'
            f'<p>Calls on this month\'s collection leads, from LeadSquared read {cut.astimezone(IST):%a %d %b %H:%M} IST.</p></div>'
            + (f'<div class="amber"><b>{len(nosave)} of {len(rows)} callers almost never save the next call time in LeadSquared after a '
               f'talk:</b> {e(", ".join(nosave))}. Without it, their follow-ups are not scheduled in the system and cannot be checked, so '
               'they are judged below on calls and missed calls only.</div>' if nosave else "")
            + f'<h2>Who needs attention ({len(short)} of {len(rows)} callers)</h2>'
              '<p class="muted">Fewer calls than the team, follow-ups not made on time, or missed calls from leads not called back.</p>'
              f'<ul class="short">{summary}</ul>'
            "<h2>Every caller over the two weeks</h2>"
            + table([("Caller", "15%"), ("Team leader", "13%"), ("Days with calls", "8%"), ("Calls a day", "8%"),
                     ("Different leads called a day", "10%"), ("Follow-ups made on time (of those due)", "12%"),
                     ("Talks after which the next call time was saved", "12%"), ("Missed calls from leads called back the same day", "13%"),
                     ("Needs attention", "9%")], body, {i: "n" for i in range(2, 8)})
            + '<ul class="notes">'
              f"<li>Calls a day and different leads called a day count only the days the caller made calls. The team's middle caller made "
              f"{mc:.0f} calls a day to {ml:.0f} different leads; a caller below 70% of that is listed.</li>"
              "<li>A follow-up is the next call time a caller saves in LeadSquared after talking to a lead. It counts as made on time when "
              "the lead is called between 15 minutes before and 2 hours after it. A caller who saves no next call times has no follow-ups to "
              "check, so the next column shows how often they save one.</li>"
              "<li>A talk is an answered call of 2 minutes or more. A missed call is a lead calling us and not being answered.</li>"
              "<li>Only calls to this month's collection leads are counted (the 24 Oct, 31 Oct, 14 Nov and 21 Nov bootcamps and November "
              "community bookings). A caller who also works older leads, or a team leader, will show fewer calls here.</li>"
              "<li>Follow-ups and missed calls count against the lead's current owner, except on the owner's days on leave, when other "
              "callers cover the leads. A caller with fewer than 5 working days in the period is not judged.</li></ul></div>")
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>Callers who fall short</title>'
            f"<style>{CSS}{EXTRA_CSS}{PAGE_CSS}</style></head><body>{''.join(pages)}</body></html>")


if __name__ == "__main__":
    run_dir, a, b, out = sys.argv[1:5]
    cut = facts.read_at(run_dir)
    first, last = date.fromisoformat(a), date.fromisoformat(b)
    res = review(facts.collect(run_dir, cut), first, last, load_leave())
    write_pdf(html(res, first, last, cut), out)
    for side, r in res.items():
        print(side, "median calls/leads a day: %.0f / %.0f" % r["median"])
        for c, x in sorted(r["rows"].items(), key=lambda kv: kv[0]):
            s = x["s"]
            print(f"  {c:26} days {s['days']:2} calls/day {per_day(s)[0]:4.0f} leads/day {per_day(s)[1]:4.0f} fu {s['fu_kept']}/{s['fu_due']} "
                  f"saved {s['talks_saved']}/{s['real']} missed {s['returned']}/{s['missed']} | {' '.join(x['short'])}")
    print(out)
