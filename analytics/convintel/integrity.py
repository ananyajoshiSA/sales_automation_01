"""Calls that may not be real conversations: per-call flags in three tiers, rolled up per caller.

The user (9 Oct 2026): "Flag the transcripts with respective calls and callers which might be fake/not-real
conversations for example < 3 mins", and flag empty transcripts with the caller and the number dialled. Every
flag means the call needs a listen, never that it was faked.

* ``short``: answered but under 3 minutes (every SHORT_CALL), and an empty transcript on a short call.
* ``suspect``: a call that counts, or may count, as a real call (REAL_CALL, or UNKNOWN: answered with no usable
  length) where the evidence says it may not be one: a recording with an empty transcript; the keyword layer's
  transcript checks (no content, thin, machine, loop); the model's reading (doubtful or not a real conversation,
  one-sided, not sales talk, machine, no content, loop); and, for REAL_CALLs, a recording much shorter than the
  logged time, an overlap with the same caller's other answered call, or no recording after every recheck.
* ``pattern``: real calls that look fine one by one but form a pattern: 3+ real calls by one caller to one lead
  in an IST day, or a caller whose real calls bunch just over 3 minutes compared with the whole account.

Overlap, repeat and bunching are judged only for people: shared logins and automation accounts are used by
several people or by the system. Thresholds are the daily report's (analytics/call_integrity.py), with its
2-minute line moved to this module's 3 minutes. ``integrity_summary`` goes to the dashboard and holds no phone
numbers; ``flag_rows`` holds the caller's line and the number dialled, so it is written only to files under data/.
"""

from __future__ import annotations

import csv
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.call_integrity import (EMPTY_WORDS, JUST_OVER_FACTOR, LOOP_SHARE, MIN_LONG_CALLS, OVERLAP_SECS,
                                      REPEAT_LONG, THIN_WPM)
from analytics.convintel import schema as S
from analytics.convintel.attribution import PERSON
from analytics.convintel.classify import REAL_CALL_SECS
from analytics.convintel.store import RECHECK_AFTER, parse_ts
from integrations.timeutil import IST, ist_day

SHORT, SUSPECT, PATTERN = "short", "suspect", "pattern"
TIER_RANK = {SUSPECT: 3, PATTERN: 2, SHORT: 1}
FLAG_ORDER = {f: i for i, f in enumerate(S.INTEGRITY_FLAGS)}
JUST_OVER = (REAL_CALL_SECS, REAL_CALL_SECS + 30)   # 180-209 s: the daily report's 30-second band, at 3 minutes
# LeadSquared's Duration matched the recording's length exactly on 10 of 10 live calls (9 Oct), so a recording
# under 75% of the logged time and a minute or more shorter is worth a listen (or was matched to the wrong call).
MISMATCH_SHARE = 0.75
MISMATCH_SECS = 60
KW_FLAGS = ("no_content", "thin", "machine", "loop")
SEM_FLAGS = ("one_sided", "not_sales_talk", "machine", "no_content", "loop")
REAL_LABEL = {"yes": "the model judged it a real conversation", "doubtful": "the model doubts it was a real conversation",
              "no": "the model judged it not a real conversation"}
CALL_IDS_CAP = 50
REVIEW = "needs review, not proof"
# The model's integrity reason reaches the dashboard; a number it quoted from the call must not.
DIGITS = re.compile(r"\+?\d(?:[\s-]?\d){6,}")
# Classes whose logged duration is usable: an UNKNOWN "call" may be a line left open for hours.
TIMED = (S.REAL_CALL, S.SHORT_CALL)
ROW_COLUMNS = ("callId", "leadId", "direction", "callerId", "caller", "team", "callerNumber", "leadNumber", "startIst",
               "durationS", "class", "transcriptState", "transcriptWords", "tiers", "flags", "reasons")


def call_time(c: dict) -> datetime | None:
    return c.get("t") or parse_ts(c.get("start_utc"))


def start_ist(c: dict) -> str | None:
    t = call_time(c)
    return t.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t else None


def caller_key(c: dict) -> str:
    return c.get("caller_id") or c.get("caller_name") or "(unknown)"


