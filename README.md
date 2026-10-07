# Sales-Skill

Integrations with sales platforms.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # fill in values locally; .env is git-ignored
```

Credentials are read from environment variables only — never commit them.
In Claude Code cloud sessions, add them in the environment settings instead of a `.env` file.

## LeadSquared

| Variable | Description |
|---|---|
| `LEADSQUARED_HOST` | API host, default `https://api-in21.leadsquared.com/v2/` |
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

## Tests

```bash
.venv/bin/python -m pytest
```
