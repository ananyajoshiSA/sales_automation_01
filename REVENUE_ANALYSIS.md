# Revenue Boost: Loophole Analysis

*As of 8 Oct 2026. Source: a line-by-line audit of this repo (the Python reports, the call plan, the API
clients and the dashboard Worker). No live data was available in this session, so every finding below is
about how leads, calls and enrolments are **selected, counted and credited**. Sizing each leak in rupees
needs read-only LeadSquared and transcript access (section 8).*

## 1. Bottom line

The repo already pulls most of the data needed to raise enrolments. Revenue leaks through three kinds of
loophole:

1. **Leads that are ready to buy fall out of the call plan.** Leads who called in and were missed,
   payment links already sent, open-stage leads gone quiet, and leads wrongly marked dead. These are the
   cheapest enrolments available, and today nothing puts them back in front of a caller.
2. **The dashboard makes weak calling look fine.** Callers under 20 dials a day vanish. Per-day averages
   are inflated up to ~3.5×. Zero enrolments alone never makes a caller "lagging", and the default "Today"
   view can never show anyone lagging.
3. **Enrolments and revenue are miscounted or not counted.** Enrolments are credited to whoever owns the
   lead *now*, not the closer. The enrolment sync can skip or freeze. Payment activities (event 213) are
   downloaded and never read.
4. **What leads actually say is never read.** No report uses call transcripts, and Zipteams' objection
   fields are downloaded and ignored. Leads are ranked on keyword hits in a one-call summary instead
   (section 5).

Fixing loopholes 1 and 4 adds enrolments directly. Fixing 2 and 3 makes the numbers trustworthy enough to
manage by, and to prove the gain.

## 2. Where each loophole hits the funnel

```
Revenue    = first-time enrolments × fee collected
Enrolments = leads reached × real-conversation rate × close rate
```

Because the terms multiply, small gains compound: +10% on each of the three terms is 1.1³ ≈ **+33%
enrolments**. This is arithmetic, not a forecast.

| Funnel term | Loopholes that shrink it | Count |
|---|---|---|
| Leads reached | Missed-inbound leads dropped; stale open stages and >45-day closure leads in no tier; Tier D and capped leads dropped with no record; dead-without-conversation never recovered; speed to lead measured from reassignment | 6 |
| Real-conversation rate | Failed dials count as effort; repeat calls to one lead count as many conversations; per-team dialer outages not detected; long calls may be lost by the 10-minute sync window | 5 |
| Close rate | Keyword scoring false positives; stage-name mismatch; tiers by guesswork; payment links not carried forward; follow-up misses erased; no fee value in priority | 7 |
| Measurement (all terms) | Dashboard inflation and blind spots; wrong enrolment credit; enrolment sync gaps; no revenue | 12 |

## 3. Top 10 loopholes, ranked by revenue impact

Each one is verified against the code; file references point to the evidence.

