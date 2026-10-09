# Fixed parameters for the daily team calling report

Version 1.4, 9 Oct 2026. v1.4 changes the layout and no team or caller figure: the 1–2 page limit is gone (P60). The PDF opens with a one-page summary anyone can read, then explains every figure in plain language with charts, a "What this means" note under the main charts and tables, and a table of every caller (P62, P62a). It adds descriptive views built from the existing figures (P23a, P24a, P29d, P52a, P52b, P63a), names the calling-software groups that P11 can pick as a team (P11a), replaces validation check 5 (the summary must fit on page 1), rewords checks 4 and 6 for the new sections and adds check 15 (every section present). The P52 coaching case now skips callers with a dialer issue or already recognised, and every caller list breaks ties by talk minutes, then name. Every v1.3 team and caller figure is computed the same way, so v1.4 and v1.3 figures for the same day are comparable. v1.3 fixes the day window and transcript times (S1, S3, S6, P2, P32, P40, checks 8 and 12; see [docs/timezone_issues.md](../timezone_issues.md)): calls are read up to 3 days past the day so later-edited calls are kept, each transcript takes its time from its LeadSquared call, each Zipteams analysis is kept on the call it analysed, including a call that ends after midnight, and windows are read one page at a time because LeadSquared's paging skips a few rows. Definitions are unchanged, but v1.3 counts can be slightly higher than v1.2 for the same day, so compare reports only within a version. v1.2 adds the call-integrity section (11, P67–P74), its page-2 block (P62) and validation check 14. v1.1 changed only the layout (P62/P64) and added the blocking validation gate (section 10). Every definition and number rule from v1.0 is unchanged, so v1.0–v1.2 figures for teams and callers are comparable with each other. Every run of the report must use these definitions unchanged. If any parameter is changed, bump the version and print it on the report, so two reports can only be compared when they share a version.

The prompt [PromptToExecute.xml](PromptToExecute.xml) carries the same values in its `<parameters>` block. Change both files together.

---

## 1. Scope

| ID | Parameter | Fixed value | Requirement |
|---|---|---|---|
| P1 | Target day | `{TARGET_DATE}`, a single IST calendar day | Window is 00:00:00 to 23:59:59 IST, i.e. previous day 18:30:00 to target day 18:29:59 UTC on LeadSquared's clock. |
| P2 | Timezone | IST (UTC+05:30) | All times shown in IST, using `integrations/timeutil.py`. LeadSquared `CreatedOn` is UTC and is the call start; never use a call note's `StartTime` (one copy is UTC, the other IST, neither labelled). A transcript's time is its matched LeadSquared call's time (`match_to_calls`), because API clocks can be 5 h 30 min off in either direction. |
| P3 | Conversion window | Target day through target day + 3 days (IST), capped at "now" | The window must be printed on the report. Never compare reports with different windows. |
| P4 | Repository | github.com/ananyajoshiSA/sales_automation_01, `main` | Run from the repo with `PYTHONPATH=.`. Install `requirements.txt` first. Run `python -m integrations.leadsquared check` and stop if it fails. |

## 2. Data sources (all three are mandatory)

| ID | Source | What is read | How |
|---|---|---|---|
| S1 | LeadSquared phone activities | Every outbound (event 22) and inbound (event 21) call in the window, parsed with `parse_phone_call` | `scripts/fetch_all_calls.py {D} {D} data/all_calls_{D}.jsonl` or `iter_activities_started`, which reads edits up to 3 days past the window (the API filters on `ModifiedOn`) and keeps calls by `CreatedOn`. Print how many later-edited calls were recovered. **Never** use "leads modified" or lead edits as a proxy for calls. |
| S2 | LeadSquared users | `UserManagement.svc/Users.Get` (all users with `MemberOfGroups`) | `LeadSquaredClient().get_users()` |
| S3 | Zipteams analysis | "Zipteams Notes" activities, event 237, created in the window or in the 2 hours after it (a note is written as its call ends) | `iter_activities_started(237, …)`, same 3-day edit margin as S1. Must be included. A report without Zipteams is not a valid run. |
| S4 | Enrollments | Leads whose `ProspectStage` = "Course Enrolled", with stage-change history (event 3002) | Same logic as `scripts/d1_backfill.py` "first-time enrollments" |
| S5 | Transcripts | Centralized transcript API, `TranscriptClient.search` | See P30–P34 for sampling and limits |
| S6 | Payments (context only) | "Payment Successful", event 213, read with the same 3-day edit margin as S1 | Report the count; do not use it as the conversion measure while it is empty. |

