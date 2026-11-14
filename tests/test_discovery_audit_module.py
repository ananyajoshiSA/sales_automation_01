from __future__ import annotations

import json

from analytics.discovery_audit_module import find, reading, report, transcripts

DAY = "2026-11-13"


def stage(created_utc: str, current: str, by: str = "System") -> dict:
    return {"CreatedOn": created_utc, "Data": [{"Key": "PreviousStage", "Value": "Call Not Picking Up"},
                                               {"Key": "CurrentStage", "Value": current}, {"Key": "CreatedBy", "Value": by}]}


def call(lead: str, start_utc: str, status: str = "Answered", secs: int = 600, caller: str | None = "Caller One", **kw) -> dict:
    return {"activity_id": f"a-{start_utc}", "lead_id": lead, "start_utc": start_utc, "status": status, "duration": secs,
            "caller": caller, "direction": "outbound", "lead_number": "+91-9876543210", **kw}


LEAD = {"ProspectID": "lead-0001-aaaa", "FirstName": "Test", "Phone": "+91-9876543210", "ProspectStage": "Discovery Call Done"}


def test_changed_on_uses_the_ist_day_of_the_move_into_the_stage():
    hist = [stage("2026-11-12 18:40:00", "Discovery Call Done"),   # 13 Nov 00:10 IST
            stage("2026-11-04 04:42:00", "Call Not Picking Up")]
    assert find.changed_on(hist, DAY).strftime("%H:%M") == "18:40"
    assert find.changed_on(hist, "2026-11-12") is None
    assert find.changed_on([stage("2026-11-13 09:00:00", "Roadmap Done")], DAY) is None


def test_pick_takes_every_call_of_the_day_before_and_after_the_change():
    hist = [stage("2026-11-13 09:43:00", "Discovery Call Done")]          # 15:13 IST
    calls = [call(LEAD["ProspectID"], "2026-11-13 08:30:52", secs=48),      # 14:00 IST, short attempt
             call(LEAD["ProspectID"], "2026-11-13 12:48:19", secs=789),     # 18:18 IST, after the change
             call(LEAD["ProspectID"], "2026-11-12 10:00:00"),               # previous day: not used
             call("someone-else", "2026-11-13 09:00:00")]
    e = find.pick(LEAD, hist, calls, DAY)
    assert [c["duration"] for c in e["calls"]] == [48, 789]
    assert e["calls_day"] == DAY and e["note"] == ""
    assert find.pick(LEAD, [], calls, DAY) is None


def test_pick_falls_back_to_the_previous_day_when_the_stage_was_set_without_a_conversation():
    hist = [stage("2026-11-13 04:00:00", "Discovery Call Done")]
    calls = [call(LEAD["ProspectID"], "2026-11-12 11:00:00", secs=700), call(LEAD["ProspectID"], "2026-11-13 03:00:00", secs=4)]
    e = find.pick(LEAD, hist, calls, DAY)
    assert e["calls_day"] == "2026-11-12" and len(e["calls"]) == 1 and "2026-11-12" in e["note"]


def test_flatten_lead_call_reads_the_fields_under_activity_fields():
    a = {"Id": "x1", "CreatedOn": "2026-11-13 09:28:26", "RelatedProspectId": "L",
         "ActivityFields": {"ActivityEvent_Note": "Caller{=}Rounak{next}Duration{=}891{next}Status{=}Answered{next}"
                                                  'SourceData{=}{"DestinationNumber":"+91-9000000000"}{next}',
                            "CreatedOn": "11/13/2026 9:28:26 AM"}}
    c = find.flatten_lead_call(a, 22)
    assert (c["caller"], c["duration"], c["status"], c["start_utc"]) == ("Rounak", 891, "Answered", "2026-11-13 09:28:26")
    assert c["lead_number"] == "+91-9000000000" and c["activity_id"] == "x1"


def subs(**kw) -> dict:
    s = {"interest": 2, "burning_problem": 1, "buying_signals": 1, "objections": 1.5, "commitment": 1,
         "objection_raised": False, "open_concern": True}
    return {**s, **kw}


def test_scores_relaxed_strict_and_pdf_band():
    rel, strict = reading.totals(subs())
    assert (rel, strict) == (6.5, 5.75)
    assert reading.relaxed_label(rel) == "H" and reading.strict_label(strict) == "M" and reading.pdf_band(strict) == "WARM"
    assert reading.totals(subs(objection_raised=True, objections=0.75)) == (5.75, 5.75)
    assert reading.relaxed_label(3.9) == "L" and reading.strict_label(6.9) == "M" and reading.pdf_band(7.0) == "HOT"


TRANSCRIPT = "Hello this is Ravi. You can easily earn from like six dollars to 25 dollars an hour. I'm facing a financial issue."


def good_result() -> dict:
    sheet = {c: "Not discussed" for c in reading.READER_COLUMNS}
    sheet.update({"Caller Name": "Ravi", "Primary Pain Identified": "Jobless and facing a financial issue",
                  "IIT Compliance": "Not Mentioned", "Pitch Discipline": 'CRITICAL: Income projection ("you can easily earn from like six dollars to 25 dollars an hour")',
                  "Red Flags — Missed Discovery Signals": 'Lead said "i\'m facing a financial issue" | b'})
    return {"lead_key": "k", "subscores": subs(), "subscore_reasons": {k: "r" for k in reading.SUBSCORES},
            "burning_problem": "IDENTIFIED",
            "compliance_flags": 'CRITICAL: Income projection ("you can easily earn from like six dollars to 25 dollars an hour")',
            "sheet": sheet, "pdf": {**{f: "x" for f in reading.PDF_FIELDS}, "what_went_wrong": ["a"], "improvements": ["b"]},
            "tags": {"strengths": ["dated_next_step"], "gaps": ["income_claim"]}, "quality_note": ""}