| # | Loophole | Evidence | What goes wrong | Fix |
|---|---|---|---|---|
| 1 | **Leads who called in and were missed are dropped from the plan** | `analytics/lead_priority.py:100-102` skips any lead without a 60s+ answered call or Zip note | A lead calls twice, nobody answers, and it appears on no caller's sheet; F tier covers only new leads | Keep every lead with a missed inbound call; make "return the lead's call" its own top tier |
| 2 | **Payment links and tokens are never carried forward** | `call_plan.py:33-35` offers "Link sent", "Token paid"; nothing reads the workbook back (no `load_workbook` anywhere); status counts only "Paid" (`:211`) | A link sent Thursday and unpaid is gone from Friday's plan unless the caller re-keys it | Read each day's sheet back (or log outcomes in LeadSquared); auto-place "link sent, not paid" in tomorrow's Tier A |
| 3 | **Payment data is fetched and ignored** | `scripts/fetch_team_data.py:26,61,74` downloads event 213 into `payments`; no module reads it | Revenue can't be measured, and a high-fee lead ranks the same as a low-fee one | Inspect event 213 (amount, course); report revenue per caller and course; use chance × fee as a tiebreak |
| 4 | **Keyword scoring rewards the wrong words** | `lead_priority.py:30-38`, `+6` per category (`:153`), up to +42 vs +30 for Zip HIGH. Checked: "shared feedback"→fee, "family law"→decision-maker, "works in finance"→EMI, "already enrolled elsewhere"→enrol intent | A law-course lead who mentions "family law" and "feedback" outranks one who asked for the payment link | Word boundaries, drop law-domain words (family, parent, batch, register), handle negation |
| 5 | **Enrolments credited to the current owner, not the closer** | Worker stores current `OwnerId` (`dashboard/src/tasks.ts:191,202`); backfill too, weeks later (`scripts/d1_backfill.py:175`); `team_report.py:330` uses `OwnerIdName` | After payment the lead moves to onboarding; the closer shows 0 enrolments and can be flagged | Credit the owner at enrolment time and the last answered caller before it (`team_compare.enrollment_credit` already does the first) |
| 6 | **Per-day averages inflated ~3.5× for light callers** | `metrics.ts:41-49` sums all days, divides by working days (20+ dials) only | 20 dials on 2 days and 19 on 5 days shows 67.5 dials/day, so no "low dialing" flag | Divide only working days' totals by working days |
| 7 | **Callers under 20 dials a day vanish from the dashboard** | `metrics.ts:39-40` `if (!active) continue` | The least active callers, and callers whose call sync broke, are invisible to team leaders | List every caller; add a "below working-day threshold" status |
| 8 | **Zero enrolments alone can't make a caller lagging** | `metrics.ts:68-72`: lagging needs a productivity flag; conversion flag fires only at exactly 0 | A busy caller who enrols nobody, or 1 vs a team median of 8, is "watch" at most | Flag enrolments per real conversation below 70% of peers; let it count toward lagging |
| 9 | **Enrolment sync can skip or freeze** | `tasks.ts:188-196` reads only page 1 (200 newest by ModifiedOn) of leads *currently* at Course Enrolled; same-timestamp batch can exceed 50 subrequests | Bulk edits to old students push new enrolments off page 1 (skipped forever) or crash every run (frozen); refunds stay counted | Drive enrolments from stage-change activities by time window; page with a stable cursor; record reversals |
| 10 | **Open-stage leads gone quiet fall into no tier** | Candidates need a conversation in the last 15 days (`lead_priority.py:205`); R covers only closure stages 16–45 days (`build_plan_pdf.py:236`) | "Call Back Later", "Discovery Call Done", "May buy later" leads with no talk for 15+ days are never called again | Nightly capped "stale open stage" tier, WhatsApp first |

## 4. All other loopholes

**Who gets called**
- Stage names disagree: `"Invalid lead"` (`lead_priority.py:25`) vs `"Invalid"` (`call_plan.py:26`, PDF
  `:332`); `fresh_leads.py:28` lacks "Duplicate". Dead leads can take shortlist slots, or reach sheets.
- Tier D, revive leads beyond 8 per owner, and callers missing from `owner_order` are dropped with no
  "excluded" list (`call_plan.py:51-52, 91-92, 353`).
- Leads staged "Not Interested"/"Invalid" with no real conversation are recovered only for fresh leads
  (`fresh_leads.py:85`), despite the PDF rule "never mark Not Interested without a real conversation".
- A missed callback disappears once the caller resets the follow-up date: only the current field is checked
  (`team_report.py:253`, `lead_priority.py:121`).
- Owners are frozen at 01:30 IST, while the PDF orders a 09:25 reassignment (`build_plan_pdf.py:220, 416`).
- The plan is hand-built for one team and one day: "Wed night", "Thu 8 Oct", "Team Elite Calling", caller
  names and targets are hardcoded (`call_plan.py:56, 157, 239, 244`; `build_plan_pdf.py:176-220`).
- Tier chances (A 25–50%, B 8–25%, F ~5%, R ~4%) drive the sort order and the "on track" verdict but are
  never checked against outcomes (`call_plan.py:27, 293`; `build_plan_pdf.py:417`).

**How effort and quality are measured**
- Failed dials count as dials; one bad day at 50%+ `CallFailure` exempts a caller for the whole range
  (`aggregate.ts:39`, `metrics.ts:70`).
- Answered inbound calls earn no talk time or conversation credit in the Worker (`aggregate.ts:51-54`), but
  do in `team_report.py:117-122`. Missed-inbound % is computed and never shown or flagged.
- The default "Today" range can never show a lagging caller (`app.js:3`, `metrics.ts:71`), so the
  intraday check-ins see 0.
- The data-gap alarm is account-wide only (`metrics.ts:81-90`): one team's dialer outage doesn't trip it.
- Zip notes can be credited to a later caller (`tasks.ts:128-140`); there is no per-caller Zip table.
  "Clear Call to Action" isn't populated, and no report uses transcripts.
- Hitting the write budget pauses ingestion with no red alert (`tasks.ts:242`); backfill rows aren't added
  to the budget.
- DNP outage detection only catches one user's same-second failures and never removes them from caller
  rates (`dnp_report.py:121-122`).

