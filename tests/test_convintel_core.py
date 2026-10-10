import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from analytics.convintel import schema as S
from analytics.convintel.analyze import save_findings
from analytics.convintel.attribution import Directory
from analytics.convintel.classify import classify, parse_duration
from analytics.convintel.fetch import match, process_number, run_fetch, source_id
from analytics.convintel.inventory import TEAM_SOURCE, from_activity, inventory, restamp_teams, window_bounds
from analytics.convintel.reconcile import reconcile
from analytics.convintel.store import Registry, derive_status, ts
from integrations.transcripts.client import Call, TranscriptError

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "Rao", "MemberOfGroups": ["Team Alpha +Neel"]},
         {"ID": "u2", "FirstName": "Ravi", "LastName": "Iyer", "MemberOfGroups": ["Team Beta"]},
         {"ID": "rj", "FirstName": "Rinku", "LastName": "Jhala", "MemberOfGroups": ["Admins"]}]


def act(i, start, status="Answered", dur="200", user="u1", caller="Asha Rao", number="9000000001", inbound=False, lead="L1"):
    src = json.dumps({"SourceNumber" if inbound else "DestinationNumber": number})
    note = "{next}".join(f"{k}{{=}}{v}" for k, v in [("Status", status), ("Duration", dur), ("UserId", user),
                                                     ("Caller", caller), ("DisplayNumber", "8000000001"), ("SourceData", src)]
                                if v is not None)
    return {"ProspectActivityId": f"c{i}", "RelatedProspectId": lead, "ActivityEvent": 21 if inbound else 22,
            "CreatedOn": start, "ModifiedOn": start, "ActivityEvent_Note": note}


def rec(i, start, status="Answered", dur=200, number="919000000001", lead="L1", user="u1"):
    return from_activity(act(i, start, status, None if dur is None else str(dur), user=user,
                             caller={"u1": "Asha Rao", "u2": "Ravi Iyer", "rj": "Rinku Jhala"}[user],
                             number=number[-10:], lead=lead), Directory(USERS))


@pytest.fixture
def reg():
    r = Registry(":memory:")
    yield r
    r.close()


# ------------------------------------------------------------------ requirement 02: the 3-minute rule

@pytest.mark.parametrize("status,dur,cls", [
    ("Answered", 179, S.SHORT_CALL), ("Answered", 180, S.REAL_CALL), ("Answered", 181, S.REAL_CALL),
    ("NotAnswered", 0, S.NOT_CONNECTED), ("Missed", 30, S.NOT_CONNECTED), ("CallFailure", None, S.NOT_CONNECTED),
    ("Answered", None, S.UNKNOWN), ("Answered", 0, S.UNKNOWN), ("Answered", 5 * 3600, S.UNKNOWN), (None, 300, S.UNKNOWN),
    ("Weird", 300, S.UNKNOWN)])
def test_classify_acceptance(status, dur, cls):
    assert classify(status, dur)[0] == cls


def test_parse_duration_keeps_unknown_apart_from_zero():
    assert parse_duration("") is None and parse_duration(None) is None and parse_duration("abc") is None
    assert parse_duration("0") == 0 and parse_duration("181.6") == 181


def test_inventory_record_from_raw_activity_keeps_raw_duration_and_people():
    d = Directory(USERS)
    r = from_activity(act(1, "2026-10-05 08:00:00", dur="", number="+91 90000 00001"), d)
    assert r["call_class"] == S.UNKNOWN and r["duration_s"] is None and r["duration_raw"] == ""
    assert r["number"] == "919000000001" and r["ist_day"] == "2026-10-05" and r["team"] == "Team Alpha +Neel"
    assert (r["caller_kind"], r["answered"], r["caller_number"]) == ("person", 1, "8000000001")
    shared = from_activity(act(2, "2026-10-05 08:00:00", user="rj", caller="Rinku Jhala"), d)
    assert shared["caller_kind"] == "shared"
    bot = from_activity(act(3, "2026-10-05 08:00:00", user="x", caller="System"), d)
    assert bot["caller_kind"] == "bot" and bot["team"] == "Not a user"


def test_window_bounds_are_ist():
    a, b = window_bounds("2026-10-05", "14:00-14:30")
    assert ts(a) == "2026-10-05 08:30:00" and ts(b) == "2026-10-05 08:59:59"


# ------------------------------------------------------------------ registry and statuses

