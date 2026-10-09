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


@pytest.mark.parametrize("raw", ["api-in21.leadsquared.com", "https://api-in21.leadsquared.com/",
                                 "https://api-in21.leadsquared.com/v2", "https://api-in21.leadsquared.com/v2/"])
def test_bare_or_full_host(raw):
    assert LeadSquaredClient("ak", "sk", host=raw).host == "https://api-in21.leadsquared.com/v2/"


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
    assert len(responses.calls) == 1  # validation errors are not retried


@responses.activate
def test_retries_on_429(client, monkeypatch):
    monkeypatch.setattr("integrations.leadsquared.client.time.sleep", lambda s: None)
    url = HOST + "LeadManagement.svc/LeadsMetaData.Get"
    responses.get(url, status=429)
    responses.get(url, json=[{"SchemaName": "FirstName"}])
    assert client.get_lead_metadata() == [{"SchemaName": "FirstName"}]


@responses.activate
def test_retries_plain_500(client, monkeypatch):
    monkeypatch.setattr("integrations.leadsquared.client.time.sleep", lambda s: None)
    url = HOST + "LeadManagement.svc/LeadsMetaData.Get"
    responses.get(url, status=502, body="Bad gateway")
    responses.get(url, json=[])
    assert client.get_lead_metadata() == []


def test_parse_phone_call():
    from integrations.leadsquared import parse_phone_call
    act = {
        "ProspectActivityId": "a1", "RelatedProspectId": "p1", "ActivityEvent": "22",
        "CreatedOn": "2026-10-06 06:37:33",
        "ActivityEvent_Note": 'Caller{=}Faisal Bashir{next}UserId{=}{next}UserId{=}u1{next}Duration{=}41'
                              '{next}Status{=}Answered{next}ResourceURL{=}https://r/x.wav{next}'
                              'SourceData{=}{"SourceNumber":"700","DestinationNumber":"600","Direction":"Outbound"}{next}',
    }
    c = parse_phone_call(act)
    assert c["user_id"] == "u1" and c["caller"] == "Faisal Bashir"
    assert (c["direction"], c["status"], c["duration"], c["lead_number"]) == ("outbound", "Answered", 41, "600")


@responses.activate
def test_iter_activities_by_event_pages(client):
    url = HOST + "ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent"
    responses.post(url, json={"RecordCount": 3, "List": [{"id": 1}, {"id": 2}]})
    responses.post(url, json={"RecordCount": 3, "List": [{"id": 3}]})
    got = list(client.iter_activities_by_event(22, datetime(2026, 10, 1), datetime(2026, 10, 2), page_size=2))
    assert [g["id"] for g in got] == [1, 2, 3]
    body = json.loads(responses.calls[0].request.body)
    assert body["Parameter"] == {"FromDate": "2026-10-01 00:00:00", "ToDate": "2026-10-02 00:00:00", "ActivityEvent": 22}


@responses.activate
def test_iter_activities_started_reads_later_edits_and_keeps_by_start(client):
    """The API filters on ModifiedOn: a day's calls edited later must still be found, and older calls edited on the day dropped."""
    from integrations.timeutil import IST

    url = HOST + "ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent"
    day1 = [{"ProspectActivityId": "a1", "CreatedOn": "2026-10-05 05:00:00", "ModifiedOn": "2026-10-05 05:02:00"},
            {"ProspectActivityId": "old", "CreatedOn": "2026-09-20 05:00:00", "ModifiedOn": "2026-10-05 07:00:00"},
            {"ProspectActivityId": "a2", "CreatedOn": "2026-10-05 06:00:00", "ModifiedOn": "2026-10-05 06:01:00"},
            {"ProspectActivityId": "bad", "CreatedOn": "", "ModifiedOn": "2026-10-05 10:00:00"}]  # kept, so it gets counted
    day2 = [{"ProspectActivityId": "midnight", "CreatedOn": "2026-10-05 18:28:00", "ModifiedOn": "2026-10-05 18:33:00"},
            {"ProspectActivityId": "a2", "CreatedOn": "2026-10-05 06:00:00", "ModifiedOn": "2026-10-06 04:00:00"},  # repeat
            {"ProspectActivityId": "next", "CreatedOn": "2026-10-05 19:00:00", "ModifiedOn": "2026-10-05 19:00:30"}]
    responses.post(url, json={"List": day1})
    responses.post(url, json={"List": day2})
    d0 = datetime(2026, 10, 5, tzinfo=IST)
    got = list(client.iter_activities_started(22, d0, d0.replace(hour=23, minute=59, second=59),
                                              margin=timedelta(days=1), now=datetime(2026, 10, 9, tzinfo=IST)))
    assert [g["ProspectActivityId"] for g in got] == ["a1", "a2", "bad", "midnight"]
    windows = [json.loads(c.request.body)["Parameter"] for c in responses.calls]
    assert [(w["FromDate"], w["ToDate"]) for w in windows] == [("2026-10-04 18:30:00", "2026-10-05 18:29:59"),
                                                              ("2026-10-05 18:30:00", "2026-10-06 18:29:59")]


@responses.activate
def test_iter_activities_started_never_reads_past_now(client):
    from integrations.timeutil import IST

    url = HOST + "ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent"
    responses.post(url, json={"List": [{"ProspectActivityId": "x", "CreatedOn": "2026-10-04 19:00:00", "ModifiedOn": ""}]})
    d0 = datetime(2026, 10, 5, tzinfo=IST)
    got = list(client.iter_activities_started(22, d0, d0 + timedelta(days=1, seconds=-1), now=d0 + timedelta(hours=10)))
    assert [g["ProspectActivityId"] for g in got] == ["x"]       # bad ModifiedOn is fine: CreatedOn decides
    (w,) = [json.loads(c.request.body)["Parameter"] for c in responses.calls]
    assert w["ToDate"] == "2026-10-05 04:30:00"


def test_parse_phone_call_keeps_the_edit_time_and_ignores_note_start_times():
    from integrations.leadsquared import parse_phone_call
    act = {"ActivityEvent": "22", "CreatedOn": "2026-10-05 14:29:02", "ModifiedOn": "2026-10-05 14:29:34",
           "ActivityEvent_Note": 'StartTime{=}10/5/2026 2:29:02 PM{next}'
                                 'SourceData{=}{"StartTime":"2026-10-05 19:59:02","DestinationNumber":"600"}{next}'}
    c = parse_phone_call(act)
    assert (c["start_utc"], c["modified_utc"]) == ("2026-10-05 14:29:02", "2026-10-05 14:29:34")
