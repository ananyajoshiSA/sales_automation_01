"""Team analytics for the conversation-intelligence view: one set of metrics for the organisation, each team and
each caller, compared with the organisation (teams), the team (callers) and the previous period.

A call is credited to its caller and to the team stamped on it when it was inventoried, so a caller who moved
teams keeps each call in the team they were in at the time (and appears once per team). Calls by shared logins,
LeadSquared automation and people who are not LeadSquared users stay in organisation totals under their own
pseudo-teams and never among callers. Counts are accumulated in one pass over the calls into one cell per
(team, caller), then summed upwards, so 30 days of calls (~700,000) stay fast.

Enrolments are credited once per view with ``attribution.credit_enrolments``: to the lead owner's team
(``enrolmentsOwner``; the owner LeadSquared shows now, in that person's current group) and to the last person with
an answered call at or before it (``enrolmentsLastCaller``). In each view the organisation's total is the teams'
sum plus the unattributed count. Revenue is not measurable until LeadSquared returns payment records.
"""

from __future__ import annotations

import re
import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import chain

from analytics.convintel import schema as S
from analytics.convintel.attribution import (BOT_KIND, NOT_USER, PERSON, SHARED, UNASSIGNED, Directory,
                                             credit_enrolments, user_name)
from analytics.definitions import ENROLLED, WORKING_DAY_DIALS
from analytics.team_performance import SHORT as TEAM_SHORT_NAMES
from integrations.timeutil import ist_day, utc

TEAM = "team"
PSEUDO_TEAMS = {SHARED: "(shared logins)", BOT_KIND: "(automation)", NOT_USER: "(not a user)"}
NOT_RECORDED = "(not recorded)"
MISSED_WINDOW = timedelta(hours=2)     # a missed inbound call counts once this passes with no answered call back
OVERDUE_AFTER = timedelta(hours=24)    # a promised callback with no answered call since is overdue after this
HIGH_INTENT = 70                       # readiness at or above this (0-100) is a high-intent lead
PAYMENT_STEPS = ("link_sent", "amount_and_date_agreed")
FLAG_TIERS = ("suspect", "pattern")    # "short" alone (under 3 minutes) is not counted as flagged
TALK_CLASSES = (S.REAL_CALL, S.SHORT_CALL)  # answered with a usable duration; UNKNOWN durations are not talk
CLASS_KEYS = {S.REAL_CALL: "real", S.SHORT_CALL: "short", S.NOT_CONNECTED: "notConnected", S.UNKNOWN: "unknown"}
# Metrics compared with the organisation (teams) or the team (callers): *Pct in percentage points, others absolute.
RATE_METRICS = ("connectPct", "realRatePct", "avgRealMin", "realPerWorkingDay", "convPerRealPct",
                "convPerContactedPct", "qualityAvg", "coveragePct", "integrityPct")

_NAME = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}"
# The only label patterns a leader is read from: "Team Bootcamp +Anas", "DSV - UK (Aditya)",
# "US accounting counselors - Sana". A hyphenated word ("Pre-Sales") is not one; the calling report's short
# names ("US Bookkeeping Accounting-Sana" -> "US Bookkeeping (Sana)") cover the hyphenated labels it knows.
_LEADER = (re.compile(rf"\+\s*({_NAME})\s*$"), re.compile(rf"\(\s*({_NAME})\s*\)\s*$"),
           re.compile(rf"\s[-–]\s+({_NAME})\s*$"))


def team_leader(team: str, config: dict | None) -> tuple[str, str]:
    """(leader, source): ``config["team_leaders"][team]`` ("config"), else a name written in the team label or in
    the calling report's short name for it (analytics/team_performance.SHORT, e.g. "US Bookkeeping
    Accounting-Sana" -> "US Bookkeeping (Sana)") ("team name"), else "(not recorded)". LeadSquared has no
    team-leader field, so nothing else is guessed."""
    leader = ((config or {}).get("team_leaders") or {}).get(team)
    if leader and str(leader).strip():
        return str(leader).strip(), "config"
    for label in (team or "", TEAM_SHORT_NAMES.get(team or "", "")):
        for rx in _LEADER:
            if m := rx.search(label):
                return m.group(1), "team name"
    return NOT_RECORDED, "not recorded"


