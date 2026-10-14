from analytics.team_lead_analysis import bootcamp_family, build, community_name, is_community, summarise, week_starts

TEAM = {"Asha Rao": {"id": "u1", "team": "Team A"}, "Ravi Das": {"id": "u2", "team": "Team B"}}


def assigned(t, prev, cur):
    return {"EventCode": 3001, "CreatedOn": t,
            "Data": [{"Key": "PreviousOwner", "Value": prev}, {"Key": "CurrentOwner", "Value": cur}]}


def stage(t, cur, by="Asha Rao", prev="New Lead"):
    return {"EventCode": 3002, "CreatedOn": t,
            "Data": [{"Key": "PreviousStage", "Value": prev}, {"Key": "CurrentStage", "Value": cur},
                     {"Key": "CreatedBy", "Value": by}]}


def bootcamp(t, name):
    return {"EventCode": 12001, "CreatedOn": t, "ActivityFields": {"mx_Custom_13": name}}


def call(lead, t, user="u1", status="Answered", dur=300, out=True):
    return {"activity_id": f"{lead}{t}{user}", "lead_id": lead, "direction": "outbound" if out else "inbound",
            "start_utc": t, "user_id": user, "caller": "", "status": status, "duration": dur}


HIST = {
    # from the pool, dialled in 10 min, real talk, enrolled 3 days later after a bootcamp
    "won": [bootcamp("2026-09-26 05:00:00", "IP Law Career Bootcamp - 26th Sep'26"),
            assigned("2026-10-01 04:00:00", "Rinku Jhala", "Asha Rao"),
            stage("2026-10-04 06:00:00", "Course Enrolled")],
    # from another team, never dialled, sent back to the pool 5 days later
    "idle": [assigned("2026-10-01 04:00:00", "Neha Sen", "Ravi Das"),
             assigned("2026-10-06 04:00:00", "Ravi Das", "Rinku Jhala")],
    # enrolled in 2024, enrolled again in the window: a repeat, not first-time
    "again": [stage("2024-01-02 04:00:00", "Course Enrolled", by="Ops"),
              assigned("2026-09-01 04:00:00", "Rinku Jhala", "Ravi Das"),
              stage("2026-10-05 04:00:00", "Course Enrolled", by="Ravi Das")],
    # moved inside the team, then marked Not Interested with no conversation
    "dead": [assigned("2026-10-02 04:00:00", "", "Asha Rao"), assigned("2026-10-03 04:00:00", "Asha Rao", "Ravi Das"),
             stage("2026-10-03 06:00:00", "Not Interested", by="Ravi Das")],
    # enrolled while owned outside the team, closer was a team caller; joined a GrowthX community first
    "community": [assigned("2026-08-01 04:00:00", "Rinku Jhala", "Onboarding Desk"),
                  stage("2026-10-06 04:00:00", "Course Enrolled", by="Ops")],
}
LEADS = {
    "won": {"ProspectID": "won", "OwnerIdName": "Asha Rao", "CreatedOn": "2026-09-30 04:00:00", "ProspectStage": "Course Enrolled"},
    "idle": {"ProspectID": "idle", "OwnerIdName": "Rinku Jhala", "CreatedOn": "2026-01-01 04:00:00"},
    "again": {"ProspectID": "again", "OwnerIdName": "Ravi Das", "CreatedOn": "2023-12-01 04:00:00"},
    "dead": {"ProspectID": "dead", "OwnerIdName": "Ravi Das", "CreatedOn": "2026-10-01 04:00:00",
             "ProspectStage": "Not Interested"},
    "community": {"ProspectID": "community", "OwnerIdName": "Onboarding Desk", "CreatedOn": "2026-07-01 04:00:00"},
    # owned by the team, no owner-change record: received at its Assigned On, never dialled
    "quiet": {"ProspectID": "quiet", "OwnerIdName": "Asha Rao", "CreatedOn": "2026-10-05 04:00:00",
              "mx_Assigned_On": "2026-10-05 05:00:00", "ProspectStage": "New Lead"},
}
HIST["quiet"] = []
CALLS = [call("won", "2026-10-01 04:10:00"), call("won", "2026-10-03 04:10:00", status="NotAnswered", dur=0),
         call("community", "2026-10-05 10:00:00", user="u2", dur=400),
         call("again", "2026-09-30 04:00:00", user="u2", status="CallFailure", dur=0)]