def is_person(c: dict) -> bool:
    return (c.get("caller_kind") or PERSON) == PERSON


def _day(c: dict) -> str | None:
    return c.get("ist_day") or ist_day(call_time(c))


def _span(d: timedelta) -> str:
    return f"{d.days} days" if d.days >= 7 else f"{int(d.total_seconds() // 3600)} h"


def _int(x) -> int | None:
    try:
        return None if x is None or x == "" else int(float(x))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ per-call flags

def _kw_reason(flag: str, ev: dict, dur: int | None) -> str:
    words, wpm, loop = ev.get("words"), ev.get("wpm"), ev.get("loop_share")
    length = f"a {dur} s call" if dur else "a call of unknown length"
    if flag == "no_content":
        return f"keyword check: only {words} words in the transcript of {length} (under {EMPTY_WORDS})" \
            if words is not None else "keyword check: almost no words in the transcript"
    if flag == "thin":
        return f"keyword check: {wpm} words a minute over {length} (under {THIN_WPM})" \
            if wpm is not None else "keyword check: very little talk for the time"
    if flag == "machine":
        return "keyword check: the opening words sound like a recorded message, IVR or voicemail"
    return f"keyword check: one phrase makes up {round(100 * loop)}% of the words ({round(100 * LOOP_SHARE)}%+ counts)" \
        if isinstance(loop, (int, float)) else "keyword check: one phrase keeps repeating"


def _empty_reason(c: dict) -> str:
    """A recording with no text. Many recordings have none when first searched (about 1 in 3 on 9 Oct 2026),
    so say whether the automatic rechecks (store.RECHECK_AFTER) are still running."""
    api = _int(c.get("transcript_api_duration"))
    rec = f"a {api} s recording" if api and api > 0 else "a recording"
    nxt = parse_ts(c.get("next_lookup_utc"))
    return f"{rec} matched this call but its transcript is empty" + (
        f"; next automatic check {nxt.astimezone(IST).strftime('%Y-%m-%d %H:%M')} IST, so the text may still arrive"
        if nxt else "; still empty after every automatic recheck")


def _evidence(c: dict, add) -> None:
    """Transcript evidence on a call that counts, or may count, as real."""
    dur = _int(c.get("duration_s"))
    real = c.get("call_class") == S.REAL_CALL
    if c.get("transcript_state") == S.T_NOT_TRANSCRIBED:
        add(c, "empty_transcript", SUSPECT, _empty_reason(c))
    kw = (c.get("kw") or {}).get("integrity") or {}
    sem = (c.get("sem") or {}).get("integrity") or {}
    verdict = sem.get("real_conversation")
    for f in dict.fromkeys(kw.get("flags") or []):
        if f in KW_FLAGS:
            add(c, f, SUSPECT, _kw_reason(f, kw, dur) + ("; " + REAL_LABEL["yes"] + ", so check before acting"
                                                         if verdict == "yes" else ""))
    sem_flags = [f for f in dict.fromkeys(sem.get("flags") or []) if f in SEM_FLAGS]
    why = DIGITS.sub("[number removed]", (sem.get("reason") or "").strip()[:200])
    tail = (f"; {REAL_LABEL[verdict]}" if verdict in REAL_LABEL else "") + (f" ({why})" if why else "")
    for f in sem_flags:
        add(c, f, SUSPECT, f"model reading: {S.INTEGRITY_FLAGS[f]}{tail}")
    if verdict in ("doubtful", "no") and not sem_flags:   # no specific sign named: filed under not_sales_talk
        add(c, "not_sales_talk", SUSPECT, f"model reading: {REAL_LABEL[verdict]}, without naming a specific sign"
            + (f" ({why})" if why else ""))
    if not real:
        return
    api = _int(c.get("transcript_api_duration"))
    if api and api > 0 and dur and api < MISMATCH_SHARE * dur and dur - api >= MISMATCH_SECS:
        add(c, "duration_mismatch", SUSPECT, f"the recording runs {api} s but LeadSquared logged {dur} s "
                                              "(or the recording was matched to the wrong call)")
    if c.get("transcript_state") == S.T_NOT_FOUND and not c.get("next_lookup_utc"):
        n = _int(c.get("lookup_attempts"))
        add(c, "no_recording_long_call", SUSPECT, f"a {dur} s call, but no recording was found "
                                                  f"{f'after {n} searches and ' if n else 'after '}every automatic recheck")


