# Bootcamp collection reports — context and runbook

Context for anyone (or any Claude session) picking up the bootcamp collection work. No lead details here:
those live only in `data/` and `exports/` (git-ignored).

## The business

- A lead who pays the ₹3,000 booking fee **during a bootcamp** becomes a *collection lead*. The fee is
  non-refundable. Collection teams then collect the balance: one shot, card EMI, loan through a finance
  partner, or instalments.
- Bootcamps run over a weekend. Day 1 leads are assigned on Monday and Day 2 leads on Tuesday. The month a
  weekend belongs to follows the pool sheet (24–26 Oct bootcamps = November weekend 1).
- Target: **85%+ lead-wise recovery per month.**
- Two teams. Each lead is credited to the last team caller who held it.
  - **Mayur — Elite Changemakers.** AD Sahil Arora → TL Sonal Tripathi (Dipak Kumar Verma, Vedahi Rana,
    Shikha Chahar, Yamini Billore) and TL Ayush Srivastava (Ayush, Aisha Sami, Himanshi; moved from Raunak
    on 4 Nov 2026). AD Raunak Mandilwar → TL Anjali Srivastava (Devika Singh, Kanika Verma, …) and TL Prawal Singh.
  - **Anmol Gakhar — Puja Malik.** TLs Nancy Bajaj, Aditya Chugh and Shivam Sharma, by LeadSquared group.
- The **Bootcamp Pool sheet** ("Bootcamp Pool data – '2026 – '27", tab "Main MIS Teamwise Split – <month>")
  is kept centrally and is the source of prices and money collected. Since 4 Nov 2026 it is read directly:
  `integrations.gsheets` downloads it as .xlsx with `BOOTCAMP_POOL_SHEET_ID` (no screenshots needed). The MIS tab gives
  price, recovered, pending and dropped per bootcamp and side; the "Mastersheet" tab has one row per student, and
  MIS "recovered" = Mastersheet status Disbursed, Installments or One shot (checked: the counts match the MIS).
- LeadSquared is read-only for this work. No writes.

## The reports (finalised with the user)

One PDF per team. A **Wednesday check** (cut-off Wed 20:00) and a **Friday review**. A review made on
another day keeps the Friday format and takes that day's name ("Monday review").

- **Page 1, summary.**
  - Tiles: booking-fee leads, not called yet, hot leads, enrolled vs 85%, collected (pool MIS).
  - "Act on these first" box: uncalled leads by number, hot leads by caller, missed call-backs, callers
    below 70% within 12 h, dialler problems, DNP after 10+ dials, and the sheet vs LSQ gap count.
  - Status bar per bootcamp (bar length = leads).
  - Callers table, AD → TL → caller: leads, not called, within 12 h, hot, warm, cold,
    DNP/No real conversation, enrolled, dropped, call-backs kept, enrolled value (est.), still open.
  - Payment intent of hot and warm leads.
- **Friday only, page 2.**
  - Recovery by bootcamp vs 85%: leads (D1+D2), enrolled, recovery, dropped, open, collected (pool MIS),
    still open.
  - Wednesday's hot leads: called at the promised time? Status now, and what happened.
  - Drops (reason, later batch offered?) and leads at risk of dropping.
- **TL pages.** Every open lead of the month, by TL → caller, latest weekend first. Earlier weekends are
  listed in full, never summarised.
  - Columns: #, number (never the LSQ lead ID), lead, bootcamp · day, tries / days, status, why (from LSQ
    notes and Salesa transcripts), payment intent, call-backs kept, next step.
- **Definitions page.**
- **Last page: pool sheet vs LeadSquared gaps.**
  - Bootcamp counts that differ from the MIS.
  - With the sheet's enrolled list: each lead enrolled in one source but not the other, and sheet entries
    matched only by name (wrong number).
  - A lead paid per the sheet but open in LSQ is also flagged on its row.

Removed at the user's request (Nov 2026):
- median first-call columns and the "Called within 12 h" tile;
- "booking fees, all teams" and "median days to pay";
- the per-lead "1st call" column.

"Not reached" is called **DNP/No real conversation**.

## Rules

- **12 hours:** first dial within 12 working hours (10:00–20:00 IST, every day) of assignment. Assignment
  is the first LSQ owner change to a team member after the bootcamp. A team member's dial made before that
  owner change counts as on time (callers dial from the allocation sheet first).