**Definitions that disagree** (against the "one meaning everywhere" rule)
- Working day: 20 dials (Worker, `dnp_report`), 10 (`team_compare.py:168`), any call (`team_report.py:132`).
- Real conversation: 120 s everywhere except `lead_priority.py:100` (60 s).
- Enrolment: first stage change (Worker) vs `mx_Enrollment_date` or current stage (`team_report.py:308, 314`).
- Speed-to-lead buckets differ between `team_report.py:57` and `fresh_leads.py:58`.

**Data that can go missing**
- Lead paging sorts by `ModifiedOn` (`client.py:217`); leads edited mid-fetch can be skipped.
- Activity paging ignores `RecordCount` (`client.py:330-334`).
- Errored lead histories are marked done and never retried (`fetch_lead_histories.py:20`).
- Leads moved out of a team leave its reports, hiding neglect (`fetch_team_data.py:44`).

## 5. Per-call transcript analysis

Today call quality is judged from one source: Zipteams' summary of a single call. The repo's own review
found that summary misjudged about half of the hottest leads (section 6). The transcripts that would
correct it are available but unused.

### 5.1 Transcript loopholes

| # | Loophole | Evidence | Revenue consequence |
|---|---|---|---|
| T1 | **No report reads a transcript.** The client returns each answered call's full text (`Call.transcript`, `integrations/transcripts/client.py:148`), but no analytics module or script calls it | grep: only the client, its test and the Zipteams client import it | Every judgement about a conversation rests on a one-call Zip summary |
| T2 | **Zipteams objection data is downloaded and ignored.** `mx_Zip_Objection_Category`, `mx_Zip_Objection`, `mx_Zip_Quality_Score`, `mx_Zip_Talking_Points` are fetched (`scripts/fetch_team_data.py:22-24`) and read nowhere | grep finds no reader | The objections that block enrolments are never counted, though they cost no transcript budget |
| T3 | **The behaviour the plan depends on is unmeasured.** "Every connected call ends with a date" (`build_plan_pdf.py:250`), but Zip's "Clear Call to Action" is empty (`team_report.py:25`) | | Nobody knows which callers end calls without a payment date or fixed callback |
| T4 | **Keyword scoring can't tell who said what.** Buying signals are matched in Zip's summary text (`lead_priority.py:118`), so the caller saying "EMI" scores like the lead asking for it | | Leads are ranked on the caller's pitch, not the lead's intent |
| T5 | **Matching a transcript to a LeadSquared call by time is fragile.** Some sources label IST as UTC and the zone is inferred (`client.py:79-107`) | | A wrong match credits a conversation to the wrong call or caller |
| T6 | **Support calls come back in the same search** (`client.py:251-252`, `kind = "support"`) and are not separated in any analysis | PDF `:402`: enrolled students' support issues reach sales | Sales time spent on support is invisible; support calls can pollute quality scores |

### 5.2 What to extract from each call

One structured record per answered call of 2+ minutes, produced by an LLM read of the transcript (the repo
already relies on a "human/LLM read" for Tier A/B, `lead_priority.py:8`). Every field maps to a lever:

