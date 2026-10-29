"""Claude's lead reading for a team's consolidated call plan: build the plan, pack leads for reviewers, merge them back.

The rules tier every lead (analytics.nightly_plan); Claude then reads the leads that matter, in parallel subagents,
and its tier, chance, opening line and ask replace the rule's. Three steps, all read-only against LeadSquared:

  plan   merge one or more fetch snapshots (scripts/fetch_team_resumable.py), build the rule plan with the AI voice
         agent's discovery (scripts/fetch_bot_calls.py output) and save it; also writes the leads to look up for that
         discovery, so `plan` is run twice: once without --bot-calls, then with.
  pack   choose the leads to read and split them into --chunks input files, plus the reviewers' brief filled in from
         docs/plan_review_brief.md. Chosen: every lead read before (a previous review file) that had an answered
         call or a new Zipteams note since --changed-since, and every lead not read before that the rules mark
         A, B or missed booked callback. Each reviewer reads the brief and writes out_<K>.json.
  merge  apply the reviews (in the order given; a later review of a lead wins) to the plan and write the workbook
         (analytics.call_plan.write_workbook) with its rows JSON next to it, for scripts/build_focus_list.py and
         scripts/build_plan_pdf.py. Tier D leaves the lead off the sheets.

Every file holds lead details: keep them under data/ and exports/.

    python scripts/plan_review.py plan data/snap_1014.json data/snap_1023.json --date 2026-10-23 --since 2026-09-01 \
        --leader "Inayat Khan" [--bot-calls data/bot.json] [--no-inbound] --out data/plan.json --leads-out data/leads.json
    python scripts/plan_review.py pack data/plan.json --reviews "data/review/*/out_*.json" --changed-since 2026-10-14T08:40Z \
        --chunks 8 --out-dir data/review/2026-10-23 --context docs/plan_teams/inayat.md --today "Friday 23 October 2026" \
        --data-to "23 Oct 12:15 IST" --previous "Wed 14 Oct (data to 14:10)" --promise-by "Mon 26 Oct"
    python scripts/plan_review.py merge data/plan.json --reviews "data/review/*/out_*.json" --team "Inayat Khan's teams" \
        --date-label "Fri 23 Oct 2026" --sheet-cap 80 --out exports/plans/call_plan_Inayat_2026-10-23.xlsx
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

from analytics.call_plan import write_workbook
from analytics.lead_priority import merge_snapshots
from analytics.nightly_plan import build_plan
from integrations.timeutil import utc

BRIEF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "plan_review_brief.md")
PROB_RANGE = {"A": (25, 50), "B": (8, 25), "C": (2, 8), "D": (0, 0)}
KEYS = ("lead_id", "tier", "prob_3d", "why", "opening_line", "next_action", "objection_to_prepare", "best_time",
        "promise_due", "stage_fix", "changed")
UNCHANGED = ("new lead", "unchanged", "no answer, unchanged")


def load_reviews(patterns: list[str]) -> dict[str, dict]:
    """lead_id -> review, files read in the order the patterns give them (sorted within a pattern); later ones win."""
    out = {}
    for pat in patterns:
        for f in sorted(glob.glob(pat)):
            for x in json.load(open(f)):
                out[x["lead_id"]] = x
    return out


def check_review(inp: list[dict], out: list[dict]) -> list[str]:
    """What is wrong with one reviewer's output against its input (empty when it is usable)."""
    errs = []
    if [x["lead_id"] for x in inp] != [x.get("lead_id") for x in out]:
        errs.append("lead_ids differ from the input or are out of order")
    for x in out:
        if set(KEYS) - set(x):
            errs.append(f"{x.get('lead_id')}: missing {sorted(set(KEYS) - set(x))}")
        lo, hi = PROB_RANGE.get(x.get("tier"), (None, None))
        if lo is None:
            errs.append(f"{x.get('lead_id')}: tier {x.get('tier')!r}")
        elif not isinstance(x.get("prob_3d"), int) or not lo <= x["prob_3d"] <= hi:
            errs.append(f"{x.get('lead_id')}: prob_3d {x.get('prob_3d')!r} outside {lo}-{hi} for tier {x['tier']}")
    return errs


