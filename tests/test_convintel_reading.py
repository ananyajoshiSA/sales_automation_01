"""Claude reading rounds: transcripts handed out in packs, readings checked and stored, nothing sent to an API.
All data is synthetic; nothing is written under data/."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from analytics.convintel import reading
from analytics.convintel import schema as S
from analytics.convintel.reconcile import reconcile
from analytics.convintel.store import Registry

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
TEXT = ("Hello, this is the counsellor calling about the diploma course. Haan ji, fees kitni hai? "
        "The fee is Rs 25,000 and EMI is possible. Theek hai, kal shaam 5 baje call karna.")


def _minimal(sch: dict):
    if "anyOf" in sch:
        return None
    t = sch["type"]
    if t == "object":
        return {k: _minimal(v) for k, v in sch["properties"].items()}
    return {"array": [], "integer": 0, "boolean": False}.get(t, sch.get("enum", [""])[0])


def good_reading(excerpt: str = "fees kitni hai") -> dict:
    out = _minimal(S.SEMANTIC_SCHEMA)
    out["intent"].update(readiness_score=60, readiness_band="warm")
    out["speaker_turns"]["inferred"] = True
    out["word_analysis"]["phrases"] = [{"category": "payment_intent", "excerpt": excerpt, "speaker": "customer",
                                        "note": "asks the fee"}]
    out["findings"] = [{"category": "payment_ready", "excerpt": excerpt, "confidence": "medium",
                        "reasoning": "The customer asked the fee.", "recommended_action": "Call back at 5 pm."}]
    out["summary"] = "The customer asked the fee and agreed a call tomorrow."
    return out


@pytest.fixture
def reg(tmp_path):
    r = Registry(str(tmp_path / "registry.sqlite"))
    rows = []
    for i in range(3):
        rows.append({"call_id": f"c{i}", "lead_id": f"lead-{i}", "lead_number": f"91900000000{i}",
                     "number": f"91900000000{i}", "caller_number": "918000000001", "direction": "outbound",
                     "call_status": "Answered", "answered": 1, "start_utc": f"2026-10-09 0{5 + i}:00:00",
                     "ist_day": "2026-10-09", "duration_s": 240, "duration_raw": "240", "call_class": S.REAL_CALL,
                     "class_reason": "", "caller_id": "u1", "caller_name": "Asha Rao", "caller_kind": "person",
                     "team": "Team Alpha", "team_source": "test", "source": "test"})
    r.upsert_calls(rows, NOW)
    for i in range(3):
        p = tmp_path / f"c{i}.txt"
        p.write_text(TEXT, encoding="utf-8")
        r.set_transcript(f"c{i}", S.T_FOUND, NOW, transcript_ref=str(p), transcript_words=len(TEXT.split()),
                         transcript_sha256=hashlib.sha256(p.read_bytes()).hexdigest(), transcript_source_id=f"s{i}")
    r.refresh(None, NOW)
    return r


def write(folder, cid, data):
    path = os.path.join(folder, "results", f"{cid}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))
    return path


def finish(reg, folder, cid, data):
    """Write a reading and run ``read check`` on it, as Claude does."""
    path = write(folder, cid, data)
    return reading.check(reg, path)["ok"]


def test_prepare_writes_packs_without_lead_details_and_hands_calls_out(reg, tmp_path):
    out = reading.prepare(reg, NOW, per_pack=2, base=str(tmp_path / "reading"))
    assert (out["calls"], out["packs"]) == (3, 2)
    folder = out["folder"]
    text = open(os.path.join(folder, "INSTRUCTIONS.md"), encoding="utf-8").read()
    assert "word for word" in text and "No API" in text and '"readiness_score"' in text
    packs = [json.load(open(os.path.join(folder, f), encoding="utf-8")) for f in sorted(os.listdir(folder))
             if f.startswith("pack_")]
    assert [len(p["calls"]) for p in packs] == [2, 1]
    body = json.dumps(packs)
    assert "fees kitni hai" in body
    for secret in ("919000000000", "918000000001", "lead-0", "Asha", "Team Alpha"):
        assert secret not in body
    c = reg.call("c0")
    assert c["analysis_status"] == S.ANALYSIS_IN_PROGRESS and out["round"] in c["status_reason"]
    assert reading.prepare(reg, NOW, base=str(tmp_path / "reading"))["calls"] == 0   # never handed out twice
    assert "Words and phrases" in text and '"word_analysis"' in text


def test_limit_zero_hands_out_nothing_and_rounds_never_share_a_folder(reg, tmp_path):
    base = str(tmp_path / "reading")
    assert reading.prepare(reg, NOW, limit=0, base=base)["calls"] == 0
    a = reading.prepare(reg, NOW, limit=1, base=base)
    b = reading.prepare(reg, NOW, limit=1, base=base)                    # the same second
    assert a["folder"] != b["folder"] and b["round"] == f"{a['round']}-2"
    for out in (a, b):
        pack = json.load(open(os.path.join(out["folder"], "pack_001.json"), encoding="utf-8"))
        assert reg.call(pack["calls"][0]["call_id"])["status_reason"].count(out["round"]) == 1


def test_an_upgrade_re_derives_statuses_and_drops_the_old_no_model_access_note(reg):
    reg.set_meta("blocked_layers", {S.SEMANTIC: "no model access yet (ANTHROPIC_API_KEY is not set)"})
    reg.db.execute("UPDATE transcript_coverage_registry SET analysis_status = 'ANALYZED', "
                   "status_reason = 'semantic layer waiting: no model access yet'")
    assert reg.upgrade(NOW) and not reg.upgrade(NOW)
    c = reg.call("c0")
    assert c["analysis_status"] == S.PENDING_ANALYSIS and "model" not in c["status_reason"]
    assert reg.get_meta("blocked_layers") == {}


def test_check_reports_excerpts_that_are_not_verbatim(reg, tmp_path):
    folder = reading.prepare(reg, NOW, base=str(tmp_path / "reading"))["folder"]
    assert reading.check(reg, write(folder, "c0", good_reading()))["ok"]
    bad = reading.check(reg, write(folder, "c1", good_reading("what is the fee please")))
    assert not bad["ok"] and bad["excerptsDropped"] == 2          # the finding and its phrase
    part = good_reading()
    del part["coaching"]
    res = reading.check(reg, write(folder, "c2", part))
    assert not res["ok"] and any("coaching" in p for p in res["problems"])
    assert not reading.check(reg, write(folder, "c2", "{not json"))["ok"]
    assert not reading.check(reg, os.path.join(folder, "results", "nope.json"))["ok"]


def test_only_finished_readings_are_stored(reg, tmp_path):
    base = str(tmp_path / "reading")
    folder = reading.prepare(reg, NOW, base=base)["folder"]
    draft = write(folder, "c0", good_reading("what is the fee please"))   # not checked yet, and not verbatim
    assert not reading.check(reg, draft)["ok"]
    write(folder, "c1", "{half a file")
    assert finish(reg, folder, "c2", good_reading())
    out = reading.collect(reg, NOW, base=base)
    assert (out["done"], out["not_checked_yet"]) == (1, 2)
    assert reg.call("c2")["analysis_status"] == S.ANALYZED
    assert reg.result("c2", S.SEMANTIC)["engine"] == reading.ENGINE
    assert {reg.call(c)["analysis_status"] for c in ("c0", "c1")} == {S.ANALYSIS_IN_PROGRESS}   # left for Claude
    stored = reg.q("SELECT excerpt, offset FROM conversation_quality_findings WHERE call_id = 'c2'")[0]
    assert TEXT[stored["offset"]:stored["offset"] + len(stored["excerpt"])] == stored["excerpt"]
    assert finish(reg, folder, "c0", good_reading())                    # fixed and checked: now it counts
    write(folder, "c1", good_reading())                                 # finished writing but never checked
    assert reading.collect(reg, NOW, base=base)["done"] == 1
    assert reg.call("c1")["analysis_status"] == S.ANALYSIS_IN_PROGRESS
    path = os.path.join(folder, "results", "c1.json")
    assert reading.check(reg, path)["ok"]
    with open(path, "a", encoding="utf-8") as fh:                       # edited after its check
        fh.write(" ")
    assert reading.collect(reg, NOW, base=base)["not_checked_yet"] == 1
    assert reading.check(reg, path)["ok"] and reading.collect(reg, NOW, base=base)["done"] == 1
    assert reading.collect(reg, NOW, base=base)["done"] == 0            # stored once
    checks = {c["check"]: c for c in reconcile(reg, "2026-10-09", "2026-10-09", NOW)}
    assert checks["analysed calls have every layer"]["ok"]


def test_a_reading_finished_in_an_old_round_is_never_lost(reg, tmp_path):
    base = str(tmp_path / "reading")
    first = reading.prepare(reg, NOW, base=base)
    assert finish(reg, first["folder"], "c0", good_reading())
    later = NOW + timedelta(hours=26)                                   # the round went stale
    second = reading.prepare(reg, later, base=base)                     # collects c0 before handing out again
    assert second["collected"] == {"done": 1, "not_read_yet": 2} and second["calls"] == 2
    assert finish(reg, first["folder"], "c1", good_reading())           # a late reading in the old round's folder
    assert reading.collect(reg, later, base=base)["done"] == 1
    assert reg.call("c1")["analysis_status"] == S.ANALYZED
    assert reading.collect(reg, later, second["folder"], base=base) == {"not_read_yet": 1}


def test_a_missing_transcript_is_searched_again_not_handed_out(reg, tmp_path):
    os.remove(tmp_path / "c1.txt")
    out = reading.prepare(reg, NOW, base=str(tmp_path / "reading"))
    assert (out["calls"], out["transcript_missing"]) == (2, 1)
    assert reg.call("c1")["transcript_state"] == S.T_NOT_LOOKED_UP


def test_no_api_client_is_left_in_the_module():
    root = os.path.join(os.path.dirname(__file__), "..", "analytics", "convintel")
    for name in os.listdir(root):
        if name.endswith(".py"):
            src = open(os.path.join(root, name), encoding="utf-8").read()
            assert "import anthropic" not in src and "from anthropic" not in src, name
