# discovery-audit-module (Discovery call audit)

Reference name: **discovery-audit-module**. Code: `analytics/discovery_audit_module/` (Python needs underscores), run as
`python -m analytics.discovery_audit_module`. Use this name when asking for changes to this audit.

Every morning, for the day before: every lead (all teams) whose LeadSquared stage changed to
"Discovery Call Done", with **all** of that day's calls read together by Claude against
`analytics/discovery_audit_module/prompts/final_prompt_oct_2026.txt` (relaxed conversion score, IIT check, red flags).
One reading gives both the 16-column sheet row and a 7-section feedback PDF, so they never disagree.

Lever: conversation quality and calling the right leads first (closer briefing on each High lead the next morning).

## Run

```bash
python -m analytics.discovery_audit_module fetch 2026-11-13      # leads, calls, transcripts, reading packs
# Claude (or its helper readers) reads each pack: data/discovery_audit/<day>/reading/INSTRUCTIONS.md
python -m analytics.discovery_audit_module check 2026-11-13 data/discovery_audit/2026-11-13/reading/results/<key>.json
python -m analytics.discovery_audit_module review prepare 2026-11-13   # second reading of compliance: review/in_NN.json -> out_NN.json (14 leads per reviewer)
python -m analytics.discovery_audit_module review apply 2026-11-13
python -m analytics.discovery_audit_module status 2026-11-13
python -m analytics.discovery_audit_module build 2026-11-13      # workbook, PDFs, ZIP, CSV -> reports folder
```

## How leads are found

LeadSquared has no company-wide stage-change list (the bulk read of event 3002 returns no rows) and about 5,000
leads sit in the stage, so the job reads the day's calls and the day before (bulk, both directions), lists leads now
in Discovery Call Done or a later pipeline stage edited since the day, and reads the stage history of leads in both.
A lead counts when it moved into the stage during the IST day. All its calls that day are audited; if it had no
conversation that day, the previous day's calls are audited and the row says so.

## Rules

- LeadSquared is only read, through the admission gate. Avoid the Elite job times (07:15, 13:45, 16:45, 21:20 IST).
- Transcripts: Salesa, by each call's own number plus the lead's Phone and Mobile, in runs of at most 90 numbers.
  Matched to LeadSquared calls with `match_to_calls`.
- Credit: the caller on the call records. Stage changes are usually recorded as "System" and are never used to
  credit anyone; a call with no caller is "Unverified".
- Scores are computed by code from the reader's five subscores: the sheet's H/M/L uses the prompt's relaxed rule;
  a strict column and the PDF's score out of 10 use the strict objections rule (H/HOT from 7.0).
- Every quote must match the transcripts word for word (`check`); every compliance decision gets a second reading.
- Statuses: ANALYZED, PENDING_ANALYSIS, ANALYSIS_FAILED, TRANSCRIPT_NOT_FOUND (carried into the next two runs),
  NOT_AUDITABLE (voicemail, screening or calls under 30 s only). Every lead appears in the workbook, audited or not.
- Lead details stay in `data/discovery_audit/` (git-ignored); the per-caller trend history is
  `data/discovery_audit/history.jsonl`.
- The PDF layout follows the original chat's 7-section report; its exact PDF prompt is not in the repo yet, so the
  HOT/WARM/COLD cut-offs are provisional.
