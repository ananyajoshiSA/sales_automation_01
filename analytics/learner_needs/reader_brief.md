You are reading TimePay AI-agent call transcripts of leads registered for a Skill Arbitrage / LawSikho bootcamp. The purpose is NOT to judge the calling agent. It is to understand the LEARNERS — who they are, what they need, expect, fear and misunderstand — so the bootcamp delivery team (trainers) can shape the three live sessions, and secondarily the sales team that pitches the paid course at the end.

Paths: your task names the input chunk (`data/learner_needs/<KEY>/chunk_XX.txt`) and the output (`chunk_XX.tsv` next to it).

Read EVERY call in your file yourself, in chunks (offset/limit). Do not use keyword scripts to decide anything. Focus on what the LEAD says; agent lines only give context. Each call starts with "===== LEAD <n> | funnel ... | disposition ...".

For EACH lead write one tab-separated line (header row first) to the output file, columns:
lead | funnel | who (short profile in plain words: role, field, years, life stage, city type if said) | segment (your short group name; reuse the same names consistently across leads) | why_registered (their reason, in their words where possible) | hopes (what they want the bootcamp/career to give them: income figure, freelance/job/side income, skill, recognition…) | time (hours/days available, timing constraints) | doubts (worries/objections: fees after ₹10 or "paid course", recordings, time clash, legitimacy, ability, language, age, device…) | misconceptions (what they wrongly think the bootcamp/career is, e.g. a job, a loan, tutoring) | questions (questions they asked, short) | resonated (what landed with them on the call) | their_words (one short verbatim phrase that captures their situation, max 15 words; no phone numbers or surnames)
Use "-" for empty fields. Reuse segment names from the config's `groups` list (given in your task) where one fits, so counts merge cleanly. If someone else's file is in the folder, never overwrite it; keep scratch copies in your own subfolder. If the call is too short to tell, still write the line with "-" fields.

Then reply (under 450 words):
1. The main segments with counts (out of the leads you read).
2. Top hopes, top doubts, top misconceptions — each with counts.
3. The most common questions they want answered.
4. 5 short verbatim quotes that would help a trainer understand the room.
No phone numbers or full names in the reply.
