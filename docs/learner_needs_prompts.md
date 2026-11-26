# Learner-needs reports: project instructions and prompts

Copy the **Project instructions** block into the Claude project (or Claude Code session) on this repo.
Then anyone on the team can use the **Prompts** below. The full method is in
[learner_needs.md](learner_needs.md) and the skill `.claude/skills/learner-needs-report/SKILL.md`.

## Project instructions (paste as-is)

```
You build learner-needs reports for Skill Arbitrage / LawSikho bootcamps from TimePay AI-agent calls.
Audience: the bootcamp delivery team (trainers) first, the sales team second.

Always use the learner-needs-report skill and the code in analytics/learner_needs/. Follow its steps in
order: config → sample → read → groups → script outline → themes → findings → PDF → checks.

Rules:
- Read every call yourself (subagents in parallel, one per chunk). No keyword rules, no model API.
- Sample 300–700 leads per bootcamp: longest 60s+ call per lead, at least 8 per landing page.
- Counts come only from themes*.tsv. Never type or guess a number. Show numbers with percentages.
- The report is about learners, not the calling agent: no prompt fixes, no "problems on our side".
- If there is no session script for the batch, say so and place questions under Day 1 / Day 2 / Day 3
  headings without script segments. Ask once for the script link; do not wait for it.
- TimePay, GrowthX and Google Drive are read-only here. Never start calls, edit campaigns or send messages.
- Lead data stays in data/ and exports/ (never committed). No phone numbers or surnames in any output.
- Before sharing: aggregate exits 0, every chunk has a TSV, hand-check 20 TSV lines against calls and
  20 theme lines against TSV rows, proofread every headline number against summary.json, look at page 1–3.
- Plain language, IST dates, say how fresh the calls are. Deliver the PDF, then a 5-line summary in chat.
- Ask clarifying questions alongside answers, not instead of them.
```

## Prompts

Replace the parts in `<…>`.

**1. Full report for a new batch**
> Learner needs report for `<bootcamp>` `<dates>`. TimePay campaign: `<name or id>`. Scripts: `<Day 1/2/3
> doc links, or "no script yet">`. Sample size `<300–700>`.

**2. Same bootcamp, next batch** (reuses the config and groups)
> Re-run the learner needs report for `<KEY>` for the `<dates>` batch, campaign `<id>`, calls from `<date>`.

**3. Add the script later** (no re-reading needed)
> Here are the `<bootcamp>` scripts: `<links>`. Write the script outline and rebuild the learner needs PDF
> with each question placed on its script segment.

**4. Status**
> What is the status of the learner needs run for `<KEY>`? Which chunks are left?

**5. Checks only**
> Hand-check the `<KEY>` learner needs run: 20 TSV lines against calls, 20 theme lines against TSV rows,
> and every headline number against summary.json. Fix and rebuild if anything is wrong.

**6. One group deep-dive**
> From the `<KEY>` learner needs data, give trainers one page on `<group>`: who they are, what they ask,
> what to say to them, 5 quotes.

**7. Compare batches**
> Compare learner needs for `<KEY>` between the `<batch A>` and `<batch B>` runs: which groups, doubts
> and questions grew or shrank (counts and %).

**8. Sales one-pager**
> From the `<KEY>` learner needs data, write a one-page sales brief: top objections with counts, the
> answer that fits each group, and what not to promise.

**9. Marketing flag** (wrong-audience landing pages)
> From the `<KEY>` learner needs data, list the landing pages that brought the most wrong-audience or
> confused leads, with counts and what those leads thought the bootcamp was.

## Bootcamp keys

GP (Global Patent), IP (IP Law), ReW (Remote Work for Women, organic), WomenAI, USAcc, ID, HR, CF, DPDP.
New bootcamp: prompt 1 creates `analytics/learner_needs/bootcamps/<KEY>.json`.
