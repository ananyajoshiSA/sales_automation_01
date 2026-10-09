"""Fetch every lead tagged for bootcamp collections in a range of bootcamp months, with full histories.

Reads the "Bootcamp collections" tag options, keeps those whose bootcamp date falls in the months asked
for, and reads every lead with one of those tags across the whole account (so leads parked outside the
collection teams are counted too). Writes ``meta.json``, ``tagged_leads.json`` and ``hist.jsonl``
(resumable) into ``out_dir``. Read-only.

    python scripts/fetch_bootcamp_collections.py 2026-06 2026-10 data/bc
"""

from __future__ import annotations

import json
import os
import sys

from analytics.bootcamp_collections import TEAMS, parse_tag
from integrations.leadsquared import LeadSquaredClient
from integrations.timeutil import now_utc
from scripts.fetch_lead_histories import main as fetch_histories

COLUMNS = ["ProspectID", "OwnerId", "OwnerIdName", "ProspectStage", "CreatedOn", "mx_Bootcamp_collections",
           "mx_Enquired_Course", "mx_Course_Fees", "mx_Bootcamp_attended", "mx_Next_follow_up_date"]


def main(first_month: str, last_month: str, out_dir: str) -> None:
    c = LeadSquaredClient()
    os.makedirs(out_dir, exist_ok=True)
    field = next(f for f in c.get_lead_metadata() if f.get("SchemaName") == "mx_Bootcamp_collections")
    tags = [o["Value"] for o in field.get("Options") or []
            if o.get("Value") and first_month <= parse_tag(o["Value"])[1][:7] <= last_month]
    leads, seen = [], set()
    for tag in tags:
        for l in c.iter_leads("mx_Bootcamp_collections", tag, page_size=1000, columns=COLUMNS):
            if l["ProspectID"] not in seen:
                seen.add(l["ProspectID"])
                leads.append(l)
    members = [(u, t) for t in TEAMS for u in c.get_users_in_group(t)]
    team_of_owner = {u["ID"]: t for u, t in members}
    team_callers = sorted({f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip() for u, _ in members})
    print(f"{len(tags)} bootcamps, {len(leads)} tagged leads", file=sys.stderr)
    json.dump(leads, open(os.path.join(out_dir, "tagged_leads.json"), "w"))
    ids = os.path.join(out_dir, "ids.json")
    json.dump([l["ProspectID"] for l in leads], open(ids, "w"))
    fetch_histories(ids, os.path.join(out_dir, "hist.jsonl"))
    json.dump({"fetched_at_utc": now_utc().strftime("%Y-%m-%d %H:%M:%S"), "bootcamps": tags,
               "months": [first_month, last_month], "team_of_owner": team_of_owner,
               "team_callers": team_callers},
              open(os.path.join(out_dir, "meta.json"), "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