GROWTHX = [{"leadtype": "Criminal Community", "status": "Paid", "capturedAt": "1 Sept 2026, 10:00 am",
            "whatsapp": "+91 98765 43210"}]
SRC = {"team": TEAM, "leads": LEADS, "hist": HIST, "calls": CALLS, "growthx": GROWTHX,
       "contacts": {"community": {"Phone": "9876543210"}}, "meta": {}}


def run():
    return build(SRC, "2026-09-24", "2026-10-07")


def test_names():
    assert bootcamp_family("US corporate law Bootcamp - 4th July'26") == ("US Corporate Law", "2026-07-04")
    assert community_name("legal-ai-community") == "Legal AI"
    assert community_name("ID Community") == "Independent Director"
    assert is_community("contract drafting") and is_community("Criminal Community") and not is_community("hr-oct")
    starts = week_starts("2026-09-24", "2026-10-07")
    assert [f"{s:%m-%d}" for s in starts] == ["09-24", "10-01"]


def test_arrivals_departures_and_moves():
    b = run()
    arr = {a["lead_id"]: a for a in b["arrivals"]}
    assert set(arr) == {"won", "idle", "dead", "quiet"}       # "again" arrived before the range
    assert arr["won"]["origin"] == "Pool (Rinku Jhala)" and arr["won"]["first_dial_mins"] == 10
    assert arr["won"]["real"] and arr["won"]["enrolled"] and arr["won"]["new_lead"]
    assert arr["idle"]["origin"] == "Another caller or team" and arr["idle"]["dials"] == 0 and arr["idle"]["left"]
    assert arr["quiet"]["origin"].startswith("Unknown")
    [d] = b["departures"]
    assert d["lead_id"] == "idle" and d["to"] == "Pool (Rinku Jhala)" and not d["dialled"] and d["held_days"] == 5.0
    assert [m["lead_id"] for m in b["moves"]] == ["dead"]
    assert [a["lead_id"] for a in b["backlog"]] == ["quiet"]


def test_enrolments_first_time_repeat_and_credit():
    b = run()
    e = {x["lead_id"]: x for x in b["enrols"]}
    assert e["won"]["first_time"] and e["won"]["credited_to"] == "Asha Rao"
    assert e["won"]["bootcamp"] == "IP Law Career" and e["won"]["days_since_bootcamp"] == 8
    assert not e["again"]["first_time"]
    # owned outside the team: credited to the team caller who had the last real conversation
    assert e["community"]["credited_to"] == "Ravi Das" and e["community"]["community"] == "Criminal"
    [c] = b["closures"]
    assert c["lead_id"] == "dead" and c["owner"] == "Ravi Das" and not c["real_conversation"]


def test_summary_weeks_and_drop():
    s = summarise(run(), "2026-09-24", "2026-10-07")
    w = {r["week"]: r for r in s["weeks"]}
    last = w["01 Oct–07 Oct"]
    assert last["first_time_enrolments"] == 2 and last["repeat_enrolments"] == 1
    assert last["leads_received"] == 4 and last["churned_out"] == 1 and last["closed_dead_without_talk"] == 1
    assert w["24 Sep–30 Sep"]["dials"] == 1
    teams = {t["team"]: t for t in s["teams"]}
    assert teams["Team A"]["leads_received"] == 3 and teams["Team A"]["dialled_within_24h_%"] == 33.3
    assert {r["bootcamp"]: r["first_time_enrolments"] for r in s["bootcamps"]}["IP Law Career"] == 1
