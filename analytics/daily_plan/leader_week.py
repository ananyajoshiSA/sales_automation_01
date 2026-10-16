"""The team leader's weekly report: what she could have done differently in the week and what to change next week.

Built after the weekly review (``weekly.py``). It adds two things the review lacks: the week's operating facts
(who dialled on which days and from when, which hours give real conversations, how leads were allocated and how fast
they were first dialled, leads chased by many callers) and the near misses: leads that showed a buying signal and slipped
for a reason the team leader could have fixed. Claude reads every open, lost or part-paid lead that had a conversation
(``docs/weekly_leader_brief.md``) and writes, per lead, the signal, the miss, the team leader's lever and a short story.
The report leads with what she can do from Monday, each move with real conversations from the week as examples.

    python -m analytics.daily_plan weekly-leader-prepare 2026-10-08 2026-10-16
    python -m analytics.daily_plan weekly-leader-build 2026-10-08 2026-10-16
"""

from __future__ import annotations

import glob
import html
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import date

from analytics.daily_plan import leadflow
from analytics.daily_plan.common import Snap
from analytics.daily_plan.pdf import css

REAL = 120
WORKING_DIALS = 20
LEVERS = {
    "callback_check": "Promised callback or decision call missed",
    "join_or_close_call": "Needed a senior voice to close",
    "payment_plan_approval": "Payment problem the team leader could solve",
    "payer_joint_call": "The person who pays was never on a call",
    "reassign_or_ownership": "Lead sat with the wrong caller, or too many callers",
    "course_price_control": "Wrong course, shifting fee or over-promise",
    "inbound_return": "Lead rang us and was not called back",
    "handover_followup": "Handover to a senior or finance did not happen",
    "stage_fix": "Ready lead marked Not Interested and dropped",
}
SIGNALS = ("strong", "some", "none")
SAVEABLE = ("likely", "possible", "unlikely")


def candidates(reads: dict[str, dict]) -> list[dict]:
    """Leads worth judging as near misses: part-paid, or open/lost after a conversation (not unreached, not a clear no)."""
    return sorted((r for r in reads.values() if r.get("outcome") == "paying" or (
        r.get("outcome") in ("open", "lost") and r.get("primary_reason") not in (None, "", "L3", "NOT_INTERESTED"))),
        key=lambda r: (r.get("owner") or "", r["lead_id"]))


