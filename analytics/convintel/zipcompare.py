"""Our reading of each call against Zipteams' intent rating, kept independent: Zipteams is one input, never the answer.

Our view is the call's readiness score (0-100) from Claude's reading of the transcript, in three coarse levels:
high >= 65, medium 35-64, low < 35. Zipteams' intent maps HIGH -> high, MODERATE -> medium, NEUTRAL / LOW -> low.
A call with our analysis but no Zipteams rating (most teams have no Zipteams notes) has no baseline, which is
never a disagreement; a call our analysis rates "unclear" (too little talk) is not compared either. Every disagreement becomes a finding (category "zip_disagreement") for review, and
the benchmark shows how often each side's high-intent leads later enrolled.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from analytics.convintel.attribution import PERSON
from analytics.convintel.team import TEAM, buying_types, entity, objections, readiness
from analytics.definitions import ENROLLED
from integrations.timeutil import ist_day, utc

ZIP_LEVELS = {"HIGH": "high", "MODERATE": "medium", "NEUTRAL": "low", "LOW": "low"}
OUR_HIGH, OUR_MEDIUM = 65, 35
EXAMPLES = 20
FINDINGS_CAP = 200          # in the snapshot; disagreements() returns them all
SMALL_SAMPLE = 30           # fewer leads than this: the enrolled share is not reliable
ENGINE_LABEL = {"semantic": "Claude's reading of the transcript"}


def our_level(score: float | None) -> str | None:
    if score is None:
        return None
    return "high" if score >= OUR_HIGH else "medium" if score >= OUR_MEDIUM else "low"


def _zip_intent(call: dict) -> str | None:
    z = call.get("zip")
    return (str(z.get("intent") or "").upper() or None) if isinstance(z, dict) else None


def _judge(call: dict) -> dict | None:
    """Both levels for a call with a Zipteams rating and a clear reading of ours; None otherwise."""
    zl = ZIP_LEVELS.get(_zip_intent(call) or "")
    score, _band, engine = readiness(call)
    if zl is None or score is None:  # no Zipteams rating, or ours not measured ("unclear")
        return None
    ours = our_level(score)
    signals = buying_types(call)
    kind = ("unsupported_hot" if zl == "high" and ours == "low" and not signals
            else "missed_hot" if zl == "low" and ours == "high" else None)
    return {"zip": zl, "ours": ours, "score": score, "engine": engine, "signals": signals, "kind": kind}


def zip_agrees(call: dict) -> bool | None:
    """True / False when Zipteams and our reading put the call at the same coarse level; None when there is no
    Zipteams rating, no analysis of ours, or ours is unclear."""
    j = _judge(call)
    return None if j is None else j["zip"] == j["ours"]


def _row(c: dict) -> dict:
    t = c.get("t") or utc(c.get("start_utc"))
    return {"callId": c.get("call_id"), "leadId": c.get("lead_id"), "callerId": c.get("caller_id"),
            "caller": c.get("caller_name"), "team": entity(c)[0], "day": c.get("ist_day") or ist_day(t)}


def _plain(xs) -> str:
    return ", ".join(x.replace("_", " ") for x in xs) or "none"


def _finding(c: dict, j: dict) -> dict:
    zi, who = _zip_intent(c), ENGINE_LABEL[j["engine"]]
    if j["kind"] == "unsupported_hot":
        why = (f"Zipteams rated intent HIGH, but {who} puts readiness at {j['score']}/100 (low) and finds no buying "
               "signal. Zipteams is one input, not the answer.")
        act = ("Team leader: listen to this call before treating the lead as hot. Caller: on the next call ask "
               "about fees and start date to check the interest is real.")
    elif j["kind"] == "missed_hot":
        why = (f"Zipteams rated intent {zi} (low), but {who} puts readiness at {j['score']}/100 (high), from: "
               f"{_plain(j['signals'])}. A ready lead may be getting too little attention.")
        act = "Call this lead back within a day and move to a payment step: share the fee and link, agree a date."
    else:
        why = f"Zipteams rated intent {zi} ({j['zip']}); {who} puts readiness at {j['score']}/100 ({j['ours']})."
        act = "Team leader: review this call and decide which reading is right before planning the next step."
    return {**_row(c), "category": "zip_disagreement", "excerpt": "", "offset": -1,
            "confidence": "medium" if j["engine"] == "semantic" else "low",
            "reasoning": why + " This needs review; it is not proof either way.", "recommended_action": act}


def disagreements(calls: list[dict]) -> list[dict]:
    """A finding-style row for every call where Zipteams and our reading disagree, by call id."""
    out = [_finding(c, j) for c in calls if (j := _judge(c)) and j["zip"] != j["ours"]]
    return sorted(out, key=lambda r: str(r["callId"]))


def _enrolment_times(enrolments: list[dict]) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for e in enrolments or []:
        if e.get("lead_id") and (at := utc(e.get("at_utc"))) and (e["lead_id"] not in out or at < out[e["lead_id"]]):
            out[e["lead_id"]] = at
    return out


def _share(first: dict[str, datetime], enrol_at: dict[str, datetime], leads: dict) -> dict:
    """Of the leads first rated high at ``first[lead]``, how many enrolled at or after that. Leads already
    enrolled before it (or shown as enrolled with no first-time enrolment after it) are left out and counted."""
    n = k = already = 0
    for lead, t in first.items():
        at = enrol_at.get(lead)
        if at is not None and at >= t:
            n, k = n + 1, k + 1
        elif at is not None or ((leads or {}).get(lead) or {}).get("stage") == ENROLLED:
            already += 1
        else:
            n += 1
    return {"leads": n, "enrolled": k, "pct": round(100 * k / n, 1) if n else None, "smallSample": n < SMALL_SAMPLE,
            "alreadyEnrolled": already}


def _first(store: dict[str, datetime], lead: str | None, t: datetime | None) -> None:
    if lead and t and (lead not in store or t < store[lead]):
        store[lead] = t


def compare(calls: list[dict], enrolments: list[dict], leads: dict) -> dict:
    """The snapshot "zip" section."""
    tot, engines = Counter(), Counter()
    teams: dict[str, Counter] = defaultdict(Counter)
    team_kind: dict[str, str] = {}
    zip_high, our_high, our_high_all = {}, {}, {}
    found: list[tuple[dict, dict]] = []
    for c in calls:
        intent = _zip_intent(c)
        score, _band, engine = readiness(c)
        if engine is None:
            tot["zipOnly"] += intent in ZIP_LEVELS
            continue
        label, kind = entity(c)[:2]
        t = c.get("t") or utc(c.get("start_utc"))
        row = teams[label]
        team_kind[label] = TEAM if kind == PERSON else kind
        tot["analysed"] += 1
        row["analysed"] += 1
        if our_level(score) == "high":
            _first(our_high_all, c.get("lead_id"), t)
        if intent not in ZIP_LEVELS:
            tot["noBaseline"] += 1
            row["noBaseline"] += 1
            tot["zipNoIntent"] += intent is not None
            continue
        j = _judge(c)
        if j is None:
            tot["ourUnclear"] += 1
            row["ourUnclear"] += 1
            continue
        tot["compared"] += 1
        row["compared"] += 1
        engines[j["engine"]] += 1
        if j["zip"] == "high":
            _first(zip_high, c.get("lead_id"), t)
        if j["ours"] == "high":
            _first(our_high, c.get("lead_id"), t)
        res = "agree" if j["zip"] == j["ours"] else "disagree"
        tot[res] += 1
        row[res] += 1
        if res == "disagree":
            found.append((c, j))
            if j["kind"]:
                key = "unsupportedHot" if j["kind"] == "unsupported_hot" else "missedHot"
                tot[key] += 1
                row[key] += 1
    found.sort(key=lambda cj: str(cj[0].get("call_id")))
    examples = [{**_row(c), "zipIntent": _zip_intent(c), "zipLevel": j["zip"], "ourLevel": j["ours"],
                 "ourReadiness": j["score"], "engine": j["engine"], "kind": j["kind"] or "level_differs",
                 "signals": j["signals"], "objections": sorted(objections(c))} for c, j in found[:EXAMPLES]]
    findings = [_finding(c, j) for c, j in found[:FINDINGS_CAP]]
    by_team = [_team_row(label, team_kind[label], r) for label, r in teams.items()]
    by_team.sort(key=lambda r: (-r["compared"], r["team"]))
    no_base = [r["team"] for r in by_team if not r["baseline"]]
    enrol_at = _enrolment_times(enrolments)
    keys = ("compared", "agree", "disagree", "noBaseline", "zipNoIntent", "ourUnclear", "unsupportedHot", "missedHot",
            "zipOnly", "analysed")
    agree_pct = round(100 * tot["agree"] / tot["compared"], 1) if tot["compared"] else None
    return {**{k: tot[k] for k in keys}, "agreePct": agree_pct,
            "byEngine": {"semantic": engines["semantic"]},
            "byTeam": by_team, "examples": examples, "examplesTotal": len(found),
            "findings": findings, "findingsTotal": len(found),
            "benchmark": {"zipHigh": _share(zip_high, enrol_at, leads), "ourHigh": _share(our_high, enrol_at, leads),
                          "ourHighAllCalls": _share(our_high_all, enrol_at, leads),
                          "note": "Share of high-intent leads that later enrolled (first-time enrolments in this "
                                  "period's data). 'ourHigh' uses only calls Zipteams also rated, so both sides "
                                  "judge the same calls; 'ourHighAllCalls' uses every analysed call. Under "
                                  f"{SMALL_SAMPLE} leads the share is not reliable (smallSample)."},
            "notes": _notes(tot, no_base)}


def _team_row(label: str, kind: str, r: Counter) -> dict:
    baseline = r["compared"] + r["ourUnclear"] > 0
    return {"team": label, "kind": kind, "analysed": r["analysed"], "compared": r["compared"], "agree": r["agree"],
            "disagree": r["disagree"], "agreePct": round(100 * r["agree"] / r["compared"], 1) if r["compared"] else None,
            "noBaseline": r["noBaseline"], "ourUnclear": r["ourUnclear"], "unsupportedHot": r["unsupportedHot"],
            "missedHot": r["missedHot"], "baseline": baseline,
            "note": None if baseline else "No Zipteams rating on this team's analysed calls, so there is nothing to "
                                          "compare (this is not a disagreement)."}


def _notes(tot: Counter, no_base: list[str]) -> list[str]:
    out = [f"Zipteams intent is one input, never the answer. Levels: ours high {OUR_HIGH}+, medium {OUR_MEDIUM}-"
           f"{OUR_HIGH - 1}, low under {OUR_MEDIUM} (readiness 0-100); Zipteams HIGH = high, MODERATE = medium, "
           "NEUTRAL or LOW = low.",
           "A disagreement means the call needs a listen; it does not say which side is right.",
           f"{tot['noBaseline']} analysed calls have no Zipteams rating (no baseline, not a disagreement); "
           f"{tot['ourUnclear']} were too thin for our own reading and were not compared."]
    if no_base:
        out.append("Teams with no Zipteams baseline: " + ", ".join(no_base) + ".")
    return out
