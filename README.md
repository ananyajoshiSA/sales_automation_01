# Sales-Skill

Integrations with sales platforms.

**Revenue plan:** the daily team report, nightly call plan and the plan's other actions are in
[docs/revenue_plan.md](docs/revenue_plan.md).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

**Credentials live in one file only:** `.env` in the repo root (git-ignored). Importing
`integrations` loads it automatically: values are taken literally, blank values are
skipped, and variables already set in the environment win. To keep the file elsewhere,
set `SALES_SKILL_ENV_FILE=/path/to/file`. In Claude Code cloud sessions, set the same
variables in the environment settings instead.

## LeadSquared

| Variable | Description |
|---|---|
| `LEADSQUARED_HOST` | API host, bare (`api-in21.leadsquared.com`) or full base URL; default `https://api-in21.leadsquared.com/v2/` |
| `LEADSQUARED_ACCESS_KEY` | Access key (Settings → API and Webhooks) |
| `LEADSQUARED_SECRET_KEY` | Secret key |

```python
from integrations.leadsquared import LeadSquaredClient

ls = LeadSquaredClient()                       # reads env vars
lead_id = ls.upsert_lead({"FirstName": "Asha", "EmailAddress": "asha@example.com"})
ls.update_lead(lead_id, {"mx_City": "Delhi"})
ls.post_activity(lead_id, activity_event=201, note="Discovery call done")
lead = ls.get_lead_by_email("asha@example.com")
for l in ls.iter_leads("mx_City", "Delhi"):
    ...
```

Available: lead metadata, get by id/email/phone, search + auto-paging, create, update,
create-or-update, activity types, list/post activities, users. Retries on 429/5xx.

CLI (read-only):

```bash
python -m integrations.leadsquared check
python -m integrations.leadsquared fields          # all lead field schema names
python -m integrations.leadsquared activity-types  # ActivityEvent codes
python -m integrations.leadsquared users
python -m integrations.leadsquared lead-by-email someone@example.com
```

## Call transcripts

| Variable | Description |
|---|---|
| `SALESA_BASE_URL` | default `https://centralized-transcript-api.altlapps.com/api/v1/` |
| `SALESA_API_KEY` | `x-api-key` header value |

The older names `TRANSCRIPT_API_BASE` / `TRANSCRIPT_API_KEY` / `TRANSCRIPT_MAX_REQUESTS_PER_RUN` still work.

```python
from integrations.transcripts import TranscriptClient

tc = TranscriptClient()
calls = tc.search(["+91-9876543210", "9000000000"])   # any phone format; batched 10 per request
for c in calls:
    print(c.kind, c.agent_name, c.start_time, c.duration, c.has_transcript)
tc.generate_transcripts([c.phone for c in calls if not c.has_transcript])
```

**Request limits.** At most 10 numbers per search request (API limit) and fewer than
10 requests per run: each `TranscriptClient` has a budget of 9 requests (retries
count; lower it with `SALESA_MAX_REQUESTS_PER_RUN`). Work that would go over is
refused with `RequestBudgetExceeded` before anything is sent — so one run covers
up to 90 numbers.

**Timezones.** The API labels every time `Z`, but several sources send IST. Each
call's `start_time` is returned timezone-aware and `source_tz` says which it was:
support calls → IST; sales via Acefone → UTC; sales with S3 `/recordings/` → IST;
other sales are inferred (created before the call started, or a start in the
future → IST, else UTC).

## Zipteams

| Variable | Description |
|---|---|
| `ZIPTEAMS_API_KEY` | Customer API key (`x-zip-api-key` header) |
| `ZIPTEAMS_API_SECRET`, `ZIPTEAMS_TENANT_ID`, `ZIPTEAMS_SUB_TENANT_ID` | Partner API; set all three to switch to it |
| `ZIPTEAMS_INGEST_URL` / `ZIPTEAMS_CUSTOMER_SYNC_URL` / `ZIPTEAMS_PARTNER_BASE` | Endpoint overrides (defaults set) |

