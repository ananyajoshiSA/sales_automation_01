import json
import os
from datetime import date, datetime, timezone

import pytest
from openpyxl import load_workbook

from analytics.collection_daily import facts, morning
from analytics.collection_daily.__main__ import judged_on
from integrations.timeutil import IST


def ist(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)


def call(t, answered=True, dur=200, out=True):
    return {"t": ist(t), "answered": answered, "dur": dur, "out": out, "team": True}


def form(t, due):
    return {"t": ist(t), "due": ist(due) if due else None, "status": "", "note": ""}


def test_deadline_is_end_of_calling_day_two_days_on():
    assert facts.deadline_of(ist("2026-11-16 10:00")) == ist("2026-11-18 20:00")
    assert facts.deadline_of(ist("2026-11-16 23:30")) == ist("2026-11-18 20:00")


def test_allocation_is_last_owner_change_to_the_caller():
    lead = {"caller": "Ankita", "assigned": ist("2026-11-15 09:00")}
    utc = lambda s: ist(s).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    acts = [{"EventCode": 3001, "CreatedOn": utc("2026-11-15 09:00"), "Data": [{"Key": "CurrentOwner", "Value": "Ravi"}]},
            {"EventCode": 3001, "CreatedOn": utc("2026-11-16 12:00"), "Data": [{"Key": "CurrentOwner", "Value": "Ankita"}]}]
    start = ist("2026-11-14 00:00")
    assert facts.allocation(lead, acts, start) == ist("2026-11-16 12:00")
    assert facts.allocation(lead, [], start) == lead["assigned"]


def test_follow_ups_only_after_an_answered_call_and_kept_within_two_hours():
    lead = {"calls": [call("2026-11-16 10:00", dur=60), call("2026-11-17 15:10"), call("2026-11-16 18:00", answered=False)],
            "forms": [form("2026-11-16 10:05", "2026-11-17 15:00"),      # after a pick-up, kept by the 15:10 dial
                      form("2026-11-16 10:06", "2026-11-16 10:10"),      # "call back now" default: not a follow-up
                      form("2026-11-16 18:01", "2026-11-17 11:00"),      # after an unanswered dial: not a follow-up
                      form("2026-11-16 10:07", "2026-11-17 19:00")]}     # due after the cut: not due yet
    fu = facts.follow_ups(lead, ist("2026-11-17 18:00"))
    assert [(f["due"], f["kept"]) for f in fu] == [(ist("2026-11-17 15:00"), True), (ist("2026-11-17 19:00"), None)]
    fu = facts.follow_ups({**lead, "calls": lead["calls"][:1]}, ist("2026-11-17 18:00"))
    assert fu[0]["kept"] is False


def open_lead(lid, caller="Ankita", real=1, **kw):
    base = {"lead_id": lid, "side": "Mayur", "phone": "9" + lid.rjust(9, "0"), "name": "", "caller": caller, "tl": "Sonal", "ad": "Sahil",
            "bootcamp": "US Accounting", "day": 1, "enrolled": False, "dropped": False, "real_talks": real,
            "deadline": ist("2026-11-16 20:00"), "due48": real == 0, "met48": real > 0, "first_pickup": None,
            "next_follow_up": None, "follow_ups": [], "unreturned_calls": [], "last_real": ist("2026-11-15 12:00") if real else None,
            "dials": 4, "dial_days": 2}
    return base | kw


