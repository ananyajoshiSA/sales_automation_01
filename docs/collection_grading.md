# Grading brief — collection leads (bootcamp and community checks and reviews)

Give this brief to each reader who grades leads for `analytics/collection_reports.py` (Wednesday check, Friday review)
or `analytics/community_reports.py`. Readers are Claude itself reading each lead's timeline: no model API, no keyword
rules. Rules agreed with the user up to 10 Nov 2026.

**What each timeline must carry (the user's rule, 12 Nov 2026):** every LeadSquared Note (the Notes tab, fetched with
`scripts/fetch_lead_notes.py`) and every LeadSquared comment — stage-change comments, disposition notes with their
call-back times, Zipteams / Salesa / Converse notes and the lead's comment fields (Last Remark, Follow-up comments) —
next to every call and its transcript. Do not add the Bootcamp Pool sheet's comment columns (L1, L2, audit): the
sheet is used for money and enrolled lists only.

Each lead paid a booking fee (₹3,000 for a bootcamp, ₹999–₹3,000 for a community programme). The collection team is
collecting the balance: one shot, card EMI, a loan through a finance partner (Bajaj, Eduvanz, Shopse, Credence, Propelld,
Fibe, paper finance) or instalments. You read each lead's timeline and grade **the lead** as of the cut-off.

The timeline header gives the number, bootcamp or batch, caller (TL, AD), assignment, LSQ status at the cut (OPEN,
ENROLLED or DROPPED), the call facts including `real talks (answered 120s+)` and, for a review, the earlier check's grade.
A marked line shows the earlier check's cut-off where there is one. Read the lead's **Notes** too (lines "NOTE (Notes
tab)", fetched with `scripts/fetch_lead_notes.py`): callers record unreachable or invalid numbers, WhatsApp attempts and
what the lead said there, and that effort counts as much as the dials.

## 1. OPEN leads — `status`, exactly one of
- `notreached` — **if and only if** the header says `real talks (answered 120s+) 0`.
- `hot` — a real talk and a **buying step**: payment date or mode agreed, link asked for, loan/KYC documents shared or
  in process, or the lead called us back to move forward.
- `warm` — a real talk, interested, with an open objection or no date.
- `cold` — "not now" or a deferral with no date, wants to cancel or a refund, or unanswered since the last talk with no
  buying step before it.

Grade the lead, not our calling: **our** missed or failed dials (CallFailure, missed call-backs) do not lower a grade.
The lead's own silence does: the report itself turns a hot or warm lead cold when it did not pick up for 7 days despite
2+ dials a day (`silent_since` in `analytics/collection_cycle.py`). Don't apply that rule yourself; record `waiting_until`
instead when it applies (below).

Fields: `why` (≤ 18 words, evidence with its date), `intent` ("Loan (Bajaj) · docs pending", "Card EMI · Sat 7 Nov",
"One shot · 10 Nov", "Instalments · first on 9 Nov", "Undecided: loan or card", "Loan rejected, other route", "—"),
`next` (who does what when; a promised call-back time if after the cut), `cb_promised` / `cb_kept` (call-backs promised
after a real talk that fell due before the cut / kept with a dial within 2 h; ignore "call-back now" defaults on DNP
forms), and:

- `waiting_until` — "YYYY-MM-DD" only when the lead asked us to wait for a named event or date (exam result, salary date,
  after Diwali) and we agreed; the lead is then not "silent" until that date. Otherwise leave it out.
- `risk_kind` and `risk` — **only** for one of these, else leave both out:
  `cancel or refund` (asked to cancel or for a refund), `won't continue` (said it will not go ahead),
  `later batch, no date` (wants a later batch with no date), `no payment route` (loan rejected and no card, no
  co-applicant, no funds). `risk` is ≤ 12 words with the date. Silence, DNP and doubts belong in `why`, not here.

## 2. DROPPED leads
`status` "dropped", `drop_reason` (≤ 15 words with the date) and `later_batch_offered` ("yes", "no" or "—").

## 3. Leads hot at the earlier check (reviews only), whatever their status now
`wed_hot_followup`: "yes" (dialled within 2 h of the call-back time promised by then), "no", "none agreed" or
"not due yet" (the time is after this cut-off); `wed_hot_outcome` (≤ 18 words: what happened since). A lead now
ENROLLED gets `status` "enrolled" and these two fields.

## Comments that cannot be traced
When a comment or Note records what the lead said or decided but no answered call is near it and it does not say how it
was learned (WhatsApp, email, a call on a date), say so in `why` ("26 Oct note: no funds — no call that day, channel not
stated"). Callers should write the date and the channel in such comments.

## Transcripts and wording
Transcripts have no speaker labels: never write "the lead said" or "the caller said"; write what the call was about.
Ignore garbled machine text. No personal details (addresses, ID numbers, family, health). Never he/she/his/her for the
lead: "the lead". When a talk has no transcript or notes, say so in `why`; a missing transcript is not evidence.

## Output
One JSON file per reader, `{"<lead_id>": {...}}`, an entry for every lead given; no names in any field. Reply with a
3-line summary only.
