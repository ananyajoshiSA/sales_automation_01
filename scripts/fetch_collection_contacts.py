"""Fresh name, phone, stage and owner for every lead with a collection tag, just before a plan is built (read-only).

A plan is read from data pulled earlier; leads paid or dropped since then must leave the call sheets, and the
sheets need each lead's name and phone. Writes {lead_id: {name, phone, stage, owner}} to ``out``.

    python scripts/fetch_collection_contacts.py data/coll_now/meta.json data/coll_now/contacts.json
"""

from __future__ import annotations

import json
import re
import sys

import integrations  # noqa: F401  (loads .env)
from integrations.leadsquared import LeadSquaredClient
from integrations.timeutil import now_utc

COLUMNS = ["ProspectID", "FirstName", "LastName", "Phone", "ProspectStage", "OwnerIdName"]


def contact(lead: dict) -> dict:
    name = " ".join(x for x in ((lead.get("FirstName") or "").strip(), (lead.get("LastName") or "").strip()) if x)
    name = re.sub(r"\b(\w+) \1\b", r"\1", name, flags=re.I)  # the surname is often held twice
    return {"name": name, "phone": lead.get("Phone") or "", "stage": lead.get("ProspectStage") or "",
            "owner": lead.get("OwnerIdName") or ""}


def main(meta_path: str, out: str) -> None:
    tags = json.load(open(meta_path))["bootcamps"]
    c = LeadSquaredClient()
    found = {}
    for tag in tags:
        for lead in c.iter_leads("mx_Bootcamp_collections", tag, page_size=1000, columns=COLUMNS):
            found[lead["ProspectID"]] = contact(lead)
    json.dump({"fetched_at_utc": now_utc().strftime("%Y-%m-%d %H:%M:%S"), "leads": found}, open(out, "w"))
    print(f"{len(found)} leads -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
