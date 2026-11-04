"""Fetch everything the collection reports need: both Google Sheets and the LeadSquared leads tagged for collections.

Downloads the "Community Webinar Collection Sheet" and the "Bootcamp Pool data" sheet (whole workbooks, hidden tabs
included; IDs from ``COMMUNITY_COLLECTION_SHEET_ID`` and ``BOOTCAMP_POOL_SHEET_ID``). Then reads every lead whose
"Bootcamp collections" field is a community collection (``Community Webinar Collections...``) or a bootcamp
collection dated in or after ``first_month`` (default 2025-11), with phone, email, owner, stage and Funnel name.
Lead fields only, no activity histories, so it is a few hundred LeadSquared requests at most. Read-only.

Resumable: each tag is written to ``lsq_tags.jsonl`` once done; if the shared LeadSquared gate defers, the run stops
with exit code 75 and rerunning the same command carries on.

    python -m scripts.fetch_collections data/collections/2026-11-02 [first_month]
"""

from __future__ import annotations

import json
import os
import sys

from analytics.collections.tags import is_collection_tag, tag_kind, tag_date
from analytics.definitions import team_of
from integrations.gsheets import download_collection_sheets
from integrations.leadsquared import AdmissionDeferred, LeadSquaredClient
from integrations.timeutil import IST, now_utc

DEFERRED_EXIT = 75
TAG_FIELD = "mx_Bootcamp_collections"
COLUMNS = ["ProspectID", "FirstName", "LastName", "Phone", "Mobile", "EmailAddress", "OwnerId", "OwnerIdName",
           "ProspectStage", TAG_FIELD, "mx_Funnel_name", "CreatedOn", "ModifiedOn"]


def wanted_tags(options: list[str], first_month: str) -> list[str]:
    """Community tags always; bootcamp tags dated in or after ``first_month``, or undated."""
    out = []
    for tag in options:
        if not is_collection_tag(tag):
            continue
        day = tag_date(tag)
        if tag_kind(tag) == "community" or not day or day[:7] >= first_month:
            out.append(tag)
    return out


def main(out_dir: str, first_month: str = "2025-11") -> int:
    os.makedirs(out_dir, exist_ok=True)
    download_collection_sheets(out_dir)
    c = LeadSquaredClient()
    done_path = os.path.join(out_dir, "lsq_tags.jsonl")
    done = set()
    if os.path.exists(done_path):
        done = {json.loads(line)["tag"] for line in open(done_path) if line.strip()}
    try:
        field = next(f for f in c.get_lead_metadata() if f.get("SchemaName") == TAG_FIELD)
        tags = wanted_tags([o["Value"] for o in field.get("Options") or [] if o.get("Value")], first_month)
        with open(done_path, "a") as fh:
            for tag in tags:
                if tag in done:
                    continue
                leads = {l["ProspectID"]: l for l in c.iter_leads(TAG_FIELD, tag, page_size=1000, columns=COLUMNS,
                                                                    sort_by="CreatedOn", descending=False)}
                fh.write(json.dumps({"tag": tag, "leads": list(leads.values())}) + "\n")
                fh.flush()
        users = c.get_users()
    except AdmissionDeferred as e:
        print(f"LeadSquared asked us to wait ({e}); rerun the same command to carry on.", file=sys.stderr)
        return DEFERRED_EXIT
    teams = {u.get("ID"): team_of(u.get("MemberOfGroups")) for u in users if u.get("ID")}
    meta = {"fetched_at_ist": now_utc().astimezone(IST).strftime("%Y-%m-%d %H:%M"), "first_month": first_month,
            "tags": tags, "team_of_owner": teams}
    json.dump(meta, open(os.path.join(out_dir, "meta.json"), "w"), indent=1)
    n = sum(len(json.loads(line)["leads"]) for line in open(done_path) if line.strip())
    print(f"{len(tags)} collection tags, {n} tagged leads", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
