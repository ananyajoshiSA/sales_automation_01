"""Each lead's journey across its calls: readiness and its trend, repeated objections, follow-ups promised but
not made, plain-language flags and one next step.

Readiness is the latest score from Claude's reading of the lead's calls; a score whose band is "unclear" (too
little talk to judge) is never used. A follow-up counts as missed only once 48 hours have
passed with no later answered call on the lead (or enrolment). Both that and "no call in 48 h" are judged only up
to where the calls run (the latest call given, or ``now`` if earlier): a report on a past period can't see the
calls made after it, so a promise made in its last 48 hours is left unjudged rather than called missed.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.convintel import schema as S
from analytics.convintel.integrity import call_time, is_person
from analytics.definitions import ENROLLED
from integrations.timeutil import IST, utc

FOLLOW_UP = timedelta(hours=48)   # a promised callback or dated next step should be done by then
HOT = 70                          # readiness at or above: ready to pay (team views use the same line)
WARM = 40
TREND_POINTS = 10                 # latest score this far above or below the earlier calls' average
REPEATED = 2                      # an objection raised in this many calls is repeated
_CATEGORIES = frozenset(S.OBJECTION_CATEGORIES)
OBJECTION_LABEL = {"price": "price", "emi_or_finance": "EMI or finance", "time": "no time", "value_doubt": "doubts value",
                   "trust": "trust", "family_approval": "family approval", "course_fit": "course fit",
                   "course_unavailable": "course not available", "joined_elsewhere": "joined elsewhere",
                   "job_or_placement": "job or placement", "language": "language", "technical": "technical problem",
                   "not_interested": "not interested", "other": "other"}


def _ist(t: datetime | None) -> str | None:
    return t.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t else None


def _score(layer: dict | None, part: str) -> tuple[int, str | None] | None:
    x = (layer or {}).get(part) or {}
    s, band = x.get("readiness_score"), x.get("readiness_band")
    return (s, band) if isinstance(s, int) and not isinstance(s, bool) and band != "unclear" else None


def objections(c: dict) -> set[str]:
    """Objection categories on one call, from Claude's reading."""
    return {o.get("category") for o in (c.get("sem") or {}).get("objections") or []} & _CATEGORIES


def commitment(c: dict) -> bool:
    """Did the call leave a follow-up for the caller: a commitment by the caller or a promised callback?"""
    sem = c.get("sem") or {}
    return any(x.get("by") == "caller" for x in sem.get("commitments") or []) \
        or any(f.get("category") == "callback_promised" for f in sem.get("findings") or [])


def trend(scores: list[int]) -> str:
    if not scores:
        return "unknown"
    if len(scores) == 1:
        return "single call"
    d = scores[-1] - sum(scores[:-1]) / (len(scores) - 1)
    return "rising" if d >= TREND_POINTS else "falling" if d <= -TREND_POINTS else "flat"


def _next_action(j: dict, missed_at: list[datetime], quiet: bool, obj_n: Counter) -> str:
    r = j["readiness"]
    if j["enrolled"]:
        return "Already enrolled: no sales call needed."
    if missed_at:
        return (f"Call today: a follow-up promised on the call of {_ist(missed_at[-1])} IST was not made within "
                "48 hours. Apologise, then pick up where that call left off.")
    if r is not None and r >= HOT:
        when = f"no call since {j['lastCallIst']} IST" if quiet else "called recently"
        return (f"{'Call today to close' if quiet else 'Close on the next call'}: readiness {r}/100, {when}. "
                "Confirm the fee, send the payment link during the call and agree the date to pay.")
    if j["repeatedObjections"]:
        cat = j["repeatedObjections"][0]
        return (f"Prepare an answer to the '{OBJECTION_LABEL[cat]}' objection before calling again: it came up in "
                f"{obj_n[cat]} calls.")
    if j["readinessTrend"] == "falling":
        return "Ask what has changed before pitching again: readiness has fallen across the calls."
    if r is None:
        return ("Reach the lead for a full conversation (3+ minutes): none yet in this period." if not j["realCalls"]
                else "No analysed call yet: listen to the latest real call before the next one.")
    if r >= WARM:
        return f"Book a dated follow-up and answer the lead's open questions: readiness {r}/100."
    return f"Low readiness ({r}/100): one call to check interest and budget; if it stays low, follow up less often."


