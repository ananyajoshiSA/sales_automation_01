from datetime import datetime, timedelta, timezone

from analytics.zip_calls import attach, match_notes

D_END = datetime(2026, 10, 5, 18, 30, tzinfo=timezone.utc)   # midnight IST


def call(lead, start, dur, ans=True):
    return {"lead_id": lead, "t": datetime.fromisoformat(start).replace(tzinfo=timezone.utc), "duration": dur, "ans": ans}


def note(lead, created, intent="HIGH"):
    return {"RelatedProspectId": lead, "CreatedOn": created, "mx_Custom_1": intent, "ProspectActivityId": f"n-{lead}-{created}"}


def test_each_note_lands_on_the_call_it_analysed():
    calls = [call("L1", "2026-10-05 05:00:00", 300), call("L1", "2026-10-05 07:00:00", 600),
             call("L1", "2026-10-05 07:30:00", 0, ans=False)]
    pairs, dropped, other = match_notes([note("L1", "2026-10-05 07:10:00"), note("L9", "2026-10-05 07:10:00")], calls, D_END)
    assert [c["t"].hour for _, c in pairs] == [7] and (dropped, other) == (1, 0)


def test_a_call_running_past_midnight_keeps_its_note_but_a_next_day_call_does_not_borrow_one():
    late = call("L1", "2026-10-05 18:25:00", 600)                       # 23:55 IST, ends 00:05 IST
    zips = [note("L1", "2026-10-05 18:35:00"),                          # written as that call ended
            note("L1", "2026-10-05 20:00:00", "LOW")]                   # 01:30 IST: a next-day call's note
    dropped, other = attach(zips, [late], D_END)
    assert late["zip"]["intent"] == "HIGH" and (dropped, other) == (0, 1)