def test_order_of_groups_and_one_place_per_lead():
    day = date(2026, 11, 18)
    leads = [open_lead("1", real=2), open_lead("2", real=0), open_lead("3", real=1, next_follow_up=ist("2026-11-18 12:00")),
             open_lead("4", real=1), open_lead("5", real=1), open_lead("6", real=1), open_lead("7", real=1, enrolled=True),
             open_lead("8", real=1, unreturned_calls=[ist("2026-11-17 19:00")])]
    judged = {"1": {"status": "cold"}, "2": {"status": "callback"}, "3": {"status": "hot"}, "4": {"status": "counsel"},
              "5": {"status": "hot", "waiting_until": "2026-11-25"}, "6": {"status": "connected", "followup_at": "2026-11-18 16:30"},
              "8": {"status": "connected"}}
    leads += [open_lead("9", real=0, deadline=ist("2026-11-17 20:00")), open_lead("10", real=0, deadline=ist("2026-11-18 20:00")),
              open_lead("11", real=0)]
    judged |= {"9": {"status": "notconnected"}, "10": {"status": "callback"}, "11": {"status": "notconnected"}}
    rows = morning.plan(leads, judged, day)
    got = [(l["lead_id"], p["group"]) for l, _, p in rows]
    # 2 and 11 missed their 48 hours on 16 Nov, before yesterday: out of the race, into their status group
    assert got == [("3", 1), ("6", 1), ("8", 1), ("9", 2), ("10", 2), ("4", 4), ("2", 5), ("11", 7), ("1", 8), ("5", 9)]
    assert rows[3][2]["why"].startswith("48 h missed") and rows[4][2]["why"].startswith("Needs a real talk by")
    assert rows[6][2]["why"] == "No real talk yet; 48 h missed 16 Nov"


def test_grade_must_fit_the_calls():
    with pytest.raises(ValueError):
        morning.plan([open_lead("1", real=0)], {"1": {"status": "hot"}}, date(2026, 11, 18))
    with pytest.raises(ValueError):
        morning.plan([open_lead("1", real=2)], {"1": {"status": "callback"}}, date(2026, 11, 18))
    with pytest.raises(ValueError):
        morning.plan([open_lead("1")], {}, date(2026, 11, 18))


def test_build_writes_one_workbook_per_team_and_the_plan(tmp_path):
    leads = [open_lead("1"), open_lead("2", caller="Ravi", real=0, deadline=ist("2026-11-18 20:00")),
             open_lead("3", caller="Shweta", side="Puja / Anmol", tl="Aditya", ad="Anmol")]
    judged = {"1": {"status": "hot", "payment": "Card EMI · Sat 21 Nov"}, "2": {"status": "notconnected"}, "3": {"status": "connected"}}
    leads = [l | {"allocated": ist("2026-11-14 10:00")} for l in leads]
    books, saved = morning.build(leads, judged, date(2026, 11, 18), ist("2026-11-17 21:00"), str(tmp_path))
    assert [os.path.basename(b) for b in books] == ["Bootcamp collections call list - Mayur's team - Wed 18 Nov 2026.xlsx",
                                                    "Bootcamp collections call list - Anmol's team - Wed 18 Nov 2026.xlsx"]
    mayur, anmol = (load_workbook(b) for b in books)
    assert mayur.sheetnames == ["Team summary", "How to use", "Ankita", "Ravi"]
    assert anmol.sheetnames == ["Team summary", "How to use", "Shweta"]
    assert mayur["Ankita"].cell(row=5, column=6).value == "Lead details"
    assert mayur["Ankita"].row_dimensions[6].height == 62.5
    assert mayur["Ankita"].cell(row=6, column=6).value.splitlines() == [
        "Bootcamp: US Accounting · D1", "Status: Hot", "48-hour deadline: done in time", "Last real talk: 15 Nov",
        "Payment: Card EMI · Sat 21 Nov"]
    assert "Mayur's team" in mayur["Team summary"].cell(row=1, column=1).value
    summary = [v for row in mayur["Team summary"].iter_rows(values_only=True) for v in row if v]
    assert "Score: real talk within 48 hours (%)" in summary and "1. Follow-ups due today or missed" in summary
    assert json.load(open(saved))["leads"]["2"]["group"] == 2


def test_latest_grade_made_by_the_read_day(tmp_path):
    p = tmp_path / "j.json"
    p.write_text(json.dumps({"1": {"2026-11-16": {"status": "connected"}, "2026-11-17": {"status": "hot"},
                                   "2026-11-18": {"status": "cold"}}}))
    assert judged_on(str(p), date(2026, 11, 17)) == {"1": {"status": "hot"}}
    assert judged_on(str(p), date(2026, 11, 18)) == {"1": {"status": "cold"}}


