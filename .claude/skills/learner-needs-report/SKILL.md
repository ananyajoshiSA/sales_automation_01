---
name: learner-needs-report
description: Learner-needs report for any bootcamp from 300–700 TimePay call transcripts — who is coming, what each group wants, the questions to answer on Day 1/2/3, wrong ideas, doubts and quotes — as a PDF for the bootcamp delivery team (trainers), with a short section for sales. Use when someone asks "learner needs for <bootcamp>", "what do <bootcamp> leads need", "trainer brief for <bootcamp>", "needs report for <campaign>", or "who is coming to <bootcamp>".
---

# Learner-needs report (per bootcamp)

Readers are Claude itself: every call is read by a subagent, never by keyword rules or a model API.
Guide with the reasons behind each rule: [docs/learner_needs.md](../../../docs/learner_needs.md).
Project instructions and ready prompts: [docs/learner_needs_prompts.md](../../../docs/learner_needs_prompts.md).
Code: `analytics/learner_needs/`. Bootcamp settings: `analytics/learner_needs/bootcamps/<KEY>.json`.
Run data (lead PII) lives in `data/learner_needs/<KEY>/` and is never committed.

## Ask only if missing
1. Which bootcamp and batch (e.g. "IP Law 28–30 Nov").
2. The session scripts (Day 1–3 Google Doc links or the month's folder doc). If none exist, say so and
   organise the report by learner group instead of by day.

## Steps
1. **Config.** Open `bootcamps/<KEY>.json`; for a new bootcamp copy the closest one. Fill
   `campaign_ids` (TimePay main discovery campaign of that batch: `TimePayClient().campaigns()` or the
   TimePay `list_campaigns` tool, read only), `calls_from`, `sample_size` (300–700: 500 for a paid ₹10
   bootcamp, 700 for a big free one, all eligible if fewer), `growthx.leadtype/from/to` so calls are spread
   over landing pages, and `scripts` (`{"day1": "<doc id>", ...}`).
2. **Sample.** `python -m analytics.learner_needs sample <KEY>` — longest 60s+ call per lead, spread over
   funnels (at least 8 each), transcripts fetched, written as `chunk_XX.txt` of about 130 calls.
   Read-only on TimePay. Report the numbers it prints.
3. **Read.** One general-purpose subagent per chunk, all in one message, in the background. Prompt:
   "First read analytics/learner_needs/reader_brief.md and follow it exactly. Bootcamp: <name, dates,
   price, who it is for, what work it leads to>. Reuse these segment names where they fit: <groups from
   the config>. Input: data/learner_needs/<KEY>/chunk_XX.txt Output: data/learner_needs/<KEY>/chunk_XX.tsv".
   `python -m analytics.learner_needs status <KEY>` shows what is left.
4. **Groups.** `python -m analytics.learner_needs aggregate <KEY>`. It exits 1 and lists every unmapped
   segment label: add each to the config's `groups` (label → one standard group; short or unclear calls →
   `Not enough said`; wrong audience gets its own group). Re-run until it exits 0. Keep 6–10 groups.
5. **Script outline.** One subagent reads the Day 1–3 docs (Google Drive `read_file_content`) and writes
   `data/learner_needs/<KEY>/script_outline.md`: each day's segments in order, stories, objection parts,
   where the paid pitch is. No links or passwords.
6. **Themes.** From the readers' replies draft `data/learner_needs/<KEY>/codebook.json`: 6–12 themes per
   kind (`hope`, `doubt`, `misconception`, `question`, `resonated`), each `{"theme", "means"}`. Then one
   subagent per ~300 rows: "Read analytics/learner_needs/themes_brief.md and follow it. Codebook: …
   Inputs: chunk_01.tsv … Output: data/learner_needs/<KEY>/themes_<n>.tsv". `aggregate` reads every
   `themes*.tsv`. For any OTHER that came up 3+ times, add a theme and recode those rows; re-run step 4.
7. **Findings.** Read `summary.json`, the TSVs and the outline, then write
   `data/learner_needs/<KEY>/findings.json` (shape below). Put each question on the day and script segment
   where it should be answered. Every item that has a count names its `theme`; never type a count.
8. **PDF.** `python -m analytics.learner_needs pdf <KEY>` → `exports/Learner_needs_<KEY>.pdf` plus
   `exports/Learner_needs_<KEY>_leads.csv`.
9. **Checks before sharing (all required):**
   - `aggregate` exit 0; total lead lines = chunk calls (`status`); every chunk has a TSV.
   - Hand-check 20 random TSV lines against their calls (`chunk_XX.txt`, same LEAD number) and 20
     `themes*.tsv` lines against their TSV rows. Report how many were right; fix and re-run if any were wrong.
   - Open the PDF (render page 1–2 to PNG and look): no empty sections, no "-" counts where a theme was named.
   - No phone numbers or surnames in findings, quotes or the PDF.

## findings.json
```json
{"fresh": "Calls 1 Nov–26 Nov 2026",
 "headline": ["3–5 actions for the trainer, plain words"],
 "groups": {"<group>": {"who": "...", "wants": "..."}},
 "days": [{"day": "Day 1", "segment": "<script segment>", "items": [{"ask": "...", "answer": "...", "theme": "<question theme>"}]}],
 "misconceptions": [{"belief": "...", "correct": "...", "theme": "..."}],
 "doubts": [{"doubt": "...", "answer": "...", "theme": "..."}],
 "hopes": [{"hope": "...", "note": "...", "theme": "..."}],
 "resonated": [{"what": "...", "note": "...", "theme": "..."}],
 "per_group": [{"group": "...", "points": ["..."]}],
 "quotes": [{"q": "...", "who": "career-break mother, 6-year gap"}],
 "sales": ["objections and what answers them; no prices unless the script has them"]}
```

## Rules
- About learners, not the calling agent: no prompt fixes or "problems on our side" in the trainer PDF.
- Plain words for trainers; numbers with percentages; IST dates; say how fresh the calls are.
- Never invent numbers. Counts come from `themes.tsv`; reader tallies are only for drafting the codebook.
- TimePay is read-only here. Lead data stays in `data/` and `exports/`.
