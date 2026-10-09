from analytics.call_markers import PAYMENT_STEP
from analytics.coaching import longest_calls, objections, quality, score_sample

USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "K"}, {"ID": "u2", "FirstName": "Ravi", "LastName": "S"}]


def call(user, lead, t, dur, status="Answered"):
    return {"user_id": user, "lead_id": lead, "start_utc": t, "status": status, "duration": dur, "lead_number": "9" + lead}


SNAP = {
    "users": USERS,
    "leads": [{"ProspectID": "L1", "mx_Zip_Objection_Category": "Price, Time", "mx_Enquired_Course": "Contract drafting",
               "ProspectStage": "Course Enrolled", "mx_Zip_Quality_Score": "80"},
              {"ProspectID": "L2", "mx_Zip_Objection_Category": "Price", "mx_Enquired_Course": "Contract drafting",
               "ProspectStage": "Not Interested", "OwnerIdName": "Ravi S"},
              {"ProspectID": "L3", "ProspectStage": "Follow Up For Closure"}],
    "calls": [call("u1", "L1", "2026-10-05 05:00:00", 900), call("u1", "L1", "2026-10-05 07:00:00", 300),
              call("u1", "L3", "2026-10-05 08:00:00", 600), call("u1", "L3", "2026-10-05 09:00:00", 0, "NotAnswered"),
              call("u2", "L3", "2026-10-05 06:00:00", 100)],
    "zip_activities": [{"ActivityEvent": "237", "RelatedProspectId": "L1", "CreatedOn": "2026-10-05 05:20:00",
                        "mx_Custom_4": '{"mx_CustomObject_1":"100"}', "ActivityEvent_Note": "<p>Shared the payment link</p>"},
                       {"ActivityEvent": "237", "RelatedProspectId": "L2", "CreatedOn": "2026-10-05 05:20:00"}],
}


def test_objections_per_caller_course_category():
    rows = {(r["caller"], r["objection"]): r for r in objections(SNAP)}
    assert rows[("Asha K", "Price")]["enrolled"] == 1
    assert rows[("Ravi S", "Price")]["leads"] == 1 and rows[("Ravi S", "Price")]["enrolled_%"] == 0  # owner fallback


def test_quality_attributed_to_last_answered_caller():
    q = {r["caller"]: r for r in quality(SNAP)}
    assert q["Asha K"]["zip_notes"] == 1 and q["Asha K"]["pitch_%"] == 100 and q["Asha K"]["payment_step_%"] == 100
    assert q["Asha K"]["lead_quality_score"] == 80 and q["Asha K"]["probing_%"] is None


def test_longest_real_calls_one_per_lead():
    s = longest_calls(SNAP, per_caller=5)
    assert [(r["caller"], r["lead_id"], r["minutes"]) for r in s] == [("Asha K", "L1", 15.0), ("Asha K", "L3", 10.0)]
    scored = score_sample(s, {"L1": "please make the payment by tomorrow at 6 pm", "L3": "tell me about yourself"})
    assert scored[0]["calls_read"] == 2 and scored[0][PAYMENT_STEP] == 50


def test_sample_transcript_is_the_sampled_call_not_the_longest_on_the_number():
    from datetime import timedelta
    from types import SimpleNamespace

    from analytics.coaching import sample_texts
    from integrations.transcripts import normalize_phone

    s = longest_calls(SNAP, per_caller=5)
    l1 = next(r for r in s if r["lead_id"] == "L1")
    k = normalize_phone(l1["lead_number"])
    api = [SimpleNamespace(phone=k, start_time=l1["_call"]["t"] - timedelta(minutes=330), duration=l1["_call"]["duration"] + 5,
                           transcript="the sampled call"),
           SimpleNamespace(phone=k, start_time=l1["_call"]["t"] + timedelta(days=3), duration=5000, transcript="a later call")]
    assert sample_texts(s, api) == {"L1": "the sampled call"}
