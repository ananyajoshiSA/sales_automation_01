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

## The team PDF for the AD and team leaders (agreed 19 Nov 2026)

One PDF per team, built after the morning lists, from the same run: the previous day's review (scorecard, what the list
asked vs what happened, each caller's numbers, leads not called or not reached, missed opportunities, points for the
huddle), priority leads for today (payment promised, loans in progress, refund or cancel risk), the TL's checks by time,
the team overview, each caller's feedback card (went well, fix, their three for today), the 48-hour tracker, money
against the 85% target from the pool sheet MIS and the payment pipeline, objections and the IIT add-on, cold and
waiting leads, records to fix, and the day's routine.

1. Download the pool sheet into the run folder as `bootcamp_pool.xlsx` (`integrations.gsheets.download_xlsx`).
2. Write the caller day files and have readers write the feedback with `docs/collection_feedback_brief.md`
   (`analytics.collection_daily.review.digests`, outputs `<run_dir>/feedback/out<n>.json`).
3. Build, passing the previous day's plan and a one-line transcript note:

```bash
.venv/bin/python -m analytics.collection_daily.pdf $D 2026-11-19 exports/collection_daily/plan_2026-11-18.json exports/collection_daily "Salesa transcripts: …"
```

## Management report for Puja Malik and Anmol Gakhar (sample 25 Nov 2026)

Two pages, no lead lists: where the month stands against 85%, a scorecard per TL (red where off target), money waiting
on a decision, callers to look at, compliance, records to fix, and patterns for sales. Built from the same run as the
TL PDFs, plus the pool sheet downloaded a week earlier for the week-on-week line. Recovery is compared like with like:
November bootcamps held 21+ days ago against October's final figure (newer bootcamps are still being collected).
Compliance lists only calls checked against their transcript, from a small JSON file (`[{side, caller, date, what}]`).

```bash
.venv/bin/python -m analytics.collection_daily.management data/coll_daily/<day> <day> data/coll_daily/<day-7>/bootcamp_pool.xlsx exports/collection_daily --compliance <checked.json>
```

Thresholds, frequency, delivery and whether compliance stays in are still with the user.

## Community leads (added 21 Nov 2026)

The lists and PDFs also carry community collection leads, named "Lead (Community)". Put them in the run folder's
`community/` before `prepare`:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.fetch_collections $D/community 2026-10
```

Then write `$D/community/months.json` (booking months, e.g. `["2026-11"]`) and `$D/community/ids.json` (the matched
lead ids of those months, from `analytics.community_cycle.sheet_rows`), fetch their histories with
`scripts/fetch_lead_histories.py $D/community/ids.json $D/community/hist.jsonl`, and add `$D/community/ids.json` to
the Notes fetch. A community lead counts from its booking date; its money is the community sheet's own.

