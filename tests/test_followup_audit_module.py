from __future__ import annotations

import json

from analytics.discovery_audit_module import reading as dr
from analytics.followup_audit_module import find, reading, report

DAY = "2026-11-13"
FU, DCD = "Follow Up For Closure", "Discovery Call Done"
LEAD = {"ProspectID": "lead-0001-aaaa", "FirstName": "Test", "Phone": "+91-9876543210", "ProspectStage": FU}


def stage(created_utc: str, current: str) -> dict:
    return {"CreatedOn": created_utc, "Data": [{"Key": "CurrentStage", "Value": current}]}


def call(start_utc: str, status: str = "Answered", secs: int = 600, caller: str | None = "Caller One", **kw) -> dict:
    return {"activity_id": f"a-{start_utc}", "lead_id": LEAD["ProspectID"], "start_utc": start_utc, "status": status,
            "duration": secs, "caller": caller, "direction": "outbound", "lead_number": "+91-9876543210", **kw}


def test_window_runs_from_the_real_discovery_move_to_the_move_with_the_discovery_call_as_background():
    hist = [stage("2026-10-23 14:33:00", DCD), stage("2026-11-13 06:38:00", FU)]       # 23 Oct 20:03, 13 Nov 12:08 IST
    calls = [call("2026-10-23 13:38:00", secs=99, caller="Avani"), call("2026-10-23 14:14:00", secs=1091, caller="Avani"),
             call("2026-10-22 07:11:00", status="NotAnswered", secs=1),                    # day before: not background
             call("2026-10-28 05:08:00", secs=1319), call("2026-10-28 09:43:00", secs=10),  # short: not read
             call("2026-10-28 10:02:00", status="NotAnswered", secs=6),
             call("2026-11-13 06:18:00", secs=1046),                                       # 11:48 IST, ends after the move: counts
             call("2026-11-13 07:00:00", secs=900)]                                        # after the move: never read
    e = find.pick(LEAD, hist, calls, DAY)
    assert e["start_reason"] == "discovery" and e["window_start_utc"] == "2026-10-23 14:33:00"
    assert [c["duration"] for c in e["calls"]] == [1319, 1046]
    assert [c["duration"] for c in e["background"]] == [99, 1091]
    assert e["not_read"] == {"unanswered": 1, "short": 1} and not e.get("moved_without_call")
    assert find.pick(LEAD, hist, calls, "2026-11-12") is None


def test_a_lead_that_comes_back_into_the_stage_is_judged_from_its_previous_move():
    hist = [stage("2026-10-23 14:33:00", DCD), stage("2026-10-26 06:24:00", FU), stage("2026-10-29 05:08:00", "May buy later"),
            stage("2026-11-13 06:38:00", FU)]
    calls = [call("2026-10-23 14:14:00"), call("2026-10-26 05:20:00"), call("2026-10-28 05:08:00"), call("2026-11-13 06:18:00")]
    e = find.pick(LEAD, hist, calls, DAY)
    assert e["start_reason"] == "previous_follow_up" and [c["start_utc"][:10] for c in e["calls"]] == ["2026-10-28", "2026-11-13"]
    assert "previous move into Follow Up For Closure" in reading.start_text(e)


def test_a_discovery_move_with_no_conversation_before_it_is_ignored_and_the_window_stops_at_30_days():
    hist = [stage("2026-10-13 07:56:00", DCD), stage("2026-11-13 06:38:00", FU)]       # the bulk update: no call before it
    calls = [call("2026-10-12 05:00:00"), call("2026-10-14 05:00:00"), call("2026-11-10 05:00:00")]
    e = find.pick(LEAD, hist, calls, DAY)
    assert e["start_reason"] == "30_days" and e["window_start_utc"] == "2026-10-14 06:38:00"
    assert [c["start_utc"][:10] for c in e["calls"]] == ["2026-11-10"] and e["background"] == []
    assert "was not used as the start" in e["note"]
    # A real discovery move more than 30 days back: the window still stops at 30 days, the discovery call stays background.
    hist = [stage("2026-06-09 14:00:00", DCD), stage("2026-11-13 06:38:00", FU)]
    e = find.pick(LEAD, hist, [call("2026-06-09 13:30:00"), call("2026-11-10 05:00:00")], DAY)
    assert e["start_reason"] == "30_days" and len(e["background"]) == 1 and len(e["calls"]) == 1


def test_one_call_stored_as_two_activities_is_read_once():
    hist = [stage("2026-11-10 05:00:00", DCD), stage("2026-11-13 06:38:00", FU)]
    twin = dict(call("2026-11-10 04:30:00", user_id="u1"), activity_id="a-twin", recording_url="https://rec/1")
    calls = [call("2026-11-10 04:30:00", user_id="u1"), twin, call("2026-11-12 05:00:00", user_id="u1"),
             call("2026-11-12 05:00:00", user_id="u2", caller="Caller Two")]     # same time, another caller: two calls
    e = find.pick(LEAD, hist, calls, DAY)
    assert [c["activity_id"] for c in e["background"]] == ["a-twin"]           # the copy with the recording is kept
    assert len(e["calls"]) == 2


