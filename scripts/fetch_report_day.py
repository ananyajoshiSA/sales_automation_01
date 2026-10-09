"""Fetch everything the daily team performance report (Parameters v1.0) needs for one IST day.

Writes data/report_{DATE}/: meta.json, users.json, calls.json (S1), zip.json (S3),
enrollments.json (S4: first-ever "Course Enrolled" inside the conversion window) and
payments.json (S6). Read-only against LeadSquared. Activities are read with a 3-day edit margin and kept
by their start (``CreatedOn``), because the API filters on the last edit (``iter_activities_started``).

    python scripts/fetch_report_day.py 2026-10-05 [--as-of "2026-10-08 13:00"] [--out data/report_2026-10-05]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

from analytics.team_performance import ENROLLED, first_enrollment
from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, format_datetime, parse_phone_call
from integrations.timeutil import EDIT_MARGIN, IST, ist_day_start, now_utc, utc

ZIP_NOTES, PAYMENT_SUCCESS, STAGE_CHANGE = 237, 213, 3002


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def windows(date: str, as_of: str | None = None) -> tuple[datetime, datetime, datetime]:
    """P1 day window [d0, d1] and P3 conversion window end (target day + 3 days, capped at now / as-of)."""
    d0 = ist_day_start(date)
    d1 = d0 + timedelta(days=1) - timedelta(seconds=1)
    cap = datetime.strptime(as_of, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if as_of else now_utc()
    return d0, d1, min(d0 + timedelta(days=4) - timedelta(seconds=1), cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--as-of", help="cap the conversion window at this IST time (YYYY-MM-DD HH:MM) to reproduce a past run")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = a.out or f"data/report_{a.date}"
    os.makedirs(out, exist_ok=True)
    w = lambda name, obj: json.dump(obj, open(os.path.join(out, name), "w"))  # noqa: E731

    c = LeadSquaredClient()
    d0, d1, cw_end = windows(a.date, a.as_of)
    w("meta.json", {"date": a.date, "d0": d0.isoformat(), "cw_end": cw_end.isoformat(),
                    "fetched": now_utc().isoformat(), "edit_margin_days": EDIT_MARGIN.days})
    w("users.json", c.get_users())
    calls = [parse_phone_call(x) for ev in (PHONE_OUTBOUND, PHONE_INBOUND) for x in c.iter_activities_started(ev, d0, d1)]
    w("calls.json", calls)
    log("calls", len(calls))
    zips = list(c.iter_activities_started(ZIP_NOTES, d0, d1))
    w("zip.json", zips)
    log("zipteams notes", len(zips))
    pays = list(c.iter_activities_started(PAYMENT_SUCCESS, d0, cw_end))
    w("payments.json", pays)
    log("payments", len(pays))

    # S4: leads now in Course Enrolled, newest edit first; a lead first enrolled in the window was edited then too.
    enrolled, scanned = [], 0
    for l in c.iter_leads("ProspectStage", ENROLLED, columns=["ProspectID", "ModifiedOn", "OwnerId", "OwnerIdName"],
                          sort_by="ModifiedOn", page_size=1000):
        mod = utc(l.get("ModifiedOn"))
        if mod and mod < d0:
            break
        scanned += 1
        hist = (c.get_lead_activities(l["ProspectID"], activity_event=STAGE_CHANGE, row_count=100) or {}).get("ProspectActivities") or []
        first = first_enrollment(hist)
        if first and d0 <= first <= cw_end:
            enrolled.append({**l, "enrolled_at": format_datetime(first)})
    w("enrollments.json", enrolled)
    log(f"scanned {scanned} enrolled leads, {len(enrolled)} first enrollments in window -> {out}")


if __name__ == "__main__":
    main()
