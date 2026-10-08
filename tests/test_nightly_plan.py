import json

from analytics.nightly_plan import build_plan, load_chances, rule_tier
from analytics.call_plan import write_workbook
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
    assert [f["lead_id"] for f in p["first"]] == ["new"]
    assert [r["lead_id"] for r in p["revive"]] == ["quiet", "rang"]  # "rang" ends up in tier M below
    rows = write_workbook(p, str(tmp_path / "call_plan_Team_Test_2026-10-09.xlsx"))["Asha K"]
    assert [(r["lead_id"], r["tier"]) for r in rows] == [("rang", "M"), ("hot", "A"), ("new", "F"), ("quiet", "R")]


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
