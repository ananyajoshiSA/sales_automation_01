"""Module-only call classes: REAL_CALL is answered with at least 180 seconds of talk (requirement 02).

The project-wide "real conversation" (analytics/definitions.py, the calling report v1.3 and the dashboard)
stays at 2 minutes; this 3-minute rule is used only inside analytics/convintel and its own dashboard view,
where it is always labelled "real calls (3+ min)".

Talk time is LeadSquared's call ``Duration``. For answered calls the dialer logs the connected time; an
unanswered call logs 0. A missing, unreadable, zero or negative duration on an answered call is UNKNOWN and
never counts as a real call.
"""

from __future__ import annotations

from analytics.convintel.schema import NOT_CONNECTED, REAL_CALL, SHORT_CALL, UNKNOWN

REAL_CALL_SECS = 180
MAX_PLAUSIBLE_SECS = 4 * 3600   # a longer "call" is a logging error (line left open), not talk
ANSWERED = {"answered"}
NOT_ANSWERED = {"notanswered", "not answered", "missed", "callfailure", "call failure", "busy", "noanswer",
                "no answer", "rejected", "cancelled", "canceled", "failed", "unanswered", "voicemail"}


def parse_duration(raw) -> int | None:
    """Seconds as an int, or None when the value is missing or unreadable (never 0 for "unknown")."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def classify(status: str | None, duration: int | None) -> tuple[str, str]:
    """(call class, reason) for one call from its LeadSquared status and talk seconds.

    179 s -> SHORT_CALL, 180 s -> REAL_CALL, 181 s -> REAL_CALL, unanswered -> NOT_CONNECTED,
    answered with no usable duration -> UNKNOWN.
    """
    s = (status or "").strip().lower()
    if s in NOT_ANSWERED:
        return NOT_CONNECTED, f"status {status}"
    if s not in ANSWERED:
        return UNKNOWN, f"unrecognised call status {status!r}" if status else "no call status"
    if duration is None:
        return UNKNOWN, "answered, duration missing or unreadable"
    if duration <= 0:
        return UNKNOWN, "answered, duration recorded as 0"
    if duration > MAX_PLAUSIBLE_SECS:
        return UNKNOWN, f"answered, duration {duration} s is implausible"
    if duration >= REAL_CALL_SECS:
        return REAL_CALL, f"answered, {duration} s"
    return SHORT_CALL, f"answered, {duration} s (under {REAL_CALL_SECS} s)"


def is_answered(status: str | None) -> bool:
    """Connected calls: answered, whatever their length (short and unknown-length calls stay in connected counts)."""
    return (status or "").strip().lower() in ANSWERED
