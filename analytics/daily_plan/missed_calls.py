"""Every call from a lead that nobody answered, with the line it came in on and whether and when someone called back.

A miss within 5 minutes of an answered call with the same lead is a dropped line (the lead rang straight back), not a lead
left waiting. A call back is the first outbound dial after the miss, by anyone; reached is the first answered call after it."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from analytics.daily_plan.common import Snap

DROP = timedelta(minutes=5)


def line_name(display: str | None) -> str:
    d = display or ""
    if d.startswith("Sales_Outbound_"):
        return f"Rang back the outbound {d.rsplit('_', 1)[-1]} number"
    if d.endswith("_Incoming"):
        return f"{d.split('_')[0]} incoming line"
    return f"Direct number {d}" if d else "Unknown line"


def _phone(snap: Snap, c: dict) -> str:
    p = snap.phone(c)
    return f"+91-{p}" if p else ""


def rows(snap: Snap, days: list[str]) -> list[dict]:
    """One row per unanswered inbound call on ``days`` (IST dates), oldest first."""
    out = []
    for c in sorted((c for c in snap.calls if c["direction"] == "inbound" and c["status"] != "Answered"
                     and c["t"].strftime("%Y-%m-%d") in days), key=lambda c: c["t"]):
        cs = snap.by_lead.get(c["lead_id"], [])
        after = [d for d in cs if d["t"] > c["t"]]
        back = next((d for d in after if d["direction"] == "outbound"), None)
        reached = next((d for d in after if d["status"] == "Answered"), None)
        dropped = any(d["status"] == "Answered" and c["t"] - DROP <= d["t"] < c["t"] for d in cs)
        if dropped and reached:
            result = "Dropped call, reached again"
        elif back and reached:
            result = "Called back and reached"
        elif back:
            result = "Called back, not reached yet"
        elif reached:
            result = "Lead rang again and got through"
        else:
            result = "Not called back"
        lead = snap.leads.get(c["lead_id"]) or {}
        who = lambda d: snap.users.get(d.get("user_id")) or d.get("caller") or ""  # noqa: E731
        out.append({
            "day": c["t"].strftime("%a %d %b"), "time": c["t"].strftime("%H:%M"), "lead_id": c["lead_id"], "name": snap.lead_name(c["lead_id"]),
            "phone": _phone(snap, c), "owner": lead.get("OwnerIdName") or "Not a team lead", "rang": who(c) or "No caller (system)",
            "line": line_name(c.get("display_number")), "logged_as": c.get("call_notes") or c["status"], "dropped": dropped,
            "back_at": f"{back['t']:%a %H:%M}" if back else "", "back_by": who(back) if back else "",
            "back_mins": round((back["t"] - c["t"]).total_seconds() / 60) if back else None,
            "reached_at": f"{reached['t']:%a %H:%M}" if reached else "", "result": result, "stage": lead.get("ProspectStage") or "",
        })
    return out


def by_line(snap: Snap, days: list[str]) -> list[tuple[str, int, int, int]]:
    """(line, calls from leads, answered, missed) for ``days``, busiest line first."""
    tot, ans = Counter(), Counter()
    for c in snap.calls:
        if c["direction"] == "inbound" and c["t"].strftime("%Y-%m-%d") in days:
            k = line_name(c.get("display_number"))
            tot[k] += 1
            ans[k] += c["status"] == "Answered"
    return [(k, n, ans[k], n - ans[k]) for k, n in tot.most_common()]
