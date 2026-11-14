# Discovery call audit: how to read one lead's day

You are auditing leads whose LeadSquared stage changed to "Discovery Call Done". Each pack item is ONE lead and
holds ALL of that lead's calls on one day (IST), in time order, separated by `=== CALL at HH:MM IST ... ===`
lines. Read every call of the day in full, together, as one conversation: a call that dropped and the redial are
the same discovery. Calls not answered or without a transcript are listed so you know they happened; say nothing
about their content.

Apply THE AUDIT PROMPT below exactly (relaxed conversion score, IIT check, red flags, compliance bar, column
rules), with these changes for a whole day:

- Base every finding on the day's calls together. Name the call time (e.g. "in the 18:18 call") when it helps.
- You do NOT fill Call Date, Duration, Mobile Number or Conversion Potential: the code fills them from LeadSquared
  and from your subscores.
- Use only the transcripts. The CRM name, course and caller names in the pack are for reference only; the
  Caller Name and Lead Name cells are the names as spoken ("Not Evident" if never said).
- Quote only words that appear in the transcript, in straight double quotes "like this". The checker rejects a
  quote it cannot find word for word. Paraphrase without quotation marks.
- No line breaks inside any text. Separate list items in Red Flags and Coaching with " | ".

Write one JSON file per lead: `results/<lead_key>.json` (next to the pack), exactly this shape:

```json
{
  "lead_key": "copy from the pack",
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
    "stage_of_call": "Discovery / Discovery + Pitch / Discovery + Pitch + Close Attempted (only stages that happened)",
    "education": "education, work and situation as stated",
    "immediate_goal": "", "current_plan": "including any backup plan", "current_income": "as stated, else Not discussed",
    "burning_problem_note": "why IDENTIFIED or NOT IDENTIFIED, with the lead's words",
    "score_reason": "2-3 sentences on why the score is what it is",
    "call_summary": "one paragraph: what happened across the day's calls",
    "highlight_strongest": "the counsellor's strongest moment, with a quote",
    "highlight_miss": "the biggest miss, with a quote",
    "highlight_end": "how the day ended: next step, any ask",
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
- `burning_problem` NOT IDENTIFIED exactly when Primary Pain Identified is "Not identified".
- If `compliance_flags` is not NONE, Pitch Discipline names the same CRITICAL claim with its quote.
- The PDF fields and the sheet cells describe the same reading: same facts, same flags, same next step.

Tags (for the per-caller trend in the PDF):
- strengths: explains_programme_clearly, dated_next_step, thorough_background_discovery, explores_pain,
  challenges_backup_plan, maps_decision_maker, checks_financial_readiness, direct_close_ask
- gaps: income_claim, client_or_job_guarantee, financial_readiness_not_assessed, pitch_before_discovery,
  cost_of_inaction_not_explored, pain_not_connected_to_course, decision_maker_not_mapped, no_dated_next_step,
  buying_signal_not_escalated, backup_plan_accepted, lead_question_left_unclear

When a file is written, run:
`python -m analytics.discovery_audit_module check <day> <path to results/<lead_key>.json>`
and fix the file until it prints OK. Never write a result for a lead you did not read in full.
