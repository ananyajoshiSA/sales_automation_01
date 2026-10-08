# Project Goal: Revenue Boost

## 1. Goal

**Raise course revenue by turning more of the leads already in LeadSquared into first-time enrolments,
with the same calling team.** Guiding question: *which call, made by whom and when, most raises today's
enrolments?*

- **Track A, Grow:** five levers raise enrolments daily (section 3).
- **Track B, Stop the leaks:** find every loophole that loses leads (section 4), measure the revenue each
  costs (section 5), then recover and prevent it, largest loss first.

No report reads revenue yet: payment-success activities (event 213) are fetched but unused (G1). Until
then, success and losses are stated in **first-time enrolments**, in rupees only where a fee is known.
Code evidence for every leak: `REVENUE_ANALYSIS.md`.

## 2. Targets

| # | Target | Done when |
|---|---|---|
| T0 | Revenue boost: lift in enrolments and revenue over baseline | Size and date set by the owner (section 11) |
| T1 | Every lead in the window in exactly one ledger state | Ledger total = LeadSquared count, no gap |
| T2 | Every lost lead has one primary leak | None unexplained |
| T3 | Every leak proven: rule in code, count, 20 random leads hand-checked | ≥18 of 20 correct, else fix the rule first |
| T4 | Every leak priced with a range, recoverable vs sunk | Report reproduces from one command |
| T5 | Recoverable leads reach a caller's plan | Within 1 working day |
| T6 | Weekly scan lists lost leads no rule explains | Each new pattern added with T3 proof |
| T7 | Share of recoverable lost revenue won back monthly | Set by the owner from the first loss report |

## 3. Track A: how enrolments grow

`Revenue = enrolments × fee` · `Enrolments = leads reached × real-conversation rate × close rate`
(+10% per term ≈ +33%; arithmetic, not a forecast).

| Lever | Raises | Closes | Measured by |
|---|---|---|---|
| 1 Speed to lead | Leads reached | L1, L2, L13 | Capture to first dial: ≤5 min … >24 h, never |
| 2 Productive dialling | Conversation rate | L3 | Real conversations (2+ min) per working day (20+ dials); connect % |
| 3 Return leads' calls | Close rate | L4 | Missed inbound not returned same day ("strongest signal") |
| 4 Right leads first | Close rate | L6–L9, L12 | Tiers A/B/F/R/C, month-end promises; enrolments per tier |
| 5 Conversation quality | Close rate | L5, L10, L11 | Zipteams pass rates; dated next step; objections handled |

## 4. Track B: finding every loophole that loses leads

**Lead ledger.** Each lead is traced: created → assigned → first dial → real conversation → dated next
step → payment started → payment (213) → first Course Enrolled. It ends as *enrolled*, *open*, *lost at
L#* or *lost after a complete process* (the benchmark). The primary leak is the first hit; others are tags.

| # | Leak | Detection rule |
|---|---|---|
| L1 | Not assigned or held | Creation to first `LeadAssigned` to a caller |
| L2 | Never or late dialled | Creation to first dial or answered inbound |
| L3 | Never connected | N dials, no answer (several N); `CallFailure` apart |
| L4 | Lead's call not returned | Missed inbound, no dial back same working day |
| L5 | No dated next step | No follow-up set within 2 h; none in transcript |
| L6 | Follow-up missed | Due passed, no owner dial ±2 h (from history) |
| L7 | Dead without talk | Not Interested/Invalid with no 120 s+ call |
| L8 | Dropped from plans | Open, no talk 15+ days, on no tier |
| L9 | Payment not completed | Link, token or EMI started; no 213 in horizon |
| L10 | Course not matched | Asked course unavailable (transcript/Zip) |
| L11 | Objection unhandled | Objection with no answer (Zip fields/transcript) |
| L12 | Hot lead ranked low | Lead's own buying words, below Tier B |
| L13 | Lost in reassignment | No dial by new owner in D days |

Thresholds sit in one place in code; unset ones are reported at several values.