# ------------------------------------------------------------------ per-call readings shared with zipcompare

def _dicts(x) -> list[dict]:
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _measured(score, band) -> int | float | None:
    return None if band == "unclear" or not _num(score) else score


def readiness(call: dict) -> tuple[int | float | None, str | None, str | None]:
    """(score 0-100 or None, band, engine) from the semantic layer when it ran, else the keyword layer; engine is
    "semantic" or "keyword", all None when neither has a reading. Band "unclear" means not measured: the score is
    None, because the semantic schema cannot hold a null score and its 0 there is not a real 0."""
    sem, kw = call.get("sem"), call.get("kw")
    for engine, part in (("semantic", sem.get("intent") if isinstance(sem, dict) else None),
                         ("keyword", kw.get("signals") if isinstance(kw, dict) else None)):
        if isinstance(part, dict) and (part.get("readiness_band") or _num(part.get("readiness_score"))):
            band = part.get("readiness_band")
            return _measured(part.get("readiness_score"), band), band, engine
    return None, None, None


def buying_types(call: dict) -> list[str]:
    """Buying-signal types found on the call (semantic layer when it ran, else keyword layer)."""
    sem, kw = call.get("sem"), call.get("kw")
    if isinstance(sem, dict):
        return sorted({b.get("type") for b in _dicts(sem.get("buying_signals")) if b.get("type")})
    return sorted(set((kw.get("signals") or {}).get("buying") or [])) if isinstance(kw, dict) else []


def objections(call: dict) -> set[str]:
    """Objection categories raised on the call, once each (semantic layer when it ran, else keyword layer)."""
    sem, kw = call.get("sem"), call.get("kw")
    if isinstance(sem, dict):
        cats = {o.get("category") for o in _dicts(sem.get("objections"))}
    elif isinstance(kw, dict):
        cats = set((kw.get("signals") or {}).get("objections") or [])
    else:
        return set()
    return {c if c in S.OBJECTION_CATEGORIES else "other" for c in cats if c}


def payment_ready(call: dict) -> bool:
    """A payment step or a strong intent to pay on the call: a payment link sent, amount and date agreed, a
    payment-ready finding, or (semantic) a strong payment-intent signal / (keyword) a payment-intent phrase."""
    sem, kw = call.get("sem"), call.get("kw")
    if isinstance(sem, dict):
        return ((sem.get("outcome") or {}).get("payment_step") in PAYMENT_STEPS
                or any(f.get("category") == "payment_ready" for f in _dicts(sem.get("findings")))
                or any(b.get("type") == "payment_intent" and b.get("strength") == "strong"
                       for b in _dicts(sem.get("buying_signals"))))
    if isinstance(kw, dict):
        sig = kw.get("signals") or {}
        return (bool(sig.get("payment_step")) or "payment_intent" in (sig.get("buying") or [])
                or any(f.get("category") == "payment_ready" for f in _dicts(kw.get("findings"))))
    return False


def promised_callback(call: dict) -> bool:
    """The caller promised to call back or follow up: semantic commitments by the caller (or a callback finding)
    when the semantic layer ran, else the keyword layer's callback_requested."""
    sem, kw = call.get("sem"), call.get("kw")
    if isinstance(sem, dict):
        return (any(c.get("by") == "caller" for c in _dicts(sem.get("commitments")))
                or any(f.get("category") == "callback_promised" for f in _dicts(sem.get("findings"))))
    return bool((kw.get("signals") or {}).get("callback_requested")) if isinstance(kw, dict) else False