```python
from integrations.zipteams import ZipteamsClient

zt = ZipteamsClient()          # Customer API unless all Partner credentials are set
zt.sync_calls([{"call": {"id": "c1", "recording_url": "https://...", "start_time": "2026-10-06T15:30:00+05:30",
                         "phone_number": "9876543210"},
                "agent": {"id": "a1", "email": "asha@example.com"}}])
zt.update_dispositions([{"agent": {"id": "a1", "email": "asha@example.com"},
                         "customer": {"phone_number": "9876543210", "disposition_status": "Interested"}}])
zt.upsert_customer("asha@example.com", name="Ravi", phone_number="9876543210")
```

Every call writes to Zipteams (phones go out as E.164). Zipteams' analysis comes back
into LeadSquared as "Zipteams Notes" activities, which is what `analytics/` reads.

## Google Ads, Meta Ads, Zoom and TimePay (read-only)

These four clients only read. They feed the Revenue Boost levers like this:

| Source | What it adds | Lever |
|---|---|---|
| Google Ads, Meta Ads | Daily spend, clicks and platform leads per campaign | Calling the right leads first, once campaigns are joined to LeadSquared enrolments |
| Zoom | Who attended each bootcamp or webinar, and for how long | Calling the right leads first; speed to lead after a session |
| TimePay | AI voice-agent campaigns and call logs (reminders, confirmations) | Speed to lead; returning leads' calls |

| Variable | Description |
|---|---|
| `GOOGLE_DEV_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` | Google Ads OAuth app and refresh token |
| `GOOGLE_LOGIN_CUSTOMER_ID`, `GOOGLE_CUSTOMER_IDS` | Manager account, and the ad accounts to report on (comma-separated) |
| `META_TOKEN`, `META_GRAPH_VERSION` | System User token with `ads_read`; Graph version (default `v21.0`) |
| `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` | Main Zoom Server-to-Server app; `ZOOM_MKT_*` and `ZOOM_WEBINAR_*` for the other two |
| `TIMEPAY_BASE_URL`, `TIMEPAY_TOKEN`, `TIMEPAY_ORG_ID` | TimePay REST API; `TIMEPAY_AUTH_HEADER` overrides the token header (default `Authorization: Bearer`) |

```python
from integrations.google_ads import GoogleAdsClient
from integrations.meta_ads import MetaAdsClient
from integrations.zoom import ZoomClient, attendance
from integrations.timepay import TimePayClient

GoogleAdsClient().campaign_daily("2026-10-01", "2026-10-07")      # spend in rupees
MetaAdsClient().campaign_daily("act_123", "2026-10-01", "2026-10-07")
z = ZoomClient("main")                                            # or "marketing", "webinar"
attendance(z.webinar_participants(webinar_uuid))                  # minutes per person
TimePayClient().count_logs("2026-10-08T00:00:00", "2026-10-08T23:59:59", type="call")
```

```bash
PYTHONPATH=. .venv/bin/python scripts/check_integrations.py            # read-only check, prints no secrets
PYTHONPATH=. .venv/bin/python scripts/fetch_ad_spend.py 2026-10-01 2026-10-07 exports/ad_spend.csv
PYTHONPATH=. .venv/bin/python scripts/fetch_zoom_attendance.py main host@lawsikho.in 2026-10-01 2026-10-07 data/zoom.csv
```

Notes: Meta budgets come back in paise and insights spend in rupees; an ACTIVE Meta campaign
may not be delivering. Zoom attendance reports need the `report:read:admin` scope, which the
webinar app does not have yet. TimePay `/logs` returns 10 rows a page, so read counts with
`count_logs`. The TimePay client has no call, WhatsApp or SMS methods on purpose: those reach
real people and need a go-ahead for each run.

## Live dashboard

[dashboard/](dashboard/) is a Cloudflare Worker (free tier) that pulls LeadSquared every minute and
shows leads, first-time enrollments, calling activity, lagging callers, dialer issues and Zipteams
call quality. Setup, limits and definitions: [dashboard/README.md](dashboard/README.md).
History is loaded with `scripts/d1_backfill.py`.

## Tests

```bash
.venv/bin/python -m pytest
```
