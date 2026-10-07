"""All phone calls (inbound + outbound) made/received by a set of users over an IST date range.

    python scripts/fetch_user_calls.py users.json 2026-09-01 2026-09-30 out.json
users.json: {"TeamName": [{"id": ..., "name": ...}, ...], ...}
"""
import json
import sys
from datetime import datetime, timedelta, timezone

from integrations.leadsquared import PHONE_INBOUND, PHONE_OUTBOUND, LeadSquaredClient, parse_phone_call

IST = timezone(timedelta(hours=5, minutes=30))


def main(users_path, start, end, out):
    teams = json.load(open(users_path))
    uid = {m["id"]: t for t, ms in teams.items() for m in ms}
    names = {m["name"]: t for t, ms in teams.items() for m in ms}
    c = LeadSquaredClient()
    day = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    stop = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)
    calls = []
    while day < stop:
        nxt = day + timedelta(days=1)
        for ev in (PHONE_OUTBOUND, PHONE_INBOUND):
            n = k = 0
            for a in c.iter_activities_by_event(ev, day, nxt - timedelta(seconds=1)):
                n += 1
                call = parse_phone_call(a)
                team = uid.get(call["user_id"]) or names.get((call["caller"] or "").strip())
                if team:
                    call["team"] = team
                    calls.append(call)
                    k += 1
            print(f"{day:%Y-%m-%d} ev{ev}: {n} total, {k} kept", flush=True)
        day = nxt
    json.dump(calls, open(out, "w"))
    print(f"saved {len(calls)} calls -> {out}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:5])