## 3. Team mapping

| ID | Parameter | Fixed value |
|---|---|---|
| P10 | Caller identity | Match a call to a user by `user_id` first, then by exact caller full name. |
| P11 | Team of a caller | The **first** group in the user's `MemberOfGroups` (same as `analytics/dnp_report.team_of_user`). Each caller counts in exactly one team. |
| P11a | Calling-software groups | "Acefone Users" and "Mcube Users" are groups for the calling software, not sales teams. When LeadSquared lists one first, P11 still puts the caller there; the report says so on page 1 (when such a group is ranked), in section 1 and in section 3, which lists each group's callers by their next sales group. |
| P12 | Unassigned | Callers with no group go to "Unassigned". Calls whose caller is not a LeadSquared user go to "Not a user" (IVR, bots). Both are shown in totals but **never ranked**. |
| P13 | Ranking eligibility | A team is ranked only if it has **≥ 3 callers** and **≥ 25 real conversations** (P22). Smaller teams appear in an appendix line only. |
| P14 | Bots | Automated welcome, reminder or webinar agents are excluded from every caller and team figure. |

## 4. Calling metrics (all from S1)

| ID | Metric | Exact definition |
|---|---|---|
| P20 | Dials | Count of outbound phone activities (event 22) by the team's callers in the window. |
| P21 | Answer rate | Outbound calls with `status == "Answered"` ÷ dials, as a percentage with 1 decimal. |
| P22 | Real conversation | Any call (inbound or outbound) with `status == "Answered"` **and** `duration ≥ 120` seconds. |
| P23 | Leads reached | Distinct `lead_id` with at least one real conversation. |
| P24 | Talk minutes | Sum of `duration` of all answered calls ÷ 60, rounded. |
| P25 | Callers | Distinct callers with at least one outbound call. |
| P23a | Calls to enrolments | Across all calls of the day (including Unassigned and Not a user): calls, answered calls (inbound and outbound), real conversations, leads reached, credited enrollments, credited ÷ leads reached, and how many credited leads are among the leads reached (credit follows any answered call, P28, so a lead can enrol after a call shorter than 2 minutes). Descriptive only. |
| P24a | Calls by hour | Per IST hour, from the first to the last hour with calls (an empty hour between them shows 0 and "–"): dials, answered dials and their share, real conversations and inbound calls. The best and worst answer-rate hours are named only among hours with at least 2% of the day's dials. The hour table is drawn in blocks of 12 hours. |
| P26 | Not allowed | "Connected" defined by lead stage, lead edits or owner counts. These measure CRM activity, not calling. |

## 5. Conversion metrics (from S4)

| ID | Metric | Exact definition |
|---|---|---|
| P27 | Enrollment | A lead's **first ever** stage change to "Course Enrolled", with its timestamp inside the conversion window (P3). Re-tagged or already-enrolled leads do **not** count. A lead that merely *sits* in an enrolled stage is not an enrollment. |
| P28 | Enrollment credit | Credit each enrollment to the caller with the **most answered talk time on that lead on the target day**. If no caller spoke to the lead that day, it is not credited to calling. |
| P29a | Conversion rate | Credited enrollments ÷ leads reached (P23), as a percentage with 1 decimal. **This is the primary ranking metric.** |
| P29b | Same-day enrollments by owner | Enrollments with timestamp on the target day, grouped by the lead owner's team. Context column only. |
| P29c | Warm-lead flag | Teams that mainly call bootcamp registrants or post-enrolment leads (currently Elite Changemakers and DSV teams) are marked "warm leads" in the table. They stay ranked, but the flag must be visible. |
| P29d | Enrollments by day | Enrollments in the conversion window by IST day, split into credited (P28) and not credited. |