| Field | Values | Feeds |
|---|---|---|
| Next step agreed | payment date / fixed callback / demo / none; the date and time | Plan Tier A/B; callback check; caller "ends with a date" rate |
| Payment discussed | fee quoted, EMI offered, link sent on call, token, finance refused | "Link sent, not paid" tier; payment-friction log |
| Objections | price, time, job relevance, family approval, course not offered, enrolled elsewhere, trust/refund; **handled or not** | Coaching per caller; catalogue and pricing feedback |
| Lead's own buying signals | quotes from the **lead's** turns only | Replaces keyword scoring (#4, T4) |
| Decision-maker | lead decides / needs parent or spouse / unknown | Three-way call scheduling |
| Course asked vs pitched | the two course names | Lost sales from catalogue gaps |
| Stage consistency | did the lead refuse? vs stage set after the call | Recovers leads wrongly marked Not Interested |
| Caller behaviour | probing questions asked, pitch matched to stated goal, talk share | Per-caller coaching beyond Zip pass/fail |
| Support issue | yes / no | Separates support load from sales (T6) |

Check the extraction before trusting it: compare "next step = payment date" and lead buying signals with
enrolments within 7 days, and with Zip intent on the same calls.

### 5.3 Working within the transcript budget

The client allows 9 requests of 10 numbers per run (`client.py:32-33`), so **90 leads per run**, each
returning that number's full call history. Calls without a transcript need a `generate_transcripts` request
first and a later run to read them. Spend each run in this order:

1. Tier A and B leads for tomorrow's plan, so the plan is built on what the lead actually said.
2. Leads where Zipteams and LeadSquared disagree (`crosscheck_flags`, `lead_priority.py:98`).
3. Leads moved to Not Interested or Invalid after a real conversation.
4. A fixed weekly sample per caller (for example their 5 longest calls) for coaching.

Transcripts contain lead PII: store them under `data/` (git-ignored) only.

## 6. What the repo's own reviews already found

These come from the 8 Oct call-plan review (judgement, not measured):

- **Yield gap:** about 1–2 enrolments per caller likely tomorrow, ~4 across three days, against a target of
  4 per caller per day (`build_plan_pdf.py:204-210`).
- **Zip misjudges the hottest leads:** it understated about a third (Neutral/Low leads who already had
  payment links, EMI paperwork or part-payments) and overstated about a sixth (`build_plan_pdf.py:229`).
- **Wasted dials:** several Nurture leads had 15–30 dials with no conversation (`:305`).
- **Lost sales from catalogue gaps:** leads asking for an unavailable course were lost "last week" (`:347`).
- **Payment friction:** finance refused for age or card limits; an invoice discrepancy (`:401`).
- **Workload imbalance:** some callers had 21–23 Tier B leads, one had no Tier A (`:220`).
- **Dialer failures:** two callers' calls failing in the same second (`:245`).

## 7. Action plan

Ordered by revenue per unit of effort. Steps 1–6 are small code changes in this repo.

| Step | Do | Loopholes closed | Lever |
|---|---|---|---|
| 1 | Add a "missed call from lead" tier and a "link sent, not paid" tier to the call plan | #1, #2 | Return calls; close rate |
| 2 | Read payment activities (event 213); show revenue per caller and course | #3 | North star |
| 3 | Fix keyword patterns and unify stage names in one shared module | #4, stage mismatch | Close rate |
| 3a | Report Zipteams objection categories and quality scores per caller and course (no transcript budget) | T2 | Conversation quality |
| 3b | Per-call transcript review (section 5.2) for Tier A/B and disputed leads before each plan; weekly sample per caller | T1, T3, T4, T6 | Close rate; coaching |
| 4 | Dashboard: per-working-day averages, list sub-20 callers, conversion-based lagging, 7-day default for the Callers card | #6, #7, #8, Today view | Productive dialling |
| 5 | Credit enrolments to the owner at enrolment and the last answered caller | #5 | Measurement |
| 6 | Add "stale open stage" and "dead without conversation" lists | #10, wrongly dead leads | Leads reached |
| 7 | Rebuild the enrolment sync on stage-change activities; nightly rebuild of yesterday | #9, sync window | Measurement |
| 8 | Automate the nightly plan for every team, with dates computed and owners refreshed (G2) | hardcoding, frozen owners | All |
| 9 | Record outcomes per tier and re-set tier chances from them | uncalibrated tiers | Close rate |
| 10 | Align definitions (working day, real conversation, enrolment, buckets) across Python and Worker | divergences | Measurement |

## 8. Sizing the leaks (needs read-only LeadSquared and transcript access)

With `LEADSQUARED_ACCESS_KEY` and `LEADSQUARED_SECRET_KEY` (and `SALESA_API_KEY` for transcripts) set, these counts turn the list above into
rupees. Each uses scripts already in the repo:

| Question | How |
|---|---|
| How many leads called in, were missed, and got no callback within 1 day? | `scripts/fetch_all_calls.py` for the last 30 days, grouped by lead |
| How many leads have a payment link sent but no payment? | Payment activities (event 213) vs stage and notes |
| What does event 213 contain: amount, course? Is it complete? | One day of `fetch_team_data.py` output, `payments` field |
| How many open-stage leads have had no conversation for 15+ days? | Lead search by stage + last activity date |
| How many leads were marked Not Interested/Invalid without a 2-min conversation? | `analytics/fresh_leads.py` extended to all leads |
| How different are enrolments credited by current owner vs owner at enrolment? | `team_compare.enrollment_credit` vs current owner |
| How much are dashboard per-day averages inflated per caller? | D1 `caller_day` rows, both formulas |
| Which objections block enrolments, by course and caller? | Zipteams objection fields already in `fetch_team_data.py` snapshots |
| What share of real conversations end with a dated next step, per caller? | Per-call transcript review (needs `SALESA_API_KEY`) |
| Is there a daily cap on transcript requests beyond 9 per run? | Ask the Salesa API owner |
