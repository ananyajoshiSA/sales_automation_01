"""Close-behaviour markers in call text (transcripts or Zipteams summaries).

These are the P33 regexes of the daily report (Parameters v1.0), kept verbatim so the report stays
reproducible. The payment step is the plan's first action: every real conversation should end with
an amount, an EMI option, a payment link sent on the call and a date to pay.
"""

from __future__ import annotations

import re

MARKERS = {
    "Price / fee / EMI": r"\b(emi|fee|fees|price|cost|amount|rupees|installment)\b",
    "Discount / scholarship / offer": r"\b(discount|scholarship|offer|waive)",
    "Payment step (link, pay now, balance)": r"(payment link|pay (it )?now|make the payment|link .{0,30}(pay|payment)|razorpay|complete the payment|balance (amount|payment))",
    "Urgency (deadline, seats, last date)": r"\b(today itself|by tonight|deadline|last date|seats?|limited|closing)\b",
    "Discovery questions": r"(what do you do|currently working|your background|tell me about yourself|purpose of joining|your goal|what made you|how is the journey|which year)",
    "Batch / LMS / onboarding": r"\b(batch|lms|onboarding|orientation|login)\b",
    "Career / ROI": r"\b(job|salary|income|earn|client|placement|career)\b",
    "Fixed next step (date/time)": r"(tomorrow at|by (tomorrow|evening|tonight)|\d ?(pm|am)\b|o'clock|i will call you (at|tomorrow))",
}
PAYMENT_STEP = "Payment step (link, pay now, balance)"
NEXT_STEP = "Fixed next step (date/time)"
_RX = {k: re.compile(v, re.I) for k, v in MARKERS.items()}

# Zipteams summaries describe calls in the third person ("opted for NEFT payment", "payment options
# (NEFT, scanner, payment link)"), so the plan tracker and coaching pack use this wider pattern on them.
# It is not a P33 marker and never feeds the daily report's figures.
SUMMARY_PAYMENT_STEP = re.compile(
    r"payment link|pay(ment)? (now|today|tonight)|complete the payment|make the payment|balance (amount|payment)"
    r"|opted for (neft|upi|emi|card)|chose .{0,25}payment|(neft|upi|scanner|razorpay|card) .{0,25}payment"
    r"|payment .{0,25}(made|completed|done|initiated|received)|booking (fee|amount)|token (amount|payment)"
    r"|seat (block|blocking)|registration (fee|amount)", re.I)
_CONVERSATION_LINK = re.compile(r"link to the conversation:\s*\S+", re.I)


def markers_in(text: str | None) -> set[str]:
    return {k for k, rx in _RX.items() if rx.search(text or "")}


def has_payment_step(text: str | None) -> bool:
    return bool(_RX[PAYMENT_STEP].search(text or ""))


def summary_has_payment_step(summary: str | None) -> bool:
    return bool(SUMMARY_PAYMENT_STEP.search(_CONVERSATION_LINK.sub(" ", summary or "")))


def marker_rates(texts: list[str]) -> dict[str, int | None]:
    """% of texts containing each marker (None when there are no texts)."""
    hits = [markers_in(t) for t in texts]
    return {k: (round(100 * sum(k in h for h in hits) / len(hits)) if hits else None) for k in MARKERS}
