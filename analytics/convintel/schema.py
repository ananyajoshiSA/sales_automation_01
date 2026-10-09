"""The module's contract: call classes, analysis statuses, analysis layers and their mandatory parts.

A call is ANALYZED only when every required layer has a validated result at the current version. Two layers:

* ``keyword`` (engine ``analytics/convintel/rules.py``): word- and phrase-level signals in English, Hindi and
  Hinglish, sentence labels, repeated phrases, talk density. Deterministic and free, so it runs on every
  transcript, but it counts words: it is not the context-aware reading the module is for.
* ``semantic`` (engine ``analytics/convintel/llm.py``): a model reads the whole call for intent, tone, quality,
  objections and how they were handled, commitments, coaching and evidence-backed findings. It needs model
  access; until then every transcript stays ANALYSIS_INCOMPLETE with the semantic layer named as missing.

Bumping a layer's version re-queues every call for that layer, so coverage is always measured against what
the current version requires.
"""

from __future__ import annotations

# ------------------------------------------------------------------ call classes (requirement 02)

REAL_CALL = "REAL_CALL"            # answered and >= 180 s of talk
SHORT_CALL = "SHORT_CALL"          # answered and < 180 s
NOT_CONNECTED = "NOT_CONNECTED"    # missed, not answered, failed
UNKNOWN = "UNKNOWN"                # answered but the talk time is missing or unreliable
CALL_CLASSES = (REAL_CALL, SHORT_CALL, NOT_CONNECTED, UNKNOWN)

# ------------------------------------------------------------------ analysis statuses (requirement 01)

ANALYZED = "ANALYZED"
PENDING_ANALYSIS = "PENDING_ANALYSIS"
ANALYSIS_IN_PROGRESS = "ANALYSIS_IN_PROGRESS"
ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"
ANALYSIS_FAILED = "ANALYSIS_FAILED"
TRANSCRIPT_NOT_FOUND = "TRANSCRIPT_NOT_FOUND"
# A call that never connected has no recording to transcribe. It stays in the inventory with this status and
# is left out of the coverage denominator; if a transcript turns up for it anyway it is analysed like any other.
NO_TRANSCRIPT_EXPECTED = "NO_TRANSCRIPT_EXPECTED"

STATUSES = (ANALYZED, PENDING_ANALYSIS, ANALYSIS_IN_PROGRESS, ANALYSIS_INCOMPLETE, ANALYSIS_FAILED,
            TRANSCRIPT_NOT_FOUND, NO_TRANSCRIPT_EXPECTED)
# Every status that counts against coverage: expected (or found) transcripts not yet fully analysed.
GAP_STATUSES = (PENDING_ANALYSIS, ANALYSIS_IN_PROGRESS, ANALYSIS_INCOMPLETE, ANALYSIS_FAILED, TRANSCRIPT_NOT_FOUND)

# Transcript lookup, kept apart from the analysis status so both are visible.
T_NOT_LOOKED_UP = "NOT_LOOKED_UP"   # queued: the call's number has not been searched since the call
T_FOUND = "FOUND"                   # matched to a recording with transcript text
T_NOT_FOUND = "NOT_FOUND"           # searched; no recording matches the call (rechecked on a schedule)
T_NOT_TRANSCRIBED = "NOT_TRANSCRIBED"  # a recording matches but has no transcript text yet (rechecked)
T_NO_NUMBER = "NO_NUMBER"           # the call record has no lead number, so it can't be searched
T_LOOKUP_FAILED = "LOOKUP_FAILED"   # the search request failed; retried
T_NOT_EXPECTED = "NOT_EXPECTED"     # not connected
TRANSCRIPT_STATES = (T_NOT_LOOKED_UP, T_FOUND, T_NOT_FOUND, T_NOT_TRANSCRIBED, T_NO_NUMBER, T_LOOKUP_FAILED,
                     T_NOT_EXPECTED)

# ------------------------------------------------------------------ layers and versions

KEYWORD, SEMANTIC = "keyword", "semantic"
LAYERS = (KEYWORD, SEMANTIC)
KEYWORD_VERSION = "kw-1.0"
SEMANTIC_VERSION = "sem-1.0"
REQUIRED_LAYERS = (KEYWORD, SEMANTIC)
ANALYSIS_VERSION = f"ci-1.0[{KEYWORD_VERSION}+{SEMANTIC_VERSION}]"

