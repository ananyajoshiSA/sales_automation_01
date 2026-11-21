# Grading brief — daily bootcamp collection call list

Give this brief to each reader who grades open November bootcamp collection leads for `analytics/collection_daily`
(the morning call list and the evening update). Readers are Claude itself reading each lead's timeline: no model API,
no keyword rules. Statuses agreed with the user on 17 Nov 2026; they replace hot / warm / cold for the daily reports
only (`docs/collection_grading.md` still governs the Wednesday and Friday reports until those are redesigned).

Each lead paid a booking fee for a bootcamp, or (leads marked "(Community)", added 21 Nov 2026) a booking fee of ₹999–₹3,000 for a community programme batch; grade both the same way. The caller is collecting the balance: one shot, card EMI, a loan through a
finance partner (Bajaj, Eduvanz, Shopse, Credence, Propelld, Fibe, paper finance) or instalments.

The timeline (`<run_dir>/tl/<lead_id>.txt`) carries every call with its Salesa transcript where one matched, every
LeadSquared comment (stage changes, disposition notes with call-back times, Zipteams and other notes), every Note from
the Notes tab and the lead's comment fields. Its header gives the allocation time, the 48-hour deadline, the call facts
including `real talks (answered 120s+)`, and the saved call-backs with whether each was kept. Never use the pool
sheet's comments.

## `status` — exactly one of

| status | when |
|---|---|
| `notconnected` | no real talk (header says `real talks (answered 120s+) 0`) and no short pick-up asking for a call-back |
| `callback` | no real talk; a short pick-up (under 2 min) where the lead asked to be called back |
| `connected` | a real talk, but payment mode and timeline not discussed yet, and no objection |
| `hot` | payment mode **and** timeline discussed (a date or week to pay; a loan with documents shared or "will share the documents") — the lead is willing and plans to pay |
| `counsel` | the lead raised an objection (price, value, time, family, doubt about the course, wants to defer or cancel) that the caller has not resolved yet. An unresolved objection wins over `hot` |
| `cold` | refused, wants a refund or cancellation, or has not picked up for 7+ days despite calls after the last talk |

Hard rule: with `real talks 0` the status is `notconnected`, `callback` or (with clear evidence, such as a written
refusal in a Note) `cold`. With one or more real talks it is `connected`, `hot`, `counsel` or `cold`.

Grade the lead, not our calling: our missed or failed dials do not lower a grade. When a Note or comment records what
the lead said but no answered call is near it and the channel is not stated, take it into account and say so in
`last_talk` ("Note 14 Nov: no funds — channel not stated").

## Other fields

- `last_talk` — ≤ 20 words: date (IST) and what the last real talk or Note was about. "—" if none.
- `payment` — ≤ 10 words: mode and timeline, e.g. "Loan (Bajaj) · docs by Thu 19 Nov", "Card EMI · Sat 21 Nov",
  "One shot · salary 1 Dec", "Undecided". "—" if not discussed.
- `objection` — ≤ 12 words, only for `counsel` (and for `cold` when the lead refused): what the lead objects to.
- `followup_at` — "YYYY-MM-DD HH:MM" (IST) of the next call agreed with the lead in a transcript or Note, if it is
  after the cut-off; a call-back saved in LeadSquared is already in the header, so add it here only when the talk agreed
  a different time. Leave it out otherwise. A day without a time: use 11:00.
- `waiting_until` — "YYYY-MM-DD" only when the lead asked us to wait until a named date or event and the caller agreed
  (salary date, exam, loan decision). The lead is not called before it unless it calls us. Leave it out otherwise.
- `iit_pitch` — ≤ 25 words, always present: whether the IIT add-on (the IIT programme offered with the bootcamp) was
  pitched to the lead, when, and what was said about it: the lead's interest, price or EMI talk, a decision ("Pitched 16
  Nov: interested, will add to card EMI"; "Pitched 17 Nov: declined, budget"; "Asked about it 15 Nov, not pitched yet").
  Write "Not pitched" when no call, transcript, comment or Note mentions it.
- `next` — ≤ 15 words: the next step for the caller ("Call Wed 11:00 to collect Bajaj documents").

## Wording
Transcripts have no speaker labels: never write "the lead said" or "the caller said"; write what the call was about.
Ignore garbled machine text. No names or personal details (addresses, ID numbers, family, health) in any field. Never
he/she/his/her for the lead: "the lead".

## Output
One JSON file per reader, `{"<lead_id>": {"status": ..., "last_talk": ..., "payment": ..., ...}}`, an entry for every
lead given. Reply with a 3-line summary only. The run's `judgements.json` keeps every grade by date:
`{"<lead_id>": {"YYYY-MM-DD": {...}}}`, the date being the day the grade was made.
