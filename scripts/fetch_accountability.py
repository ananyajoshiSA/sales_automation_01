"""Inputs for analytics.accountability: users, the leads whose Assigned On falls in an IST date range, and
their full activity histories (resumable). ``--owner`` limits it to the leads now in one account.

Lead rows carry no names, phones or emails, only IDs, owners and assignment fields.

    python scripts/fetch_accountability.py 2026-10-04 2026-10-09 data/accountability [--owner "Rinku Jhala"]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta

from integrations.leadsquared import LeadSquaredClient
from integrations.leadsquared.client import format_datetime
from integrations.timeutil import ist_day_start, utc
from scripts.fetch_lead_histories import main as fetch_histories

COLUMNS = ["ProspectID", "OwnerId", "OwnerIdName", "CreatedOn", "CreatedByName", "ModifiedOn", "ProspectStage",
           "mx_Assigned_By", "mx_Assigned_On", "mx_Next_follow_up_date", "mx_Follow_up_date_and_time"]


def owner_leads(c: LeadSquaredClient, owner_id: str, d0, d1) -> list[dict]:
    """The owner's leads, newest Assigned On first, read until Assigned On drops below ``d0``."""
    out, page = [], 1
    while True:
        batch = c.search_leads("OwnerId", owner_id, columns=COLUMNS, page_index=page, page_size=1000,
                               sort_by="mx_Assigned_On", descending=True)
        out += [l for l in batch if (t := utc(l.get("mx_Assigned_On"))) and d0 <= t < d1]
        last = utc(batch[-1].get("mx_Assigned_On")) if batch else None
        if len(batch) < 1000 or not last or last < d0:
            return out
        page += 1


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("out_dir")
    p.add_argument("--owner", help="account name, e.g. 'Rinku Jhala'")
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args(argv)
    d0, d1 = ist_day_start(a.start), ist_day_start(a.end) + timedelta(days=1)
    c = LeadSquaredClient()
    os.makedirs(a.out_dir, exist_ok=True)
    users = c.get_users()
    json.dump(users, open(os.path.join(a.out_dir, "users.json"), "w"))
    if a.owner:
        ids = [u["ID"] for u in users if f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip() == a.owner]
        if len(ids) != 1:
            raise SystemExit(f"{len(ids)} users are named {a.owner!r}")
        leads = owner_leads(c, ids[0], d0, d1)
    else:
        leads = [l for l in c.iter_leads("mx_Assigned_On", format_datetime(d0), operator=">=", page_size=1000,
                                         columns=COLUMNS) if (t := utc(l.get("mx_Assigned_On"))) and t < d1]
    leads = list({l["ProspectID"]: l for l in leads}.values())
    json.dump(leads, open(os.path.join(a.out_dir, "leads.json"), "w"))
    json.dump([l["ProspectID"] for l in leads], open(os.path.join(a.out_dir, "lead_ids.json"), "w"))
    print(f"{len(leads)} leads", flush=True)
    fetch_histories(os.path.join(a.out_dir, "lead_ids.json"), os.path.join(a.out_dir, "histories.jsonl"), a.threads)


if __name__ == "__main__":
    main()