def touched_since(snap: dict, cut) -> set[str]:
    """Leads with an answered call or a Zipteams note at or after ``cut``."""
    out = {c["lead_id"] for c in snap["calls"] if c.get("status") == "Answered" and (t := utc(c.get("start_utc"))) and t >= cut}
    out |= {a.get("RelatedProspectId") for a in snap.get("zip_activities", []) if (t := utc(a.get("CreatedOn"))) and t >= cut}
    return out


def choose(plan: dict, reviews: dict, touched: set[str]) -> tuple[list[str], list[str]]:
    """(leads to read again, leads to read for the first time), from the plan's tiered leads."""
    tiered = {t["lead_id"]: t for t in plan["tiered"]}
    again = [l for l in tiered if l in reviews and l in touched]
    first = [l for l, t in tiered.items() if l not in reviews and (t["tier"] in "AB" or t.get("callback_missed"))]
    return again, first


def _trim(text: str) -> str:
    text = re.sub(r"Link to the conversation:.*", "", text or "", flags=re.S)
    return re.sub(r"\s*\n\s*", " | ", text).strip(" |")[:700]


def pack_rows(plan: dict, ids: list[str], reviews: dict) -> list[dict]:
    """One reviewer input row per lead: what the plan knows about it, and its previous review if any."""
    cand = {c["lead_id"]: c for c in plan["candidates"]}
    tiered = {t["lead_id"]: t for t in plan["tiered"]}
    rows = []
    for lid in ids:
        c, t = cand.get(lid), tiered.get(lid)
        if not c or not t:
            continue
        x = {"lead_id": lid, "owner": c["owner"], "name": c["name"], "rule_tier": t["tier"], "stage": c["stage"],
             "course": c["course"], "zip_best_intent": c["best_zip_intent"], "buying_signals": c["buying_signals"],
             "flags": c["crosscheck_flags"], "dials": c["dials"], "longest_min": c["longest_call_min"],
             "talk_min": c["total_talk_min"], "lead_called_in": c["inbound_calls"],
             "last_conversation_ist": c["last_conversation_ist"], "days_since_conv": c["days_since_last_conversation"],
             "unanswered_since_conv": c["unanswered_since_last_conversation"], "next_follow_up_ist": c["next_follow_up_ist"],
             "callback_missed": t.get("callback_missed", False), "best_time": t.get("best_time", ""),
             "discovery_so_far": plan.get("discovery", {}).get(lid, ""),
             "zip_notes": [{**z, "summary": _trim(z.get("summary")), "reason": (z.get("reason") or "")[:300]}
                           for z in c["zip_notes"]][-4:],
             "call_log": c["call_log"][-12:]}
        if lid in reviews:
            x["previous_review"] = {k: reviews[lid].get(k) for k in KEYS if k not in ("lead_id", "changed")}
        rows.append(x)
    return sorted(rows, key=lambda r: (r["owner"], r["lead_id"]))


def fill_brief(template: str, **values: str) -> str:
    """The brief with every {NAME} filled; a placeholder left unfilled is an error, not a silent gap."""
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    if left := sorted(set(re.findall(r"\{([A-Z_]+)\}", out)) - {"K"}):
        raise ValueError(f"brief placeholders not filled: {', '.join(left)}")
    return out


