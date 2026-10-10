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
| D1: 5,000,000 rows read/day | Each summary is cached per date range for `SUMMARY_TTL_SECONDS` (60) and served as stored text. Cron runs and computed summaries add their reads to `read_budget`; at `READ_BUDGET` (4.5M) summaries pause (last copy shown, marked paused) until 05:30 IST. Enrollments are indexed by day. |
| D1: 100 bound parameters per query | Multi-row writes use escaped literals, split under 90 KB per statement. |
| 100,000 requests/day; 5 cron triggers | One `* * * * *` trigger (1,440 runs/day). Static assets don't count. |
| Access: 50 users free | Whole hostname behind Access; the Worker also checks the Access token on every `/api/*` route except `/api/health` and refuses until Access is configured. |

**Freshness:** data is 10–15 minutes behind (windows are read once they are 10 minutes old).
**Each call is counted once:** a call is counted only in the 5-minute window containing its start
time, and a run's totals and cursor are written in the same transaction, so a run cut off by the
CPU limit writes nothing and is retried.

Cron rotation: even minutes → outbound calls; odd minutes rotate inbound calls, new leads,
Zipteams notes, enrollments (and the daily users/teams refresh); :15 and :45 past each hour → who
put new leads into a shared admin account ([docs/accountability.md](../docs/accountability.md)).

## Definitions (same as the Python report)

- **Team:** the first group LeadSquared lists for the user that is not a calling-software group
  (any group named Acefone or Mcube, such as *Mcube Users*); users with no such group are left out, and one
  who had a team loses it at the next daily users refresh.
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
- **Shared admin accounts** (`SHARED_ACCOUNTS`, default `Rinku Jhala,Admin`): never treated as a
  person. Their calls are listed apart from callers, enrollments on their leads are labelled
  "(shared account)", and each lead put into one is credited to whoever actually did it, or Unverified.

## Deploy (runbook for the target account)

Prerequisites: Node 20+, the LeadSquared access/secret keys, and a Cloudflare API token for the
target account.

**1. API token.** My Profile → API Tokens → Create Token → "Edit Cloudflare Workers" template, add
**Account → D1 → Edit**, and limit *Account Resources* to this account only. Then:

```bash
cd dashboard && npm install
export CLOUDFLARE_API_TOKEN=...            # never commit or print it
export CLOUDFLARE_ACCOUNT_ID=...           # Workers & Pages → Account details
npx wrangler whoami                        # confirm the account name
```

**2. Config.** `wrangler.jsonc` pins `account_id` (not a secret; it stops a deploy landing in
another account) and `name`, which must match the Worker's name in the dashboard
(`sales-automation-01`) or Workers Builds refuses to deploy. If the account has no
`sales_dashboard` database yet, run `npx wrangler d1 create sales_dashboard` and put the printed
ID in `database_id`. Commit it.

**3. Secrets**, piped from environment variables so they never appear on screen or in history:

```bash
printf '%s' "$LEADSQUARED_ACCESS_KEY" | npx wrangler secret put LEADSQUARED_ACCESS_KEY
printf '%s' "$LEADSQUARED_SECRET_KEY" | npx wrangler secret put LEADSQUARED_SECRET_KEY
printf '%s' "$RUN_TOKEN"              | npx wrangler secret put RUN_TOKEN   # any long random string
```

**4. Migrate and deploy:** `npm run deploy:prod` (applies D1 migrations, then deploys). It prints
`https://sales-automation-01.<subdomain>.workers.dev`. If Wrangler says the account has no workers.dev
subdomain, set one first: Workers & Pages → Your subdomain.

Until step 5 is done, the page loads but the API answers *"Dashboard not yet protected: finish
Cloudflare Access setup"*. That is deliberate: no data is served without Access.

**5. Cloudflare Access (dashboard only, about 5 minutes):**
1. Zero Trust: create the free team if asked (up to 50 users; it may ask for a card but charges
   nothing on the free plan). Note the team domain, `<team>.cloudflareaccess.com`.
2. Workers & Pages → sales-automation-01 → Settings → Domains & Routes → workers.dev → enable
   Cloudflare Access. Edit its policy: Allow → the team leaders' emails or the company email domain.
3. Zero Trust → Access → Applications → the app → Overview: copy the **AUD tag**.
4. Put both in `wrangler.jsonc` vars, `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD` (not secrets), commit,
   and `npm run deploy:prod` again. `/api/summary` now needs an Access login.

**6. Load history** (from the repo root, complete days only, ideally early morning):
```bash
PYTHONPATH=. .venv/bin/python scripts/d1_backfill.py 2026-09-08 2026-10-07 data/d1_backfill.sql
cd dashboard && npx wrangler d1 execute sales_dashboard --remote --file ../data/d1_backfill.sql
rm ../data/d1_backfill.sql                 # lead data: never commit, print or move it
```
About 600 rows per day of history; keep each load under the 100,000 rows/day free limit.
The script also points the cron at the following midnight, so there is no overlap.

**7. Auto-deploy from GitHub (optional):** Workers & Pages → the Worker → Settings → Builds →
connect the repo, **root directory `dashboard`** (the repo root has no Wrangler config, so a build
there fails with "Could not detect a directory containing static files"), build command empty,
deploy command `npm run deploy:prod`, production branch `main`. The Builds token also needs D1 Edit (for the migrations). The free plan includes
3,000 build minutes a month.

**Check it:** `GET /` → the page; `GET /api/health` → `ok`; `GET /api/summary` without a login →
403 (or the Access login page once step 5 is done); `POST /api/run` without the token → 401.

## Operate

