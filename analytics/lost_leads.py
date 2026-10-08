"""Leads that fall out of every call plan: open stages gone quiet, and leads marked dead without a conversation.

Plan step 6 (loopholes #10 and "wrongly dead"). Two lists, from a fetch_team_data.py snapshot that
covers at least the last ``--days`` days (several snapshots can be merged):

- stale_open.csv: leads in an open stage ("Call Back Later", "Discovery Call Done", "May buy
  later"...) with no real conversation (answered, 2+ min) in the last ``--days`` days. Capped per
  owner, closest stage first, WhatsApp first. Leads over the cap are kept in the file, marked
  over_cap, so nothing is dropped silently.
- dead_no_conversation.csv: leads moved to a dead stage (edited inside the snapshot) with no real
  conversation in the snapshot. "Not Interested" without a conversation breaks the call rule, so
  those are to recover; "Invalid" or "Irrelevant" with no answered call at all are to check.

    python -m analytics.lost_leads data/snap1.json [data/snap2.json ...] --out exports/lost [--days 15] [--cap 8]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

from analytics.definitions import DEAD_STAGES, OPEN_STAGES, REAL_CONVERSATION_SECS
from analytics.lead_priority import merge_snapshots
from analytics.team_report import IST, utc

# Closest to paying first; stages not listed sort last.
STAGE_ORDER = ["Follow Up For Closure", "Opportunity Created", "Counselled lead", "Roadmap\xa0Done", "Roadmap Done",
               "Discovery Call Done", "May buy later", "Call Back Later"]
STAGE_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}


def _ist(t: datetime | None) -> str:
    return t.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t else ""


def lost_leads(snap: dict, days: int = 15, cap: int = 8) -> dict:
    now = datetime.fromisoformat(snap["fetched_at"])
    since = now - timedelta(days=days)
    covered_from = datetime.strptime(snap["start"], "%Y-%m-%d").replace(tzinfo=IST) if snap.get("start") else None
    answered, real = defaultdict(list), defaultdict(list)
    for c in snap.get("calls", []):
        t = utc(c.get("start_utc"))
        if t and c.get("status") == "Answered":
            answered[c["lead_id"]].append(t)
            if (c.get("duration") or 0) >= REAL_CONVERSATION_SECS:
                real[c["lead_id"]].append(t)

    stale = []
    dead = []
    for l in snap["leads"]:
        lid, stage = l["ProspectID"], l.get("ProspectStage") or ""
        last_real = max(real.get(lid, []), default=None)
        created = utc(l.get("CreatedOn"))
        if stage in OPEN_STAGES and (last_real is None or last_real < since) and not (created and created >= since):
            stale.append({"_rank": STAGE_RANK.get(stage, len(STAGE_RANK)), "lead_id": lid,
                          "owner": l.get("OwnerIdName") or "", "stage": stage.replace("\xa0", " "),
                          "course": l.get("mx_Enquired_Course") or "", "last_real_conversation_ist": _ist(last_real),
                          "last_called_ist": _ist(utc(l.get("mx_Last_Called_Date_DT"))), "channel": "WhatsApp first"})
        modified = utc(l.get("ModifiedOn"))
        if stage in DEAD_STAGES - {"Duplicate"} and not real.get(lid) \
                and modified and (covered_from is None or modified >= covered_from):
            no_answer = not answered.get(lid)
            dead.append({"lead_id": lid, "owner": l.get("OwnerIdName") or "", "stage": stage,
                         "course": l.get("mx_Enquired_Course") or "", "marked_on_or_before_ist": _ist(modified),
                         "answered_calls": len(answered.get(lid, [])),
                         "action": "recover: call again" if stage == "Not Interested"
                         else ("check: no answered call" if no_answer else "check: only short calls")})

    # per owner: closest stage first, then the most recent conversation, then leads never talked to
    stale.sort(key=lambda r: r["last_real_conversation_ist"], reverse=True)
    stale.sort(key=lambda r: (r["owner"], r.pop("_rank"), r["last_real_conversation_ist"] == ""))
    per_owner = defaultdict(int)
    for r in stale:
        per_owner[r["owner"]] += 1
        r["over_cap"] = per_owner[r["owner"]] > cap
    dead.sort(key=lambda r: (r["action"] != "recover: call again", r["owner"], r["marked_on_or_before_ist"]))
    short = covered_from is not None and covered_from > since
    return {"stale_open": stale, "dead_no_conversation": dead,
            "summary": {"stale_open": len(stale), "stale_open_within_cap": sum(not r["over_cap"] for r in stale),
                        "dead_no_conversation": len(dead),
                        "recover_not_interested": sum(r["action"].startswith("recover") for r in dead),
                        "warning": (f"The snapshot starts {snap['start']}, less than {days} days back, so some leads "
                                    "listed as stale may have talked earlier than it covers.") if short else None}}


def write_csv(path: str, rows: list[dict]) -> None:
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--cap", type=int, default=8, help="stale open-stage leads per owner per day")
    a = ap.parse_args()
    snaps = [json.load(open(p)) for p in a.snapshots]
    snap = merge_snapshots(snaps) if len(snaps) > 1 else snaps[0]
    if len(snaps) > 1:
        snap["start"] = min(s.get("start", "9999") for s in snaps)
    r = lost_leads(snap, a.days, a.cap)
    os.makedirs(a.out, exist_ok=True)
    write_csv(os.path.join(a.out, "stale_open.csv"), r["stale_open"])
    write_csv(os.path.join(a.out, "dead_no_conversation.csv"), r["dead_no_conversation"])
    print(json.dumps(r["summary"], indent=1))


if __name__ == "__main__":
    main()