def _overlaps(calls: list[dict], add) -> None:
    """Each real call that overlaps the same person's other answered call by more than OVERLAP_SECS (the largest)."""
    per = defaultdict(list)
    for c in calls:
        t, d = call_time(c), _int(c.get("duration_s"))
        if c.get("answered") and c.get("call_class") in TIMED and is_person(c) and t and d and d > 0:
            per[caller_key(c)].append((t, t + timedelta(seconds=d), c))
    worst: dict[str, tuple[int, dict, dict]] = {}
    for cs in per.values():
        cs.sort(key=lambda x: (x[0], x[2]["call_id"]))
        prev = None
        for s, e, c in cs:
            if prev and (secs := round((min(prev[1], e) - s).total_seconds())) > OVERLAP_SECS:
                for x, other in ((prev[2], c), (c, prev[2])):
                    if x.get("call_class") == S.REAL_CALL and secs > worst.get(x["call_id"], (0,))[0]:
                        worst[x["call_id"]] = (secs, other, x)
            if not prev or e > prev[1]:
                prev = (s, e, c)
    for secs, other, c in worst.values():
        add(c, "overlap", SUSPECT, f"overlaps this caller's answered call of {start_ist(other)} IST "
                                   f"(call {other['call_id']}) by {secs} s")


def _repeats(real: list[dict], add) -> None:
    groups = defaultdict(list)
    for c in real:
        if c.get("lead_id"):
            groups[(caller_key(c), c["lead_id"], _day(c))].append(c)
    for (_, _, day), cs in groups.items():
        if len(cs) >= REPEAT_LONG:
            for c in cs:
                add(c, "repeat", PATTERN, f"{len(cs)} real calls by this caller to the same lead on {day} (IST)")


def _bunching(real: list[dict], add) -> None:
    """A caller's real calls in the 180-209 s band at JUST_OVER_FACTOR times the account's share that IST day."""
    near = lambda c: JUST_OVER[0] <= c["duration_s"] < JUST_OVER[1]  # noqa: E731
    day_all, day_near, per = Counter(), Counter(), defaultdict(list)
    for c in real:
        d = _day(c)
        day_all[d] += 1
        day_near[d] += near(c)
        per[(caller_key(c), d)].append(c)
    for (_, d), cs in per.items():
        band = [c for c in cs if near(c)]
        base = day_near[d] / day_all[d]
        if len(cs) >= MIN_LONG_CALLS and band and len(band) / len(cs) >= JUST_OVER_FACTOR * base:
            for c in band:
                add(c, "just_over_3_min", PATTERN,
                    f"{len(band)} of this caller's {len(cs)} real calls on {d} lasted {JUST_OVER[0]}-{JUST_OVER[1] - 1} s "
                    f"({round(100 * len(band) / len(cs))}%), against {round(100 * base)}% across the account that day")


