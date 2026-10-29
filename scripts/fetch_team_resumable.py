"""Fetch a team snapshot like scripts/fetch_team_data.py, but resumable: every read is cached as it lands.

Each lead owner's leads (for the IST day of the run) and each one-day window of the company-wide activity read
are saved under ``--cache`` (default data/fetch_cache/<groups>/) as soon as they are read. A run that stops (a
gate deferral, a LeadSquared 403, a lost container) keeps what it already read; rerunning the same command reads
only the rest. The snapshot has the same keys and rows as fetch_team_data.py's, so analytics.nightly_plan and
scripts/plan_review.py read it unchanged. It holds lead details: write it under data/.

Every request goes through the client's admission gate (docs/leadsquared_load_control.md). On AdmissionDeferred
the run stops with exit code 75 and says when to try again; it never skips the window. A 403 or 429 on a window
is tried again after BLOCK_WAITS; if it is still refused the run stops with exit code 1, keeping the cache.

    python scripts/fetch_team_resumable.py "Corporate law trainees" "Team Bootcamp +Anas" 2026-10-12 2026-10-23 \
        data/snap.json [--cache data/fetch_cache/inayat]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from analytics.zip_calls import ZIP_LATE_WINDOW
from integrations.leadsquared import (
    PHONE_INBOUND, PHONE_OUTBOUND, AdmissionDeferred, LeadSquaredClient, LeadSquaredError, parse_activity_note,
    parse_phone_call,
)
from integrations.timeutil import EDIT_MARGIN, IST, ist_day_start, now_utc, utc
from scripts.fetch_team_data import LEAD_COLUMNS, PAYMENT_SUCCESS, ZIP_EVENTS

DEFERRED_EXIT = 75  # EX_TEMPFAIL: try again later
BLOCK_WAITS = (120, 120)  # seconds before each further try of a window LeadSquared refused (403/429)
WORKERS = int(os.environ.get("FETCH_WORKERS", "4"))
sleep = time.sleep


class Deferred(Exception):
    """The gate deferred a read; the run must stop and be rerun later."""


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:80]


def day_windows(f: datetime, stop: datetime) -> list[tuple[datetime, datetime]]:
    """``[f, stop]`` as one-day edit windows, the last one cut at ``stop``."""
    out, day = [], f
    while day < stop:
        end = min(day + timedelta(days=1), stop)
        out.append((day, end))
        day = end
    return out


def cached(path: str, read):
    """The JSON at ``path``, else ``read()`` saved there first (written whole, so a stop never leaves half a file)."""
    if os.path.exists(path):
        return json.load(open(path))
    rows = read()
    with open(path + ".tmp", "w") as fh:
        json.dump(rows, fh)
    os.replace(path + ".tmp", path)
    return rows


def patient(read, what: str):
    """``read()``, tried again after a pause while LeadSquared refuses it (403/429). A deferral stops the run."""
    waits = (0,) + BLOCK_WAITS
    for i, wait in enumerate(waits):
        if wait:
            log(f"{what}: refused by LeadSquared; trying again in {wait} s")
            sleep(wait)
        try:
            return read()
        except AdmissionDeferred as e:
            raise Deferred(str(e)) from e
        except LeadSquaredError as e:
            if e.status_code not in (403, 429) or i == len(waits) - 1:
                raise
    raise AssertionError("unreachable")


def started(client, cache: str, ev: int, f: datetime, t: datetime, keep, now: datetime) -> list[dict]:
    """Every activity of ``ev`` created in ``[f, t]`` (read by edit day up to ``t`` + EDIT_MARGIN), one cached file a day."""
    stop = min(t + EDIT_MARGIN, now)

    def window(w):
        def read():
            rows = []
            for a in client.clone()._iter_unpaged(ev, *w):
                when = utc(a.get("CreatedOn")) or utc(a.get("ModifiedOn"))
                if when and f <= when <= t and keep(a):
                    rows.append(a)
            return rows
        # a window still open (ending at "now") is never reused: tomorrow's run must see its later edits
        name = f"ev{ev}_{w[0]:%Y%m%d%H%M}_{w[1]:%Y%m%d%H%M}_{f:%Y%m%d}_{t:%Y%m%d}.json"
        rows = patient(read, name) if w[1] >= now else cached(os.path.join(cache, name), lambda: patient(read, name))
        log(f"  event {ev}, edits on {w[0].astimezone(IST):%d %b}: {len(rows)}")
        return rows

    with ThreadPoolExecutor(WORKERS) as pool:
        batches = list(pool.map(window, day_windows(f, stop)))
    seen, out = set(), []
    for rows in batches:
        for a in rows:
            k = a.get("ProspectActivityId") or a.get("Id")
            if k and k in seen:
                continue
            seen.add(k)
            out.append(a)
    return out


def fetch(groups: list[str], start: str, end: str, out: str, cache: str, client=None) -> dict:
    c = client or LeadSquaredClient()
    os.makedirs(cache, exist_ok=True)
    now = now_utc()
    d0 = ist_day_start(start)
    last = min(ist_day_start(end) + timedelta(days=1), now) - timedelta(seconds=1)
    users = list({u["ID"]: u for g in groups for u in patient(lambda g=g: c.get_users_in_group(g), "users")}.values())
    user_ids = {u["ID"] for u in users}
    log(f"{len(users)} users in {', '.join(map(repr, groups))}")

    today = now.astimezone(IST).strftime("%Y%m%d")  # leads are read fresh once per IST day, so owners and stages are current

    def owner(u):
        cl = c.clone()
        read = lambda: list(cl.iter_leads("OwnerId", u["ID"], page_size=1000, columns=LEAD_COLUMNS))  # noqa: E731
        return cached(os.path.join(cache, f"leads_{today}_{u['ID']}.json"), lambda: patient(read, f"leads of {u['ID']}"))

    with ThreadPoolExecutor(WORKERS) as pool:
        leads = list({l["ProspectID"]: l for batch in pool.map(owner, users) for l in batch}.values())
    lead_ids = {l["ProspectID"] for l in leads}
    log(f"{len(leads)} team-owned leads")

    def mine(a):
        call = parse_phone_call(a)
        return call["user_id"] in user_ids or call["lead_id"] in lead_ids

    def theirs(a):
        return a.get("RelatedProspectId") in lead_ids

    calls = []
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        kept = [parse_phone_call(a) for a in started(c, cache, ev, d0, last, mine, now)]
        calls.extend(kept)
        log(f"event {ev}: {len(kept)} kept")
    payments, zip_acts = [], []
    for ev, sink in ((PAYMENT_SUCCESS, payments),) + tuple((z, zip_acts) for z in ZIP_EVENTS):
        to = last if ev == PAYMENT_SUCCESS else min(last + ZIP_LATE_WINDOW, now)  # a call ending after midnight
        for a in started(c, cache, ev, d0, to, theirs, now):
            a["note"] = parse_activity_note(a.get("ActivityEvent_Note"))
            sink.append(a)
    log(f"payments {len(payments)}, zip {len(zip_acts)}")
    snap = {"group": " + ".join(groups), "groups": groups, "start": start, "end": end, "fetched_at": now.isoformat(),
            "users": users, "leads": leads, "calls": calls, "payments": payments, "zip_activities": zip_acts,
            "source": "bulk (resumable)"}
    with open(out, "w") as fh:
        json.dump(snap, fh)
    log(f"saved {out}: {len(calls)} calls, {len(payments)} payments, {len(zip_acts)} zip activities")
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="+", help="GROUP [GROUP ...] START END OUT")
    ap.add_argument("--cache", help="cache folder (default data/fetch_cache/<groups>)")
    a = ap.parse_args(argv)
    *groups, start, end, out = a.args
    for d in (start, end):
        datetime.strptime(d, "%Y-%m-%d")
    cache = a.cache or os.path.join("data", "fetch_cache", slug("_".join(groups)))
    try:
        fetch(groups, start, end, out, cache)
    except Deferred as e:
        log(f"stopped: {e}. Everything read so far is kept in {cache}; rerun the same command to resume.")
        return DEFERRED_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
