from datetime import datetime, timezone

from analytics.fresh_leads import analyse_lead


def act(code, name, t, data=None, note=None, **fields):
    a = {"EventCode": code, "EventName": name, "CreatedOn": t, "Data": [{"Key": k, "Value": v} for k, v in (data or {}).items()]}
    if note:
        a["ActivityFields"] = {"ActivityEvent_Note": note, **fields}
    elif fields:
        a["ActivityFields"] = fields
    return a


def test_fresh_lead_timeline():
    lead = {"ProspectID": "L", "CreatedOn": "2026-10-05 06:00:00", "OwnerIdName": "A B", "Source": "Lawsikho",
            "ProspectStage": "Follow Up For Closure"}
    acts = [
        act(23, "Lead Capture", "2026-10-05 06:00:00"),
        act(3001, "LeadAssigned", "2026-10-05 06:10:00", {"PreviousOwner": "Admin", "CurrentOwner": "Amit Ray"}),
        act(3001, "LeadAssigned", "2026-10-06 05:00:00", {"PreviousOwner": "Amit Ray", "CurrentOwner": "A B"}),
        act(22, "Outbound", "2026-10-06 06:00:00", note="Caller{=}A B{next}Status{=}NotAnswered{next}Duration{=}0"),
        act(22, "Outbound", "2026-10-06 08:00:00", note="Caller{=}A B{next}Status{=}Answered{next}Duration{=}300"),
        act(237, "Zipteams Notes", "2026-10-06 08:10:00", mx_Custom_1="HIGH", mx_Custom_4='{"mx_CustomObject_1":"100"}'),
    ]
    r = analyse_lead(lead, acts, {"A B"}, datetime(2026, 10, 7, tzinfo=timezone.utc))
    assert r["mins_create_to_team"] == 23 * 60
    assert r["mins_team_to_first_dial"] == 60
    assert r["mins_create_to_first_dial"] == 24 * 60
    assert r["mins_create_to_real_conversation"] == 26 * 60
    assert r["holders_before_team"] == "Admin > Amit Ray"
    assert (r["dials"], r["answered_calls"], r["real_conversations"]) == (2, 1, 1)
    assert r["zip_best_intent"] == "HIGH" and r["zip_pitch_done"]
    assert not r["dead_without_real_conversation"]
