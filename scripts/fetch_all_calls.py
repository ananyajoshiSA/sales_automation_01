"""Every phone call (inbound + outbound) across the account for an IST date range, compact form.

    python scripts/fetch_all_calls.py 2026-10-01 2026-10-07 out.jsonl
"""
import json
import sys
from datetime import datetime, timedelta, timezone

from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_phone_call

IST = timezone(timedelta(hours=5, minutes=30))
KEEP = ("lead_id", "direction", "start_utc", "user_id", "caller", "status", "duration", "display_number")


def main(start, end, out):
    c = LeadSquaredClient()
    day = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    stop = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)
    with open(out, "w") as fh:
        while day < stop:
            nxt = day + timedelta(days=1)
            for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
                n = 0
                for a in c.iter_activities_by_event(ev, day, nxt - timedelta(seconds=1)):
                    call = parse_phone_call(a)
                    fh.write(json.dumps({k: call.get(k) for k in KEEP}) + "\n")
                    n += 1
                print(f"{day:%Y-%m-%d} ev{ev}: {n}", flush=True)
            day = nxt
    print("done", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
