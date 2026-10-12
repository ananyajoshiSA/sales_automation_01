from analytics.bootcamp_recovery import build, opted_course, opted_kind
from integrations.timeutil import utc

NOW = utc("2026-10-12 09:00:00")
CALLERS = frozenset({"Asha Rao"})
CHAT = "Bootcamp | Independent Director - September 12, September 13, September 14, 2026 - Chat"
FAILED = "Bootcamp | Patent Law & Analysis - September 19, September 20, September 21, 2026 - Failed"


def stage(t, cur, comment=""):
    return {"EventCode": 3002, "CreatedOn": t, "Data": [{"Key": "CurrentStage", "Value": cur}, {"Key": "Comment", "Value": comment}]}


def assign(t, to="Asha Rao"):
    return {"EventCode": 3001, "CreatedOn": t, "Data": [{"Key": "CurrentOwner", "Value": to}]}


def call(t, out=True, status="Answered", dur=200, by="Asha Rao"):
    note = f"Caller{{=}}{by}{{next}}Duration{{=}}{dur}{{next}}Status{{=}}{status}"
    return {"EventCode": 22 if out else 21, "CreatedOn": t, "ActivityFields": {"ActivityEvent_Note": note}}


def lead(lid, stage_now, opted="", tag=""):
    return {"ProspectID": lid, "OwnerId": "u1", "OwnerIdName": "Asha Rao", "ProspectStage": stage_now,
            "mx_Enrollment_Opted": opted, "mx_Bootcamp_collections": tag}


def test_opted_parsing():
    assert opted_kind(CHAT) == "Chat" and opted_kind(FAILED) == "Failed payment" and opted_kind("x - Payment Opted") is None
    assert opted_course(CHAT) == ("Independent Director", "2026-09-12")
    assert opted_course("Bootcamp | AI for Business Growth | Women (Re) - September 19, September 20, 2026 - Failed")[1] == "2026-09-19"


HIST = {
    # chat lead handed over on day 2, called once, then left for 3 weeks
    "chat_idle": [assign("2026-09-13 06:00:00"), call("2026-09-13 07:00:00", status="NotAnswered", dur=0)],
    # failed payment, worked daily and still being called
    "failed_busy": [assign("2026-09-20 06:00:00")] + [call(f"2026-10-{d:02d} 06:00:00") for d in range(5, 13)],
    # failed payment, dropped the same day with no conversation, then called in two days later
    "failed_dropped": [assign("2026-09-20 06:00:00"), call("2026-09-20 07:00:00", status="NotAnswered", dur=0),
                       stage("2026-09-20 08:00:00", "Not Interested", "NI"), call("2026-09-23 06:00:00", out=False, status="Missed", dur=0)],
    # booked, talked, then said no for good
    "booked_refused": [stage("2026-09-14 05:00:00", "Booking fees received"), call("2026-09-15 05:00:00"),
                       call("2026-09-16 05:00:00"), call("2026-09-17 05:00:00"),
                       stage("2026-09-17 06:00:00", "Not Interested", "joined another institute")],
    # chat lead that went on to pay the booking fee
    "chat_won": [assign("2026-09-13 06:00:00"), call("2026-09-13 07:00:00"), stage("2026-09-13 08:00:00", "Booking fees received")],
}
LEADS = [lead("chat_idle", "New Lead", CHAT), lead("failed_busy", "Follow Up For Closure", FAILED),
         lead("failed_dropped", "Not Interested", FAILED), lead("booked_refused", "Not Interested", tag="US Accounting Bootcamp Collection - 12th September'26"),
         lead("chat_won", "Booking fees received", CHAT), lead("neither", "New Lead")]


def test_under_worked_and_dropped_too_early():
    views, skipped = build(LEADS, {**HIST, "neither": []}, {"u1": "Elite Changemakers"}, CALLERS, NOW)
    v = {x["lead_id"]: x for x in views}
    assert skipped == {"not booked, chat or failed payment": 1}
    assert v["chat_idle"]["outcome"] == "open" and v["chat_idle"]["start_ist"] == "2026-09-13 11:30"
    assert "not reached, called on only 1 day(s)" in v["chat_idle"]["gaps"] and v["chat_idle"]["priority"].startswith("B")
    assert v["failed_busy"]["priority"] == "C: being worked"
    d = v["failed_dropped"]
    assert d["outcome"] == "dropped" and d["priority"].startswith("A")
    assert "dropped without a 2-minute conversation" in d["gaps"] and "came back after the drop: called in" in d["gaps"]
    assert v["booked_refused"]["priority"] == "Explicit refusal: leave"
    assert v["chat_won"]["outcome"] == "won"
