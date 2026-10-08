from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from analytics.call_integrity import analyse, log_flags, match, pick_sample, transcript_flags
from analytics.report_validation import integrity_check

T0 = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)


def call(i, name, mins, dur, lead="L", ans=True, team="T1", number=None):
    return {"activity_id": f"c{i}", "name": name, "team": team, "t": T0 + timedelta(minutes=mins), "duration": dur,
            "ans": ans, "real": ans and dur >= 120, "lead_id": lead, "lead_number": number or f"90000000{i:02d}"}


def test_overlap_and_repeat_flags():
    calls = [call(1, "Asha", 0, 600, "A"), call(2, "Asha", 5, 300, "B"),  # second starts while the first runs
             call(3, "Ravi", 0, 130, "X"), call(4, "Ravi", 10, 140, "X"), call(5, "Ravi", 20, 200, "X"),
             call(6, "Ravi", 30, 125, "Y", team="Not a user")]
    flags, _ = log_flags(calls)
    assert flags["c1"] == ["overlap"] and flags["c2"] == ["overlap"]
    assert all(flags[f"c{i}"] == ["repeat"] for i in (3, 4, 5)) and "c6" not in flags


def test_just_over_bunching_needs_twice_the_account_share():
    calls = [call(i, "Bunch", i * 10, 125, f"b{i}") for i in range(10)]
    calls += [call(20 + i, "Even", i * 10, 400, f"e{i}") for i in range(30)]
    flags, info = log_flags(calls)
    assert info["bunched_callers"] == ["Bunch"] and flags["c0"] == ["just_over"] and "c20" not in flags


def test_transcript_flags():
    assert transcript_flags("hello hello", 300)[0] == ["no_content"]
    assert transcript_flags(" ".join(["word"] * 100), 600)[0] == ["thin", "loop"]  # 10 wpm, one phrase repeating
    talk = " ".join(f"w{i}" for i in range(450))
    assert transcript_flags(talk, 180)[0] == []
    flags, ev = transcript_flags("The number you have dialled is switched off " + talk, 180)
    assert flags == ["machine"] and ev["machine_text"]


def test_match_picks_the_closest_call_within_ten_minutes():
    c = call(1, "Asha", 0, 300, number="9000000001")
    api = [SimpleNamespace(phone="9000000001", start_time=T0 + timedelta(minutes=3), duration=290, transcript="near", transcript_url="u"),
           SimpleNamespace(phone="9000000001", start_time=T0 + timedelta(minutes=40), duration=290, transcript="far", transcript_url="u"),
           SimpleNamespace(phone="9000000002", start_time=T0, duration=300, transcript="", transcript_url=None)]
    assert match([c], api, lambda n: n) == ({"c1": "near"}, {})
    later, untranscribed, unknown = call(2, "Asha", 60, 300, number="9000000001"), call(3, "Asha", 0, 300, number="9000000002"), \
        call(4, "Asha", 0, 300, number="9000000009")
    assert match([later, untranscribed, unknown], api, lambda n: n) == ({}, {
        "c2": "no API call within 10 min", "c3": "not transcribed", "c4": "number not in the transcript API"})


def test_sample_mixes_flagged_longest_and_just_over_with_distinct_numbers():
    calls = [call(1, "A", 0, 900), call(2, "A", 20, 125), call(3, "B", 0, 1200), call(4, "B", 30, 130, number="9000000003")]
    s = pick_sample(calls, {"c1": ["overlap"]}, 3)
    assert [c["activity_id"] for c in s] == ["c1", "c3", "c2"]


def test_rollup_and_gate():
    calls = [call(i, "Asha", i * 10, 300, f"l{i}") for i in range(12)]
    texts = {"c0": "", "c1": " ".join(f"w{i}" for i in range(800))}
    I = analyse(calls, texts, calls[:2])
    asha = I["callers"][0]
    assert (asha["long_calls"], asha["flagged"], asha["checked"], asha["checked_flagged"], asha["ranked"]) == (12, 1, 2, 1, True)
    assert I["by_flag"]["no_content"] == 1 and I["transcripts_matched"] == 2
    assert integrity_check({k: v for k, v in I.items() if k != "calls"})["ok"]
    assert all("lead" not in k for row in I["calls"] for k in row)


def test_match_allows_the_ist_offset_only_when_durations_agree():
    c = call(1, "Asha", 0, 2988, number="9000000001")
    shifted = SimpleNamespace(phone="9000000001", start_time=T0 - timedelta(minutes=330), duration=2988, transcript="t",
                              transcript_url="u")
    assert match([c], [shifted], lambda n: n)[0] == {"c1": "t"}
    other = SimpleNamespace(**{**vars(shifted), "duration": 600})
    assert match([c], [other], lambda n: n)[1] == {"c1": "no API call within 10 min"}
