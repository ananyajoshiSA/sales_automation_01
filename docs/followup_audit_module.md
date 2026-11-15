# followup-audit-module (Follow-up call audit)

Reference name: **followup-audit-module**. Code: `analytics/followup_audit_module/` (Python needs underscores), run as
`python -m analytics.followup_audit_module`. Use this name when asking for changes to this audit. It is built on
[discovery-audit-module](discovery_audit_module.md) and reuses its finding, transcript, checking and PDF code.

Every morning, for the day before: every lead (all teams) whose LeadSquared stage changed to "Follow Up For Closure",
with its follow-up calls **up to the moment of the move** read together by Claude against
`analytics/discovery_audit_module/prompts/final_prompt_oct_2026.txt`, unchanged. The follow-up window is explained to
the reader in the prompt's own CUSTOM INSTRUCTIONS slot (written per lead by code). One reading gives the 16-column
sheet row and a 7-section feedback PDF.

Lever: conversation quality and close rate. Readers are team leaders: coach callers on compliance and closing gaps, and
ask about leads moved to closure without a conversation.

## Run

```bash
python -m analytics.followup_audit_module fetch 2026-11-14          # leads, windows, calls, transcripts, reading packs
# Claude (or its helper readers) reads each pack: data/followup_audit/<run>/reading/INSTRUCTIONS.md
python -m analytics.followup_audit_module check 2026-11-14 data/followup_audit/2026-11-14/reading/results/<key>.json
python -m analytics.followup_audit_module review prepare 2026-11-14  # second reading of compliance: review/in_NN.json -> out_NN.json
python -m analytics.followup_audit_module review apply 2026-11-14
python -m analytics.followup_audit_module status 2026-11-14
python -m analytics.followup_audit_module build 2026-11-14 --discovery data/discovery_audit   # workbook, PDFs, ZIP, CSV
```

On demand (the old way of pasting numbers): put the numbers in a file, one per line, then
`python -m analytics.followup_audit_module fetch-numbers numbers.txt`. Each lead's latest move to the stage is used,
the run is `numbers_<today>`, and the same check / review / build steps follow.

## Which calls

- **Window end:** the last move to Follow Up For Closure on the day. A call in progress at that moment counts; calls
  after it are never read.
- **Window start:** the latest of (1) the last move to Discovery Call Done that had an answered call of 30+ s in the
  2 hours before it (a stage set without a conversation, such as the bulk update of 13 Oct 2026, does not count);
  (2) the lead's previous move into Follow Up For Closure, so a lead that left and came back is judged on the calls
  since; (3) 30 days before the move. Never further back.
- **Scored:** every answered call of 30+ s in the window, oldest first, one row per lead. Unanswered and shorter calls
  are counted, not shown.
- **Background, not scored:** the calls on the day of that Discovery Call Done move, up to the move (the discovery call),
  so the reader knows the lead's profile and pain. The discovery audit scores that call; it is not scored twice.
- **Scored only with a real conversation:** at least one answered call of 2+ minutes (the shared definition). Claude
  marks voicemail, IVR, recorded messages and one-sided calls as `not_a_conversation`; they never count. A lead without
  one goes on the "Moved without a conversation" tab with its last real call and its first one after the move.

## How leads are found

LeadSquared has no company-wide stage-change list, so the job reads the stage history of every lead now in Follow Up
For Closure and edited since the day (about 1,000), plus leads in a later stage that had a call on the day or the day
before, then each moved lead's own calls (both directions, up to 500 each way). LeadSquared is only read, through the
admission gate. Avoid the Elite job times (07:15, 13:45, 16:45, 21:20 IST) and run after the 10:27 discovery run.

## Rules

- Transcripts: Salesa, by each call's own number plus the lead's Phone and Mobile, matched with `match_to_calls`. A lead
  waits ("Needs a Check", retried the next two mornings, 3 tries in all) only when a 2+ minute call from the last two
  days before the move has no recording, or none of its real calls has one. An older call still without a recording is
  read around and named in the row's note.
- Credit: the callers on the call records, in order. Stage changes are often "System" or an automation and never credit
  anyone; a call with no caller is "Unverified".
- Scores are computed by code from the reader's five subscores, as in the discovery audit: H from 6.5 (the prompt's
  relaxed rule), a strict column and the PDF's score out of 10 from 7.0.
- Every quote must match the transcripts word for word (`check`); every compliance decision gets a second reading,
  which ignores the background section.
- Discovery audit column: that audit's H/M/L for the lead (latest on or before the move), read from `--discovery`
  (default `data/discovery_audit`; on another machine load that team's Drive save into a spare folder first).
- 7-day outcome: each fetch checks the leads audited 7 days earlier for a first-time Course Enrolled since their move
  and lists them by H/M/L.
- Lead details stay in `data/followup_audit/` (git-ignored); the per-caller trend history is
  `data/followup_audit/history.jsonl`. Reports go to `/mnt/project-files/reports/followup_audit/<run>/`.
