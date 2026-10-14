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


def test_reports_render_for_both_teams_and_the_community_team(tmp_path):
    def v(lid, team, outcome, month="2026-08", caller="Asha Rao"):
        return {"lead_id": lid, "team": team, "caller": caller, "outcome": outcome, "month": month, "booked_ist": f"{month}-03 12:00",
                "booked_day": "Mon", "stage": "Call Back Later", "allocated_by": "Pratik Sarkar", "mature": True,
                "closure_stage": False, "hrs_handover_to_team_dial": 2.0, "team_dialled_24h": True, "connected_72h": True,
                "real_convs": 1, "days_to_collect": 2.0 if outcome == "collected" else None, "inbound_unreturned_days": 0,
                "max_dials_one_day": 1, "first_talk_min": 4.0}
    reading = {"what_happened": "w", "why_not_paid": "no card", "community_note": "Expected Rs 3,000 a month",
               "primary_cause": "Community: booked without understanding the balance or terms", "contributing": [],
               "savable": "Maybe", "expect_payment": "In Nov or Dec", "counselling": "Good"}
    data = {"as_of_ist": "2026-10-11 11:14", "stats_from": "2026-05-01", "read_from": "2026-08-01",
            "views": [v("a", "Elite Changemakers", "open"), v("b", "Elite Changemakers", "collected"),
                      v("c", "Team Puja Malik (DSV+Women AI)", "lost", caller="Ravi Das")],
            "readings": {"a": {**reading, "cause_owner": "Community team"},
                         "c": {**reading, "primary_cause": "Caller: not called or counselled in time", "cause_owner": "Caller"}}}
    names = {"a": {"name": "Malti Jain Jain"}}
    page = cr.team_html("Elite Changemakers", data, names, {"actions": [{"title": "Return calls.", "text": "Same day."}]})
    assert "for Mayur Sachdeva" in page and "Malti Jain<" in page and "Return calls." in page
    assert "Community team: 1 leads" in page and "50.0%" in page
    page = cr.community_html(data, names, {"summary": [{"title": "Fit.", "text": "Check payment route at booking."}]})
    assert "For the community team" in page and "Expected Rs 3,000 a month" in page and "Pratik Sarkar" in page


def test_full_round_gives_every_call_and_rejects_a_skipped_one(tmp_path):
    booked = utc("2026-09-10 05:00:00")
    calls = [{"start_api": "2026-08-01T05:00:00+00:00", "transcript": "old sales call", "duration": 600},
             {"start_api": "2026-09-09T05:00:00+00:00", "transcript": "booking call " + "x" * 30000, "duration": 1800},
             {"start_api": "2026-09-12T05:00:00+00:00", "transcript": "first collection call", "duration": 300},
             {"start_api": "2026-09-20T05:00:00+00:00", "transcript": "", "duration": 200}]
    full = cr.full_transcripts(calls, booked)       # the last call before the booking window is kept and marked
    assert [t["n"] for t in full] == [1, 2, 3] and [t["before_handover"] for t in full] == [True, False, False]
    assert full[1]["text"].startswith("booking call") and len(full[1]["text"]) > 30000 and full[2]["date"] == "12 Sep 2026"
    calls[1]["start_api"] = "2026-09-01T05:00:00+00:00"           # booking call more than 2 days before: kept, marked
    full = cr.full_transcripts(calls, booked)
    assert [t["before_handover"] for t in full] == [True, False] and "old sales call" not in [t["text"] for t in full]

    assert len(full) == 2
    items = [{"lead_id": "a", "flags": [], "dossier": "d", "transcripts": full}]
    folder = cr.write_round(items, str(tmp_path), "2026-10-14 10:00", full=True)
    assert folder.endswith("-full") and "calls_read" in open(f"{folder}/INSTRUCTIONS.md").read()
    base = {"lead_id": "a", "what_happened": "w", "why_not_paid": "y", "community_note": "none",
            "primary_cause": "Lead: cannot afford it now", "contributing": [], "savable": "No",
            "expect_payment": "Not expected", "counselling": "Good"}
    read = tmp_path / "read.jsonl"
    read.write_text(json.dumps({**base, "calls_read": [{"n": 2, "gist": "first collection call"}]}) + "\n")
    assert cr.check_reading(str(read), f"{folder}/batch_01.jsonl") == ["line 1: calls_read is missing call(s) [1]"]
    read.write_text(json.dumps({**base, "calls_read": [{"n": 1, "gist": "booking"}, {"n": 2, "gist": "first call"}]}) + "\n")
    assert cr.check_reading(str(read), f"{folder}/batch_01.jsonl") == []


def test_full_batches_are_sized_by_text():
    item = lambda lid, size: {"lead_id": lid, "dossier": "", "transcripts": [{"text": "x" * size}]}  # noqa: E731
    out = cr.batches_by_size([item("a", 150_000), item("b", 120_000), item("c", 40_000), item("d", 50_000)], full=True)
    assert [[i["lead_id"] for i in b] for b in out] == [["a"], ["b", "d"], ["c"]]   # largest first
