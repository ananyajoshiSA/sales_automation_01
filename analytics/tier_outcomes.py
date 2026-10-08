"""How each call-plan tier actually converted, against the chance the plan gave it (plan step 9).

Reads earlier plan JSONs (written next to each workbook by analytics.call_plan / nightly_plan) and a
team snapshot fetched after them, and counts, per tier, how many planned leads enrolled within 3
days of the plan day (the window the "Est. chance (3 days)" column predicts). Leads already enrolled
before the plan day are left out. Enrolment dates come from the leads' stage history
(scripts/fetch_lead_histories.py): LeadSquared's enrolment date field is usually blank, and reading it
alone would record every tier as 0%. Writes tier_chances.json; a tier's measured rate is used by
nightly_plan only once it has at least --min-leads planned leads, otherwise the estimate stays.

    python -m analytics.tier_outcomes exports/plans/call_plan_Team_X_2026-10-09.json [...] \
        --snapshot data/snap_after.json --histories data/hist.jsonl [--out exports/tier_chances.json]

The plan day is read from the file name (YYYY-MM-DD), or given as path:YYYY-MM-DD.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from analytics.call_plan import TIERS
from analytics.team_report import IST, enrolment_credit, load_histories

WINDOW_DAYS = 3
MIN_LEADS = 30


def plan_day(arg: str) -> tuple[str, datetime]:
    path, _, day = arg.partition(":") if re.search(r":\d{4}-\d{2}-\d{2}$", arg) else (arg, "", "")
    day = day or (re.findall(r"\d{4}-\d{2}-\d{2}", os.path.basename(path)) or [None])[-1]
    if not day:
        raise SystemExit(f"no plan day in {arg!r}; pass it as path:YYYY-MM-DD")
    return path, datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)


def tier_outcomes(plans: list[tuple[dict, datetime]], snap: dict, histories: dict | None = None,
                  min_leads: int = MIN_LEADS) -> dict:
    histories = histories or {}
    leads = {l["ProspectID"]: l for l in snap["leads"]}
    enrolled_at = {}
    for lid, l in leads.items():
        if e := enrolment_credit(l, histories.get(lid), []):
            enrolled_at[lid] = e["t"]
    stats = defaultdict(lambda: {"leads": 0, "enrolled": 0, "predicted_sum": 0.0})
    already = not_in_snapshot = 0
    latest = datetime.fromisoformat(snap["fetched_at"])
    for by_owner, day in plans:
        if day + timedelta(days=WINDOW_DAYS) > latest:
            raise SystemExit(f"the snapshot was fetched before the {day:%d %b} plan's {WINDOW_DAYS}-day window ended")
        for rows in by_owner.values():
            for r in rows:
                lid = r["lead_id"]
                if lid not in leads:
                    not_in_snapshot += 1
                    continue
                t = enrolled_at.get(lid)
                if t and t < day:
                    already += 1
                    continue
                s = stats[r["tier"]]
                s["leads"] += 1
                s["predicted_sum"] += float(r.get("prob_3d") or 0)
                s["enrolled"] += bool(t and t < day + timedelta(days=WINDOW_DAYS))
    tiers = {}
    for t in TIERS:
        if t not in stats:
            continue
        s = stats[t]
        actual = round(100 * s["enrolled"] / s["leads"], 1)
        tiers[t] = {"leads": s["leads"], "enrolled": s["enrolled"], "actual_%": actual,
                    "predicted_%": round(s["predicted_sum"] / s["leads"], 1),
                    "use_%": actual if s["leads"] >= min_leads else None}
    return {"window_days": WINDOW_DAYS, "min_leads": min_leads, "plans": len(plans), "tiers": tiers,
            "left_out": {"enrolled_before_plan_day": already, "not_in_snapshot": not_in_snapshot},
            "note": "Enrolment dates come from stage history, falling back to the enrolment date field."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plans", nargs="+")
    ap.add_argument("--snapshot", required=True, help="team snapshot fetched after the plans' 3-day windows")
    ap.add_argument("--histories", required=True, help="fetch_lead_histories.py file for the planned leads (first-ever enrolment dates)")
    ap.add_argument("--min-leads", type=int, default=MIN_LEADS)
    ap.add_argument("--out", default="exports/tier_chances.json")
    a = ap.parse_args()
    plans = [(json.load(open(p)), d) for p, d in map(plan_day, a.plans)]
    r = tier_outcomes(plans, json.load(open(a.snapshot)), load_histories(a.histories), a.min_leads)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(r, open(a.out, "w"), indent=1)
    print(f"{'tier':4} {'leads':>6} {'enrolled':>9} {'actual':>7} {'planned':>8}  used next")
    for t, v in r["tiers"].items():
        used = f"{v['use_%']}%" if v["use_%"] is not None else f"estimate (under {a.min_leads} leads)"
        print(f"{t:4} {v['leads']:6} {v['enrolled']:9} {v['actual_%']:6}% {v['predicted_%']:7}%  {used}")
    print(f"saved {a.out}")


if __name__ == "__main__":
    main()
