import json
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
    base = {"lead_id": lid, "phone": "9" + lid.rjust(9, "0"), "name": "", "caller": caller, "tl": "Sonal", "ad": "Sahil",
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
    rows = morning.plan(leads, judged, day)
    got = [(l["lead_id"], p["group"]) for l, _, p in rows]
    assert got == [("3", 1), ("6", 1), ("8", 1), ("2", 2), ("4", 4), ("1", 7), ("5", 8)]
    assert rows[3][2]["why"].startswith("48 h passed")


def test_grade_must_fit_the_calls():
    with pytest.raises(ValueError):
        morning.plan([open_lead("1", real=0)], {"1": {"status": "hot"}}, date(2026, 11, 18))
    with pytest.raises(ValueError):
        morning.plan([open_lead("1", real=2)], {"1": {"status": "callback"}}, date(2026, 11, 18))
    with pytest.raises(ValueError):
        morning.plan([open_lead("1")], {}, date(2026, 11, 18))


def test_build_writes_caller_sheets_and_the_plan(tmp_path):
    leads = [open_lead("1"), open_lead("2", caller="Ravi", real=0)]
    judged = {"1": {"status": "hot", "payment": "Card EMI · Sat 21 Nov"}, "2": {"status": "notconnected"}}
    leads = [l | {"allocated": ist("2026-11-14 10:00")} for l in leads]
    xlsx, saved = morning.build(leads, judged, date(2026, 11, 18), ist("2026-11-17 21:00"), str(tmp_path))
    wb = load_workbook(xlsx)
    assert wb.sheetnames == ["Team summary", "How to use", "Ankita", "Ravi"]
    assert wb["Ankita"].cell(row=6, column=12).value == "Card EMI · Sat 21 Nov"
    assert json.load(open(saved))["leads"]["2"]["group"] == 2


def test_latest_grade_before_the_day(tmp_path):
    p = tmp_path / "j.json"
    p.write_text(json.dumps({"1": {"2026-11-16": {"status": "connected"}, "2026-11-17": {"status": "hot"},
                                   "2026-11-18": {"status": "cold"}}}))
    assert judged_on(str(p), date(2026, 11, 18)) == {"1": {"status": "hot"}}


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
