# Fixed parameters for the daily team calling report

Version 1.1, 8 Oct 2026 (v1.1 changed only the layout, P62/P64, and added the blocking validation gate in section 10; every definition and number rule is unchanged from v1.0). Every run of the report must use these definitions unchanged. If any parameter is changed, bump the version and print it on the report, so two reports can only be compared when they share a version.

The prompt [PromptToExecute.xml](PromptToExecute.xml) carries the same values in its `<parameters>` block. Change both files together.

---

## 1. Scope

| ID | Parameter | Fixed value | Requirement |
|---|---|---|---|
| P1 | Target day | `{TARGET_DATE}`, a single IST calendar day | Window is 00:00:00 to 23:59:59 IST, i.e. previous day 18:30:00 to target day 18:29:59 UTC on LeadSquared's clock. |
| P2 | Timezone | IST (UTC+05:30) | All times shown in IST. LeadSquared `CreatedOn` is UTC and must be converted. Transcript times use `Call.start_time` (already timezone-corrected by `integrations/transcripts`). |
| P3 | Conversion window | Target day through target day + 3 days (IST), capped at "now" | The window must be printed on the report. Never compare reports with different windows. |
| P4 | Repository | github.com/ananyajoshiSA/sales_automation_01, `main` | Run from the repo with `PYTHONPATH=.`. Install `requirements.txt` first. Run `python -m integrations.leadsquared check` and stop if it fails. |

## 2. Data sources (all three are mandatory)

| ID | Source | What is read | How |
|---|---|---|---|
| S1 | LeadSquared phone activities | Every outbound (event 22) and inbound (event 21) call in the window, parsed with `parse_phone_call` | `scripts/fetch_all_calls.py {D} {D} data/all_calls_{D}.jsonl` or `iter_activities_by_event`. **Never** use "leads modified" or lead edits as a proxy for calls. |
| S2 | LeadSquared users | `UserManagement.svc/Users.Get` (all users with `MemberOfGroups`) | `LeadSquaredClient().get_users()` |
| S3 | Zipteams analysis | "Zipteams Notes" activities, event 237, created in the window | `iter_activities_by_event(237, …)`. Must be included. A report without Zipteams is not a valid run. |
| S4 | Enrollments | Leads whose `ProspectStage` = "Course Enrolled", with stage-change history (event 3002) | Same logic as `scripts/d1_backfill.py` "first-time enrollments" |
| S5 | Transcripts | Centralized transcript API, `TranscriptClient.search` | See P30–P34 for sampling and limits |
| S6 | Payments (context only) | "Payment Successful", event 213 | Report the count; do not use it as the conversion measure while it is empty. |

## 3. Team mapping

| ID | Parameter | Fixed value |
|---|---|---|
| P10 | Caller identity | Match a call to a user by `user_id` first, then by exact caller full name. |
| P11 | Team of a caller | The **first** group in the user's `MemberOfGroups` (same as `analytics/dnp_report.team_of_user`). Each caller counts in exactly one team. |
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
| P26 | Not allowed | "Connected" defined by lead stage, lead edits or owner counts. These measure CRM activity, not calling. |

## 5. Conversion metrics (from S4)

| ID | Metric | Exact definition |
|---|---|---|
| P27 | Enrollment | A lead's **first ever** stage change to "Course Enrolled", with its timestamp inside the conversion window (P3). Re-tagged or already-enrolled leads do **not** count. A lead that merely *sits* in an enrolled stage is not an enrollment. |
| P28 | Enrollment credit | Credit each enrollment to the caller with the **most answered talk time on that lead on the target day**. If no caller spoke to the lead that day, it is not credited to calling. |
| P29a | Conversion rate | Credited enrollments ÷ leads reached (P23), as a percentage with 1 decimal. **This is the primary ranking metric.** |
| P29b | Same-day enrollments by owner | Enrollments with timestamp on the target day, grouped by the lead owner's team. Context column only. |
| P29c | Warm-lead flag | Teams that mainly call bootcamp registrants or post-enrolment leads (currently Elite Changemakers and DSV teams) are marked "warm leads" in the table. They stay ranked, but the flag must be visible. |

## 6. Zipteams quality metrics (from S3)

