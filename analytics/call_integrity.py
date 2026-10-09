"""Call integrity: long calls that may be fake, stretched, or empty. Every flag means "needs review", never proof.

Two kinds of signal (Parameters v1.2, P67-P74):

- From the call log, for every answered call of 2+ minutes by a LeadSquared user (no API budget):
  overlap (the caller was on another answered call at the same time), repeat (3+ real conversations
  with the same lead that day), and just-over-2-minutes bunching (a caller whose share of real
  conversations lasting 120-149 s is at least twice the day's account-wide share).
- From transcripts, for a sample (the API allows 90 numbers a run): no content (under 30 words),
  thin (under 60 words a minute; the 5 Oct sample's median was 148), and a recorded message,
  IVR or hold text in the opening words, or one phrase looping (15%+ of the words).

The transcript text has no speaker labels or timestamps, so the customer's share of the talk and
silences can't be measured directly; words per minute stands in for silence.

Per-call evidence (call ID, caller, duration, words, flags; no lead details) goes to
data/report_{DATE}/integrity_calls.csv and the summary to integrity.json, which the daily report reads.

    python -m analytics.call_integrity 2026-10-05 [--limit 10] [--data data/report_2026-10-05] [--no-transcripts]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.team_report import IST
from analytics.zip_calls import attach
from integrations.transcripts.client import match_to_calls, shift_counts

OVERLAP_SECS = 30          # P68
REPEAT_LONG = 3            # P69: real conversations with one lead in a day
JUST_OVER = (120, 150)     # P70
JUST_OVER_FACTOR = 2       # P70: caller share vs the day's account-wide share
MIN_LONG_CALLS = 10        # P71: callers ranked only with this many long calls
EMPTY_WORDS = 30           # P72
THIN_WPM = 60              # P72: about 40% of the 5 Oct median of 148 words a minute
LOOP_SHARE = 0.15          # P72: the 5 Oct maximum was 4%
MATCH_MINUTES = 10         # P73: transcript-to-call match window
MACHINE = re.compile(
    r"number you (have )?(dialled|dialed|called|are (trying|calling))|not reachable|switched off|out of (coverage|service)"
    r"|leave (a|your) message|voice ?mail|after the (tone|beep)|please (stay on the line|hold)|your call is (important|in queue)"
    r"|(agents|executives) are busy|abhi vyast|pahunch se bahar|kripya pratiksha|व्यस्त|पहुंच से बाहर|स्विच ऑफ|प्रतीक्षा", re.I)
LABEL = {"overlap": "overlaps another call", "repeat": "3+ long calls to one lead", "just_over": "caller bunched just over 2 min",
         "no_content": "no transcript content", "thin": "little talk for the time", "machine": "recorded message or IVR",
         "loop": "one phrase repeating"}


def eligible(calls: list[dict]) -> list[dict]:
    """Answered calls of 2+ minutes by LeadSquared users (calls from analytics.team_performance.map_calls)."""
    return sorted((c for c in calls if c["real"] and c["team"] != "Not a user"), key=lambda c: c["t"])


def log_flags(calls: list[dict]) -> tuple[dict[str, list[str]], dict]:
    """activity_id -> call-log flags, and the day's just-over-2-minutes figures."""
    flags = defaultdict(list)
    long_calls = eligible(calls)
    answered = defaultdict(list)
    for c in calls:
        if c["ans"] and c["team"] != "Not a user" and (c.get("duration") or 0) > 0:
            answered[c["name"]].append(c)
    for cs in answered.values():
        cs.sort(key=lambda c: c["t"])
        prev = None
        for c in cs:
            if prev and (prev["t"] + timedelta(seconds=prev["duration"]) - c["t"]).total_seconds() > OVERLAP_SECS:
                for x in (prev, c):
                    if x["real"] and "overlap" not in flags[x["activity_id"]]:
                        flags[x["activity_id"]].append("overlap")
            if not prev or c["t"] + timedelta(seconds=c["duration"]) > prev["t"] + timedelta(seconds=prev["duration"]):
                prev = c
    pairs = Counter((c["name"], c["lead_id"]) for c in long_calls)
    for c in long_calls:
        if pairs[(c["name"], c["lead_id"])] >= REPEAT_LONG:
            flags[c["activity_id"]].append("repeat")
    near = lambda c: JUST_OVER[0] <= c["duration"] < JUST_OVER[1]  # noqa: E731
    share = sum(map(near, long_calls)) / len(long_calls) if long_calls else 0
    per = defaultdict(list)
    for c in long_calls:
        per[c["name"]].append(c)
    bunched = {n for n, cs in per.items()
               if len(cs) >= MIN_LONG_CALLS and sum(map(near, cs)) / len(cs) >= JUST_OVER_FACTOR * share}
    for c in long_calls:
        if c["name"] in bunched and near(c):
            flags[c["activity_id"]].append("just_over")
    return dict(flags), {"account_share_pct": round(100 * share, 1), "bunched_callers": sorted(bunched)}


