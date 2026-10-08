"""Account-wide calling health: DNP (did-not-pick) ratios, effort and red flags.

Caller-days where half or more of the dials failed at the telephony layer are listed in
dialer_days.csv and left out of that caller's rates (plan action 5: fix the dialer first).

    python -m analytics.dnp_report data/all_calls.jsonl data/users_all.json exports/dnp
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict

from analytics.definitions import DIALER_FAILURE_SHARE, REAL_CONVERSATION_SECS, WORKING_DAY_DIALS
from analytics.team_report import IST, utc

SALES_GROUP_HINTS = ("team", "us ", "closure", "id", "elite", "bootcamp", "dsv", "community", "counsel", "academic",
                     "women", "corporate", "trainee", "group", "gourp")


def team_of_user(u):
    gs = u.get("MemberOfGroups") or []
    return gs[0] if gs else "(no group)"


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def dialer_days(outbound: list[dict]) -> list[dict]:
    """Caller-days with 20+ dials where 50%+ ended in CallFailure: a dialer problem to fix before judging the caller.

    Same-second failures across several leads are counted too, since they point at the line, not the lead.
    """
    per = defaultdict(list)
    for c in outbound:
        per[(c["name"], c["day"])].append(c)
    out = []
    for (name, day), cs in sorted(per.items()):
        fails = [c for c in cs if c["status"] == "CallFailure"]
        if len(cs) >= WORKING_DAY_DIALS and len(fails) >= DIALER_FAILURE_SHARE * len(cs):
            same_sec = Counter(c["start_utc"] for c in fails)
            out.append({"caller": name, "team": cs[0]["team"], "day": day, "dials": len(cs), "failures": len(fails),
                        "failure_%": pct(len(fails), len(cs)),
                        "same_second_failures": sum(v for v in same_sec.values() if v >= 2)})
    return out


def main(calls_path, users_path, out_dir):
    users = json.load(open(users_path))
    by_id = {u["ID"]: u for u in users}
    by_name = {f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip(): u for u in users}
    rows = [json.loads(l) for l in open(calls_path)]
    for r in rows:
        u = by_id.get(r.get("user_id")) or by_name.get((r.get("caller") or "").strip())
        r["name"] = f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip() if u else (r.get("caller") or "").strip() or "(unknown)"
        r["team"] = team_of_user(u) if u else "(not a user)"
        r["t"] = utc(r["start_utc"])
        r["day"] = r["t"].astimezone(IST).strftime("%Y-%m-%d") if r["t"] else None
        r["hour"] = r["t"].astimezone(IST).hour if r["t"] else None

    out = [r for r in rows if r["direction"] == "outbound"]
    inb = [r for r in rows if r["direction"] == "inbound"]

    def stat(calls):
        n = len(calls)
        st = Counter(c["status"] for c in calls)
        ans = [c for c in calls if c["status"] == "Answered"]
        leads = defaultdict(list)
        for c in calls:
            leads[c["lead_id"]].append(c)
        never = [l for l, cs in leads.items() if not any(c["status"] == "Answered" for c in cs)]
        per_day = Counter((c["lead_id"], c["day"]) for c in calls)
        return {
            "dials": n,
            "answered_%": pct(st["Answered"], n),
            "dnp_%": pct(st["NotAnswered"], n),
            "call_failure_%": pct(st["CallFailure"], n),
            "other_%": pct(n - st["Answered"] - st["NotAnswered"] - st["CallFailure"], n),
            "unique_leads": len(leads),
            "leads_never_answered_%": pct(len(never), len(leads)),
            "dials_per_lead": round(n / len(leads), 1) if leads else 0,
            "lead_days_with_6plus_dials": sum(1 for v in per_day.values() if v >= 6),
            "answered_under_30s_%": pct(sum(1 for c in ans if c["duration"] < 30), len(ans)),
            "answered_2min_plus_%": pct(sum(1 for c in ans if c["duration"] >= REAL_CONVERSATION_SECS), len(ans)),
            "talk_hrs": round(sum(c["duration"] for c in ans) / 3600, 1),
        }

    overall = stat(out)
    overall["inbound"] = len(inb)
    overall["inbound_missed_%"] = pct(sum(1 for c in inb if c["status"] != "Answered"), len(inb))

    teams = defaultdict(list)
    for c in out:
        teams[c["team"]].append(c)
    team_rows = []
    for t, cs in teams.items():
        s = stat(cs)
        callers = {c["name"] for c in cs}
        ti = [c for c in inb if c["team"] == t]
        s.update(team=t, callers=len(callers), inbound=len(ti),
                 inbound_missed_pct=pct(sum(1 for c in ti if c["status"] != "Answered"), len(ti)))
        team_rows.append(s)
    team_rows.sort(key=lambda r: -r["dials"])

    dialer = dialer_days(out)
    bad = {(d["caller"], d["day"]) for d in dialer}
    callers = defaultdict(list)
    for c in out:
        callers[c["name"]].append(c)
    caller_rows = []
    for name, all_cs in callers.items():
        # a dialer-failure day says nothing about the caller, so it is left out of their rates
        cs = [c for c in all_cs if (name, c["day"]) not in bad] or all_cs
        days = {c["day"] for c in cs}
        s = stat(cs)
        active = len([d for d in days if sum(1 for c in cs if c["day"] == d) >= WORKING_DAY_DIALS]) or 1
        ci = [c for c in inb if c["name"] == name]
        s.update(caller=name, team=cs[0]["team"], dialer_failure_days=sum(1 for n, _ in bad if n == name),
                 active_days=active, dials_per_day=round(len(cs) / active),
                 talk_hrs_per_day=round(s["talk_hrs"] / active, 2),
                 real_calls_per_day=round(sum(1 for c in cs if c["status"] == "Answered" and c["duration"] >= REAL_CONVERSATION_SECS) / active, 1),
                 inbound=len(ci), inbound_missed_pct=pct(sum(1 for c in ci if c["status"] != "Answered"), len(ci)))
        caller_rows.append(s)
    caller_rows.sort(key=lambda r: -r["dials"])

    hours = []
    for h in range(7, 23):
        cs = [c for c in out if c["hour"] == h]
        if cs:
            hours.append({"hour": h, "dials": len(cs), "answered_%": pct(sum(1 for c in cs if c["status"] == "Answered"), len(cs)),
                          "failure_%": pct(sum(1 for c in cs if c["status"] == "CallFailure"), len(cs))})
    days = []
    for d in sorted({c["day"] for c in out if c["day"]}):
        cs = [c for c in out if c["day"] == d]
        days.append({"day": d, "dials": len(cs), "answered_%": pct(sum(1 for c in cs if c["status"] == "Answered"), len(cs)),
                     "dnp_%": pct(sum(1 for c in cs if c["status"] == "NotAnswered"), len(cs)),
                     "failure_%": pct(sum(1 for c in cs if c["status"] == "CallFailure"), len(cs))})
    nums = defaultdict(list)
    for c in out:
        nums[c.get("display_number") or "(none)"].append(c)
    num_rows = sorted(({"display_number": k, "dials": len(v), "answered_%": pct(sum(1 for c in v if c["status"] == "Answered"), len(v)),
                        "failure_%": pct(sum(1 for c in v if c["status"] == "CallFailure"), len(v))}
                       for k, v in nums.items() if len(v) >= 500), key=lambda r: -r["dials"])
    # failures that hit several different leads in the same second = dialer/telephony outage, not the lead
    sec = Counter((c["start_utc"], c["user_id"]) for c in out if c["status"] == "CallFailure")
    burst = sum(v for v in sec.values() if v >= 3)

    os.makedirs(out_dir, exist_ok=True)
    for name, data in (("teams.csv", team_rows), ("callers.csv", caller_rows), ("hours.csv", hours), ("days.csv", days),
                       ("numbers.csv", num_rows), ("dialer_days.csv", dialer)):
        if not data:
            continue
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    res = {"overall": overall, "teams": team_rows, "hours": hours, "days": days, "numbers": num_rows,
           "failure_bursts_calls": burst, "dialer_days": dialer, "callers": caller_rows}
    json.dump(res, open(os.path.join(out_dir, "dnp.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "callers"}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
