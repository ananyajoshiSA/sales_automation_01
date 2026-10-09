import pytest
import responses

from integrations.growthx import GrowthXClient, GrowthXError, captured_ist

URL = "https://gx.test/api/public/leads"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("GROWTHX_TOKEN", raising=False)
    monkeypatch.delenv("GROWTHX_URL", raising=False)


def page(rows, more, n=1):
    return {"meta": {"page": n, "pageSize": 10000, "total": 99, "hasNextPage": more}, "leads": rows}


def test_captured_ist_formats():
    assert captured_ist("9 Oct 2026, 6:25 pm").isoformat() == "2026-10-09T18:25:00+05:30"
    assert captured_ist("26 Sept 2026, 12:05 am").isoformat() == "2026-09-26T00:05:00+05:30"
    assert captured_ist("1 Jul 2026, 12:40 pm").hour == 12
    for bad in (None, "", "2026-10-09T18:25", "31 Sept 2026, 1:00 pm", "9 Oct 2026, 13:00 pm"):
        assert captured_ist(bad) is None


def test_needs_credentials():
    with pytest.raises(GrowthXError, match="GROWTHX_TOKEN"):
        GrowthXClient()


@responses.activate
def test_paging_dedupes_and_sends_bearer():
    responses.get(URL, json=page([{"id": "a"}, {"id": "b"}], True))
    responses.get(URL, json=page([{"id": "b"}, {"id": "c"}], False, 2))
    gx = GrowthXClient("tok", URL, max_retries=0)
    assert [r["id"] for r in gx.leads("2026-10-07", "2026-10-08", leadtype="hr-oct")] == ["a", "b", "c"]
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok"
    assert "from=2026-10-07" in req.url and "to=2026-10-08" in req.url and "leadtype=hr-oct" in req.url
    assert "page=2" in responses.calls[1].request.url


@responses.activate
def test_ist_days_widen_utc_window_and_keep_by_capture_time():
    responses.get(URL, json=page([
        {"id": "late", "capturedAt": "9 Oct 2026, 12:10 am"},
        {"id": "in", "capturedAt": "8 Oct 2026, 11:59 pm"},
        {"id": "first", "capturedAt": "8 Oct 2026, 12:00 am"},
        {"id": "before", "capturedAt": "7 Oct 2026, 11:59 pm"},
        {"id": "odd", "capturedAt": "yesterday"},
    ], False))
    rows, bad = GrowthXClient("tok", URL, max_retries=0).leads_for_ist_days("2026-10-08", "2026-10-08")
    assert [r["id"] for r in rows] == ["in", "first"] and bad == 1
    assert rows[1]["captured_ist"] == "2026-10-08T00:00"
    assert "from=2026-10-07" in responses.calls[0].request.url and "to=2026-10-08" in responses.calls[0].request.url


@responses.activate
def test_error_answer_raises():
    responses.get(URL, json={"error": "from must be in YYYY-MM-DD format"}, status=400)
    with pytest.raises(GrowthXError, match="HTTP 400"):
        GrowthXClient("tok", URL, max_retries=0).total("bad")
    responses.get(URL, json={"error": "odd"})
    with pytest.raises(GrowthXError, match="unexpected"):
        GrowthXClient("tok", URL, max_retries=0).total()
