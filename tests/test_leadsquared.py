import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from integrations.leadsquared import LeadSquaredClient, LeadSquaredError, format_datetime, to_attributes

HOST = "https://api-test.leadsquared.com/v2/"


@pytest.fixture
def client():
    return LeadSquaredClient("ak", "sk", host=HOST, max_retries=1)


def query(call):
    return {k: v[0] for k, v in parse_qs(urlparse(call.request.url).query).items()}


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("LEADSQUARED_ACCESS_KEY", raising=False)
    monkeypatch.delenv("LEADSQUARED_SECRET_KEY", raising=False)
    with pytest.raises(LeadSquaredError, match="Missing credentials"):
        LeadSquaredClient()


def test_reads_credentials_from_env(monkeypatch):
    monkeypatch.setenv("LEADSQUARED_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("LEADSQUARED_SECRET_KEY", "env-sk")
    monkeypatch.setenv("LEADSQUARED_HOST", "https://x.leadsquared.com/v2")
    c = LeadSquaredClient()
    assert (c.access_key, c.secret_key, c.host) == ("env-ak", "env-sk", "https://x.leadsquared.com/v2/")


def test_helpers():
    assert to_attributes({"FirstName": "A", "Phone": None}) == [
        {"Attribute": "FirstName", "Value": "A"},
        {"Attribute": "Phone", "Value": ""},
    ]
    ist = timezone(timedelta(hours=5, minutes=30))
    assert format_datetime(datetime(2026, 1, 1, 10, 0, tzinfo=ist)) == "2026-01-01 04:30:00"


@responses.activate
def test_auth_params_sent(client):
    responses.get(HOST + "LeadManagement.svc/Leads.GetById", json=[{"ProspectID": "p1"}])
    assert client.get_lead_by_id("p1") == {"ProspectID": "p1"}
    q = query(responses.calls[0])
    assert q == {"accessKey": "ak", "secretKey": "sk", "id": "p1"}


@responses.activate
def test_get_lead_by_email_none(client):
    responses.get(HOST + "LeadManagement.svc/Leads.GetByEmailaddress", json=[])
    assert client.get_lead_by_email("nobody@example.com") is None


@responses.activate
def test_create_lead(client):
    responses.post(
        HOST + "LeadManagement.svc/Lead.Create",
        json={"Status": "Success", "Message": {"Id": "new-id"}},
    )
    assert client.create_lead({"FirstName": "Asha", "EmailAddress": "a@x.com"}) == "new-id"
    assert json.loads(responses.calls[0].request.body) == [
        {"Attribute": "FirstName", "Value": "Asha"},
        {"Attribute": "EmailAddress", "Value": "a@x.com"},
    ]


@responses.activate
def test_upsert_adds_search_by(client):
    responses.post(
        HOST + "LeadManagement.svc/Lead.CreateOrUpdate",
        json={"Status": "Success", "Message": {"Id": "id1"}},
    )
    client.upsert_lead({"Phone": "9999999999"}, search_by="Phone")
    body = json.loads(responses.calls[0].request.body)
    assert body[-1] == {"Attribute": "SearchBy", "Value": "Phone"}


@responses.activate
def test_update_lead(client):
    responses.post(HOST + "LeadManagement.svc/Lead.Update", json={"Status": "Success"})
    client.update_lead("p1", {"mx_City": "Delhi"})
    assert query(responses.calls[0])["leadId"] == "p1"


@responses.activate
def test_iter_leads_pages(client):
    url = HOST + "LeadManagement.svc/Leads.Get"
    responses.post(url, json=[{"id": 1}, {"id": 2}])
    responses.post(url, json=[{"id": 3}])
    assert [l["id"] for l in client.iter_leads("mx_City", "Delhi", page_size=2)] == [1, 2, 3]
    pages = [json.loads(c.request.body)["Paging"]["PageIndex"] for c in responses.calls]
    assert pages == [1, 2]


@responses.activate
def test_post_activity(client):
    responses.post(
        HOST + "ProspectActivity.svc/Create",
        json={"Status": "Success", "Message": {"Id": "act1"}},
    )
    when = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    assert client.post_activity("p1", 201, "Called", when, {"mx_Custom_1": "x"}) == "act1"
    body = json.loads(responses.calls[0].request.body)
    assert body == {
        "RelatedProspectId": "p1",
        "ActivityEvent": 201,
        "ActivityNote": "Called",
        "ActivityDateTime": "2026-10-07 12:00:00",
        "Fields": [{"SchemaName": "mx_Custom_1", "Value": "x"}],
    }


@responses.activate
def test_error_response_raises(client, monkeypatch):
    monkeypatch.setattr("integrations.leadsquared.client.time.sleep", lambda s: None)
    responses.post(
        HOST + "LeadManagement.svc/Lead.Create",
        status=500,
        json={"Status": "Error", "ExceptionType": "MXDuplicateEntryException", "ExceptionMessage": "Duplicate"},
    )
    with pytest.raises(LeadSquaredError, match="Duplicate") as exc:
        client.create_lead({"EmailAddress": "a@x.com"})
    assert exc.value.status_code == 500
    assert len(responses.calls) == 2  # retried once


@responses.activate
def test_retries_on_429(client, monkeypatch):
    monkeypatch.setattr("integrations.leadsquared.client.time.sleep", lambda s: None)
    url = HOST + "LeadManagement.svc/LeadsMetaData.Get"
    responses.get(url, status=429)
    responses.get(url, json=[{"SchemaName": "FirstName"}])
    assert client.get_lead_metadata() == [{"SchemaName": "FirstName"}]
