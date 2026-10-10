"""Status check during the day (14:00, 17:00): what happened so far against today's plan, and the to-do list for the
next block. Everything is computed from LeadSquared and the saved plan state; no lead details are stored."""

from __future__ import annotations

import html
from datetime import datetime

from analytics.daily_plan.common import CLOSED, Snap, ist
from analytics.daily_plan.stats import day_stats

GROUP_SHORT = ["Money moving", "Tier A", "Callback today", "Hot B", "Rang us", "Later day"]
CSS = """@page { size: A4; margin: 12mm; } body { font-family: Arial, sans-serif; font-size: 10px; color: #1d2b28; }
h1 { color: #1F4E46; font-size: 20px; margin: 0 0 4px; } h2 { color: #1F4E46; font-size: 14px; margin: 14px 0 6px; }
table { border-collapse: collapse; width: 100%; } th { background: #E8EFEC; text-align: left; } th, td { border: 1px solid #d6dcda; padding: 4px 5px; vertical-align: top; }
.box { border-left: 4px solid #1F4E46; background: #F4F7F6; padding: 8px 10px; margin: 6px 0; } .bad { color: #B03A2E; font-weight: bold; } .ok { color: #1F7A4D; font-weight: bold; }
li { margin-bottom: 3px; } .small { color: #555; font-size: 9px; }"""


def next_block(hour: int) -> str:
    return "14:00–17:00" if hour < 16 else "17:00–20:30"


def lead_status(snap: Snap, lid: str, date: str) -> tuple[str, str]:
    cs = [c for c in snap.by_lead.get(lid, []) if c["t"].strftime("%Y-%m-%d") == date]
    ans = [c for c in cs if c["status"] == "Answered"]
    o = [c for c in cs if c["direction"] == "outbound"]
    if ans:
        best = max(ans, key=lambda c: c["duration"])
        return ("reached", f"spoke {best['duration'] // 60}m{best['duration'] % 60:02d}s at {best['t']:%H:%M}")
    if o:
        return ("tried", f"{len(o)} dial(s), not reached (last {o[-1]['t']:%H:%M})")
    return ("untouched", "not dialled yet")


def build(snap: Snap, date: str, plan: dict, enrolled: list[dict], leader: str) -> dict:
    """``plan`` is the resolved state {lead_id: entry} for today."""
    now = snap.fetched
    # enrolled leads are done: never on the to-do list (legacy P/verify rows, or Course Enrolled before today's calls)
    plan = {lid: e for lid, e in plan.items() if not (e.get("tier") == "P" or e.get("verify")
                                                       or (snap.leads.get(lid) or {}).get("ProspectStage") in CLOSED)}
    st = day_stats(snap, date, plan)
    T = st["team"]
    pri = []
    for lid, e in plan.items():
        if e.get("group") is None:
            continue
        s, txt = lead_status(snap, lid, date)
        pri.append({"lead_id": lid, "name": snap.lead_name(lid), "owner": e["owner"], "group": e["group"], "check_by": e.get("check_by") or "",
                    "status": s, "detail": txt, "phone": (snap.leads.get(lid) or {}).get("Phone") or ""})
    pri.sort(key=lambda p: ({"untouched": 0, "tried": 1, "reached": 2}[p["status"]], p["group"]))
    enr_today = []
    for l in enrolled:
        t = ist(l.get("first_enrolled"))
        if t and t.strftime("%Y-%m-%d") == date and l.get("OwnerIdName") in snap.callers:
            by = comment = ""
            for a in l.get("history") or []:
                d = {x["Key"]: x["Value"] for x in a.get("Data") or []}
                if d.get("CurrentStage") == "Course Enrolled":
                    by, comment = d.get("CreatedBy") or "", d.get("Comment") or ""
            enr_today.append({"name": ((l.get("FirstName") or "") + " " + (l.get("LastName") or "")).strip() or "Unnamed",
                              "owner": l.get("OwnerIdName"), "at": t.strftime("%H:%M"), "by": by, "comment": comment})
    unret = []
    for n, v in st["callers"].items():
        for lid in v["unreturned_leads"]:
            unret.append({"name": snap.lead_name(lid), "owner": n, "phone": (snap.leads.get(lid) or {}).get("Phone") or ""})
    untouched = [p for p in pri if p["status"] == "untouched"]
    tried = [p for p in pri if p["status"] == "tried"]
    hm = now.strftime("%H:%M")
    timed = [p for p in pri if p["group"] == 2 and p["status"] != "reached"]
    overdue = [p for p in timed if not (len(p["check_by"]) == 5 and p["check_by"][2] == ":") or p["check_by"] <= hm]
    later = [p for p in timed if p not in overdue]
    lines = [n for n, v in st["callers"].items() if v["dials"] >= 20 and v["zero_sec_pct"] >= 50]
    todo = []
    if T["absent"]:
        todo.append(f"Not dialling yet: {', '.join(T['absent'])}. Confirm attendance; give their untouched P/A leads to the callers present.")
    if untouched:
        todo.append(f"{len(untouched)} priority leads not dialled yet — first thing in the next block: "
                    + "; ".join(f"{p['name']} ({p['owner'].split()[0]})" for p in untouched[:15]) + ("…" if len(untouched) > 15 else "") + ".")
    if tried:
        todo.append(f"{len(tried)} priority leads tried but not reached — redial on another line"
                    + (" at 15:00 and 17:00." if now.hour < 16 else " at 18:30 and 19:30."))
    if unret:
        todo.append(f"{len(unret)} leads rang us and were not called back — return them now: "
                    + "; ".join(f"{u['name']} ({u['owner'].split()[0]})" for u in unret[:12]) + ".")
    if overdue:
        todo.append("Callbacks agreed for earlier today and not done — call now: " + "; ".join(f"{p['name']} ({p['owner'].split()[0]})" for p in overdue[:10]) + ".")
    if later:
        todo.append("Callbacks booked later today: " + "; ".join(f"{p['name']} {p['check_by']} ({p['owner'].split()[0]})" for p in later[:10]) + ".")
    if enr_today:
        todo.append(f"Enrolled today ({len(enr_today)}): "
                    + "; ".join(f"{e['name']} ({e['owner'].split()[0]}, by {e['by'] or '?'})" for e in enr_today) + ".")
    if lines:
        todo.append(f"Lines: most non-answers log as 0 seconds for {', '.join(lines)} — switch them to the +91 8065 pool.")
    low_sheet = [f"{n.split()[0]} {v['on_sheet_pct']}%" for n, v in st["callers"].items() if v["dials"] >= 20 and v["on_sheet_pct"] < 60]
    if low_sheet:
        todo.append("Below 60% of dials on the sheet: " + ", ".join(low_sheet) + ". Sheet leads first; old leads only after 16:00.")
    return {"date": date, "at": now.strftime("%H:%M"), "block": next_block(now.hour), "stats": st, "priority": pri,
            "enrolled_today": enr_today, "unreturned": unret, "todo": todo, "leader": leader}


