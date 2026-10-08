"""One meaning everywhere: stage names, thresholds, speed buckets and buying-signal patterns.

Every report imports these instead of keeping its own copy (plan step 10). The keyword patterns
(plan step 3) use word boundaries, leave out law-domain words that are course names rather than
buying signals ("family law", "batch", "register"), and ignore a match that is negated just before it.
"""

from __future__ import annotations

import re

ENROLLED = "Course Enrolled"
# LeadSquared has used both spellings of the invalid stage; both are closed.
CLOSED_STAGES = frozenset({ENROLLED, "Irrelevant lead", "Invalid lead", "Invalid", "Duplicate"})
DEAD_STAGES = frozenset({"Not Interested", "Irrelevant lead", "Invalid lead", "Invalid", "Duplicate"})
OPEN_STAGES = frozenset({"Call Back Later", "Discovery Call Done", "Roadmap\xa0Done", "Roadmap Done", "Counselled lead",
                         "Follow Up For Closure", "May buy later", "Opportunity Created"})
PIPELINE_STAGES = OPEN_STAGES - {"Call Back Later"}

REAL_CONVERSATION_SECS = 120   # answered and at least this long
WORKING_DAY_DIALS = 20         # a caller's working day
DIALER_FAILURE_SHARE = 0.5     # 50%+ CallFailure on a working day = dialer problem, not the caller

SPEED_BUCKETS = ((5, "≤5 min"), (15, "5–15 min"), (60, "15–60 min"), (240, "1–4 h"), (1440, "4–24 h"))
BUCKETS = [label for _, label in SPEED_BUCKETS] + [">24 h", "never"]


def speed_bucket(minutes: float | None) -> str:
    if minutes is None:
        return "never"
    return next((label for limit, label in SPEED_BUCKETS if minutes <= limit), ">24 h")


BUYING_SIGNALS = {
    "fee": r"\b(fees?|price|pricing|cost|how much)\b",
    "emi_or_loan": r"\b(emis?|instal+ments?|loan|no[- ]cost emi|financing option)\b",
    "payment": r"\b(payment link|pay(ment)? (today|tomorrow|now)|will pay|make the payment|transfer the (fee|amount))\b",
    "decision_maker": r"\b(discuss(ed)? (it )?with (my |his |her )?(parents?|father|mother|husband|wife|spouse|family)"
                      r"|(parents?|father|mother|husband|wife|spouse|family) (will|has to|have to|needs? to|must) (decide|approve|pay|agree))\b",
    "start_date": r"\b(start date|when (does|will) (it|the course|the batch|the program) start|next cohort|joining date|next batch)\b",
    "enroll_intent": r"\b((wants?|ready|keen|plans?) to (enrol+|join|pay|sign up)|sign up|join the (course|program))\b",
    "refund_or_guarantee": r"\b(refund|money[- ]back|guarantee|placement|job assistance)\b",
}
NEGATIVE_SIGNALS = {
    "joined_elsewhere": r"\b(already (joined|enrolled|purchased|took)|another (course|institute)|other institute)\b",
    "not_interested": r"\b(not interested|no interest|do not call|don'?t call|stop calling)\b",
    "no_budget": r"\b(can'?t afford|cannot afford|no budget|too expensive|not able to pay)\b",
    "wrong_person": r"\b(wrong number|not (the )?(right|same) person|didn'?t (fill|enquire|register))\b",
}
_NEGATION = re.compile(r"\b(not|no|never|don'?t|didn'?t|doesn'?t|isn'?t|won'?t|without)\b[\w\s,]{0,20}$", re.I)


def signals(text: str, patterns: dict[str, str], negatable: bool = True) -> list[str]:
    """Categories with at least one match not negated just before it ("not interested in EMI")."""
    found = []
    for key, rx in patterns.items():
        for m in re.finditer(rx, text, re.I):
            if not (negatable and _NEGATION.search(text[max(0, m.start() - 30):m.start()])):
                found.append(key)
                break
    return sorted(found)