def test_note_html_read_as_text():
    from analytics.collection_daily.timelines import text
    raw = '<div style="x"><h3>Summary</h3><p>Loan &amp; EMI</p><br/><ul><li>Docs by Thu</li></ul></div>'
    assert text(raw) == "Summary / Loan & EMI / Docs by Thu"


def test_merge_checks_grades_and_lists_the_ungraded(tmp_path):
    from analytics.collection_daily.__main__ import merge
    (tmp_path / "grade").mkdir()
    (tmp_path / "grade" / "out0.json").write_text(json.dumps({"1": {"status": "hot"}, "x": {"status": "cold"}}))
    added, missing = merge(str(tmp_path), "2026-11-17", [open_lead("1"), open_lead("2")])
    assert (added, missing) == (1, ["2"])
    assert json.load(open(tmp_path / "judgements.json")) == {"1": {"2026-11-17": {"status": "hot"}}}
    (tmp_path / "grade" / "out0.json").write_text(json.dumps({"2": {"status": "hot"}}))
    with pytest.raises(ValueError):
        merge(str(tmp_path), "2026-11-17", [open_lead("2", real=0)])


def test_cut_is_the_earliest_weekend_read(tmp_path):
    for name, tag, at in (("w1", "US Accounting Bootcamp Collection - 24th October'26", "2026-11-17 11:22:00"),
                          ("w2", "NCA Bootcamp Collection - 31st October'26", "2026-11-17 11:17:00")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps({"bootcamps": [tag], "fetched_at_utc": at}))
    assert [day for _, day in facts.weekends(str(tmp_path))] == ["2026-10-24", "2026-10-31"]
    assert facts.read_at(str(tmp_path)) == ist("2026-11-17 16:47")


def test_a_week_without_pick_up_despite_calls_turns_cold():
    cut = ist("2026-11-17 20:00")
    calls = [call(f"2026-11-{d} {h}:00", answered=False) for d in range(10, 18) for h in (11, 16)]
    rows = morning.plan([open_lead("1", real=1, calls=calls)], {"1": {"status": "hot"}}, date(2026, 11, 18), cut)
    l, j, p = rows[0]
    assert (j["status"], p["group"]) == ("cold", 8) and p["why"].startswith("Was Hot; no pick-up since")


def test_a_follow_up_already_dialled_today_leaves_group_one():
    day = date(2026, 11, 18)
    due = ist("2026-11-18 10:00")
    kept = open_lead("1", next_follow_up=due, last_dial=ist("2026-11-18 10:05"))
    open_ = open_lead("2", next_follow_up=due, last_dial=ist("2026-11-17 15:00"))
    rows = morning.plan([kept, open_], {"1": {"status": "hot"}, "2": {"status": "hot"}}, day)
    assert [(l["lead_id"], p["group"]) for l, _, p in rows] == [("2", 1), ("1", 3)]


def test_numbers_whose_real_talk_has_no_saved_transcript(tmp_path):
    from analytics.collection_daily.timelines import without_transcript
    talk = {"id": "a1", "t": ist("2026-11-17 11:14"), "answered": True, "dur": 423, "out": True}
    lead = open_lead("1", phone="+91-9000000001", calls=[talk])
    rec = {"start_time": "2026-11-17T05:44:00.000Z", "end_time": "2026-11-17T05:51:03.000Z", "call_duration": 423,
           "transcript": {"text": "balance payment by card"}}
    (tmp_path / "919000000001.json").write_text(json.dumps({"sales_call": []}))  # read before Salesa had written it
    assert without_transcript([lead], [str(tmp_path)], ist("2026-11-18 12:00")) == ["+91-9000000001"]
    (tmp_path / "919000000001.json").write_text(json.dumps({"sales_call": [rec]}))
    assert without_transcript([lead], [str(tmp_path)], ist("2026-11-18 12:00")) == []


