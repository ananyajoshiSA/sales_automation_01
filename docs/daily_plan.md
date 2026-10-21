# Daily plan pipeline: 09:00 report and 14:00 / 17:00 status checks

**Lever:** calling the right leads first, returning leads' calls, and the ask on every conversation. It is seen in
enrollments on the team leader's day-close count.

Four scheduled Claude Code routines run every day (IST) in fresh cloud sessions on this repository:

| Routine | Starts | Lands | What it sends |
|---|---|---|---|
| Morning report | 07:15 | ~09:00 | PDF + workbook: review of the previous working day first, then today's plan (team leader's priority list, checks, feedback, caller sheets) |
| Status 1 | 13:45 | ~14:00 | Short PDF + email: first half against the plan, to-do list for 14:00–17:00 |
| Status 2 | 16:45 | ~17:00 | Same, to-do list for 17:00–20:30 |
| Nightly pipeline | 21:20 daily | ~22:00 | PDF + workbook: every lead with a real chance of enrolling by month end, highest chance first, with changes since last night |

Code: `analytics/daily_plan/` (`python -m analytics.daily_plan --help`). Lead PII stays in `data/daily/` and
`exports/daily/` (git-ignored). Plan state between runs lives in `plan_state/` on the working branch (`PLAN_STATE_BRANCH`, default: the checked-out
branch) and holds **salted hashes of lead IDs only** (with owner, tier, chance, group, check-by): no names, phones or notes.

## Morning report (the routine follows these steps exactly)

```bash
python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
D=$(TZ=Asia/Kolkata date +%F)
.venv/bin/python -c "from analytics.daily_plan import state; state.checkout()"
.venv/bin/python -m analytics.daily_plan fetch $D            # ~1 min: team snapshot (4 days back) + enrolled leads
.venv/bin/python -m analytics.daily_plan prepare $D          # transcripts (≤10 numbers/request, ≤9 requests/run), dossiers, batches
```

`fetch` and `status` reuse the newest earlier pull in `data/daily/` that covers the window (last night's
pipeline fetch, the morning snapshot, a status pull): they read only activities edited since 2 hours before that
pull, and keep the stage history of leads whose first enrolment is already known. The lead list is always read
in full (about 45 s). On 11 Oct the incremental pull matched a full pull call for call. Add `--full` to read
everything again (about 20 min), for example after a gap of several days or if a number looks wrong.

`prepare` prints `previous_day` and the batch files (`data/daily/$D/reads/batch_*.txt`).

1. **Read every lead.** Launch one subagent per batch, in parallel. Give each the brief `docs/daily_plan_brief.md`,
   the previous working day's date, its batch file and its output file `data/daily/$D/reads/out_<k>.jsonl`. Claude reads
   every dossier itself: no model API, no keyword rules. Check that each output's line count matches its batch.
2. **Write the narrative** in `data/daily/$D/narrative.json`, after reading `data/daily/$D/facts.json`. To get that file
   first, run `build` once without the narrative; it writes `facts.json` and `content.json`. Keys:
   - `headline` (one paragraph; `<b>` allowed): the previous day's result in plain words, with the numbers
   - `enrollment_summary`: who enrolled (Course Enrolled, or said on a call that they paid), with what each paid
   - `intro`: two or three lines for the team leader's sheet
   - `feedback`: `{caller: {good, fix, rules: [3 short rules], one_line}}`, from that caller's numbers and conversations
   - `lessons`: `{worked: [...], didnt: [...], plan_wrong: [...]}` (`<b>` allowed); `plan_wrong` compares the
     forecast with the actuals and says by how much it was off
   - `talking`: 5–7 points for the owner's conversation with the team leader
   - `changes`: today's dialling rules, each tied to a number from the previous day
   - optional: `missed_extra: [[title, text]]` and `priority_overrides: {lead_id: {role, check_by, group}}`

   Follow GOAL.md and CLAUDE.md: never invent numbers and label estimates. Course Enrolled, or a lead saying on a call that they paid, is an enrollment.
3. **Build, check and send:**
   ```bash
   .venv/bin/python -m analytics.daily_plan build $D --publish-state --mail
   ```
   `build` stops if any workbook formula errors after the LibreOffice recalculation. Look at the first 6 pages of the PDF
   (Read tool with `pages`) before sending. If no mail route is set up, `--mail` says so and sends nothing; then send both
   files to the user with SendUserFile.

## Status checks (14:00 and 17:00)

```bash
.venv/bin/python -c "from analytics.daily_plan import state; state.checkout()"
.venv/bin/python -m analytics.daily_plan status $(TZ=Asia/Kolkata date +%F) --mail    # ~6 min: today's calls + enrolled leads
```

