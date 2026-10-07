from analytics.team_report import analyse


def call(lead, user, t, direction="outbound", status="Answered", dur=200, caller="A B"):
    return {"lead_id": lead, "user_id": user, "start_utc": t, "direction": direction,
            "status": status, "duration": dur, "caller": caller}


SNAP = {
    "fetched_at": "2026-10-07T11:00:00+00:00",
    "users": [{"ID": "u1", "FirstName": "A", "LastName": "B"}],
    "leads": [
        # assigned 11:00 IST (05:30 UTC), dialled 3 min later, connected 10 min later
        {"ProspectID": "L1", "OwnerIdName": "A B", "Source": "Lawsikho", "ProspectStage": "Course Enrolled",
         "mx_Assigned_On": "2026-10-06 05:30:00"},
        # assigned during working hours, never dialled
        {"ProspectID": "L2", "OwnerIdName": "A B", "Source": "Inbound Phone call", "ProspectStage": "New Lead",
         "mx_Assigned_On": "2026-10-06 06:00:00", "mx_Next_follow_up_date": "2026-10-06 09:00:00"},
    ],
    "calls": [
        call("L1", "u1", "2026-10-06 05:33:00", status="NotAnswered", dur=0),
        call("L1", "u1", "2026-10-06 05:40:00"),
        call("L2", None, "2026-10-06 07:00:00", direction="inbound", status="Missed", dur=0, caller=""),
    ],
    "zip_activities": [
        {"ActivityEvent": "237", "RelatedProspectId": "L1", "CreatedOn": "2026-10-06 06:00:00",
         "mx_Custom_1": "HIGH", "mx_Custom_4": '{"mx_CustomObject_1":"80"}',
         "mx_Custom_5": '{"mx_CustomObject_1":"60"}'},
    ],
}


def test_analyse():
    r = analyse(SNAP, "2026-10-06", "2026-10-06")
    stl = {x["lead_id"]: x for x in r["speed_to_lead_rows"]}
    assert stl["L1"]["mins_to_first_dial"] == 3 and stl["L1"]["mins_to_first_connect"] == 10
    assert stl["L2"]["mins_to_first_dial"] is None
    assert r["speed_to_lead_working_hours"]["≤5 min"] == 1 and r["speed_to_lead_working_hours"]["never"] == 1

    assert len(r["missed_inbound"]) == 1 and r["missed_inbound"][0]["mins_to_callback"] is None
    assert r["follow_ups"][0]["called_within_2h"] is False

    caller = r["callers"][0]
    assert caller["dials_per_day"] == 2 and caller["connected_per_day"] == 1 and caller["meaningful_per_day"] == 1

    q = r["quality"][0]
    assert (q["caller"], q["product_pitch"], q["probing"], q["high_intent_%"]) == ("A B", 80, 60, 100)
    assert r["outcomes"][0]["enrolled"] == 1