## 6. Zipteams quality metrics (from S3)

| ID | Metric | Exact definition |
|---|---|---|
| P40 | Attribution | Each 237 note is credited to the caller of the lead's **last answered call at or before** the note's `CreatedOn`, and its analysis (intent, scores, payment step) is kept on that call (`analytics/zip_calls.py`; per call in `data/report_{D}/zip_calls.csv`). Notes with no such call are dropped and counted. A note written after the day counts only if it lands within 5 minutes of the end of one of the day's calls; the rest belong to another day and are counted separately. |
| P41 | Probing % | Mean of `mx_Custom_5` (0 or 100) over the caller's or team's notes. |
| P42 | Product pitch % | Mean of `mx_Custom_4`. |
| P43 | Objection handling % | Mean of `mx_Custom_6`. |
| P44 | Intent | `mx_Custom_1` ∈ {HIGH, MODERATE, NEUTRAL, LOW, NOT_AVAILABLE}. **High/moderate %** = (HIGH + MODERATE) ÷ notes rated (excluding NOT_AVAILABLE). |
| P45 | Coverage | Show "–" for teams with no Zipteams notes. Never treat a missing score as zero. |
| P46 | Clear call to action | `mx_Custom_7` is not populated by Zipteams and must not be used. |

## 7. Transcript analysis (from S5)

| ID | Parameter | Fixed value |
|---|---|---|
| P30 | Request limits | ≤ 10 numbers per request; ≤ 9 requests per `TranscriptClient` run; and the API allows **≤ 10 requests per minute**, so wait ≥ 7 s between requests and 60 s after earlier calls. Use `max_retries=0` and catch errors per chunk. |
| P31 | Sample | Up to 90 leads: up to 40 **converted** leads (P27, credited by P28, longest target-day call first) and up to 50 **non-converted** leads with an answered call ≥ 300 s, 10 at random (seed 5) from each of the 5 ranked teams with the most real conversations. |
| P32 | Unit of analysis | Keep only transcripts that match one of the lead's target-day LeadSquared calls (same number, start within 10 min, a ±5 h 30 min shift allowed when durations agree within 10%); print how many needed the shift. Per lead, analyse the **longest** transcript of ≥ 120 s. |
| P33 | Close-behaviour markers | Use these case-insensitive regex markers and report the % of converted vs non-converted calls containing each: price/fee/EMI; discount/scholarship/offer; payment step (payment link, pay now, balance payment); urgency (deadline, seats, last date); discovery questions; batch/LMS/onboarding; career/ROI; fixed next step (date/time). |
| P34 | Quotes | At most 3 short quotes, each tied to a named caller and team. Never quote customer phone numbers or names. |

## 8. Ranking and recognition

| ID | Parameter | Fixed value |
|---|---|---|
| P50 | Team ranking | Sort eligible teams (P13) by credited enrollments (P28), then by conversion rate (P29a), then by real conversations. |
| P51 | Best team | The rank-1 team. Name a runner-up and the best non-warm front-line team separately. |
| P52 | Caller recognition | Up to 9 callers: 8 sorted by credited enrollments, then real conversations, then talk minutes, then name, plus one coaching case: the caller with the most real conversations (then talk minutes, then name) among callers with ≤ 1 credited enrollment and ≥ 1 real conversation, team not Unassigned, no dialer issue (P52b) and not already recognised. |
| P52a | Callers who need support | Up to 5 callers chosen like the P52 coaching case, which comes first. The note under them is generated from their figures: their real conversations and enrollments, any of them with most of their long calls flagged (P71), and how many more callers tie with the last one listed. |
| P52b | Dialer issue | A caller with 20+ dials (a working day) of which 50%+ ended in `CallFailure` (analytics/definitions.py). Listed so the dialer is fixed before the caller is judged; never a lagging caller. |
| P53 | Coaching assets | Callers with ≥ 15 Zipteams notes, sorted by probing + pitch + objection, then name. List up to 4, headed "Good questioning and explaining"; the note names any with no credited enrollment (their calls teach questioning, not closing) and any also in P52a. |

