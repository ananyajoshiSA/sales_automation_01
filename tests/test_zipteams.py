import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import responses

from integrations.env import load_dotenv
from integrations.zipteams import ZipteamsClient, ZipteamsError, clean, to_e164, to_utc_z

INGEST = "https://ingest.test/calls"
SYNC = "https://zip.test/customer-sync"
PARTNER = "https://zip.test/partner"
ZIP_ENV = ["ZIPTEAMS_API_KEY", "ZIPTEAMS_API_SECRET", "ZIPTEAMS_TENANT_ID", "ZIPTEAMS_SUB_TENANT_ID"]

CALL = {
    "call": {"id": "c1", "recording_url": "https://rec.test/1.mp3", "start_time": "2026-10-06T15:30:00+05:30",
             "end_time": "2026-10-06T15:35:00+05:30", "phone_number": "98765 43210"},
    "agent": {"id": "a1", "email": "asha@example.com", "name": ""},
    "customer": {"id": "lead-1", "name": "Ravi"},
}


@pytest.fixture(autouse=True)
def no_zip_env(monkeypatch):
    for k in ZIP_ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def customer():
    return ZipteamsClient("key", ingest_url=INGEST, customer_sync_url=SYNC, max_retries=0)


@pytest.fixture
def partner():
    return ZipteamsClient("pk", api_secret="ps", tenant_id="t", sub_tenant_id="st",
                          partner_base=PARTNER, max_retries=0)


def body(i=0):
    return json.loads(responses.calls[i].request.body)


def test_helpers():
    assert to_e164("098765 43210") == "+919876543210"
    assert to_e164("") is None
    assert to_utc_z("2026-10-06T15:30:00+05:30") == "2026-10-06T10:00:00Z"
    assert to_utc_z(datetime(2026, 10, 6, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))) == "2026-10-06T04:30:00Z"
    with pytest.raises(ZipteamsError):
        to_utc_z("2026-10-06T10:00:00")
    assert clean({"a": "", "b": None, "c": [{"d": "", "e": 1}]}) == {"c": [{"e": 1}]}


def test_missing_key():
    with pytest.raises(ZipteamsError):
        ZipteamsClient()


def test_mode_from_env(monkeypatch):
    monkeypatch.setenv("ZIPTEAMS_API_KEY", "ck")
    assert ZipteamsClient().mode == "customer"
    monkeypatch.setenv("ZIPTEAMS_API_SECRET", "ps")
    assert ZipteamsClient().mode == "customer"  # partner needs secret + both tenant ids
    for k in ["ZIPTEAMS_TENANT_ID", "ZIPTEAMS_SUB_TENANT_ID"]:
        monkeypatch.setenv(k, "x")
    assert ZipteamsClient().mode == "partner"


@responses.activate
def test_customer_sync_calls(customer):
    responses.post(INGEST, json={"message": "ok"})
    assert customer.sync_calls([CALL]) == {"message": "ok"}
    req = responses.calls[0].request
    assert req.headers["x-zip-api-key"] == "key"
    sent = body()["data"][0]
    assert sent["call"] == {"id": "c1", "recording_url": "https://rec.test/1.mp3",
                            "start_time": "2026-10-06T15:30:00+05:30", "phone_number": "+919876543210"}
    assert sent["agent"] == {"id": "a1", "email": "asha@example.com"}  # empty name dropped


@responses.activate
def test_partner_sync_calls(partner):
    responses.post(PARTNER + "/ingest/batch-call", json={"success": True})
    partner.sync_calls([CALL])
    headers = responses.calls[0].request.headers
    assert (headers["x-api-key"], headers["x-api-secret"], headers["x-tenant-id"], headers["x-sub-tenant-id"]) == \
        ("pk", "ps", "t", "st")
    assert body()["data"][0]["call"] == {"id": "c1", "recording_url": "https://rec.test/1.mp3",
                                         "start_time": "2026-10-06T10:00:00Z", "end_time": "2026-10-06T10:05:00Z",
                                         "contact_number": "+919876543210"}


def test_partner_sync_requires_fields(partner):
    with pytest.raises(ZipteamsError, match="customer.id"):
        partner.sync_calls([{**CALL, "customer": {}}])


@responses.activate
def test_dispositions(customer, partner):
    responses.post(INGEST, json={"message": "ok"})
    responses.put(PARTNER + "/ingest/disposition-status", json={"success": True})
    rec = {"agent": {"id": "a1", "email": "asha@example.com"},
           "customer": {"id": "lead-1", "phone_number": "9876543210", "disposition_status": "Interested"}}
    customer.update_dispositions([rec])
    assert body(0)["type"] == "disposition-status"
    assert body(0)["data"][0]["customer"]["phone_number"] == "+919876543210"
    partner.update_dispositions([rec])
    assert body(1) == {"customer_id": "lead-1", "disposition_status": "Interested"}


@responses.activate
def test_upsert_customer(customer, partner):
    responses.post(SYNC, json={"ok": True})
    customer.upsert_customer("asha@example.com", name="Ravi", phone_number="9876543210", email="")
    assert body() == {"agent_email": "asha@example.com", "name": "Ravi", "phone_number": "+919876543210"}
    with pytest.raises(ZipteamsError):
        partner.upsert_customer("asha@example.com")


@responses.activate
def test_http_error(customer):
    responses.post(INGEST, status=401, json={"message": "bad key"})
    with pytest.raises(ZipteamsError) as exc:
        customer.sync_calls([CALL])
    assert exc.value.status_code == 401


def test_load_dotenv(tmp_path, monkeypatch):
    f = tmp_path / "secrets.env"
    f.write_text('# c\nA_KEY=u$r1\nB_KEY="quoted"\nexport C_KEY=3\nKEEP=file\nBLANK=\n')
    monkeypatch.setenv("KEEP", "env")
    for k in ["A_KEY", "B_KEY", "C_KEY", "BLANK"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SALES_SKILL_ENV_FILE", str(f))
    assert load_dotenv() == {"A_KEY": "u$r1", "B_KEY": "quoted", "C_KEY": "3"}
    assert os.environ["KEEP"] == "env"
    assert "BLANK" not in os.environ
    for k in ["A_KEY", "B_KEY", "C_KEY"]:
        monkeypatch.delenv(k)
