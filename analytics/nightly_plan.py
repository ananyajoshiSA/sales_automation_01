"""Tomorrow's call plan for any team, built by rule every night (plan step 8).

Fetches the team's leads and the last ``--days`` days of calls (so owners and stages are as of the
run, not frozen), ranks every open lead with ``analytics.lead_priority``, tiers it by rule, adds
new leads, open-stage leads gone quiet (``analytics.lost_leads``) and unpaid payment links from the
team's earlier workbooks, and writes the workbook. Dates, titles and targets are computed; nothing
is hardcoded to one team or day.

The tier chances are estimates. Once ``analytics.tier_outcomes`` has measured enough outcomes, pass
its tier_chances.json with --chances and the measured rates replace them.

    python -m analytics.nightly_plan "Team Elite Calling" --out exports/plans [--date 2026-10-09]
        [--snapshot data/snap.json] [--chances exports/tier_chances.json] [--leader "Name"] [--target 4]
        [--tier-b-cap 12] [--days 15] [--since 2026-09-01] [--sheet-cap 80]

--since widens the window: every lead with a conversation since that day is tiered and every lead created
since then with no real conversation yet is a new lead (F). Revive (R) still means 15+ quiet days.

Tier rules (no human read; the lead review can still override by editing the plan JSON):
  A  a real conversation in the last 7 days and a strong signal: asked for the payment link or said
     they want to join, Zip intent HIGH, or stage Follow Up For Closure
  B  a real conversation in the last 15 days and some interest: Zip HIGH/MODERATE, any buying
     signal, or the lead called us
  C  any other lead with a real conversation (nurture)
  -  leads with a negative signal or a dead stage are left off
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

from analytics.call_plan import TIERS, read_carry, write_workbook
from analytics.definitions import CLOSED_STAGES, DEAD_STAGES, REAL_CONVERSATION_SECS
from analytics.lead_priority import build_timelines
from analytics.lost_leads import lost_leads
from analytics.team_report import IST, utc

# Estimates until outcomes are measured: A and B from the lead review's High/Medium calibration
# (call_plan.REVIEWED_PROB), C from the middle of its 2-8% nurture range. Not measured.
DEFAULT_CHANCE = {"A": 35, "B": 15, "C": 5}
FRESH_DAYS = 7
CALLBACK_DAYS = 7  # a booked callback missed in the last week is flagged; older ones are just overdue
NEXT_ACTION = {
    "A": "Close on this call: say the amount, offer the EMI split, send the payment link while on the line, "
         "and agree the time they will pay.",
    "B": "Answer the open question, then ask for a token or seat block and book a dated callback.",
    "C": "WhatsApp a short recap and the fee sheet first, then one call.",
}


def _name(u: dict) -> str:
    return f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip()


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def rule_tier(r: dict) -> str | None:
    days = r["days_since_last_conversation"]
    if r["negative_signals"] or r["stage"] in DEAD_STAGES | CLOSED_STAGES or days is None:
        return None
    strong = bool({"payment", "enroll_intent"} & set(r["buying_signals"])) or r["best_zip_intent"] == "HIGH" \
        or r["stage"] == "Follow Up For Closure"
    some = r["best_zip_intent"] in ("HIGH", "MODERATE") or r["buying_signals"] or r["inbound_calls"]
    if strong and days <= 7:
        return "A"
    if some and days <= 15:
        return "B"
    return "C"


SLOTS = (("10:00–12:00", 0, 12), ("12:00–15:00", 12, 15), ("15:00–18:00", 15, 18), ("18:00–21:00", 18, 24))


def best_time(hours: list[int]) -> str:
    """The slot (IST) in which the lead answered most often (hours in call order); a tie goes to the latest answer's slot."""
    if not hours:
        return ""
    n = {label: sum(lo <= h < hi for h in hours) for label, lo, hi in SLOTS}
    last = next(label for label, lo, hi in SLOTS if lo <= hours[-1] < hi)
    top = max(n.values())
    return last if n[last] == top else next(label for label, _, _ in SLOTS if n[label] == top)


