"""Which leads to audit for a day, and which of their calls are scored.

A lead counts when its LeadSquared stage changed to "Follow Up For Closure" during IST day D. Its window ends at the
last such move on D (a call in progress at that moment counts; later calls are never read) and starts at the latest of:

- the last real move into Discovery Call Done before the window end. A move counts as real when an answered call of
  30+ s started in the 2 hours before it; a stage set with no conversation (for example the bulk update of 13 Oct 2026)
  does not count;
- the lead's previous move into Follow Up For Closure (before the first move on D), so a lead that left the stage and
  came back is judged on the calls since it last came in;
- 30 days before the window end (never further back).

Scored calls: every answered call of 30+ s in the window, read together. The lead is scored only if one of them is a
real conversation (answered, 120+ s, the shared definition); otherwise it is listed as "moved without a conversation".
Background (read, never scored): the calls on the day of that Discovery Call Done move, up to the move.

LeadSquared has no company-wide stage-change list, so the day's leads are found from the stage history of leads now
in Follow Up For Closure and edited since D (all of them), plus leads in a later stage that had a call on D or D-1.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

from analytics.discovery_audit_module import find as dfind
from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient
from integrations.timeutil import IST, utc

STAGE = "Follow Up For Closure"
DCD = "Discovery Call Done"
LATER_STAGES = ("May buy later", "Opportunity Created", "Course Enrolled", "Booking fees received", "Booking Fees Received")
LEAD_COLUMNS = dfind.LEAD_COLUMNS
MAX_DAYS = 30            # the window never reaches further back than this before the move
READ_SECS = 30           # answered calls this long or longer are read
REAL_SECS = 120          # a lead is scored only with one answered call this long (real conversation, CLAUDE.md)
DCD_CALL_HOURS = 2       # a Discovery Call Done move needs an answered 30+ s call this close before it
FMT = "%Y-%m-%d %H:%M:%S"

day_window, prev_day, ist_hm = dfind.day_window, dfind.prev_day, dfind.ist_hm


def answered(c: dict, secs: int = READ_SECS) -> bool:
    return c.get("status") == "Answered" and (c.get("duration") or 0) >= secs


def real(c: dict) -> bool:
    return answered(c, REAL_SECS)


def moves_on(history: list[dict], day: str, stage: str = STAGE) -> list[datetime]:
    d0, d1 = day_window(day)
    return [t for t in dfind.stage_changes_to(history, stage) if d0 <= t < d1]


def _start(c: dict) -> datetime | None:
    return utc(c.get("start_utc"))


def real_dcd_move(history: list[dict], calls: list[dict], before: datetime) -> tuple[datetime | None, datetime | None]:
    """(last real Discovery Call Done move before ``before``, last such move at all, real or not)."""
    moves = [t for t in dfind.stage_changes_to(history, DCD) if t < before]
    for t in reversed(moves):
        if any(answered(c) and (s := _start(c)) and t - timedelta(hours=DCD_CALL_HOURS) <= s <= t for c in calls):
            return t, moves[-1]
    return None, (moves[-1] if moves else None)


def once(calls: list[dict]) -> list[dict]:
    """Each call once: LeadSquared sometimes stores one call as two activities (same start, caller, length, status)."""
    seen: dict[tuple, dict] = {}
    for c in calls:
        k = (c.get("start_utc"), c.get("user_id") or c.get("caller"), c.get("duration"), c.get("status"))
        if k not in seen or (c.get("recording_url") and not seen[k].get("recording_url")):
            seen[k] = c
    return list(seen.values())


def _brief(c: dict | None) -> dict | None:
    return c and {k: c.get(k) for k in ("start_utc", "caller", "duration", "status")}


def pick(lead: dict, history: list[dict], calls: list[dict], day: str) -> dict | None:
    """The audit entry for a lead moved to Follow Up For Closure on ``day``, or None if it wasn't.

    ``calls`` are the lead's own calls (any dates)."""
    moves = moves_on(history, day)
    if not moves:
        return None
    end, first = moves[-1], moves[0]
    prev_fu = next((t for t in reversed(dfind.stage_changes_to(history, STAGE)) if t < first), None)
    dcd, any_dcd = real_dcd_move(history, calls, end)
    floor = end - timedelta(days=MAX_DAYS)
    options = [(dcd, "discovery"), (prev_fu, "previous_follow_up")]
    start, reason = max(((t, r) for t, r in options if t), default=(floor, "30_days"))
    if start < floor:
        start, reason = floor, "30_days"
    mine = sorted((c for c in once(calls) if _start(c)), key=lambda c: c["start_utc"])
    inside = [c for c in mine if start <= _start(c) < end]
    scored = [c for c in inside if answered(c)]
    background = []
    if dcd:
        d0 = datetime.strptime(dcd.astimezone(IST).strftime("%Y-%m-%d"), "%Y-%m-%d").replace(tzinfo=IST)
        background = [c for c in mine if d0 <= _start(c) < dcd and answered(c) and c not in scored]
    note = []
    if any_dcd and any_dcd != dcd:
        note.append(f"Discovery Call Done on {any_dcd.astimezone(IST).strftime('%d-%m-%Y %H:%M')} IST had no answered call in the "
                    f"{DCD_CALL_HOURS} hours before it, so it was not used as the start")
    if len(moves) > 1:
        note.append(f"Moved to {STAGE} {len(moves)} times that day; the last move ends the window")
    entry = {"lead": lead, "calls_day": day, "moved_utc": end.strftime(FMT), "window_start_utc": start.strftime(FMT),
             "start_reason": reason, "dcd_utc": dcd.strftime(FMT) if dcd else None,
             "prev_follow_up_utc": prev_fu.strftime(FMT) if prev_fu else None,
             "calls": scored, "background": background,
             "not_read": {"unanswered": sum(c.get("status") != "Answered" for c in inside),
                          "short": sum(c.get("status") == "Answered" and not answered(c) for c in inside)},
             "note": ". ".join(note)}
    if not any(real(c) for c in scored):
        before = [c for c in mine if _start(c) < end and real(c)]
        after = [c for c in mine if _start(c) >= end and real(c)]
        hint = next((c.get("caller") for c in (scored[::-1] + before[-1:] + after[:1]) if c.get("caller")), None)
        entry.update(moved_without_call=True, last_call_before=_brief(before[-1] if before else None),
                     first_call_after=_brief(after[0] if after else None), caller_hint=hint)
    return entry


