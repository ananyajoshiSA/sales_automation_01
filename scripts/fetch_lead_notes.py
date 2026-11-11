"""Fetch the lead Notes (LeadSquared's Notes tab) written since a date for the leads in one or more id files. Read-only.

Callers write much of a lead's story as Notes ("number out of network", "not replying on WhatsApp"), which are not
activities, so the activity histories never carry them. The API cannot filter Notes by lead or date, so this pages the
whole account's Notes from the newest back to ``since`` (about 1,000 notes, one request, per two days) and keeps the
wanted leads'. Writes one JSON object per lead: {"lead_id": ..., "notes": [{"t", "by", "text"}, ...]} (oldest first).

    python scripts/fetch_lead_notes.py 2026-09-23 data/oct/notes.jsonl data/oct/ids.json [more_ids.json ...]
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime

from integrations.leadsquared import AdmissionDeferred, LeadSquaredClient
from integrations.timeutil import IST

DEFERRED_EXIT = 75


def clean(html: str) -> str:
    text = re.sub(r"<br\s*/?>", " / ", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip(" /")


def main(since: str, out_path: str, *id_files: str) -> int:
    wanted = set()
    for p in id_files:
        wanted |= set(json.load(open(p)))
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=IST)
    found, read = defaultdict(list), 0
    try:
        for n in LeadSquaredClient().iter_notes_since(start):
            read += 1
            if n.get("RelatedProspectId") in wanted:
                found[n["RelatedProspectId"]].append({"t": n.get("CreatedOn"), "by": n.get("CreatedByName") or "", "text": clean(n.get("Note"))})
    except AdmissionDeferred as exc:
        print(f"deferred by the LeadSquared gate: {exc}; run again later", file=sys.stderr)
        return DEFERRED_EXIT
    with open(out_path, "w") as fh:
        for lead_id, notes in found.items():
            fh.write(json.dumps({"lead_id": lead_id, "notes": sorted(notes, key=lambda x: x["t"] or "")}) + "\n")
    print(f"{read} notes read since {since}; {sum(len(v) for v in found.values())} notes on {len(found)} of {len(wanted)} leads",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
