"""The report export end to end on a synthetic registry: snapshot shape, privacy, flagged-call rows and the
registry tables it fills. No network, nothing under data/."""

import json
from datetime import datetime, timezone
from pathlib import Path

from analytics.convintel import export, integrity
from analytics.convintel import schema as S
from analytics.convintel.analyze import keyword_pass
from analytics.convintel.attribution import Directory
from analytics.convintel.fetch import save_text
from analytics.convintel.inventory import from_activity
from analytics.convintel.store import Registry

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "convintel_snapshot.json"
USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "Rao", "MemberOfGroups": ["Team Alpha +Neel"]},
         {"ID": "u2", "FirstName": "Ravi", "LastName": "Iyer", "MemberOfGroups": ["Team Beta"]},
         {"ID": "rj", "FirstName": "Rinku", "LastName": "Jhala", "MemberOfGroups": ["Admins"]}]
NAMES = {"u1": "Asha Rao", "u2": "Ravi Iyer", "rj": "Rinku Jhala"}
LINE = "8000000001"
TEXT = ("Hello sir, fees kitni hai? EMI ho jayega? I want to enrol, please send the payment link today. "
        "Theek hai, kal 11 baje call karna. Course start date kya hai? ") * 6


def act(i, start, status, dur, user, number, lead):
    src = json.dumps({"DestinationNumber": number})
    note = "{next}".join(f"{k}{{=}}{v}" for k, v in [("Status", status), ("Duration", dur), ("UserId", user),
                                                     ("Caller", NAMES[user]), ("DisplayNumber", LINE),
                                                     ("SourceData", src)])
    return {"ProspectActivityId": f"c{i}", "RelatedProspectId": lead, "ActivityEvent": 22, "CreatedOn": start,
            "ModifiedOn": start, "ActivityEvent_Note": note}


# (call, start UTC, status, seconds, user, lead number, lead)
CALLS = [(1, "2026-10-07 05:00:00", "Answered", "400", "u1", "9000000001", "L1"),
         (2, "2026-10-07 05:30:00", "Answered", "95", "u1", "9000000002", "L2"),
         (3, "2026-10-07 06:00:00", "NotAnswered", "0", "u2", "9000000003", "L3"),
         (4, "2026-10-08 05:00:00", "Answered", "250", "u2", "9000000004", "L4"),
         (5, "2026-10-08 05:30:00", "Answered", "300", "rj", "9000000005", "L5"),
         (6, "2026-10-08 06:00:00", "Answered", "179", "u2", "9000000006", "L6")]


def build(tmp_path):
    reg = Registry(str(tmp_path / "registry.sqlite"))
    d = Directory(USERS)
    reg.upsert_calls([from_activity(act(*c), d) for c in CALLS], NOW)
    for cid in ("c1", "c4", "c5"):
        path, sha, words = save_text(reg.call(cid), TEXT, str(tmp_path / "tx"))
        reg.set_transcript(cid, S.T_FOUND, NOW, transcript_ref=path, transcript_sha256=sha, transcript_words=words,
                           transcript_source_id=f"s-{cid}")
    reg.set_transcript("c2", S.T_NOT_TRANSCRIBED, NOW, transcript_ref=None, transcript_source_id="s-c2",
                       lookup_note="the recording exists but has no transcript text yet")
    reg.refresh(None, NOW)
    keyword_pass(reg, NOW)
    leads = {f"L{n}": {"stage": "Prospect", "course": "Diploma in Contract Drafting", "owner_id": "u1",
                       "owner_name": "Asha Rao", "score": 40} for n in range(1, 7)}
    enrol = [{"lead_id": "L4", "at_utc": "2026-10-08 09:00:00", "ist_day": "2026-10-08", "owner_id": "u2",
              "owner_name": "Ravi Iyer", "set_by": "u2"}]
    ds = export.dataset(reg, "2026-10-07", "2026-10-08", USERS, leads, enrol, [], [], NOW)
    return reg, ds


def test_snapshot_shape_matches_the_dashboard_fixture(tmp_path):
    reg, ds = build(tmp_path)
    snap = export.snapshot(ds, reg, "2d", "Two days")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(snap) == set(fixture)
    for part in ("org", "coverage", "integrity", "revenue", "zip", "accountability", "definitions", "filters"):
        assert set(fixture[part]) <= set(snap[part]), part
    for rows in ("teams", "callers", "leads", "calls"):
        assert snap[rows], rows
        assert set(fixture[rows][0]) <= set(snap[rows][0]) | {"vsOrg", "vsTeam", "prior"}, rows
    assert all("coaching" in c and "integrity" in c for c in snap["callers"])
    assert snap["definitions"]["realCallSecs"] == 180


def test_snapshot_has_no_phone_numbers_or_transcript_text(tmp_path):
    reg, ds = build(tmp_path)
    body = json.dumps(export.snapshot(ds, reg, "2d", "Two days"))
    for _, *_, number, _ in CALLS:
        assert number not in body
    assert LINE not in body
    assert "fees kitni hai" not in body