**Measurement loopholes to fix first:** M1 enrolment credited to current owner, not closer; M2 enrolment
sync can skip or freeze; M3 Python and Worker define enrolment differently; M4 per-day averages inflated;
M5 callers under 20 dials vanish; M6 late-posted calls missed; M7 paging can skip records.

## 5. Track B: measuring the revenue each loophole costs

Per leak L, by stratum (source × course × lead age):

`Lost revenue(L) = Σ n_L × (p_ok − p_L) × fee(course)`

- `n_L`: leads whose primary leak is L.
- `p_L`: their enrolment rate within horizon H.
- `p_ok`: rate of comparable leads that passed that stage cleanly.
- `H`: time within which 90% of enrolments happen, measured from history.
- `fee`: median paid per course from event 213 (list price only if missing, labelled).

**Precision rules**
- Every figure has a 90% range (bootstrap); thin strata merge upward, stated.
- Revenue counts on the primary leak only, so leaks add up to the total.
- Reconcile: enrolled + open + lost + complete-process = all leads; revenue = event 213 total.
- Split recoverable (open, contactable) from sunk; only recoverable feeds T7.
- No leak is priced before its T3 check passes.
- `p_ok − p_L` is a comparison, not proof of cause: stratify, and where agreed confirm the largest leaks
  by working recoverable leads in two random batches.
- Every number comes from repo scripts with date range and pull time; estimates labelled.

**Per-call transcript review** (LLM read, human spot-checked) of answered 2+ min calls: next step
agreed; payment talk (fee, EMI, link, token, finance refused); objections and whether handled; lead's own
buying words; decision-maker; refusal vs stage set; support issue. Feeds L5–L7 and L9–L12. Budget: 90 leads per
run (9 requests × 10 numbers), in order: T3 samples, Tier A/B, Zip–LeadSquared
disagreements, weekly per-caller sample.

**Monthly loss report:** total with range; by leak, largest first; by team, caller, course, source;
recoverable leads with next steps.

## 6. Principles

Zipteams intent is one input, never the answer. Fix the dialer (50%+ `CallFailure`) before judging the
caller. Count first-time enrolments only, each call once, in IST. No invented numbers.

## 7. Success measures

Baseline: leads created 8 Sep–7 Oct 2026, outcomes followed to H. Track: revenue and enrolments per
caller, team, course, source (T0); loss by leak, weekly leak counts, recovery (T4, T6, T7); the lever
measures in section 3; guardrails: dialer issues, data-gap alarms.

## 8. Gaps

| # | Gap | Done when |
|---|---|---|
| G1 | Read event 213; revenue in D1 and dashboard | Revenue per caller, team, course, source |
| G2 | Nightly plan for every team, no hardcoding | Plan each morning, no manual edits |
| G3 | Tier chances are guesses | Re-set from measured outcomes |
| G4 | Speed to lead not alerted | Undialled fresh leads seen live |
| G5 | Transcripts and Zip objections unused | Reviews before each plan; weekly per caller |
| G6 | Reports run one team at a time | Every team weekly, by assignment history |
| G7 | No ledger or leak detection | T1–T3 met for the baseline |
| G8 | No loss report | T4 met |
| G9 | M1–M7 | All tools agree on the same days |

## 9. Rhythm

Evening: plan built with recoverable leads. 09:35: payment links ready. 10:00–11:30: Tier A and missed
calls first; every call ends with a date. 12:00, 15:00, 18:00, 19:30: team leader checks. Weekly: levers,
lagging callers, leak scan. Monthly: revenue vs T0, loss report, recovery vs T7.

## 10. Constraints and non-goals

- **Constraints:** read-only against LeadSquared and Zipteams; Cloudflare free tier; transcripts 9 × 10
  per run; credentials in `.env`; lead data in `data/` and `exports/` only.
- **Non-goals:** more leads, pricing, hiring, marketing.

## 11. Open decisions

- [ ] Is event 213 complete, with amount and course? If not, which system holds payments?
- [ ] T0: what lift, by when?
- [ ] T7: what recovery share?
- [ ] May the largest leaks be confirmed with two random batches?
- [ ] Which teams follow Team Elite Calling?
