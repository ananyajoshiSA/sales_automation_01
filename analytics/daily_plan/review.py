"""Facts for the previous working day's review: numbers, plan adherence, enrollments, conversation quality,
priority-lead outcomes and missed opportunities. Everything here is computed; Claude adds the narrative."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from analytics.daily_plan.common import Snap, ist
from analytics.daily_plan.stats import day_stats


def _secs(c) -> int:
    try:
        return int(c.get("secs") or 0)
    except (TypeError, ValueError):
        return 0


def _issues(c) -> str:
    x = c.get("issues")
    return "; ".join(x) if isinstance(x, list) else (x or "")


def quality(reads: dict) -> dict:
    """Per caller: real conversations read, full ask made, link sent on the call, who-decides asked."""
    agg = defaultdict(Counter)
    for r in reads.values():
        for c in r.get("fri_conversations") or r.get("conversations") or []:
            who = (c.get("caller") or r.get("owner") or "?").strip()
            agg[who]["conv"] += 1
            for k in ("full_ask_made", "link_sent_on_call", "who_decides_asked"):
                agg[who][k] += bool(c.get(k))
    return {k: dict(v) for k, v in agg.items()}


def enrollments(snap: Snap, day: str, enrolled: list[dict], reads: dict) -> list[dict]:
    """Team leads first moved to Course Enrolled on ``day``, with who moved them and what the calls show."""
    out = []
    for l in enrolled:
        t = ist(l.get("first_enrolled"))
        if not t or t.strftime("%Y-%m-%d") != day or l.get("OwnerIdName") not in snap.callers:
            continue
        by = comment = ""
        for a in l.get("history") or []:
            d = {x["Key"]: x["Value"] for x in a.get("Data") or []}
            if d.get("CurrentStage") == "Course Enrolled":
                by, comment = d.get("CreatedBy") or "", d.get("Comment") or ""
        r = reads.get(l["ProspectID"], {})
        out.append({"lead_id": l["ProspectID"], "name": ((l.get("FirstName") or "") + " " + (l.get("LastName") or "")).strip() or "Unnamed",
                    "owner": l.get("OwnerIdName"), "at": t.strftime("%H:%M"), "by": by, "comment": comment,
                    "status": r.get("status") or "not re-read", "evidence": r.get("payment_evidence") or r.get("friday_summary") or ""})
    return out


def priority_outcomes(snap: Snap, day: str, prev_plan: dict, reads: dict) -> list[dict]:
    out = []
    for lid, p in prev_plan.items():
        if p.get("group") is None:
            continue
        cs = [c for c in snap.by_lead.get(lid, []) if c["t"].strftime("%Y-%m-%d") == day]
        o = [c for c in cs if c["direction"] == "outbound"]
        best = max((c["duration"] for c in cs if c["status"] == "Answered"), default=0)
        r = reads.get(lid, {})
        if r.get("status") == "paid_new":
            result = "Won"
        elif not o and not cs:
            result = "Not called"
        elif best >= 120:
            result = "Spoke"
        elif best:
            result = "Short pickup"
        else:
            result = "Not reached"
        out.append({"lead_id": lid, "name": snap.lead_name(lid), "owner": p.get("owner"), "group": p["group"], "result": result,
                    "what": r.get("friday_summary") or (f"{len(o)} dial(s), none answered" if o else "No dial")})
    order = {"Won": 0, "Spoke": 1, "Short pickup": 2, "Not reached": 3, "Not called": 4}
    return sorted(out, key=lambda x: (order[x["result"]], x["group"]))


def missed(snap: Snap, day: str, stats: dict, prev_plan: dict, reads: dict) -> dict:
    team = stats["team"]
    not_dialled = [(snap.lead_name(lid), prev_plan[lid]["owner"]) for lid in team["a_not_tried"]]
    long_no_ask = []
    for r in reads.values():
        if r.get("tier") not in ("A", "B"):
            continue
        for c in r.get("fri_conversations") or []:
            if _secs(c) >= 300 and not c.get("full_ask_made"):
                long_no_ask.append({"name": r.get("name"), "caller": c.get("caller") or r.get("owner"), "min": _secs(c) // 60,
                                    "issue": _issues(c)[:160]})
    long_no_ask.sort(key=lambda x: -x["min"])
    broken = []
    for r in reads.values():
        txt = " ".join(_issues(c) for c in r.get("fri_conversations") or []) + " " + (r.get("friday_summary") or "")
        if re.search(r"call-?back.{0,40}(never|not made|missed|late)|promised.{0,40}(never|not)|ring-?back.{0,20}missed", txt, re.I):
            broken.append({"name": r.get("name"), "owner": r.get("owner"), "what": (r.get("friday_summary") or "")[:200]})
    unreturned = {n: v["missed_unreturned"] for n, v in stats["callers"].items() if v["missed_unreturned"]}
    no_proof = [{"name": r.get("name"), "owner": r.get("owner"), "evidence": (r.get("payment_evidence") or "")[:200]}
                for r in reads.values() if r.get("status") == "enrolled_no_proof"]
    hidden_lines = {n: v["zero_sec_pct"] for n, v in stats["callers"].items() if v["dials"] and v["zero_sec_pct"] >= 50}
    return {"not_dialled": not_dialled, "long_no_ask": long_no_ask, "broken_callbacks": broken, "unreturned": unreturned,
            "no_proof": no_proof, "lines": hidden_lines}


def facts(snap: Snap, day: str, before: str | None, prev_plan: dict, reads: dict, enrolled: list[dict]) -> dict:
    stats = day_stats(snap, day, prev_plan)
    stats_before = day_stats(snap, before, None) if before else None
    return {"day": day, "before": before, "stats": stats, "stats_before": stats_before, "quality": quality(reads),
            "enrollments": enrollments(snap, day, enrolled, reads), "priority_outcomes": priority_outcomes(snap, day, prev_plan, reads),
            "missed": missed(snap, day, stats, prev_plan, reads), "has_plan": bool(prev_plan)}