def entity(call: dict) -> tuple[str, str, str]:
    """(team label, kind, account) a call is credited to; kind is person, shared, bot or not_a_user."""
    kind = call.get("caller_kind")
    if kind == PERSON:
        return call.get("team") or UNASSIGNED, PERSON, call.get("caller_id") or call.get("caller_name") or "(unknown)"
    kind = kind if kind in PSEUDO_TEAMS else NOT_USER
    return PSEUDO_TEAMS[kind], kind, call.get("caller_id") or call.get("caller_name") or "(unknown)"


# ------------------------------------------------------------------ accumulation

class _Acc:
    __slots__ = ("n", "dim_sum", "dim_n", "objections", "flags", "days", "real_days", "contacted", "real_leads", "name")

    def __init__(self, name: str | None = None):
        self.n, self.dim_sum, self.dim_n = Counter(), Counter(), Counter()
        self.objections, self.flags = Counter(), Counter()
        self.days: Counter = Counter()       # outbound dials per IST day, for working days
        self.real_days: Counter = Counter()  # real calls per IST day, for real calls per working day
        self.contacted: set[str] = set()
        self.real_leads: set[str] = set()
        self.name = name

    def merge(self, o: _Acc) -> _Acc:
        for mine, theirs in ((self.n, o.n), (self.dim_sum, o.dim_sum), (self.dim_n, o.dim_n),
                             (self.objections, o.objections), (self.flags, o.flags)):
            mine.update(theirs)
        self.contacted |= o.contacted
        self.real_leads |= o.real_leads
        return self


def _answer_index(calls) -> dict[str, list[datetime]]:
    """Answered-call start times per lead, sorted."""
    idx: dict[str, list[datetime]] = defaultdict(list)
    for c in calls:
        if c.get("answered") and c.get("lead_id") and (t := c.get("t") or utc(c.get("start_utc"))):
            idx[c["lead_id"]].append(t)
    for v in idx.values():
        v.sort()
    return idx


def _answered_after(times: list[datetime] | None, t: datetime, within: timedelta | None = None) -> bool:
    if not times:
        return False
    i = bisect_right(times, t)
    return i < len(times) and (within is None or times[i] <= t + within)


def _dedupe(enrolments: list[dict]) -> list[dict]:
    """One enrolment per lead (the earliest), so a duplicated record is never credited twice."""
    first: dict[str, dict] = {}
    rest = []
    for e in enrolments or []:
        lead = e.get("lead_id")
        if not lead:
            rest.append(e)
        elif lead not in first or (e.get("at_utc") or "9999") < (first[lead].get("at_utc") or "9999"):
            first[lead] = e
    return list(first.values()) + rest


