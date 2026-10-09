"""Coaching from analysed calls: per call, per caller (recurring strengths and gaps, three actions), a weekly
manual-review sample, and per team (gaps shared by 2+ callers, what the strongest callers do well).

Claude's reading of each call gives quality scores, objections and how they were handled, buying signals the
caller acted on or missed, and the call's outcome; a call Claude has not read yet gets no coaching. Items are fixed
plain labels so they can be counted across calls and callers; an item is a strength or a gap only when it recurs
(RECURRING calls), and a quality dimension only when its average over RECURRING+ scored calls is GOOD+ or WEAK-.
Shared logins, automation and non-users get no coaching: nobody in particular can act on it.

The weekly sample is each caller's 5 longest real calls per IST week (Monday start): an extra set for a team
leader to listen to, never a replacement for analysing every call.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from analytics.convintel import schema as S
from analytics.convintel.crosscall import HOT, OBJECTION_LABEL
from analytics.convintel.integrity import call_time, caller_key, is_person
from integrations.timeutil import IST

GOOD, WEAK = 7, 4     # quality averages (0-10) at or above / at or below
RECURRING = 2         # calls an item needs before it counts as a habit
SAMPLE = 5
TOP_ACTIONS = 3
SEM_BASIS = "Claude's reading of the transcript"

DIM_LABEL = {"questioning": "asking questions", "active_listening": "listening", "objection_handling": "handling objections",
             "product_explanation": "explaining the course", "pricing_explanation": "explaining the fee",
             "payment_guidance": "guiding the payment", "follow_up_discipline": "following up",
             "customer_engagement": "engaging the lead", "closing": "closing"}
DIM_ACTION = {
    "questioning": "Ask about the lead's background, goal and timeline before pitching.",
    "active_listening": "Repeat the lead's goal back in their words before answering.",
    "objection_handling": "When an objection comes up, ask what is behind it, answer it, then check it is settled.",
    "product_explanation": "Explain the course in two or three sentences tied to the lead's own goal.",
    "pricing_explanation": "State the full fee, the EMI per month and what is included, clearly and once.",
    "payment_guidance": "Walk the lead through paying on the call: amount, link, and confirm the link opened.",
    "follow_up_discipline": "End every call with a dated next step, and keep it.",
    "customer_engagement": "Ask open questions and let the lead talk more than you do.",
    "closing": "Ask for the enrolment directly once the lead's questions are answered."}
SIGNAL_LABEL = {"fee_question": "fee question", "payment_intent": "wants to pay", "emi_interest": "asked about EMI",
                "start_date": "asked the start date", "enrol_intent": "wants to join", "urgency": "in a hurry",
                "decision_maker_ready": "decision maker ready", "documents_or_details": "asked for details",
                "other": "other interest"}
SIGNAL_ACTION = {
    "fee_question": "When a lead asks the fee, quote the amount and the EMI option, then offer the payment link on the call.",
    "payment_intent": "When a lead says they want to pay, send the payment link during the call and stay on until it opens.",
    "emi_interest": "When a lead asks about EMI, give the monthly amount and how to apply, on the same call.",
    "start_date": "When a lead asks the start date, give the next batch date and use it to set a date to pay.",
    "enrol_intent": "When a lead says they want to join, go straight to the payment step: amount, link, date.",
    "urgency": "When a lead is in a hurry, close on the same call: amount, link and a time to pay today.",
    "decision_maker_ready": "When the decision maker is on the call, ask for the decision and the payment step then.",
    "documents_or_details": "When a lead asks for details, send them during the call and fix a time to follow up.",
    "other": "When a lead shows interest, name the next step and its date before ending the call."}
OBJECTION_ACTION = {
    "price": "On price, give the total, the EMI per month and what the course leads to, then ask for a decision date.",
    "emi_or_finance": "When EMI or finance comes up, explain the steps on the call and send the link while they are on the line.",
    "time": "When time is the worry, ask how many hours a week they have and show how the course fits, then fix a start.",
    "value_doubt": "When value is doubted, give one concrete outcome tied to their goal and ask what would convince them.",
    "trust": "When trust is the issue, offer proof they can check for themselves before asking for payment.",
    "family_approval": "When family must approve, offer a short call with that person and fix its time.",
    "course_fit": "When course fit is doubted, ask their goal and match it to a specific part of the course or a better course.",
    "course_unavailable": "When the course they want isn't available, offer the closest course or the next batch and log it.",
    "joined_elsewhere": "When they joined elsewhere, ask why and log the reason; offer another course only if it fits.",
    "job_or_placement": "On jobs and placement, say plainly what support exists and never promise a job.",
    "language": "When language is a concern, explain which language the classes use and answer in the lead's language.",
    "technical": "On technical problems, solve them on the call or book a fixed time with support.",
    "not_interested": "When a lead says not interested, ask one question to find the real reason before ending.",
    "other": "Write the objection down and agree an answer with your team leader."}

UNANSWERED = "lead's questions left unanswered"
PRESSURE = "too much pressure on the lead"
NO_DATED = "no dated next step"
NO_PAY_READY = "no payment step with a ready lead"
PAY_STEP = "payment step taken"
DATED = "dated next step agreed"
ACTIONS = {
    **{DIM_LABEL[d]: a for d, a in DIM_ACTION.items()},
    **{f"missed buying signal: {SIGNAL_LABEL[s]}": a for s, a in SIGNAL_ACTION.items()},
    **{f"objection not handled well: {OBJECTION_LABEL[o]}": a for o, a in OBJECTION_ACTION.items()},
    UNANSWERED: "Note every question the lead asks and answer each before the call ends, or promise a dated answer.",
    PRESSURE: "Ease off: give the lead room to decide and agree a dated follow-up instead of pushing.",
    NO_DATED: "End every real call with a date and time for the next step.",
    NO_PAY_READY: "When a lead is ready, send the payment link during the call and agree the date to pay."}


def _num(x) -> int | float | None:
    x = x.get("score") if isinstance(x, dict) else x
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _semantic(c: dict) -> dict:
    sem = c["sem"]
    q, co, out = sem.get("quality") or {}, sem.get("coaching") or {}, sem.get("outcome") or {}
    strengths, weaknesses = [], []
    for s in sem.get("buying_signals") or []:
        if s.get("caller_acted_on_it") == "no" and s.get("type") in SIGNAL_LABEL:
            weaknesses.append(f"missed buying signal: {SIGNAL_LABEL[s['type']]}")
    for o in sem.get("objections") or []:
        if (lab := OBJECTION_LABEL.get(o.get("category"))) and o.get("handled") in ("no", "partly"):
            weaknesses.append(f"objection not handled well: {lab}")
        elif lab and o.get("handled") == "yes":
            strengths.append(f"objection handled: {lab}")
    if sem.get("unanswered_questions"):
        weaknesses.append(UNANSWERED)
    if (sem.get("tone") or {}).get("caller_pressure") == "excessive":
        weaknesses.append(PRESSURE)
    ready = _num((sem.get("intent") or {}).get("readiness_score"))
    if out.get("payment_step") in ("link_sent", "amount_and_date_agreed"):
        strengths.append(PAY_STEP)
    elif out.get("payment_step") == "none" and ready is not None and ready >= HOT:
        weaknesses.append(NO_PAY_READY)
    if out.get("dated") is True:
        strengths.append(DATED)
    elif c.get("call_class") == S.REAL_CALL and out.get("dated") is False:
        weaknesses.append(NO_DATED)
    return {"source": S.SEMANTIC, "basis": SEM_BASIS, "overall": _num(q.get("overall")),
            "scores": {d: _num(q.get(d)) for d in S.QUALITY_DIMENSIONS},
            "strengths": _dedupe(strengths), "weaknesses": _dedupe(weaknesses),
            "modelStrengths": [x.get("point") for x in co.get("strengths") or [] if x.get("point")],
            "improvements": [{"point": x.get("point"), "betterResponse": x.get("better_response") or None}
                             for x in co.get("improvements") or [] if x.get("point")],
            "missedSignals": [x.get("what") for x in co.get("missed_signals") or [] if x.get("what")],
            "priorityAction": co.get("priority_action") or None, "hints": []}


def call_coaching(call: dict) -> dict | None:
    """Coaching for one call from Claude's reading (labelled by "source" and "basis"); None when Claude has not read
    the call or it was made from a shared login or by automation."""
    if not is_person(call) or not call.get("sem"):
        return None
    return _semantic(call)


def _week(c: dict) -> str | None:
    if t := call_time(c):
        d = t.astimezone(IST).date()
    elif c.get("ist_day"):
        d = date.fromisoformat(c["ist_day"])
    else:
        return None
    return (d - timedelta(days=d.weekday())).isoformat()


def weekly_sample(calls: list[dict], n: int = SAMPLE) -> dict[str, dict[str, list[str]]]:
    """caller_id -> {Monday of the IST week 'YYYY-MM-DD': ids of the caller's n longest REAL_CALLs that week},
    whatever their analysis status (ties: lower call id first)."""
    groups = defaultdict(list)
    for c in calls:
        if c.get("call_class") == S.REAL_CALL and is_person(c) and isinstance(c.get("duration_s"), int) \
                and (w := _week(c)):
            groups[(caller_key(c), w)].append(c)
    out: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for (k, w), cs in sorted(groups.items()):
        out[k][w] = [c["call_id"] for c in sorted(cs, key=lambda c: (-c["duration_s"], c["call_id"]))[:n]]
    return dict(out)


def _ranked(counts: Counter, dims: dict[str, list], keep) -> list[dict]:
    items = [{"item": DIM_LABEL[d], "n": len(v), "avg": round(sum(v) / len(v), 1)} for d, v in dims.items()
             if len(v) >= RECURRING and keep(sum(v) / len(v))]
    items += [{"item": i, "n": n} for i, n in counts.items() if n >= RECURRING]
    return sorted(items, key=lambda x: (-x["n"], x["item"]))


def caller_coaching(calls: list[dict]) -> dict[str, dict]:
    """caller_id -> {"caller", "team", "analysedCalls", "qualityAvg", "strengths": [{"item", "n"(, "avg")}],
    "weaknesses": [...], "actions": [top 3], "weeklySample": {week: [call ids]}} for every person in ``calls``.

    A quality dimension's "n" is the calls it was scored on; any other item's "n" is the calls it appeared in."""
    sample = weekly_sample(calls)
    acc: dict[str, dict] = {}
    for c in calls:
        if not is_person(c):
            continue
        a = acc.get(k := caller_key(c))
        if a is None:
            a = acc[k] = {"caller": c.get("caller_name"), "team": c.get("team"), "t": None, "n": 0, "overall": [],
                          "dims": defaultdict(list), "s": Counter(), "w": Counter(), "pa": []}
        t = call_time(c)
        if t and (a["t"] is None or t > a["t"]):
            a["t"], a["team"] = t, c.get("team")
        if not (cc := call_coaching(c)):
            continue
        a["n"] += 1
        a["s"].update(cc["strengths"])
        a["w"].update(cc["weaknesses"])
        for d, v in (cc["scores"] or {}).items():
            if v is not None:
                a["dims"][d].append(v)
        if cc["overall"] is not None:
            a["overall"].append(cc["overall"])
        if cc["source"] == S.SEMANTIC and cc["priorityAction"]:
            a["pa"].append((t or datetime.min.replace(tzinfo=IST), cc["priorityAction"]))
    out = {}
    for k, a in acc.items():
        weaknesses = _ranked(a["w"], a["dims"], lambda avg: avg <= WEAK)
        actions = _dedupe([ACTIONS[w["item"]] for w in weaknesses if w["item"] in ACTIONS]
                          + [p for _, p in sorted(a["pa"], key=lambda x: x[0], reverse=True)])[:TOP_ACTIONS]
        out[k] = {"caller": a["caller"], "team": a["team"], "analysedCalls": a["n"],
                  "qualityAvg": round(sum(a["overall"]) / len(a["overall"]), 1) if a["overall"] else None,
                  "strengths": _ranked(a["s"], a["dims"], lambda avg: avg >= GOOD), "weaknesses": weaknesses,
                  "actions": actions, "weeklySample": sample.get(k, {})}
    return out


def team_coaching(calls: list[dict], per_caller: dict) -> list[dict]:
    """The snapshot's coachingTeams: per team, gaps shared by 2+ callers ("callers" = how many, "who" = their
    names, "n" = calls), practices from the strongest third of its callers (best average quality score in Claude's readings,
    then most strengths; "from" = their names) and up to 3 priorities. A caller belongs to the team of their
    latest call."""
    team_of, seen = {}, {}
    for c in calls:
        if is_person(c) and (k := caller_key(c)) in per_caller:
            t = call_time(c)
            if k not in seen or (t and (seen[k] is None or t > seen[k])):
                seen[k], team_of[k] = t, c.get("team")
    teams = defaultdict(list)
    for k, team in team_of.items():
        teams[team or "Unassigned"].append(k)
    name = lambda k: per_caller[k].get("caller") or k  # noqa: E731
    out = []
    for team in sorted(teams):
        ks = sorted(teams[team], key=name)
        who, n = defaultdict(list), Counter()
        for k in ks:
            for w in per_caller[k].get("weaknesses") or []:
                who[w["item"]].append(name(k))
                n[w["item"]] += w["n"]
        gaps = sorted(({"item": i, "callers": len(v), "n": n[i], "who": v} for i, v in who.items() if len(v) >= 2),
                      key=lambda g: (-g["callers"], -g["n"], g["item"]))
        ranked = sorted((k for k in ks if per_caller[k].get("strengths")),
                        key=lambda k: (per_caller[k].get("qualityAvg") is None, -(per_caller[k].get("qualityAvg") or 0),
                                       -len(per_caller[k]["strengths"]), name(k)))
        top = ranked[:max(1, -(-len(ks) // 3))]
        p_n, p_from = Counter(), defaultdict(list)
        for k in top:
            for s in per_caller[k]["strengths"]:
                p_n[s["item"]] += s["n"]
                p_from[s["item"]].append(name(k))
        practices = sorted(({"item": i, "n": v, "from": p_from[i]} for i, v in p_n.items()),
                           key=lambda p: (-p["n"], p["item"]))
        priorities = [f"{g['item'][:1].upper()}{g['item'][1:]} ({g['callers']} callers, {g['n']} calls): "
                      f"{ACTIONS.get(g['item'], 'agree a better way to handle it at the next team huddle.')}"
                      for g in gaps[:TOP_ACTIONS]]
        out.append({"team": team, "callers": len(ks), "gaps": gaps, "practices": practices, "priorities": priorities})
    return out
