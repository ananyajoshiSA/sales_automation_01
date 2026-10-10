# Project instructions

## Mission

This repo exists for one goal: **Revenue Boost**. Raise course revenue by turning more of the leads already
in LeadSquared into first-time enrolments, with the same calling team. Read [GOAL.md](GOAL.md) before
starting any task. It holds the targets (T0–T7), the leak map (L1–L13), the revenue-loss method, the
levers, the gaps (G1–G9) and the open decisions.

Before building anything, answer: *which lever does this move (speed to lead, productive dialling,
returning leads' calls, calling the right leads first, conversation quality), and how will we see it in
enrolments?* If the answer is "none", say so and ask before building.

When choosing what to work on next, go in gap order unless told otherwise: G1 (payment data) unblocks
every rupee figure; G2 (automated nightly call plan) is the biggest daily-use win; G7–G8 (lead ledger and
revenue-loss report) find and price every leak.

Any number about lost leads or lost revenue must follow GOAL.md section 5: one primary leak per lead, a
range with every figure, a 20-lead hand check before a leak is priced, and reconciliation to the
LeadSquared lead count.

## Repo map

| Path | What it is |
|---|---|
| `integrations/` | API clients: `leadsquared` (CRM), `transcripts` (Salesa), `zipteams`; read-only `google_ads`, `meta_ads`, `zoom` (attendance), `timepay` (AI voice-agent logs), `growthx` (funnel leads and bootcamp checkouts); `env.py` loads `.env` |
| `scripts/` | Bulk fetchers (IST date ranges → JSON/JSONL in `data/`), `d1_backfill.py`, `build_plan_pdf.py` |
| `analytics/` | Reports: the daily `team_performance` report (rules in `docs/report/`), `call_integrity`, `nightly_plan` / `call_plan`, `bootcamp_collections` (bootcamp and community collection pools, booking to balance), `collection_audit` (every open collection lead: chance from history, next step read by Claude, pipeline per caller and team), `coaching`, `revenue`, `lost_leads`, `tier_outcomes`, `team_report`, `team_compare`, `fresh_leads`, `lead_priority`, `dnp_report`, `accountability` (who really assigned or transferred each lead, [docs/accountability.md](docs/accountability.md)); shared definitions in `definitions.py`. Guide: [docs/revenue_plan.md](docs/revenue_plan.md) |
| `analytics/convintel/` | Conversation intelligence (additive): every call inventoried with a status until its transcript is analysed, the 3-minute REAL_CALL rule (this module only), every transcript read by Claude itself (no model API, no keyword rules), possibly-not-real call flags, team analytics, dashboard snapshots. `python -m analytics.convintel status`. Guide: [docs/conversation_intelligence.md](docs/conversation_intelligence.md) |
| `dashboard/` | Cloudflare Worker + D1 + static page; cron ingest every minute ([dashboard/README.md](dashboard/README.md)) |
| `tests/`, `dashboard/test/` | pytest (HTTP mocked with `responses`), vitest |

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                     # Python tests
cd dashboard && npm install && npm test && npm run typecheck
```

Run the relevant tests before every commit. Add or update a test with every behaviour change.

## Definitions — one meaning everywhere

These are shared by the Python reports and the Worker (`dashboard/src/metrics.ts`). Change them in both
places and in GOAL.md together, or not at all.

- **First-time enrolment:** the lead's first ever stage change to *Course Enrolled*, credited to the owner.
  Re-tagged existing students never count.
- **Working day:** 20+ outbound dials. **Real conversation:** answered and 120+ seconds.
- **Team:** the first group LeadSquared lists for a user that is not a calling-software group (a name with the word
  Acefone or Mcube): `team_of` in `analytics/definitions.py`, `teamFromGroups` in the Worker.
- **Lagging caller:** below 70% of the team median (whole account if the team has < 3 callers) on 2+
  measures over 2+ working days, at least one of dials, real conversations or talk time.
- **Dialer issue:** 50%+ of dials end in `CallFailure`. Such a caller is never marked lagging.
- **Data-gap alarm:** last hour's dials < 30% of the same hour's median over the previous 7 days.
- **Zipteams notes** (activity 237) are attributed to the lead's last answered call.

## Rules

**Data and judgement**
- Zipteams intent is one input, never the answer. Where Zipteams and LeadSquared disagree, flag it.
- Fix the dialer before judging the caller.
- Every time shown to people is IST. Take `IST`, `utc()` and the clock from `integrations/timeutil.py`;
  a test fails on a second IST or a zoneless `datetime.now()`. Details: [docs/timezone_issues.md](docs/timezone_issues.md).
  - A call starts at its `CreatedOn` (UTC). Never read a call note's `StartTime`: one copy is UTC, the other IST,
    and neither is labelled.
  - LeadSquared's activity date filter is on `ModifiedOn`, so fetch calls, notes and payments for days with
    `iter_activities_started`, which reads 3 days of later edits and keeps activities by `CreatedOn`. It also
    reads each window in one page, because paging through a large result skips a few rows.
  - Each Zipteams note is written as its call ends; `analytics/zip_calls.py` keeps the analysis on that call,
    including a call that ends after midnight.
  - Transcript API clocks can be 5 h 30 min off in either direction. Place a transcript on a day only through
    `match_to_calls`, which takes the time from its LeadSquared call.
- Count each call once. Don't add per-call rows to D1.
- Never invent numbers, targets or probabilities. Label estimates as estimates (as the call-plan PDF does).
  `analytics.revenue` reads payment activities (event 213), but the API returned none for 5–8 Oct 2026;
  until payments come through, report enrolments and say so.

**Live systems**
- Treat LeadSquared, Zipteams, TimePay, Zoom and the ad accounts as production. Reads are fine. **Any write**
  (`create_lead`, `update_lead`, `upsert_lead`, `post_activity`, any Zipteams sync, any TimePay call, WhatsApp,
  SMS or campaign change, any ad or Zoom change) needs the user's explicit go-ahead for that run.
- Transcript API: at most 10 numbers per request and 9 requests per run. `TranscriptClient` enforces it;
  never raise the limit.
- Dashboard must stay on Cloudflare's free tier: ≤ 10 ms CPU per run, ≤ 50 subrequests and D1 queries per
  run, < 100,000 D1 rows written per day (`WRITE_BUDGET` 90,000). Check a change against these before
  merging; `test/cpu.local.test.ts` measures CPU.
- Don't deploy the Worker or run remote D1 commands unless asked.

**Privacy and secrets**
- `data/` and `exports/` hold lead PII and are git-ignored. Never commit them, paste lead details into
  commits or PRs, or move them outside the repo.
- Credentials live only in the root `.env` (or the cloud environment's settings; `dashboard/.dev.vars`
  for local Worker dev; `wrangler secret` in production). Never hardcode or print a key.

## Code style

- Match the surrounding code: small modules, type hints, `from __future__ import annotations`, few
  comments that explain *why*.
- Every runnable module or script starts with a docstring that ends in its usage line
  (`python -m analytics.x <inputs> <out_dir>`).
- Keep dependencies minimal (`requests`, `openpyxl`); ask before adding one.
- Tests use small hand-built fixtures and mocked HTTP; never call a live API in tests.

## Output for people

Reports and plans are read by team leaders and callers, not engineers. Write plain language, lead with
the action, show IST times, say how fresh the data is, and name each lead's next step. Callers need
*who to call, when, and what to say*; team leaders need *who is stuck and why*.