class _View:
    """One period's cells keyed (team label, kind, account) plus the enrolment totals."""

    def __init__(self, calls: list[dict], enrolments: list[dict], directory: Directory, enrolled: set[str],
                 now: datetime, idx: dict[str, list[datetime]]):
        self.cells: dict[tuple[str, str, str], _Acc] = {}
        self.now, self.idx = now, idx
        self.latest: dict[str, tuple[datetime, tuple, float | None, bool]] = {}
        enrolments = _dedupe(enrolments)
        self.enrolments, self.unattributed = len(enrolments), Counter()
        enrol_leads = {e.get("lead_id") for e in enrolments}
        on_enrolled = []  # credit_enrolments only needs the calls on enrolled leads; scanning all of them is slow
        for c in calls:
            self._add(c)
            if c.get("lead_id") in enrol_leads:
                on_enrolled.append(c)
        self._working_days()
        self._lead_level(enrolled)
        self._credit(credit_enrolments(enrolments, on_enrolled, directory), directory)

    def _add(self, c: dict) -> None:
        get = c.get
        key = entity(c)
        a = self.cells.get(key)
        if a is None:
            a = self.cells[key] = _Acc(get("caller_name"))
        n = a.n
        n["calls"] += 1
        lead, ans, cls = get("lead_id"), bool(get("answered")), get("call_class")
        t = get("t") or utc(get("start_utc"))
        day = get("ist_day") or ist_day(t) or ""
        if get("direction") == "inbound":
            n["inbound"] += 1
            # any unanswered inbound call, as in team_performance.py and revenue.py; the lead id is needed to
            # look for the answered call back
            if (not ans and lead and t and self.now - t >= MISSED_WINDOW
                    and not _answered_after(self.idx.get(lead), t, MISSED_WINDOW)):
                n["missedInbound"] += 1
        else:
            n["dials"] += 1
            if ans:
                n["answeredOut"] += 1
            a.days[day] += 1
        n[CLASS_KEYS.get(cls, "unknown")] += 1
        if ans:
            n["connected"] += 1
            if lead:
                a.contacted.add(lead)
            d = get("duration_s")
            if cls in TALK_CLASSES and d:
                n["talkSec"] += d
                n["talkN"] += 1
        if cls == S.REAL_CALL:
            n["realSec"] += get("duration_s") or 0
            a.real_days[day] += 1
            if lead:
                a.real_leads.add(lead)
        state = get("transcript_state")
        if get("transcript_expected") or state == S.T_FOUND:
            n["expected"] += 1
        if state == S.T_FOUND:
            n["found"] += 1
        if get("analysis_status") == S.ANALYZED:
            n["analyzed"] += 1
        sem, kw = get("sem"), get("kw")
        if sem or kw:
            a.objections.update(objections(c))
            if isinstance(sem, dict):
                self._quality(a, sem)
            if lead and t and promised_callback(c) and not _answered_after(self.idx.get(lead), t):
                n["overdueFollowUps" if self.now - t >= OVERDUE_AFTER else "pendingCallbacks"] += 1
            if lead and t and (lead not in self.latest or t > self.latest[lead][0]):
                score, ready = readiness(c)[0], payment_ready(c)
                if score is not None or ready:  # a call too thin to read never hides an earlier reading
                    self.latest[lead] = (t, key, score, ready)
        flags = get("flags")
        if flags:
            a.flags.update({f.get("flag") for f in flags if f.get("flag")})
            if any(f.get("tier") in FLAG_TIERS for f in flags):
                n["flagged"] += 1

    def _working_days(self) -> None:
        """A caller-day with 20+ dials, credited to the team the caller made most of that day's dials in. Only
        real calls made on working days count towards real calls per working day, as in
        analytics/team_compare.py, so a caller with one full day and many light ones is not inflated."""
        dials: dict[tuple[str, str], Counter] = defaultdict(Counter)
        people = [(key, a) for key, a in self.cells.items() if key[1] == PERSON]
        for key, a in people:
            for day, k in a.days.items():
                dials[(key[2], day)][key[0]] += k
        working = {cd for cd, teams in dials.items() if sum(teams.values()) >= WORKING_DAY_DIALS}
        for cid, day in working:
            self.cells[(dials[(cid, day)].most_common(1)[0][0], PERSON, cid)].n["workingDays"] += 1
        for key, a in people:
            a.n["realOnWorkingDays"] = sum(k for day, k in a.real_days.items() if (key[2], day) in working)

    def _lead_level(self, enrolled: set[str]) -> None:
        """High-intent and payment-ready leads, each once, judged on its latest call with a reading."""
        for lead, (_t, key, score, ready) in self.latest.items():
            if lead in enrolled:
                continue
            if ready or (score is not None and score >= HIGH_INTENT):
                self.cells[key].n["highIntentUnconverted"] += 1
                self.cells[key].n["paymentReady"] += ready

    @staticmethod
    def _quality(a: _Acc, sem: dict) -> None:
        """Dimension scores as given (null = not shown on the call). ``overall`` cannot be null in the model's
        schema, so like readiness it is not a measurement when the call gave no basis: band "unclear" or no
        skill scored at all."""
        q = sem.get("quality") if isinstance(sem.get("quality"), dict) else {}
        scored = False
        for d in S.QUALITY_DIMENSIONS:
            s = q[d].get("score") if isinstance(q.get(d), dict) else None
            if _num(s):
                a.dim_sum[d] += s
                a.dim_n[d] += 1
                scored = True
        band = (sem.get("intent") or {}).get("readiness_band") if isinstance(sem.get("intent"), dict) else None
        if scored and band != "unclear" and _num(q.get("overall")):
            a.n["qualitySum"] += q["overall"]
            a.n["qualityN"] += 1

    def _cell(self, key: tuple[str, str, str], name: str | None) -> _Acc:
        a = self.cells.get(key)
        if a is None:
            a = self.cells[key] = _Acc(name)
        return a

    def _credit(self, credited: list[dict], directory: Directory) -> None:
        for e in credited:
            if e.get("owner_team"):
                u = directory.find(e.get("owner_id"), e.get("owner_name"))
                self._cell((e["owner_team"], PERSON, u.get("ID") or user_name(u)), user_name(u)).n["enrolmentsOwner"] += 1
            else:
                self.unattributed["ownerAtEnrolment"] += 1
            if e.get("last_caller_id") or e.get("last_caller"):
                key = (e.get("last_caller_team") or UNASSIGNED, PERSON, e.get("last_caller_id") or e.get("last_caller"))
                self._cell(key, e.get("last_caller")).n["enrolmentsLastCaller"] += 1
            else:
                self.unattributed["lastAnsweredCaller"] += 1

    def teams(self) -> dict[str, list[tuple[tuple, _Acc]]]:
        out: dict[str, list[tuple[tuple, _Acc]]] = defaultdict(list)
        for key, a in self.cells.items():
            out[key[0]].append((key, a))
        return out