def ops(snap: Snap, start: str, end: str, reads: dict[str, dict]) -> dict:
    """Attendance, hours, allocation and speed for the team, from LeadSquared calls and lead fields."""
    team = set(snap.callers)
    name = lambda c: snap.users.get(c.get("user_id"))  # noqa: E731
    calls = [c for c in snap.calls if start <= c["t"].strftime("%Y-%m-%d") <= end]
    days = sorted({c["t"].strftime("%Y-%m-%d") for c in calls})
    weekdays = [d for d in days if date.fromisoformat(d).weekday() < 5]
    out = [c for c in calls if c["direction"] == "outbound" and name(c) in team]
    enrolled = Counter(r.get("owner") for r in reads.values() if r.get("outcome") == "enrolled")
    callers = {}
    for n in sorted(team):
        by_day = defaultdict(list)
        for c in out:
            if name(c) == n:
                by_day[c["t"].strftime("%Y-%m-%d")].append(c)
        worked = [d for d in weekdays if len(by_day[d]) >= WORKING_DIALS]
        firsts = sorted(min(c["t"] for c in by_day[d]).strftime("%H:%M") for d in worked)
        real = sum(1 for c in calls if name(c) == n and c["status"] == "Answered" and c["duration"] >= REAL)
        callers[n] = {"worked": len(worked), "weekdays": len(weekdays), "off": [d for d in weekdays if d not in worked],
                      "weekend_dials": sum(len(v) for d, v in by_day.items() if d not in weekdays),
                      "dials_per_day": round(sum(len(by_day[d]) for d in worked) / len(worked)) if worked else 0,
                      "first": firsts[len(firsts) // 2] if firsts else "", "real": real,
                      "real_per_day": round(real / len(worked), 1) if worked else 0.0, "enrolled": enrolled.get(n, 0)}
    hours = defaultdict(Counter)
    for c in out:
        h = hours[c["t"].hour]
        h["dials"] += 1
        h["real"] += c["status"] == "Answered" and c["duration"] >= REAL
    hours = {h: {**v, "real_per_100": round(100 * v["real"] / v["dials"], 1)} for h, v in sorted(hours.items()) if v["dials"] >= 50}
    by_10 = {d: sum(1 for n in team if any(name(c) == n and c["t"].strftime("%Y-%m-%d") == d and c["t"].hour < 11 for c in out))
             for d in weekdays}
    alloc = defaultdict(Counter)
    flow = leadflow.rows(snap, start, end)
    for r in flow:
        a = alloc[r["owner"]]
        a["came_in"] += 1
        a["fresh"] += r["new"]
        a["never_dialled"] += r["first_dial"] is None
        a["real"] += r["real"]
        d = r["allocated"].strftime("%Y-%m-%d")
        a["on_day_off"] += d in callers.get(r["owner"], {}).get("off", [])
    mins = [r["mins"] for r in flow if r["mins"] is not None]
    by_lead = defaultdict(list)
    for c in calls:
        by_lead[c["lead_id"]].append(c)
    crowd = heavy = 0
    for cs in by_lead.values():
        talked = any(c["status"] == "Answered" and c["duration"] >= REAL for c in cs)
        who = {name(c) for c in cs if c["direction"] == "outbound" and name(c) in team}
        crowd += len(who) >= 3 and not talked
        heavy += sum(c["direction"] == "outbound" for c in cs) >= 8 and not talked
    real_leads = {c["lead_id"] for c in calls if c["status"] == "Answered" and c["duration"] >= REAL}
    prospects = [lid for lid in real_leads if (reads.get(lid) or {}).get("outcome") in ("enrolled", "paying", "open", "lost")]
    won = sum((reads.get(lid) or {}).get("outcome") == "enrolled" for lid in prospects)
    return {"weekdays": weekdays, "callers": callers, "hours": hours, "callers_by_11": by_10,
            "allocation": {k: dict(v) for k, v in alloc.items()},
            "came_in": len(flow), "fresh": sum(r["new"] for r in flow), "never_dialled": sum(r["first_dial"] is None for r in flow),
            "first_dial_median_min": statistics.median(mins) if mins else None, "first_dial_in5": sum(m <= 5 for m in mins),
            "crowded_no_talk": crowd, "dialled_8_no_talk": heavy,
            "prospects_talked": len(prospects), "won_after_talk": won,
            "enrolled": sum(r.get("outcome") == "enrolled" for r in reads.values())}


BULK = 50          # leads handed out in one hour by one person: a bulk handout, not a lead arriving
BEST = ("High Priority", "Incoming", "Incoming Missed")


def sources(snap: Snap, start: str, end: str, reads: dict[str, dict], week_start: str | None = None) -> dict:
    """Leads are not equal: by type and fresh or handed out again, how fast each was dialled, reached, talked to, enrolled.
    Bulk handouts (one person, one hour, 50+ old leads) are their own row; best leads (fresh High Priority, Incoming,
    Incoming Missed) by caller; each caller's share of the week's dials (from ``week_start``) spent on bulk-handout leads."""
    enrolled = {k for k, r in reads.items() if r.get("outcome") == "enrolled"}
    flow = leadflow.rows(snap, start, end)
    by = lambda r: (snap.leads.get(r["lead_id"]) or {}).get("mx_Assigned_By") or "?"  # noqa: E731
    drops = Counter((r["allocated"].strftime("%Y-%m-%d %H"), by(r)) for r in flow if not r["new"])
    big = {k for k, n in drops.items() if n >= BULK}
    bulk = [r for r in flow if not r["new"] and (r["allocated"].strftime("%Y-%m-%d %H"), by(r)) in big]
    bulk_ids = {r["lead_id"] for r in bulk}

    def row(rs: list[dict]) -> dict:
        m = [r["mins"] for r in rs if r["mins"] is not None]
        return {"leads": len(rs), "dials": sum(r["attempts"] for r in rs), "median_first_min": statistics.median(m) if m else None,
                "in5": sum(x <= 5 for x in m), "never": sum(r["first_dial"] is None for r in rs), "reached": sum(r["reached"] for r in rs),
                "real": sum(r["real"] for r in rs), "enrolled": sum(r["lead_id"] in enrolled for r in rs)}
    groups = defaultdict(list)
    for r in flow:
        groups["Bulk handout of old leads" if r["lead_id"] in bulk_ids else f"{r['type']} ({'fresh' if r['new'] else 'given again'})"].append(r)
    best = defaultdict(list)
    for r in flow:
        if r["new"] and r["type"] in BEST:
            best[r["owner"]].append(r)
    name = lambda c: snap.users.get(c.get("user_id"))  # noqa: E731
    dials, on_bulk = Counter(), Counter()
    for c in snap.calls:
        if c["direction"] == "outbound" and (week_start or start) <= c["t"].strftime("%Y-%m-%d") <= end and name(c) in snap.callers:
            dials[name(c)] += 1
            on_bulk[name(c)] += c["lead_id"] in bulk_ids
    arrival = defaultdict(list)
    for rs in best.values():
        for r in rs:
            if r["mins"] is not None:
                arrival[r["arrived"].hour].append(r["mins"])
    return {"period": f"{start} to {end}", "types": sorted(((k, row(v)) for k, v in groups.items()), key=lambda kv: -kv[1]["leads"]),
            "handouts": [{"when": k[0], "by": k[1], "leads": n, "to": dict(Counter(r["owner"] for r in bulk if (r["allocated"].strftime("%Y-%m-%d %H"), by(r)) == k))}
                         for k, n in drops.most_common() if k in big],
            "best_by_caller": {k: row(v) for k, v in sorted(best.items(), key=lambda kv: -len(kv[1]))},
            "best_wait_by_arrival_hour": {h: statistics.median(v) for h, v in sorted(arrival.items()) if len(v) >= 5},
            "bulk_dial_share": {k: round(100 * on_bulk[k] / dials[k]) for k in dials if dials[k] >= 100}}


def load_near(d: str) -> dict[str, dict]:
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "out_*.jsonl"))):
        for ln in open(f):
            if ln.strip():
                try:
                    r = json.loads(ln)
                    out[r["lead_id"]] = r
                except (ValueError, KeyError):
                    continue
    return out


