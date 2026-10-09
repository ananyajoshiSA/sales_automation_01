import csv
import json
from datetime import datetime, timedelta, timezone

import pytest

from analytics.convintel import schema as S
from analytics.convintel.classify import classify
from analytics.convintel.coaching import (ACTIONS, DIM_ACTION, SEM_BASIS, call_coaching, caller_coaching,
                                          team_coaching, weekly_sample)
from analytics.convintel.crosscall import lead_journeys, trend
from analytics.convintel.integrity import (CALL_IDS_CAP, ROW_COLUMNS, call_flags, flag_rows, integrity_summary,
                                           write_flag_csv)
from analytics.convintel.store import ts
from integrations.timeutil import ist_day

T0 = datetime(2026, 10, 8, 5, 0, tzinfo=timezone.utc)   # 10:30 IST, Thursday
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
NAMES = {"u1": "Asha Rao", "u2": "Ravi Iyer", "u3": "Meera Shah", "u4": "Kabir Das", "u5": "Neha Jain",
         "rj": "Rinku Jhala", "sys": "System"}


def call(cid, at=T0, dur=200, status="Answered", caller="u1", lead="L1", team="Team Alpha", **extra):
    cls, why = classify(status, dur)
    kind = {"rj": "shared", "sys": "bot"}.get(caller, "person")
    row = {"call_id": cid, "lead_id": lead, "lead_number": "9000000001", "number": "919000000001",
           "caller_number": "918000000001", "direction": "outbound", "call_status": status,
           "answered": int(status == "Answered"), "start_utc": ts(at), "ist_day": ist_day(at), "duration_s": dur,
           "call_class": cls, "class_reason": why, "caller_id": caller, "caller_name": NAMES[caller],
           "caller_kind": kind, "team": team, "transcript_expected": 1, "transcript_state": S.T_FOUND,
           "transcript_words": 400, "transcript_api_duration": dur, "lookup_attempts": 1, "next_lookup_utc": None,
           "sem": None, "zip": None, "t": at}
    return {**row, **extra}


def semout(score=60, band="warm", real="yes", iflags=(), reason="", objections=(), signals=(), commitments=(),
           findings=(), dims=None, overall=6, payment_step="none", dated=False, pressure="none", unanswered=(),
           priority="Send the payment link on the call."):
    dims = dims or {}
    return {"language": {"primary": "english", "code_switching": False, "notes": ""},
            "intent": {"explicit_intent": "", "readiness_score": score, "readiness_band": band,
                       "genuineness": "genuine", "conditional_commitments": [], "hidden_objections": [],
                       "conflicting_statements": [], "priorities_constraints": [], "evidence": []},
            "tone": {"caller_pressure": pressure},
            "quality": {**{d: {"score": dims.get(d), "evidence": "", "note": ""} for d in S.QUALITY_DIMENSIONS},
                        "overall": overall},
            "objections": [{"category": c, "excerpt": "", "handled": h, "caller_response_excerpt": "", "note": ""}
                           for c, h in objections],
            "buying_signals": [{"type": t, "excerpt": "", "strength": "strong", "caller_acted_on_it": a}
                               for t, a in signals],
            "commitments": [{"by": b, "what": "call back", "due_text": "kal", "excerpt": ""} for b in commitments],
            "unanswered_questions": [{"topic": t, "excerpt": ""} for t in unanswered],
            "coaching": {"strengths": [{"point": "Warm opening", "excerpt": ""}],
                         "improvements": [{"point": "Quote the fee", "excerpt": "", "better_response": "It is 30,000"}],
                         "missed_signals": [{"what": "Asked about EMI", "excerpt": ""}], "priority_action": priority},
            "outcome": {"next_step_agreed": dated, "next_step": "", "dated": dated, "payment_step": payment_step,
                        "course_discussed": ""},
            "integrity": {"real_conversation": real, "flags": list(iflags), "reason": reason},
            "findings": [{"category": c, "excerpt": "", "confidence": "medium", "reasoning": "", "recommended_action": "",
                          "offset": -1} for c in findings],
            "summary": "Synthetic call."}


def flagmap(fl, cid):
    return {f["flag"]: f for f in fl.get(cid, [])}