def test_no_real_conversation_in_the_window_means_moved_without_a_conversation(tmp_path):
    hist = [stage("2026-11-06 05:07:00", DCD), stage("2026-11-13 05:48:00", FU)]
    calls = [call("2026-11-06 04:50:00", secs=900, caller="Caller One"),                 # the discovery call
             call("2026-11-10 05:00:00", secs=43, caller="Caller Two"),                  # under 2 min
             call("2026-11-14 05:00:00", secs=500, caller="Caller Three")]               # after the move
    e = find.pick(LEAD, hist, calls, DAY)
    assert e["moved_without_call"] and e["caller_hint"] == "Caller Two"
    assert e["last_call_before"]["duration"] == 900 and e["first_call_after"]["duration"] == 500
    assert report.status(e, str(tmp_path))[0] == "MOVED_WITHOUT_CALL"
    assert "No conversation since the move to Discovery Call Done" in report.moved_text(e)[0]


def entry(transcript: str | None = "Hello this is Ravi. You can easily earn from like six dollars to 25 dollars an hour.") -> dict:
    hist = [stage("2026-11-10 05:00:00", DCD), stage("2026-11-13 06:38:00", FU)]
    calls = [call("2026-11-10 04:30:00", transcript="I'm facing a financial issue."), call("2026-11-12 05:00:00", transcript=transcript),
             call("2026-11-12 08:00:00", secs=200, transcript="Hi, you have reached the voicemail of")]
    return find.pick(LEAD, hist, calls, DAY)


def result(**kw) -> dict:
    sheet = {c: "Not discussed" for c in dr.READER_COLUMNS}
    sheet.update({"Caller Name": "Ravi", "Primary Pain Identified": "Facing a financial issue (background call)",
                  "IIT Compliance": "Not Mentioned", "Pitch Discipline": 'CRITICAL: Income projection ("you can easily earn from like six dollars to 25 dollars an hour")'})
    r = {"lead_key": "k", "not_a_conversation": ["12-11 13:30"],
         "subscores": {"interest": 2, "burning_problem": 1, "buying_signals": 1, "objections": 1.5, "commitment": 1,
                       "objection_raised": False, "open_concern": True},
         "subscore_reasons": {k: "r" for k in dr.SUBSCORES}, "burning_problem": "IDENTIFIED",
         "compliance_flags": 'CRITICAL: Income projection ("you can easily earn from like six dollars to 25 dollars an hour")',
         "sheet": sheet, "pdf": {**{f: "x" for f in dr.PDF_FIELDS}, "what_went_wrong": ["a"], "improvements": ["b"]},
         "tags": {"strengths": [], "gaps": ["income_claim"]}, "quality_note": ""}
    return {**r, **kw}


def test_reader_text_marks_background_and_scored_calls_and_the_custom_instructions_name_the_window():
    e = entry()
    text = reading.lead_text(e)
    assert text.index("BACKGROUND, NOT SCORED") < text.index("financial issue") < text.index("SCORED CALLS") < text.index("six dollars")
    assert "=== CALL 12-11 10:30 IST" in text and "up to the move to Follow Up For Closure on 13-11-2026 12:08 IST" in text
    ci = reading.pack_item(e)["custom_instructions"]
    assert "These are 2 follow-up calls" in ci and "The 1 call under BACKGROUND" in ci and "never score them" in ci and "next 48 hours count from the move" in ci
    assert reading.pack_item(e)["scored_call_labels"] == ["12-11 10:30", "12-11 13:30"]


def test_check_accepts_known_call_labels_and_scoring_skips_calls_that_were_not_conversations(tmp_path):
    e = entry()
    p = tmp_path / "k.json"
    p.write_text(json.dumps(result()))
    assert reading.check(str(p), e) == [] and reading.finished(str(p), reading.lead_text(e))
    assert [c["duration"] for c in reading.real_left(e, result())] == [600]
    bad = result(not_a_conversation=["01-01 00:00"])
    assert any("not_a_conversation" in x for x in reading.problems(bad, e))
    # Every real call was voicemail: the lead is not scored, it goes to "moved without a conversation".
    folder = tmp_path / "reading"
    (folder / "results").mkdir(parents=True)
    q = folder / "results" / f"{reading.lead_key(e)}.json"
    q.write_text(json.dumps(result(not_a_conversation=["12-11 10:30", "12-11 13:30"])))
    assert reading.check(str(q), e) == []
    st, why = report.status(e, str(folder))
    assert st == "MOVED_WITHOUT_CALL" and "voicemail" in why


def test_missing_transcript_on_a_real_call_needs_a_check_but_not_on_a_short_one(tmp_path):
    e = entry(transcript=None)
    assert report.status(e, str(tmp_path))[0] == "TRANSCRIPT_NOT_FOUND"
    e = entry()
    e["calls"].append(call("2026-11-12 09:00:00", secs=45, transcript=None))
    assert report.status(e, str(tmp_path))[0] == "PENDING_ANALYSIS"


