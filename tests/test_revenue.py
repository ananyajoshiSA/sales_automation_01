from analytics.revenue import revenue

USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "K"}]


def test_no_payments_is_flagged_and_estimate_needs_a_fee():
    r = revenue({"payments": [], "calls": [], "users": USERS, "leads": [], "enrolments": 80})
    assert r["payments"] == 0 and "cannot be measured" in r["flag"] and r["estimate_inr"] is None
    assert revenue({"payments": [], "enrolments": 80}, avg_fee=40000)["estimate_inr"] == 3_200_000


def test_payments_credited_to_last_answered_caller_by_course():
    calls = [{"lead_id": "L1", "status": "Answered", "start_utc": "2026-10-05 05:00:00", "user_id": "u1"}]
    pays = [{"RelatedProspectId": "L1", "CreatedOn": "2026-10-05 07:00:00", "note": {"Amount": "₹ 45,000", "Course Name": "Contract drafting"}},
            {"RelatedProspectId": "L2", "CreatedOn": "2026-10-05 07:00:00", "note": {}}]
    leads = [{"ProspectID": "L2", "OwnerIdName": "Ravi S"}]
    r = revenue({"payments": pays, "calls": calls, "users": USERS, "leads": leads})
    assert r["revenue_inr"] == 45000 and r["by_caller"] == [{"caller": "Asha K", "revenue_inr": 45000}]
    assert r["by_course"][0]["course"] == "Contract drafting" and r["payments_without_amount"] == 1
    assert r["rows"][1]["credited_to"] == "Ravi S" and "flag" not in r


def test_named_amount_field():
    pays = [{"RelatedProspectId": "L1", "CreatedOn": "2026-10-05 07:00:00", "mx_Custom_2": "12000", "mx_Custom_3": "HR bootcamp"}]
    r = revenue({"payments": pays}, amount_field="mx_Custom_2", course_field="mx_Custom_3")
    assert r["revenue_inr"] == 12000 and r["by_course"][0]["course"] == "HR bootcamp"