def until(at):
    """Another lead's dial at ``at``: the period's calls run up to then."""
    return call("other", at=at, lead="L-other", dur=20, status="NotAnswered", caller="u5")


# ------------------------------------------------------------------ integrity: per-call flags

def test_179_seconds_is_short_and_180_is_not_flagged():
    fl = call_flags([call("a", dur=179), call("b", at=T0 + timedelta(hours=1), dur=180)])
    assert [(f["flag"], f["tier"]) for f in fl["a"]] == [("under_3_min", "short")]
    assert "179 s" in fl["a"][0]["reason"] and "needs review, not proof" in fl["a"][0]["reason"]
    assert "b" not in fl


# Every sign Claude's reading may name (thin included) must be honoured by the integrity view.
CLAUDE_FLAGS = S.SEMANTIC_SCHEMA["properties"]["integrity"]["properties"]["flags"]["items"]["enum"]


@pytest.mark.parametrize("flag", CLAUDE_FLAGS)
def test_claudes_integrity_flags_are_suspect_on_real_calls_only(flag):
    fl = call_flags([call("r", dur=300, sem=semout(real="doubtful", iflags=[flag])),
                     call("s", at=T0 + timedelta(hours=1), dur=100, sem=semout(real="doubtful", iflags=[flag])),
                     call("unread", at=T0 + timedelta(hours=2), dur=300, lead="L2")])
    assert set(flagmap(fl, "r")) == {flag} and flagmap(fl, "r")[flag]["tier"] == "suspect"
    assert flagmap(fl, "r")[flag]["reason"].startswith(
        f"Claude's reading: {S.INTEGRITY_FLAGS[flag]}; Claude doubts it was a real conversation")
    assert set(flagmap(fl, "s")) == {"under_3_min"}
    assert "unread" not in fl                      # no reading, no transcript flag: nothing else judges the words


def test_a_flag_on_a_call_claude_judged_real_says_so():
    fl = call_flags([call("r", dur=300, sem=semout(real="yes", iflags=["thin"], reason="Lead mostly silent"))])
    reason = flagmap(fl, "r")["thin"]["reason"]
    assert reason.startswith("Claude's reading: very little talk for the time; Claude judged it a real conversation")
    assert "(Lead mostly silent)" in reason and reason.endswith("(needs review, not proof)")


def test_claudes_verdict_without_a_sign_and_one_flag_per_sign():
    calls = [call("no", dur=300, sem=semout(real="no", reason="Wrong number")),
             call("one", dur=300, sem=semout(real="doubtful", iflags=["one_sided"]), caller="u2"),
             call("twice", dur=300, sem=semout(real="no", iflags=["machine", "machine"]), caller="u3"),
             call("fine", dur=300, sem=semout(real="yes"), caller="u4")]
    fl = call_flags(calls)
    assert set(flagmap(fl, "no")) == {"not_sales_talk"} and "Wrong number" in fl["no"][0]["reason"]
    assert flagmap(fl, "one")["one_sided"]["tier"] == "suspect"
    assert set(flagmap(fl, "twice")) == {"machine"}          # a named sign is not also filed under not_sales_talk
    machine = flagmap(fl, "twice")["machine"]["reason"]
    assert machine.count("Claude's reading") == 1 and "Claude judged it not a real conversation" in machine
    assert "fine" not in fl


def test_empty_transcript_on_every_connected_call_tiered_by_class():
    calls = [call("real", dur=300, transcript_state=S.T_NOT_TRANSCRIBED),
             call("short", dur=40, transcript_state=S.T_NOT_TRANSCRIBED, caller="u2"),
             call("unknown", dur=None, transcript_state=S.T_NOT_TRANSCRIBED, caller="u3"),
             call("missed", dur=20, status="NotAnswered", transcript_state=S.T_NOT_TRANSCRIBED, caller="u4")]
    fl = call_flags(calls)
    assert flagmap(fl, "real")["empty_transcript"]["tier"] == "suspect"
    assert flagmap(fl, "unknown")["empty_transcript"]["tier"] == "suspect"
    assert {f: x["tier"] for f, x in flagmap(fl, "short").items()} == {"under_3_min": "short", "empty_transcript": "short"}
    assert "missed" not in fl