def test_upsert_is_idempotent_and_never_overwrites_stamped_team(reg):
    r = rec(1, "2026-10-05 05:00:00")
    assert reg.upsert_calls([r], NOW)["new"] == 1
    assert reg.upsert_calls([r], NOW)["unchanged"] == 1
    moved = {**r, "team": "Team Beta", "duration_s": 240, "duration_raw": "240"}
    assert reg.upsert_calls([moved], NOW)["changed"] == 1
    c = reg.call("c1")
    assert c["team"] == "Team Alpha +Neel" and c["duration_s"] == 240


def test_team_is_the_first_group_that_is_not_calling_software():
    d = Directory(USERS + [
        {"ID": "u4", "FirstName": "Meena", "LastName": "Das", "MemberOfGroups": ["Acefone Users", "Team Beta", "Team Alpha"]},
        {"ID": "u5", "FirstName": "Kiran", "LastName": "Pal", "MemberOfGroups": ["New Joinees - Mcube", " "]}])
    assert d.person("u4")["team"] == "Team Beta" and d.person("u5")["team"] == "Unassigned"
    assert d.person("u1")["team"] == "Team Alpha +Neel"


def test_only_calls_the_old_team_rule_got_wrong_move_and_only_once(reg):
    reg.upsert_calls([
        {**rec(1, "2026-10-05 05:00:00"), "caller_id": "u4", "caller_name": "Meena Das", "team": "Mcube Users"},
        rec(2, "2026-10-05 05:10:00"),                                  # a sales team: the same under both rules
        {**rec(3, "2026-10-05 05:20:00"), "caller_id": "gone", "caller_name": "Left Company", "team": "Acefone Users"},
        {**rec(4, "2026-10-05 05:30:00"), "caller_id": None, "caller_name": "(a phone number, not a LeadSquared user)",
         "caller_kind": "not_a_user", "team": "Not a user"},
        {**rec(6, "2026-10-05 05:40:00"), "caller_id": "x", "caller_name": "System", "caller_kind": "bot",
         "team": "Not a user"},
        {**rec(7, "2026-10-05 05:50:00", user="u2"), "team": "Unassigned"}], NOW)     # first group was blank
    reg.db.execute("UPDATE transcript_coverage_registry SET team_source = ?", ("caller's LeadSquared group when inventoried",))
    save_findings(reg, reg.call("c1"), S.SEMANTIC, [{"category": "other", "reasoning": "x"}], NOW)
    users = [{"ID": "u1", "FirstName": "Asha", "LastName": "Rao", "MemberOfGroups": ["Team Beta"]},   # moved since
             {"ID": "u2", "FirstName": "Ravi", "LastName": "Iyer", "MemberOfGroups": ["", "Team Beta"]},
             {"ID": "u4", "FirstName": "Meena", "LastName": "Das", "MemberOfGroups": ["Mcube Users", "Team Beta"]}]

    assert restamp_teams(reg, Directory(users), NOW) == 3
    assert [reg.call(f"c{i}")["team"] for i in (1, 2, 3, 4, 6, 7)] == [
        "Team Beta", "Team Alpha +Neel", "Unassigned", "Not a user", "Not a user", "Team Beta"]
    assert reg.q("SELECT team FROM conversation_quality_findings WHERE call_id = 'c1'") == [{"team": "Team Beta"}]
    assert {reg.call(f"c{i}")["team_source"] for i in (1, 2, 3, 4, 6, 7)} == {TEAM_SOURCE}
    users[2]["MemberOfGroups"] = ["Mcube Users", "Team Gamma"]
    assert restamp_teams(reg, Directory(users), NOW) == 0                     # once only: later moves change nothing
    assert reg.call("c1")["team"] == "Team Beta"
    assert reg.q("SELECT team FROM conversation_quality_findings WHERE call_id = 'c1'") == [{"team": "Team Beta"}]
    assert rec(5, "2026-10-05 06:00:00")["team_source"] == TEAM_SOURCE       # new calls carry today's rule


def test_not_connected_call_becomes_expected_when_it_turns_out_answered(reg):
    reg.upsert_calls([rec(1, "2026-10-05 05:00:00", status="NotAnswered", dur=0)], NOW)
    reg.refresh(None, NOW)
    assert reg.call("c1")["analysis_status"] == S.NO_TRANSCRIPT_EXPECTED
    reg.upsert_calls([rec(1, "2026-10-05 05:00:00", status="Answered", dur=300)], NOW)
    reg.refresh(None, NOW)
    c = reg.call("c1")
    assert c["transcript_expected"] == 1 and c["transcript_state"] == S.T_NOT_LOOKED_UP
    assert c["analysis_status"] == S.PENDING_ANALYSIS


