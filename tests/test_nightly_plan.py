import json

from analytics.nightly_plan import best_time, build_plan, load_chances, rule_tier
from analytics.call_plan import build_rows, write_workbook
from analytics.tier_outcomes import plan_day, tier_outcomes


def cand(**kw):
    r = {"negative_signals": [], "stage": "Call Back Later", "days_since_last_conversation": 2.0, "buying_signals": [],
         "best_zip_intent": None, "inbound_calls": 0}
    r.update(kw)
    return r


def test_rule_tiers():
    assert rule_tier(cand(buying_signals=["payment"])) == "A"
    assert rule_tier(cand(buying_signals=["payment"], days_since_last_conversation=10)) == "B"
    assert rule_tier(cand(best_zip_intent="MODERATE")) == "B"
    assert rule_tier(cand()) == "C"
    assert rule_tier(cand(negative_signals=["no_budget"], buying_signals=["payment"])) is None
    assert rule_tier(cand(stage="Not Interested")) is None
    assert rule_tier(cand(days_since_last_conversation=None)) is None  # missed-call-only leads go to tier M


def test_best_time_is_the_slot_the_lead_answers_in():
    assert best_time([]) == ""
    assert best_time([11, 19, 20]) == "18:00–21:00"
    assert best_time([19, 11]) == "10:00–12:00"  # tie: the latest answer's slot


def lead(lid, owner, stage="Call Back Later", created="2026-09-01 05:00:00", **kw):
    return {"ProspectID": lid, "OwnerIdName": owner, "ProspectStage": stage, "CreatedOn": created,
            "ModifiedOn": "2026-10-07 05:00:00", "FirstName": "Lead", "LastName": lid, "Phone": "9", **kw}


def call(lid, t, dur=300, status="Answered", direction="outbound"):
    return {"lead_id": lid, "start_utc": t, "duration": dur, "status": status, "direction": direction, "caller": "x"}


SNAP = {
    "group": "Team Test", "start": "2026-09-23", "end": "2026-10-08", "fetched_at": "2026-10-08T20:00:00+00:00",
    "users": [{"ID": "u1", "FirstName": "Asha", "LastName": "K"}, {"ID": "u2", "FirstName": "Lead", "LastName": "Er"}],
    "leads": [lead("hot", "Asha K", "Follow Up For Closure"), lead("new", "Asha K", "New Lead", created="2026-10-07 05:00:00"),
              lead("quiet", "Asha K", "May buy later"), lead("rang", "Asha K"), lead("gone", "Asha K", "Course Enrolled")],
    "calls": [call("hot", "2026-10-07 06:00:00"), call("rang", "2026-10-08 06:00:00", 0, "NotAnswered", "inbound"),
              call("gone", "2026-10-06 06:00:00")],
    "zip_activities": [],
}


def test_build_plan_for_any_team_and_day(tmp_path):
    p = build_plan(SNAP, "2026-10-09", leaders=["Lead Er"], chances={"A": 31})
    assert p["team"] == "Team Test" and p["date_label"] == "Fri 9 Oct 2026"
    assert p["owner_order"] == ["Asha K", "Lead Er"] and p["targets"] == {"Lead Er": 0}
    assert [(t["lead_id"], t["tier"], t["prob_3d"]) for t in p["tiered"]] == [("hot", "A", 31)]
    assert p["tiered"][0]["best_time"] == "10:00–12:00"  # answered at 11:30 IST
    assert [f["lead_id"] for f in p["first"]] == ["new"]
    assert [r["lead_id"] for r in p["revive"]] == ["quiet", "rang"]  # "rang" ends up in tier M below
    rows = write_workbook(p, str(tmp_path / "call_plan_Team_Test_2026-10-09.xlsx"))["Asha K"]
    assert [(r["lead_id"], r["tier"]) for r in rows] == [("rang", "M"), ("hot", "A"), ("new", "F"), ("quiet", "R")]


def test_since_widens_the_window_but_revive_still_means_15_quiet_days():
    snap = {**SNAP, "start": "2026-09-01",
            "leads": SNAP["leads"] + [lead("sept", "Asha K", "New Lead", created="2026-09-10 05:00:00"),
                                      lead("talked", "Asha K", "Call Back Later", created="2026-08-01 05:00:00")],
            "calls": SNAP["calls"] + [call("talked", "2026-09-12 06:00:00")]}
    narrow = build_plan(snap, "2026-10-09")
    assert "sept" not in {f["lead_id"] for f in narrow["first"]} and "talked" not in {t["lead_id"] for t in narrow["tiered"]}
    wide = build_plan(snap, "2026-10-09", since="2026-09-05")
    assert wide["since"] == "2026-09-05"
    assert {f["lead_id"] for f in wide["first"]} == {"new", "sept"}
    assert ("talked", "C") in {(t["lead_id"], t["tier"]) for t in wide["tiered"]}
    assert {r["lead_id"] for r in wide["revive"]} >= {"quiet", "talked"}  # 27 days quiet: revive candidate too


