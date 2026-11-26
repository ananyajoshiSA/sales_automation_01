# Learner-needs reports

**Lever:** conversation quality and show-up. Trainers who know who is in the room answer the questions that
block a purchase (fees, "is it a job", recordings, language) before the Day 2 pitch, and sales hears the
real objections. We see it in Day 1→Day 3 attendance and bootcamp-to-course enrolments per batch.

**What it is:** for one bootcamp batch, 300–700 TimePay discovery calls are read in full, one line per
lead, then counted and written up as a PDF for the bootcamp delivery team, with a short sales section.

## How to run it

Ask Claude: *"Learner needs report for IP Law 28–30 Nov"* (skill: `learner-needs-report`). The skill
holds the step list; this page holds the why.

| Step | Command / who | Output in `data/learner_needs/<KEY>/` |
|---|---|---|
| Sample | `python -m analytics.learner_needs sample <KEY>` | `chunk_XX.txt`, `idmap.json` |
| Read | subagents with `analytics/learner_needs/reader_brief.md` | `chunk_XX.tsv` |
| Group | `python -m analytics.learner_needs aggregate <KEY>` + config `groups` | `summary.json`, `exports/…_leads.csv` |
| Themes | subagents with `themes_brief.md` and a `codebook.json` | `themes.tsv` |
| Write | Claude | `script_outline.md`, `findings.json` |
| PDF | `python -m analytics.learner_needs pdf <KEY>` | `exports/Learner_needs_<KEY>.pdf` |

`python -m analytics.learner_needs status <KEY>` shows which chunks still need a reader.

## Why each rule

- **Longest call per lead, 60 s+**: shorter calls rarely tell who the person is; one call per lead stops a
  talkative lead counting twice.
- **Spread over landing pages, at least 8 per page**: big funnels would otherwise drown small ones, and
  the small ones are often where the wrong audience comes from (e.g. loan seekers on IP Law).
- **Claude reads every call; no keyword rules**: Hindi-English mix, regional languages and sarcasm break
  keyword counts.
- **Readers name their own segments; the config maps them**: readers work in parallel and cannot share
  names. The mapping is explicit and reviewable, and `aggregate` refuses to finish while a label is unmapped.
- **Counts come from `themes.tsv`, never typed**: a reader's "about 15" is a guess; the theme file is one
  row per lead and theme, so a count can be traced back to leads.
- **Questions placed on a script day**: trainers work from the Day 1–3 script; a question is only useful if
  it says where to answer it.
- **No phone numbers or surnames** in TSVs, findings or the PDF; the lead key (`chunk_03-17`) is enough to
  find a call again through `idmap.json`, which stays in `data/`.

## Adding a bootcamp

Copy a file in `analytics/learner_needs/bootcamps/`, set `name`, `dates`, `campaign_ids`, `sample_size`,
`growthx`, `scripts`, and start `groups` empty; step 4 fills it. Commit the config (it holds no lead data).

## Batches done

November and December 2026 runs: GP, IP, ReW (28–30 Nov); WomenAI, USAcc, ID (5–7 Dec); HR, CF, DPDP (12–14 Dec).
Women AI has no session script in the Nov/Oct folders, so its report is organised by group.
