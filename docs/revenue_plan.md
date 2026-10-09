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

- The rules are in [docs/report/report_parameters.md](report/report_parameters.md) (Parameters v1.4).
  A call is a LeadSquared call activity started that day in IST. An enrolment is a lead's first-ever
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
```

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
- **The narrative plan PDF is a one-off for 8 Oct.** `scripts/build_plan_pdf.py` was written for
  that date. The nightly job builds the workbook only.
