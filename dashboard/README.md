# Sales dashboard (Cloudflare free tier)

Live dashboard for leads, first-time enrollments, calling activity, lagging callers, dialer
problems and Zipteams call quality. Runs entirely on Cloudflare's free plan: one Worker, one D1
database, one cron trigger and static assets, behind Cloudflare Access.

```
LeadSquared API ◄── cron every minute (one small task per run) ── Worker ──► D1 (daily totals)
                                                                    │
Browser (Cloudflare Access login) ──► /index.html (static, free) ──► /api/summary
```

## How it stays inside the free tier

| Free limit | Design |
|---|---|
| 10 ms CPU per invocation (cron too) | Each run reads ≤ 200 calls / 80 Zipteams notes / 500 leads. Measured on real data: ≤ 3 ms cold. |
| 50 subrequests & 50 D1 queries per invocation | ≤ 8 LeadSquared pages per run; all writes go in one D1 batch of a few statements. |
| D1: 100,000 rows written/day, 500 MB | Only daily totals are stored (no per-call rows), tables are `WITHOUT ROWID` with no extra indexes. ~30–40k rows/day; ingestion pauses at `WRITE_BUDGET` (90k). |
| D1: 100 bound parameters per query | Multi-row writes use escaped literals, split under 90 KB per statement. |
| 100,000 requests/day; 5 cron triggers | One `* * * * *` trigger (1,440 runs/day). Static assets don't count. |
| Access: 50 users free | Whole hostname behind Access. |

**Freshness:** data is 10–15 minutes behind (windows are read once they are 10 minutes old).
**Each call is counted once:** a call is counted only in the 5-minute window containing its start
time, and a run's totals and cursor are written in the same transaction, so a run cut off by the
CPU limit writes nothing and is retried.

Cron rotation: even minutes → outbound calls; odd minutes rotate inbound calls, new leads,
Zipteams notes, enrollments (and the daily users/teams refresh).

## Definitions (same as the Python report)

- **New leads:** lead records by `CreatedOn` (IST day), by source and owner's team.
- **First-time enrollment:** the lead's first ever stage change to *Course Enrolled*; re-tagged
  existing students are not counted. Credited to the lead owner.
- **Working day:** 20+ outbound dials. **Real conversation:** answered and 2+ minutes.
- **Lagging caller:** over 2+ working days, below 70% of their team's median (whole account if the
  team has < 3 callers) on 2+ of dials/day, real conversations/day, talk time/day, connect rate, or
  no enrollments while peers enroll, with at least one of the first three. **Dialer issue:** 50%+
  of dials end in `CallFailure` (not judged as lagging).
- **Zipteams:** notes (activity 237) attributed to the lead's last answered call.
- **Data-gap alarm:** last hour's dials < 30% of the same hour's median over the previous 7 days.

## Setup (new Cloudflare account)

Prerequisites: Node 20+, the LeadSquared access/secret keys.

```bash
cd dashboard
npm install
npx wrangler login                          # approve in the browser, on the NEW account
npx wrangler whoami                         # confirm the account

npx wrangler d1 create sales_dashboard      # copy the printed database_id into wrangler.jsonc
npm run db:migrate:remote                   # create the tables

npx wrangler secret put LEADSQUARED_ACCESS_KEY
npx wrangler secret put LEADSQUARED_SECRET_KEY
npm run deploy                              # prints https://sales-dashboard.<subdomain>.workers.dev
```

Then:
1. **Lock it down (do this before sharing the URL):** Zero Trust → Access → Applications → Add a
   self-hosted application for the Worker's hostname; policy: Allow → emails (or your email domain).
   Zero Trust's free plan asks for a payment card at signup but charges nothing.
2. **Load history** (from the repo root, complete days only, ideally early morning):
   ```bash
   PYTHONPATH=. .venv/bin/python scripts/d1_backfill.py 2026-09-08 2026-10-07 data/d1_backfill.sql
   cd dashboard && npx wrangler d1 execute sales_dashboard --remote --file ../data/d1_backfill.sql
   ```
   About 600 rows per day of history; keep each load under the 100,000 rows/day free limit.
   The script also points the cron at the following midnight, so there is no overlap.
3. **Auto-deploy from GitHub (optional):** Workers & Pages → the Worker → Settings → Builds →
   connect the repo, root directory `dashboard`, deploy command `npx wrangler deploy`, production
   branch `main`.

## Operate

- `GET /api/health` – cursors, last errors, D1 rows written today.
- `POST /api/run?task=calls_out|calls_in|leads|zip|enroll|users` – run one task now.
- `npx wrangler tail` – live logs.
- A task stuck on an error shows in the dashboard's *Data sync* table and as an alert.
- Rebuild a bad day (e.g. a dialer sync outage that was fixed later): run the backfill for that day
  with `--no-cursors`.

## Local development

```bash
cd dashboard
printf "LEADSQUARED_ACCESS_KEY='...'\nLEADSQUARED_SECRET_KEY='...'\n" > .dev.vars   # git-ignored; single quotes keep '$' literal
npm run db:migrate:local
npm run dev                                  # http://localhost:8787 ; trigger cron: /__scheduled?cron=*+*+*+*+*
npm test && npm run typecheck
CPU_FIXTURES=/path/to/saved/api/pages npx vitest run test/cpu.local.test.ts   # CPU benchmark
```

Not in the Worker (yet): Salesa transcripts (limited to 9 requests per run) – use the Python tools.