def apply_reviews(plan: dict, reviews: dict) -> Counter:
    """Write each review onto the plan's tiered lead (or add the lead); returns (rule tier, read tier) moves."""
    cand = {c["lead_id"]: c for c in plan["candidates"]}
    tiered = {t["lead_id"]: t for t in plan["tiered"]}
    moved = Counter()
    for lid, x in reviews.items():
        c, t = cand.get(lid), tiered.get(lid)
        if c is None:  # closed, enrolled, another team's or out of the window since it was read
            continue
        moved[(t["tier"] if t else "-", x["tier"])] += 1
        if x["tier"] == "D":
            if t:
                t["tier"] = "D"
            continue
        base = t or {"owner": c["owner"], "lead_id": lid, "name": c["name"], "course": c.get("course") or "",
                     "phone": c.get("phone"), "callback_missed": False, "rank": c["pre_score"]}
        why = x["why"].strip()
        if x.get("stage_fix") and "Stage check" not in why:
            why += f" Stage check: {x['stage_fix']}."
        changed = (x.get("changed") or "").strip()
        base.update({"tier": x["tier"], "prob_3d": int(x["prob_3d"]), "why": why, "opening_line": x["opening_line"],
                     "next_action": x["next_action"], "objection_to_prepare": x.get("objection_to_prepare") or "",
                     "best_time": x.get("best_time") or base.get("best_time", ""),
                     "month_end_due": bool(x.get("promise_due")), "stage_fix": x.get("stage_fix") or "", "reviewed": True,
                     "changed": "" if changed.lower().startswith(UNCHANGED) else changed})
        tiered[lid] = base
    plan["tiered"] = list(tiered.values())
    return moved


def cmd_plan(a) -> None:
    snap = merge_snapshots([json.load(open(p)) for p in a.snapshots])
    last = json.load(open(a.snapshots[-1]))
    snap.update({"group": last.get("group"), "groups": last.get("groups"), "start": a.since, "payments": []})
    bot = json.load(open(a.bot_calls)) if a.bot_calls else None
    p = build_plan(snap, a.date, since=a.since, leaders=a.leader, inbound=not a.no_inbound, course=a.course,
                   bot_calls=bot, bot_campaign=a.bot_campaign)
    with open(a.out, "w") as fh:
        json.dump(p, fh, default=str)
    if a.snapshot_out:
        with open(a.snapshot_out, "w") as fh:
            json.dump(snap, fh)
    if a.leads_out:
        leads = {l["ProspectID"]: l for l in snap["leads"]}
        ids = {t["lead_id"] for t in p["tiered"]} | {f["lead_id"] for f in p["first"]} | {r["lead_id"] for r in p["revive"]}
        with open(a.leads_out, "w") as fh:
            json.dump([{"lead_id": i, "phone": leads[i].get("Phone")} for i in sorted(ids)], fh)
    print(f"plan {a.out}: tiered {dict(Counter(t['tier'] for t in p['tiered']))}, "
          f"missed callbacks {sum(bool(t.get('callback_missed')) for t in p['tiered'])}, new {len(p['first'])}, "
          f"revive {len(p['revive'])}")


def parse_utc(text: str) -> datetime:
    """A UTC time as "2026-10-14T08:40Z", "2026-10-14T08:40:00+00:00" or LeadSquared's "2026-10-14 08:40:00"."""
    t = utc(text)
    if t is None:
        try:
            t = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            t = None
    if t is None or t.tzinfo is None:
        raise SystemExit(f"--changed-since: can't read {text!r} as a UTC time, e.g. 2026-10-14T08:40Z")
    return t.astimezone(timezone.utc)


