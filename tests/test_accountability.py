import csv
import json
from datetime import datetime, timezone

from analytics.accountability import People, lead_actions, lead_origin, run

USERS = [{"FirstName": n.split()[0], "LastName": " ".join(n.split()[1:])}
         for n in ("Rinku Jhala", "Admin", "System", "Pratik Sarkar", "Kanishka", "Asha Rao", "Ravi Das")]
PEOPLE = People(USERS)
D0, D1 = datetime(2026, 10, 4, tzinfo=timezone.utc), datetime(2026, 10, 10, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def assigned(i, t, prev, to, by):
    return {"Id": f"A{i}", "EventCode": 3001, "EventName": "LeadAssigned", "CreatedOn": t,
            "Data": [{"Key": "PreviousOwner", "Value": prev}, {"Key": "CurrentOwner", "Value": to},
                     {"Key": "CreatedBy", "Value": by}]}


def call(i, t, inbound, who, status):
    key = "Reciever" if inbound else "Caller"
    return {"Id": f"C{i}", "EventCode": 21 if inbound else 22, "CreatedOn": t,
            "ActivityFields": {"ActivityEvent_Note": f"{key}{{=}}{who}{{next}}Status{{=}}{status}"}}


def lead(**kw):
    return {"ProspectID": "L1", "OwnerIdName": "Rinku Jhala", "CreatedOn": "2026-01-01 00:00:00",
            "CreatedByName": "Pratik Sarkar", **kw}


def test_personal_login_is_the_person_and_moves_are_classified():
    acts = [assigned(1, "2026-10-05 05:00:00", "", "Asha Rao", "Kanishka"),
            assigned(2, "2026-10-06 05:00:00", "Asha Rao", "Ravi Das", "Kanishka"),
            assigned(3, "2026-10-07 05:00:00", "Ravi Das", "Rinku Jhala", "Pratik Sarkar")]
    rows = lead_actions(lead(mx_Assigned_By="Asha Rao"), acts, PEOPLE, D0, D1, NOW)
    got = [(r["action"], r["action_performed_by"], r["accountability_status"]) for r in rows]
    assert got == [("assignment", "Kanishka", "Verified"), ("transfer", "Kanishka", "Verified"),
                   ("moved into shared account", "Pratik Sarkar", "Verified")]
    assert rows[0]["assigned_by"] == "Kanishka" and rows[1]["transferred_by"] == "Kanishka"
    assert rows[2]["current_responsible_person"] == "No one (lead is in Rinku Jhala account)"


def test_shared_login_uses_assigned_by_only_when_it_belongs_to_that_change():
    fresh = lead(mx_Assigned_By="Asha Rao", mx_Assigned_On="2026-10-07 05:00:30")
    acts = [assigned(1, "2026-10-07 05:00:00", "Ravi Das", "Rinku Jhala", "Rinku Jhala")]
    r = lead_actions(fresh, acts, PEOPLE, D0, D1, NOW)[0]
    assert (r["action_performed_by"], r["accountability_status"]) == ("Asha Rao", "Verified (Assigned By)")

    # The named person assigned this lead before, so the field probably predates the shared-login change.
    stale = acts + [assigned(0, "2026-09-01 05:00:00", "", "Ravi Das", "Asha Rao")]
    r = lead_actions(fresh, stale, PEOPLE, D0, D1, NOW)[0]
    assert (r["action_performed_by"], r["accountability_status"], r["possible_actor"]) == ("Unverified", "Unverified", "Asha Rao")

    # Assigned On older than the change, a blank field, or a field naming the shared account: all Unverified.
    for l in (lead(mx_Assigned_By="Asha Rao", mx_Assigned_On="2026-10-01 00:00:00"), lead(),
              lead(mx_Assigned_By="Rinku Jhala", mx_Assigned_On="2026-10-07 05:00:30")):
        assert lead_actions(l, acts, PEOPLE, D0, D1, NOW)[0]["accountability_status"] == "Unverified"


def test_automation_and_calls_that_ring_the_shared_account():
    acts = [assigned(1, "2026-10-05 05:00:00", "Asha Rao", "Ravi Das", "System"),
            call(1, "2026-10-06 05:00:00", False, "Ravi Das", "Answered"),
            call(2, "2026-10-07 05:00:00", True, "Rinku Jhala", "Missed")]
    rows = lead_actions(lead(OwnerIdName="Ravi Das"), acts, PEOPLE, D0, D1, NOW)
    assert [(r["action"], r["accountability_status"]) for r in rows] == [
        ("transfer", "Automated"), ("inbound call missed", "Unverified")]
    assert rows[1]["possible_actor"] == "Ravi Das" and rows[1]["action_performed_by"] == "Unverified"


def test_missed_follow_up_goes_to_the_owner_at_the_due_time():
    acts = [assigned(1, "2026-10-01 05:00:00", "", "Asha Rao", "Kanishka"),
            assigned(2, "2026-10-08 05:00:00", "Asha Rao", "Rinku Jhala", "Kanishka")]
    r = lead_actions(lead(mx_Next_follow_up_date="2026-10-06 06:00:00"), acts, PEOPLE, D0, D1, NOW)[-1]
    assert (r["action"], r["action_performed_by"], r["accountability_status"]) == ("follow-up missed", "Asha Rao", "Verified")

    acts.append(call(1, "2026-10-08 06:00:00", False, "Ravi Das", "NotAnswered"))
    r = lead_actions(lead(mx_Next_follow_up_date="2026-10-09 06:00:00"), acts, PEOPLE, D0, D1, NOW)[-1]
    assert (r["action"], r["accountability_status"], r["possible_actor"]) == ("follow-up missed", "Unverified", "Ravi Das")

    acts.append(call(2, "2026-10-09 07:30:00", False, "Ravi Das", "Answered"))
    r = lead_actions(lead(mx_Next_follow_up_date="2026-10-09 06:00:00"), acts, PEOPLE, D0, D1, NOW)[-1]
    assert r["action"] == "follow-up done by Ravi Das"


def test_lead_origin_and_creation_through_a_shared_login():
    l = lead(CreatedOn="2026-10-06 05:00:00", CreatedByName="Rinku Jhala")
    o = lead_origin(l, [], PEOPLE)
    assert (o["how"], o["put_there_by"], o["accountability_status"]) == ("created there", "Unverified", "Unverified")
    r = lead_actions(l, [], PEOPLE, D0, D1, NOW)[0]
    assert (r["action"], r["action_performed_by"]) == ("lead created", "Unverified")


def test_admin_is_never_made_accountable_and_corrections_are_audited(tmp_path):
    leads = [lead(ProspectID="L1"), lead(ProspectID="L2", OwnerIdName="Asha Rao")]
    hist = {"L1": [assigned(1, "2026-10-07 05:00:00", "Ravi Das", "Rinku Jhala", "Rinku Jhala"),
                   call(1, "2026-10-07 06:00:00", True, "Rinku Jhala", "Missed"),
                   {"Id": "S1", "EventName": "StageChange", "CreatedOn": "2026-10-07 07:00:00",
                    "Data": [{"Key": "CurrentStage", "Value": "Call Back Later"}, {"Key": "CreatedBy", "Value": "Rinku Jhala"}]}],
            "L2": [assigned(2, "2026-10-08 05:00:00", "Rinku Jhala", "Asha Rao", "Admin")]}
    audit = tmp_path / "audit.jsonl"
    s = run(leads, hist, USERS, "2026-10-04", "2026-10-09", str(tmp_path / "out"), audit=str(audit), now=NOW)
    rows = list(csv.DictReader(open(tmp_path / "out" / "actions.csv")))
    assert rows and not any(r["action_performed_by"] in ("Rinku Jhala", "Admin") for r in rows)
    assert s["by_status"] == {"Unverified": 4} and s["audit"] == {"first seen": 4}
    assert s["put_in_account_by"] == {"Rinku Jhala": {"Unverified (Rinku Jhala login)": 1}}

    fix = [{"key": "assign:A1", "performed_by": "Ravi Das", "confirmed_by": "Kanishka", "note": "TL checked"}]
    s = run(leads, hist, USERS, "2026-10-04", "2026-10-09", str(tmp_path / "out"), corrections=fix, audit=str(audit), now=NOW)
    assert s["audit"] == {"re-attributed": 1}
    entries = [json.loads(x) for x in open(audit)]
    assert len(entries) == 5   # nothing rewritten, one entry added
    last = entries[-1]
    assert (last["performed_by"], last["status"], last["previous"]["performed_by"]) == ("Ravi Das", "Corrected", "Unverified")
    rows = list(csv.DictReader(open(tmp_path / "out" / "actions.csv")))
    fixed = next(r for r in rows if r["key"] == "assign:A1")
    assert fixed["transferred_by"] == "Ravi Das" and "confirmed by Kanishka" in fixed["evidence"]
