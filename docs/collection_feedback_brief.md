# Feedback brief — one card per caller in the daily collection PDF

Give this brief to each reader who writes caller feedback for `analytics/collection_daily` (the team PDF for the AD and
team leaders). Readers are Claude itself reading each caller's day file: no model API, no keyword rules.

Each file (`<run_dir>/feedback/in/<Caller>.txt`) holds one caller's day on the November bootcamp collection leads: the
day's numbers, every call (with its Salesa transcript, cut to 2,500 characters), disposition, stage change and Note on
their leads that day, and the top of their list for the next day. Only calls to collection leads are in it.

Write, for each caller:

- `went_well` — ≤ 25 words, concrete and with names: a payment or loan step done, a strong counselling call, every
  follow-up kept, a lead's call returned fast. "None" only when nothing was done that day.
- `fix` — ≤ 35 words, concrete and with names and times: a follow-up not kept, a lead's call not returned, a 48-hour lead
  not reached, a long talk that ended with no payment mode and date, a price or promise outside policy, a lead dialled
  many times within minutes, a refund or cancel question not passed to the TL. Write what happened, not advice.
- `today` — exactly three short items (≤ 8 words each), each a named lead with a time or a concrete action from the
  next-day list or the day's calls ("Ravi 11:00: collect bank statement", "Return Meena's call first",
  "Payment mode and date on every talk"). At least two must name a lead.

Rules: no lead phone numbers; first names or the name as written are fine (the PDF is internal). Never he/she/his/her
for a lead or caller: use the name or "the lead". Transcripts have no speaker labels: say what the call was about, not
who said what. Use the day's numbers line as given; do not recount.

Output: one JSON file `{"<Caller name exactly as in the CALLER line>": {"went_well", "fix", "today": [3]}}` for
every caller in the batch. Reply with one line only.
