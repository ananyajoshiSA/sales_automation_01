"""All phone calls (inbound + outbound) made/received by a set of users over an IST date range.

Calls are kept by when they started (IST days, end inclusive), including calls edited up to 3 days later.

    python scripts/fetch_user_calls.py users.json 2026-09-01 2026-09-30 out.json
users.json: {"TeamName": [{"id": ..., "name": ...}, ...], ...}
"""
import json
import sys
from datetime import timedelta

from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_phone_call
from integrations.timeutil import ist_day_start


def main(users_path, start, end, out):
    teams = json.load(open(users_path))
    uid = {m["id"]: t for t, ms in teams.items() for m in ms}
    names = {m["name"]: t for t, ms in teams.items() for m in ms}
    c = LeadSquaredClient()
    d0 = ist_day_start(start)
    d1 = ist_day_start(end) + timedelta(days=1) - timedelta(seconds=1)
    calls = []
    for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
        n = k = 0
        for a in c.iter_activities_started(ev, d0, d1):
            n += 1
            call = parse_phone_call(a)
            team = uid.get(call["user_id"]) or names.get((call["caller"] or "").strip())
            if team:
                call["team"] = team
                calls.append(call)
                k += 1
        print(f"{start} to {end} ev{ev}: {n} total, {k} kept", flush=True)
    json.dump(calls, open(out, "w"))
    print(f"saved {len(calls)} calls -> {out}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:5])