KEYWORD_COMPONENTS = ("language", "word_level", "sentence_level", "signals", "integrity")
SEMANTIC_COMPONENTS = ("language", "speaker_turns", "intent", "tone", "quality", "objections", "buying_signals",
                       "commitments", "unanswered_questions", "coaching", "outcome", "integrity", "findings", "summary")
COMPONENTS = {KEYWORD: KEYWORD_COMPONENTS, SEMANTIC: SEMANTIC_COMPONENTS}

# ------------------------------------------------------------------ shared vocabularies

QUALITY_DIMENSIONS = ("questioning", "active_listening", "objection_handling", "product_explanation",
                      "pricing_explanation", "payment_guidance", "follow_up_discipline", "customer_engagement",
                      "closing")
OBJECTION_CATEGORIES = ("price", "emi_or_finance", "time", "value_doubt", "trust", "family_approval", "course_fit",
                        "course_unavailable", "joined_elsewhere", "job_or_placement", "language", "technical",
                        "not_interested", "other")
SIGNAL_TYPES = ("fee_question", "payment_intent", "emi_interest", "start_date", "enrol_intent", "urgency",
                "decision_maker_ready", "documents_or_details", "other")
READINESS_BANDS = ("hot", "warm", "cool", "cold", "unclear")
FINDING_CATEGORIES = ("buying_signal_missed", "objection_unhandled", "payment_ready", "payment_friction",
                      "course_unavailable", "commitment_made", "callback_promised", "question_unanswered",
                      "misinformation_risk", "pressure_excessive", "strong_practice", "possible_not_real",
                      "zip_disagreement", "other")
# "Possibly not a real conversation" flags (user, 9 Oct 2026). Each means "needs review", never proof. Every flagged
# call is listed with its caller, the caller's dialer number and the number dialled (user, 9 Oct 2026), in files
# kept under data/ only.
INTEGRITY_FLAGS = {
    "under_3_min": "answered but under 3 minutes",
    "empty_transcript": "a recording exists but its transcript is empty",
    "no_content": "almost no words in the transcript",
    "thin": "very little talk for the time",
    "machine": "recorded message, IVR or voicemail",
    "loop": "one phrase repeating",
    "duration_mismatch": "recording much shorter than the time LeadSquared logged",
    "overlap": "caller was on another answered call at the same time",
    "repeat": "3+ real calls to one lead in a day",
    "just_over_3_min": "caller's calls bunch just over 3 minutes",
    "no_recording_long_call": "a 3+ minute call with no recording or transcript",
    "one_sided": "only one side seems to speak",
    "not_sales_talk": "not a sales conversation (wrong number, personal, hold music)",
}

_EXCERPT = {"type": "string", "description": "Exact words copied from the transcript (verbatim, no translation), or '' if none."}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": list(props) if required is None else required}


def _enum(values, desc: str = "") -> dict:
    d = {"type": "string", "enum": list(values)}
    return {**d, "description": desc} if desc else d


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


_NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_LEVEL = _enum(("high", "medium", "low", "unclear"))

