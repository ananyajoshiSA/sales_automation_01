import json

from analytics import community_review as cr
from integrations.timeutil import utc


def stage(t, cur, by="Pratik Sarkar", prev="Roadmap Done"):
    return {"EventCode": 3002, "CreatedOn": t, "Data": [{"Key": "PreviousStage", "Value": prev},
            {"Key": "CurrentStage", "Value": cur}, {"Key": "CreatedBy", "Value": by}]}


def test_allocator_and_closure_stage():
    booked = utc("2026-09-01 05:00:00")
    acts = [stage("2026-08-01 05:00:00", "Booking fees received", by="Someone"), stage("2026-09-01 05:00:00", "Booking fees received"),
            stage("2026-09-03 05:00:00", "Loan pending", by="Asha Rao")]
    assert cr.allocated_by(acts, booked) == "Pratik Sarkar"
    assert cr.reached_closure(acts, booked) and not cr.reached_closure(acts[:2], booked)


def view(outcome, mature=True, closure=False, **kw):
    base = {"outcome": outcome, "mature": mature, "closure_stage": closure, "hrs_handover_to_team_dial": 2.0,
            "team_dialled_24h": True, "connected_72h": True, "real_convs": 1, "days_to_collect": 3.0 if outcome == "collected" else None,
            "inbound_unreturned_days": 0, "max_dials_one_day": 2, "first_talk_min": 5.0}
    return {**base, **kw}


def test_funnel_compares_on_mature_leads():
    f = cr.funnel([view("collected", closure=True), view("lost"), view("open", mature=False), view("open", closure=True)])
    assert (f["allocated"], f["mature"], f["collected"], f["open"]) == (4, 3, 1, 2)
    assert f["collected_%_mature"] == 33.3 and f["collected_%_of_closure_mature"] == 50.0 and f["closure_stage_%"] == 50.0


def test_check_reading(tmp_path):
    batch = tmp_path / "batch_01.jsonl"
    batch.write_text(json.dumps({"lead_id": "a"}) + "\n")
    good = {"lead_id": "a", "what_happened": "w", "why_not_paid": "y", "community_note": "none",
            "primary_cause": "Lead: cannot afford it now", "contributing": ["Caller: not called or counselled in time"],
            "savable": "Maybe", "expect_payment": "In Nov or Dec", "counselling": "Partial"}
    read = tmp_path / "read_01.jsonl"
    read.write_text(json.dumps({**good, "primary_cause": "Money"}) + "\n")
    assert cr.check_reading(str(read), str(batch)) == ["line 1: primary_cause 'Money' not in the list"]
    read.write_text(json.dumps(good) + "\n")
    assert cr.check_reading(str(read), str(batch)) == []
    folder = tmp_path / "reading" / "r1"
    folder.mkdir(parents=True)
    (folder / "batch_01.jsonl").write_text(batch.read_text())
    (folder / "read_01.jsonl").write_text(read.read_text())
    assert cr.load_readings(str(tmp_path / "reading"))["a"]["cause_owner"] == "Lead"
