"""Pick the calls to read for a learner-needs report and write them as reading chunks.

One call per lead (the longest), only calls of ``min_dur``+ seconds with a transcript, spread over the
lead's GrowthX funnel (landing page) so small funnels are still heard: every funnel gets at least
``min_per`` calls (or all it has), the rest is shared in proportion to funnel size.
"""

from __future__ import annotations

import random
from collections import defaultdict

SKIP_DISPOSITIONS = {"Out of Business Hour"}


def phone10(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:]


def longest_per_lead(logs: list[dict], min_dur: int = 60) -> list[dict]:
    """One row per phone: the longest answered call with a transcript and at least ``min_dur`` seconds."""
    best: dict[str, dict] = {}
    for r in logs:
        p = phone10(r.get("to"))
        dur = int(r.get("duration") or 0)
        if not p or not r.get("transcript_id") or dur < min_dur:
            continue
        if (r.get("disposition") or "") in SKIP_DISPOSITIONS:
            continue
        if p not in best or dur > int(best[p].get("duration") or 0):
            best[p] = r
    return [{"p": p, "tid": r["transcript_id"], "dur": int(r["duration"]), "disp": r.get("disposition") or "",
             "start": r.get("start_time") or ""} for p, r in best.items()]


def stratified(calls: list[dict], n: int, min_per: int = 8, seed: int = 7) -> list[dict]:
    """``n`` calls spread over ``slug``; deterministic for a given seed."""
    if len(calls) <= n:
        return sorted(calls, key=lambda c: (c.get("slug") or "", c["p"]))
    rng = random.Random(seed)
    by: dict[str, list[dict]] = defaultdict(list)
    for c in sorted(calls, key=lambda c: c["p"]):
        by[c.get("slug") or ""].append(c)
    for v in by.values():
        rng.shuffle(v)
    take = {s: min(len(v), min_per) for s, v in by.items()}
    left = n - sum(take.values())
    if left < 0:  # more funnels than room: biggest funnels keep their floor
        take = {}
        for s in sorted(by, key=lambda s: -len(by[s])):
            k = min(len(by[s]), min_per, n - sum(take.values()))
            if k > 0:
                take[s] = k
    else:
        spare = {s: len(by[s]) - take[s] for s in by}
        pool = sum(spare.values())
        for s in sorted(by, key=lambda s: -spare[s]):
            extra = min(spare[s], round(left * spare[s] / pool)) if pool else 0
            take[s] += extra
        diff = n - sum(take.values())
        for s in sorted(by, key=lambda s: -len(by[s])):
            if diff == 0:
                break
            step = 1 if diff > 0 else -1
            if 0 < take[s] + step <= len(by[s]):
                take[s] += step
                diff -= step
    out = [c for s, k in take.items() for c in by[s][:k]]
    return sorted(out, key=lambda c: (c.get("slug") or "", c["p"]))


def format_call(i: int, call: dict, turns: list[dict] | None) -> str:
    head = (f"===== LEAD {i} | funnel {call.get('slug') or 'n/a'} | {call.get('start', '')} IST | "
            f"{call['dur']} s | disposition: {call['disp']}")
    lines = [head]
    for t in turns or []:
        who = "AGENT" if str(t.get("speaker", "")).upper() in {"BOT", "AGENT", "ASSISTANT"} else "LEAD"
        msg = " ".join(str(t.get("message") or "").split())
        if msg:
            lines.append(f"{who}: {msg}")
    if len(lines) == 1:
        lines.append("(no transcript text)")
    return "\n".join(lines)


def chunks(calls: list[dict], size: int) -> list[list[dict]]:
    """Split into near-equal chunks of at most ``size``."""
    if not calls:
        return []
    k = -(-len(calls) // size)
    base, extra = divmod(len(calls), k)
    out, at = [], 0
    for j in range(k):
        n = base + (1 if j < extra else 0)
        out.append(calls[at:at + n])
        at += n
    return out