def _why(r: dict) -> str:
    bits = []
    if r["best_zip_intent"]:
        bits.append(f"Zip intent {r['best_zip_intent']}")
    if r["buying_signals"]:
        bits.append("talked about " + ", ".join(s.replace("_", " ") for s in r["buying_signals"]))
    if r["inbound_calls"]:
        bits.append(f"called us {r['inbound_calls']} time(s)")
    bits.append(f"stage {r['stage'] or '(blank)'}")
    bits.append(f"last real talk {r['last_conversation_ist']} ({r['days_since_last_conversation']:.0f} days), "
                f"longest call {r['longest_call_min']} min")
    if r["crosscheck_flags"]:
        bits.append("check: " + "; ".join(r["crosscheck_flags"]))
    return "; ".join(bits) + "."


def build_plan(snap: dict, plan_date: str, days: int = 15, chances: dict | None = None, leaders=(),
               target: int = 4, tier_b_cap: int | None = None, carry: list[dict] | None = None,
               since: str | None = None, sheet_cap: int | None = None) -> dict:
    chances = {**DEFAULT_CHANCE, **(chances or {})}
    team = sorted(_name(u) for u in snap["users"])
    leads = {l["ProspectID"]: l for l in snap["leads"]}
    day = datetime.strptime(plan_date, "%Y-%m-%d").replace(tzinfo=IST)
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=IST) if since else None
    window = max(days, -(-(datetime.fromisoformat(snap["fetched_at"]) - start).total_seconds() // 86400)) if start else days
    fresh_from = start or day - timedelta(days=FRESH_DAYS)

    real, calls, picked = set(), {}, {}
    for c in snap["calls"]:
        n = calls.setdefault(c["lead_id"], {"dials": 0, "answered": 0})
        n["dials"] += c.get("direction") == "outbound"
        n["answered"] += c.get("status") == "Answered"
        if c.get("status") == "Answered" and (t := utc(c.get("start_utc"))):
            picked.setdefault(c["lead_id"], []).append(t)
        if c.get("status") == "Answered" and (c.get("duration") or 0) >= REAL_CONVERSATION_SECS:
            real.add(c["lead_id"])
    cands = [r for r in build_timelines(snap, int(window)) if r["owner"] in team and r["stage"] not in CLOSED_STAGES]
    tiered = []
    for r in cands:
        if tier := rule_tier(r):
            fu = r["next_follow_up_ist"] and datetime.strptime(r["next_follow_up_ist"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            tiered.append({"owner": r["owner"], "lead_id": r["lead_id"], "name": r["name"], "tier": tier,
                           "prob_3d": chances[tier], "month_end_due": False, "why": _why(r), "rank": r["pre_score"],
                           "callback_missed": bool(fu and day - fu <= timedelta(days=CALLBACK_DAYS)
                                                   and "follow-up overdue and not called" in r["crosscheck_flags"]),
                           "next_action": NEXT_ACTION[tier], "opening_line": "",
                           "best_time": best_time([t.astimezone(IST).hour for t in sorted(picked.get(r["lead_id"], []))]),
                           "objection_to_prepare": "", "course": r.get("course") or "", "phone": r.get("phone")})

    first = []
    for lid, l in leads.items():
        created = utc(l.get("CreatedOn"))
        if (l.get("OwnerIdName") in team and created and fresh_from <= created < day
                and lid not in real and l.get("ProspectStage") not in DEAD_STAGES | CLOSED_STAGES):
            n = calls.get(lid, {}).get("dials", 0)
            first.append({"owner": l["OwnerIdName"], "lead_id": lid, "name": _name(l), "source": l.get("Source") or "",
                          "rank": -(5 * min(n, 12) + (day - created).days),  # least tried, then newest, first
                          "created_ist": created.astimezone(IST).strftime("%d %b %H:%M"),
                          "dials": n, "answered_calls": calls.get(lid, {}).get("answered", 0),
                          "course": l.get("mx_Enquired_Course") or "", "phone": l.get("Phone"), "stage": l.get("ProspectStage") or ""})

    revive = []
    for s in lost_leads(snap, days)["stale_open"]:
        if s["over_cap"] or s["owner"] not in team:
            continue
        l = leads[s["lead_id"]]
        last = s["last_real_conversation_ist"] or s["last_called_ist"]
        quiet = (day - datetime.strptime(last, "%Y-%m-%d %H:%M").replace(tzinfo=IST)).days if last else days
        revive.append({"owner": s["owner"], "lead_id": s["lead_id"], "name": _name(l), "stage": s["stage"],
                       "days": quiet, "last_activity": last or "before the data window",
                       "last_activity_name": "real conversation" if s["last_real_conversation_ist"] else "call",
                       "course": s["course"], "phone": l.get("Phone")})

    return {
        "team": snap.get("group") or "Team", "date_label": day.strftime("%a %-d %b %Y"), "plan_date": plan_date,
        "since": since, "fetched_at": snap.get("fetched_at"),
        "owner_order": [o for o in team if o not in leaders] + [o for o in team if o in leaders],
        "targets": {o: 0 for o in leaders}, "default_target": target, "tier_b_cap": tier_b_cap, "sheet_cap": sheet_cap,
        "chances": {t: v for t, v in chances.items() if t in TIERS},
        "reviewed": [], "tiered": tiered, "first": first, "revive": revive, "candidates": cands,
        "stages": {lid: l.get("ProspectStage") or "" for lid, l in leads.items()}, "carry": carry or [],
    }


def load_chances(path: str | None) -> dict:
    """Measured chances from analytics.tier_outcomes; tiers without enough outcomes keep the estimate."""
    if not path or not os.path.exists(path):
        return {}
    return {t: v["use_%"] for t, v in json.load(open(path))["tiers"].items() if v.get("use_%") is not None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("team", nargs="+", help="LeadSquared group name(s), e.g. 'Team Elite Calling'; several are one team")
    ap.add_argument("--out", default="exports/plans")
    ap.add_argument("--date", help="plan day, YYYY-MM-DD (default: tomorrow in IST)")
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--since", help="first day of the lead window, YYYY-MM-DD (default: --days before the plan day)")
    ap.add_argument("--snapshot", help="reuse a fetch_team_data.py snapshot instead of fetching")
    ap.add_argument("--chances", default="exports/tier_chances.json")
    ap.add_argument("--leader", action="append", default=[], help="team leader(s): no personal target")
    ap.add_argument("--target", type=int, default=4)
    ap.add_argument("--tier-b-cap", type=int)
    ap.add_argument("--sheet-cap", type=int, help="most leads per sheet; extra F, R and C leads go to the Backlog sheet")
    a = ap.parse_args()
    plan_date = a.date or (datetime.now(timezone.utc).astimezone(IST) + timedelta(days=1)).strftime("%Y-%m-%d")
    day = datetime.strptime(plan_date, "%Y-%m-%d")
    if a.snapshot:
        snap = json.load(open(a.snapshot))
    else:
        from scripts.fetch_team_data import main as fetch

        path = f"data/plan_{slug('_'.join(a.team))}_{plan_date}.json"  # lead PII: data/ only
        os.makedirs("data", exist_ok=True)
        fetch(a.team, a.since or (day - timedelta(days=a.days)).strftime("%Y-%m-%d"), (day - timedelta(days=1)).strftime("%Y-%m-%d"), path)
        snap = json.load(open(path))
    os.makedirs(a.out, exist_ok=True)
    stem = f"call_plan_{slug('_'.join(a.team))}_"
    earlier = sorted(p for p in glob.glob(os.path.join(a.out, stem + "*.xlsx")) if p < os.path.join(a.out, stem + plan_date))[-3:]
    plan = build_plan(snap, plan_date, a.days, load_chances(a.chances), a.leader, a.target, a.tier_b_cap, read_carry(earlier), a.since,
                      a.sheet_cap)
    out = os.path.join(a.out, f"{stem}{plan_date}.xlsx")
    by_owner = write_workbook(plan, out)
    print(f"{plan['team']} plan for {plan['date_label']}: {out}"
          f"{' (carried unpaid links from ' + ', '.join(map(os.path.basename, earlier)) + ')' if earlier else ''}")
    print("chances: " + ", ".join(f"{t} {v}%" for t, v in plan["chances"].items())
          + ("" if load_chances(a.chances) else " (estimates; no measured tier outcomes yet)"))
    for o, rs in by_owner.items():
        print(f"{o:24} {len(rs):3} leads " + " ".join(f"{t}{sum(r['tier'] == t for r in rs)}" for t in TIERS))


if __name__ == "__main__":
    main()