def test_a_complete_result_passes_and_is_marked_ok(tmp_path):
    p = tmp_path / "k.json"
    p.write_text(json.dumps(good_result()))
    assert reading.check(str(p), TRANSCRIPT) == []
    assert reading.finished(str(p))
    p.write_text(json.dumps({**good_result(), "quality_note": "edited"}))
    assert not reading.finished(str(p))


def test_checker_rejects_invented_quotes_inconsistency_and_bad_values():
    r = good_result()
    r["sheet"]["Closer Briefing"] = 'Lead said "I will pay tomorrow"'
    r["burning_problem"] = "NOT IDENTIFIED"
    r["sheet"]["IIT Compliance"] = "Maybe"
    r["subscores"]["commitment"] = 2
    r["sheet"]["Coaching Action (Caller-Specific)"] = "a | b | c | d"
    out = " ".join(reading.problems(r, TRANSCRIPT))
    for bit in ("I will pay tomorrow", "Not identified", "IIT Compliance", "commitment", "at most 3"):
        assert bit in out


def test_flag_quote_must_be_in_double_quotes():
    r = {**good_result(), "compliance_flags": "CRITICAL: Income projection (you can easily earn from like six dollars to 25 dollars an hour)"}
    assert any("compliance_flags" in p for p in reading.problems(r, TRANSCRIPT))


def test_flag_must_also_appear_in_pitch_discipline():
    r = good_result()
    r["sheet"]["Pitch Discipline"] = "No prohibited claim evident."
    assert any("Pitch Discipline" in p for p in reading.problems(r, TRANSCRIPT))


def test_callers_come_from_the_call_records_never_the_stage_change():
    e = {"lead": LEAD, "calls": [call("L", "2026-11-13 08:00:00", caller="Caller Two"), call("L", "2026-11-13 09:00:00", status="NotAnswered", caller="Other")]}
    assert report.callers(e) == "Caller Two"
    e = {"lead": LEAD, "calls": [call("L", "2026-11-13 08:00:00", caller=None, user_id="u-1")]}
    assert report.callers(e) == "Unverified (user id u-1)"


def test_status_and_mobile(tmp_path):
    e = {"lead": LEAD, "calls_day": DAY, "calls": [call(LEAD["ProspectID"], "2026-11-13 08:00:00", secs=12)]}
    assert report.status(e, str(tmp_path))[0] == "NOT_AUDITABLE"
    e["calls"] = [call(LEAD["ProspectID"], "2026-11-13 08:00:00", transcript=None)]
    assert report.status(e, str(tmp_path))[0] == "TRANSCRIPT_NOT_FOUND"
    e["calls"][0]["transcript"] = "text"
    assert report.status(e, str(tmp_path))[0] == "PENDING_ANALYSIS"
    assert report.mobile(e) == "9876543210"


def test_transcript_numbers_include_each_calls_number_and_the_leads_phones():
    e = {"lead": {**LEAD, "Mobile": "+91-9111111111"},
         "calls": [call("L", "2026-11-13 08:00:00", lead_number="+91-9222222222"), call("L", "2026-11-13 09:00:00", lead_number=None)]}
    n = transcripts.normalize_phone
    assert transcripts.numbers_of(e) == [n("+91-9222222222"), n("+91-9876543210"), n("+91-9111111111")]
    lc = transcripts.ls_calls(e)
    assert lc[1]["lead_number"] == n("+91-9222222222") and lc[0]["t"].year == 2026


def test_review_splits_into_parts_numbers_on_and_applies_each_verdict_once(tmp_path):
    from analytics.discovery_audit_module import review
    folder = tmp_path / "reading"
    (folder / "results").mkdir(parents=True)
    entries = []
    for i in range(review.PER_PART + 1):
        e = {"lead": {**LEAD, "ProspectID": f"lead{i:04d}-x"}, "calls_day": DAY,
             "calls": [call("L", "2026-11-13 08:00:00", transcript=TRANSCRIPT)]}
        entries.append(e)
        p = folder / "results" / f"{reading.lead_key(e)}.json"
        p.write_text(json.dumps({**good_result(), "lead_key": reading.lead_key(e)}))
        assert reading.check(str(p), reading.day_text(e)) == []
    parts = review.prepare(entries, str(folder))
    assert [p.rsplit("/", 1)[1] for p in parts] == ["in_01.json", "in_02.json"]
    keys = [x["lead_key"] for p in parts for x in json.load(open(p))]
    out = [{"lead_key": k, "verdict": "keep", "compliance_flags": "", "pitch_discipline": "", "reason": "ok"} for k in keys]
    out[0] = {"lead_key": keys[0], "verdict": "change", "compliance_flags": "none",
              "pitch_discipline": "No prohibited claim evident.", "reason": "figure was the lead's"}
    (folder / "review" / "out_01.json").write_text(json.dumps(out[:review.PER_PART]))
    (folder / "review" / "out_02.json").write_text(json.dumps(out[review.PER_PART:]))
    done = review.apply(entries, str(folder))
    assert (done["kept"], done["changed"]) == (review.PER_PART, 1), done["failed"]
    r = json.load(open(folder / "results" / f"{keys[0]}.json"))
    assert r["compliance_flags"] == "NONE" and r["review"]["before"].startswith("CRITICAL")
    assert review.apply(entries, str(folder)) == {"kept": 0, "changed": 0, "failed": []}
    assert review.prepare(entries, str(folder)) == []