def test_call_without_number_is_not_found_with_reason(reg):
    reg.upsert_calls([{**rec(1, "2026-10-05 05:00:00"), "number": None, "lead_number": None}], NOW)
    reg.refresh(None, NOW)
    c = reg.call("c1")
    assert c["analysis_status"] == S.TRANSCRIPT_NOT_FOUND and "no lead number" in c["status_reason"]


def test_derive_status_every_state():
    found = {"transcript_state": S.T_FOUND, "transcript_expected": 1}
    kw = {"layer": "keyword", "version": "kw-1.0", "state": "done"}     # left in old registries by the removed rules
    sem = {"layer": S.SEMANTIC, "version": S.SEMANTIC_VERSION, "state": "done"}
    assert S.REQUIRED_LAYERS == (S.SEMANTIC,)                          # Claude's reading is the analysis
    assert derive_status(found, {}, NOW)[0] == S.PENDING_ANALYSIS
    assert derive_status(found, {S.SEMANTIC: sem}, NOW)[0] == S.ANALYZED
    st, missing, why = derive_status(found, {"keyword": kw}, NOW)     # an old keyword result changes nothing
    assert (st, missing) == (S.PENDING_ANALYSIS, [S.SEMANTIC]) and "Claude reading" in why
    old = {**sem, "version": "sem-0.9"}
    assert derive_status(found, {S.SEMANTIC: old}, NOW)[0] == S.PENDING_ANALYSIS   # version bump re-queues
    running = {**sem, "state": "in_progress", "claimed_utc": ts(NOW - timedelta(minutes=5))}
    assert derive_status(found, {S.SEMANTIC: running}, NOW)[0] == S.ANALYSIS_IN_PROGRESS
    stale = {**running, "claimed_utc": ts(NOW - timedelta(hours=2))}
    assert derive_status(found, {S.SEMANTIC: stale}, NOW)[0] == S.PENDING_ANALYSIS
    batched = {**sem, "state": "batched", "batch_id": "r1", "claimed_utc": ts(NOW - timedelta(hours=10))}
    st, _, why = derive_status(found, {S.SEMANTIC: batched}, NOW)
    assert st == S.ANALYSIS_IN_PROGRESS and "handed out in round r1" in why
    assert derive_status(found, {S.SEMANTIC: {**batched, "claimed_utc": ts(NOW - timedelta(hours=26))}},
                         NOW)[0] == S.PENDING_ANALYSIS                 # an unread round is handed out again
    failed = {**sem, "state": "failed", "error": "boom"}
    assert derive_status(found, {S.SEMANTIC: failed}, NOW)[0] == S.ANALYSIS_FAILED
    part = {**sem, "state": "incomplete", "missing": '["coaching"]'}
    st, _, why = derive_status(found, {S.SEMANTIC: part}, NOW)
    assert st == S.ANALYSIS_INCOMPLETE and "Claude reading missing coaching" in why
    both = (S.SEMANTIC, "other")                                        # the rules still work for several layers
    st, missing, why = derive_status(found, {S.SEMANTIC: sem}, NOW, required=both)
    assert (st, missing) == (S.ANALYSIS_INCOMPLETE, ["other"]) and "not done yet" in why
    assert derive_status({"transcript_state": S.T_NOT_EXPECTED, "transcript_expected": 0}, {}, NOW)[0] == S.NO_TRANSCRIPT_EXPECTED
    assert derive_status({"transcript_state": S.T_NOT_LOOKED_UP, "transcript_expected": 1}, {}, NOW)[0] == S.PENDING_ANALYSIS
    assert derive_status({"transcript_state": S.T_LOOKUP_FAILED, "transcript_expected": 1}, {}, NOW)[0] == S.PENDING_ANALYSIS
    for t in (S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED, S.T_NO_NUMBER):
        assert derive_status({"transcript_state": t, "transcript_expected": 1}, {}, NOW)[0] == S.TRANSCRIPT_NOT_FOUND


