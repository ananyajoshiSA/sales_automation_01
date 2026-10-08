from analytics.lead_priority import build_timelines, merge_snapshots

USERS = [{"ID": "u1", "FirstName": "A", "LastName": "B"}]


def snap(leads, calls, zips, fetched="2026-10-07T11:00:00+00:00"):
    return {"fetched_at": fetched, "users": USERS, "leads": leads, "calls": calls, "zip_activities": zips}


def call(lead, t, direction="outbound", status="Answered", dur=600, aid=None):
    return {"activity_id": aid or f"{lead}{t}", "lead_id": lead, "start_utc": t, "direction": direction,
            "status": status, "duration": dur, "caller": "A B", "user_id": "u1"}


def zipnote(lead, t, intent, reason=""):
    return {"ProspectActivityId": f"z{lead}{t}", "ActivityEvent": "237", "RelatedProspectId": lead,
            "CreatedOn": t, "mx_Custom_1": intent, "mx_Custom_2": reason, "ActivityEvent_Note": "<p>summary</p>"}


def by_id(rows):
    return {r["lead_id"]: r for r in rows}


def test_zip_high_but_going_dark_is_flagged_and_ranked_below_engaged_neutral():
    leads = [{"ProspectID": "HOT", "OwnerIdName": "A B", "ProspectStage": "Call Back Later"},
             {"ProspectID": "WARM", "OwnerIdName": "A B", "ProspectStage": "Follow Up For Closure"}]
    calls = [call("HOT", "2026-10-03 06:00:00")] + [
        call("HOT", f"2026-10-0{d} 06:00:00", status="NotAnswered", dur=0) for d in (4, 5, 6)] + [
        call("HOT", "2026-10-06 09:00:00", status="NotAnswered", dur=0),
        call("HOT", "2026-10-06 12:00:00", status="NotAnswered", dur=0),
        call("WARM", "2026-10-06 06:00:00", dur=1200),
        call("WARM", "2026-10-06 10:00:00", direction="inbound", dur=300)]
    zips = [zipnote("HOT", "2026-10-03 06:30:00", "HIGH"),
            zipnote("WARM", "2026-10-06 06:30:00", "NEUTRAL", "asked about EMI and the fee")]
    r = by_id(build_timelines(snap(leads, calls, zips), 15))
    assert any("unanswered dials since last talk" in f for f in r["HOT"]["crosscheck_flags"])
    assert any("understates interest" in f for f in r["WARM"]["crosscheck_flags"])
    assert {"emi_or_loan", "fee"} <= set(r["WARM"]["buying_signals"])
    assert r["WARM"]["pre_score"] > r["HOT"]["pre_score"]


def test_unreturned_missed_call_flagged():
    leads = [{"ProspectID": "L", "OwnerIdName": "A B", "ProspectStage": "Call Back Later"}]
    calls = [call("L", "2026-10-05 06:00:00"), call("L", "2026-10-06 06:00:00", direction="inbound", status="Missed", dur=0)]
    r = by_id(build_timelines(snap(leads, calls, []), 15))
    assert r["L"]["inbound_missed_unreturned"] == 1


def test_leads_without_conversation_excluded_and_window_respected():
    leads = [{"ProspectID": "X", "OwnerIdName": "A B"}, {"ProspectID": "OLD", "OwnerIdName": "A B"}]
    calls = [call("X", "2026-10-06 06:00:00", status="NotAnswered", dur=0), call("OLD", "2026-09-01 06:00:00")]
    assert build_timelines(snap(leads, calls, []), 15) == []


def test_merge_snapshots_dedupes():
    a = snap([{"ProspectID": "L", "ProspectStage": "old"}], [call("L", "2026-10-01 06:00:00", aid="c1")], [],
             fetched="2026-10-02T00:00:00+00:00")
    b = snap([{"ProspectID": "L", "ProspectStage": "new"}], [call("L", "2026-10-01 06:00:00", aid="c1"),
                                                             call("L", "2026-10-05 06:00:00", aid="c2")], [])
    m = merge_snapshots([b, a])
    assert m["leads"][0]["ProspectStage"] == "new" and len(m["calls"]) == 2


def test_lead_who_rang_and_was_never_called_back_is_kept_without_a_conversation():
    leads = [{"ProspectID": "R", "OwnerIdName": "A B", "ProspectStage": "New Lead"}]
    calls = [call("R", "2026-10-06 06:00:00", direction="inbound", status="Missed", dur=0)]
    r = by_id(build_timelines(snap(leads, calls, []), 15))
    assert r["R"]["inbound_missed_unreturned"] == 1
