"""Callers to talk to, in plain words (asked 26 Nov 2026): for each collection team, the callers who fall short over a
period and why, one short paragraph each, with no averages. The user's format: "simply explain why I need to talk to
this caller".

A caller is listed for the three things asked about, over the working days (Monday to Friday) of the period, leaving
out days on leave (``data/coll_daily/leave.json``):
- missed calls: leads who called and were missed, not called back the same day 5+ times and in 40%+ of such calls;
- follow-ups (next call times saved in LeadSquared after a talk) made at the agreed time in under half of those due (5+);
- calling too few leads, with 10+ open leads: 3+ open leads not called for 3 days, 2+ working days with no or very few
  calls (under 5), or most working days with calls to under half of the open leads.
For a listed caller, new leads without a real talk within 48 hours (4+, under 85%) and never saving the next call time
after a talk are added; never saving is named once per team for the rest.
A caller with 10 or fewer open leads who calls nearly all of them is not judged on how many calls they make.

    python -m analytics.collection_daily.caller_talks <run_dir> <first day YYYY-MM-DD> <last day YYYY-MM-DD> <out.pdf>
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

from analytics.collection_daily import facts
from analytics.collection_daily.caller_trend import day_of, load_leave, on_leave
from analytics.collection_daily.facts import SIDES
from analytics.collection_daily.pdf import EXTRA_CSS, TEAM_ADS
from analytics.collection_reports import CSS, e, write_pdf
from integrations.timeutil import IST

FEW_CALLS = 5          # a working day with fewer calls than this counts as "very few"
BIG_BOOK = 10          # more open leads than this: judged on how many of them get called
PAGE_CSS = """
body { font-size:10.2px; line-height:1.5; } h2 { font-size:13px; margin:12px 0 4px; }
.who { border-left:4px solid #1F4E46; padding:4px 10px; margin:8px 0; page-break-inside: avoid; }
.who h3 { margin:0 0 2px; font-size:11px; } .who .good { color:#52514e; margin:0 0 2px; }
.who ul { margin:2px 0 0 16px; padding:0; } .who li { margin:2px 0; }
ul.notes { margin:2px 0 2px 16px; padding:0; color:#52514e; font-size:8.8px; }
"""


def days_text(ds: list[date]) -> str:
    names = [f"{d:%a %d %b}" for d in ds]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def caller_facts(caller: str, team: list[dict], first: date, last: date, leave: dict, cut: datetime) -> dict:
    """What happened on one caller's work between ``first`` and ``last``: calls by day, missed calls, follow-ups,
    48-hour deadlines, open leads and enrolments. Calls count on any of the team's leads; the rest on leads they own."""
    mine = [l for l in team if l["caller"] == caller]
    work = [first + timedelta(k) for k in range((last - first).days + 1)]
    work = [d for d in work if d.weekday() < 5 and not on_leave(leave, caller, d)]
    in_work = lambda t: day_of(t) in work
    calls, leads = defaultdict(int), defaultdict(set)
    talks = saved = 0
    for l in team:
        for c in l["calls"]:
            if c["by"] != caller or not first <= day_of(c["t"]) <= last or on_leave(leave, caller, day_of(c["t"])):
                continue
            if c["out"]:
                calls[day_of(c["t"])] += 1
                leads[day_of(c["t"])].add(l["lead_id"])
            if c["answered"] and c["dur"] >= facts.REAL_SECS:
                talks += 1
                saved += any(f.get("source", "lsq") == "lsq" and timedelta(0) <= f["set_at"] - c["t"] <= facts.FORM_AFTER_CALL for f in l["follow_ups"])
    missed = [(l, c) for l in mine for c in l["calls"] if not c["out"] and not c["answered"] and in_work(c["t"])]
    late = [(l, c) for l, c in missed if not any(d["out"] and d["t"] > c["t"] and day_of(d["t"]) == day_of(c["t"]) for d in l["calls"])]
    never = {l["lead_id"] for l, c in missed if not any(d["out"] and d["t"] > c["t"] for d in l["calls"])}
    due = [f for l in mine for f in l["follow_ups"] if in_work(f["due"]) and f["kept"] is not None]
    d48 = [l for l in mine if first <= day_of(l["deadline"]) <= last and not on_leave(leave, caller, day_of(l["deadline"]))]
    open_ = [l for l in mine if not l["enrolled"] and not l["dropped"]]
    since = day_of(cut) - timedelta(days=4)  # no call on the last 3 days before the data was read
    stale = [l for l in open_ if day_of(l["allocated"]) <= since and (not l["last_dial"] or day_of(l["last_dial"]) <= since)]
    return {"caller": caller, "tl": mine[0]["tl"] if mine else "", "work": work, "calls": calls, "leads": leads, "talks": talks,
            "saved": saved, "missed": len(missed), "late": len(late), "never": len(never), "due": len(due),
            "made": sum(1 for f in due if f["kept"]), "d48": len(d48), "met48": sum(l["met48"] for l in d48), "open": len(open_),
            "stale": len(stale), "since": since, "enrolled": sum(1 for l in mine if l["enrolled_at"] and first <= day_of(l["enrolled_at"]) <= last),
            "leave": any(on_leave(leave, caller, first + timedelta(k)) for k in range((last - first).days + 1))}


def problems(f: dict) -> list[str]:
    """Why to talk to this caller, one plain sentence each (empty: no talk needed)."""
    out = []
    if f["open"] > BIG_BOOK:
        none = [d for d in f["work"] if not f["calls"][d]]
        few = [d for d in f["work"] if 0 < f["calls"][d] < FEW_CALLS]
        if len(none) + len(few) >= 2:  # one quiet day may be a day off
            parts = ([f"no calls at all on {days_text(none)}"] if none else []) + \
                    ([f"very few calls on {days_text(few)} ({', '.join(str(f['calls'][d]) for d in few)})"] if few else [])
            out.append(f"Has {f['open']} open leads but made " + " and ".join(parts) + ".")
        thin = [d for d in f["work"] if f["calls"][d] and len(f["leads"][d]) * 2 < f["open"]]
        if len(thin) * 2 > len(f["work"]):
            n = sorted(len(f["leads"][d]) for d in thin)
            span = f"{n[0]}" if n[0] == n[-1] else f"{n[0]}–{n[-1]}"
            out.append(f"On {len(thin)} of {len(f['work'])} working days called only {span} of the {f['open']} open leads.")
        if f["stale"] >= 3:
            out.append(f"{f['stale']} open leads were not called on any of the last 3 days "
                       f"({f['since'] + timedelta(days=1):%d}–{f['since'] + timedelta(days=3):%d %b}).")
    if f["late"] >= 5 and f["late"] * 10 >= 4 * f["missed"]:
        out.append(f"When leads called and the call was missed, they were not called back the same day {f['late']} times (of {f['missed']})"
                   + (f"; {f['never']} of these leads {'were' if f['never'] != 1 else 'was'} never called back." if f["never"] else "."))
    if f["due"] >= 5 and f["made"] * 2 < f["due"]:
        out.append(f"Made only {f['made']} of {f['due']} follow-ups at the agreed time.")
    if out and f["d48"] - f["met48"] >= 4 and f["met48"] < 0.85 * f["d48"]:
        out.append(f"{f['d48'] - f['met48']} of {f['d48']} new leads did not get a real talk (2 minutes or more) within 48 hours.")
    if out and nosave(f):
        out.append(f"Does not save the next call time in LeadSquared after a talk ({f['saved']} after {f['talks']} talks), so the team "
                   "leader cannot see the coming follow-ups in LeadSquared.")
    return out


def nosave(f: dict) -> bool:
    return f["talks"] >= 20 and f["saved"] * 10 < f["talks"]


def good(f: dict) -> str:
    """One plain line of what is going well, so the talk starts fair."""
    worked = [d for d in f["work"] if f["calls"][d]]
    bits = []
    if worked and len(worked) == len(f["work"]) and min(f["calls"][d] for d in worked) >= 20:
        bits.append("calls every working day")
    bits.append(f"enrolled {f['enrolled']} lead{'s' if f['enrolled'] != 1 else ''} in this period")
    if f["d48"] >= 5 and f["met48"] >= 0.9 * f["d48"]:
        bits.append(f"talked to {f['met48']} of {f['d48']} new leads within 48 hours")
    s = ", ".join(bits)
    return s[0].upper() + s[1:] + "."


def html(teams: dict, first: date, last: date, cut: datetime) -> str:
    pages = []
    for side, fs in teams.items():
        listed = sorted(((f, problems(f)) for f in fs if not (f["leave"] and len(f["work"]) < 3)), key=lambda x: (-len(x[1]), x[0]["tl"], x[0]["caller"]))
        listed = [(f, p) for f, p in listed if p]
        names = {f["caller"] for f, _ in listed}
        small = sorted(f["caller"] for f in fs if f["open"] <= BIG_BOOK and f["caller"] not in names and not f["leave"])
        quiet = sorted(f["caller"] for f in fs if nosave(f) and f["caller"] not in names)
        away = [f for f in fs if f["leave"]]
        cards = "".join(f'<div class="who"><h3>{e(f["caller"])} <span class="muted">(TL {e(f["tl"])})</span></h3>'
                        f'<p class="good">{e(good(f))}</p><ul>' + "".join(f"<li>{e(x)}</li>" for x in p) + "</ul></div>" for f, p in listed)
        notes = []
        if quiet:
            notes.append(f"{', '.join(quiet)} also do not save the next call time in LeadSquared after a talk, so their team leaders cannot "
                         "see the coming follow-ups there; their follow-ups are checked from the times agreed on calls. Nothing else stood "
                         "out for them.")
        if small:
            notes.append(f"{', '.join(small)} have {BIG_BOOK} or fewer open leads and call nearly all of them, so they are not judged on how "
                         "many calls they make.")
        for f in away:
            notes.append(f"{f['caller']} is on leave for part of this period; those days are left out, and other callers are covering "
                         "the leads.")
        pages.append(
            f'<div class="page"><div class="band"><small>Collections · {e(TEAM_ADS[side])}</small>'
            f'<h1>Callers to talk to · {first:%a %d %b} to {last:%a %d %b %Y}</h1>'
            f'<p>Working days (Monday to Friday) only. Calls on this month\'s collection leads, from LeadSquared read '
            f'{cut.astimezone(IST):%a %d %b %H:%M} IST.</p></div>'
            + (cards or "<p>No caller needs a talk on these points.</p>")
            + "<h2>Not on the list</h2><ul class=\"notes\">" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>"
            + '<ul class="notes"><li>A follow-up is the next call time agreed with a lead: saved in LeadSquared after the talk or, when '
              "nothing is saved, the time agreed on the call or in a Note (read from the transcripts and Notes). It is made on time when "
              "the lead is called between 15 minutes before and 2 hours after it. A real talk is an answered call of 2 minutes or "
              "more. Missed calls, follow-ups and new leads count for the caller who owns the lead now.</li></ul></div>")
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>Callers to talk to</title>'
            f"<style>{CSS}{EXTRA_CSS}{PAGE_CSS}</style></head><body>{''.join(pages)}</body></html>")


def build(leads: list[dict], first: date, last: date, leave: dict, cut: datetime) -> dict:
    out = {}
    for side in SIDES:
        team = [l for l in leads if l["side"] == side]
        out[side] = [caller_facts(c, team, first, last, leave, cut) for c in sorted({l["caller"] for l in team})]
    return out


if __name__ == "__main__":
    run_dir, a, b, path = sys.argv[1:5]
    cut = facts.read_at(run_dir)
    first, last = date.fromisoformat(a), date.fromisoformat(b)
    teams = build(facts.collect(run_dir, cut), first, last, load_leave(), cut)
    write_pdf(html(teams, first, last, cut), path)
    for side, fs in teams.items():
        print(side)
        for f in fs:
            p = problems(f)
            print(f"  {f['caller']:24} open {f['open']:2} stale {f['stale']} missed {f['late']}/{f['missed']} never {f['never']} "
                  f"fu {f['made']}/{f['due']} 48h {f['met48']}/{f['d48']} saved {f['saved']}/{f['talks']} enrolled {f['enrolled']}")
            for x in p:
                print("      -", x)
    print(path)