def _journey(lead_id: str, cs: list[tuple[datetime, dict]], n: tuple[int, int], info: dict,
             enrolled_at: datetime | None, enrolled: bool, until: datetime) -> dict:
    pts, obj_n = [], Counter()
    analysed = [(t, c) for t, c in cs if c.get("sem")]
    for _, c in analysed:
        if (s := _score(c.get("sem"), "intent")) is not None:
            pts.append(s)
        obj_n.update(objections(c))
    readiness, band = pts[-1] if pts else (None, None)
    source = S.SEMANTIC if pts else None
    answered = [t for t, c in cs if c.get("answered")]
    missed_at = []
    for t, c in analysed:
        due = t + FOLLOW_UP
        if until >= due and commitment(c):
            i = bisect_right(answered, t)
            if not (i < len(answered) and answered[i] <= due) and not (enrolled_at and t <= enrolled_at <= due):
                missed_at.append(t)
    last_t = cs[-1][0] if cs else None
    quiet = bool(last_t and until - last_t > FOLLOW_UP)
    last_person = next((c for _, c in reversed(cs) if is_person(c)), None)
    repeated = sorted((k for k, n in obj_n.items() if n >= REPEATED), key=lambda k: (-obj_n[k], k))
    j = {"leadId": lead_id, "owner": info.get("owner_name"), "team": info.get("team"), "stage": info.get("stage"),
         "course": info.get("course"), "priority": info.get("priority"), "score": info.get("score"),
         "calls": n[0], "realCalls": n[1],
         "lastCallIst": _ist(last_t), "readiness": readiness, "readinessSource": source,
         "readinessBand": band, "readinessTrend": trend([p for p, _ in pts]), "trendSource": source,
         "repeatedObjections": repeated, "missedCommitments": len(missed_at), "flags": [], "nextAction": "",
         "enrolled": enrolled, "lastCallerId": (last_person or {}).get("caller_id"),
         "lastCaller": (last_person or {}).get("caller_name"), "lastCallerTeam": (last_person or {}).get("team")}
    if not enrolled:
        if readiness is not None and readiness >= HOT and quiet:
            j["flags"].append("hot lead, no call in 48 h")
        j["flags"] += [f"same objection in {obj_n[k]} calls: {OBJECTION_LABEL[k]}" for k in repeated]
        if missed_at:
            j["flags"].append("promised callback missed" if len(missed_at) == 1 else
                              f"{len(missed_at)} promised callbacks missed")
        if j["readinessTrend"] == "falling":
            j["flags"].append("readiness falling")
    j["nextAction"] = _next_action(j, missed_at, quiet, obj_n)
    return j


def lead_journeys(calls: list[dict], leads: dict, enrolments: list[dict], now: datetime,
                  data_end: datetime | None = None) -> dict[str, dict]:
    """lead_id -> LEAD for every lead with a call in ``calls``: the snapshot's lead shape plus readinessSource,
    readinessBand, trendSource and the last person who called (lastCallerId, lastCaller, lastCallerTeam).

    ``leads``: lead_id -> {"stage", "course", "owner_name", "team", "priority", "score"} (missing fields are None);
    ``enrolments``: first-time enrolments ({"lead_id", "at_utc"}); ``now``: aware datetime. ``data_end``: how far
    the calls given run (default: the latest call in ``calls``); follow-ups are judged up to it, never past ``now``."""
    enrolled_at: dict[str, datetime | None] = {}
    for e in enrolments:
        if lid := e.get("lead_id"):
            t = utc(e.get("at_utc"))
            enrolled_at[lid] = min((x for x in (enrolled_at.get(lid), t) if x), default=None)
    timed, counts, real, latest = defaultdict(list), Counter(), Counter(), None
    for c in calls:
        t = call_time(c)
        latest = max(latest, t) if latest and t else latest or t
        if lid := c.get("lead_id"):
            counts[lid] += 1
            real[lid] += c.get("call_class") == S.REAL_CALL
            if t:
                timed[lid].append((t, c))
    until = min(data_end or latest or now, now)
    out = {}
    for lid, n in counts.items():
        cs = sorted(timed.get(lid, []), key=lambda x: (x[0], x[1]["call_id"]))
        info = leads.get(lid) or {}
        enrolled = lid in enrolled_at or info.get("stage") == ENROLLED
        out[lid] = _journey(lid, cs, (n, real[lid]), info, enrolled_at.get(lid), enrolled, until)
    return out