@pytest.mark.parametrize("dur,api,hit", [(300, 200, True), (400, 290, True), (300, 240, False), (200, 145, False),
                                         (300, None, False), (300, 0, False)])
def test_duration_mismatch(dur, api, hit):
    fl = call_flags([call("c", dur=dur, transcript_api_duration=api)])
    assert ("duration_mismatch" in flagmap(fl, "c")) is hit


def test_empty_transcript_reason_says_whether_rechecks_are_still_running():
    pending = call("p", dur=300, transcript_state=S.T_NOT_TRANSCRIBED, transcript_api_duration=290,
                   next_lookup_utc="2026-10-08 07:00:00")
    done = call("d", dur=100, transcript_state=S.T_NOT_TRANSCRIBED, next_lookup_utc=None, caller="u2")
    fl = call_flags([pending, done])
    assert "a 290 s recording" in flagmap(fl, "p")["empty_transcript"]["reason"]
    assert "next automatic check 2026-10-08 12:30 IST" in flagmap(fl, "p")["empty_transcript"]["reason"]
    assert "after every automatic recheck" in flagmap(fl, "d")["empty_transcript"]["reason"]


def test_model_reason_loses_phone_numbers_and_a_bare_verdict_is_not_given_a_sign():
    sem = semout(real="doubtful", reason="Customer read out 90000 00001, then hung up")
    fl = call_flags([call("n", dur=300, sem=sem)])
    reason = flagmap(fl, "n")["not_sales_talk"]["reason"]
    assert "00001" not in reason and "[number removed]" in reason
    assert "without naming a specific sign" in reason and "wrong number" not in reason


def test_no_recording_long_call_only_after_rechecks_end():
    pending = call("p", dur=300, transcript_state=S.T_NOT_FOUND, next_lookup_utc="2026-10-09 05:00:00")
    done = call("d", dur=300, transcript_state=S.T_NOT_FOUND, next_lookup_utc=None, lookup_attempts=5, caller="u2")
    short = call("s", dur=100, transcript_state=S.T_NOT_FOUND, next_lookup_utc=None, caller="u3")
    fl = call_flags([pending, done, short])
    assert "p" not in fl
    assert "after 5 searches" in flagmap(fl, "d")["no_recording_long_call"]["reason"]
    assert set(flagmap(fl, "s")) == {"under_3_min"}


def test_overlap_needs_more_than_30_seconds_and_one_person():
    s = timedelta(seconds=1)
    calls = [call("a", at=T0, dur=300), call("b", at=T0 + 270 * s, dur=200, lead="L2"),       # 30 s: not flagged
             call("c", at=T0 + 3600 * s, dur=300), call("d", at=T0 + 3860 * s, dur=200, lead="L2"),  # 40 s
             call("e", at=T0 + 3900 * s, dur=60, lead="L3"),                                   # short, overlaps c and d
             call("f", at=T0 + 7200 * s, dur=300, caller="u2"), call("g", at=T0 + 7300 * s, dur=300, caller="u3"),
             call("h", at=T0 + 9000 * s, dur=300, caller="rj"), call("i", at=T0 + 9100 * s, dur=300, caller="rj")]
    fl = call_flags(calls)
    assert "a" not in fl and "b" not in fl
    assert "overlap" in flagmap(fl, "c") and "overlap" in flagmap(fl, "d")
    assert "by 40 s" in flagmap(fl, "c")["overlap"]["reason"] or "by 60 s" in flagmap(fl, "c")["overlap"]["reason"]
    assert set(flagmap(fl, "e")) == {"under_3_min"}
    assert not {"f", "g", "h", "i"} & set(fl)


def test_overlap_ignores_an_unknown_call_with_an_implausible_duration():
    # A 5-hour "call" is a line left open (class UNKNOWN): it must not make every later call an overlap.
    calls = [call("open", at=T0, dur=5 * 3600), call("a", at=T0 + timedelta(hours=1), dur=300, lead="L2"),
             call("b", at=T0 + timedelta(hours=2), dur=300, lead="L3")]
    assert calls[0]["call_class"] == S.UNKNOWN
    fl = call_flags(calls)
    assert not any(f["flag"] == "overlap" for fs in fl.values() for f in fs)


