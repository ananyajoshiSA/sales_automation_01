import json
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from integrations.transcripts import TranscriptClient, TranscriptError, normalize_phone

BASE = "https://transcripts.test/api/v1/"
SEARCH = BASE + "webhook/search-by-numbers-v1"


@pytest.fixture
def client():
    return TranscriptClient("key", base_url=BASE, max_retries=0, batch_size=2)


@pytest.mark.parametrize("raw,expected", [
    ("+91-98765 43210", "919876543210"),
    ("09876543210", "919876543210"),
    ("9876543210", "919876543210"),
    ("919876543210", "919876543210"),
    ("91919876543210", "919876543210"),
    ("", None),
    (None, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_missing_key(monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_API_KEY", raising=False)
    with pytest.raises(TranscriptError):
        TranscriptClient()


@responses.activate
def test_search_batches_and_parses(client):
    responses.get(SEARCH, json={
        "919000000001": {
            "sales_call": [{"caller_id": "919000000001", "start_time": "2026-10-06T10:00:00.000Z",
                            "call_duration": 60, "agent_name": "Asha-Extension ",
                            "transcript": {"text": "Hello"}}],
            "support_calls": [{"start_time": "2026-10-06T12:00:00.000Z", "transcript": {"text": ""}}],
        },
        "919000000002": {"sales_call": [], "support_calls": []},
    })
    responses.get(SEARCH, json={"919000000003": {"sales_call": [], "support_calls": []}})

    calls = client.search(["9000000001", "+91 90000 00002", "9000000003", "9000000001"])

    assert [parse_qs(urlparse(c.request.url).query)["numbers"][0] for c in responses.calls] == [
        "919000000001,919000000002", "919000000003",
    ]
    assert responses.calls[0].request.headers["x-api-key"] == "key"
    assert [(c.kind, c.has_transcript) for c in calls] == [("support", False), ("sales", True)]
    assert calls[1].agent_name == "Asha-Extension"
    assert calls[1].duration == 60


@responses.activate
def test_generate_transcripts_sends_10_digit_numbers(client):
    responses.post(BASE + "webhook/generate-transcripts-by-phone", json={"ok": True})
    client.generate_transcripts(["+91 98765 43210", "8001950065"])
    assert json.loads(responses.calls[0].request.body) == [
        {"student_phone": "9876543210"}, {"student_phone": "8001950065"},
    ]


@responses.activate
def test_http_error(client):
    responses.get(SEARCH, status=401, json={"message": "Invalid API key"})
    with pytest.raises(TranscriptError, match="401"):
        client.search(["9000000001"])


def test_start_time_timezone_resolution():
    from datetime import timedelta
    from integrations.transcripts.client import IST, Call

    # created minutes after start -> genuine UTC
    utc = Call.from_api("p", "sales", {"start_time": "2025-12-05T07:04:54.000Z", "createdAt": "2025-12-05T07:39:40.000Z"})
    assert utc.start_time.utcoffset() == timedelta(0)

    # created "before" start -> start is IST wall-clock labelled Z
    ist = Call.from_api("p", "support", {"start_time": "2026-05-03T17:23:28.000Z",
                                          "end_time": "2026-05-03T17:25:28.000Z",
                                          "createdAt": "2026-05-03T11:55:18.000Z"})
    assert ist.start_time.tzinfo == IST and ist.start_time.hour == 17
    assert ist.end_time.tzinfo == IST