## 9. Output format

| ID | Parameter | Fixed value |
|---|---|---|
| P60 | File | PDF, A4 portrait, **no page limit**: a one-page summary, then as many pages as the explained sections and the caller table need. Saved as `/mnt/project-files/reports/team_calling_report_{TARGET_DATE}.pdf`. |
| P61 | Rendering | HTML rendered by headless Chromium (`--print-to-pdf --no-pdf-header-footer`), as in `scripts/build_plan_pdf.py`, with "Page N of M" and the day and version in each page's footer. The summary is also rendered alone and must come to exactly 1 page; if it doesn't, page 1 draws fewer ranked teams (15, then 12, 10, 8, 5, 3; the rest stay in section 3). Page 1 cuts team names after 32 characters; section 3 has them in full. If `meta.json` carries a `sample` note (a layout sample built from part of a day), page 1 shows it in a banner and every footer reads "SAMPLE, partial data"; a normal run never sets it. |
| P62 | Sections, in order | **Page 1, the summary, readable in a minute:** title with the weekday and date, the conversion window and when the data was read · the day as 5 connected steps (P23a: calls, answered, real conversations, leads reached, enrolled) · "what happened" (best team, runner-up, best front-line team, busiest teams, the biggest gap or tied gaps in the P33 sample, with call counts) · Teams ranked (conversion bar, enrolled, real conversations) · Callers to recognise (P52, incl. the coaching case) · What to do next (fix the phones first when any caller has a dialer issue, then the payment step, a full pitch and the coaching case). **Then:** 1. How to read this report (every term in plain words, contents) · 2. From calls to enrolments (P23a, P29d) · 3. Teams compared (P63, teams not ranked) · 4. Weak spots of the day (what happened, what to do) · 5. Team by team (a card per ranked team: figures, sentences against all teams, one suggested next step) · 6. Callers (recognise, need support P52a, dialer issue P52b, calls to learn from P53) · 7. What the converting calls had in common (P33 chart) · 8. Call quality scores (Zipteams, P41–P45) · 9. When the calls happened (P24a charts and table) · 10. Calls to review (P74) · 11. How this report was made and checked (method and every validation check) · Appendix: every caller, by team. |
| P62a | Plain language | Written for people who are not analysts: every term is explained in section 1; sections 2, 3, 6, 7, 8 and 9 carry a "What this means" note generated from the figures, and sections 4 and 5 say what to do for each finding and each team; conversion is written as enrolments per 100 leads reached, never as a share of them; every sentence is generated from the figures and handles ties, single teams, zero and missing values; every chart's numbers are also on its marks or in a table, colour is never the only signal (labels, legends), "–" means no data, and the PDF never shows customer names or numbers. |
| P63 | Scorecard columns | Section 3: Team, Callers, Dials, Answer rate, Real conversations, Leads reached, Talk minutes, Enrolled (credited), Conversion, Enrolled on the day (owner's team, P29b). Section 8: calls checked, Asked about needs (probing), Explained the course fully (pitch), Answered concerns well (objection handling), Lead's interest high or moderate (intent); ranked teams first, then the others under a divider. |
| P63a | Low answer rate | A team's answer rate is called low when it is below 70% of the day's answer rate (all dials; the same 70% as CLAUDE.md's lagging caller). Used only in the words of section 4 and the team cards, never in the ranking. |
| P64 | Required footnotes | Section 11 states the window (P3), the enrollment and credit definitions (P27, P28), the Zipteams coverage gap (P45), the team-mapping rule and the number of callers in more than one group (P11), the warm-lead flag (P29c), the validation summary with every check's result, and the parameter version. Section 1 explains the window, the enrollment and credit definitions, the warm-lead flag and any calling-software group (P11a) in plain words. |
| P65 | Weekday | Compute it from the date; never write it from memory. |
| P66 | Repo changes | None during a run. Data goes to the git-ignored `data/`. Nothing is committed or pushed without the owner's approval. The run itself is one command: `python -m analytics.team_performance {TARGET_DATE}`. |

## 10. Validation checklist (a blocking gate: every check runs before rendering, and one failure means no PDF and a message naming it; full log in `data/report_{TARGET_DATE}/validation.txt`)

1. `python -m integrations.leadsquared check` passed and the three env vars were set.
2. The S1 call count is printed and equals the sum of team dials and inbound calls, including Unassigned and Not a user.
3. Zipteams notes attributed / total is printed, and dropped notes are < 5%.
4. Every team figure quoted in page 1's "what happened" appears in the team tables (section 3).
5. The summary, rendered alone, fits on exactly 1 page (P61).
6. The parameter version ("Parameters v1.4") is printed in section 11, How this report was made and checked, which lists every check with its result.
7. No duplicate call or Zipteams activity IDs.
8. Every call and Zipteams note falls inside the target IST day, and no call has a start time that can't be read.
9. Calls from callers who are not LeadSquared users stay under 5%.
10. Each enrollment is counted once, and every credited caller had an answered call with that lead on the target day.
11. Every rate is within 0–100 and no call has a negative duration.
12. Totals reconcile: fetched = counted + other-day + unreadable-time + bot calls; calls = team dials + inbound; credited = sum of team credited = sum of their callers; Zipteams attributed + dropped = total.
13. A seeded sample of 10 credited enrollments is re-read from LeadSquared stage history and confirmed as first-ever "Course Enrolled" in the window.
14. Call-integrity counts reconcile (flagged and checked calls never exceed eligible calls; caller totals add up) and carry no lead details.
15. Every P62 section is in the report, in order.

## 11. Call integrity (v1.2; every flag means "needs review", never proof)

Eligible calls: answered calls of 2+ minutes (P20) by LeadSquared users. Thresholds were calibrated on 5 Oct 2026: 1,450 eligible calls, and a 90-call transcript sample with a median of 148 words a minute (lowest 28, 38) and a most-repeated 3-word phrase of at most 4% of the words.

| ID | Parameter | Definition |
|---|---|---|
| P67 | Purpose | Find long calls that may be fake, artificially stretched, or long with little or no conversation, and the callers who have the most of them. A flag is a prompt to listen to the recording. |
| P68 | Overlap | The caller's answered call starts more than 30 s before their previous answered call ends. Both calls are flagged if they are 2+ minutes long. 5 Oct: 6 calls. |
| P69 | Repeat | 3 or more real conversations between the same caller and lead on the day. 1% of 5 Oct caller-lead pairs. |
| P70 | Just over 2 minutes | A caller with 10+ eligible calls whose share of calls lasting 120–149 s is at least twice the day's account-wide share. That caller's 120–149 s calls are flagged. 5 Oct: account share 17.2%, 7 callers. |
| P71 | Ranking | Per caller: eligible calls, flagged calls, flagged share, and transcripts checked. Only callers with 10+ eligible calls are ranked, by flagged share. |
| P72 | Transcript signals | No content: under 30 words. Thin: under 60 words a minute of LeadSquared duration, about 40% of the 5 Oct median. Recorded message: IVR, voicemail, switched-off or hold text in the first 40 words. Loop: one 3-word phrase making up 15%+ of the words. The transcript has no speaker labels or timestamps, so the customer's share of the talk and silences can't be measured; words a minute stands in for silence. |
| P73 | Transcript sample | Within the API limits (90 numbers, 9 requests a run, and its own run: `python -m analytics.call_integrity TARGET_DATE [--limit N]`). The sample is one third calls already flagged by P68–P70, one third each caller's longest call, and the rest 120–149 s calls. A transcript matches the LeadSquared call on the same number that started closest to it, within 10 minutes. Some API start times are off by exactly 5 h 30 min (wrong timezone label), so that shift also counts when the two durations agree within 10%. |
| P74 | Output | The report's section 10 "Calls to review" shows flagged counts by signal, transcript coverage, and up to 5 ranked callers with flagged/eligible calls. Per-call evidence (call ID, caller, duration, words, words a minute, flags) goes to `data/report_{TARGET_DATE}/integrity_calls.csv`. No lead names or numbers appear anywhere in the output. |
