"""Call transcripts for the open collection leads, from the transcript API (read-only, resumable).

Takes the lead IDs in the collection audit's ``open_leads.csv`` and their phones from
``scripts/fetch_collection_contacts.py``, and searches the numbers in runs that keep the repository's limits:
10 numbers a request, 9 requests a run (one ``TranscriptClient`` per run), requests 7 s apart and 60 s
between runs (the API answers HTTP 429 above about 10 requests a minute). Writes one JSON line per number
searched, with every sales and support call found and its transcript; a number already in ``out`` is not
searched again. API times can be 5 h 30 min off, so they are kept as given and only used to order calls.

Output holds call transcripts of real leads: keep it under data/ (git-ignored).

    python scripts/fetch_collection_transcripts.py exports/collection_audit/open_leads.csv data/coll_now/contacts.json data/coll_eve/transcripts.jsonl
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time

import integrations  # noqa: F401  (loads .env)
from integrations.transcripts import TranscriptClient
from integrations.transcripts.client import MAX_NUMBERS_PER_REQUEST, MAX_REQUESTS_PER_RUN, TranscriptError, normalize_phone

REQUEST_GAP = 7
RUN_GAP = 60


def main(open_csv: str, contacts_path: str, out: str) -> None:
    contacts = json.load(open(contacts_path))["leads"]
    lead_of: dict[str, str] = {}
    for r in csv.DictReader(open(open_csv, encoding="utf-8")):
        n = normalize_phone(contacts.get(r["lead_id"], {}).get("phone"))
        if n:
            lead_of.setdefault(n, r["lead_id"])
    done = set()
    if os.path.exists(out):
        done = {json.loads(line)["phone"] for line in open(out, encoding="utf-8") if line.strip()}
    todo = [n for n in lead_of if n not in done]
    print(f"{len(lead_of)} numbers, {len(done)} already searched, {len(todo)} to go", flush=True)
    per_run = MAX_NUMBERS_PER_REQUEST * MAX_REQUESTS_PER_RUN
    with open(out, "a", encoding="utf-8") as fh:
        for start in range(0, len(todo), per_run):
            if start:
                time.sleep(RUN_GAP)
            client = TranscriptClient()
            run = todo[start:start + per_run]
            for i in range(0, len(run), MAX_NUMBERS_PER_REQUEST):
                chunk = run[i:i + MAX_NUMBERS_PER_REQUEST]
                if i:
                    time.sleep(REQUEST_GAP)
                try:
                    calls = client.search(chunk)
                except TranscriptError as exc:
                    print(f"stopped: {exc}", file=sys.stderr, flush=True)
                    return
                found: dict[str, list] = {n: [] for n in chunk}
                for c in calls:
                    found.setdefault(c.phone, []).append({
                        "kind": c.kind, "agent": c.agent_name, "start_api": c.start_time.isoformat() if c.start_time else None,
                        "duration": c.duration, "transcript": c.transcript})
                for n in chunk:
                    fh.write(json.dumps({"phone": n, "lead_id": lead_of[n], "calls": found.get(n, [])}, ensure_ascii=False) + "\n")
                fh.flush()
            print(f"run done: {min(start + per_run, len(todo))}/{len(todo)} numbers", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