def test_repeat_three_real_calls_to_one_lead_in_an_ist_day():
    h = timedelta(hours=1)
    late = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)     # 23:30 IST on the 8th
    calls = [call("a", at=T0), call("b", at=T0 + h), call("c", at=T0 + 2 * h),
             call("d", at=late, lead="L2"), call("e", at=late + h, lead="L2"), call("f", at=late - 2 * h, lead="L2"),
             call("g", at=T0, caller="u2", lead="L3"), call("h", at=T0 + h, caller="u2", lead="L3")]
    fl = call_flags(calls)
    assert all(flagmap(fl, x)["repeat"]["tier"] == "pattern" for x in "abc")
    assert "3 real calls" in flagmap(fl, "a")["repeat"]["reason"]
    assert not {"d", "e", "f", "g", "h"} & set(fl)                # L2's calls fall on two IST days


def _day_calls(caller, n, band, start):
    return [call(f"{caller}-{i}", at=start + timedelta(minutes=10 * i), caller=caller, lead=f"{caller}-L{i}",
                 dur=190 if i < band else 400) for i in range(n)]


def test_just_over_3_min_against_the_account_baseline():
    calls = (_day_calls("u1", 10, 6, T0) + _day_calls("u2", 30, 1, T0) + _day_calls("u3", 30, 1, T0)
             + _day_calls("u4", 9, 9, T0))
    fl = call_flags(calls)
    flagged = {cid for cid, fs in fl.items() if any(f["flag"] == "just_over_3_min" for f in fs)}
    assert flagged == {f"u1-{i}" for i in range(6)}
    assert "6 of this caller's 10 real calls" in flagmap(fl, "u1-0")["just_over_3_min"]["reason"]


def test_just_over_3_min_not_flagged_when_the_whole_account_bunches():
    fl = call_flags(_day_calls("u1", 10, 6, T0) + _day_calls("u5", 10, 6, T0))
    assert not any(f["flag"] == "just_over_3_min" for fs in fl.values() for f in fs)


# ------------------------------------------------------------------ integrity: roll-ups

def test_summary_sorting_caps_totals_and_no_phone_numbers():
    m = timedelta(minutes=10)
    calls = [call(f"s{i}", at=T0 + i * m, dur=30 + i) for i in range(60)]                     # u1: 60 short calls
    h = timedelta(hours=1)
    calls += [call("x1", at=T0 - h, dur=300, sem=semout(iflags=["machine"]), lead="L2"),
              call("x2", at=T0 - 2 * h, dur=900, transcript_state=S.T_NOT_TRANSCRIBED, lead="L3"),
              call("x3", at=T0 - 3 * h, dur=300, lead="L4")]
    calls += [call("y1", caller="u2", dur=300, sem=semout(iflags=["thin"])), call("y2", at=T0 + h, caller="u2", dur=300)]
    calls += [call("z1", caller="rj", dur=300, sem=semout(iflags=["loop"]))]
    calls += [call("n1", caller="u3", dur=20, status="NotAnswered")]
    fl = call_flags(calls)
    summary = integrity_summary(calls, fl)
    assert summary["flaggedCalls"] == 4 and summary["shortCalls"] == 60
    assert summary["byFlag"]["under_3_min"] == 60 and summary["byFlag"]["machine"] == 1
    assert set(summary["byFlag"]) == set(S.INTEGRITY_FLAGS)
    assert summary["byTier"] == {"suspect": 4, "pattern": 0, "short": 60}
    rows = summary["byCaller"]
    assert [r["callerId"] for r in rows] == ["u1", "u2", "rj"]           # people by flagged share, shared last
    asha = rows[0]
    assert (asha["calls"], asha["realCalls"], asha["shortCalls"], asha["flagged"]) == (63, 3, 60, 2)
    assert asha["flaggedPct"] == pytest.approx(66.7) and asha["shortPct"] == pytest.approx(95.2)
    assert asha["callIds"][:2] == ["x2", "x1"] and len(asha["callIds"]) == CALL_IDS_CAP
    assert asha["callIdsTotal"] == 62
    assert rows[1]["flaggedPct"] == 50.0 and rows[2]["kind"] == "shared"
    text = json.dumps(summary)
    assert "919000000001" not in text and "918000000001" not in text and "9000000001" not in text
    assert any("not that it was faked" in n for n in summary["notes"])


