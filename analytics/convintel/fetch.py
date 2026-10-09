"""Transcript search queue: finds the recording and transcript of every connected call, one-to-one, with rechecks.

Each run searches the lead numbers that are due (``Registry.due_numbers``): numbers with calls never searched
first, newest call first, then scheduled rechecks (2 h, 24 h, 72 h and 7 days after the call) and failed
searches. One run is one TranscriptClient: at most 9 requests of 10 numbers, which the client enforces and this
module never raises. The API also refuses more than about 10 requests a minute per key (HTTP 429, seen on 8 Oct
2026), so requests are spaced ``PACE_S`` apart and a 429 ends the run with the rest left queued. Continuous
processing is repeated runs (``python -m analytics.convintel run --loop``); nothing is ever dropped from the
queue because a run ran out of requests.

A search returns every recording ever made on a number, so one search settles all of that number's calls. A
recording belongs to the call on the same number that started within 10 minutes of it (or exactly 5 h 30 min
off when the durations agree, ``match_gap``); each recording goes to at most one call and each call gets at most
one recording, closest first. Recordings in an inventoried period that match no call are kept as orphans for
reconciliation. Transcript text is saved under data/convintel/transcripts/ (git-ignored) and never logged.

    python -m analytics.convintel fetch-transcripts [--requests 9] [--pace 7]
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime

from analytics.convintel import schema as S
from analytics.convintel.store import LOOKUP_LAG, Registry, ts
from integrations.timeutil import utc
from integrations.transcripts.client import (MATCH_MINUTES, Call, RequestBudgetExceeded, TranscriptClient,
                                             TranscriptError, match_gap, normalize_phone)

TRANSCRIPT_DIR = os.path.join("data", "convintel", "transcripts")
PACE_S = 7.0
OPEN_STATES = (S.T_NOT_LOOKED_UP, S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED, S.T_LOOKUP_FAILED)


def source_id(x: Call) -> str:
    """The API gives recordings no id, so one is made from what identifies a recording."""
    key = f"{x.kind}|{x.audio_url or ''}|{x.start_time.isoformat() if x.start_time else ''}|{x.duration}|{x.phone}"
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def save_text(call: dict, text: str, base: str = TRANSCRIPT_DIR) -> tuple[str, str, int]:
    """(path, sha256, words) of the transcript written for one call."""
    folder = os.path.join(base, call.get("ist_day") or "unknown-day")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{call['call_id']}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path, hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text.split())


def read_text(call: dict) -> str | None:
    """The saved transcript if it is still there and unchanged, else None (it is then searched for again)."""
    path = call.get("transcript_ref")
    if not path or not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    if call.get("transcript_sha256") and hashlib.sha256(text.encode("utf-8")).hexdigest() != call["transcript_sha256"]:
        return None
    return text


def match(calls: list[dict], recordings: list[Call]) -> list[tuple[dict, Call, float, int]]:
    """(call, recording, gap in minutes, shift) pairs, one-to-one, closest first."""
    pairs = []
    for c in calls:
        t = utc(c.get("start_utc"))
        for x in recordings:
            if t and x.start_time:
                gap, shift = match_gap(x, t, c.get("duration_s") or 0)
                if gap <= MATCH_MINUTES:
                    pairs.append((gap, c["call_id"], source_id(x), c, x, shift))
    pairs.sort(key=lambda p: (p[0], p[1], p[2]))
    used_c, used_x, out = set(), set(), []
    for gap, cid, sid, c, x, shift in pairs:
        if cid not in used_c and sid not in used_x:
            used_c.add(cid)
            used_x.add(sid)
            out.append((c, x, gap, shift))
    return out


def inventoried_windows(reg: Registry) -> list[tuple[datetime, datetime]]:
    """The UTC spans whose calls are in the registry (whole IST days, or the sample windows read)."""
    out = []
    for r in reg.q("SELECT value FROM meta WHERE key LIKE 'source:%'"):
        m = json.loads(r["value"])
        if (a := utc(m.get("from_utc"))) and (b := utc(m.get("to_utc"))):
            out.append((a, b))
    return out


def process_number(reg: Registry, number: str, recordings: list[Call], now: datetime,
                   inventoried: list[tuple[datetime, datetime]], base: str = TRANSCRIPT_DIR) -> Counter:
    """Settle every open call on one number from its search result."""
    out = Counter()
    calls = reg.calls_for_number(number)
    taken = {c["transcript_source_id"] for c in calls if c["transcript_state"] == S.T_FOUND and c["transcript_source_id"]}
    free = [x for x in recordings if source_id(x) not in taken]
    open_calls = [c for c in calls if c["transcript_expected"] and c["transcript_state"] in OPEN_STATES]
    matched = match(open_calls, free)
    done = set()
    for c, x, gap, shift in matched:
        fields = {"transcript_source_id": source_id(x), "transcript_api_duration": x.duration,
                  "transcript_shift_min": shift, "transcript_gap_min": round(gap, 2)}
        if x.has_transcript:
            path, sha, words = save_text(c, x.transcript, base)
            reg.set_transcript(c["call_id"], S.T_FOUND, now, transcript_ref=path, transcript_sha256=sha,
                               transcript_words=words, **fields)
            out["found"] += 1
        else:
            reg.set_transcript(c["call_id"], S.T_NOT_TRANSCRIBED, now, transcript_ref=None, **fields,
                               lookup_note="the recording exists but has no transcript text yet")
            out["not_transcribed"] += 1
        done.add(c["call_id"])
    cutoff = now - LOOKUP_LAG
    for c in open_calls:
        if c["call_id"] in done:
            continue
        if (t := utc(c["start_utc"])) and t > cutoff:
            out["too_recent"] += 1      # stays queued; the dialer and transcriber may not have caught up
            continue
        reg.set_transcript(c["call_id"], S.T_NOT_FOUND, now, transcript_source_id=None,
                           lookup_note=f"{len(recordings)} recordings on this number, none within {MATCH_MINUTES} min")
        out["not_found"] += 1
    used = taken | {source_id(x) for _, x, _, _ in matched}
    orphans = [{"source_id": source_id(x), "number": number, "kind": x.kind, "start_utc": ts(x.start_time),
                "duration": x.duration, "agent_name": x.agent_name, "has_text": int(x.has_transcript)}
               for x in recordings if x.start_time and source_id(x) not in used
               and any(a <= x.start_time <= b for a, b in inventoried)]
    if orphans:
        reg.save_orphans(orphans, now)
        out["orphans"] += len(orphans)
    reg.record_lookup(number, now, True, len(recordings))
    reg.refresh([c["call_id"] for c in calls], now)
    return out


def recordings_for(raw: dict, number: str) -> list[Call]:
    groups = next((v for k, v in raw.items() if normalize_phone(k) == number), None) or {}
    return [Call.from_api(number, kind, r) for kind, key in (("sales", "sales_call"), ("support", "support_calls"))
            for r in groups.get(key) or []]


def run_fetch(reg: Registry, now: datetime, client: TranscriptClient | None = None, requests: int | None = None,
              pace_s: float = PACE_S, sleep=time.sleep, base: str = TRANSCRIPT_DIR, log=lambda *a: None) -> Counter:
    """One run: search the due numbers with at most ``requests`` (<= the client's 9) requests."""
    client = client or TranscriptClient(max_retries=0)   # a retry would spend the run's budget on a 429
    n_req = min(requests or client.max_requests, client.requests_remaining)
    numbers = reg.due_numbers(now, n_req * client.batch_size)
    inventoried = inventoried_windows(reg)
    run = reg.start_run("fetch", {"numbers": len(numbers), "requests": n_req}, now)
    out = Counter(numbers=len(numbers))
    for i in range(0, len(numbers), client.batch_size):
        chunk = numbers[i:i + client.batch_size]
        if i:
            sleep(pace_s)
        try:
            raw = client.search_raw(chunk)
            out["requests"] += 1
        except RequestBudgetExceeded:
            break
        except TranscriptError as e:
            out["requests"] += 1
            out["failed_numbers"] += len(chunk)
            for n in chunk:
                reg.lookup_failed(n, now, f"HTTP {e.status_code}" if e.status_code else str(e)[:120])
            if e.status_code == 429:
                out["rate_limited"] += 1
                log("rate limited (HTTP 429): stopping this run, the rest stays queued")
                break
            continue
        for n in chunk:
            out.update(process_number(reg, n, recordings_for(raw or {}, n), now, inventoried, base))
        reg.checkpoint(run, dict(out))
    reg.finish_run(run, "done", dict(out), now)
    log(f"searched {out['numbers']} numbers with {out['requests']} requests: {dict(out)}")
    return out
