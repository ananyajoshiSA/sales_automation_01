from analytics.bootcamp_collections import build, callbacks, loss_reason, parse_tag, summarise
from integrations.timeutil import utc

NOW = utc("2026-10-09 06:00:00")


def stage(t, cur, prev="New Lead", comment=""):
    return {"EventCode": 3002, "CreatedOn": t,
            "Data": [{"Key": "PreviousStage", "Value": prev}, {"Key": "CurrentStage", "Value": cur},
                     {"Key": "Comment", "Value": comment}]}


def call(t, out=True, status="Answered", dur=200, by="Asha Rao"):
    note = f"Caller{{=}}{by}{{next}}Duration{{=}}{dur}{{next}}Status{{=}}{status}"
    return {"EventCode": 22 if out else 21, "CreatedOn": t, "ActivityFields": {"ActivityEvent_Note": note}}


def form(t, due, note="", status="Call Back Later"):
    return {"EventCode": 103, "CreatedOn": t,
            "ActivityFields": {"mx_Custom_1": due, "Status": status, "ActivityEvent_Note": note}}


def lead(lid, stage_now, owner="u1", tag="Independent Director Bootcamp Collection - 18th July'26"):
    return {"ProspectID": lid, "OwnerId": owner, "OwnerIdName": "Asha Rao", "ProspectStage": stage_now,
            "mx_Bootcamp_collections": tag}


BOOK = "2026-10-01 05:00:00"   # 10:30 IST
HIST = {
    # dialled in 1 h, collected; one callback kept, one missed
    "fast": [stage(BOOK, "Booking fees received"), call("2026-10-01 06:00:00"),
             form("2026-10-01 06:05:00", "2026-10-02 06:00:00"), call("2026-10-02 06:30:00", status="NotAnswered", dur=0),
             form("2026-10-02 06:35:00", "2026-10-03 06:00:00"), call("2026-10-04 09:00:00"),
             stage("2026-10-04 09:10:00", "Course Enrolled", "Follow Up For Closure")],
    # first dial after 3 days, never answered, then marked not interested asking for a refund
    "slow": [stage(BOOK, "Booking fees received"), call("2026-10-04 05:00:00", status="NotAnswered", dur=0),
             call("2026-10-01 07:00:00", out=False, status="Missed", dur=0),
             stage("2026-10-05 05:00:00", "Not Interested", "Booking fees received", "wants a refund, will not continue")],
    # never dialled; still open
    "idle": [stage(BOOK, "Booking fees received")],
    # never booked: not a collection lead
    "nobook": [stage(BOOK, "Call Back Later")],
}
LEADS = [lead("fast", "Course Enrolled"), lead("slow", "Not Interested"), lead("idle", "Booking fees received", owner="x"),
         lead("nobook", "Call Back Later"), lead("nohist", "Call Back Later")]


def test_parse_tag():
    assert parse_tag("Independent Director Bootcamp Collection - 18th July'26") == ("Independent Director", "2026-07-18")
    assert parse_tag("Remote Women AI Bootcamp Collection - 3rd October'26") == ("Remote Women AI", "2026-10-03")
    assert parse_tag("Women AI bootcamp collections - 6th July '24")[1] == "2024-07-06"
    assert parse_tag("Community Webinar Collections") == ("Community Webinar", "")


def test_views_trace_speed_connection_and_outcome():
    views, recon = build(LEADS, HIST, {"u1": "Elite Changemakers"}, NOW)
    assert recon == {"tagged": 5, "analysed": 3, "no history fetched": 1, "never reached Booking fees received": 1}
    v = {x["lead_id"]: x for x in views}
    assert v["fast"]["dialled_24h"] and v["fast"]["connected_24h"] and v["fast"]["outcome"] == "collected"
    assert v["fast"]["course"] == "Independent Director" and v["fast"]["bootcamp_day"] == "2026-07-18"
    assert (v["fast"]["callbacks_set"], v["fast"]["callbacks_due"], v["fast"]["callbacks_missed"]) == (2, 2, 1)
    assert v["slow"]["days_to_loss"] == 4.0
    assert v["slow"]["mins_to_first_dial"] == 3 * 1440 and not v["slow"]["dialled_24h"] and not v["slow"]["connected"]
    assert v["slow"]["outcome"] == "lost" and v["slow"]["reason"] == "Asked for refund (no reason given)"
    assert v["slow"]["lost_without_real_conv"] and v["slow"]["inbound_unreturned_days"] == 1
    assert v["idle"]["team"] == "Outside the two teams" and v["idle"]["stalled"] and v["idle"]["speed"] == "never"


def test_collection_team_dial_and_handover_are_measured_apart():
    hist = {"h": [stage(BOOK, "Booking fees received"), call("2026-10-01 06:00:00", by="Seller"),
                  {"EventCode": 3001, "CreatedOn": "2026-10-02 07:00:00", "Data": [{"Key": "CurrentOwner", "Value": "Asha Rao"}]},
                  call("2026-10-02 08:00:00")]}
    leads = [{**lead("h", "Follow Up For Closure"), "mx_Next_follow_up_date": "2026-10-05 06:00:00.000"}]
    (v,), _ = build(leads, hist, {"u1": "Elite Changemakers"}, NOW, frozenset({"Asha Rao"}))
    assert v["dialled_24h"] and not v["team_dialled_24h"] and v["team_mins_to_first_dial"] == 27 * 60
    assert v["hrs_to_handover"] == 26.0 and v["followup_overdue"]


def test_summary():
    views, _ = build(LEADS, HIST, {"u1": "Elite Changemakers"}, NOW)
    s = summarise(views)
    assert s["leads"] == 3 and s["dialled_24h_%"] == 33.3 and s["never_dialled"] == 1
    assert (s["collected"], s["lost"], s["open"]) == (1, 1, 1) and s["collected_%_of_closed"] == 50.0
    assert s["callbacks_missed_%"] == 50.0 and s["dial_answer_%"] == 50.0


def test_callbacks_skip_replaced_instant_and_future_plans():
    start = utc(BOOK)
    forms = [form("2026-10-01 06:00:00", "2026-10-03 06:00:00"),   # replaced by the next form a day early
             form("2026-10-02 06:00:00", "2026-10-02 06:05:00"),   # set for "now": not a plan
             form("2026-10-02 07:00:00", "2026-10-02 12:00:00"),   # due, dialled 1 h later: kept
             form("2026-10-02 13:00:00", "2026-10-09 05:00:00")]   # due less than 2 h before now
    out = callbacks(forms, [utc("2026-10-02 13:00:00")], start, None, NOW)
    assert [(c["due"], c["kept"]) for c in out] == [(utc("2026-10-02 12:00:00"), True)]


def test_loss_reason():
    assert loss_reason("loan got rejected by the NBFC") == "Loan, EMI or payment process failed"
    assert loss_reason("busy with exams") == "No time / personal or health reasons"
    assert loss_reason("I do not want to continue because of financial issues") == "Cannot afford the balance"
    assert loss_reason("didn't find the course relevant, wants refund") == "Doubts about course value or trust"
    assert loss_reason("NI") == "No reason recorded"