def test_flag_rows_carry_caller_and_dialled_numbers_most_serious_first(tmp_path):
    calls = [call("s1", dur=100), call("r1", at=T0 + timedelta(hours=1), dur=300, sem=semout(iflags=["no_content"]),
                                       direction="inbound"), call("ok", at=T0 + timedelta(hours=2), dur=300)]
    fl = call_flags(calls)
    rows = flag_rows(calls, fl)
    assert [r["callId"] for r in rows] == ["r1", "s1"]
    r = rows[0]
    assert set(r) == set(ROW_COLUMNS)
    assert (r["callerNumber"], r["leadNumber"], r["direction"]) == ("918000000001", "919000000001", "inbound")
    assert r["startIst"] == "2026-10-08 11:30" and r["tiers"] == ["suspect"] and r["flags"] == ["no_content"]
    assert r["transcriptWords"] == 400 and r["caller"] == "Asha Rao"
    path = tmp_path / "flags" / "integrity_flags.csv"
    write_flag_csv(str(path), rows)
    got = list(csv.DictReader(open(path, encoding="utf-8")))
    assert list(got[0]) == list(ROW_COLUMNS) and got[1]["flags"] == "under_3_min" and got[0]["tiers"] == "suspect"


def test_every_flagged_call_of_every_tier_has_a_row_with_both_numbers_and_the_summary_has_none():
    h = timedelta(hours=1)
    calls = [call("short", dur=90), call("short-empty", at=T0 + h, dur=60, transcript_state=S.T_NOT_TRANSCRIBED),
             call("real-empty", at=T0 + 2 * h, dur=400, transcript_state=S.T_NOT_TRANSCRIBED, lead="L2"),
             call("unknown-empty", at=T0 + 3 * h, dur=None, transcript_state=S.T_NOT_TRANSCRIBED, lead="L3"),
             call("model", at=T0 + 4 * h, dur=300, lead="L4",
                  sem=semout(real="no", iflags=["one_sided"], reason="Called 919000000001 by mistake")),
             call("shared", at=T0 + 5 * h, dur=50, caller="rj"), call("bot", at=T0 + 6 * h, dur=40, caller="sys")]
    calls += [call(f"rep{i}", at=T0 + (7 + i) * h, dur=300, caller="u2", lead="L9") for i in range(3)]
    calls += [call("ok", at=T0 + 12 * h, dur=300, lead="L5")]
    fl = call_flags(calls)
    assert {f["flag"] for f in fl["short-empty"]} == {"under_3_min", "empty_transcript"}
    assert {f["tier"] for fs in fl.values() for f in fs} == {"short", "suspect", "pattern"}
    rows = flag_rows(calls, fl)
    assert sorted(r["callId"] for r in rows) == sorted(fl) and "ok" not in fl
    assert all(r["callerNumber"] == "918000000001" and r["leadNumber"] == "919000000001" and r["caller"] for r in rows)
    summary = integrity_summary(calls, fl)
    by = {r["callerId"]: r for r in summary["byCaller"]}
    assert all(r["callIdsTotal"] == r["flagged"] + r["shortCalls"] for r in by.values())
    assert set(by["u1"]["callIds"]) == {"short", "short-empty", "real-empty", "unknown-empty", "model"}
    text = json.dumps(summary) + json.dumps(fl)          # reasons reach the dashboard too
    assert "9000000001" not in text and "8000000001" not in text


# ------------------------------------------------------------------ cross-call journeys

def test_trend_rules():
    assert trend([]) == "unknown" and trend([50]) == "single call"
    assert trend([30, 40, 70]) == "rising" and trend([80, 70, 40]) == "falling" and trend([50, 55, 58]) == "flat"