def test_due_numbers_fresh_newest_first_then_rechecks_and_lag(reg):
    reg.upsert_calls([rec(1, "2026-10-09 06:00:00", number="919000000001"),
                      rec(2, "2026-10-09 09:00:00", number="919000000002"),
                      rec(3, "2026-10-09 11:50:00", number="919000000003")], NOW)   # 10 min old: too soon
    assert reg.due_numbers(NOW, 10) == ["919000000002", "919000000001"]
    reg.set_transcript("c2", S.T_NOT_FOUND, NOW)
    assert reg.due_numbers(NOW, 10) == ["919000000001"]
    assert reg.call("c2")["next_lookup_utc"] == "2026-10-10 09:00:00"  # the 2 h recheck has passed: next is 24 h
    assert "919000000002" in reg.due_numbers(NOW + timedelta(hours=22), 10)


def test_recheck_schedule_ends_after_seven_days(reg):
    reg.upsert_calls([rec(1, "2026-10-01 06:00:00")], NOW)
    reg.set_transcript("c1", S.T_NOT_FOUND, NOW)                        # 8 days after the call: no more rechecks
    c = reg.call("c1")
    assert c["next_lookup_utc"] is None and "no more automatic rechecks" in c["lookup_note"]
    assert reg.requeue_not_found(NOW) == 1 and reg.call("c1")["next_lookup_utc"] == ts(NOW)


def test_coverage_partitions_every_call(reg):
    reg.upsert_calls([rec(1, "2026-10-05 05:00:00"), rec(2, "2026-10-05 05:10:00", status="NotAnswered", dur=0),
                      rec(3, "2026-10-05 05:20:00", dur=60)], NOW)
    reg.refresh(None, NOW)
    cov = reg.coverage("2026-10-05", "2026-10-05")
    assert cov["total_calls"] == 3 and cov["expected_transcripts"] == 2 and cov["analyzed"] == 0
    assert cov["coverage_pct"] == 0.0 and cov["reconciles"]
    assert cov["by_class"] == {S.REAL_CALL: 1, S.SHORT_CALL: 1, S.NOT_CONNECTED: 1, S.UNKNOWN: 0}


# ------------------------------------------------------------------ inventory from LeadSquared

