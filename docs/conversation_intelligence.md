# Conversation intelligence and team analytics

An additive module (`analytics/convintel/`) that tracks every LeadSquared call to its transcript and analysis,
has Claude read each transcript for words and phrases, intent, quality, objections, commitments and coaching (no
API and no keyword rules: Claude does all of it), flags calls that may
not be real conversations, and rolls everything up by caller, team and organisation. It changes no existing report,
dashboard number, shared definition or Worker task. Lever (GOAL.md): conversation quality and calling the right
leads first, seen in enrolments per real call.

## What it does, step by step

| Step | Command | What happens |
|---|---|---|
| Inventory | `python -m analytics.convintel inventory FROM TO` | Every outbound and inbound call of each IST day goes into the registry with its class, caller, kind (person, shared login, bot, not a user) and team when inventoried. LeadSquared's count per day is stored for reconciliation. |
| Transcript search | `python -m analytics.convintel fetch-transcripts` | One run searches the lead numbers that are due (never-searched first, newest call first, then rechecks) and matches each recording to one call. |
| Claude reading | `python -m analytics.convintel read prepare [--per-pack 6]` | Hands every found transcript that still needs a reading to a new round folder under `data/convintel/reading/` (instructions plus packs of a few calls). |
| | (a Claude Code session) | Claude reads each pack and writes one reading per call to `results/<call_id>.json`, checking each with `read check FILE`; a passing check marks the reading as finished (`results/<call_id>.ok`). |
| | `python -m analytics.convintel read collect [ROUND]` | Stores every finished reading of a call still handed out, from whichever round's folder holds it; drafts, half-written files and files changed since their check wait. `read prepare` collects first. |
| Reconciliation | `python -m analytics.convintel reconcile FROM TO` | Proves the registry accounts for every call (below). |
| Report | `python -m analytics.convintel report FROM TO --key 7d` | Snapshot JSON, `flagged_calls.csv` and an offline `index.html` under `data/convintel/reports/<key>/`; the period's opportunities, Zipteams disagreements, coaching, team metrics, lead journeys and accountability rows are stored in the registry tables. |
| Dashboard | `python -m analytics.convintel.d1push SNAPSHOT.json --push --yes` | Writes the snapshot to D1 for the dashboard's Conversation Intelligence page. A remote write: only on the user's go-ahead. |
| All of it | `python -m analytics.convintel run FROM TO [--loop --every 300]` | One cycle (or a loop): re-reads days still inside LeadSquared's 3-day edit margin, searches transcripts, collects Claude's readings, hands new transcripts to a reading round, reconciles. A Claude Code session reads the rounds. |
| Status | `python -m analytics.convintel status [FROM TO]` | Counts per status, coverage and the last runs. |

The registry is one SQLite file, `data/convintel/registry.sqlite` (git-ignored: it holds lead numbers), with the
tables requirement 11 names: `transcript_coverage_registry` (one row per call, never deleted),
`conversation_analysis_jobs`, `conversation_analysis_results` (kept per layer version),
`conversation_quality_findings`, `caller_coaching_insights`, `team_conversation_aggregates`,
`lead_accountability_findings`, `revenue_opportunity_findings`, plus the search queue (`number_lookups`),
`orphan_recordings` and `processing_runs` (run log with checkpoints). Transcripts are saved as text files under
`data/convintel/transcripts/` with their SHA-256 in the registry.

## Call classes (this module only)

A **real call** here is answered with at least 180 seconds of talk: 179 s is `SHORT_CALL`, 180 s and 181 s are
`REAL_CALL`, an unanswered or failed call is `NOT_CONNECTED`, and an answered call whose duration is missing,
zero, unreadable or over 4 hours is `UNKNOWN` (never a real call). The project's 2-minute "real conversation"
(CLAUDE.md, the calling report, the main dashboard) is unchanged; this module always labels its rule
"real calls (3+ min)".

## Statuses

Every call has exactly one status, derived from its transcript search and its analysis jobs:

