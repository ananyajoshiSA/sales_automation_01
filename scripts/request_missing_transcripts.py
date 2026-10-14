"""Ask the transcript API to generate transcripts for answered calls that have none yet (resumable).

Reads the leads to cover (``lead_id``, ``booked_ist`` 'YYYY-MM-DD HH:MM') and the search output of
``scripts/fetch_collection_transcripts.py``. A number needs generation when it has an answered call of 60 s or
more, from two days before the booking on, with no transcript text. Numbers are sent 10 to a request, 9 requests
to a run (one ``TranscriptClient`` per run), 7 s between requests and 60 s between runs. Each number sent is
logged to ``log`` with the time, and a number already in the log is not sent again.

Generation takes a while on the API's side: search the numbers again later and merge, the later search winning.

    python scripts/request_missing_transcripts.py data/coll_d14/to_read.csv data/coll_d14/transcripts.jsonl data/coll_d14/generate.jsonl
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta

import integrations  # noqa: F401  (loads .env)
from integrations.timeutil import IST, now_utc
from integrations.transcripts import TranscriptClient
from integrations.transcripts.client import MAX_NUMBERS_PER_REQUEST, MAX_REQUESTS_PER_RUN, TranscriptError

REQUEST_GAP = 7
RUN_GAP = 60
MIN_SECONDS = 60


def missing(leads_csv: str, searched: str) -> dict[str, int]:
    """Untranscribed answered calls of MIN_SECONDS+ since booking - 2 days, per number."""
    booked = {r["lead_id"]: datetime.strptime(r["booked_ist"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
              for r in csv.DictReader(open(leads_csv, encoding="utf-8"))}
    need: dict[str, int] = {}
    for line in open(searched, encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if row["lead_id"] not in booked:
            continue
        start = booked[row["lead_id"]] - timedelta(days=2)
        n = 0
        for c in row["calls"]:
            try:
                at = datetime.fromisoformat(c["start_api"]) if c.get("start_api") else None
            except ValueError:
                at = None
            # API clocks can be 5 h 30 min off: keep calls that may fall on the boundary
            if (at is None or at >= start - timedelta(hours=6)) and (c.get("duration") or 0) >= MIN_SECONDS \
                    and not (c.get("transcript") or "").strip():
                n += 1
        if n:
            need[row["phone"]] = need.get(row["phone"], 0) + n
    return need


def main(leads_csv: str, searched: str, log: str, sleep=time.sleep) -> None:
    need = missing(leads_csv, searched)
    sent = set()
    if os.path.exists(log):
        sent = {json.loads(line)["phone"] for line in open(log, encoding="utf-8") if line.strip()}
    todo = [p for p in need if p not in sent]
    print(f"{len(need)} numbers with {sum(need.values())} untranscribed calls, {len(sent)} already sent, "
          f"{len(todo)} to go", flush=True)
    per_run = MAX_NUMBERS_PER_REQUEST * MAX_REQUESTS_PER_RUN
    with open(log, "a", encoding="utf-8") as fh:
        for start in range(0, len(todo), per_run):
            if start:
                sleep(RUN_GAP)
            client = TranscriptClient()
            run = todo[start:start + per_run]
            for i in range(0, len(run), MAX_NUMBERS_PER_REQUEST):
                chunk = run[i:i + MAX_NUMBERS_PER_REQUEST]
                if i:
                    sleep(REQUEST_GAP)
                try:
                    answer = client.generate_transcripts(chunk)
                except TranscriptError as exc:
                    print(f"stopped: {exc}", file=sys.stderr, flush=True)
                    return
                at = now_utc().isoformat()
                for p in chunk:
                    fh.write(json.dumps({"phone": p, "calls": need[p], "sent_utc": at, "answer": answer}) + "\n")
                fh.flush()
            print(f"run done: {min(start + per_run, len(todo))}/{len(todo)} numbers", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
