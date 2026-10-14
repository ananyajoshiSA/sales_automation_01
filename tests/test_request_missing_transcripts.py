import json

import responses

from scripts import request_missing_transcripts as rmt

BASE = "https://transcripts.test/api/v1/"
GENERATE = BASE + "webhook/generate-transcripts-by-phone"


def _inputs(tmp_path, n_numbers=1):
    leads = tmp_path / "leads.csv"
    leads.write_text("lead_id,booked_ist\nL1,2026-10-05 12:00\n" + "".join(f"L{i},2026-10-05 12:00\n" for i in range(2, n_numbers + 1)))
    rows = [{"phone": "919000000001", "lead_id": "L1", "calls": [
        {"start_api": "2026-10-06T05:00:00+00:00", "duration": 300, "transcript": ""},        # counts
        {"start_api": "2026-10-06T06:00:00+00:00", "duration": 300, "transcript": "Hello"},   # has text
        {"start_api": "2026-10-06T07:00:00+00:00", "duration": 30, "transcript": ""},         # too short
        {"start_api": "2026-09-01T07:00:00+00:00", "duration": 300, "transcript": ""},        # long before booking
    ]}]
    rows += [{"phone": f"9190000{i:05d}", "lead_id": f"L{i}", "calls": [
        {"start_api": "2026-10-06T05:00:00+00:00", "duration": 90, "transcript": ""}]} for i in range(2, n_numbers + 1)]
    rows.append({"phone": "919000099999", "lead_id": "OTHER", "calls": [
        {"start_api": "2026-10-06T05:00:00+00:00", "duration": 300, "transcript": ""}]})   # not a lead to cover
    searched = tmp_path / "searched.jsonl"
    searched.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(leads), str(searched)


def test_missing_counts_only_long_untranscribed_calls_since_booking(tmp_path):
    leads, searched = _inputs(tmp_path)
    assert rmt.missing(leads, searched) == {"919000000001": 1}


@responses.activate
def test_main_sends_10_a_request_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("SALESA_API_KEY", "key")
    monkeypatch.setenv("SALESA_BASE_URL", BASE)
    responses.post(GENERATE, json={"ok": True})
    leads, searched = _inputs(tmp_path, n_numbers=12)
    log = str(tmp_path / "log.jsonl")
    sleeps = []
    rmt.main(leads, searched, log, sleep=sleeps.append)
    bodies = [json.loads(c.request.body) for c in responses.calls]
    assert [len(b) for b in bodies] == [10, 2]
    assert sleeps == [rmt.REQUEST_GAP]
    assert len(open(log).readlines()) == 12
    rmt.main(leads, searched, log, sleep=sleeps.append)      # nothing left to send
    assert len(responses.calls) == 2
