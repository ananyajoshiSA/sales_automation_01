from analytics.call_markers import has_payment_step, marker_rates
from analytics.report_validation import data_checks, spot_check, verdict_numbers_check
from analytics.team_performance import analyse, plan_tracker
from analytics.team_performance_html import report_html, scorecard_cells, tracker_html

USERS = [
    {"ID": "u1", "FirstName": "Asha", "LastName": "K", "MemberOfGroups": ["Team A"]},
    {"ID": "u2", "FirstName": "Ravi", "LastName": "S", "MemberOfGroups": ["Team A", "Other"]},
    {"ID": "u3", "FirstName": "Meena", "LastName": "P", "MemberOfGroups": ["Team A"]},
    {"ID": "u4", "FirstName": "Neha", "LastName": "T", "MemberOfGroups": []},
    {"ID": "bot", "FirstName": "Webinar", "LastName": "Bot", "MemberOfGroups": ["Team A"]},
]


def call(user, lead, start, status="Answered", dur=200, direction="outbound", caller=""):
    return {"user_id": user, "caller": caller, "lead_id": lead, "start_utc": start, "status": status,
            "duration": dur, "direction": direction, "lead_number": "98765" + lead[-5:].rjust(5, "0")}


def run():
    calls = [call("u1", f"L{i}", "2026-10-05 05:00:00") for i in range(10)]
    calls += [call("u2", f"M{i}", "2026-10-05 06:00:00", dur=150) for i in range(10)]
    calls += [call("u3", f"N{i}", "2026-10-05 07:00:00", dur=130) for i in range(10)]
    calls += [call("u3", "L1", "2026-10-05 08:00:00", dur=30),                  # shorter talk on L1: u1 keeps the credit
              call("u1", "X1", "2026-10-05 09:00:00", status="NotAnswered", dur=0),
              call("u4", "Y1", "2026-10-05 09:00:00"),                           # no group -> Unassigned
              call("", "Z1", "2026-10-05 09:00:00", caller="Front Desk"),          # not a user
              call("bot", "B1", "2026-10-05 09:00:00"),                          # bot: excluded everywhere
              call("u1", "L2", "2026-10-05 10:00:00", status="NotAnswered", dur=0, direction="inbound"),
              call("u1", "L2", "2026-10-05 10:30:00", status="NotAnswered", dur=0)]
    zips = [{"CreatedOn": "2026-10-05 05:10:00", "RelatedProspectId": "L1", "mx_Custom_1": "HIGH",
             "mx_Custom_4": '{"mx_CustomObject_1":"100"}', "mx_Custom_5": '{"mx_CustomObject_1":"0"}',
             "ActivityEvent_Note": "<p>Shared the payment link on WhatsApp</p>"},
            {"CreatedOn": "2026-10-05 05:10:00", "RelatedProspectId": "Q9", "mx_Custom_1": "LOW"}]  # no call -> dropped
    enr = [{"ProspectID": "L1", "OwnerId": "u2", "enrolled_at": "2026-10-06 06:00:00"},
           {"ProspectID": "M3", "OwnerId": "u2", "enrolled_at": "2026-10-05 06:00:00"},
           {"ProspectID": "Q1", "OwnerId": "u1", "enrolled_at": "2026-10-05 06:00:00"}]  # never called that day
    return {"meta": {"date": "2026-10-05", "d0": "2026-10-05T00:00:00+05:30", "cw_end": "2026-10-08T23:59:59+05:30"},
            "users": USERS, "calls": calls, "zips": zips, "enrollments": enr, "payments": []}


def test_scorecard_credit_and_ranking():
    A = analyse(run())
    t = A["teams"]["Team A"]
    assert A["totals"]["bots_excluded"] == 1
    assert (t["callers"], t["dials"], t["inbound"], t["real"], t["reached"]) == (3, 33, 1, 30, 30)
    assert t["credited"] == 2 and t["conv_pct"] == round(100 * 2 / 30, 1)
    assert t["same_day_owner"] == 2 and A["teams"]["Unassigned"]["callers"] == 1
    assert A["rank"] == ["Team A"]                        # Unassigned / Not a user are never ranked
    assert A["totals"]["enroll_window"] == 3 and A["totals"]["enroll_credited"] == 2
    assert A["totals"]["multi_group_callers"] == 1
    asha = next(p for p in A["people"] if p["name"] == "Asha K")
    assert asha["credited"] == 1 and asha["pitch"] == 100 and asha["probe"] == 0 and asha["obj"] is None
    assert A["totals"]["zip_dropped"] == 1
    checks = {c["check"]: c["ok"] for c in data_checks(run(), A)}
    failed = [k for k, ok in checks.items() if not ok]
    assert failed == ["Zipteams included, under 5% of notes dropped"]  # 1 of 2 notes dropped


def test_as_of_caps_the_conversion_window():
    from datetime import datetime
    from analytics.team_report import IST
    A = analyse(run(), datetime(2026, 10, 5, 23, 0, tzinfo=IST))
    assert A["totals"]["enroll_window"] == 2


def test_plan_tracker_and_html():
    A = analyse(run())
    P = plan_tracker(A)
    team = next(t for t in P["teams"] if t["team"] == "Team A")
    assert team["payment_step_zip_pct"] == 100 and team["missed_inbound_leads"] == 1
    assert team["called_back_same_day_pct"] == 100 and team["median_callback_min"] == 30
    html, nums = report_html(A, None, "Validated")
    assert "Monday 5 October 2026" in html and "Parameters v1.1." in html
    assert verdict_numbers_check(nums, scorecard_cells(A))["ok"]
    assert "Parameters v1.1" in tracker_html(P)


def test_markers():
    assert has_payment_step("please complete the payment today")
    assert not has_payment_step("we discussed the fee")
    rates = marker_rates(["the fee is 40,000 and the payment link is sent", "tell me about yourself"])
    assert rates["Price / fee / EMI"] == 50 and rates["Discovery questions"] == 50


def test_gate_catches_bad_data():
    r = run()
    r["calls"].append(dict(r["calls"][0]))                                  # duplicate activity
    r["calls"][0]["activity_id"] = r["calls"][-1]["activity_id"] = "dup"
    r["calls"].append(call("u1", "W1", "2026-10-06 09:00:00"))              # next IST day
    A = analyse(r)
    failed = {c["check"] for c in data_checks(r, A) if not c["ok"]}
    assert "No duplicate activity IDs" in failed and "Every call and note is inside the IST day" in failed


def test_spot_check_against_stage_history():
    A = analyse(run())
    hist = {"L1": [{"CreatedOn": "2026-10-06 06:00:00", "Data": [{"Key": "CurrentStage", "Value": "Course Enrolled"}]}],
            "M3": [{"CreatedOn": "2026-10-05 06:00:00", "Data": [{"Key": "CurrentStage", "Value": "Course Enrolled"}]}]}
    assert spot_check(A, lambda l: hist[l])["ok"]
    hist["M3"].insert(0, {"CreatedOn": "2026-09-01 06:00:00", "Data": [{"Key": "CurrentStage", "Value": "Course Enrolled"}]})
    assert not spot_check(A, lambda l: hist[l])["ok"]                       # enrolled before: not first-time
    assert not verdict_numbers_check(["99.9"], scorecard_cells(A))["ok"]