def test_discovery_label_reads_the_discovery_audit_result_on_or_before_the_move(tmp_path):
    e = entry()
    d = tmp_path / "2026-11-10" / "reading" / "results"
    d.mkdir(parents=True)
    (d / f"2026-11-10_{LEAD['ProspectID'][:8]}.json").write_text(json.dumps(result()))
    later = tmp_path / "2026-11-20" / "reading" / "results"
    later.mkdir(parents=True)
    (later / f"2026-11-20_{LEAD['ProspectID'][:8]}.json").write_text(json.dumps(result(subscores={**result()["subscores"], "interest": 0})))
    assert report.discovery_label(e, str(tmp_path)) == "H (10-11-2026)"
    assert report.discovery_label({**e, "lead": {**LEAD, "ProspectID": "other-lead"}}, str(tmp_path)) == "Not in the discovery audit"


def test_callers_listed_in_order_from_the_call_records():
    e = entry()
    e["calls"][1]["caller"] = "Caller Two"
    assert report.callers(e) == "Caller One, Caller Two"


def test_review_prepare_uses_the_follow_up_text_and_warns_about_background(tmp_path):
    from analytics.followup_audit_module import review
    e = entry()
    folder = tmp_path / "reading"
    (folder / "results").mkdir(parents=True)
    p = folder / "results" / f"{reading.lead_key(e)}.json"
    p.write_text(json.dumps({**result(), "lead_key": reading.lead_key(e)}))
    assert reading.check(str(p), e) == []
    parts = review.prepare([e], str(folder))
    item = json.load(open(parts[0]))[0]
    assert item["transcript"] == reading.lead_text(e) and "BACKGROUND" in (folder / "review" / "REVIEW.md").read_text()
    (folder / "review" / "out_01.json").write_text(json.dumps([{"lead_key": item["lead_key"], "verdict": "keep",
                                                                "compliance_flags": "", "pitch_discipline": "", "reason": "ok"}]))
    assert review.apply([e], str(folder)) == {"kept": 1, "changed": 0, "failed": []}


def test_missing_transcripts_are_carried_into_the_next_two_runs(tmp_path, monkeypatch):
    from analytics.followup_audit_module import __main__ as cli
    monkeypatch.setattr(cli, "BASE", str(tmp_path))
    e = entry(transcript=None)
    (tmp_path / DAY).mkdir()
    (tmp_path / DAY / "leads.json").write_text(json.dumps([e]))
    nxt = cli.retry_earlier("2026-11-14")
    assert [x["tries"] for x in nxt] == [2] and "Carried over" in nxt[0]["note"]


def test_an_old_call_without_a_recording_is_scored_around_but_a_recent_one_is_waited_for(tmp_path):
    e = entry()
    e["calls"].insert(0, call("2026-11-01 05:00:00", secs=300, transcript=None))   # 12 days before the move
    assert report.waiting(e) == [] and report.status(e, str(tmp_path))[0] == "PENDING_ANALYSIS"
    e["calls"].append(call("2026-11-13 05:00:00", secs=300, transcript=None))      # the morning of the move
    assert len(report.waiting(e)) == 1 and report.status(e, str(tmp_path))[0] == "TRANSCRIPT_NOT_FOUND"


def test_every_tab_with_a_lead_shows_its_number_so_a_search_finds_it(tmp_path, monkeypatch):
    import openpyxl
    monkeypatch.setattr(report, "print_pdf", lambda doc, html, pdf: open(pdf, "wb").write(b"%PDF-1.4"))
    hist = [stage("2026-11-06 05:07:00", DCD), stage("2026-11-13 05:48:00", FU)]
    moved = find.pick(LEAD, hist, [call("2026-11-06 04:50:00", secs=900), call("2026-11-14 05:00:00", secs=500)], DAY)
    late = entry(transcript=None)
    late["lead"] = {**LEAD, "ProspectID": "lead-0002-bbbb", "Phone": None, "Mobile": "9123456780"}
    outcomes = [{"lead": "Test", "mobile": "9876543210", "caller": "Ravi", "label": "M", "moved": "06-11-2026 10:37", "enrolled": "No"}]
    out = report.build(DAY, [moved, late], str(tmp_path / "f"), str(tmp_path / "out"), str(tmp_path / "history.jsonl"),
                       ddir=str(tmp_path / "d"), outcomes=outcomes)
    wb = openpyxl.load_workbook(out["xlsx"])
    for tab, number in (("Moved without a conversation", "9876543210"), ("Needs a Check", "9123456780"),
                        ("Calls", "9123456780"), ("7-day outcome", "9876543210")):
        ws = wb[tab]
        col = [c.value for c in ws[1]].index("Mobile Number")
        assert number in [r[col] for r in ws.iter_rows(min_row=2, values_only=True)], tab