| ID | Metric | Exact definition |
|---|---|---|
| P40 | Attribution | Each 237 note is credited to the caller of the lead's **last answered call at or before** the note's `CreatedOn`. Notes with no such call are dropped and counted. |
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
| P32 | Unit of analysis | Keep only calls whose IST start date = target day. Per lead, analyse the **longest** transcript of ≥ 120 s. |
| P33 | Close-behaviour markers | Use these case-insensitive regex markers and report the % of converted vs non-converted calls containing each: price/fee/EMI; discount/scholarship/offer; payment step (payment link, pay now, balance payment); urgency (deadline, seats, last date); discovery questions; batch/LMS/onboarding; career/ROI; fixed next step (date/time). |
| P34 | Quotes | At most 3 short quotes, each tied to a named caller and team. Never quote customer phone numbers or names. |

## 8. Ranking and recognition

| ID | Parameter | Fixed value |
|---|---|---|
| P50 | Team ranking | Sort eligible teams (P13) by credited enrollments (P28), then by conversion rate (P29a), then by real conversations. |
| P51 | Best team | The rank-1 team. Name a runner-up and the best non-warm front-line team separately. |
| P52 | Caller recognition | Up to 9 callers: sorted by credited enrollments, then real conversations. Include the highest-volume caller with ≤ 1 enrollment as a coaching case. |
| P53 | Coaching assets | Callers with ≥ 15 Zipteams notes, sorted by probing + pitch + objection. List up to 4. |

## 9. Output format

| ID | Parameter | Fixed value |
|---|---|---|
| P60 | File | PDF, A4 portrait, **exactly 1–2 pages**, saved as `/mnt/project-files/reports/team_calling_report_{TARGET_DATE}.pdf` |
| P61 | Rendering | HTML rendered by headless Chromium (`--print-to-pdf --no-pdf-header-footer`), as in `scripts/build_plan_pdf.py`. Check the page count before delivering. |
| P62 | Sections, in order | **Page 1, readable at a glance:** title with the weekday and date · 4 KPI tiles (calls, answered, Zipteams-scored calls, credited enrollments) · verdict box · 1. Teams ranked (conversion bar, enrolled, real convs) · 2. Callers to recognise (P52, incl. the coaching case) · 3. Do next (top three actions). **Page 2:** 4. Team scorecard (P63) · 5. Why the top teams won · 6. Where other teams lost revenue · Method and limits with the validation summary |
| P63 | Scorecard columns | Team, Callers, Dials, Answer %, Real convs, Talk min, Enrolled (credited), Conversion %, Enrolled same day (owner), Probing, Pitch, Objection, High/mod intent |
| P64 | Required footnotes | The window (P3), the enrollment and credit definitions (P27, P28), the Zipteams coverage gap (P45), the team-mapping rule and the number of callers in more than one group (P11), the warm-lead flag (P29c), the validation summary, and the parameter version. |
| P65 | Weekday | Compute it from the date; never write it from memory. |
| P66 | Repo changes | None during a run. Data goes to the git-ignored `data/`. Nothing is committed or pushed without the owner's approval. The run itself is one command: `python -m analytics.team_performance {TARGET_DATE}`. |

## 10. Validation checklist (a blocking gate: every check runs before rendering, and one failure means no PDF and a message naming it; full log in `data/report_{TARGET_DATE}/validation.txt`)

1. `python -m integrations.leadsquared check` passed and the three env vars were set.
2. The S1 call count is printed and equals the sum of team dials and inbound calls, including Unassigned and Not a user.
3. Zipteams notes attributed / total is printed, and dropped notes are < 5%.
4. Every number in the verdict appears in the scorecard.
5. The PDF has 1 or 2 pages.
6. The parameter version is printed in the Method section.
7. No duplicate call or Zipteams activity IDs.
8. Every call and Zipteams note falls inside the target IST day.
9. Calls from callers who are not LeadSquared users stay under 5%.
10. Each enrollment is counted once, and every credited caller had an answered call with that lead on the target day.
11. Every rate is within 0–100 and no call has a negative duration.
12. Totals reconcile: calls = team dials + inbound; credited = sum of team credited = sum of their callers; Zipteams attributed + dropped = total.
13. A seeded sample of 10 credited enrollments is re-read from LeadSquared stage history and confirmed as first-ever "Course Enrolled" in the window.