# The semantic layer's output, enforced with structured outputs and checked again by validate.py (ranges,
# excerpts found verbatim in the transcript). Integers carry their range in the description because the API
# does not accept numeric bounds in the schema.
SEMANTIC_SCHEMA = _obj({
    "language": _obj({"primary": _enum(("english", "hindi", "hinglish", "other", "unclear")),
                      "code_switching": {"type": "boolean"}, "notes": {"type": "string"}}),
    "speaker_turns": _obj({
        "labels_in_transcript": {"type": "boolean", "description": "Does the transcript itself mark who speaks?"},
        "inferred": {"type": "boolean", "description": "True when turns were inferred from content, not labels."},
        "caller_share_pct": {**_NULLABLE_INT, "description": "0-100, null if it cannot be judged"},
        "caller_questions": {**_NULLABLE_INT, "description": "questions the caller asked, null if unknown"},
        "customer_questions": {**_NULLABLE_INT, "description": "questions the customer asked, null if unknown"},
        "progression": {"type": "string", "description": "How the conversation moved, in one or two sentences."},
    }),
    "intent": _obj({
        "explicit_intent": {"type": "string"},
        "readiness_score": {"type": "integer", "description": "0-100 likelihood of paying soon, from this call only"},
        "readiness_band": _enum(READINESS_BANDS),
        "genuineness": _enum(("genuine", "superficial", "unclear")),
        "conditional_commitments": _arr(_obj({"condition": {"type": "string"}, "excerpt": _EXCERPT})),
        "hidden_objections": _arr(_obj({"concern": {"type": "string"}, "excerpt": _EXCERPT})),
        "conflicting_statements": _arr(_obj({"conflict": {"type": "string"}, "excerpt": _EXCERPT})),
        "priorities_constraints": _arr({"type": "string"}),
        "evidence": _arr(_EXCERPT),
    }),
    "tone": _obj({
        "basis": _enum(("transcript_text",)),
        "vocal_analysis": _enum(("not_available",)),
        "customer_engagement": _LEVEL,
        "sentiment_start": _enum(("positive", "neutral", "negative", "unclear")),
        "sentiment_end": _enum(("positive", "neutral", "negative", "unclear")),
        "customer_confidence": _enum(("confident", "uncertain", "mixed", "unclear")),
        "customer_frustration": {"type": "boolean"},
        "customer_hesitation": {"type": "boolean"},
        "caller_empathy": _LEVEL,
        "caller_professionalism": _LEVEL,
        "caller_pressure": _enum(("none", "appropriate", "excessive", "unclear")),
        "caller_responsiveness": _LEVEL,
        "clarity": _LEVEL,
        "evidence": _arr(_EXCERPT),
    }),
    "quality": _obj({
        **{d: _obj({"score": {**_NULLABLE_INT, "description": "0-10, null when the call gave no chance to show it"},
                    "evidence": _EXCERPT, "note": {"type": "string"}}) for d in QUALITY_DIMENSIONS},
        "overall": {"type": "integer", "description": "0-10 overall sales quality of this call"},
    }),
    "objections": _arr(_obj({"category": _enum(OBJECTION_CATEGORIES), "excerpt": _EXCERPT,
                             "handled": _enum(("yes", "partly", "no", "unclear")),
                             "caller_response_excerpt": _EXCERPT, "note": {"type": "string"}})),
    "buying_signals": _arr(_obj({"type": _enum(SIGNAL_TYPES), "excerpt": _EXCERPT,
                                 "strength": _enum(("strong", "moderate", "weak")),
                                 "caller_acted_on_it": _enum(("yes", "no", "unclear"))})),
    "commitments": _arr(_obj({"by": _enum(("customer", "caller", "unclear")), "what": {"type": "string"},
                              "due_text": {"type": "string", "description": "The time words used, e.g. 'kal shaam 5 baje', or ''"},
                              "excerpt": _EXCERPT})),
    "unanswered_questions": _arr(_obj({"topic": {"type": "string"}, "excerpt": _EXCERPT})),
    "coaching": _obj({
        "strengths": _arr(_obj({"point": {"type": "string"}, "excerpt": _EXCERPT})),
        "improvements": _arr(_obj({"point": {"type": "string"}, "excerpt": _EXCERPT,
                                   "better_response": {"type": "string"}})),
        "missed_signals": _arr(_obj({"what": {"type": "string"}, "excerpt": _EXCERPT})),
        "priority_action": {"type": "string"},
    }),
    "outcome": _obj({
        "next_step_agreed": {"type": "boolean"}, "next_step": {"type": "string"}, "dated": {"type": "boolean"},
        "payment_step": _enum(("link_sent", "amount_and_date_agreed", "amount_quoted", "emi_discussed", "none", "unclear")),
        "course_discussed": {"type": "string"},
    }),
    "integrity": _obj({
        "real_conversation": _enum(("yes", "doubtful", "no")),
        "flags": _arr(_enum(("one_sided", "not_sales_talk", "machine", "no_content", "loop"))),
        "reason": {"type": "string"},
    }),
    "findings": _arr(_obj({"category": _enum(FINDING_CATEGORIES), "excerpt": _EXCERPT,
                           "confidence": _enum(("high", "medium", "low")), "reasoning": {"type": "string"},
                           "recommended_action": {"type": "string"}})),
    "summary": {"type": "string", "description": "Two or three neutral sentences: who wanted what, what was agreed."},
})