| Status | Meaning |
|---|---|
| `ANALYZED` | Claude's reading of the transcript is stored and validated at the current version. |
| `PENDING_ANALYSIS` | Not searched yet, a search failed and is retrying, or the transcript is found and waiting for Claude's reading. |
| `ANALYSIS_IN_PROGRESS` | The transcript is handed out in a reading round and Claude has not finished it (handed out again after 25 h). |
| `ANALYSIS_INCOMPLETE` | Claude's reading lacks a required part; the reason names it, and it is read again. |
| `ANALYSIS_FAILED` | The reading could not be used (not valid JSON, failed validation); a retry is scheduled (5 min, 30 min, 2 h, 12 h, then daily). |
| `TRANSCRIPT_NOT_FOUND` | Searched, and no recording matches, or the recording has no text yet, or the call has no number. Rechecked 2 h, 24 h, 72 h and 7 days after the call; `retry` queues them all again. |
| `NO_TRANSCRIPT_EXPECTED` | Not connected, so nothing was recorded. Kept in the inventory, left out of the coverage denominator. |

Coverage = analysed ÷ calls expected to have a transcript (every connected call, plus any other call whose
transcript turned up). Batching or a run's request budget never removes a call: it stays queued.

## Matching a transcript to its call

The transcript API returns every recording ever made on a number, so one search settles all of that number's
calls. A recording belongs to the call on the same number that started within 10 minutes of it (or exactly
5 h 30 min off when the durations agree, because some API clocks are shifted; `match_gap`). Each recording
goes to one call and each call gets one recording, closest first. The call's time is always LeadSquared's.
Recordings in an inventoried period that match no call are kept as orphans for reconciliation.

## Transcript API limits (checked)

- At most 10 numbers per request (the API rejects more).
- The repo's own ceiling of 9 requests per run (`TranscriptClient`, CLAUDE.md). Not raised here.
- About 10 requests a minute per key across all runs (HTTP 429 above that, seen 8 Oct 2026). Requests are spaced
  7 s apart, a 429 ends the run and the rest stays queued, and `run` waits 60 s between runs.

One run therefore settles up to 90 numbers. Continuous processing is repeated runs; the queue never drops work.

## How transcripts are analysed: Claude only

Every transcript is read by Claude itself inside a Claude Code session working in this repository. No API key is
used and no model API is called (user, 9 Oct 2026: "All transcripts are to be done via claude models only").

- `read prepare` writes a round folder: `INSTRUCTIONS.md` (how to read a call, from `prompt.py`, and the exact JSON
  shape, `schema.SEMANTIC_SCHEMA`) and packs of a few calls. A pack holds each call's id, its IST start, direction,
  talk time and call class, and the transcript; never the lead's number, lead id or anyone's name.
- Claude reads the whole call and writes language; the word and phrase analysis (`word_analysis`: the phrases that
  carry meaning, each copied word for word with its speaker and kind, such as buying words, hesitation, commitment,
  payment intent, urgency, uncertainty, persuasive or ineffective wording, plus phrases and objections that repeat,
  judged in context rather than matched against a list); inferred speaker turns, intent and readiness, tone from the words
  (vocal tone is "not available" from text), nine quality scores, objections and how they were handled, buying
  signals and whether the caller acted on them, commitments, unanswered questions, coaching, outcome, an integrity
  verdict and findings.
- `read check` and `read collect` run `validate.py`: every excerpt must be found word for word in the transcript or
  it is dropped and counted (a phrase without its words is removed); a repeat count is replaced by the transcript's
  own count; out-of-range numbers are clamped and listed; an overall quality score on a call where no skill could be
  scored is left blank; a missing part keeps the call incomplete.
- On 9 Oct the sample's readings were made by one Claude instance per pack and then checked and corrected by a
  second, independent Claude instance before they were collected.