def test_flagged_calls_list_caller_line_and_number_dialled(tmp_path):
    reg, ds = build(tmp_path)
    export.snapshot(ds, reg, "2d", "Two days")
    rows = {r["callId"]: r for r in integrity.flag_rows(ds["calls"], {c["call_id"]: c["flags"] for c in ds["calls"]})}
    short_empty = rows["c2"]
    assert {"under_3_min", "empty_transcript"} <= set(short_empty["flags"])
    assert (short_empty["caller"], short_empty["callerNumber"], short_empty["leadNumber"]) == (
        "Asha Rao", LINE, "919000000002")
    assert "under_3_min" in rows["c6"]["flags"] and rows["c6"]["leadNumber"] == "919000000006"
    assert "c3" not in rows          # not connected: nothing to flag


def test_past_period_says_later_follow_ups_are_not_seen(tmp_path):
    reg, ds = build(tmp_path)
    notes = export.snapshot(ds, reg, "2d", "Two days")["definitions"]["notes"]
    assert any("after this period's last day" in n for n in notes)


def test_persist_fills_the_registry_tables_and_replaces_on_rerun(tmp_path):
    reg, ds = build(tmp_path)
    actions = [{"key": "k1", "lead_id": "L1", "action": "transfer", "action_time_ist": "2026-10-07 11:00",
                "account_owner": "Asha Rao", "assigned_to": "Asha Rao", "assigned_by": "Rinku Jhala",
                "performed_by": "Rinku Jhala", "status": "Unverified", "evidence": "shared login"}]
    derived: dict = {}
    export.snapshot(ds, reg, "2d", "Two days", accountability_rows=actions, derived=derived)
    first = export.persist(reg, ds, derived, NOW)
    again = export.persist(reg, ds, derived, NOW)
    assert first == again
    count = lambda t: reg.q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]  # noqa: E731
    assert count("team_conversation_aggregates") == first["team_conversation_aggregates"] > 0
    assert count("caller_coaching_insights") == first["caller_coaching_insights"]
    assert count("lead_accountability_findings") == 1
    assert count("lead_cross_call") == first["lead_cross_call"] > 0
    acc = reg.q("SELECT * FROM lead_accountability_findings")[0]
    assert acc["responsible_person"] is None and acc["status"] == "UNVERIFIED"
    real = reg.q("SELECT value, denominator FROM team_conversation_aggregates WHERE team = ? AND metric = ?",
                 (export.ORG_ROW, "realRatePct"))[0]
    assert real["denominator"] == "connected calls"


def test_read_audit(tmp_path):
    assert export.read_audit(str(tmp_path)) is None
    (tmp_path / "audit.jsonl").write_text('{"key": "k1", "previous": {"performed_by": "X"}}\n\n', encoding="utf-8")
    assert export.read_audit(str(tmp_path)) == [{"key": "k1", "previous": {"performed_by": "X"}}]


def test_lead_rows_get_the_owners_team(tmp_path):
    leads = export.lead_teams({"L1": {"owner_id": "u2", "owner_name": "Ravi Iyer"},
                               "L2": {"owner_id": "rj", "owner_name": "Rinku Jhala"}}, USERS)
    assert leads["L1"]["team"] == "Team Beta"
    assert leads["L2"].get("team") is None       # a shared login's group is not a team


def test_shared_login_calls_show_under_their_pseudo_team(tmp_path):
    reg, ds = build(tmp_path)
    snap = export.snapshot(ds, reg, "2d", "Two days")
    teams = {t["team"] for t in snap["teams"]}
    assert {c["team"] for c in snap["calls"]} <= teams
    assert {r["team"] for r in snap["coverage"]["byTeam"]} <= teams


def test_reports_only_go_under_data_or_exports(tmp_path):
    assert export.private_dir("data/convintel/reports/7d")
    assert export.private_dir("exports/x")
    assert not export.private_dir(str(tmp_path))
    assert not export.private_dir("data_copy/x")


def test_a_caller_named_by_phone_number_is_not_shown(tmp_path):
    reg = Registry(str(tmp_path / "registry.sqlite"))
    rec = from_activity(act(9, "2026-10-08 07:00:00", "Answered", "200", "u1", "9000000009", "L9"), Directory(USERS))
    rec.update(caller_id="x9", caller_name="+91-9876500000", caller_kind="not_a_user", team="Not a user")
    reg.upsert_calls([rec], NOW)
    ds = export.dataset(reg, "2026-10-08", "2026-10-08", USERS, {}, [], [], [], NOW)
    assert "98765" not in json.dumps(export.snapshot(ds, reg, "1d", "One day"))
    assert Directory(USERS).person("zz", "+91 98765 00000")["name"] == "(a phone number, not a LeadSquared user)"


def test_snapshot_says_when_the_model_layer_has_not_run(tmp_path):
    reg, ds = build(tmp_path)
    assert export.snapshot(ds, reg, "2d", "Two days")["definitions"]["semanticEngine"].startswith("not run yet")
