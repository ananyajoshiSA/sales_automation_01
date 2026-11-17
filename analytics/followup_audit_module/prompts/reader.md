# Follow-up call audit: how to read one lead's follow-up calls

You are auditing leads whose LeadSquared stage changed to "Follow Up For Closure". Each pack item is ONE lead and
holds:

- `transcript`: first (if any) a BACKGROUND section with the lead's discovery call(s), then the SCORED CALLS: every
  answered call of 30+ seconds from the window start up to the moment of the move, oldest first, each headed
  `=== CALL DD-MM HH:MM IST · direction · seconds · caller ===`. Calls after the move are never included.
- `custom_instructions`: the prompt's CUSTOM INSTRUCTIONS for this lead (the window, what is background). Apply
  them exactly as the prompt's {customInstructions}.
- `scored_call_labels`: the "DD-MM HH:MM" label of every scored call.

Apply THE AUDIT PROMPT below exactly (relaxed conversion score, IIT check, red flags, compliance bar, column
rules) to the SCORED CALLS read together as one conversation, with these changes:

- Background calls are context only: use them to understand the lead's profile, pain and goals and what was
  promised, but never score them, flag compliance from them, or coach on them. Base scores, flags, red flags and
  coaching on the scored calls only. Name the call (e.g. "in the 28-10 15:49 call") when it helps.
- Judge the lead as it stood at the end of the last scored call; the next 48 hours count from the move.
- Voicemail, IVR menus, recorded messages, ringback music and one-sided calls (only the counsellor speaks) are not
  conversations: put their labels in `not_a_conversation` and say nothing else about them. If every scored call is
  one, still write the file with that list and score the evidence that exists (usually all zeros).
- You do NOT fill Call Date, Duration, Mobile Number or Conversion Potential: the code fills them from LeadSquared
  and from your subscores.
- Use only the transcripts. The CRM name, course and caller names in the pack are for reference only; the
  Caller Name and Lead Name cells are the names as spoken ("Not Evident" if never said). Several counsellors may
  speak across the calls: name them all in Caller Name, in order.
- Quote only words that appear in the transcript, in straight double quotes "like this". The checker rejects a
  quote it cannot find word for word. Paraphrase without quotation marks.
- No line breaks inside any text. Separate list items in Red Flags and Coaching with " | ".

Write one JSON file per lead: `results/<lead_key>.json` (next to the pack), exactly this shape:

```json
{
  "lead_key": "copy from the pack",
  "not_a_conversation": [],
  "subscores": {
    "interest": 0, "burning_problem": 0, "buying_signals": 0, "objections": 1.5, "commitment": 0,
    "objection_raised": false,
    "open_concern": false
  },
  "subscore_reasons": {"interest": "...", "burning_problem": "...", "buying_signals": "...", "objections": "...", "commitment": "..."},
  "burning_problem": "IDENTIFIED or NOT IDENTIFIED (binary rule in the prompt)",
  "compliance_flags": "NONE, or CRITICAL: <type> (<verbatim quote>) items joined by \" | \"",
  "sheet": {
    "Caller Name": "", "Lead Name": "", "Profile Summary (Discovery Quality)": "", "Pain Point Discovery": "",
    "Primary Pain Identified": "", "Discovery Framework Adherence": "", "Goal Setting Clarity": "",
    "Pitch Discipline": "", "IIT Compliance": "Compliant | Non-Compliant | Not Mentioned",
    "Red Flags — Missed Discovery Signals": "", "Coaching Action (Caller-Specific)": "", "Closer Briefing": ""
  },
  "pdf": {
    "stage_of_call": "Follow-up / Pitch / Close Attempted / Multi-call Sequence (only what happened)",
    "education": "education, work and situation as stated",
    "immediate_goal": "", "current_plan": "including any backup plan", "current_income": "as stated, else Not discussed",
    "burning_problem_note": "why IDENTIFIED or NOT IDENTIFIED, with the lead's words",
    "score_reason": "2-3 sentences on why the score is what it is",
    "call_summary": "one paragraph: what happened across the scored calls",
    "highlight_strongest": "the counsellor's strongest moment, with a quote",
    "highlight_miss": "the biggest miss, with a quote",
    "highlight_end": "where it stood at the move: payment asked or not, next step and its date",
    "what_went_wrong": ["1-5 short gaps, each tied to a moment"],
    "improvements": ["1-5 specific actions for the next call; example lines the caller could say are fine"]
  },
  "tags": {"strengths": ["from the fixed list"], "gaps": ["from the fixed list"]},
  "quality_note": "empty, or what limited the audit (garbled text, cut-off call, name unclear)"
}
```

Subscores (the prompt's five criteria): interest 0-3; burning_problem 0-2; buying_signals 0-2; commitment
0/0.5/1/1.5. Objections: set `objection_raised` true only if the lead raised a material objection, and then score
`objections` 0/0.75/1.5 as the prompt says. If no objection was raised, set `objections` to 1.5 and set
`open_concern` true if anything was left open (fee deferred, "will think", someone to consult). The code computes
the relaxed label (the prompt's), a strict label and the PDF score from these.

Rules that keep the sheet and PDF consistent:
- `burning_problem` NOT IDENTIFIED exactly when Primary Pain Identified is "Not identified". Pain the lead stated in
  a background call may be named, since it is the same lead, but say which call it came from.
- If `compliance_flags` is not NONE, Pitch Discipline names the same CRITICAL claim with its quote.
- The PDF fields and the sheet cells describe the same reading: same facts, same flags, same next step.

Tags (for the per-caller trend in the PDF):
- strengths: explains_programme_clearly, dated_next_step, thorough_background_discovery, explores_pain,
  challenges_backup_plan, maps_decision_maker, checks_financial_readiness, direct_close_ask
- gaps: income_claim, client_or_job_guarantee, financial_readiness_not_assessed, pitch_before_discovery,
  cost_of_inaction_not_explored, pain_not_connected_to_course, decision_maker_not_mapped, no_dated_next_step,
  buying_signal_not_escalated, backup_plan_accepted, lead_question_left_unclear

When a file is written, run:
`python -m analytics.followup_audit_module check <run> <path to results/<lead_key>.json>`
(`<run>` is the folder name under data/followup_audit/, e.g. 2026-11-14) and fix the file until it prints OK.
Never write a result for a lead you did not read in full.