# ------------------------------------------------------------------ metrics

def _pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


def _cv(values: list[float]) -> float | None:
    """Coefficient of variation (population standard deviation / mean); None below two callers."""
    if len(values) < 2 or not (m := statistics.fmean(values)):
        return None
    return round(statistics.pstdev(values) / m, 2)


def _metrics(a: _Acc, level: str, callers: int | None = None, cv: float | None = None,
             share: float | None = None, enrolments: int | None = None) -> dict:
    """METRICS for one entity. ``level`` is "org", "team" or "caller"; org passes ``enrolments`` (every enrolment
    once, attributed or not). Conversion rates use only enrolments credited to a last caller, also for the org,
    so a team's rate and the org's are on the same footing (enrolments with no answered call by a person in the
    period, often leads worked before it, would otherwise lift only the org's rate)."""
    n = a.n
    dials, conn, real, wd = n["dials"], n["connected"], n["real"], n["workingDays"]
    leads_contacted, leads_real = len(a.contacted), len(a.real_leads)
    credited = n["enrolmentsLastCaller"]
    owner = n["enrolmentsOwner"] if enrolments is None else enrolments
    last = credited if enrolments is None else enrolments
    m = {"calls": n["calls"], "dials": dials, "inbound": n["inbound"], "connected": conn, "realCalls": real,
         "shortCalls": n["short"], "unknownCalls": n["unknown"], "notConnected": n["notConnected"],
         "connectPct": _pct(n["answeredOut"], dials), "realRatePct": _pct(real, conn),
         # answered calls whose duration is all missing -> talk not measurable; no answered calls -> 0 talk
         "talkMin": round(n["talkSec"] / 60, 1) if n["talkN"] or not conn else None,
         "avgRealMin": round(n["realSec"] / real / 60, 1) if real else None,
         "workingDays": wd, "realPerWorkingDay": round(n["realOnWorkingDays"] / wd, 2) if wd else None,
         "leadsContacted": leads_contacted, "leadsReal": leads_real,
         "enrolmentsOwner": owner, "enrolmentsLastCaller": last, "revenue": None, "revenuePerCaller": None,
         "convPerRealPct": _pct(credited, leads_real), "convPerContactedPct": _pct(credited, leads_contacted),
         "missedInbound": n["missedInbound"], "pendingCallbacks": n["pendingCallbacks"],
         "overdueFollowUps": n["overdueFollowUps"], "paymentReady": n["paymentReady"],
         "highIntentUnconverted": n["highIntentUnconverted"],
         "qualityAvg": round(n["qualitySum"] / n["qualityN"], 1) if n["qualityN"] else None,
         "qualityByDim": {d: round(a.dim_sum[d] / a.dim_n[d], 1) if a.dim_n[d] else None for d in S.QUALITY_DIMENSIONS},
         "transcriptsExpected": n["expected"], "transcriptsFound": n["found"], "analyzed": n["analyzed"],
         "coveragePct": _pct(n["analyzed"], n["expected"]),
         "objections": dict(sorted(a.objections.items(), key=lambda kv: (-kv[1], kv[0]))),
         "integrityFlagged": n["flagged"], "integrityPct": _pct(n["flagged"], conn)}
    if level == "caller":
        m["dialsShare"] = share
    else:
        m["callers"], m["workloadCv"] = callers, cv
    return m