class FakeLSQ:
    def __init__(self, acts):
        self.acts = acts

    def get_users(self):
        return USERS

    def iter_activities_started(self, ev, d0, d1, now=None):
        for a in self.acts:
            t = datetime.strptime(a["CreatedOn"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if a["ActivityEvent"] == ev and d0 <= t <= d1:
                yield a


def test_inventory_records_source_counts_and_reconciles(reg):
    acts = [act(1, "2026-10-04 19:00:00"), act(2, "2026-10-05 05:00:00", status="NotAnswered", dur="12"),
            act(3, "2026-10-05 06:00:00", inbound=True, status="Missed", dur="0"), act(4, "2026-10-06 05:00:00")]
    got = inventory(reg, FakeLSQ(acts), "2026-10-05", "2026-10-05", NOW)
    assert got["new"] == 3 and reg.get_meta("source:2026-10-05")["calls"] == 3   # 19:00 UTC on the 4th is the 5th in IST
    checks = {c["check"]: c for c in reconcile(reg, "2026-10-05", "2026-10-05", NOW)}
    assert checks["registry matches LeadSquared's call count"]["ok"]
    assert not checks["100% coverage confirmed"]["ok"]
    acts.pop(0)                                                         # deleted in LeadSquared
    inventory(reg, FakeLSQ(acts), "2026-10-05", "2026-10-05", NOW + timedelta(hours=1))
    checks = {c["check"]: c for c in reconcile(reg, "2026-10-05", "2026-10-05", NOW + timedelta(hours=1))}
    assert not checks["registry matches LeadSquared's call count"]["ok"]
    assert "not in the latest read" in checks["registry matches LeadSquared's call count"]["detail"]


# ------------------------------------------------------------------ transcript search

def api_call(start, dur=200, text="hello this is a synthetic call about the course fees", kind="sales", audio="a1",
             number="919000000001"):
    return Call(phone=number, kind=kind, caller_id=None, agent_name="Asha", start_time=start, end_time=None,
                duration=dur, transcript=text, transcript_url=None, audio_url=f"https://example.invalid/{audio}.mp3")


def test_match_is_one_to_one_closest_first():
    t = datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc)
    calls = [{"call_id": "c1", "start_utc": ts(t), "duration_s": 200},
             {"call_id": "c2", "start_utc": ts(t + timedelta(minutes=3)), "duration_s": 200}]
    recs = [api_call(t + timedelta(minutes=2), audio="x")]
    pairs = match(calls, recs)
    assert [p[0]["call_id"] for p in pairs] == ["c2"]                  # 1 min beats 2 min; c1 gets nothing


def test_process_number_found_not_transcribed_not_found_and_orphans(reg, tmp_path):
    reg.upsert_calls([rec(1, "2026-10-05 05:00:00"), rec(2, "2026-10-05 07:00:00"), rec(3, "2026-10-05 09:00:00"),
                      rec(4, "2026-10-09 11:55:00")], NOW)
    day = (datetime(2026, 10, 4, 18, 30, tzinfo=timezone.utc), datetime(2026, 10, 5, 18, 29, 59, tzinfo=timezone.utc))
    t = datetime(2026, 10, 5, 5, 1, tzinfo=timezone.utc)
    recs = [api_call(t), api_call(t + timedelta(hours=2), text="", audio="b"),
            api_call(t + timedelta(hours=6), audio="orphan")]
    recs.append(api_call(t - timedelta(days=3), audio="older"))       # outside what was inventoried: not an orphan
    out = process_number(reg, "919000000001", recs, NOW, [day], base=str(tmp_path))
    assert (out["found"], out["not_transcribed"], out["not_found"], out["orphans"], out["too_recent"]) == (1, 1, 1, 1, 1)
    c1 = reg.call("c1")
    assert c1["transcript_state"] == S.T_FOUND and os.path.exists(c1["transcript_ref"]) and c1["transcript_words"] == 10
    assert c1["analysis_status"] == S.PENDING_ANALYSIS
    assert reg.call("c2")["analysis_status"] == S.TRANSCRIPT_NOT_FOUND and reg.call("c2")["next_lookup_utc"]
    assert reg.call("c4")["transcript_state"] == S.T_NOT_LOOKED_UP
    # a later search does not hand c1's recording to another call
    again = process_number(reg, "919000000001", recs, NOW + timedelta(hours=3), [day], base=str(tmp_path))
    assert again["found"] == 0 and reg.call("c1")["transcript_source_id"] == source_id(recs[0])


class FakeTx:
    def __init__(self, results, fail_at=None, status=429):
        self.results, self.fail_at, self.status = results, fail_at, status
        self.max_requests, self.batch_size, self.requests_made = 9, 10, 0

    @property
    def requests_remaining(self):
        return self.max_requests - self.requests_made

    def search_raw(self, numbers):
        self.requests_made += 1
        if self.fail_at == self.requests_made:
            raise TranscriptError("rate limited", status_code=self.status)
        return {n: self.results.get(n, {}) for n in numbers}


def test_run_fetch_paces_and_stops_on_rate_limit_leaving_the_rest_queued(reg, tmp_path):
    reg.upsert_calls([rec(i, "2026-10-05 05:00:00", number=f"9190000{i:05d}") for i in range(25)], NOW)
    sleeps = []
    out = run_fetch(reg, NOW, client=FakeTx({}, fail_at=2), sleep=sleeps.append, base=str(tmp_path))
    assert out["rate_limited"] == 1 and out["requests"] == 2 and sleeps == [7.0]
    states = [c["transcript_state"] for c in reg.q("SELECT transcript_state FROM transcript_coverage_registry")]
    assert states.count(S.T_NOT_FOUND) == 10 and states.count(S.T_LOOKUP_FAILED) == 10 and states.count(S.T_NOT_LOOKED_UP) == 5
    assert sum(reg.coverage()["by_status"].values()) == 25


def test_run_fetch_never_exceeds_nine_requests(reg, tmp_path):
    reg.upsert_calls([rec(i, "2026-10-05 05:00:00", number=f"9190000{i:05d}") for i in range(120)], NOW)
    tx = FakeTx({})
    out = run_fetch(reg, NOW, client=tx, sleep=lambda s: None, base=str(tmp_path))
    assert tx.requests_made == 9 and out["numbers"] == 90


# ------------------------------------------------------------------ analysis runner

def test_inventory_reads_a_block_once_and_counts_each_day(reg):
    acts = [act(1, "2026-10-04 19:00:00"), act(2, "2026-10-05 20:00:00"), act(3, "2026-10-06 10:00:00")]
    lsq = FakeLSQ(acts)
    reads = []
    real = lsq.iter_activities_started
    lsq.iter_activities_started = lambda ev, d0, d1, now=None: (reads.append((ev, d0, d1)), real(ev, d0, d1, now))[1]
    inventory(reg, lsq, "2026-10-05", "2026-10-06", NOW)
    assert len(reads) == 2                                              # one read per event for the whole block
    assert reg.get_meta("source:2026-10-05")["calls"] == 1 and reg.get_meta("source:2026-10-06")["calls"] == 2