def call_flags(calls: list[dict]) -> dict[str, list[dict]]:
    """call_id -> [{"flag", "tier", "reason"}], most serious first; calls with no flag are left out.

    ``calls`` are registry rows (plus "kw", "sem" and "t" when known). The same flag found twice (the keyword
    check and the model's reading both hear a machine) is one flag with both reasons."""
    found: dict[str, dict[str, dict]] = defaultdict(dict)

    def add(c: dict, flag: str, tier: str, reason: str) -> None:
        cur = found[c["call_id"]].get(flag)
        if cur:
            cur["reasons"].append(reason)
            cur["tier"] = max(cur["tier"], tier, key=TIER_RANK.get)
        else:
            found[c["call_id"]][flag] = {"flag": flag, "tier": tier, "reasons": [reason]}

    for c in calls:
        cls = c.get("call_class")
        if cls == S.SHORT_CALL:
            add(c, "under_3_min", SHORT, f"answered but only {c.get('duration_s')} s, under 3 minutes")
            if c.get("transcript_state") == S.T_NOT_TRANSCRIBED:
                add(c, "empty_transcript", SHORT, _empty_reason(c))
        elif cls in (S.REAL_CALL, S.UNKNOWN) and c.get("answered", True):
            _evidence(c, add)
    real = [c for c in calls if c.get("call_class") == S.REAL_CALL and is_person(c) and isinstance(c.get("duration_s"), int)]
    _overlaps(calls, add)
    _repeats(real, add)
    _bunching(real, add)
    return {cid: [{"flag": f["flag"], "tier": f["tier"], "reason": f"{'; '.join(f['reasons'])} ({REVIEW})"}
                  for f in sorted(fs.values(), key=lambda f: (-TIER_RANK[f["tier"]], FLAG_ORDER.get(f["flag"], 99)))]
            for cid, fs in found.items()}


# ------------------------------------------------------------------ roll-ups

def _tiers(fl: list[dict]) -> list[str]:
    return sorted({f["tier"] for f in fl}, key=lambda t: -TIER_RANK[t])


def severity(c: dict, fl: list[dict]) -> tuple:
    """Sort key, most serious first: highest tier, then most evidence or pattern flags, then longest logged time."""
    return (-max((TIER_RANK[f["tier"]] for f in fl), default=0), -sum(f["tier"] != SHORT for f in fl),
            -(_int(c.get("duration_s")) or 0), c["call_id"])


def integrity_summary(calls: list[dict], flags: dict[str, list[dict]]) -> dict:
    """The snapshot's "integrity" section: counts by flag and tier and one row per caller. No phone numbers.

    "flagged" counts calls with a suspect or pattern flag; short calls are counted on their own. A caller's
    flaggedPct is flagged calls over their answered calls that count or may count as real (realCalls +
    unknownCalls), the only calls that can be flagged. callIds lists every flagged call of the caller, short ones
    included, most serious first (capped at CALL_IDS_CAP; callIdsTotal = flagged + shortCalls). callerId is the
    caller key the team views and coaching use (user id, else name)."""
    by_flag, by_tier = Counter(), Counter()
    flagged = short = real_total = real_checked = 0
    rows: dict[str, dict] = {}
    for c in calls:
        fl = flags.get(c["call_id"]) or []
        tiers = set(_tiers(fl))
        cls = c.get("call_class")
        if cls == S.REAL_CALL:
            real_total += 1
            real_checked += bool(c.get("kw") or c.get("sem"))
        if not (c.get("answered") or fl):
            continue
        by_flag.update(f["flag"] for f in fl)
        by_tier.update(tiers)
        is_flagged = bool(tiers & {SUSPECT, PATTERN})
        flagged += is_flagged
        short += cls == S.SHORT_CALL
        r = rows.get(k := caller_key(c))
        if r is None:
            r = rows[k] = {"callerId": k, "caller": c.get("caller_name") or "(unknown)",
                           "team": c.get("team"), "kind": c.get("caller_kind") or PERSON, "calls": 0, "realCalls": 0,
                           "unknownCalls": 0, "shortCalls": 0, "suspect": 0, "pattern": 0, "flagged": 0,
                           "byFlag": Counter(), "_ids": [], "_t": None}
        t = call_time(c)
        if t and (r["_t"] is None or t > r["_t"]):
            r["_t"], r["team"] = t, c.get("team")
        r["calls"] += bool(c.get("answered"))
        r["realCalls"] += cls == S.REAL_CALL
        r["unknownCalls"] += cls == S.UNKNOWN
        r["shortCalls"] += cls == S.SHORT_CALL
        r["suspect"] += SUSPECT in tiers
        r["pattern"] += PATTERN in tiers
        r["flagged"] += is_flagged
        r["byFlag"].update(f["flag"] for f in fl)
        if fl:
            r["_ids"].append((severity(c, fl), c["call_id"]))
    out = []
    for r in rows.values():
        ids = [cid for _, cid in sorted(r.pop("_ids"))]
        r.pop("_t")
        base = r["realCalls"] + r["unknownCalls"]
        out.append({**r, "shortPct": round(100 * r["shortCalls"] / r["calls"], 1) if r["calls"] else None,
                    "flaggedPct": round(100 * r["flagged"] / base, 1) if base else None,
                    "byFlag": dict(r["byFlag"]), "callIds": ids[:CALL_IDS_CAP], "callIdsTotal": len(ids)})
    out.sort(key=lambda r: (r["kind"] != PERSON, r["flaggedPct"] is None, -(r["flaggedPct"] or 0), -r["flagged"],
                            r["caller"]))
    return {"flaggedCalls": flagged, "shortCalls": short, "byFlag": {f: by_flag.get(f, 0) for f in S.INTEGRITY_FLAGS},
            "byTier": {t: by_tier.get(t, 0) for t in (SUSPECT, PATTERN, SHORT)}, "byCaller": out, "notes": [
                "A flag means the call needs a listen, not that it was faked.",
                "Short calls (answered, under 3 minutes) are counted on their own; 'flagged' counts calls that count "
                "as real (answered, 3+ minutes, or of unknown length) where the transcript, the model's reading or "
                "the call log suggests they may not be real conversations.",
                "A caller's flagged % is flagged calls over their real calls plus answered calls of unknown length. "
                "'Calls to review' lists every flagged call, short calls included, most serious first.",
                "Empty transcript: a recording was found but has no text. The search is repeated "
                f"{', '.join(_span(d) for d in RECHECK_AFTER)} after the call, so this flag can clear on its own; "
                "each reason says whether rechecks are still running.",
                f"Transcript checks need an analysed transcript: {real_checked:,} of {real_total:,} real calls had one.",
                f"Thresholds: overlap over {OVERLAP_SECS} s; {REPEAT_LONG}+ real calls to one lead in a day; "
                f"{JUST_OVER[0]}-{JUST_OVER[1] - 1} s calls at {JUST_OVER_FACTOR}x the account's share, for callers "
                f"with {MIN_LONG_CALLS}+ real calls that day; a recording under {round(100 * MISMATCH_SHARE)}% of the "
                f"logged time and {MISMATCH_SECS}+ s shorter; empty transcript = a recording with no text.",
                "Shared logins and automation accounts are listed by kind and never checked for overlap, repeats or "
                "bunching, because several people or the system use them."]}


