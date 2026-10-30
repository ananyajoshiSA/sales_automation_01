from datetime import datetime

from analytics.collection_cycle import add_working_hours, cycle_leads, short_bootcamp, state, working_hours_between
from integrations.timeutil import IST

T = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
TAG = "Technology Law Bootcamp Collection - 24th October'26"
GROUPS = {"Vedahi Rana": ["Elite Changemakers"], "Sonal Tripathi": ["Elite Changemakers"], "Other Person": ["Group A"]}
META = {"member_groups": GROUPS, "team_of_owner": {}, "team_callers": []}


def utc(s):  # IST wall time -> the UTC string LeadSquared stores
    return T(s).astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def owner(t, prev, cur):
    return {"EventCode": 3001, "CreatedOn": utc(t), "Data": [{"Key": "PreviousOwner", "Value": prev}, {"Key": "CurrentOwner", "Value": cur}]}


def stage(t, cur):
    return {"EventCode": 3002, "CreatedOn": utc(t), "Data": [{"Key": "CurrentStage", "Value": cur}]}


def call(t, status="Answered", dur=200, out=True):
    note = f"Caller{{=}}Vedahi Rana{{next}}Duration{{=}}{dur}{{next}}Status{{=}}{status}"
    return {"EventCode": 22 if out else 21, "CreatedOn": utc(t), "Id": t, "ActivityFields": {"ActivityEvent_Note": note}}


def lead(lid, owner_name="Vedahi Rana", tag=TAG):
    return {"ProspectID": lid, "FirstName": "Lead", "LastName": lid, "Mobile": "+91-0000000001", "OwnerIdName": owner_name,
            "ProspectStage": "Call Back Later", "mx_Bootcamp_collections": tag}


def test_working_hours_skip_nights():
    assert add_working_hours(T("2026-10-26 18:00"), 12) == T("2026-10-27 20:00")
    assert add_working_hours(T("2026-10-26 21:30"), 1) == T("2026-10-27 11:00")
    assert working_hours_between(T("2026-10-26 18:00"), T("2026-10-27 11:00")) == 3.0
    assert working_hours_between(T("2026-10-27 11:00"), T("2026-10-26 18:00")) == 0.0


def test_assignment_day_deadline_and_state():
    hist = {
        # assigned Monday 11:00 to the caller: Day 1; dialled 3 working hours later; enrolled Wednesday
        "a": [stage("2026-10-26 11:00", "Booking fees received"), owner("2026-10-26 11:00", "Seller", "Vedahi Rana"),
              call("2026-10-26 14:00"), stage("2026-10-28 12:00", "Course Enrolled")],
        # assigned Tuesday 19:00 (Day 2), first dial Thursday 12:00: 13 working hours, late
        "b": [owner("2026-10-27 19:00", "Seller", "Vedahi Rana"), call("2026-10-29 12:00", "NotAnswered", 0)],
        # held by someone outside the team: not a cycle lead
        "c": [owner("2026-10-26 11:00", "Seller", "Other Person")],
        # another bootcamp's tag: not this cycle
        "d": [owner("2026-10-26 11:00", "Seller", "Vedahi Rana")],
    }
    leads = [lead("a"), lead("b"), lead("c", "Other Person"), lead("d", tag="Data Protection Collection - 17th October'26")]
    L = {x["lead_id"]: x for x in cycle_leads(leads, hist, META, "2026-10-24", "Mayur")}
    assert set(L) == {"a", "b"}
    a, b = L["a"], L["b"]
    assert (a["day"], b["day"]) == (1, 2) and a["tl"] == "Sonal Tripathi" and a["bootcamp"] == "Technology Law 24 Oct"
    wed = state(a, T("2026-10-28 20:00"))
    assert wed["on_time"] and wed["delay_wh"] == 3.0 and wed["real_talks"] == 1 and wed["enrolled"]
    assert not state(a, T("2026-10-28 11:00"))["enrolled"]
    sb = state(b, T("2026-10-28 20:00"))
    assert not sb["first_dial"] and sb["overdue_wh"] == 1.0          # due Wed 19:00
    sb = state(b, T("2026-10-29 13:00"))
    assert sb["delay_wh"] == 13.0 and not sb["on_time"]


def test_short_bootcamp_name():
    assert short_bootcamp("US Accounting Bootcamp Collection - 24th October'26") == "US Accounting 24 Oct"