def cmd_pack(a) -> None:
    plan = json.load(open(a.plan))
    reviews = load_reviews(a.reviews)
    snap = json.load(open(a.snapshot))
    again, first = choose(plan, reviews, touched_since(snap, parse_utc(a.changed_since)))
    rows = pack_rows(plan, again + first, reviews)
    os.makedirs(a.out_dir, exist_ok=True)
    size = -(-len(rows) // a.chunks) if rows else 0
    for k in range(a.chunks):
        with open(os.path.join(a.out_dir, f"in_{k}.json"), "w") as fh:
            json.dump(rows[k * size:(k + 1) * size], fh, ensure_ascii=False, default=str)
    brief = fill_brief(open(a.template).read(), CONTEXT=open(a.context).read().strip(), TODAY=a.today, DATA_TO=a.data_to,
                       PREVIOUS=a.previous, PROMISE_BY=a.promise_by, DIR=os.path.abspath(a.out_dir))
    with open(os.path.join(a.out_dir, "brief.md"), "w") as fh:
        fh.write(brief)
    print(f"{len(rows)} leads to read ({len(again)} read before with something new, {len(first)} first reads) in "
          f"{a.chunks} files of up to {size} -> {a.out_dir}; brief {os.path.join(a.out_dir, 'brief.md')}")


def cmd_check(a) -> None:
    bad = 0
    for k in range(a.chunks):
        inp = json.load(open(os.path.join(a.dir, f"in_{k}.json")))
        path = os.path.join(a.dir, f"out_{k}.json")
        errs = ["missing"] if not os.path.exists(path) else check_review(inp, json.load(open(path)))
        bad += bool(errs)
        print(f"out_{k}: " + ("ok" if not errs else "; ".join(errs[:5])))
    raise SystemExit(1 if bad else 0)


def cmd_merge(a) -> None:
    plan = json.load(open(a.plan))
    reviews = load_reviews(a.reviews)
    moved = apply_reviews(plan, reviews)
    plan.update({"sheet_cap": a.sheet_cap, "team": a.team, "date_label": a.date_label})
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    by_owner = write_workbook(plan, a.out)
    tiers = Counter(r["tier"] for rs in by_owner.values() for r in rs)
    rows = [r for rs in by_owner.values() for r in rs]
    print(f"read {len(reviews)} leads; rule -> read {sorted(moved.items())}")
    print(f"sheets {dict(sorted(tiers.items()))}; promises due {sum(bool(r.get('month_end_due')) for r in rows)}; "
          f"updated from the latest calls {sum(bool(r.get('changed')) for r in rows)} -> {a.out}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("snapshots", nargs="+")
    p.add_argument("--date", required=True)
    p.add_argument("--since", required=True)
    p.add_argument("--leader", action="append", default=[])
    p.add_argument("--no-inbound", action="store_true")
    p.add_argument("--course")
    p.add_argument("--bot-calls")
    p.add_argument("--bot-campaign")
    p.add_argument("--out", required=True)
    p.add_argument("--snapshot-out", help="also save the merged snapshot (pack reads it)")
    p.add_argument("--leads-out", help="the plan's leads to look up with scripts/fetch_bot_calls.py")
    k = sub.add_parser("pack")
    k.add_argument("plan")
    k.add_argument("--snapshot", required=True, help="the merged snapshot `plan --snapshot-out` saved")
    k.add_argument("--reviews", nargs="*", default=[], help="earlier review files (globs), oldest first")
    k.add_argument("--changed-since", required=True, help="UTC time of the last plan's data, e.g. 2026-10-14T08:40Z")
    k.add_argument("--chunks", type=int, default=8)
    k.add_argument("--out-dir", required=True)
    k.add_argument("--template", default=BRIEF)
    k.add_argument("--context", required=True, help="file with the team's paragraph for the brief")
    k.add_argument("--today", required=True)
    k.add_argument("--data-to", required=True)
    k.add_argument("--previous", default="none (first reading)")
    k.add_argument("--promise-by", required=True)
    c = sub.add_parser("check")
    c.add_argument("dir")
    c.add_argument("--chunks", type=int, default=8)
    m = sub.add_parser("merge")
    m.add_argument("plan")
    m.add_argument("--reviews", nargs="+", required=True, help="review files (globs), oldest first")
    m.add_argument("--team", required=True)
    m.add_argument("--date-label", required=True)
    m.add_argument("--sheet-cap", type=int, default=80)
    m.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"plan": cmd_plan, "pack": cmd_pack, "check": cmd_check, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
