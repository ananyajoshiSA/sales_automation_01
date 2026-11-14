"""Which leads to audit for a day, and which of their calls.

LeadSquared has no company-wide list of stage changes (the bulk stage-change read returns no rows) and about
5,000 leads sit in "Discovery Call Done" at any time, so the day's leads are found from the day's calls:

1. every call on day D and on D-1 (bulk read, both directions), keeping leads with an answered call of 30+ s;
2. leads now in Discovery Call Done or a later pipeline stage and edited since D (lead search);
3. the stage history of the leads in both lists, keeping those changed to Discovery Call Done on D (IST).

The calls audited are every call to that lead on D. A lead whose stage was set on D after a call on D-1 only
(the caller set it the next morning) is audited on its D-1 calls, and the row says so.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_phone_call
from integrations.timeutil import IST, ist_day_start, utc

STAGE = "Discovery Call Done"
STAGE_CHANGE = 3002
# A lead moved on the same evening is still found: search these stages as well.
LATER_STAGES = ("Roadmap Done", "Roadmap\xa0Done", "Counselled lead", "Follow Up For Closure", "May buy later",
                "Opportunity Created", "Course Enrolled", "Booking fees received", "Booking Fees Received")
LEAD_COLUMNS = ["ProspectID", "FirstName", "LastName", "Phone", "Mobile", "ModifiedOn", "OwnerIdName",
                "mx_Enquired_Course", "ProspectStage"]
MIN_CONVERSATION_SECS = 30   # an answered call shorter than this holds no discovery conversation


def day_window(day: str) -> tuple[datetime, datetime]:
    d0 = ist_day_start(day)
    return d0, d0 + timedelta(days=1)


def prev_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def _data(a: dict) -> dict:
    d = a.get("Data")
    if isinstance(d, list):
        return {x.get("Key"): x.get("Value") for x in d if isinstance(x, dict)}
    return d if isinstance(d, dict) else {}


def stage_changes_to(history: list[dict], stage: str = STAGE) -> list[datetime]:
    """UTC times the lead moved into ``stage``, oldest first (stage-change activities, any order)."""
    out = []
    for a in history or []:
        d = _data(a)
        cur = (d.get("CurrentStage") or "").replace("\xa0", " ").strip()
        t = utc(a.get("CreatedOn"))
        if t and cur.lower() == stage.lower():
            out.append(t)
    return sorted(out)


def changed_on(history: list[dict], day: str, stage: str = STAGE) -> datetime | None:
    """The first move into ``stage`` during IST ``day``, or None."""
    d0, d1 = day_window(day)
    return next((t for t in stage_changes_to(history, stage) if d0 <= t < d1), None)


def flatten_lead_call(a: dict, event: int) -> dict:
    """A call from the per-lead activity read: its fields sit under ``ActivityFields``."""
    f = {k: v for k, v in (a.get("ActivityFields") or {}).items() if k not in ("CreatedOn", "ModifiedOn")}
    return parse_phone_call({**a, **f, "ActivityEvent": event, "ProspectActivityId": a.get("Id")})


def is_conversation(call: dict) -> bool:
    return (call.get("status") == "Answered") and (call.get("duration") or 0) >= MIN_CONVERSATION_SECS


def calls_for(lead_id: str, calls: list[dict], day: str) -> list[dict]:
    d0, d1 = day_window(day)
    out = [c for c in calls if c.get("lead_id") == lead_id and (t := utc(c.get("start_utc"))) and d0 <= t < d1]
    return sorted(out, key=lambda c: c["start_utc"])


def pick(lead: dict, history: list[dict], calls: list[dict], day: str) -> dict | None:
    """The audit entry for a lead changed to Discovery Call Done on ``day``, or None if it wasn't."""
    t = changed_on(history, day)
    if not t:
        return None
    lid = lead["ProspectID"]
    own = calls_for(lid, calls, day)
    calls_day, note = day, ""
    if not any(is_conversation(c) for c in own):
        before = calls_for(lid, calls, prev_day(day))
        if any(is_conversation(c) for c in before):
            own, calls_day = before, prev_day(day)
            note = f"Stage set on {day} with no conversation that day; audited the calls of {calls_day}."
    return {"lead": lead, "stage_changed_utc": t.strftime("%Y-%m-%d %H:%M:%S"), "calls_day": calls_day,
            "calls": own, "note": note}