def test_journey_readiness_trend_and_repeated_objections():
    h = timedelta(hours=3)
    calls = [call("a", at=T0, sem=semout(score=30, objections=[("price", "no")])),
             call("b", at=T0 + h, sem=semout(score=40, objections=[("price", "partly"), ("time", "yes")])),
             call("c", at=T0 + 2 * h, sem=semout(score=75, objections=[("price", "yes")])),
             call("d", at=T0 + 3 * h, dur=100),                                   # later, not read by Claude yet
             call("e", at=T0 + 4 * h, sem=semout(score=0, band="unclear"))]         # too thin to judge: ignored
    j = lead_journeys(calls, {"L1": {"stage": "Counselled lead", "course": "Diploma X", "owner_name": "Asha Rao"}},
                      [], NOW)["L1"]
    assert (j["readiness"], j["readinessSource"], j["readinessBand"]) == (75, "semantic", "warm")
    assert (j["readinessTrend"], j["trendSource"]) == ("rising", "semantic")
    assert j["repeatedObjections"] == ["price"] and "same objection in 3 calls: price" in j["flags"]
    assert (j["calls"], j["realCalls"], j["lastCallIst"]) == (5, 4, "2026-10-08 22:30")
    assert j["owner"] == "Asha Rao" and j["team"] is None and j["priority"] is None and not j["enrolled"]
    assert j["nextAction"].startswith("Close on the next call: readiness 75/100")


def test_journey_reads_only_claudes_readings():
    h = timedelta(hours=3)
    calls = [call("a", at=T0, sem=semout(score=70)), call("b", at=T0 + h),                 # b: not read yet
             call("c", at=T0 + 2 * h, sem=semout(score=40, band="cool"))]
    j = lead_journeys(calls, {}, [], NOW)["L1"]
    assert (j["readiness"], j["readinessSource"], j["readinessBand"]) == (40, "semantic", "cool")
    assert (j["readinessTrend"], j["trendSource"]) == ("falling", "semantic")
    assert "readiness falling" in j["flags"] and j["nextAction"].startswith("Ask what has changed")
    unread = lead_journeys([call("a")], {}, [], NOW)["L1"]                                  # a real call, no reading
    assert (unread["readiness"], unread["readinessSource"], unread["readinessBand"]) == (None, None, None)
    assert (unread["readinessTrend"], unread["trendSource"]) == ("unknown", None)
    assert unread["nextAction"].startswith("No analysed call yet")
    none = lead_journeys([call("a", dur=20, status="NotAnswered")], {}, [], NOW)["L1"]
    assert none["readiness"] is None and none["readinessTrend"] == "unknown" and none["readinessSource"] is None
    assert "3+ minutes" in none["nextAction"]


@pytest.mark.parametrize("follow_up,now_h,missed", [
    (None, 47, 0),                                   # 48 h not over yet: not judged
    (None, 49, 1),                                   # no later call
    (("Answered", 30), 49, 0),                       # answered follow-up within 48 h
    (("NotAnswered", 30), 72, 1),                    # a dial that wasn't answered doesn't count
    (("Answered", 50), 72, 1),                       # answered, but after 48 h
])
def test_missed_commitment_timing(follow_up, now_h, missed):
    calls = [call("a", sem=semout(commitments=["caller"])), until(T0 + timedelta(hours=now_h))]
    if follow_up:
        calls.append(call("b", at=T0 + timedelta(hours=follow_up[1]), status=follow_up[0], dur=60))
    j = lead_journeys(calls, {}, [], T0 + timedelta(hours=now_h))["L1"]
    assert j["missedCommitments"] == missed
    assert ("promised callback missed" in j["flags"]) is bool(missed)
    if missed:
        assert j["nextAction"].startswith("Call today: a follow-up promised on the call of 2026-10-08 10:30 IST")


def test_follow_ups_judged_only_as_far_as_the_calls_run():
    # A past period: the calls end 20 h after the promise, the report runs days later. The follow-up may have
    # been made after the period, so the promise is not called missed, and a hot lead is not called quiet.
    calls = [call("a", sem=semout(score=85, commitments=["caller"])), until(T0 + timedelta(hours=20))]
    j = lead_journeys(calls, {}, [], NOW + timedelta(days=5))["L1"]
    assert j["missedCommitments"] == 0 and j["flags"] == []
    # The same calls judged with the period said to run to 49 h after the promise: missed.
    j = lead_journeys(calls, {}, [], NOW + timedelta(days=5), data_end=T0 + timedelta(hours=49))["L1"]
    assert j["missedCommitments"] == 1 and "promised callback missed" in j["flags"]
    # data_end never runs past now.
    j = lead_journeys(calls, {}, [], T0 + timedelta(hours=47), data_end=T0 + timedelta(days=9))["L1"]
    assert j["missedCommitments"] == 0


