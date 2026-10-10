"""One day's numbers per caller and for the team, measured against that day's plan (sheet)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from analytics.daily_plan.common import REAL_SECS, Snap


def _hour(c) -> float:
    return c["t"].hour + c["t"].minute / 60


def day_stats(snap: Snap, date: str, sheet: dict[str, dict] | None = None, until: float | None = None) -> dict:
    """``sheet`` is {lead_id: {"owner", "tier"}} for the plan that day; ``until`` (hour, e.g. 14.0) cuts the day."""
    sheet = sheet or {}
    day = [c for c in snap.day(date) if until is None or _hour(c) < until]
    by_lead = defaultdict(list)
    for c in day:
        by_lead[c["lead_id"]].append(c)
    out_by_lead = {lid: [c for c in cs if c["direction"] == "outbound"] for lid, cs in by_lead.items()}
    reached = {lid for lid, cs in by_lead.items() if any(c["status"] == "Answered" for c in cs)}
    callers = {}
    for uid, name in snap.users.items():
        if name not in snap.callers:
            continue
        mine = [c for c in day if c.get("user_id") == uid]
        dials = sorted((c for c in mine if c["direction"] == "outbound"), key=lambda c: c["t"])
        ans = [c for c in mine if c["status"] == "Answered"]
        mine_sheet = {lid for lid, s in sheet.items() if s.get("owner") == name}
        tier = {lid: sheet[lid]["tier"] for lid in mine_sheet}
        a_leads = [lid for lid in mine_sheet if tier[lid] in "AP" or sheet[lid].get("verify")]
        ab = [lid for lid in mine_sheet if tier[lid] in "APB"]
        missed = [c for c in day if c["direction"] == "inbound" and c["status"] != "Answered"
                  and (snap.leads.get(c["lead_id"]) or {}).get("OwnerIdName") == name]
        cs_of = lambda c: by_lead[c["lead_id"]]  # noqa: E731
        unret = [c for c in missed if not any(d["t"] > c["t"] and (d["direction"] == "outbound" or d["status"] == "Answered") for d in cs_of(c))]
        ret15 = [c for c in missed if any(d["direction"] == "outbound" and c["t"] < d["t"] <= c["t"] + timedelta(minutes=15) for d in cs_of(c))]
        n = len(dials)
        callers[name] = {
            "dials": n, "answer_pct": round(100 * sum(c["status"] == "Answered" for c in dials) / n) if n else 0,
            "fail_pct": round(100 * sum(c["status"] == "CallFailure" for c in dials) / n) if n else 0,
            "talk_min": round(sum(c["duration"] for c in ans) / 60), "real": sum(c["duration"] >= REAL_SECS for c in ans),
            "first": dials[0]["t"].strftime("%H:%M") if dials else None, "last": dials[-1]["t"].strftime("%H:%M") if dials else None,
            "on_sheet_pct": round(100 * sum(c["lead_id"] in mine_sheet for c in dials) / n) if n else 0,
            "sheet_leads": len(mine_sheet), "sheet_worked": sum(1 for lid in mine_sheet if out_by_lead.get(lid)),
            "a_total": len(a_leads), "a_tried": sum(1 for lid in a_leads if out_by_lead.get(lid)),
            "a_twice": sum(1 for lid in a_leads if len(out_by_lead.get(lid, [])) >= 2), "a_reached": sum(1 for lid in a_leads if lid in reached),
            "ab_total": len(ab), "ab_reached": sum(1 for lid in ab if lid in reached),
            "missed_in": len(missed), "missed_unreturned": len(unret), "missed_ret15": len(ret15),
            "unreturned_leads": sorted({c["lead_id"] for c in unret}),
            "lines": dict(Counter((c.get("display_number") or "?").replace("+", "").replace("-", "") for c in dials)),
            "zero_sec_pct": round(100 * sum(c["status"] != "Answered" and not c["duration"] for c in dials) / n) if n else 0,
        }
    team_out = [c for c in day if c["direction"] == "outbound" and c.get("user_id") in snap.users]
    hours = defaultdict(lambda: [0, 0])
    for c in team_out:
        hours[c["t"].hour][0] += 1
        hours[c["t"].hour][1] += c["status"] == "Answered"
    redial = {"<=30m": [0, 0], "30m-2h": [0, 0], ">2h": [0, 0]}
    single = 0
    for lid, o in out_by_lead.items():
        o = sorted(o, key=lambda c: c["t"])
        if len(o) == 1 and o[0]["status"] != "Answered" and not any(c["direction"] == "inbound" for c in by_lead[lid]):
            single += 1
        for a, b in zip(o, o[1:]):
            if a["status"] == "Answered":
                continue
            g = (b["t"] - a["t"]).total_seconds() / 60
            k = "<=30m" if g <= 30 else "30m-2h" if g <= 120 else ">2h"
            redial[k][0] += 1
            redial[k][1] += b["status"] == "Answered"
    # plan adherence on the A list
    a_all = [lid for lid, s in sheet.items() if s["tier"] in "AP" or s.get("verify")]
    first_unans = redial15 = 0
    unreached_a = []
    for lid in a_all:
        o = sorted(out_by_lead.get(lid, []), key=lambda c: c["t"])
        if o and o[0]["status"] != "Answered":
            first_unans += 1
            if len(o) >= 2 and (o[1]["t"] - o[0]["t"]) <= timedelta(minutes=15):
                redial15 += 1
        if o and lid not in reached:
            unreached_a.append(lid)
    inb = [c for c in day if c["direction"] == "inbound"]
    before16 = [c for c in team_out if _hour(c) < 16]
    b_all = [lid for lid, s in sheet.items() if s["tier"] == "B"]
    team = {
        "dials": len(team_out), "answered": sum(c["status"] == "Answered" for c in team_out),
        "answer_pct": round(100 * sum(c["status"] == "Answered" for c in team_out) / len(team_out)) if team_out else 0,
        "talk_min": round(sum(c["duration"] for c in day if c.get("user_id") in snap.users and c["status"] == "Answered") / 60),
        "real": sum(1 for c in day if c.get("user_id") in snap.users and c["status"] == "Answered" and c["duration"] >= REAL_SECS),
        "callers_dialling": sum(1 for v in callers.values() if v["dials"]), "callers": len(callers),
        "absent": [n for n, v in callers.items() if not v["dials"]],
        "leads_dialled": len({c["lead_id"] for c in team_out}), "single_unanswered": single,
        "inbound": len(inb), "inbound_missed": sum(c["status"] != "Answered" for c in inb),
        "hours": {h: v for h, v in sorted(hours.items())}, "redial": redial,
        "a_total": len(a_all), "a_tried": sum(1 for lid in a_all if out_by_lead.get(lid)),
        "a_not_tried": [lid for lid in a_all if not out_by_lead.get(lid)],
        "a_twice": sum(1 for lid in a_all if len(out_by_lead.get(lid, [])) >= 2),
        "a_first_unanswered": first_unans, "a_redial15": redial15, "a_unreached": unreached_a,
        "a_retry_15": sum(1 for lid in unreached_a if any(15 <= _hour(c) < 16 for c in out_by_lead[lid])),
        "a_retry_evening": sum(1 for lid in unreached_a if any(_hour(c) >= 18.5 for c in out_by_lead[lid])),
        "b_total": len(b_all), "b_tried": sum(1 for lid in b_all if out_by_lead.get(lid)), "b_reached": sum(1 for lid in b_all if lid in reached),
        "before16": len(before16), "before16_on_sheet": sum(1 for c in before16 if c["lead_id"] in sheet),
        "last_call": max((c["t"] for c in day), default=None),
    }
    if team["last_call"]:
        team["last_call"] = team["last_call"].strftime("%H:%M")
    return {"date": date, "callers": callers, "team": team}
