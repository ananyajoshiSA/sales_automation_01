"""Transcripts for the audited calls: Salesa, searched by the number on each call and the lead's phone fields,
then matched to the LeadSquared call (the call's time is LeadSquared's; Salesa clocks can be 5 h 30 min off)."""

from __future__ import annotations

import json
import os
import sys
import time

from integrations.timeutil import utc
from integrations.transcripts import TranscriptClient
from integrations.transcripts.client import (MAX_NUMBERS_PER_REQUEST, MAX_REQUESTS_PER_RUN, Call, match_to_calls,
                                             normalize_phone)

PER_RUN = MAX_NUMBERS_PER_REQUEST * MAX_REQUESTS_PER_RUN


def numbers_of(entry: dict) -> list[str]:
    """Every number to search for one lead: each call's own number first, then the lead's Phone and Mobile."""
    raw = [c.get("lead_number") for c in entry["calls"]] + [entry["lead"].get("Phone"), entry["lead"].get("Mobile")]
    return list(dict.fromkeys(n for n in (normalize_phone(x) for x in raw) if n))


def ls_calls(entry: dict) -> list[dict]:
    """The lead's answered calls in the shape match_to_calls needs; a call with no number uses the lead's phone."""
    fallback = next(iter(numbers_of(entry)), None)
    return [{**c, "t": utc(c["start_utc"]), "lead_number": c.get("lead_number") or fallback}
            for c in entry["calls"] if c.get("status") == "Answered"]


def search(numbers: list[str], cache: str, pause: int = 60, client_factory=TranscriptClient, sleep=time.sleep) -> list[Call]:
    """Salesa calls for ``numbers``, in runs of at most 90 numbers (the client's own cap), cached per number."""
    raw: dict = json.load(open(cache)) if os.path.exists(cache) else {}
    todo = [n for n in numbers if n not in raw]
    while todo:
        chunk, todo = todo[:PER_RUN], todo[PER_RUN:]
        got = client_factory().search_raw(chunk, call_status=None)
        for n in chunk:
            raw[n] = got.get(n) or {}
        json.dump(raw, open(cache, "w"))
        print(f"transcripts: searched {len(chunk)} numbers, {len(todo)} left", file=sys.stderr, flush=True)
        if todo:
            sleep(pause)
    out = []
    for n in numbers:
        for kind, key in (("sales", "sales_call"), ("support", "support_calls")):
            out.extend(Call.from_api(n, kind, r) for r in (raw.get(n) or {}).get(key) or [])
    return out


def attach(entries: list[dict], out_dir: str, **kw) -> dict:
    """Puts ``transcript`` (text or None) and ``tz_shift`` on every answered call. Returns counts."""
    nums = list(dict.fromkeys(n for e in entries for n in numbers_of(e)))
    api = search(nums, f"{out_dir}/salesa_raw.json", **kw)
    found = answered = 0
    for e in entries:
        lc = ls_calls(e)
        m = match_to_calls(api, lc)
        for c in e["calls"]:
            hit = m.get(c.get("activity_id"))
            c["transcript"] = hit[0].transcript.strip() if hit and hit[0].transcript.strip() else None
            c["tz_shift"] = hit[1] if hit else None
            c["salesa_agent"] = hit[0].agent_name if hit else None
            if c.get("status") == "Answered":
                answered += 1
                found += bool(c["transcript"])
    return {"numbers": len(nums), "answered_calls": answered, "with_transcript": found}
