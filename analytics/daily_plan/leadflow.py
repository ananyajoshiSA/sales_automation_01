"""Lead flow: for every lead that reached the team in a period, how fast it was first dialled, how many attempts it got,
whether it was reached, and its disposition now, by lead type (High Priority, Chat, Incoming, Incoming Missed, Other).

Lead type comes from LeadSquared's Report_Type field (High Priority / Incoming / Chat/Email/Referral), else Lead Request
type, else Source. Incoming Missed has no field: it is a lead whose first contact was a call to us that nobody answered."""

from __future__ import annotations

import html
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.daily_plan.common import REAL_SECS, Snap, ist

BUCKETS = ("High Priority", "Chat", "Incoming", "Incoming Missed", "Other")
EARLY = timedelta(minutes=10)  # a lead created by a call is logged just after that call
OPEN, CLOSE = (9, 30), (20, 30)  # calling hours (IST): a lead arriving outside them is timed from the next opening
OUTCOME = {"NotAnswered": "not answered", "CallFailure": "call failed", "Missed": "missed", "Busy": "busy"}


def clock_start(t: datetime) -> datetime:
    """When the speed-to-lead clock starts: arrival, or the next 09:30 for a lead that came in outside calling hours."""
    if (t.hour, t.minute) < OPEN:
        return t.replace(hour=OPEN[0], minute=OPEN[1], second=0, microsecond=0)
    if (t.hour, t.minute) >= CLOSE:
        return (t + timedelta(days=1)).replace(hour=OPEN[0], minute=OPEN[1], second=0, microsecond=0)
    return t


def lead_type(lead: dict, first_call: dict | None) -> str:
    if first_call and first_call["direction"] == "inbound" and first_call["status"] != "Answered":
        return "Incoming Missed"
    rt, rq, src = (lead.get(k) or "" for k in ("mx_Report_Type", "mx_Lead_Request_type", "Source"))
    if rt == "High Priority" or (not rt and rq == "High Priority"):
        return "High Priority"
    if "chat" in rt.lower() or "chat" in src.lower():
        return "Chat"
    if rt == "Incoming" or src == "Inbound Phone call":
        return "Incoming"
    return "Other"


def _day(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%d") if t else ""


def rows(snap: Snap, start: str, end: str) -> list[dict]:
    """Every lead owned by a team member that was created or assigned between ``start`` and ``end`` (IST days, inclusive)."""
    team = set(snap.users.values())
    out = []
    for lid, l in snap.leads.items():
        if l.get("OwnerIdName") not in team:
            continue
        created, assigned = ist(l.get("CreatedOn")), ist(l.get("mx_Assigned_On"))
        new = start <= _day(created) <= end
        if not new and not start <= _day(assigned) <= end:
            continue
        arrived = created if new else assigned
        allocated = assigned if assigned and assigned > arrived else arrived
        calls = [c for c in snap.by_lead.get(lid, []) if c["t"] >= arrived - EARLY]
        dials = [c for c in calls if c["direction"] == "outbound"]
        ans = [c for c in calls if c["status"] == "Answered"]
        first = dials[0]["t"] if dials else None
        last = calls[-1] if calls else None
        clock = clock_start(arrived)
        out.append({
            "lead_id": lid, "name": snap.lead_name(lid), "owner": l["OwnerIdName"], "type": lead_type(l, calls[0] if calls else None),
            "new": new, "source": l.get("Source") or "", "arrived": arrived, "allocated": allocated,
            "first_dial": first, "mins": max(0, round((first - clock).total_seconds() / 60)) if first else None,
            "waiting": None if first else max(0, round((snap.fetched - clock).total_seconds() / 60)),
            "attempts": len(dials), "reached": bool(ans), "real": any(c["duration"] >= REAL_SECS for c in ans),
            "talk": max((c["duration"] for c in ans), default=0),
            "missed_in": sum(c["direction"] == "inbound" and c["status"] != "Answered" for c in calls),
            "last": (f"{last['t']:%a %H:%M} {'in' if last['direction'] == 'inbound' else 'out'} "
                     f"{'answered ' + str(last['duration'] // 60) + 'm' if last['status'] == 'Answered' else OUTCOME.get(last['status'], (last['status'] or '').lower())}") if last else "",
            "stage": l.get("ProspectStage") or "", "phone": l.get("Phone") or "",
        })
    return sorted(out, key=lambda r: r["arrived"])


def _median(xs: list[int]) -> int | None:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def summary(rs: list[dict], key: str = "type") -> list[dict]:
    """One line per lead type (or caller): counts, speed to first dial, attempts, reached, dispositions."""
    groups = defaultdict(list)
    for r in rs:
        groups[r[key]].append(r)
    order = list(BUCKETS) if key == "type" else sorted(groups, key=lambda k: -len(groups[k]))
    out = []
    for k in order + ["All"]:
        g = rs if k == "All" else groups.get(k, [])
        if not g:
            continue
        mins = [r["mins"] for r in g if r["mins"] is not None]
        out.append({"group": k, "leads": len(g), "new": sum(r["new"] for r in g), "dialled": len(mins),
                    "in5": sum(m <= 5 for m in mins), "in30": sum(m <= 30 for m in mins), "median": _median(mins),
                    "never": len(g) - len(mins), "attempts": round(sum(r["attempts"] for r in g) / len(g), 1),
                    "reached": sum(r["reached"] for r in g), "real": sum(r["real"] for r in g),
                    "stages": Counter(r["stage"] or "—" for r in g).most_common(3)})
    return out


def _mins(m: int | None) -> str:
    if m is None:
        return "—"
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d}"