The report covers callers dialling or not, P/A leads tried and reached, every priority lead's status (untouched, tried,
reached), missed calls not returned, callbacks due or overdue, leads enrolled today, and the to-do list for the next block. It needs no Claude judgement. A 0-second unanswered call is not a line fault: the Airtel and Jio lines log a lead
who didn't pick up as CallFailure at 0 seconds. If the morning state is missing,
the report still shows the team numbers and says the plan was not found.

## Nightly pipeline (22:00, every day)

```bash
D=$(TZ=Asia/Kolkata date +%F)
.venv/bin/python -c "from analytics.daily_plan import state; state.checkout()"
.venv/bin/python -m analytics.daily_plan fetch $D --days-back 3
.venv/bin/python -m analytics.daily_plan pipeline-prepare $D      # candidates, transcripts, dossiers, brief, batches
```

Candidates are last night's pipeline (`plan_state/pipeline_<day>.json`, hashed IDs), every lead with a real conversation
today and today's plan P/M/A/B and priority leads. Leads already enrolled are dropped. Read every batch with one subagent
each, following `data/daily/$D/pipeline/brief.md` (made from `docs/pipeline_brief.md`), and write
`data/daily/$D/pipeline/reads/out_<k>.jsonl`. Then:

```bash
.venv/bin/python -m analytics.daily_plan pipeline-build $D --publish-state --mail
```

The horizon is month end (the end of next month in a month's last 3 days). The report marks new leads and chance changes
since last night. A section on **fresh leads allocated that day** (assigned or created today, split into new and reassigned) shows,
per caller, how many were dialled, reached, had a real conversation, got a dated follow-up, and entered the pipeline, and lists those leads. The expected range is the sum of the chances, up to 2.4x that.

## Email settings (environment variables in the cloud environment's settings)

`SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`; `REPORT_TO`: full reports with lead phone
numbers (team leader, callers); `REPORT_TO_SUMMARY`: the summary only (leadership), with no attachments and no lead
details. Never put these in the repo or in chat.

## Not done yet

- WhatsApp delivery needs a WhatsApp Business provider account and approved templates; it is a live-system write, so
  it needs the owner's go-ahead.
- Payments are not in the LeadSquared API (GOAL.md G1). An enrollment is a lead in Course Enrolled, or a lead who said on a
  call that they paid; no screenshot or UTR is needed. Enrolled leads never appear on the sheets or the status to-do list.
  A Rs 10 bootcamp registration (or a free bootcamp sign-up) is not an enrollment, even when staged Course Enrolled:
  the lead stays on the sheets as a prospect for the course.

## Lead flow (Shivangi's request, 11 Oct)

Every report now shows, for the leads that came in, how fast each was first dialled, how many attempts it got, whether it
was reached, and its disposition (LeadSquared stage) now, by lead type: **High Priority**, **Chat**, **Incoming**,
**Incoming Missed** and **Other** (`analytics/daily_plan/leadflow.py`).

- Type comes from the lead's Report_Type field (High Priority / Incoming / Chat/Email/Referral), else Lead Request type,
  else Source (Inbound Phone call → Incoming, a chat source → Chat). Incoming Missed has no field: the lead's first contact
  was a call to us that nobody answered.
- First dial is timed from when the lead came in (created; for a reassigned lead, when it was assigned). A lead arriving
  before 09:30 or after 20:30 IST is timed from the next 09:30.
- Status checks (14:00, 17:00): today's leads, with to-do lines for leads not dialled, dialled but not reached, and leads
  given to a caller who has made no calls today.
- Morning report (after the review): the previous day's leads by type and by caller, then this month's leads by type and
  by caller and the month's leads never reached.
- Nightly pipeline: today's leads and the month to date. `pipeline-prepare` refreshes the month pull
  (`data/daily/month/<YYYY-MM>.json`) first; `fetch-month <date> --full` rebuilds it.
- Leads not yet given to a caller are not in the team's lead list, so they are not counted.

## Email with the reports attached

Every `--mail` run (morning build, status checks, nightly pipeline) emails the report's files as soon as the build has
passed its checks, through the first route that is set up:

1. **The project's report mailer** (`REPORT_EMAIL_URL`, `REPORT_EMAIL_TOKEN`): the Apps Script that `/email-report`
   uses (`email_report/Code.gs`, [docs/email_report.md](email_report.md)). It sends the exact files the run built
   (the PDF and workbook in one email; one email per file on a script older than the multi-file Code.gs) to its own
   approved recipients, and follows its off/test/live switch, daily cap and sent log. `REPORT_TO` is ignored and the
   email body is the mailer's own. The run marks the files as sent, so a later `/email-report` in the same chat
   doesn't pick them again. The log line `mail: ... sent via the report mailer (...)` gives the mode and the result.
2. The older Gmail relay (`docs/mail_relay.gs`, `MAIL_RELAY_URL`, `MAIL_RELAY_KEY`): its own script and recipient list.
3. SMTP settings, where port 587 is open.

Without any of them, `--mail` says so and sends nothing. The weekly review and the team leader's note have no `--mail`.