def html_doc(S: dict, team: str) -> str:
    e = html.escape
    T = S["stats"]["team"]
    d = datetime.strptime(S["date"], "%Y-%m-%d")
    H = [f"<!doctype html><html><head><meta charset='utf-8'><title>Status {e(S['at'])}</title><style>{CSS}</style></head><body>",
         f"<h1>{e(team)} — status at {e(S['at'])}, {d:%a %-d %b}</h1>",
         f"<p class='small'>LeadSquared calls to {e(T['last_call'] or '—')} IST. Plan: today's priority list and sheets. Times are IST.</p>",
         f"<div class='box'><b>So far:</b> {T['dials']:,} dials · {T['answer_pct']}% answered · {T['real']} real conversations · {T['talk_min']} min talk · "
         f"{T['callers_dialling']} of {T['callers']} callers dialling · P/A leads tried {T['a_tried']} of {T['a_total']} · "
         f"{T['inbound_missed']} of {T['inbound']} calls from leads missed · {len(S['enrolled_today'])} enrolled today.</div>",
         f"<h2>To do in the next block ({e(S['block'])})</h2><ol>" + "".join(f"<li>{e(x)}</li>" for x in S["todo"]) + "</ol>",
         "<h2>Callers so far</h2><table><tr><th>Caller</th><th>Dials</th><th>Ans.</th><th>Real conv.</th><th>Talk min</th><th>First dial</th>"
         "<th>On sheet</th><th>P/A tried</th><th>P/A reached</th><th>Missed calls not returned</th></tr>"]
    for n, v in sorted(S["stats"]["callers"].items(), key=lambda kv: -kv[1]["dials"]):
        cls = " class='bad'" if not v["dials"] else ""
        H.append(f"<tr><td{cls}>{e(n)}</td><td>{v['dials']}</td><td>{v['answer_pct']}%</td><td>{v['real']}</td><td>{v['talk_min']}</td>"
                 f"<td>{e(v['first'] or '—')}</td><td>{v['on_sheet_pct']}%</td><td>{v['a_tried']}/{v['a_total']}</td><td>{v['a_reached']}</td>"
                 f"<td>{v['missed_unreturned']}</td></tr>")
    H.append("</table><h2>Priority leads</h2><table><tr><th>Group</th><th>Lead</th><th>Phone</th><th>Caller</th><th>Check by</th><th>Status</th></tr>")
    for p in S["priority"]:
        cls = {"untouched": "bad", "tried": "", "reached": "ok"}[p["status"]]
        H.append(f"<tr><td>{GROUP_SHORT[p['group']] if p['group'] is not None and p['group'] < len(GROUP_SHORT) else ''}</td><td>{e(p['name'])}</td>"
                 f"<td>{e(p['phone'])}</td><td>{e(p['owner'])}</td><td>{e(p['check_by'])}</td><td class='{cls}'>{e(p['detail'])}</td></tr>")
    H.append("</table>")
    if S["enrolled_today"]:
        H.append("<h2>Enrolled today</h2><table><tr><th>Lead</th><th>Owner</th><th>At</th><th>By</th><th>Note</th></tr>"
                 + "".join(f"<tr><td>{e(x['name'])}</td><td>{e(x['owner'])}</td><td>{e(x['at'])}</td><td>{e(x['by'])}</td><td>{e(x['comment'])}</td></tr>" for x in S["enrolled_today"])
                 + "</table>")
    H.append("</body></html>")
    return "\n".join(H)


def text_summary(S: dict, team: str) -> str:
    """Email body without lead phone numbers."""
    T = S["stats"]["team"]
    out = [f"{team} — status at {S['at']}", "",
           f"So far: {T['dials']} dials, {T['answer_pct']}% answered, {T['real']} real conversations, {T['callers_dialling']} of {T['callers']} callers dialling, "
           f"P/A leads tried {T['a_tried']} of {T['a_total']}, {T['inbound_missed']} of {T['inbound']} calls from leads missed.", "",
           f"To do {S['block']}:"] + [f"{k}. {x}" for k, x in enumerate(S["todo"], 1)]
    return "\n".join(out)