def test_semantic_commitment_by_caller_or_callback_finding_only():
    later = T0 + timedelta(hours=60)
    customer = lead_journeys([call("a", sem=semout(commitments=["customer"])), until(later)], {}, [], later)["L1"]
    caller = lead_journeys([call("a", sem=semout(commitments=["caller"])), until(later)], {}, [], later)["L1"]
    finding = lead_journeys([call("a", sem=semout(findings=["callback_promised"])), until(later)], {}, [], later)["L1"]
    assert (customer["missedCommitments"], caller["missedCommitments"], finding["missedCommitments"]) == (0, 1, 1)


def test_hot_lead_quiet_and_enrolled_leads():
    later = T0 + timedelta(days=3)
    hot = lead_journeys([call("a", sem=semout(score=85, dated=True)), until(later)], {}, [], later)["L1"]
    assert "hot lead, no call in 48 h" in hot["flags"] and hot["nextAction"].startswith("Call today to close")
    enrolled = lead_journeys([call("a", sem=semout(score=85, commitments=["caller"])), until(later)], {},
                             [{"lead_id": "L1", "at_utc": "2026-10-09 06:00:00"}], later)["L1"]
    assert enrolled["enrolled"] and enrolled["flags"] == [] and enrolled["missedCommitments"] == 0
    assert enrolled["nextAction"].startswith("Already enrolled")
    by_stage = lead_journeys([call("a")], {"L1": {"stage": "Course Enrolled"}}, [], later)["L1"]
    assert by_stage["enrolled"]


# ------------------------------------------------------------------ coaching

def test_call_coaching_needs_claudes_reading():
    assert call_coaching(call("c", dur=400)) is None                       # not read by Claude yet: no coaching
    assert call_coaching(call("d", caller="rj", sem=semout())) is None
    assert call_coaching(call("e", caller="sys", sem=semout())) is None
    paid = call_coaching(call("b", dur=400, sem=semout(score=80, payment_step="link_sent", dated=True)))
    assert (paid["source"], paid["basis"]) == ("semantic", SEM_BASIS) and "Claude" in SEM_BASIS
    assert paid["weaknesses"] == [] and paid["strengths"] == ["payment step taken", "dated next step agreed"]
    assert paid["overall"] == 6
    no_overall = call_coaching(call("n", dur=400, sem=semout(overall=None, dims={"closing": 5})))
    assert no_overall["overall"] is None and no_overall["scores"]["closing"] == 5


def test_call_coaching_semantic():
    sem = semout(score=80, signals=[("fee_question", "no"), ("start_date", "yes")],
                 objections=[("price", "no"), ("time", "yes")], dims={"questioning": 8, "closing": 2},
                 unanswered=["refund"], payment_step="none", dated=False)
    cc = call_coaching(call("a", dur=400, sem=sem))
    assert cc["source"] == "semantic" and cc["scores"]["questioning"] == 8 and cc["scores"]["active_listening"] is None
    assert set(cc["weaknesses"]) == {"missed buying signal: fee question", "objection not handled well: price",
                                     "lead's questions left unanswered", "no payment step with a ready lead",
                                     "no dated next step"}
    assert cc["strengths"] == ["objection handled: no time"]
    assert cc["improvements"] == [{"point": "Quote the fee", "betterResponse": "It is 30,000"}]
    assert cc["priorityAction"] == "Send the payment link on the call."


def _sem_call(cid, caller, day, team="Team Alpha", **kw):
    return call(cid, at=T0 + timedelta(days=day), caller=caller, lead=f"L{cid}", dur=400, team=team, sem=semout(**kw))