# ------------------------------------------------------------------ LeadSquared reads (read-only)


def fetch_candidates(c: LeadSquaredClient, day: str, called: set[str], path: str) -> dict[str, dict]:
    """Every lead now in Follow Up For Closure edited since ``day``, plus leads in a later stage edited since ``day``
    that had a call on the day or the day before (a lead moved on and out the same evening)."""
    if os.path.exists(path):
        return json.load(open(path))
    d0, _ = day_window(day)
    out: dict[str, dict] = {}
    for stage in (STAGE, *LATER_STAGES):
        for l in c.iter_leads("ProspectStage", stage, columns=LEAD_COLUMNS, sort_by="ModifiedOn", page_size=1000):
            m = utc(l.get("ModifiedOn"))
            if m and m < d0:
                break
            if stage == STAGE or l["ProspectID"] in called:
                out[l["ProspectID"]] = l
    json.dump(out, open(path, "w"))
    return out


def lead_calls(c: LeadSquaredClient, lead_id: str, pages: int = 5, row_count: int = 100) -> list[dict]:
    """The lead's calls, both directions (newest pages first; at most ``pages`` x ``row_count`` each way)."""
    out = []
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        for p in range(pages):
            r = c.get_lead_activities(lead_id, activity_event=ev, offset=p * row_count, row_count=row_count) or {}
            got = r.get("ProspectActivities") or []
            out += [{**dfind.flatten_lead_call(a, ev), "lead_id": lead_id} for a in got]
            if len(got) < row_count:
                break
    return out


def fetch_lead_calls(c: LeadSquaredClient, lead_ids: list[str], path: str) -> dict[str, list[dict]]:
    """Calls per lead, saved as it goes so a stopped run resumes."""
    got = json.load(open(path)) if os.path.exists(path) else {}
    todo = [x for x in lead_ids if x not in got]
    for n, lid in enumerate(todo, 1):
        got[lid] = lead_calls(c, lid)
        if n % 25 == 0:
            json.dump(got, open(path, "w"))
            print(f"lead calls: {n} of {len(todo)}", file=sys.stderr, flush=True)
    json.dump(got, open(path, "w"))
    return got


def find(day: str, out_dir: str, client: LeadSquaredClient | None = None) -> list[dict]:
    """The day's audit entries (also written to out_dir/leads.json)."""
    c = client or LeadSquaredClient()
    os.makedirs(out_dir, exist_ok=True)
    calls = dfind.fetch_calls(c, day, f"{out_dir}/calls_{day}.json") + dfind.fetch_calls(c, prev_day(day), f"{out_dir}/calls_{prev_day(day)}.json")
    called = {x["lead_id"] for x in calls if x.get("lead_id")}
    cands = fetch_candidates(c, day, called, f"{out_dir}/candidates.json")
    ids = sorted(cands)
    print(f"{len(cands)} leads in {STAGE} (or later and called {prev_day(day)}–{day}), edited since {day}: reading their "
          "stage history", file=sys.stderr, flush=True)
    hist = dfind.fetch_histories(c, ids, f"{out_dir}/histories.json")
    moved = [lid for lid in ids if moves_on(hist.get(lid) or [], day)]
    print(f"{len(moved)} moved to {STAGE} on {day}: reading their calls", file=sys.stderr, flush=True)
    per_lead = fetch_lead_calls(c, moved, f"{out_dir}/lead_calls.json")
    entries = [e for lid in moved if (e := pick(cands[lid], hist[lid], per_lead.get(lid) or [], day))]
    json.dump(entries, open(f"{out_dir}/leads.json", "w"), indent=1)
    return entries


def find_numbers(numbers: list[str], out_dir: str, client: LeadSquaredClient | None = None) -> list[dict]:
    """On demand: for each pasted number, the lead's latest move to Follow Up For Closure (any day) and its window."""
    c = client or LeadSquaredClient()
    os.makedirs(out_dir, exist_ok=True)
    entries, missing = [], []
    for ph in numbers:
        leads = c.get_lead_by_phone(ph) or []
        lead = next((x for x in leads if (x.get("ProspectStage") or "").replace("\xa0", " ") == STAGE), leads[0] if leads else None)
        if not lead:
            missing.append({"number": ph, "why": "no lead with this number in LeadSquared"})
            continue
        h = (c.get_lead_activities(lead["ProspectID"], activity_event=dfind.STAGE_CHANGE, row_count=50) or {}).get("ProspectActivities") or []
        moves = dfind.stage_changes_to(h, STAGE)
        if not moves:
            missing.append({"number": ph, "why": f"never moved to {STAGE}"})
            continue
        day = moves[-1].astimezone(IST).strftime("%Y-%m-%d")
        e = pick(lead, h, lead_calls(c, lead["ProspectID"]), day)
        entries.append(e)
    json.dump(entries, open(f"{out_dir}/leads.json", "w"), indent=1)
    json.dump(missing, open(f"{out_dir}/numbers_not_found.json", "w"), indent=1)
    return entries