def test_transcripts_read_before_the_data_are_read_again(tmp_path, monkeypatch):
    from analytics.collection_daily import timelines
    asked = []

    class Fake:
        def __init__(self, **kw):
            pass

        def search_raw(self, nums):
            asked.append(list(nums))
            return {n: {"sales_call": []} for n in nums}
    monkeypatch.setattr(timelines, "TranscriptClient", Fake)
    old = tmp_path / "919000000001.json"
    old.write_text("{}")
    os.utime(old, (ist("2026-11-17 17:00").timestamp(),) * 2)
    assert timelines.fetch_transcripts(str(tmp_path), ["+91-9000000001"]) == (0, 0)  # saved copy kept
    assert timelines.fetch_transcripts(str(tmp_path), ["+91-9000000001"], fresh_after=ist("2026-11-18 12:00")) == (1, 0)
    assert timelines.fetch_transcripts(str(tmp_path), ["+91-9000000001"], fresh_after=ist("2026-11-18 12:00")) == (0, 0)
    assert len(asked) == 1


def test_caller_sheet_ends_at_calls_so_far(tmp_path):
    leads = [open_lead("1") | {"allocated": ist("2026-11-14 10:00")}]
    books, _ = morning.build(leads, {"1": {"status": "hot"}}, date(2026, 11, 18), ist("2026-11-17 21:00"), str(tmp_path))
    ws = load_workbook(books[0])["Ankita"]
    assert [c.value for c in ws[5] if c.value][-1] == "Calls so far (calls / days)" and ws.max_column == 8


def test_one_caller_sample_leaves_the_plan_alone(tmp_path):
    leads = [l | {"allocated": ist("2026-11-14 10:00")} for l in (open_lead("1"), open_lead("2", caller="Ravi"))]
    judged = {"1": {"status": "hot"}, "2": {"status": "connected"}}
    books, saved = morning.build(leads, judged, date(2026, 11, 18), ist("2026-11-17 21:00"), str(tmp_path), caller="Ravi")
    assert saved is None and [os.path.basename(b) for b in books] == ["Bootcamp collections call list - Ravi - Wed 18 Nov 2026.xlsx"]
    assert load_workbook(books[0]).sheetnames == ["Team summary", "How to use", "Ravi"]
    assert not list(tmp_path.glob("plan_*.json"))


def test_last_talk_uses_the_readers_dated_text():
    l = open_lead("1")
    got = morning.details(l, {"status": "hot", "last_talk": "17 Nov: card EMI chosen"}, {"why": ""})
    assert "Last real talk: 17 Nov: card EMI chosen" in got.splitlines()


def test_iit_pitch_column(tmp_path):
    leads = [l | {"allocated": ist("2026-11-14 10:00")} for l in (open_lead("1"), open_lead("2"))]
    judged = {"1": {"status": "hot", "iit_pitch": "Pitched 16 Nov: declined, budget"}, "2": {"status": "hot"}}
    books, _ = morning.build(leads, judged, date(2026, 11, 18), ist("2026-11-17 21:00"), str(tmp_path))
    ws = load_workbook(books[0])["Ankita"]
    assert ws.cell(row=5, column=7).value == "IIT Pitch"
    assert [ws.cell(row=r, column=7).value for r in (6, 7)] == ["Pitched 16 Nov: declined, budget", "Not read yet"]


def test_a_notes_tab_entry_saying_loan_done_enrols():
    notes = [{"t": "2026-11-18 09:30:00", "text": "Loan done by Fibe"}, {"t": "2026-11-18 08:00:00", "text": "after loan done, send portal"}]
    assert facts.note_loan_done(notes, ist("2026-11-18 20:00")) == datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
    assert facts.note_loan_done(notes, ist("2026-11-18 14:00")) is None  # 09:30 UTC is 15:00 IST


def test_community_lead_details_show_the_batch_and_booking_date():
    l = open_lead("1", community=True, bootcamp="Independent Director G88–89", booked_on=date(2026, 11, 7))
    got = morning.details(l, {"status": "hot"}, {"why": ""}).splitlines()
    assert got[0] == "Community batch: Independent Director G88–89 · booked 07 Nov"
