import json

import pytest
import responses

from integrations.google_ads import GoogleAdsClient, GoogleAdsError
from integrations.google_ads.client import API_BASE as GADS, TOKEN_URL as GTOKEN
from integrations.meta_ads import MetaAdsClient, MetaAdsError, act_id, lead_count
from integrations.meta_ads.client import GRAPH

GOOGLE_ENV = ["GOOGLE_DEV_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
              "GOOGLE_LOGIN_CUSTOMER_ID", "GOOGLE_CUSTOMER_IDS", "GOOGLE_API_VERSION", "META_TOKEN",
              "META_GRAPH_VERSION"]


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for k in GOOGLE_ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def gads():
    return GoogleAdsClient("dev", "cid", "sec", "ref", login_customer_id="123-456-7890",
                           customer_ids=["111-222-3333"], version="v22", max_retries=0)


def test_google_needs_credentials():
    with pytest.raises(GoogleAdsError, match="GOOGLE_DEV_TOKEN"):
        GoogleAdsClient()


@responses.activate
def test_google_campaign_daily_pages_and_converts_micros(gads):
    responses.post(GTOKEN, json={"access_token": "tok", "expires_in": 3600})
    url = f"{GADS}/v22/customers/1112223333/googleAds:search"
    row = {"segments": {"date": "2026-10-05"}, "campaign": {"id": "9", "name": "IDB", "status": "ENABLED"},
           "metrics": {"costMicros": "1500000000", "impressions": "100", "clicks": "7", "conversions": 2.0}}
    responses.post(url, json={"results": [row], "nextPageToken": "p2"})
    responses.post(url, json={"results": [row]})
    rows = gads.campaign_daily("2026-10-05", "2026-10-05")
    assert len(rows) == 2 and rows[0]["cost_inr"] == 1500.0 and rows[0]["clicks"] == 7
    search = [c for c in responses.calls if "googleAds:search" in c.request.url]
    assert json.loads(search[1].request.body)["pageToken"] == "p2"
    h = search[0].request.headers
    assert h["Authorization"] == "Bearer tok" and h["developer-token"] == "dev" and h["login-customer-id"] == "1234567890"
    assert len([c for c in responses.calls if c.request.url == GTOKEN]) == 1  # token cached


@responses.activate
def test_google_error_does_not_leak_token(gads):
    responses.post(GTOKEN, json={"access_token": "tok", "expires_in": 3600})
    responses.get(f"{GADS}/v22/customers:listAccessibleCustomers", status=403, json={"error": {"message": "denied"}})
    with pytest.raises(GoogleAdsError) as exc:
        gads.list_accessible_customers()
    assert exc.value.status_code == 403 and "tok" not in str(exc.value) and "ref" not in str(exc.value)


def test_meta_helpers():
    assert act_id("123") == "act_123" and act_id("act_123") == "act_123"
    # Same leads reported under two action types: count once.
    assert lead_count([{"action_type": "lead", "value": "4"},
                       {"action_type": "onsite_conversion.lead_grouped", "value": "4"},
                       {"action_type": "link_click", "value": "50"}]) == 4
    assert lead_count(None) == 0


@responses.activate
def test_meta_insights_follow_cursor_and_budgets_in_paise():
    m = MetaAdsClient("t", version="v21.0", max_retries=0)
    ins = f"{GRAPH}/v21.0/act_1/insights"
    responses.get(ins, json={"data": [{"campaign_id": "c", "campaign_name": "IDB", "spend": "250.50", "impressions": "10",
                                       "clicks": "2", "actions": [{"action_type": "lead", "value": "3"}],
                                       "date_start": "2026-10-05"}],
                             "paging": {"cursors": {"after": "A"}, "next": "https://graph/next"}})
    responses.get(ins, json={"data": [], "paging": {"cursors": {"after": "B"}}})
    rows = m.campaign_daily("1", "2026-10-05", "2026-10-05")
    assert rows == [{"account_id": "act_1", "date": "2026-10-05", "campaign_id": "c", "campaign": "IDB",
                     "spend_inr": 250.5, "impressions": 10, "clicks": 2, "leads": 3}]
    assert "after=A" in responses.calls[1].request.url
    responses.get(f"{GRAPH}/v21.0/act_1/campaigns", json={"data": [{"id": "c", "daily_budget": "50000"}]})
    assert m.campaigns("1")[0]["daily_budget_inr"] == 500.0


@responses.activate
def test_meta_error():
    responses.get(f"{GRAPH}/v21.0/me/adaccounts", status=400, json={"error": {"message": "bad token"}})
    with pytest.raises(MetaAdsError, match="HTTP 400"):
        MetaAdsClient("t", version="v21.0", max_retries=0).ad_accounts()


@responses.activate
def test_fetch_ad_spend_meta_only_skips_google(monkeypatch):
    from scripts import fetch_ad_spend

    responses.get(f"{GRAPH}/v21.0/me/adaccounts", json={"data": [{"id": "act_1"}]})
    responses.get(f"{GRAPH}/v21.0/act_1/insights", json={"data": [
        {"campaign_id": "c1", "campaign_name": "HR", "spend": "120.5", "date_start": "2026-10-08",
         "actions": [{"action_type": "lead", "value": "3"}]}]})
    monkeypatch.setenv("META_TOKEN", "m")
    rows = fetch_ad_spend.rows("2026-10-08", "2026-10-08", ("meta",))  # no Google credentials needed
    assert rows == [{"platform": "meta", "account_id": "act_1", "date": "2026-10-08", "campaign_id": "c1",
                     "campaign": "HR", "spend_inr": 120.5, "impressions": 0, "clicks": 0, "platform_leads": 3}]
    assert fetch_ad_spend.main(["2026-10-08", "2026-10-08", "x.csv", "--platform", "tiktok"]) == 1
