from analytics.lost_leads import lost_leads


def lead(lid, stage, owner="A", created="2026-08-01 05:00:00", modified="2026-10-05 05:00:00"):
    return {"ProspectID": lid, "ProspectStage": stage, "OwnerIdName": owner, "CreatedOn": created, "ModifiedOn": modified}


def call(lid, t, dur=200, status="Answered"):
    return {"lead_id": lid, "start_utc": t, "duration": dur, "status": status}


SNAP = {
    "fetched_at": "2026-10-08T00:00:00+00:00", "start": "2026-09-01",
    "leads": [
        lead("old_talk", "Call Back Later"),                 # talked 20 days ago: stale
        lead("closure", "Follow Up For Closure"),            # never talked: stale, sorts first (closest stage)
        lead("recent", "Discovery Call Done"),               # talked 3 days ago: not stale
        lead("fresh", "Call Back Later", created="2026-10-06 05:00:00"),  # new lead: fresh-lead tier, not stale
        lead("ni", "Not Interested"),                        # 30 s call only: recover
        lead("inv", "Invalid lead"),                         # no answered call: check
        lead("ni_ok", "Not Interested"),                     # had a real conversation: fine
        lead("dup", "Duplicate"),
        lead("ni_old", "Not Interested", modified="2026-08-01 05:00:00"),  # marked before the snapshot
    ],
    "calls": [call("old_talk", "2026-09-18 05:00:00"), call("recent", "2026-10-05 05:00:00"),
              call("ni", "2026-10-04 05:00:00", dur=30), call("ni_ok", "2026-10-04 05:00:00")],
}


def test_stale_open_stage_leads():
    r = lost_leads(SNAP, days=15, cap=1)
    assert [x["lead_id"] for x in r["stale_open"]] == ["closure", "old_talk"]
    assert [x["over_cap"] for x in r["stale_open"]] == [False, True]
    assert r["summary"]["stale_open_within_cap"] == 1 and r["summary"]["warning"] is None


def test_dead_without_conversation():
    r = lost_leads(SNAP)
    assert {x["lead_id"]: x["action"] for x in r["dead_no_conversation"]} == {
        "ni": "recover: call again", "inv": "check: no answered call"}


def test_warns_when_snapshot_is_shorter_than_the_window():
    assert "less than 15 days" in lost_leads({**SNAP, "start": "2026-10-01"})["summary"]["warning"]