def _diff(a, b) -> float | None:
    return None if a is None or b is None else round(a - b, 2)


def _vs(m: dict, ref: dict) -> dict:
    return {k: _diff(m[k], ref[k]) for k in RATE_METRICS}


def _all_metrics(view: _View) -> tuple[dict, dict[str, dict], dict[tuple, dict], dict[str, str]]:
    """(org, {team: metrics}, {person cell key: metrics}, {team: kind}) for one period."""
    teams, team_m, caller_m, kinds = view.teams(), {}, {}, {}
    org, person_dials = _Acc(), Counter()
    for label, cells in teams.items():
        kind = TEAM if cells[0][0][1] == PERSON else cells[0][0][1]
        acc = _Acc()
        for key, a in cells:
            acc.merge(a)
            if key[1] == PERSON and a.n["calls"]:
                person_dials[key[2]] += a.n["dials"]
        org.merge(acc)
        active = [(k, a) for k, a in cells if a.n["calls"]]
        cv = _cv([a.n["dials"] for k, a in active if a.n["dials"]]) if kind == TEAM else None
        team_m[label], kinds[label] = _metrics(acc, "team", len(active), cv), kind
        if kind == TEAM:
            for key, a in cells:
                caller_m[key] = _metrics(a, "caller", share=_pct(a.n["dials"], acc.n["dials"]))
    org_m = _metrics(org, "org", len(person_dials), _cv([d for d in person_dials.values() if d]),
                     enrolments=view.enrolments)
    org_m["enrolmentsUnattributed"] = {"ownerAtEnrolment": view.unattributed["ownerAtEnrolment"],
                                       "lastAnsweredCaller": view.unattributed["lastAnsweredCaller"]}
    return org_m, team_m, caller_m, kinds


def _enrolled(leads: dict, *enrolment_lists) -> set[str]:
    out = {e.get("lead_id") for es in enrolment_lists for e in es or [] if e.get("lead_id")}
    return out | {lid for lid, l in (leads or {}).items() if (l or {}).get("stage") == ENROLLED}