- **Enrolled:** LSQ stage Course Enrolled or Collections done, or **Loan pending saved as "loan done" /
  "loan process done"** (the user's rule, 2 Nov 2026). Dropped: Not Interested, Invalid…, May buy later.
  LSQ decides the counts; gaps with the sheet are listed, not merged.
- **Grades** (read from notes and transcripts by Claude; checked against the call log; the readers' brief is
  [collection_grading.md](collection_grading.md)):
  - **Hot:** a buying step — payment mode or date given, link asked for, loan documents shared, or the
    lead called back.
  - **Warm:** interested, with an open objection or no date.
  - **Cold:** "not now", wants to cancel or a refund, or stopped answering.
  - **DNP/No real conversation:** no answered call of 2 min or more.
  - Grade the lead, not our calling: our missed or failed dials do not lower a grade. The lead's own silence does:
    a hot or warm lead that did not pick up for 7 days despite 2+ dials a day is cold, unless it asked us to wait for
    a date (exam result, salary date) (user, 10 Nov 2026; applied in code).
  - At risk of dropping: only cancel or refund, won't continue, later batch with no date, or no payment route left.
    Silence and DNP stay on the lead's row (user, 10 Nov 2026).
  - Transcripts have no speaker labels; never attribute words to the caller or the lead.
- **Money:** collected = pool MIS figure (label its date). LSQ holds no paid amounts for these leads.
  Per caller / lead: enrolled value (est.) = pool price for the day − ₹3,000; still open = the same for
  open leads.

## How to build one

```bash
# 1. Fetch the cycle (both teams; read-only, through the admission gate)
PYTHONPATH=. .venv/bin/python scripts/fetch_bootcamp_collections.py 2026-10 2026-10 data/<dir> 2026-10-24
# 1b. Lead Notes (the Notes tab): callers record unreachable numbers, WhatsApp attempts etc. there, not as activities
PYTHONPATH=. .venv/bin/python scripts/fetch_lead_notes.py <first bootcamp day − 3> data/<dir>/notes.jsonl data/<dir>/ids.json
# 2. Transcripts + one timeline file per lead: every LSQ Note and comment (see collection_grading.md), never the pool sheet's comments that needs a grade (≤ 80 numbers per run; wait 60 s, run again for the rest)
# 3. Grade each lead for the Wednesday cut-off and the report cut-off (readers of ~15 leads each),
#    merge into data/<dir>/judgements_<team>.json keyed by lead id and dated by cut-off, and check the
#    grades against the call facts (enrolled / dropped / DNP must match)
# 4. Write data/<dir>/report_config_<team>.json: side, team, month, fee, target, wed_cut, weekends,
#    pool (as_of, per bootcamp: price [D1, D2], all_teams, recovered, recovered_amount, pending,
#    pending_amount, dropped; optional lists_as_of + enrolled_list [[name, phone], …])
# 5. Build (downloads the latest pool sheet first and refreshes the config's MIS figures; add --saved to skip that)
.venv/bin/python -m analytics.collection_reports wed|fri data/<dir>/report_config_<team>.json "YYYY-MM-DD HH:MM" exports/collection_reports/<name>.pdf
```

**Latest pool sheet on every build.** Step 5 downloads the Bootcamp pool sheet next to the config
(`bootcamp_pool.xlsx`), reads the month's "Main MIS Teamwise Split" tab and updates each config bootcamp's counts and
amounts (all_teams, recovered, pending, dropped and their amounts) plus `as_of`, then saves the config. A config
bootcamp is updated only when exactly one MIS row of the team's side, on the same date, carries its course; the
others keep their figures and are printed as notes. Prices and the enrolled lists are never overwritten (the MIS
once copied a wrong price). The header shows the MIS date and the download time. Code: `analytics/collections/pool_mis.py`.

Code: `analytics/collection_cycle.py` (cycle leads, assignment, 12-hour window, state at a cut-off) and
`analytics/collection_reports.py` (the PDF). Tests: `tests/test_collection_cycle.py` and
`tests/test_collection_reports.py`. Step 2 and the step 3 merge are still scratch scripts, not in the repo.

## Community collection check (5 Nov 2026)

Same format and grading as the bootcamp check, one PDF per team (`analytics/community_reports.py`), with the changes
agreed with the user:
- **Leads:** every row of the Community Webinar Collection Sheet booked in the chosen months, matched to LeadSquared
  by phone then email (`analytics/community_cycle.py`). Credited to the last collection caller of either team who held
  it after booking (so no lead counts twice); the sheet's owner only when no collection caller held it in LeadSquared.
- **Grouping:** booking month (latest first) and batch = community + group number from "Course paid for"
  ("Independent Director G88–89"; Week_1/2/3 of a group are one batch). Each lead shows its booking date, not D1/D2.
- **Money:** the sheet's own per lead, no estimates: collected = amount paid on leads the sheet marks Disbursed,
  One shot or Instalments; balance = the sheet's pending amount on leads open in LeadSquared.
- **Pages:** summary, the earlier month's performance by batch against 85% (with the latest month so far), drops and
  leads at risk, TL pages, definitions, and the community sheet vs LeadSquared gaps, checked lead by lead.
- **Suggested days:** a Tuesday check and a Friday review (48% of bookings land on Sunday, 19% on Thursday,
  Aug–Nov 2026); not yet confirmed.

```bash
python -m scripts.fetch_collections data/collections/<day> 2026-10                 # tagged leads + both sheets
# ids of the months' matched leads -> scripts/fetch_lead_histories.py ids.json data/collections/<day>/hist.jsonl
# transcripts, timelines and grading as for bootcamps (scratch); judgements keyed by lead id and cut-off date
python -m analytics.community_reports <config.json> "YYYY-MM-DD HH:MM" exports/collection_reports/<name>.pdf
```

Config: side, team, months, target, data_dir, groups_meta (a meta.json with `member_groups`), judgements,
lsq_read_at, sheet_read_at.

## Status (4 Nov 2026)

- **Delivered for November weekend 1 (24–26 Oct bootcamps):**
  - Mayur: Wednesday (28 Oct 20:00) and Friday (30 Oct 12:00).
  - Anmol: Monday review (2 Nov 12:45).
  - These PDFs predate the latest format changes and the loan-done rule. Rebuild them when asked.
- **Anmol, Global Academic Writers:** the sheet lists 62 enrolled, LSQ 56. Six paid leads are not updated
  in LSQ, and one sheet number is wrong (11 digits). Both now show on the gaps page.
- **Mayur, Wednesday check 4 Nov 2026 (both November weekends, 128 leads):** built from the live pool sheet;
  63 open leads graded.
- **Friday reviews 13 Nov 2026 (LeadSquared read 18:14 IST):** data in `data/fri13/`, compared with the Wednesday
  check of Thu 12 Nov 12:39; graded with every LSQ Note and comment and the lead comment fields; money and gaps from
  every pool sheet student row.
- **Wednesday checks for 11 Nov 2026, run Thu 12 Nov (LeadSquared read 12:39 IST, so the cut-off is that time):**
  data in `data/wed11/`; graded with lead Notes and the 10 Nov rules (silent week, narrow drop risk). The Friday review
  compares against this cut-off (`wed_cut` in `data/wed11/cfg_*.json`). No bootcamp after 31 Oct had a collection tag
  in LeadSquared or rows in the pool sheet's November tab yet.
- **Friday reviews 6 Nov 2026 (both teams, both November weekends):** LeadSquared read again into new folders
  (`data/fri_w1`, `data/fri_w2`) so Wednesday's copy stays; Wednesday's judgements copied in and Friday's added under
  the Friday date (open leads graded, dropped leads' reasons, Wednesday-hot follow-up). The transcript service refused
  almost every search on Friday (10 requests a minute, used by something else), so transcripts cover calls up to
  Wednesday; the header says so (`transcripts_note` in the config).
- **Community checks 5 Nov 2026 (October and November bookings):** Mayur 218 leads, Anmol 159; 188 open leads graded.
- **Anmol, Wednesday check 4 Nov 2026 (both November weekends, 204 leads):** 99 open leads graded. The MIS row for
  Anmol's AI for Business Growth (Senior AI) copies Global Academic Writers' day split and price; the Mastersheet
  prices all 6 students at ₹55,000 (Day 1), which the report uses.
- **Open:**
  - Per-lead money from the Mastersheet ("Money received") instead of price minus booking fee (est.), if the user wants it.
  - Delivery (email or other) and scheduled runs.
  - Moving the transcript and grading steps into the repo.