The rule-based keyword layer (word lists and regular expressions) was removed on 9 Oct 2026 (user: "Keyword
analysis is also to be done via claude models only"). Nothing in this module analyses transcript text by rule; the
code only counts words, matches Claude's excerpts and counts its repeated phrases to check the reading. The older
daily calling report (`analytics/team_performance.py`, `analytics/call_integrity.py`) keeps its own transcript
checks unchanged.

Transcripts are machine transcriptions in English with no speaker labels and no timestamps, so speaker shares
are inferred and evidence has a character offset, not a time.

## Calls that may not be real conversations

Every call gets its flags with reasons (`integrity.py`); a flag means "needs review", never proof:

- **Under 3 minutes**: answered but under 180 s (not a real call by this module's rule).
- **Empty transcript**: a recording exists but its transcript is empty (any length; "suspect" for a call logged
  as 3+ minutes).
- **Suspect** (logged as a real call, evidence says otherwise): from Claude's reading, not a real conversation,
  almost no content, very little talk for the time, IVR or voicemail, looping, one-sided or not a sales
  conversation; from the call log, a
  recording much shorter than LeadSquared's logged time, the caller on another answered call at the same time, or a
  3+ minute call with no recording after every recheck.
- **Pattern**: 3+ real calls by one caller to one lead in a day; a caller whose real calls bunch just over
  3 minutes far more than the account as a whole.

They are rolled up per caller (share flagged, flags by type, the call ids) and listed call by call in
`flagged_calls.csv`: call id, lead id, direction, caller, team, the caller's line (LeadSquared's DisplayNumber,
a company number shared across callers), the lead's number (the number dialled on an outbound call), IST start,
duration, class, transcript state and words, flags and reasons (user, 9 Oct 2026). The file holds phone numbers,
so it stays under `data/`; the dashboard shows the same flags without numbers.

## Reconciliation

`reconcile` checks: every day was read from LeadSquared and the registry holds exactly the calls the latest read
returned (calls deleted or moved in LeadSquared are reported); every call has one status and they add up; every
found transcript is still on disk and unchanged; no recording is matched to two calls; every `ANALYZED` call has a
valid result for each layer; no claim is stuck; how much of the search queue is overdue. Coverage is called
100% only when every check passes and nothing is left unanalysed.

## Teams, accountability and revenue

- A call belongs to its caller and the caller's first LeadSquared group when inventoried (the same team rule as
  the calling report). Shared admin logins (Rinku Jhala, Admin) are never a person and bots are never callers:
  both stay in organisation totals. Some users' first group is a dialer group (Mcube Users, Acefone Users); they
  are shown under that name until the team rule changes for every report together.
- Team leader: `data/convintel/config.json` `{"team_leaders": {"Team": "Name"}}` if present, else the name in
  the team label ("Team Bootcamp +Anas" -> Anas), else "(not recorded)".
- Accountability reuses `analytics/accountability.py` ("Change log first": owner changes are credited from the
  owner-change log; Assigned By is kept as its own field). Pass its output folder with `--accountability`; its
  `audit.jsonl` is read too, so a team leader's correction keeps the CRM's original performer next to it.
- Follow-ups are judged only from calls inside the period, so for a past period a callback made after its last day
  is not seen (the snapshot says so).
- Opportunities (`revenue.py`): high intent is readiness 70+ (50+ for a weak follow-up); a payment link unpaid after
  24 h, a missed inbound call not returned within 2 h, a promised follow-up not made within 48 h, and 6+ dials
  with no conversation. A window that runs past the calls read is not judged.
- Revenue is not measurable while LeadSquared returns no payment records; enrolments are credited once per view
  (owner at enrolment, last answered caller) and the payment fields that do exist are listed without values Once payments
  arrive, check the amount field with `python -m analytics.convintel.revenue PAYMENTS.json` and set it as
  `payment_amount_field` in `data/convintel/config.json`; until then the field is only guessed from its name.

## Privacy

Transcripts, reading rounds, lead numbers and the registry stay under `data/` (git-ignored). The dashboard snapshot
carries no lead numbers and no transcript excerpts unless `--excerpts` is given. Claude's reading packs hold the
transcript, the duration, direction, class and start time, never the lead's number, id or name; `d1push` refuses a
snapshot in which a phone number appears. The dashboard shows Claude's word and phrase analysis only as counts by
kind of phrase, never the words.

Reading a transcript in a Claude Code session also puts its text in that session's conversation log on the machine
running it (under `~/.claude/`) and sends it to Claude through the user's Claude account, as any file Claude reads
does. Run reading rounds only on a machine and account cleared for customer call data.