def near_counts(near: dict[str, dict]) -> dict:
    hot = [r for r in near.values() if r.get("signal") in ("strong", "some")]
    return {"judged": len(near), "signal": dict(Counter(r.get("signal") for r in near.values())),
            "likely": sum(r.get("saveable") == "likely" for r in hot), "possible": sum(r.get("saveable") == "possible" for r in hot),
            "levers": Counter(r.get("tl_lever") for r in hot if r.get("saveable") in ("likely", "possible")).most_common()}


def _mins(m) -> str:
    if m is None:
        return "—"
    m = int(m)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d}"


def _e(s) -> str:
    return html.escape(str(s or ""))


def _story(r: dict) -> str:
    return (f"<div class='fb'><h4>{_e(r.get('name'))} <span class='sub'>({_e(r.get('owner'))}"
            f"{', still open' if r.get('still_open') else ''})</span></h4><p>{_e(r.get('story'))}</p>"
            f"<p class='sub'><b>What you could have done:</b> {_e(r.get('tl_action'))}"
            + (f"<br><b>Now:</b> {_e(r.get('next_step'))}" if r.get("still_open") else "") + "</p></div>")


def html_doc(F: dict, near: dict[str, dict], N: dict, meta: dict) -> str:
    """The report: cover, the moves for next week with stories, every near miss, allocation, the team, hours, checklist."""
    C = near_counts(near)
    hot = [r for r in near.values() if r.get("signal") in ("strong", "some") and r.get("saveable") in ("likely", "possible") and r.get("story")]
    order = {"likely": 0, "possible": 1}
    hot.sort(key=lambda r: (order[r["saveable"]], r.get("signal") != "strong", r.get("owner") or ""))
    by_id = {r["lead_id"]: r for r in hot}
    used = set()
    H = [f"<!doctype html><html><head><meta charset='utf-8'><title>Weekly note to the team leader</title><style>{css}"
         " .move{border:1px solid #d6dcda;border-radius:8px;padding:10px 14px;margin:12px 0;page-break-inside:avoid}"
         " .move h3{margin:0 0 6px} h2{page-break-before:auto;margin-top:22px} h2.brk{page-break-before:always} .eg{border-left:4px solid #B8860B;background:#FFF8E6;padding:6px 10px;margin:6px 0}</style></head><body>"]
    H.append(f"<div class='cover'><div class='k'>{_e(meta['team'])} · week {_e(meta['period'])} · for {_e(meta['leader'])}</div>"
             f"<h1>{_e(N.get('title') or 'More enrollments were possible this week')}</h1><p>{N.get('headline', '')}</p></div>")
    H.append("<div class='tiles'>"
             f"<div class='tile'><b>{F['enrolled']}</b><span>first-time enrollments this week</span></div>"
             f"<div class='tile'><b>{C['likely']}</b><span>more leads ready to pay that a team-leader action would likely have saved</span></div>"
             f"<div class='tile'><b>{C['possible']}</b><span>more that could possibly have been saved</span></div></div>")
    if N.get("moves"):
        H.append("<h2>Five things to do from Monday</h2>")
        for i, m in enumerate(N["moves"], 1):
            eg = [by_id[x] for x in m.get("examples", []) if x in by_id]
            used.update(r["lead_id"] for r in eg)
            H.append(f"<div class='move'><h3>{i}. {m.get('title', '')}</h3><p>{m.get('do', '')}</p><p class='sub'><b>Why:</b> {m.get('why', '')}</p>"
                     + "".join(f"<div class='eg'><b>{_e(r.get('name'))}</b> ({_e(r.get('owner'))}): {_e(r.get('story'))}</div>" for r in eg)
                     + (f"<p class='sub'><b>How you'll know it worked:</b> {m['check']}</p>" if m.get("check") else "") + "</div>")
    S = F.get("sources")
    if N.get("sources") or S:
        H.append("<h2 class='brk'>Not all leads are equal</h2>" + "".join(f"<p>{x}</p>" for x in N.get("sources", [])))
    if S:
        H.append(f"<p class='sub'>Leads that came to the team {_e(S['period'])}. Enrolled = checked against the calls in the week's review.</p>"
                 "<table><tr><th>Lead</th><th>Leads</th><th>Median first dial</th><th>Dialled in 5 min</th><th>Never dialled</th>"
                 "<th>Reached</th><th>Real conversation</th><th>Enrolled</th></tr>")
        for k, v in S["types"]:
            n = v["leads"] or 1
            H.append(f"<tr><td>{_e(k)}</td><td>{v['leads']}</td><td>{_mins(v['median_first_min'])}</td><td>{100 * v['in5'] // n}%</td>"
                     f"<td>{v['never']}</td><td>{100 * v['reached'] // n}%</td><td>{100 * v['real'] // n}%</td><td>{v['enrolled']}</td></tr>")
        H.append("</table>")
    if N.get("allocation"):
        H.append("<h2>How leads were handed out</h2>" + "".join(f"<p>{x}</p>" for x in N["allocation"]))
    if S and S["handouts"]:
        H.append("<h3>Bulk handouts of old leads</h3><table><tr><th>When (IST)</th><th>By</th><th>Leads</th><th>To</th></tr>" + "".join(
            f"<tr><td>{_e(h['when'])}:00</td><td>{_e(h['by'])}</td><td>{h['leads']}</td><td>{_e(', '.join(f'{k} {v}' for k, v in sorted(h['to'].items(), key=lambda kv: -kv[1])))}</td></tr>"
            for h in S["handouts"]) + "</table>")
        H.append("<p class='sub'>Share of each caller's dials this week spent on bulk-handout leads: " + "; ".join(
            f"{_e(k)} {v}%" for k, v in sorted(S["bulk_dial_share"].items(), key=lambda kv: -kv[1]) if v) + ".</p>")
    if S and S["best_by_caller"]:
        H.append("<h3>Who got the best leads (fresh High Priority, Incoming, Incoming Missed) and how fast they were called</h3>"
                 "<table><tr><th>Caller</th><th>Best leads</th><th>Median first dial</th><th>Dialled in 5 min</th><th>Real conversation</th><th>Enrolled</th></tr>"
                 + "".join(f"<tr><td>{_e(k)}</td><td>{v['leads']}</td><td>{_mins(v['median_first_min'])}</td><td>{v['in5']}</td>"
                           f"<td>{100 * v['real'] // (v['leads'] or 1)}%</td><td>{v['enrolled']}</td></tr>" for k, v in S["best_by_caller"].items()) + "</table>")
        if S["best_wait_by_arrival_hour"]:
            H.append("<p class='sub'>Median wait for a best lead's first dial, by the hour it arrived: " + "; ".join(
                f"{h}:00 {_mins(m)}" for h, m in S["best_wait_by_arrival_hour"].items()) + ".</p>")
    H.append(f"<p class='sub'>{F['came_in']} leads came to the team in the week ({F['fresh']} fresh, {F['came_in'] - F['fresh']} older leads "
             f"assigned again); {F['never_dialled']} were never dialled; median first dial {F['first_dial_median_min']} min after the lead came in "
             f"({F['first_dial_in5']} within 5 min).</p>")
    H.append("<h3>Leads given this week, by caller</h3><table><tr><th>Caller</th><th>Weekdays dialling</th><th>Leads given</th><th>of them fresh</th><th>Given on a day off</th>"
             "<th>Never dialled</th><th>Real conversations a day</th><th>Enrolled (own leads)</th></tr>")
    for n, c in sorted(F["callers"].items(), key=lambda kv: -F["allocation"].get(kv[0], {}).get("came_in", 0)):
        a = F["allocation"].get(n, {})
        H.append(f"<tr><td>{_e(n)}</td><td>{c['worked']} of {c['weekdays']}</td><td>{a.get('came_in', 0)}</td><td>{a.get('fresh', 0)}</td>"
                 f"<td>{a.get('on_day_off', 0)}</td><td>{a.get('never_dialled', 0)}</td><td>{c['real_per_day']}</td><td>{c['enrolled']}</td></tr>")
    H.append("</table>")
    if N.get("management"):
        H.append("<h2>Running the team</h2>" + "".join(f"<p>{x}</p>" for x in N["management"]))
    H.append("<table><tr><th>Caller</th><th>Days off (weekdays)</th><th>Usual first dial</th><th>Dials a day</th><th>Weekend dials</th></tr>"
             + "".join(f"<tr><td>{_e(n)}</td><td>{', '.join(d[8:] for d in c['off']) or '—'}</td><td>{c['first'] or '—'}</td>"
                       f"<td>{c['dials_per_day']}</td><td>{c['weekend_dials']}</td></tr>" for n, c in sorted(F["callers"].items())) + "</table>")
    if N.get("slots"):
        H.append("<h2>Calling hours</h2>" + "".join(f"<p>{x}</p>" for x in N["slots"]))
    H.append("<table><tr><th>Hour (IST)</th>" + "".join(f"<th>{h}:00</th>" for h in F["hours"]) + "</tr>"
             "<tr><td>Dials</td>" + "".join(f"<td>{v['dials']:,}</td>" for v in F["hours"].values()) + "</tr>"
             "<tr><td>Real conversations per 100 dials</td>" + "".join(f"<td>{v['real_per_100']}</td>" for v in F["hours"].values()) + "</tr></table>")
    if N.get("day_plan"):
        H.append("<h3>Suggested day</h3><table><tr><th style='width:16%'>Time</th><th>What the team does</th></tr>"
                 + "".join(f"<tr><td>{_e(t)}</td><td>{x}</td></tr>" for t, x in N["day_plan"]) + "</table>")
    if N.get("checklist"):
        H.append("<h2>Next week, day by day</h2><ul>" + "".join(f"<li>{x}</li>" for x in N["checklist"]) + "</ul>")
    rest = [r for r in hot if r["lead_id"] not in used]
    likely = [r for r in rest if r["saveable"] == "likely"]
    H.append("<h2 class='brk'>Every lead that was ready, and what to do now</h2>"
             f"<p>Claude read every open, lost or part-paid lead that had a conversation this week ({C['judged']} leads) and marked the "
             "ones that showed a buying signal (agreed a price or seat block, asked how to pay, got a link, or fixed a decision time) "
             f"and slipped for a reason a team leader can fix. <b>{C['likely']}</b> would likely have enrolled this week with that action, "
             f"<b>{C['possible']}</b> possibly. These are judgements from the calls, not certainties.</p>")
    if C["levers"]:
        H.append("<table><tr><th>What went wrong</th><th>Leads (likely + possible)</th></tr>" + "".join(
            f"<tr><td>{_e(LEVERS.get(k, k))}</td><td>{n}</td></tr>" for k, n in C["levers"]) + "</table>")
    if likely:
        H.append(f"<h3>The other leads who would likely have enrolled ({len(likely)})</h3>" + "".join(_story(r) for r in likely))
    possible = [r for r in rest if r["saveable"] == "possible"]
    if possible:
        H.append(f"<h3>Could possibly have been saved ({len(possible)})</h3><table><tr><th style='width:15%'>Lead (caller)</th>"
                 "<th style='width:28%'>What they said or did</th><th style='width:25%'>What went wrong</th><th>Do now</th></tr>" + "".join(
                     f"<tr><td><b>{_e(r.get('name'))}</b><br><span class='sub'>{_e(r.get('owner'))}</span></td><td>{_e(r.get('signal_text'))}</td>"
                     f"<td>{_e(r.get('miss_text'))}</td><td>{_e(r.get('next_step') if r.get('still_open') else 'Closed for now')}</td></tr>"
                     for r in sorted(possible, key=lambda r: (r.get("tl_lever") or "", r.get("owner") or ""))) + "</table>")
    H.append(f"<p class='small'>Built {_e(meta['built'])} IST from every connected call of the week and its transcript. Enrollment = first-time "
             "Course Enrolled or a lead saying on a call that they paid. \"Likely\" and \"possible\" are judgements, not forecasts; "
             f"for scale, {F['won_after_talk']} of the {F['prospects_talked']} prospects who had a real conversation this week enrolled.</p>")
    H.append("</body></html>")
    return "\n".join(H)
