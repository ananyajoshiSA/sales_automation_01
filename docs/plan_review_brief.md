You are reading leads for a team's calling plan for TODAY, {TODAY}. {CONTEXT} Data covers up to {DATA_TO}. Previous plan: {PREVIOUS}.

INPUT: {DIR}/in_{K}.json. Each lead has its owner (the caller), stage, course, Zipteams notes (AI notes of one call; often wrong, judge from the content), the call log (last 12 calls, IST, caller in brackets), "discovery_so_far" (the AI voice agent's discovery call and/or the caller's own notes), the next follow-up date, whether a booked callback was missed, and the rule tier the plan gave it. Leads with "previous_review" were read for the previous plan and have since had an answered call or a new note: re-judge them on what happened since (a payment, a refusal, a new promise, no answer). Leads without one are read for the first time: judge them fresh.

For EVERY lead decide:
- tier: "A" can close today or tomorrow (payment/EMI/documents/seat block/decision under discussion or due, and reachable); "B" real interest, 1–2 steps from paying; "C" nurture (blocked, more than a week away, or gone quiet); "D" leave off (refused, not a prospect, already enrolled/paid, wrong person, support issue only). If LeadSquared says Not Interested or May buy later after the latest call and the notes agree, lower the tier.
- prob_3d: integer % chance of enrolling within 3 days (A 25–50, B 8–25, C 2–8, D 0). Be honest and conservative: only a handful of leads per team enrol on a day, and many "hot" leads say no.
- why: max ~250 chars, plain, concrete facts with dates (what they said, what is blocking); for a lead read before, say what changed.
- opening_line: max ~220 chars, using the caller's first name (the owner) and the lead's first name, referring to the latest conversation. Never apologise for missing an incoming call.
- next_action: max ~250 chars, the specific ask on today's call.
- objection_to_prepare: max ~120 chars or "".
- best_time: short, e.g. "18:00–21:00" or "after 19:00" (use the call log: when they answered).
- promise_due: true only if the lead said they would decide/pay/talk by a date on or before {PROMISE_BY}, or a callback was booked for today or tomorrow.
- stage_fix: correct stage + short reason if the LeadSquared stage is clearly wrong, else "".
- changed: for a lead read before, one short phrase on what changed ("paid token on 19 Oct", "said not interested on 20 Oct", "no answer, unchanged"); "new lead" for a first read.
Never invent fees, amounts, discounts, batch dates or offers that are not in the notes. Use neutral pronouns for leads. Read every lead yourself: no keyword rules, no model API. Lead data is personal: write it only to the output file and your own scratch folder.

OUTPUT: {DIR}/out_{K}.json — a JSON list, one object per input lead in the same order, keys: lead_id, tier, prob_3d, why, opening_line, next_action, objection_to_prepare, best_time, promise_due, stage_fix, changed. Validate with python (length and lead_ids match the input, tier in A/B/C/D, prob_3d an integer inside its tier's range), or run `python scripts/plan_review.py check {DIR}`. Modify no other file in the repository. Reply only with counts per tier, how many moved up / down against previous reviews, and how many promise_due; no lead names.
