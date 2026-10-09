"""Rebuild the dashboard's daily totals for past IST days and write them as SQL for D1.

Runs locally (no Cloudflare CPU limits), using the same definitions as the Worker in dashboard/:
outbound/inbound calls per caller per day, dials per hour, new leads per day/source/owner, first-time
enrollments, and Zipteams notes attributed to the lead's last answered call.

    PYTHONPATH=. python scripts/d1_backfill.py 2026-09-08 2026-10-07 data/d1_backfill.sql
    cd dashboard && npx wrangler d1 execute sales_dashboard --remote --file ../data/d1_backfill.sql

The SQL first deletes those days, so re-running is safe. Unless --no-cursors is given it also sets
every ingest cursor to the start of the day after END, so the live cron carries on from there with no
overlap (rows it had already counted from that day on are deleted). Backfill only complete days
(END = yesterday); early morning is best, as the cron then has little of today to catch up on.
Free tier: D1 allows 100,000 rows written per day; the script prints its estimate - split long ranges.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from analytics.definitions import team_of
from analytics.team_report import zip_score
from integrations.timeutil import IST
from integrations.leadsquared import (
    PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, format_datetime, parse_phone_call,
)

REAL_CALL_SECS = 120
ENROLLED = "Course Enrolled"
ZIP_NOTES = 237
STAGE_CHANGE = 3002
MAX_STATEMENT = 90_000


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def utc(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if s else None
    except ValueError:
        return None


def ist_day(t: datetime) -> str:
    return t.astimezone(IST).strftime("%Y-%m-%d")


def q(v) -> str:
    return "'" + str(v if v is not None else "").replace("'", "''") + "'"


def inserts(table: str, cols: list[str], rows: list[list[str]], tail: str = "") -> list[str]:
    head = f"INSERT INTO {table} ({','.join(cols)}) VALUES "
    out, parts, size = [], [], len(head) + len(tail)
    for r in rows:
        v = "(" + ",".join(r) + ")"
        if parts and size + len(v) + 1 > MAX_STATEMENT:
            out.append(head + ",".join(parts) + f" {tail};")
            parts, size = [], len(head) + len(tail)
        parts.append(v)
        size += len(v) + 1
    if parts:
        out.append(head + ",".join(parts) + f" {tail};")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("start", help="first IST day, YYYY-MM-DD")
    ap.add_argument("end", help="last IST day (yesterday at the latest)")
    ap.add_argument("out", help="SQL file to write")
    ap.add_argument("--no-cursors", action="store_true", help="don't move the live ingest cursors")
    args = ap.parse_args()

    d0 = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=IST)
    d1 = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)
    if d1 > datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0):
        sys.exit("END must be yesterday or earlier: today's totals are built by the live cron.")
    c = LeadSquaredClient(max_retries=8)

    users = [{**u, "team": t} for u in c.get_users() if (t := team_of(u.get("MemberOfGroups"), ""))]
    log(f"users in a team: {len(users)}")

    # ---- calls
    caller = defaultdict(lambda: defaultdict(int))
    names: dict[str, str] = {}
    hours = defaultdict(lambda: [0, 0])
    answered_by_lead = defaultdict(list)
    day = d0
    while day < d1:
        nxt = day + timedelta(days=1)
        for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
            n = 0
            for a in c.iter_activities_by_event(ev, day, nxt - timedelta(seconds=1)):
                call = parse_phone_call(a)
                t = utc(call["start_utc"])
                if not t or not (day <= t < nxt) or not call["user_id"]:
                    continue
                n += 1
                k = (ist_day(t), call["user_id"])
                if call["caller"]:
                    names[call["user_id"]] = call["caller"].strip()
                ok = call["status"] == "Answered"
                if ev == PHONE_OUTBOUND:
                    caller[k]["dials"] += 1
                    caller[k]["answered"] += ok
                    caller[k]["not_answered"] += call["status"] == "NotAnswered"
                    caller[k]["failures"] += call["status"] == "CallFailure"
                    if ok:
                        caller[k]["talk_secs"] += call["duration"]
                        caller[k]["real_calls"] += call["duration"] >= REAL_CALL_SECS
                    h = hours[(ist_day(t), t.astimezone(IST).hour)]
                    h[0] += 1
                    h[1] += ok
                else:
                    caller[k]["inbound"] += 1
                    caller[k]["inbound_missed"] += not ok
                if ok and call["lead_id"]:
                    answered_by_lead[call["lead_id"]].append((t, call["user_id"]))
            log(f"{day:%Y-%m-%d} event {ev}: {n} calls")
        day = nxt
    for v in answered_by_lead.values():
        v.sort()

    # ---- Zipteams notes
    zipd = defaultdict(lambda: defaultdict(int))
    for a in c.iter_activities_by_event(ZIP_NOTES, d0, d1 - timedelta(seconds=1)):
        t = utc(a.get("CreatedOn"))
        if not t or not (d0 <= t < d1):
            continue
        prior = [u for ct, u in answered_by_lead.get(a.get("RelatedProspectId"), []) if ct <= t]
        z = zipd[(ist_day(t), prior[-1] if prior else "")]
        z["analysed"] += 1
        for field, key in (("mx_Custom_4", "pitch"), ("mx_Custom_5", "probe"), ("mx_Custom_6", "obj")):
            s = zip_score(a.get(field))
            if s is not None:
                z[f"{key}_n"] += 1
                z[f"{key}_sum"] += int(s)
        intent = (a.get("mx_Custom_1") or "").upper()
        if intent and intent not in ("NOT_AVAILABLE", "UNKNOWN"):
            z["intent_rated"] += 1
            z["intent_high"] += intent == "HIGH"
            z["intent_moderate"] += intent == "MODERATE"
            z["intent_low"] += intent == "LOW"
    log(f"zip notes: {sum(z['analysed'] for z in zipd.values())}")

    # ---- new leads
    leads = defaultdict(int)
    for l in c.iter_leads("CreatedOn", format_datetime(d0), operator=">=", columns=["ProspectID", "CreatedOn", "Source", "OwnerId"],
                          sort_by="CreatedOn", descending=False, page_size=1000):
        t = utc(l.get("CreatedOn"))
        if not t:
            continue
        if t >= d1:
            break
        leads[(ist_day(t), (l.get("Source") or "(blank)")[:80], l.get("OwnerId") or "")] += 1
    log(f"new leads: {sum(leads.values())}")

    # ---- first-time enrollments
    enroll = []
    for l in c.iter_leads("ProspectStage", ENROLLED, columns=["ProspectID", "ModifiedOn", "OwnerId"],
                          sort_by="ModifiedOn", page_size=1000):
        mod = utc(l.get("ModifiedOn"))
        if mod and mod < d0:
            break
        hist = (c.get_lead_activities(l["ProspectID"], activity_event=STAGE_CHANGE, row_count=100) or {}).get("ProspectActivities") or []
        firsts = sorted(((utc(h.get("CreatedOn")), {d.get("Key"): d.get("Value") for d in h.get("Data") or []})
                         for h in hist if utc(h.get("CreatedOn"))), key=lambda x: x[0])
        first = next(((t, d) for t, d in firsts if d.get("CurrentStage") == ENROLLED), None)
        if first and d0 <= first[0] < d1:
            enroll.append((l["ProspectID"], ist_day(first[0]), format_datetime(first[0]), l.get("OwnerId") or "",
                           (first[1].get("CreatedBy") or "").strip()))
    log(f"first-time enrollments: {len(enroll)}")

    # ---- SQL
    days = f"day BETWEEN {q(args.start)} AND {q(args.end)}"
    sql = [f"DELETE FROM {t} WHERE {days};" for t in ("caller_day", "calls_hour", "lead_day", "zip_day", "enrollment")]
    now = format_datetime(datetime.now(timezone.utc))
    sql += inserts("users", ["id", "name", "team", "updated_at"],
                   [[q(u["ID"]), q(f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip()),
                     q(u["team"]), q(now)] for u in users],
                   "ON CONFLICT(id) DO UPDATE SET name = excluded.name, team = excluded.team, updated_at = excluded.updated_at")
    cols = ["dials", "answered", "not_answered", "failures", "real_calls", "talk_secs", "inbound", "inbound_missed"]
    sql += inserts("caller_day", ["day", "user_id", "name", *cols],
                   [[q(d), q(u), q(names.get(u, "")), *[str(int(v[c])) for c in cols]] for (d, u), v in caller.items()])
    sql += inserts("calls_hour", ["day", "hour", "dials", "answered"],
                   [[q(d), str(h), str(v[0]), str(v[1])] for (d, h), v in hours.items()])
    sql += inserts("lead_day", ["day", "source", "owner_id", "n"], [[q(d), q(s), q(o), str(n)] for (d, s, o), n in leads.items()])
    zcols = ["analysed", "pitch_n", "pitch_sum", "probe_n", "probe_sum", "obj_n", "obj_sum",
             "intent_rated", "intent_high", "intent_moderate", "intent_low"]
    sql += inserts("zip_day", ["day", "user_id", *zcols], [[q(d), q(u), *[str(int(v[c])) for c in zcols]] for (d, u), v in zipd.items()])
    sql += inserts("enrollment", ["lead_id", "day", "at", "owner_id", "set_by"], [[q(x) for x in e] for e in enroll],
                   "ON CONFLICT(lead_id) DO NOTHING")
    if not args.no_cursors:
        # The cron restarts from the day after END, so drop anything it already counted from then on.
        nxt_day = d1.strftime("%Y-%m-%d")
        sql += [f"DELETE FROM {t} WHERE day >= {q(nxt_day)};" for t in ("caller_day", "calls_hour", "lead_day", "zip_day", "enrollment")]
        cur = format_datetime(d1)
        sql += inserts("sync_state", ["task", "cursor", "page", "updated_at", "last_error"],
                       [[q(t), q(cur), "1", q(now), "NULL"] for t in ("calls_out", "calls_in", "leads", "zip", "enroll")],
                       "ON CONFLICT(task) DO UPDATE SET cursor = excluded.cursor, page = 1, last_error = NULL")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sql) + "\n")

    rows = len(users) + len(caller) + len(hours) + len(leads) + len(zipd) + len(enroll)
    log(f"wrote {args.out}: ~{rows:,} rows (+ deletes of any existing rows for these days)")
    if rows > 80_000:
        log("WARNING: over 80,000 rows - on the free tier split the range across days (100,000 rows written/day).")
    log(f"apply: cd dashboard && npx wrangler d1 execute sales_dashboard --remote --file ../{args.out}")


if __name__ == "__main__":
    main()
