"""Every phone call (inbound + outbound) across the account for an IST date range, compact form.

Calls are kept by when they started (IST days, end inclusive), including calls edited up to 3 days later.

    python scripts/fetch_all_calls.py 2026-10-01 2026-10-07 out.jsonl
"""
import json
import sys
from collections import Counter
from datetime import timedelta

from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_phone_call
from integrations.timeutil import ist_day, ist_day_start, utc

KEEP = ("lead_id", "direction", "start_utc", "user_id", "caller", "status", "duration", "display_number")


def main(start, end, out):
    c = LeadSquaredClient()
    d0 = ist_day_start(start)
    d1 = ist_day_start(end) + timedelta(days=1) - timedelta(seconds=1)
    with open(out, "w") as fh:
        for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
            per_day = Counter()
            for a in c.iter_activities_started(ev, d0, d1):
                call = parse_phone_call(a)
                fh.write(json.dumps({k: call.get(k) for k in KEEP}) + "\n")
                per_day[ist_day(utc(call["start_utc"])) or "unreadable time"] += 1
            for day, n in sorted(per_day.items()):
                print(f"{day} ev{ev}: {n}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
