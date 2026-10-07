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
            "support_calls": [{"start_time": "2026-10-06T18:00:00.000Z", "transcript": {"text": ""}}],  # IST -> 12:30 UTC
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


# --------------------------------------------------------------- timezone rules

from datetime import datetime, timedelta, timezone  # noqa: E402

from integrations.transcripts import IST, Call, RequestBudgetExceeded, detect_call_timezone  # noqa: E402

NOW = datetime(2026, 10, 7, 11, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("kind,raw,expected", [
    # support is always IST, even when createdAt == start_time (older records)
    ("support", {"start_time": "2025-10-01T15:00:00.000Z", "createdAt": "2025-10-01T15:00:00.000Z"}, IST),
    # Acefone sales recordings are genuine UTC, even with a 5.5h createdAt lag
    ("sales", {"start_time": "2026-10-06T06:00:00.000Z", "createdAt": "2026-10-06T11:30:00.000Z",
               "s3_audio_file_url": "https://console.acefone.in/file/recording?callId=1"}, timezone.utc),
    # S3 /recordings/ sales are IST, even when created much later
    ("sales", {"start_time": "2026-10-06T19:00:00.000Z", "createdAt": "2026-10-07T03:00:00.000Z",
               "s3_audio_file_url": "https://x.s3.amazonaws.com/recordings/abc.mp3"}, IST),
    # mixed sources: created before start -> IST
    ("sales", {"start_time": "2026-10-06T15:00:00.000Z", "createdAt": "2026-10-06T09:35:00.000Z",
               "s3_audio_file_url": "https://x.s3.amazonaws.com/audio/1.mp3"}, IST),
    # mixed sources: start in the future -> IST
    ("sales", {"start_time": "2026-10-07T14:00:00.000Z"}, IST),
    # mixed sources: created shortly after start -> UTC
    ("sales", {"start_time": "2026-10-06T07:00:00.000Z", "createdAt": "2026-10-06T07:30:00.000Z",
               "s3_audio_file_url": "https://x.s3.amazonaws.com/audio/2.mp3"}, timezone.utc),
])
def test_detect_call_timezone(kind, raw, expected):
    assert detect_call_timezone(kind, raw, now=NOW) is expected


def test_call_times_relabelled_not_shifted():
    c = Call.from_api("p", "support", {"start_time": "2026-10-06T17:23:28.000Z",
                                        "end_time": "2026-10-06T17:25:28.000Z"})
    assert c.source_tz == "IST"
    assert (c.start_time.hour, c.start_time.utcoffset()) == (17, timedelta(hours=5, minutes=30))
    assert c.start_time.astimezone(timezone.utc).hour == 11
    assert c.end_time.tzinfo is IST


# --------------------------------------------------------------- request budget

def test_batch_size_cannot_exceed_api_limit():
    with pytest.raises(ValueError):
        TranscriptClient("key", base_url=BASE, batch_size=11)


@pytest.mark.parametrize("n", [0, 10, 50])
def test_max_requests_strictly_below_10(n):
    with pytest.raises(ValueError):
        TranscriptClient("key", base_url=BASE, max_requests=n)


def test_max_requests_from_env(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_MAX_REQUESTS_PER_RUN", "3")
    assert TranscriptClient("key", base_url=BASE).max_requests == 3
    monkeypatch.setenv("TRANSCRIPT_MAX_REQUESTS_PER_RUN", "10")
    with pytest.raises(ValueError):
        TranscriptClient("key", base_url=BASE)


@responses.activate
def test_search_over_budget_sends_nothing():
    c = TranscriptClient("key", base_url=BASE, max_requests=9)
    numbers = [f"90000{i:05d}" for i in range(91)]  # 10 batches of 10 -> over 9
    with pytest.raises(RequestBudgetExceeded, match="needs 10 request"):
        c.search(numbers)
    assert len(responses.calls) == 0 and c.requests_made == 0


@responses.activate
def test_budget_tracked_across_calls():
    responses.get(SEARCH, json={})
    c = TranscriptClient("key", base_url=BASE, max_requests=3, max_retries=0)
    c.search([f"90000{i:05d}" for i in range(20)])     # 2 requests
    assert (c.requests_made, c.requests_remaining) == (2, 1)
    with pytest.raises(RequestBudgetExceeded):
        c.search([f"91000{i:05d}" for i in range(11)])  # needs 2, 1 left
    assert len(responses.calls) == 2


@responses.activate
def test_retries_count_against_budget(monkeypatch):
    monkeypatch.setattr("integrations.transcripts.client.time.sleep", lambda s: None)
    responses.get(SEARCH, status=503)
    c = TranscriptClient("key", base_url=BASE, max_requests=2, max_retries=5)
    with pytest.raises(RequestBudgetExceeded):
        c.search(["9000000001"])
    assert len(responses.calls) == 2
