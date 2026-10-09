"""Inventory: every LeadSquared call of a period into the registry, classified, with its caller and team.

Each call keeps the call class (REAL_CALL / SHORT_CALL / NOT_CONNECTED / UNKNOWN, analytics/convintel/classify.py),
its caller and kind (person, shared login, bot, not a user) and the caller's team when it was inventoried.
LeadSquared's raw ``Duration`` is kept, so a missing or unreadable duration stays UNKNOWN instead of 0 seconds.
Per day, the number of calls LeadSquared returned is stored for reconciliation (analytics/convintel/reconcile.py).

    python -m analytics.convintel inventory FROM TO [--window 14:00-14:30]
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from analytics.convintel.attribution import Directory
from analytics.convintel.classify import classify, is_answered, parse_duration
from analytics.convintel.sources import calls as source_calls, days
from analytics.convintel.store import Registry, ts
from integrations.leadsquared import parse_activity_note, parse_phone_call
from integrations.timeutil import IST, ist_day, ist_day_start, utc
from integrations.transcripts.client import normalize_phone

TEAM_SOURCE = "caller's LeadSquared group when inventoried"
BLOCK_DAYS = 7


def record(c: dict, directory: Directory, duration_raw=None, source: str = "leadsquared") -> dict:
    """A registry row from a parsed call (``parse_phone_call``). ``duration_raw`` is the note's own Duration;
    without it the parsed duration is used, where 0 can also mean "missing" (classified UNKNOWN when answered)."""
    t = utc(c.get("start_utc"))
    raw = c.get("duration") if duration_raw is None else duration_raw
    dur = parse_duration(raw)
    status = (c.get("status") or "").strip() or None
    cls, why = classify(status, dur)
    who = directory.person(c.get("user_id"), c.get("caller"))
    return {"call_id": c["activity_id"], "lead_id": c.get("lead_id"), "lead_number": c.get("lead_number"),
            "number": normalize_phone(c.get("lead_number")), "caller_number": c.get("display_number") or None,
            "direction": c.get("direction"), "call_status": status,
            "answered": int(is_answered(status)), "start_utc": ts(t) if t else c.get("start_utc"), "ist_day": ist_day(t),
            "duration_s": dur, "duration_raw": None if raw is None else str(raw), "call_class": cls, "class_reason": why,
            "caller_id": who["id"], "caller_name": who["name"], "caller_kind": who["kind"], "team": who["team"],
            "team_source": TEAM_SOURCE, "source": source}


def from_activity(a: dict, directory: Directory) -> dict:
    note = parse_activity_note(a.get("ActivityEvent_Note"))
    return record(parse_phone_call(a), directory, note.get("Duration"))


def window_bounds(day: str, window: str | None) -> tuple[datetime, datetime]:
    """The IST day, or the 'HH:MM-HH:MM' IST part of it (used to validate on a small sample)."""
    d0 = ist_day_start(day)
    if not window:
        return d0, d0 + timedelta(days=1) - timedelta(seconds=1)
    a, b = window.split("-")
    at = lambda hm: d0.replace(hour=int(hm[:2]), minute=int(hm[3:5]))  # noqa: E731
    return at(a), at(b) - timedelta(seconds=1)


def inventory(reg: Registry, client, day_from: str, day_to: str, now: datetime, window: str | None = None,
              users: list[dict] | None = None, block_days: int = BLOCK_DAYS, log=lambda *a: None) -> Counter:
    """Read every call of each IST day (or of ``window`` on each day) and upsert it, day by day. Whole days are
    read ``block_days`` at a time: each read also covers LeadSquared's 3-day edit margin, so one read per block
    instead of per day saves most of the requests on a backfill. Resumable: each finished block is a
    checkpoint, and a re-run only refreshes what changed."""
    directory = Directory(users if users is not None else client.get_users())
    run = reg.start_run("inventory", {"from": day_from, "to": day_to, "window": window}, now)
    total = Counter()
    all_days = days(day_from, day_to)
    blocks = [[d] for d in all_days] if window else [all_days[i:i + block_days] for i in range(0, len(all_days), block_days)]
    try:
        for block in blocks:
            bounds = {d: window_bounds(d, window) for d in block}
            per_day: dict[str | None, list[dict]] = {d: [] for d in block}
            for a in source_calls(client, bounds[block[0]][0], bounds[block[-1]][1], now=now):
                r = from_activity(a, directory)
                per_day.setdefault(r["ist_day"], []).append(r)
            unreadable = per_day.pop(None, [])
            for i, day in enumerate(block):
                recs = per_day.get(day, []) + (unreadable if i == 0 else [])
                got = Counter(r["direction"] for r in recs)
                got["unreadable_start"] = len(unreadable) if i == 0 else 0
                res = reg.upsert_calls(recs, now)
                reg.refresh([r["call_id"] for r in recs], now)
                d0, d1 = bounds[day]
                reg.set_meta(f"source:{day}", {"calls": len(recs), **got, "read_utc": ts(now), "window": window,
                                               "from_utc": ts(d0), "to_utc": ts(d1),
                                               "from_ist": d0.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                                               "to_ist": d1.astimezone(IST).strftime("%Y-%m-%d %H:%M")})
                total.update(res)
                total["calls"] += len(recs)
                log(f"{day}: {len(recs)} calls read ({dict(res)})")
            reg.checkpoint(run, {"done_through": block[-1], **total})
        reg.finish_run(run, "done", dict(total), now)
    except Exception as e:
        reg.finish_run(run, "failed", dict(total), now, error=str(e)[:500])
        raise
    return total
