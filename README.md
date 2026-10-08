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

## Live dashboard

[dashboard/](dashboard/) is a Cloudflare Worker (free tier) that pulls LeadSquared every minute and
shows leads, first-time enrollments, calling activity, lagging callers, dialer issues and Zipteams
call quality. Setup, limits and definitions: [dashboard/README.md](dashboard/README.md).
History is loaded with `scripts/d1_backfill.py`.

## Tests

```bash
.venv/bin/python -m pytest
```
