import pytest
import responses

from integrations.timepay import TimePayClient, TimePayError
from integrations.zoom import ZoomClient, ZoomError, attendance, meeting_path_id
from integrations.zoom.client import API_BASE as ZOOM, TOKEN_URL as ZTOKEN

BASE = "https://tp.test/api/v1"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for p in ("ZOOM", "ZOOM_MKT", "ZOOM_WEBINAR"):
        for k in ("ACCOUNT_ID", "CLIENT_ID", "CLIENT_SECRET"):
            monkeypatch.delenv(f"{p}_{k}", raising=False)
    for k in ("TIMEPAY_TOKEN", "TIMEPAY_BASE_URL", "TIMEPAY_ORG_ID", "TIMEPAY_AUTH_HEADER"):
        monkeypatch.delenv(k, raising=False)


def test_zoom_account_prefix(monkeypatch):
    with pytest.raises(ZoomError, match="ZOOM_MKT_ACCOUNT_ID"):
        ZoomClient("marketing")
    with pytest.raises(ZoomError, match="Unknown"):
        ZoomClient("other", "a", "b", "c")
    monkeypatch.setenv("ZOOM_MKT_ACCOUNT_ID", "acc")
    monkeypatch.setenv("ZOOM_MKT_CLIENT_ID", "id")
    monkeypatch.setenv("ZOOM_MKT_CLIENT_SECRET", "sec")
    assert ZoomClient("marketing").account_id == "acc"


def test_meeting_path_id_double_encodes_odd_uuids():
    assert meeting_path_id(123) == "123"
    assert meeting_path_id("abc==") == "abc%3D%3D"
    assert meeting_path_id("/ab//c=") == "%252Fab%252F%252Fc%253D"


def test_attendance_sums_rejoins():
    rows = attendance([
        {"user_email": "A@x.com", "name": "Asha", "duration": 600, "join_time": "2026-10-05T13:00:00Z",
         "leave_time": "2026-10-05T13:10:00Z"},
        {"user_email": "a@x.com", "name": "Asha", "duration": 1200, "join_time": "2026-10-05T13:15:00Z",
         "leave_time": "2026-10-05T13:35:00Z"},
        {"user_email": "", "name": "Ravi", "duration": 60},
        {"user_email": "", "name": "", "duration": 60},
    ])
    assert rows[0] == {"email": "a@x.com", "name": "Asha", "minutes": 30.0, "sessions": 2,
                       "first_join": "2026-10-05T13:00:00Z", "last_leave": "2026-10-05T13:35:00Z"}
    assert rows[1]["name"] == "Ravi" and len(rows) == 2


@responses.activate
def test_zoom_token_and_paging():
    responses.post(ZTOKEN, json={"access_token": "zt", "expires_in": 3600})
    responses.get(f"{ZOOM}/report/meetings/abc%3D%3D/participants",
                  json={"participants": [{"name": "A"}], "next_page_token": "n"})
    responses.get(f"{ZOOM}/report/meetings/abc%3D%3D/participants", json={"participants": [{"name": "B"}]})
    z = ZoomClient("main", "acc", "id", "sec", max_retries=0)
    assert [p["name"] for p in z.meeting_participants("abc==")] == ["A", "B"]
    tok = responses.calls[0].request
    assert "grant_type=account_credentials" in tok.url and "account_id=acc" in tok.url
    assert tok.headers["Authorization"].startswith("Basic ")
    assert responses.calls[1].request.headers["Authorization"] == "Bearer zt"
    assert "next_page_token=n" in responses.calls[2].request.url


def test_timepay_needs_credentials():
    with pytest.raises(TimePayError, match="TIMEPAY_TOKEN"):
        TimePayClient()


@responses.activate
def test_timepay_headers_paging_and_max_pages():
    page = lambda n, more: {"success": True, "data": [{"id": n}], "pagination": {"page": n, "hasNext": more}}
    responses.get(f"{BASE}/logs", json=page(1, True))
    responses.get(f"{BASE}/logs", json=page(2, True))
    responses.get(f"{BASE}/logs", json=page(3, False))
    tp = TimePayClient("tp_x", "org1", BASE + "/", max_retries=0)
    assert [r["id"] for r in tp.iter_logs("2026-10-08T00:00:00", "2026-10-08T23:59:59", max_pages=2, type="call")] == [1, 2]
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Bearer tp_x" and req.headers["x-org-id"] == "org1"
    assert "type=call" in req.url and "start_time=2026-10-08T00%3A00%3A00" in req.url and "page=1" in req.url
    assert "page=2" in responses.calls[1].request.url


@responses.activate
def test_timepay_count_custom_header_and_failure():
    responses.get(f"{BASE}/logs", json={"success": True, "total": 43378})
    tp = TimePayClient("tp_x", None, BASE, auth_header="x-api-key", max_retries=0)
    assert tp.count_logs(type="call") == 43378
    req = responses.calls[0].request
    assert req.headers["x-api-key"] == "tp_x" and "x-org-id" not in req.headers and "count_only=true" in req.url
    responses.get(f"{BASE}/campaigns", json={"success": False, "message": "invalid org"})
    with pytest.raises(TimePayError, match="invalid org"):
        tp.campaigns()