- `GET /api/health` (no login, no names or error text): `rowsWrittenToday` / `writeBudget`,
  `rowsReadToday` / `readBudget`, `accessConfigured`, and per task `cursor`, `updatedAt`,
  `behindMin`, `hasError` and `errorHttp` (the HTTP status of a failed LeadSquared call: 401 =
  keys missing or wrong). The error text itself shows on the page (behind Access).
- `POST /api/run?task=calls_out|calls_in|leads|zip|enroll|users|arrivals` with
  `Authorization: Bearer <RUN_TOKEN>` – run one task now.
- `/api/summary` answers with header `x-cache: hit | miss | stale` (stale = paused copy) and 503
  when paused with nothing cached. Ranges are capped at 31 days.
- `npx wrangler tail` – live logs.
- A task stuck on an error shows in the dashboard's *Data sync* table and as an alert.
- Rebuild a bad day (e.g. a dialer sync outage that was fixed later): run the backfill for that day
  with `--no-cursors`.

## Local development

```bash
cd dashboard
printf "LEADSQUARED_ACCESS_KEY='...'\nLEADSQUARED_SECRET_KEY='...'\nACCESS_LOCAL_DEV=1\n" > .dev.vars   # git-ignored; single quotes keep '$' literal
npm run db:migrate:local
npm run dev                                  # http://localhost:8787 ; trigger cron: /__scheduled?cron=*+*+*+*+*
# ACCESS_LOCAL_DEV=1 lets localhost requests skip the Access check; a deployed Worker never sees localhost.
# READ_BUDGET=10 in .dev.vars shows the paused state.
npm test && npm run typecheck
CPU_FIXTURES=/path/to/saved/api/pages npx vitest run test/cpu.local.test.ts   # CPU benchmark
```

Not in the Worker (yet): Salesa transcripts (limited to 9 requests per run) – use the Python tools.

## Conversation intelligence (`ci.html`)

A second page, *Conversation Intelligence & Team Analytics*, linked from the main page's header. It shows
transcript analysis, team and caller comparisons, calls that may not be real conversations, opportunities,
the Zipteams comparison, coaching, accountability and revenue for a date range, with drilldowns from the
organisation to a team, a caller, a lead and one call. Here a **real call** means answered and **3+ minutes**;
the main page's 2-minute *real conversation* is unchanged.

```
python -m analytics.convintel report ... ──► snapshot.json ──► analytics/convintel/d1push.py ──► D1 ci_snapshot (parts)
                                                                                                     │
Browser (Access login) ──► /ci.html + ci.js ──► /api/ci/ranges, /api/ci/snapshot?range=7d ◄──────────┘
```

**Free tier.** The analysis runs offline in Python; the Worker computes nothing. A snapshot is one JSON text
per range (`today`, `yesterday`, `7d`, `30d` or a day `YYYY-MM-DD`), stored in parts under 90 KB
(migration `0004`). Each request is one D1 query that reads one range's parts (a few dozen rows) and returns
them joined as text, never parsed; the Worker never writes these tables. A load writes about two rows per part (old parts deleted, new ones inserted) plus one index row,
and `d1push.py` refuses a push over 5,000 rows. Snapshots are capped at 1.5 MB: the command trims the
longest lists (calls, leads, opportunities, coverage gaps) from the end to fit, and the page still shows
"showing N of M" from the snapshot's totals. While a load is half-way, `/api/ci/snapshot` answers 503
"This view is being updated" and the page tries again a minute later. The 1.5 MB cap is meant to keep
D1's hand-over of the parts inside 10 ms of CPU: locally (Node's V8, 9 Oct 2026) parsing D1's reply for a
1.5 MB snapshot, joining the parts and building the response took about 4 ms, but it is not measured on
Cloudflare yet: check CPU time per request with `npx wrangler tail` after the first real load before raising it.

**Routes** (behind the same Access check as `/api/summary`; 404 when `CI_ENABLED` is not `"1"` in
`wrangler.jsonc` vars):
- `GET /api/ci/ranges` – the snapshots loaded: key, label, IST days, generated time, parts, bytes.
- `GET /api/ci/snapshot?range=KEY` – one snapshot as stored; 400 for a malformed key, 404 when none is
  loaded, 503 while a load is incomplete. `x-ci-generated-at` gives its UTC time.

**Load a snapshot** (a remote write: only with the user's go-ahead for that run):

```bash
.venv/bin/python -m analytics.convintel report 2026-10-02 2026-10-08 --key 7d       # data/convintel/reports/7d/
.venv/bin/python -m analytics.convintel.d1push data/convintel/reports/7d/snapshot.json --sql data/ci_7d.sql
cd dashboard && npx wrangler d1 execute sales_dashboard --remote --file ../data/ci_7d.sql && rm ../data/ci_7d.sql
# or straight through the D1 HTTP API (CLOUDFLARE_API_TOKEN with D1 Edit, in the root .env):
.venv/bin/python -m analytics.convintel.d1push data/convintel/reports/7d/snapshot.json --push --yes
```

Without `--push --yes` nothing is sent. A snapshot built with `--excerpts` (verbatim transcript lines) is
refused: excerpts stay in the local copy. A phone number quoted in free text (a summary or reasoning from Claude's reading) is
replaced with "[number removed]" before loading, and a snapshot with a phone-number field is refused. The key must be one of the range keys above (give `--key` for a
multi-day report). Apply migration `0004` first (`npm run db:migrate:remote`, or `npm run deploy:prod`).

**Offline copy.** `python -m analytics.convintel.snapshot_html SNAPSHOT.json OUT.html` (the report also
writes `index.html` next to `snapshot.json`) is the same page with the CSS, script and data inlined; it
opens from a file with no server or network. It holds lead ids and caller names: keep it under `data/` or
`exports/` and share it only inside the team.

**Turn it off:** set `CI_ENABLED` to `"0"` and deploy; the page then explains that it is switched off.