# ------------------------------------------------------------------ LeadSquared reads (read-only)


def fetch_calls(c: LeadSquaredClient, day: str, path: str) -> list[dict]:
    """Every call that started on IST ``day``, cached in ``path``."""
    if os.path.exists(path):
        return json.load(open(path))
    d0, d1 = day_window(day)
    rows = []
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        rows.extend(parse_phone_call({**a, "ActivityEvent": ev}) for a in c.iter_activities_started(ev, d0, d1 - timedelta(seconds=1)))
    json.dump(rows, open(path, "w"))
    return rows


def fetch_candidates(c: LeadSquaredClient, day: str, path: str) -> dict[str, dict]:
    """Leads now in Discovery Call Done or a later stage, edited since the start of ``day``."""
    if os.path.exists(path):
        return json.load(open(path))
    d0, _ = day_window(day)
    out: dict[str, dict] = {}
    for stage in (STAGE, *LATER_STAGES):
        for l in c.iter_leads("ProspectStage", stage, columns=LEAD_COLUMNS, sort_by="ModifiedOn", page_size=1000):
            m = utc(l.get("ModifiedOn"))
            if m and m < d0:
                break
            out[l["ProspectID"]] = l
    json.dump(out, open(path, "w"))
    return out


def fetch_histories(c: LeadSquaredClient | None, lead_ids: list[str], path: str, workers: int = 2) -> dict[str, list[dict]]:
    """Stage-change history per lead, two reads at a time (the gate's limit); saved as it goes so a stopped run resumes."""
    from concurrent.futures import ThreadPoolExecutor
    hist = json.load(open(path)) if os.path.exists(path) else {}
    todo = [x for x in lead_ids if x not in hist]
    clients = [LeadSquaredClient() for _ in range(workers)] if c is None or workers > 1 else [c]

    def read(i_lid: tuple[int, str]) -> tuple[str, list[dict]]:
        i, lid = i_lid
        r = clients[i % len(clients)].get_lead_activities(lid, activity_event=STAGE_CHANGE, row_count=50) or {}
        return lid, r.get("ProspectActivities") or []

    with ThreadPoolExecutor(len(clients)) as pool:
        for n, (lid, h) in enumerate(pool.map(read, enumerate(todo)), 1):
            hist[lid] = h
            if n % 50 == 0:
                json.dump(hist, open(path, "w"))
                print(f"stage histories: {n} of {len(todo)}", file=sys.stderr, flush=True)
    json.dump(hist, open(path, "w"))
    return hist


def find(day: str, out_dir: str, client: LeadSquaredClient | None = None) -> list[dict]:
    """The day's audit entries (also written to out_dir/leads.json)."""
    c = client or LeadSquaredClient()
    os.makedirs(out_dir, exist_ok=True)
    calls = fetch_calls(c, day, f"{out_dir}/calls_{day}.json") + fetch_calls(c, prev_day(day), f"{out_dir}/calls_{prev_day(day)}.json")
    cands = fetch_candidates(c, day, f"{out_dir}/candidates.json")
    # Only leads with a conversation on the day or the day before can have had a discovery call to audit.
    called = {x["lead_id"] for x in calls if x.get("lead_id") and is_conversation(x)}
    ids = sorted(called & set(cands))
    print(f"{len(calls)} calls on {prev_day(day)}–{day}; {len(cands)} leads in the stage or later, edited since {day}; "
          f"{len(ids)} in both: reading their stage history", file=sys.stderr, flush=True)
    hist = fetch_histories(c, ids, f"{out_dir}/histories.json")
    entries = [e for lid in ids if (e := pick(cands[lid], hist.get(lid) or [], calls, day))]
    json.dump(entries, open(f"{out_dir}/leads.json", "w"), indent=1)
    print(f"{len(entries)} leads moved to {STAGE} on {day}", file=sys.stderr)
    return entries


def ist_hm(s: str | None) -> str:
    t = utc(s)
    return t.astimezone(IST).strftime("%H:%M") if t else ""