def summary_table(S: list[dict], label: str = "Lead type") -> str:
    e = html.escape
    H = [f"<table><tr><th>{e(label)}</th><th>Leads (fresh)</th><th>Dialled</th><th>In 5 min</th><th>In 30 min</th><th>Median to 1st dial</th>"
         "<th>Never dialled</th><th>Attempts per lead</th><th>Reached</th><th>2 min+ talk</th><th>Disposition now (top 3)</th></tr>"]
    for s in S:
        b = " style='font-weight:bold'" if s["group"] == "All" else ""
        H.append(f"<tr{b}><td>{e(s['group'])}</td><td>{s['leads']} ({s['new']})</td><td>{s['dialled']}</td><td>{s['in5']}</td><td>{s['in30']}</td>"
                 f"<td>{_mins(s['median'])}</td><td>{s['never']}</td><td>{s['attempts']}</td><td>{s['reached']}</td><td>{s['real']}</td>"
                 f"<td>{e('; '.join(f'{k} {n}' for k, n in s['stages']))}</td></tr>")
    return "".join(H) + "</table>"


def lead_table(rs: list[dict], phones: bool = True, limit: int = 200) -> str:
    e = html.escape
    H = ["<table><tr><th>Lead</th>" + ("<th>Phone</th>" if phones else "") + "<th>Type</th><th>Caller</th><th>In at</th><th>First dial</th>"
         "<th>Attempts</th><th>Reached</th><th>Last call</th><th>Disposition now</th></tr>"]
    for r in sorted(rs, key=lambda r: (r["reached"], r["mins"] is not None, r["arrived"]))[:limit]:
        fd = _mins(r["mins"]) if r["mins"] is not None else f"<span class='bad'>not yet ({_mins(r['waiting'])})</span>"
        reached = "yes" + (" (2 min+)" if r["real"] else "") if r["reached"] else "<span class='bad'>no</span>"
        H.append(f"<tr><td>{e(r['name'])}{'' if r['new'] else ' <span class=small>(reassigned)</span>'}</td>"
                 + (f"<td>{e(r['phone'])}</td>" if phones else "")
                 + f"<td>{e(r['type'])}</td><td>{e(r['owner'])}</td><td>{r['arrived']:%a %H:%M}</td><td>{fd}</td><td>{r['attempts']}</td>"
                 f"<td>{reached}</td><td>{e(r['last'])}</td><td>{e(r['stage'])}</td></tr>")
    more = f"<p class='small'>{len(rs) - limit} more not shown.</p>" if len(rs) > limit else ""
    return "".join(H) + "</table>" + more


NOTE = ("Type from LeadSquared's Report_Type (else Lead Request type, else Source); Incoming Missed = the lead's first contact was "
        "a call to us that nobody answered. First dial is timed from when the lead came in (created, or assigned for a reassigned lead); "
        "a lead that came in before 09:30 or after 20:30 IST is timed from the next 09:30. "
        "Leads not yet given to a caller are not in the team's lead list, so they are not counted.")


def section(title: str, rs: list[dict], leads: bool = True, phones: bool = True, by_caller: bool = False) -> str:
    """A report section: summary by lead type, optionally by caller, then the lead list."""
    e = html.escape
    if not rs:
        return f"<h2>{e(title)}</h2><p>No leads came in.</p>"
    new = sum(r["new"] for r in rs)
    split = (f"{len(rs):,} leads came to the team: {new:,} created in this period (fresh leads) and {len(rs) - new:,} older leads "
             "assigned to a caller again (re-enquiries and reassignments).")
    H = [f"<h2>{e(title)}</h2>", f"<p><b>{e(split)}</b></p>", f"<p class='small'>{e(NOTE)}</p>", summary_table(summary(rs))]
    if by_caller:
        H += ["<h3>By caller</h3>", summary_table(summary(rs, "owner"), "Caller")]
    if leads:
        H += ["<h3>Lead by lead (not reached first)</h3>", lead_table(rs, phones)]
    return "".join(H)


def text_line(rs: list[dict]) -> str:
    if not rs:
        return "New leads today: none allocated yet."
    S = {s["group"]: s for s in summary(rs)}
    a = S["All"]
    parts = [f"{k} {S[k]['leads']}" for k in BUCKETS if k in S]
    return (f"New leads today: {a['leads']} ({', '.join(parts)}) — first dial median {_mins(a['median'])}, {a['in5']} within 5 min, "
            f"{a['never']} not dialled, {a['reached']} reached.")
