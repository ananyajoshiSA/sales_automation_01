# Daily bootcamp collection reports — runbook

Agreed with the user on 17 Nov 2026. Scope: November bootcamp collection leads of both collection teams (Mayur's Elite
Changemakers: AD Sahil → TLs Sonal and Ayush, AD Raunak → TLs Anjali and Prawal; Anmol's Puja Malik: TLs Nancy,
Aditya, Shivam). Community leads come later. The Wednesday check and Friday review keep their current format until
they are redesigned.

**Lever:** speed to lead and returning leads' calls. **Measure:** one substantial conversation (answered, 2 min+) within
48 hours of allocation, target 90%+; pick-up within 48 hours is tracked next to it. Allocation is when the lead reaches
the caller's bucket (the last LeadSquared owner change to that caller); a lead allocated on Monday must have its talk
by 20:00 IST on Wednesday. Baseline from 13 Nov: talk within 48 h 78–83%, pick-up 89–94%.

## Two reports a day (Monday to Sunday)

| Report | When | For | What |
|---|---|---|---|
| Morning call list | before calling starts | each caller, TLs | one sheet per caller: every open lead in the order below, with what was discussed, payment mode and timeline, objection, the 48-hour deadline, in one Lead details cell, and an IIT Pitch column (pitched or not, and what was said); nothing to fill in (LeadSquared records every attempt) |
| End-of-day update | end of the calling day (20:00 IST) | TLs, ADs | at the top: what was missed (follow-ups not kept, 48-hour deadlines passed, leads' calls not returned) and each caller's score; then what was achieved and the next steps. No separate "yesterday" review in the morning list |

Order of the morning list (each lead once, in its first group): 1 follow-ups due today and carried over (a saved
follow-up not kept, the lead's own call not returned); 2 the 48-hour clock (no real talk yet, overdue first); 3 Hot;
4 To be counselled; 5 Call-back requested; 6 Connected – payment not discussed; 7 Cold (WhatsApp first); last, leads
that asked to wait until a date. No per-lead scripts or guidance (the user's call).

Statuses (`docs/collection_daily_brief.md`): Hot = payment mode and timeline discussed, including loan documents
shared or promised; To be counselled = an objection raised, counselled by the same caller (leads are not moved).

Follow-ups come from the call-back time saved in LeadSquared after an answered call and from times agreed on calls or
in Notes. A follow-up is kept when the lead is dialled from 15 minutes before to 2 hours after it.

## Steps (read-only; no LeadSquared writes)

```bash
D=data/coll_daily/$(date +%F)
```

1. Fetch each November bootcamp weekend into its own folder (`w1`, `w2`, ...) and the Notes:

```bash
PYTHONPATH=. .venv/bin/python scripts/fetch_bootcamp_collections.py 2026-11 2026-11 $D/w3 2026-11-14
```

```bash
PYTHONPATH=. .venv/bin/python scripts/fetch_lead_histories.py $D/w3/ids.json $D/w3/hist.jsonl
```

```bash
PYTHONPATH=. .venv/bin/python scripts/fetch_lead_notes.py 2026-10-21 $D/notes.jsonl
```

2. Comment fields, one run of transcripts and the timelines. Salesa writes a transcript some hours after the call, so
   every number with a real talk that no saved transcript covers is read again (unless already read after the data);
   run `prepare` again a minute later while it reports numbers still to fetch (`--since` limits timelines to leads with new activity,
   including new comments and Notes, after the last grading):

```bash
.venv/bin/python -m analytics.collection_daily prepare $D
```

3. Grade every lead in `$D/tl/index.json` with readers given `docs/collection_daily_brief.md`, each writing
   `$D/grade/out<n>.json`, then fold them into `$D/judgements.json` (`{lead_id: {date: grade}}`; a grade that does not
   fit the lead's calls stops the merge). Earlier grades carry forward for leads with no new activity.

```bash
.venv/bin/python -m analytics.collection_daily merge $D 2026-11-17
```

Every time in the reports is measured against when the data was read (the earliest weekend fetch), not the clock.

4. Build the morning list for the next day:

```bash
.venv/bin/python -m analytics.collection_daily morning $D 2026-11-18 exports/collection_daily
```

One workbook per team (Mayur's team, Anmol's team), each with Team summary (open leads per caller by group, and the
48-hour table per caller), How to use, and one sheet per caller. `plan_<day>.json` (both teams) is kept for the
end-of-day update. The list uses each lead's latest grade made on or before the day the data was read.

**A new grading field** (such as `iit_pitch`, added 18 Nov 2026): regrade every open lead once, running `prepare`
without `--since`; a grade made before the field existed shows "Not read yet".

**A refresh during the day:** copy the previous run's `judgements.json` into the new run folder, run `prepare` with
`--since` set to the previous read time so only leads with new activity get a timeline, grade those, `merge` under
today's date and build again.