def transcript_flags(text: str | None, duration: int) -> tuple[list[str], dict]:
    words = re.findall(r"\w+", text or "")
    n, minutes = len(words), max(duration, 1) / 60
    ev = {"words": n, "wpm": round(n / minutes), "loop_share": None, "machine_text": ""}
    if n < EMPTY_WORDS:
        return ["no_content"], ev
    flags = []
    if ev["wpm"] < THIN_WPM:
        flags.append("thin")
    if m := MACHINE.search(" ".join(words[:40])):
        flags.append("machine")
        ev["machine_text"] = m.group(0)
    low = [w.lower() for w in words]
    top = Counter(zip(low, low[1:], low[2:])).most_common(1)
    ev["loop_share"] = round(3 * top[0][1] / n, 3) if top else 0
    if ev["loop_share"] >= LOOP_SHARE:
        flags.append("loop")
    return flags, ev


def pick_sample(long_calls: list[dict], flags: dict, limit: int) -> list[dict]:
    """Up to ``limit`` calls with distinct lead numbers: a third already flagged by the call log, a third the
    longest calls (one per caller in turn), the rest just over 2 minutes (most-bunched callers first)."""
    out, numbers = [], set()

    def take(cs, k):
        for c in cs:
            if len(out) >= limit or k <= 0:
                return
            if c.get("lead_number") and c["lead_number"] not in numbers:
                numbers.add(c["lead_number"])
                out.append(c)
                k -= 1

    third = max(limit // 3, 1)
    take(sorted((c for c in long_calls if flags.get(c["activity_id"])), key=lambda c: -c["duration"]), third)
    by_caller = defaultdict(list)
    for c in sorted(long_calls, key=lambda c: -c["duration"]):
        by_caller[c["name"]].append(c)
    longest_each = sorted((cs[0] for cs in by_caller.values()), key=lambda c: -c["duration"])
    take(longest_each, third)
    take(sorted((c for c in long_calls if JUST_OVER[0] <= c["duration"] < JUST_OVER[1]),
                key=lambda c: ("just_over" not in flags.get(c["activity_id"], []), c["duration"])), limit)
    return out


def match(sample: list[dict], api_calls: list, normalize, shifts: Counter | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """activity_id -> transcript text of the API call on the same number that started closest to it (within P73,
    allowing the ±5 h 30 m shift some API times carry when durations agree), and activity_id -> why a sampled call
    has no text. A call the API never transcribed (no transcript file) is "not transcribed", never "no content".
    ``shifts`` counts the matches by the shift that was needed (0, 330 or -330 minutes)."""
    hits = match_to_calls(api_calls, sample, normalize, MATCH_MINUTES)
    numbers = {x.phone for x in api_calls if x.start_time}
    texts, why = {}, {}
    for c in sample:
        hit = hits.get(c["activity_id"])
        if not hit:
            why[c["activity_id"]] = "no API call within 10 min" if normalize(c["lead_number"]) in numbers else "number not in the transcript API"
            continue
        x, shift = hit
        if shifts is not None:
            shifts[shift] += 1
        if not x.transcript.strip() and not getattr(x, "transcript_url", None):
            why[c["activity_id"]] = "not transcribed"
        else:
            texts[c["activity_id"]] = x.transcript
    return texts, why


def fetch(sample: list[dict]) -> tuple[dict[str, str], dict[str, str], int, int, dict[str, int]]:
    """Search the sample's numbers within the API limits: (texts, reasons for no text, requests, failed chunks,
    matches by time shift)."""
    from integrations.transcripts import TranscriptClient
    from integrations.transcripts.client import normalize_phone

    nums = list(dict.fromkeys(normalize_phone(c["lead_number"]) for c in sample if normalize_phone(c["lead_number"])))[:90]
    tc = TranscriptClient(max_retries=0, timeout=120)
    found, failed = [], 0
    time.sleep(60)  # 10 requests a minute across the key, counting earlier runs
    for i in range(0, len(nums), 10):
        if i:
            time.sleep(7)
        try:
            found += tc.search(nums[i:i + 10])
        except Exception as e:  # noqa: BLE001 - one failed chunk must not lose the rest
            failed += 1
            print(f"transcript chunk {i // 10 + 1} failed: {str(e)[:120]}", file=sys.stderr)
    shifts = Counter()
    texts, why = match(sample, found, normalize_phone, shifts)
    return texts, why, tc.requests_made, failed, shift_counts(shifts)


def rollup(long_calls: list[dict], flags: dict, checked: set[str]) -> list[dict]:
    per = defaultdict(lambda: {"long_calls": 0, "flagged": 0, "checked": 0, "checked_flagged": 0, "team": ""})
    for c in long_calls:
        p = per[c["name"]]
        p["team"], fl = c["team"], flags.get(c["activity_id"], [])
        p["long_calls"] += 1
        p["flagged"] += bool(fl)
        if c["activity_id"] in checked:
            p["checked"] += 1
            p["checked_flagged"] += bool(set(fl) & {"no_content", "thin", "machine", "loop"})
    rows = [{"caller": n, **p, "flagged_pct": round(100 * p["flagged"] / p["long_calls"], 1),
             "ranked": p["long_calls"] >= MIN_LONG_CALLS} for n, p in per.items()]
    return sorted(rows, key=lambda r: (not r["ranked"], -r["flagged_pct"], -r["flagged"], r["caller"]))


def analyse(calls: list[dict], texts: dict[str, str] | None = None, sample: list[dict] | None = None,
            missing: dict[str, str] | None = None) -> dict:
    """Integrity summary for a day's mapped calls; ``texts`` maps activity_id -> transcript for the sample."""
    long_calls = eligible(calls)
    flags, bunch = log_flags(calls)
    flags = {k: list(v) for k, v in flags.items()}
    evidence = {}
    for c in long_calls:
        if texts is not None and c["activity_id"] in texts:
            tf, ev = transcript_flags(texts[c["activity_id"]], c["duration"])
            flags.setdefault(c["activity_id"], []).extend(tf)
            evidence[c["activity_id"]] = ev
    rows = rollup(long_calls, flags, set(evidence))
    calls_out = [{"call_id": c["activity_id"], "caller": c["name"], "team": c["team"],
                  "start_ist": c["t"].astimezone(IST).strftime("%H:%M"), "duration_s": c["duration"],
                  "transcript_checked": c["activity_id"] in evidence, **evidence.get(c["activity_id"], {}),
                  "transcript_note": (missing or {}).get(c["activity_id"], ""),
                  "zip_intent": (c.get("zip") or {}).get("intent", ""),
                  "flags": "; ".join(LABEL[f] for f in flags.get(c["activity_id"], []))}
                 for c in long_calls if flags.get(c["activity_id"]) or c["activity_id"] in evidence
                 or c["activity_id"] in (missing or {})]
    count = Counter(f for fl in flags.values() for f in fl)
    return {
        "long_calls": len(long_calls), "flagged_calls": sum(1 for c in long_calls if flags.get(c["activity_id"])),
        "by_flag": {f: count.get(f, 0) for f in LABEL},
        "sampled": len(sample or []), "transcripts_matched": len(evidence),
        "unmatched": dict(Counter((missing or {}).values())),
        "just_over": bunch, "callers": rows, "calls": calls_out,
        "thresholds": {"overlap_s": OVERLAP_SECS, "repeat_long": REPEAT_LONG, "just_over_s": list(JUST_OVER),
                       "just_over_factor": JUST_OVER_FACTOR, "min_long_calls": MIN_LONG_CALLS, "empty_words": EMPTY_WORDS,
                       "thin_wpm": THIN_WPM, "loop_share": LOOP_SHARE, "match_minutes": MATCH_MINUTES},
    }


def write(I: dict, data: str) -> None:
    json.dump({k: v for k, v in I.items() if k != "calls"}, open(os.path.join(data, "integrity.json"), "w"), indent=1)
    if I["calls"]:
        keys = ["call_id", "caller", "team", "start_ist", "duration_s", "transcript_checked", "words", "wpm",
                "loop_share", "machine_text", "flags", "transcript_note", "zip_intent"]
        with open(os.path.join(data, "integrity_calls.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(I["calls"])


def main():
    from analytics.team_performance import load_run, map_calls  # imports this module

    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--data", help="folder written by scripts/fetch_report_day.py (default data/report_{DATE})")
    ap.add_argument("--limit", type=int, default=90, help="calls to check against transcripts (max 90, one per lead number)")
    ap.add_argument("--no-transcripts", action="store_true", help="call-log signals only")
    a = ap.parse_args()
    data = a.data or f"data/report_{a.date}"
    run = load_run(data)
    d0 = datetime.fromisoformat(run["meta"]["d0"])
    calls, *_ = map_calls(run["calls"], run["users"], d0)
    attach(run["zips"], calls, d0 + timedelta(days=1))  # each call's Zipteams analysis, for the per-call file
    long_calls = eligible(calls)
    flags, _ = log_flags(calls)
    sample = [] if a.no_transcripts else pick_sample(long_calls, flags, min(a.limit, 90))
    texts, missing, requests, failed, shifts = fetch(sample) if sample else ({}, {}, 0, 0, shift_counts(Counter()))
    I = analyse(calls, texts if sample else None, sample, missing)
    I.update({"date": a.date, "requests": requests, "failed_chunks": failed, "time_shifts": shifts})
    write(I, data)
    print(f"{I['long_calls']:,} answered calls of 2+ min; {I['flagged_calls']} flagged for review "
          f"({', '.join(f'{LABEL[k]} {v}' for k, v in I['by_flag'].items() if v)})")
    print(f"transcripts: {I['sampled']} calls sampled, {I['transcripts_matched']} matched, {requests} requests, {failed} failed"
          + "".join(f"; {v} {k}" for k, v in I["unmatched"].items()))
    print("transcript times: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in shifts.items()))
    for c in I["calls"]:
        if c["transcript_checked"] or c["transcript_note"]:
            print(f"  {c['call_id']}  {c['caller'][:22]:22} {c['duration_s']:5}s {c.get('words', '-'):>5} words "
                  f"{c.get('wpm', '-'):>4} wpm  {c['flags'] or 'no flag'}{'  (' + c['transcript_note'] + ')' if c['transcript_note'] else ''}")
    print(f"saved {os.path.join(data, 'integrity.json')} and integrity_calls.csv (call IDs only, no lead details)")


if __name__ == "__main__":
    main()