def test_caller_and_team_coaching():
    calls = [_sem_call(f"a{i}", "u1", -i, dims={"questioning": 8, "closing": 3}, overall=8,
                       signals=[("fee_question", "no")] if i < 2 else [], payment_step="link_sent", dated=True)
             for i in range(3)]
    calls += [_sem_call(f"b{i}", "u2", -i, dims={"questioning": 5}, overall=4, signals=[("fee_question", "no")],
                        priority=f"Fix opening {i}") for i in range(2)]
    calls += [_sem_call("c0", "u3", 0, overall=5, team="Team Beta")]
    calls += [_sem_call(f"r{i}", "rj", 0, signals=[("fee_question", "no")]) for i in range(3)]
    per = caller_coaching(calls)
    assert set(per) == {"u1", "u2", "u3"}
    u1 = per["u1"]
    assert {"item": "asking questions", "n": 3, "avg": 8.0} in u1["strengths"]
    assert {"item": "payment step taken", "n": 3} in u1["strengths"]
    assert {"item": "closing", "n": 3, "avg": 3.0} in u1["weaknesses"]
    assert {"item": "missed buying signal: fee question", "n": 2} in u1["weaknesses"]
    assert u1["actions"][:2] == [DIM_ACTION["closing"], ACTIONS["missed buying signal: fee question"]]
    assert len(u1["actions"]) == 3 and u1["actions"][2] == "Send the payment link on the call."
    assert u1["qualityAvg"] == 8.0 and u1["analysedCalls"] == 3 and u1["team"] == "Team Alpha"
    assert per["u2"]["actions"][2] == "Fix opening 0"                   # then the latest call's own priority
    teams = {t["team"]: t for t in team_coaching(calls, per)}
    assert set(teams) == {"Team Alpha", "Team Beta"}
    alpha = teams["Team Alpha"]
    assert alpha["gaps"] == [{"item": "missed buying signal: fee question", "callers": 2, "n": 4,
                              "who": ["Asha Rao", "Ravi Iyer"]}]
    assert alpha["practices"] and all(p["from"] == ["Asha Rao"] for p in alpha["practices"])
    assert alpha["priorities"][0].startswith("Missed buying signal: fee question (2 callers, 4 calls)")
    assert teams["Team Beta"]["gaps"] == [] and teams["Team Beta"]["priorities"] == []


def test_caller_coaching_counts_only_calls_claude_has_read():
    calls = [call(f"k{i}", at=T0 + timedelta(hours=i), lead=f"L{i}", dur=400,
                  sem=semout(signals=[("fee_question", "no")], overall=None if i == 0 else 6)) for i in range(2)]
    calls += [call("unread", at=T0 + timedelta(hours=3), lead="L9", dur=400),
              call("m0", caller="u2", lead="L8", dur=400)]                      # Ravi: no call read yet
    per = caller_coaching(calls)
    u1 = per["u1"]
    assert u1["analysedCalls"] == 2 and u1["qualityAvg"] == 6.0             # a reading with no overall is left out
    assert {"item": "missed buying signal: fee question", "n": 2} in u1["weaknesses"]
    assert ACTIONS["missed buying signal: fee question"] in u1["actions"]
    ravi = per["u2"]
    assert (ravi["analysedCalls"], ravi["qualityAvg"], ravi["strengths"], ravi["weaknesses"], ravi["actions"]) == (
        0, None, [], [], [])
    assert ravi["weeklySample"] == {"2026-10-05": ["m0"]}                 # still sampled for a manual listen


def test_weekly_sample_five_longest_real_calls_per_monday_ist_week():
    sun_late = datetime(2026, 10, 11, 18, 20, tzinfo=timezone.utc)    # Sunday 23:50 IST -> week of 5 Oct
    mon_early = datetime(2026, 10, 11, 18, 40, tzinfo=timezone.utc)   # Monday 00:10 IST -> week of 12 Oct
    calls = [call(f"w{i}", at=T0 + timedelta(minutes=30 * i), dur=200 + 10 * i, lead=f"L{i}") for i in range(6)]
    calls += [call("sun", at=sun_late, dur=205), call("mon", at=mon_early, dur=900),
              call("short", at=T0, dur=170), call("missed", at=T0, dur=30, status="NotAnswered"),
              call("shared", at=T0, dur=2000, caller="rj")]
    sample = weekly_sample(calls)
    assert set(sample) == {"u1"}
    assert sample["u1"]["2026-10-05"] == ["w5", "w4", "w3", "w2", "w1"]
    assert sample["u1"]["2026-10-12"] == ["mon"]
    assert caller_coaching(calls)["u1"]["weeklySample"] == sample["u1"]
    assert weekly_sample(calls, n=7)["u1"]["2026-10-05"] == ["w5", "w4", "w3", "w2", "w1", "sun", "w0"]