def flag_rows(calls: list[dict], flags: dict[str, list[dict]]) -> list[dict]:
    """One row per flagged call, most serious first, for review files under data/ ONLY.

    These rows hold phone numbers: ``callerNumber`` is the company line the caller dialled from (LeadSquared's
    DisplayNumber, shared across callers) and ``leadNumber`` is the "number dialled / lead number" (normalized
    91XXXXXXXXXX: the number called on an outbound call, the lead's own number on an inbound one). Never put them
    in the snapshot, the dashboard, logs or the repo. No transcript text."""
    rows = []
    for c in calls:
        if not (fl := flags.get(c["call_id"])):
            continue
        rows.append((severity(c, fl), {
            "callId": c["call_id"], "leadId": c.get("lead_id"), "direction": c.get("direction"),
            "callerId": c.get("caller_id"), "caller": c.get("caller_name"), "team": c.get("team"),
            "callerNumber": c.get("caller_number"), "leadNumber": c.get("number") or c.get("lead_number"),
            "startIst": start_ist(c),
            "durationS": c.get("duration_s"), "class": c.get("call_class"), "transcriptState": c.get("transcript_state"),
            "transcriptWords": c.get("transcript_words"), "tiers": _tiers(fl), "flags": [f["flag"] for f in fl],
            "reasons": [f["reason"] for f in fl]}))
    return [r for _, r in sorted(rows, key=lambda x: x[0])]


def write_flag_csv(path: str, rows: list[dict]) -> None:
    """Write ``flag_rows`` to a CSV (lists joined with "; "). The rows hold phone numbers: keep the file under data/."""
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ROW_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: "; ".join(map(str, v)) if isinstance(v, list) else v for k, v in r.items()})
