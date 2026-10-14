"""Inputs for analytics.team_lead_analysis: one manager's teams over an IST date range (read-only).

Steps, each resumable, run in order (``all`` runs them all):

- ``roster``: users in the given LeadSquared groups -> ``users.json`` (whole account) and ``team_users.json``
  ({name: {"id", "team"}}; a user's team is the first listed group they belong to, the manager's is "Manager").
- ``leads``: every lead a team member owns now whose Assigned On is in the range -> ``leads.json``.
- ``calls``: every call a team member made or took, one file per 4-day chunk -> ``calls/<first day>.json``.
- ``histories``: full activity history of each lead in ``leads.json`` -> ``hist.jsonl``.
- ``extras``: leads the team had a real conversation with (answered, 2+ min) that the team no longer owns
  (enrolled leads moved to onboarding, leads sent back to the pool): their histories go into ``hist.jsonl`` and
  their lead rows into ``extra_leads.json``.
- ``contacts``: phone and email of every lead, for matching GrowthX funnels -> ``contacts.json`` (lead PII:
  stays under data/).

LeadSquared allows 30 calls per 5 seconds for the whole account, so run one step at a time.

    python scripts/fetch_team_lead_analysis.py all 2026-08-14 2026-10-13 data/inayat \
        --groups "Team Bootcamp +Anas" "Team Bootcamp +Jyoti" "Corporate law trainees" "Team Uzair" --manager "Inayat Khan"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from analytics.definitions import REAL_CONVERSATION_SECS
from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_phone_call
from integrations.leadsquared.client import LeadSquaredError
from integrations.timeutil import ist_day_start, now_utc, utc
from scripts.fetch_lead_histories import main as fetch_histories

LEAD_COLUMNS = [
    "ProspectID", "OwnerId", "OwnerIdName", "ProspectStage", "CreatedOn", "ModifiedOn", "mx_Assigned_On",
    "mx_Assigned_By", "Source", "mx_Campaign_Name", "mx_Enquired_Course", "mx_Bootcamp_Date", "mx_Bootcamp_attended",
    "mx_Bootcamp_collections", "mx_Enrollment_date", "mx_Course_Fees", "mx_Bootcamp_leads", "mx_Next_follow_up_date",
]
CONTACT_COLUMNS = ["ProspectID", "Phone", "Mobile", "EmailAddress"]


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def full_name(u: dict) -> str:
    return " ".join(f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".split())


def patient(fn, *a, **kw):
    """Retry a whole step on the account-wide rate limit instead of failing a long fetch."""
    for wait in (10, 30, 60, 120, 300):
        try:
            return fn(*a, **kw)
        except LeadSquaredError as e:
            if e.status_code != 429:
                raise
            log(f"rate limited, waiting {wait}s")
            time.sleep(wait)
    return fn(*a, **kw)


def roster(c: LeadSquaredClient, groups: list[str], manager: str | None, out: str) -> None:
    users = c.get_users()
    json.dump(users, open(os.path.join(out, "users.json"), "w"))
    team = {}
    for u in users:
        mine = [g for g in groups if g in [m.strip() for m in u.get("MemberOfGroups") or []]]
        if mine:
            name = full_name(u)
            team[name] = {"id": u["ID"], "team": "Manager" if name == manager else mine[0]}
    json.dump(team, open(os.path.join(out, "team_users.json"), "w"), indent=1)
    log(f"{len(team)} team members")


def owner_leads(c: LeadSquaredClient, owner_id: str, d0, columns: list[str]) -> list[dict]:
    out, page = [], 1
    while True:
        batch = patient(c.search_leads, "OwnerId", owner_id, columns=columns, page_index=page, page_size=1000,
                        sort_by="mx_Assigned_On", descending=True)
        out += [l for l in batch if (t := utc(l.get("mx_Assigned_On"))) and t >= d0]
        last = utc(batch[-1].get("mx_Assigned_On")) if batch else None
        if len(batch) < 1000 or not last or last < d0:
            return out
        page += 1


def leads(c: LeadSquaredClient, start: str, out: str) -> None:
    team = json.load(open(os.path.join(out, "team_users.json")))
    d0 = ist_day_start(start)
    rows = {}
    for name, m in team.items():
        for l in owner_leads(c, m["id"], d0, LEAD_COLUMNS):
            rows[l["ProspectID"]] = l
        log(f"{name}: {len(rows)} leads so far")
    json.dump(list(rows.values()), open(os.path.join(out, "leads.json"), "w"))


def calls(c: LeadSquaredClient, start: str, end: str, out: str, days: int = 4, workers: int = 8) -> None:
    """One file per ``days``-day chunk, chunks read in parallel: a 1,000-row page takes several seconds, so one
    stream is slow while far under the rate limit. Each read also covers 3 days of later edits."""
    team = json.load(open(os.path.join(out, "team_users.json")))
    ids = {m["id"] for m in team.values()}
    names = set(team)
    os.makedirs(os.path.join(out, "calls"), exist_ok=True)
    chunks, day, last = [], ist_day_start(start), ist_day_start(end) + timedelta(days=1)
    while day < last:
        chunks.append((day, min(day + timedelta(days=days), last)))
        day = chunks[-1][1]

    def chunk(span) -> None:
        d0, d1 = span
        path = os.path.join(out, "calls", d0.strftime("%Y-%m-%d") + ".json")
        if os.path.exists(path):
            return

        def read():
            kept = []
            for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
                for a in c.iter_activities_started(ev, d0, d1 - timedelta(seconds=1)):
                    call = parse_phone_call(a)
                    if call["user_id"] in ids or " ".join((call["caller"] or "").split()) in names:
                        kept.append(call)
            return kept
        kept = patient(read)
        json.dump(kept, open(path + ".tmp", "w"))
        os.replace(path + ".tmp", path)
        log(f"{d0:%Y-%m-%d} to {d1 - timedelta(days=1):%Y-%m-%d}: {len(kept)} team calls")

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(chunk, chunks))


def _ids(path: str) -> set[str]:
    return {l["ProspectID"] for l in json.load(open(path))} if os.path.exists(path) else set()


def histories(out: str, threads: int) -> None:
    ids = os.path.join(out, "lead_ids.json")
    json.dump(sorted(_ids(os.path.join(out, "leads.json"))), open(ids, "w"))
    fetch_histories(ids, os.path.join(out, "hist.jsonl"), threads)


def extras(c: LeadSquaredClient, out: str, threads: int) -> None:
    have = _ids(os.path.join(out, "leads.json"))
    talked = set()
    cdir = os.path.join(out, "calls")
    for f in sorted(os.listdir(cdir)):
        for k in json.load(open(os.path.join(cdir, f))):
            if k["status"] == "Answered" and k["duration"] >= REAL_CONVERSATION_SECS and k["lead_id"]:
                talked.add(k["lead_id"])
    todo = sorted(talked - have)
    log(f"{len(talked)} leads with a real conversation, {len(todo)} not in leads.json")
    path = os.path.join(out, "extra_ids.json")
    json.dump(todo, open(path, "w"))
    fetch_histories(path, os.path.join(out, "hist.jsonl"), threads)
    rows_path = os.path.join(out, "extra_leads.json")
    rows = {l["ProspectID"]: l for l in json.load(open(rows_path))} if os.path.exists(rows_path) else {}
    for n, lid in enumerate(i for i in todo if i not in rows):
        found = patient(c.search_leads, "ProspectID", lid, columns=LEAD_COLUMNS, page_size=1)
        rows[lid] = found[0] if found else {"ProspectID": lid}
        if n % 200 == 0:
            json.dump(list(rows.values()), open(rows_path, "w"))
            log(f"{len(rows)} extra lead rows")
    json.dump(list(rows.values()), open(rows_path, "w"))


def contacts(c: LeadSquaredClient, start: str, out: str) -> None:
    team = json.load(open(os.path.join(out, "team_users.json")))
    d0 = ist_day_start(start)
    rows = {}
    for m in team.values():
        for l in owner_leads(c, m["id"], d0, CONTACT_COLUMNS + ["mx_Assigned_On"]):
            rows[l["ProspectID"]] = {k: l.get(k) for k in CONTACT_COLUMNS}
    for lid in sorted(_ids(os.path.join(out, "extra_leads.json")) - set(rows)):
        found = patient(c.search_leads, "ProspectID", lid, columns=CONTACT_COLUMNS, page_size=1)
        if found:
            rows[lid] = {k: found[0].get(k) for k in CONTACT_COLUMNS}
    json.dump(rows, open(os.path.join(out, "contacts.json"), "w"))
    log(f"{len(rows)} lead contacts")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["roster", "leads", "calls", "histories", "extras", "contacts", "all"])
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("out_dir")
    p.add_argument("--groups", nargs="+", default=[])
    p.add_argument("--manager")
    p.add_argument("--threads", type=int, default=5)
    a = p.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    c = LeadSquaredClient(max_retries=6)
    steps = ["roster", "leads", "calls", "histories", "extras", "contacts"] if a.step == "all" else [a.step]
    for step in steps:
        log(f"== {step}")
        if step == "roster":
            roster(c, a.groups, a.manager, a.out_dir)
        elif step == "leads":
            leads(c, a.start, a.out_dir)
        elif step == "calls":
            calls(c, a.start, a.end, a.out_dir)
        elif step == "histories":
            histories(a.out_dir, a.threads)
        elif step == "extras":
            extras(c, a.out_dir, a.threads)
        else:
            contacts(c, a.start, a.out_dir)
    json.dump({"start": a.start, "end": a.end, "groups": a.groups, "manager": a.manager,
               "fetched_at_utc": now_utc().strftime("%Y-%m-%d %H:%M:%S")},
              open(os.path.join(a.out_dir, "meta.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
