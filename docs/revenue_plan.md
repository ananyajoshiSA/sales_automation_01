# Revenue plan: what was built and how to run it

The goal is more first-time enrolments from the leads we already have, with the same calling team.
This repo now carries the plan's actions as commands. Every command only reads LeadSquared and
Zipteams; nothing writes back.

## Setup

```bash
pip install -r requirements.txt
export PYTHONPATH=.
```

Keys come from the environment (`LEADSQUARED_ACCESS_KEY`, `LEADSQUARED_SECRET_KEY`, `SALESA_API_KEY`).
Fetched data goes to `data/` and outputs to `exports/`. Both hold lead details and are git-ignored.

## Daily team and caller report

```bash
python -m analytics.team_performance 2026-10-05 --fetch
```

Fetches the day, checks the data, and writes `team_calling_report_{DATE}.pdf` plus a plan tracker PDF.
Page 1 is a one-page summary: the day from calls to enrolments, what happened, the teams ranked by
conversion, the callers to recognise and what to do next. The pages after it explain every figure in plain
language with charts (teams, weak spots of the day, a card per team, callers, what converting calls had in common, Zipteams
scores, calls by hour, calls to review), then the method, every validation check and each caller's figures.

- The rules are in [docs/report/report_parameters.md](report/report_parameters.md) (Parameters v1.5).
  A call is a LeadSquared call activity started that day in IST. A caller's team is their first LeadSquared group that
  is not a calling-software group (Acefone, Mcube); section 3 also shows every group that shares callers with another team. An enrolment is a lead's first-ever
  "Course Enrolled" within the day plus 3 days, credited to the caller with the most talk time.
- The validation gate blocks the PDF if any data check fails (duplicates, calls outside the day,
  unmapped callers, double-counted enrolments, totals that don't add up, a stage-history
  spot-check). It prints `BLOCKED` and exits 2. The full log is in `data/report_{DATE}/validation.txt`.
- The plan tracker shows, per team and caller, how often a payment step was offered, the full-pitch
  rate and the same-day callback rate. It is kept separate so the main report stays reproducible.

## The plan's actions

| Action | What to run or read |
|---|---|
| 1. End every real conversation with a payment step | [docs/call_playbook.md](call_playbook.md) (adoption is measured in the plan tracker) |
| 2. Return missed calls and chase unpaid links first | Call plan tiers M and P (below) |
| 3. Best closers on the hottest leads | `--tier-b-cap N` on the call plan moves extra Tier B leads to less loaded callers |
| 4. Weekly coaching | `python -m analytics.coaching data/snapshot.json exports/coaching [--transcripts]` |
| 5. Dialer failures | `python -m analytics.dnp_report data/all_calls.jsonl data/users_all.json exports/dnp` |
| 6. Revenue | `python -m analytics.revenue data/report_2026-10-05 exports/revenue --avg-fee N` |
| Lost leads | `python -m analytics.lost_leads data/snapshot.json --out exports/lost` |
| Call integrity (fake, stretched or empty calls) | `python -m analytics.call_integrity 2026-10-05 --limit 10`, then the daily report shows it in section 7 |
| Calibrate the tiers | `python -m analytics.tier_outcomes exports/plans/call_plan_*.json --snapshot data/later.json --histories data/hist.jsonl` |

## Nightly call plan for any team

```bash
python -m analytics.nightly_plan "Team Elite Calling" --leader "Shivangi Sahu" [--tier-b-cap 12]
python -m analytics.nightly_plan "US Bookkeeping Accounting-Sana" "US accounting counselors - Sana" \
    --leader "Sana Ali" --since 2026-09-01 --sheet-cap 80 --no-inbound --course account \
    --bot-calls data/bot_calls.json --bot-campaign acc
```

For bootcamp teams, discovery is done by the AI voice agent (TimePay) or a counsellor before a closer calls.
`scripts/fetch_bot_calls.py leads.json data/bot_calls.json` looks up the bot's calls for each lead by phone
(read-only); the plan shows the bot's latest discovery and the callers' own notes (LeadSquared "Conversation
Summary") as "Discovery so far", and new leads the bot already discovered come first with a closer's opening.
`--no-inbound` is for a team that takes no incoming calls (no Tier M); `--course` leaves out other courses.

Several groups are planned as one team. `--since` widens the window: every lead with a conversation since that
day is tiered and every lead created since then without a real conversation is a new lead (F). `--sheet-cap`
keeps each sheet to a day's work: M, P, A, B and promises now due always stay; the other F, R and C leads go to
a Backlog sheet in call order. Within a tier, leads are ranked (the lead score for tiered leads; for new leads,
the least-dialled and newest first), and each lead's best time is the slot in which it answered most often.

This writes `exports/plans/call_plan_{team}_{date}.xlsx`: one sheet per caller, plus a team summary
that updates as callers fill in their sheets. Leads are ordered by tier:

| Tier | Meaning |
|---|---|
| M | The lead called us and nobody called back |
| P | Payment link sent on an earlier sheet and not paid (read from the team's last workbooks) |
| A | Can close today |
| B | Hot follow-up |
| F | New lead with no real conversation yet |
| R | Open-stage lead with no real conversation for 15+ days (WhatsApp first) |
| C | Nurture |

Each tier's chance of enrolling is an estimate until `tier_outcomes` has measured it on at least 30
leads. After that, the measured rate is used.

## Snapshot inputs

`python scripts/fetch_team_data.py "Team" 2026-09-23 2026-10-07 data/snapshot.json` fetches a team's
leads, calls and Zipteams notes. Coaching, lost leads, `team_report` and `tier_outcomes` read this
file. LeadSquared's enrolment date field is usually blank, so enrolments are dated from each lead's
stage history (`scripts/fetch_lead_histories.py ids.json data/hist.jsonl`). `tier_outcomes` needs that
file. Passed to `team_report`, it credits each enrolment to the lead's owner at the time and to the
last caller who spoke to the lead, not to whoever owns it today.

## Not done, or needs input

- **Dashboard work is out of scope for now.** That covers plan step 4 and the dashboard and Worker
  parts of steps 7 and 10.
- **The LeadSquared API returned no payment activities (event 213) for 5–8 Oct.** The revenue
  reader works, but it has nothing to read until payments come through the API or another payment
  source is connected.
- **The average fee per enrolment is not set**, so no rupee figures are shown. Set it with
  `--avg-fee` or `REVENUE_AVG_FEE_INR`.
- **The narrative plan PDF needs a short meta file.** `scripts/build_plan_pdf.py rows.json out.html --meta meta.json`
  renders any team's plan (team, leader, day, window, team leader's to-dos, objection playbook; see its docstring).
  The nightly job builds the workbook only. Opening lines, asks and objections per lead come from a lead review
  (Claude reading each hot lead's notes); without one, each tier's standard lines are used.
