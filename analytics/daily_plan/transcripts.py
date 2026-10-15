"""Salesa transcripts for one day's answered team calls, longest first.

Each run is a fresh TranscriptClient (at most 9 requests of 10 numbers); runs are separated by a pause.
Already-fetched numbers are skipped, so a stopped job can be restarted."""

from __future__ import annotations

import json
import os
import sys
import time

from analytics.daily_plan.common import Snap
from integrations.transcripts import MAX_NUMBERS_PER_REQUEST, MAX_REQUESTS_PER_RUN, TranscriptClient, normalize_phone

PER_RUN = MAX_NUMBERS_PER_REQUEST * MAX_REQUESTS_PER_RUN


def numbers_for(snap: Snap, date: str, extra_lead_ids=()) -> list[str]:
    """Numbers of the day's answered team calls, longest call first, then any extra leads."""
    day = sorted((c for c in snap.day(date) if c["status"] == "Answered" and c.get("user_id") in snap.users),
                 key=lambda c: -(c.get("duration") or 0))
    nums = []
    for c in day:
        n = normalize_phone(snap.phone(c))
        if n and n not in nums:
            nums.append(n)
    for lid in extra_lead_ids:
        n = normalize_phone((snap.leads.get(lid) or {}).get("Phone"))
        if n and n not in nums:
            nums.append(n)
    return nums


def fetch(numbers: list[str], out_dir: str, pause: int = 60, client_factory=TranscriptClient, sleep=time.sleep) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    done = set()
    runs = sorted(f for f in os.listdir(out_dir) if f.startswith("run_"))
    for f in runs:
        done |= set(json.load(open(os.path.join(out_dir, f))))
    todo = [n for n in numbers if n not in done]
    k = len(runs)
    while todo:
        chunk, todo = todo[:PER_RUN], todo[PER_RUN:]
        client = client_factory()
        raw = client.search_raw(chunk)
        for n in chunk:
            raw.setdefault(n, {})
        json.dump(raw, open(os.path.join(out_dir, f"run_{k:02d}.json"), "w"))
        print(f"transcripts run {k}: {len(chunk)} numbers, {client.requests_made} requests", file=sys.stderr, flush=True)
        k += 1
        if todo:
            sleep(pause)
    return load(out_dir)


def load(out_dir: str) -> dict:
    tx = {}
    if os.path.isdir(out_dir):
        for f in sorted(os.listdir(out_dir)):
            if f.startswith("run_"):
                tx.update(json.load(open(os.path.join(out_dir, f))))
    return tx


def coverage(tx: dict, numbers: list[str]) -> tuple[int, int]:
    """(numbers with at least one transcript text, numbers without)."""
    have = sum(1 for n in numbers if any(((x.get("transcript") or {}).get("text") or "").strip()
                                         for k in ("sales_call", "support_calls") for x in ((tx.get(n) or {}).get(k) or [])))
    return have, len(numbers) - have