def test_missed_callback_is_flagged_and_new_leads_least_tried_first():
    snap = {**SNAP, "leads": [lead("hot", "Asha K", "Follow Up For Closure", mx_Next_follow_up_date="2026-10-08 04:00:00"),
                              lead("stale", "Asha K", "Follow Up For Closure", mx_Next_follow_up_date="2026-09-20 04:00:00"),
                              lead("n1", "Asha K", "New Lead", created="2026-10-07 05:00:00"),
                              lead("n2", "Asha K", "New Lead", created="2026-10-06 05:00:00")],
            "calls": [call("hot", "2026-10-07 06:00:00"), call("stale", "2026-10-07 06:00:00"),
                      call("n1", "2026-10-07 07:00:00", 0, "NotAnswered"), call("n1", "2026-10-07 09:00:00", 0, "NotAnswered")]}
    p = build_plan(snap, "2026-10-09")
    assert {t["lead_id"]: t["callback_missed"] for t in p["tiered"]} == {"hot": True, "stale": False}
    assert [r["lead_id"] for r in build_rows(p)["Asha K"] if r["tier"] == "F"] == ["n2", "n1"]  # n1 dialled twice


def test_no_inbound_course_filter_and_discovery_notes():
    snap = {**SNAP, "leads": [lead("hot", "Asha K", "Follow Up For Closure", mx_Enquired_Course="US Accounting Bootcamp",
                                   mx_Conversation_Summary="Pune, B.Com, 3 years in AP, uses Tally, wants US clients"),
                              lead("rang", "Asha K", mx_Enquired_Course="US Accounting Bootcamp"),
                              lead("law", "Asha K", "Follow Up For Closure", mx_Enquired_Course="Contract Drafting"),
                              lead("bot", "Asha K", "Discovery Call Done", created="2026-10-06 05:00:00", mx_Conversation_Summary="cnp"),
                              lead("new", "Asha K", "New Lead", created="2026-10-07 05:00:00")],
            "calls": [call("hot", "2026-10-07 06:00:00"), call("law", "2026-10-07 06:00:00"),
                      call("rang", "2026-10-08 06:00:00", 0, "NotAnswered", "inbound")]}
    bot = {"bot": [{"start_ist": "2026-10-05T11:00:00", "campaign": "U.SAcc_12thSep", "secs": 300, "outcome": "Full Conversion",
                    "summary": "CA Inter, 2 years in audit, wants remote US work."},
                   {"start_ist": "2026-09-01T11:00:00", "campaign": "Content_Writing", "secs": 300, "outcome": "x", "summary": "y"}]}
    p = build_plan(snap, "2026-10-09", inbound=False, course="account", bot_calls=bot, bot_campaign="acc")
    rows = build_rows(p)["Asha K"]
    assert [(r["lead_id"], r["tier"]) for r in rows] == [("hot", "A"), ("bot", "F"), ("new", "F"), ("rang", "R")]
    assert "Caller's notes: Pune" in rows[0]["discovery"]
    assert rows[1]["discovery"].startswith("Bot, 5 Oct (Full Conversion, 5 min): CA Inter")
    assert "bot did discovery on 5 Oct" in rows[1]["why"] and "Discovery is done" in rows[1]["next_action"]
    assert rows[2]["discovery"] == "" and not any(r["missed_unreturned"] for r in rows)


def test_tier_outcomes_measures_each_tier_and_needs_enough_leads(tmp_path):
    path = tmp_path / "call_plan_Team_Test_2026-10-09.json"
    plan = {"Asha K": [{"lead_id": "a1", "tier": "A", "prob_3d": 35}, {"lead_id": "a2", "tier": "A", "prob_3d": 35},
                       {"lead_id": "b1", "tier": "B", "prob_3d": 15}, {"lead_id": "old", "tier": "B", "prob_3d": 15}]}
    path.write_text(json.dumps(plan))
    after = {"fetched_at": "2026-10-13T00:00:00+00:00", "leads": [
        {"ProspectID": "a1", "mx_Enrollment_date": "2026-10-10 06:00:00"},
        {"ProspectID": "a2", "mx_Enrollment_date": "2026-10-20 06:00:00"},  # after the 3-day window
        {"ProspectID": "b1"}, {"ProspectID": "old", "mx_Enrollment_date": "2026-10-01 06:00:00"}]}
    p, day = plan_day(str(path))
    r = tier_outcomes([(json.load(open(p)), day)], after, min_leads=2)
    assert r["tiers"]["A"] == {"leads": 2, "enrolled": 1, "actual_%": 50.0, "predicted_%": 35.0, "use_%": 50.0}
    assert r["tiers"]["B"]["leads"] == 1 and r["tiers"]["B"]["use_%"] is None
    assert r["left_out"]["enrolled_before_plan_day"] == 1
    out = tmp_path / "tier_chances.json"
    out.write_text(json.dumps(r))
    assert load_chances(str(out)) == {"A": 50.0}