def _notes(org: dict) -> list[str]:
    u = org["enrolmentsUnattributed"]
    return [
        "A real call here means answered and 3+ minutes. This rule is used only in this view; the main "
        "dashboard's 2-minute 'real conversation' is unchanged.",
        "Unique-lead counts (leads contacted, leads with a real call) do not add up across teams or callers: a lead "
        "called by two teams counts in each. Organisation totals count each lead once.",
        "Each enrolment is credited once in each of two views: to the lead owner's team (the owner LeadSquared "
        "shows for the lead now, in that person's current group; LeadSquared keeps no team history) and to the "
        "last person whose answered call on the lead came at or before the enrolment (only calls in this period "
        f"are searched). Organisation totals count every enrolment once; {u['ownerAtEnrolment']} could not be "
        f"credited to an owner's team (shared login, automation, not a user or no owner) and "
        f"{u['lastAnsweredCaller']} had no answered call by a person before it. Conversion rates, the "
        "organisation's included, use only enrolments credited to a last caller, so they compare fairly.",
        "Calls count for the team the caller was in when the call was recorded, so a caller who moved teams "
        "appears once per team.",
        "Shared logins, automation and callers who are not LeadSquared users count in organisation totals and "
        "coverage under their own rows, never among callers.",
        f"A working day is a caller-day with {WORKING_DAY_DIALS}+ outbound dials; a team's working days are the "
        "sum over its callers. Real calls per working day counts the real calls made on those days. Talk time "
        "counts answered calls with a recorded duration.",
        "Missed inbound: an inbound call nobody answered with no answered call to that lead within 2 hours, counted "
        "once 2 hours have passed. Pending callbacks: the caller promised a callback and nobody has reached the "
        "lead since, under 24 hours ago; overdue follow-ups: the same after 24 hours. All three look only at calls "
        "in this period.",
        f"High intent: the lead's latest call with a readiness reading has readiness {HIGH_INTENT}+ or a payment "
        "step (a call too short to read, rated 'unclear', is skipped); payment ready: a payment link sent, amount "
        "and date agreed or a clear intent to pay. Enrolled leads are left out. Each lead counts once, for the "
        "caller of that latest call.",
        "Quality scores come from the model (semantic) layer only and stay blank until it has run. A call that "
        "gave no basis for a score (readiness 'unclear' or no skill scored) is left out of the overall average.",
        "Possibly-not-real flags mean 'needs review', not proof; flagged % is flagged calls over answered calls.",
        "Revenue is not measurable yet: LeadSquared has returned no payment records, so revenue and revenue per "
        "caller are blank and enrolments are shown instead.",
    ]


def build_views(calls: list[dict], users: list[dict], leads: dict, enrolments: list[dict], now: datetime,
                prior: dict | None = None, config: dict | None = None) -> dict:
    """{"org": METRICS, "teams": [...], "callers": [...], "notes": [str]} for the period's ``calls``.
    ``prior`` = {"calls", "enrolments"} of the previous period of equal length (each entity's "prior")."""
    directory = Directory(users or [])
    prior_calls = list((prior or {}).get("calls") or [])
    enrolled = _enrolled(leads, enrolments, (prior or {}).get("enrolments"))
    cur = _View(calls, enrolments, directory, enrolled, now, _answer_index(calls))
    org, team_m, caller_m, kinds = _all_metrics(cur)
    if prior is not None:  # the prior period's follow-ups may have been answered in this period
        p_org, p_team, p_caller, _ = _all_metrics(_View(prior_calls, prior.get("enrolments") or [], directory,
                                                        enrolled, now, _answer_index(chain(prior_calls, calls))))
    teams = []
    for label, m in team_m.items():
        leader, source = team_leader(label, config) if kinds[label] == TEAM else (NOT_RECORDED, "not recorded")
        p = None if prior is None else p_team.get(label) or _metrics(_Acc(), "team", 0, None)
        teams.append({"team": label, "kind": kinds[label], "teamLeader": leader, "teamLeaderSource": source, **m,
                      "vsOrg": _vs(m, org), "prior": p})
    teams.sort(key=lambda t: (t["kind"] != TEAM, -t["realCalls"], t["team"]))
    callers = []
    for key, m in caller_m.items():
        u = directory.find(key[2], key[2])
        p = None if prior is None else p_caller.get(key) or _metrics(_Acc(), "caller")
        callers.append({"callerId": key[2], "caller": user_name(u) if u else cur.cells[key].name or key[2],
                        "team": key[0], "kind": PERSON, **m, "vsTeam": _vs(m, team_m[key[0]]), "prior": p,
                        "integrity": {"flagged": m["integrityFlagged"], "flaggedPct": m["integrityPct"],
                                      "byFlag": dict(cur.cells[key].flags.most_common())}})
    callers.sort(key=lambda c: (c["team"], -c["realCalls"], c["caller"]))
    org["prior"] = None if prior is None else p_org
    return {"org": org, "teams": teams, "callers": callers, "notes": _notes(org)}
