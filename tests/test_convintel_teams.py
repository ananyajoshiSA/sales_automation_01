"""Team analytics and the Zipteams comparison (analytics/convintel/team.py, zipcompare.py) on hand-built calls."""

import itertools
import time
from datetime import datetime, timedelta, timezone

import pytest

from analytics.convintel import schema as S
from analytics.convintel.classify import classify
from analytics.convintel.store import ts
from analytics.convintel.team import build_views, readiness, team_leader
from analytics.convintel.zipcompare import EXAMPLES, compare, disagreements, zip_agrees
from analytics.definitions import ENROLLED
from integrations.timeutil import ist_day

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)   # 17:30 IST
ALPHA, BETA = "Team Alpha +Neel", "Team Beta"
USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "Rao", "MemberOfGroups": [ALPHA]},
         {"ID": "u2", "FirstName": "Ravi", "LastName": "Iyer", "MemberOfGroups": [BETA]},
         {"ID": "u3", "FirstName": "Meena", "LastName": "Das", "MemberOfGroups": [ALPHA]},
         {"ID": "rj", "FirstName": "Rinku", "LastName": "Jhala", "MemberOfGroups": ["Admins"]},
         {"ID": "adm", "FirstName": "Admin", "MemberOfGroups": ["Admins"]},
         {"ID": "sys", "FirstName": "System", "MemberOfGroups": []}]
WHO = {"u1": ("Asha Rao", "person", ALPHA), "u2": ("Ravi Iyer", "person", BETA), "u3": ("Meena Das", "person", ALPHA),
       "rj": ("Rinku Jhala", "shared", "Admins"), "adm": ("Admin", "shared", "Admins"),
       "sys": ("System", "bot", "Unassigned"), "x9": ("Guest Dialer", "not_a_user", "Not a user")}
_ids = itertools.count()


def band(score):
    return "hot" if score >= 70 else "warm" if score >= 50 else "cool" if score >= 30 else "cold"


def sem(score=20, bnd=None, overall=6, dims=None, objections=(), buying=(), commitments=(), payment_step="none",
        findings=()):
    q = {d: {"score": (dims or {}).get(d), "evidence": "", "note": ""} for d in S.QUALITY_DIMENSIONS}
    return {"intent": {"explicit_intent": "", "readiness_score": score, "readiness_band": bnd or band(score)},
            "quality": {**q, "overall": overall},
            "objections": [{"category": c, "excerpt": "", "handled": "no", "caller_response_excerpt": "", "note": ""}
                           for c in objections],
            "buying_signals": [{"type": b if isinstance(b, str) else b[0], "excerpt": "",
                                "strength": "moderate" if isinstance(b, str) else b[1], "caller_acted_on_it": "unclear"}
                               for b in buying],
            "commitments": [{"by": by, "what": "call back", "due_text": "", "excerpt": ""} for by in commitments],
            "outcome": {"next_step_agreed": False, "next_step": "", "dated": False, "payment_step": payment_step,
                        "course_discussed": ""},
            "integrity": {"real_conversation": "yes", "flags": [], "reason": ""},
            "findings": [{"category": f, "excerpt": "", "offset": -1, "confidence": "medium", "reasoning": "",
                          "recommended_action": ""} for f in findings],
            "summary": "Synthetic call."}


def call(user="u1", at=None, status="Answered", dur=200, direction="outbound", lead="L1", team=None, s=None,
         z=None, flags=None, state=None, **extra):
    name, kind, home = WHO[user]
    at = at or NOW - timedelta(hours=6)
    cls = classify(status, dur)[0]
    expected = int(cls != S.NOT_CONNECTED)
    state = state or (S.T_FOUND if s else S.T_NOT_LOOKED_UP if expected else S.T_NOT_EXPECTED)
    status_ = S.ANALYZED if s else S.NO_TRANSCRIPT_EXPECTED if not expected else S.PENDING_ANALYSIS
    c = {"call_id": f"c{next(_ids):05d}", "lead_id": lead, "direction": direction, "call_status": status,
         "answered": int(status == "Answered"), "start_utc": ts(at), "ist_day": ist_day(at), "duration_s": dur,
         "call_class": cls, "caller_id": user, "caller_name": name, "caller_kind": kind, "team": team or home,
         "transcript_expected": expected, "transcript_state": state, "analysis_status": status_, "sem": s,
         "zip": z, "t": at, **extra}
    if flags is not None:
        c["flags"] = flags
    return c


def views(calls, enrolments=(), leads=None, **opts):
    return build_views(calls, USERS, leads or {}, list(enrolments), NOW, **opts)


def caller(v, cid, team=None):
    rows = [c for c in v["callers"] if c["callerId"] == cid and (team is None or c["team"] == team)]
    assert len(rows) == 1, rows
    return rows[0]


def team(v, label):
    return next(t for t in v["teams"] if t["team"] == label)


def enrol(lead, at, owner="u1"):
    return {"lead_id": lead, "at_utc": ts(at), "ist_day": ist_day(at), "owner_id": owner,
            "owner_name": WHO[owner][0], "set_by": "Admin"}


# ------------------------------------------------------------------ team leader

@pytest.mark.parametrize("label,config,expected", [
    ("Team Bootcamp +Anas", None, ("Anas", "team name")),
    ("DSV - UK (Aditya)", None, ("Aditya", "team name")),
    ("US accounting counselors - Sana", None, ("Sana", "team name")),
    ("DSV-Domestic-(Shivam Sharma)", None, ("Shivam Sharma", "team name")),
    ("US Accounting -Closures - Deepanshi", None, ("Deepanshi", "team name")),
    ("Elite Changemakers", None, ("(not recorded)", "not recorded")),
    ("DSV - UK", None, ("(not recorded)", "not recorded")),              # "UK" is not a name
    ("US Bookkeeping Accounting-Sana", None, ("Sana", "team name")),     # the calling report's short name
    ("Pre-Sales Accounts-Riya", None, ("(not recorded)", "not recorded")),  # hyphenated word: not guessed
    ("(shared logins)", None, ("(not recorded)", "not recorded")),
    ("Elite Changemakers", {"team_leaders": {"Elite Changemakers": "Priya"}}, ("Priya", "config")),
    ("Team Bootcamp +Anas", {"team_leaders": {"Team Bootcamp +Anas": "Jyoti"}}, ("Jyoti", "config")),
    ("Team Beta", {"team_leaders": {"Team Alpha": "Neel"}}, ("(not recorded)", "not recorded")),
])
def test_team_leader(label, config, expected):
    assert team_leader(label, config) == expected


# ------------------------------------------------------------------ counts and rates

def test_counts_rates_and_minutes():
    v = views([call(dur=240, lead="L1"), call(dur=60, lead="L2"), call(status="NotAnswered", dur=0, lead="L3"),
               call(status="CallFailure", dur=None, lead="L4"), call(direction="inbound", dur=300, lead="L5")])
    a = caller(v, "u1")
    assert (a["calls"], a["dials"], a["inbound"], a["connected"]) == (5, 4, 1, 3)
    assert (a["realCalls"], a["shortCalls"], a["notConnected"], a["unknownCalls"]) == (2, 1, 2, 0)
    assert a["connectPct"] == 50.0 and a["realRatePct"] == 66.7
    assert a["talkMin"] == 10.0 and a["avgRealMin"] == 4.5
    assert (a["leadsContacted"], a["leadsReal"]) == (3, 2)
    assert a["dialsShare"] == 100.0 and "callers" not in a and "workloadCv" not in a
    assert v["org"]["callers"] == 1 and "dialsShare" not in v["org"]


def test_real_call_is_three_minutes_in_this_module():
    v = views([call(dur=179, lead="L1"), call(dur=180, lead="L2"), call(dur=120, lead="L3")])
    a = caller(v, "u1")
    assert (a["realCalls"], a["shortCalls"], a["leadsReal"]) == (1, 2, 1)    # 2 min is real elsewhere, not here
    assert a["avgRealMin"] == 3.0 and a["realRatePct"] == 33.3
    assert any("3+ min" in n and "2-minute" in n for n in v["notes"])


def test_zero_dials_and_no_answered_calls_give_none_rates():
    v = views([call(user="u3", direction="inbound", status="Missed", dur=0, lead="L9", at=NOW - timedelta(minutes=30)),
               call(user="u2", dur=None, lead="L8")])
    m = caller(v, "u3")
    assert m["dials"] == 0 and m["connected"] == 0
    for k in ("connectPct", "realRatePct", "avgRealMin", "realPerWorkingDay", "convPerRealPct", "convPerContactedPct",
              "qualityAvg", "coveragePct", "integrityPct", "dialsShare"):
        assert m[k] is None, k
    assert m["talkMin"] == 0.0            # no answered calls: no talk, measured
    r = caller(v, "u2")                   # answered but the duration is missing: talk is not measurable
    assert r["unknownCalls"] == 1 and r["connected"] == 1 and r["talkMin"] is None and r["realRatePct"] == 0.0
    assert team(v, ALPHA)["workloadCv"] is None and team(v, ALPHA)["connectPct"] is None


def test_caller_in_two_teams_keeps_stamped_team():
    v = views([call(user="u2", team=ALPHA, lead="L1", at=NOW - timedelta(days=3)),
               call(user="u2", lead="L2", at=NOW - timedelta(hours=3)), call(user="u1", lead="L3")])
    old, new = caller(v, "u2", ALPHA), caller(v, "u2", BETA)
    assert old["realCalls"] == 1 and new["realCalls"] == 1 and old["caller"] == "Ravi Iyer"
    assert team(v, ALPHA)["realCalls"] == 2 and team(v, ALPHA)["callers"] == 2
    assert team(v, BETA)["realCalls"] == 1 and team(v, BETA)["callers"] == 1
    assert v["org"]["callers"] == 2 and v["org"]["realCalls"] == 3   # one person, counted once in the org


def test_shared_bot_and_non_users_stay_in_org_but_never_among_callers():
    busy = [call(user="rj", status="NotAnswered", dur=0, lead=f"S{i}") for i in range(25)]   # a shared login's "day"
    v = views([call(user="u1", lead="L1"), call(user="rj", lead="L2"), call(user="adm", lead="L5"),
               call(user="sys", lead="L3", dur=30), call(user="x9", status="NotAnswered", dur=0, lead="L4"), *busy])
    assert {c["callerId"] for c in v["callers"]} == {"u1"}
    assert v["org"]["calls"] == 30 and v["org"]["realCalls"] == 3 and v["org"]["callers"] == 1
    assert v["org"]["workingDays"] == 0 and v["org"]["workloadCv"] is None   # shared logins never make a working day
    kinds = {t["team"]: t["kind"] for t in v["teams"]}
    assert kinds == {ALPHA: "team", "(shared logins)": "shared", "(automation)": "bot", "(not a user)": "not_a_user"}
    assert v["teams"][0]["team"] == ALPHA                                   # real teams first
    shared = team(v, "(shared logins)")                                    # Rinku Jhala and Admin
    assert shared["teamLeader"] == "(not recorded)" and shared["realCalls"] == 2 and shared["callers"] == 2
    assert shared["realPerWorkingDay"] is None and shared["workloadCv"] is None and shared["workingDays"] == 0
    assert sum(t["calls"] for t in v["teams"]) == v["org"]["calls"]
    assert sum(t["realCalls"] for t in v["teams"]) == v["org"]["realCalls"]


# ------------------------------------------------------------------ enrolments

def test_enrolments_credited_once_per_view_with_unattributed():
    at = NOW - timedelta(hours=2)
    calls = [call(user="u1", lead="L1", at=at - timedelta(days=2)),
             call(user="u2", lead="L1", at=at - timedelta(hours=5), dur=90),        # last answered before enrolment
             call(user="u3", team=BETA, lead="L1", at=at + timedelta(minutes=30)),  # after it: not credited
             call(user="u1", lead="L7", at=at - timedelta(hours=1)),
             call(user="u2", lead="L8", status="NotAnswered", dur=0)]
    enrolments = [enrol("L1", at, "u1"), enrol("L1", at + timedelta(hours=1), "u1"),   # duplicate record
                  enrol("L7", at, "u3"), enrol("L9", at, "rj")]
    v = views(calls, enrolments)
    alpha, beta, org = team(v, ALPHA), team(v, BETA), v["org"]
    assert alpha["enrolmentsOwner"] == 2 and beta["enrolmentsOwner"] == 0       # L1 (Asha), L7 (Meena)
    assert alpha["enrolmentsLastCaller"] == 1 and beta["enrolmentsLastCaller"] == 1   # L7 Asha, L1 Ravi
    assert org["enrolmentsOwner"] == org["enrolmentsLastCaller"] == 3
    assert org["enrolmentsUnattributed"] == {"ownerAtEnrolment": 1, "lastAnsweredCaller": 1}  # L9: shared owner, no call
    owners = sum(t["enrolmentsOwner"] for t in v["teams"])
    lasts = sum(c["enrolmentsLastCaller"] for c in v["callers"])
    assert owners + 1 == org["enrolmentsOwner"] and lasts + 1 == org["enrolmentsLastCaller"]
    meena = caller(v, "u3", ALPHA)              # owner with no calls of her own in this team still gets her credit
    assert meena["calls"] == 0 and meena["enrolmentsOwner"] == 1 and meena["caller"] == "Meena Das"
    ravi = caller(v, "u2")
    assert ravi["enrolmentsLastCaller"] == 1 and ravi["leadsReal"] == 0 and ravi["convPerRealPct"] is None
    assert ravi["leadsContacted"] == 1 and ravi["convPerContactedPct"] == 100.0
    assert any("unattributed" in n or "could not be credited" in n for n in v["notes"])


def _reconciles(org, teams, callers):
    u = org["enrolmentsUnattributed"]
    for metric, view in (("enrolmentsOwner", "ownerAtEnrolment"), ("enrolmentsLastCaller", "lastAnsweredCaller")):
        assert sum(t[metric] for t in teams) + u[view] == org[metric], metric
        assert sum(c[metric] for c in callers) + u[view] == org[metric], metric


def test_enrolment_totals_reconcile_in_both_views_and_the_prior_period():
    at = NOW - timedelta(hours=2)
    calls = [call(user="u1", lead="E1", at=at - timedelta(hours=3)),
             call(user="u2", team=ALPHA, lead="E2", at=at - timedelta(days=2)),   # Ravi while still in Alpha
             call(user="u2", lead="E3", at=at - timedelta(hours=1)),
             call(user="rj", lead="E4", at=at - timedelta(hours=1)),              # only a shared login reached E4
             call(user="sys", lead="E5", at=at - timedelta(hours=1), dur=40)]     # only automation reached E5
    enrolments = [enrol("E1", at, "u1"), enrol("E2", at, "u2"), enrol("E3", at, "adm"), enrol("E4", at, "u3"),
                  enrol("E5", at, "sys"), enrol("E6", at, "u2")]                  # E6: never called this period
    prior = {"calls": [call(user="u3", lead="P1", at=NOW - timedelta(days=8))],
             "enrolments": [enrol("P1", NOW - timedelta(days=7, hours=20), "rj"),
                            enrol("P2", NOW - timedelta(days=7), "u3")]}
    v = views(calls, enrolments, prior=prior)
    org = v["org"]
    assert org["enrolmentsOwner"] == org["enrolmentsLastCaller"] == 6
    assert org["enrolmentsUnattributed"] == {"ownerAtEnrolment": 2, "lastAnsweredCaller": 3}   # Admin + bot; E4-E6
    _reconciles(org, v["teams"], v["callers"])
    _reconciles(org["prior"], [t["prior"] for t in v["teams"]], [c["prior"] for c in v["callers"]])
    assert org["prior"]["enrolmentsUnattributed"] == {"ownerAtEnrolment": 1, "lastAnsweredCaller": 1}
    assert (team(v, ALPHA)["enrolmentsOwner"], team(v, BETA)["enrolmentsOwner"]) == (2, 2)
    assert (team(v, ALPHA)["enrolmentsLastCaller"], team(v, BETA)["enrolmentsLastCaller"]) == (2, 1)
    assert caller(v, "u2", ALPHA)["enrolmentsLastCaller"] == 1 and caller(v, "u2", BETA)["enrolmentsOwner"] == 2
    for t in v["teams"]:
        if t["kind"] != "team":
            assert t["enrolmentsOwner"] == t["enrolmentsLastCaller"] == 0
    # the org's rate counts only enrolments credited to a caller, as the teams' rates do: 3 of 4 real-call leads
    assert org["leadsReal"] == 4 and org["convPerRealPct"] == 75.0
    assert team(v, ALPHA)["vsOrg"]["convPerRealPct"] == round(team(v, ALPHA)["convPerRealPct"] - 75.0, 2)


# ------------------------------------------------------------------ working days, workload, shares

def test_working_days_ist_and_workload_cv():
    d1 = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)                    # 11:30 IST on 7 Oct
    asha = [call(status="NotAnswered", dur=0, at=d1 + timedelta(minutes=i), lead=f"A{i}") for i in range(19)]
    asha.append(call(status="NotAnswered", dur=0, at=datetime(2026, 10, 7, 18, 35, tzinfo=timezone.utc), lead="A99"))
    asha += [call(status="NotAnswered", dur=0, at=d1 + timedelta(days=1, minutes=i), lead=f"B{i}") for i in range(19)]
    asha.append(call(dur=400, at=d1 + timedelta(days=1, hours=2), lead="B99"))
    asha.append(call(direction="inbound", dur=300, at=d1 + timedelta(hours=1), lead="A98"))  # real call, light day
    meena = [call(user="u3", status="NotAnswered", dur=0, at=d1 + timedelta(minutes=i), lead=f"M{i}") for i in range(13)]
    v = views(asha + meena + [call(user="u2", lead="R1")])
    a = caller(v, "u1")
    # 19 dials on 7 Oct IST; the 00:05 IST dial belongs to 8 Oct, which then has 20 + 1 real call = 21 dials.
    # Only the real call made on the working day counts per working day.
    assert a["dials"] == 40 and a["workingDays"] == 1 and a["realCalls"] == 2 and a["realPerWorkingDay"] == 1.0
    assert caller(v, "u3")["workingDays"] == 0 and caller(v, "u3")["realPerWorkingDay"] is None
    alpha = team(v, ALPHA)
    assert alpha["workingDays"] == 1 and alpha["callers"] == 2
    assert alpha["workloadCv"] == round(13.5 / 26.5, 2)                       # dials 40 and 13
    assert a["dialsShare"] == 75.5 and caller(v, "u3")["dialsShare"] == 24.5
    assert team(v, BETA)["workloadCv"] is None                               # one caller
    assert v["org"]["workingDays"] == 1 and v["org"]["workloadCv"] is not None


def test_working_day_of_a_caller_split_across_two_teams_counts_once():
    d = datetime(2026, 10, 7, 5, 0, tzinfo=timezone.utc)
    calls = [call(user="u2", status="NotAnswered", dur=0, at=d + timedelta(minutes=i), lead=f"B{i}") for i in range(12)]
    calls += [call(user="u2", team=ALPHA, status="NotAnswered", dur=0, at=d + timedelta(hours=3, minutes=i),
                   lead=f"A{i}") for i in range(9)]
    calls.append(call(user="u2", team=ALPHA, dur=300, at=d + timedelta(hours=4), lead="A99"))
    v = views(calls)
    beta, alpha = caller(v, "u2", BETA), caller(v, "u2", ALPHA)
    assert (beta["workingDays"], beta["realPerWorkingDay"]) == (1, 0.0)        # most of the day's dials
    assert (alpha["workingDays"], alpha["realPerWorkingDay"], alpha["realCalls"]) == (0, None, 1)
    assert v["org"]["workingDays"] == 1 and v["org"]["realPerWorkingDay"] == 1.0 and v["org"]["callers"] == 1


# ------------------------------------------------------------------ time-judged follow-ups

def test_missed_inbound_judged_after_two_hours():
    miss = dict(direction="inbound", status="Missed", dur=0)
    calls = [call(lead="L20", at=NOW - timedelta(hours=5), **miss), call(lead="L20", at=NOW - timedelta(hours=4)),
             call(lead="L21", at=NOW - timedelta(hours=6), **miss), call(lead="L21", at=NOW - timedelta(hours=3)),
             call(lead="L22", at=NOW - timedelta(hours=1), **miss),                       # not judged yet
             call(lead="L23", at=NOW - timedelta(hours=3), **miss),
             call(user="u2", lead="L23", at=NOW - timedelta(hours=2, minutes=30), dur=40),  # anyone's answer counts
             call(lead="L24", at=NOW - timedelta(hours=3), **miss),
             call(lead="L24", at=NOW - timedelta(hours=2, minutes=50), status="NotAnswered", dur=0)]
    v = views(calls)
    assert caller(v, "u1")["missedInbound"] == 2 and v["org"]["missedInbound"] == 2   # L21 and L24


def test_missed_inbound_is_any_unanswered_inbound_call_with_a_lead():
    old = NOW - timedelta(hours=3)
    calls = [call(lead="M1", at=old, direction="inbound", status="Missed", dur=0),
             call(lead="M2", at=old, direction="inbound", status="NotAnswered", dur=12),   # ring time, not talk
             call(lead="M3", at=old, direction="inbound", status="", dur=0),               # no status: not answered
             call(lead=None, at=old, direction="inbound", status="Missed", dur=0),         # no lead: can't be judged
             call(lead="M4", at=old, direction="inbound", status="Answered", dur=30)]
    v = views(calls)
    assert caller(v, "u1")["missedInbound"] == 3 and v["org"]["missedInbound"] == 3
    assert caller(v, "u1")["inbound"] == 5


def test_callbacks_pending_and_overdue():
    old, recent = NOW - timedelta(hours=30), NOW - timedelta(hours=5)
    promise = sem(commitments=("caller",))
    calls = [call(lead="L30", at=old, s=promise),                                             # overdue
             call(lead="L31", at=recent, s=promise),                                          # pending
             call(lead="L32", at=old, s=sem(commitments=("customer",))),                      # customer's promise
             call(lead="L33", at=old, s=promise),
             call(user="u2", lead="L33", at=NOW - timedelta(hours=20), dur=50),              # reached since
             call(lead="L34", at=old, s=sem(findings=("callback_promised",))),                # overdue: a finding
             call(lead="L35", at=old, s=promise),
             call(lead="L35", at=NOW - timedelta(hours=10), status="NotAnswered", dur=0),    # not reached
             call(lead="L36", at=old)]                                                        # not read yet: unknown
    a = caller(views(calls), "u1")
    assert (a["pendingCallbacks"], a["overdueFollowUps"]) == (1, 3)


def test_prior_follow_up_answered_in_current_period_is_not_overdue():
    prior = [call(lead="P1", at=NOW - timedelta(days=9), s=sem(commitments=("caller",))),
             call(lead="P2", at=NOW - timedelta(days=9), s=sem(commitments=("caller",)))]
    v = views([call(user="u2", lead="P1", at=NOW - timedelta(days=2))], prior={"calls": prior, "enrolments": []})
    p = v["org"]["prior"]
    assert p["overdueFollowUps"] == 1 and p["dials"] == 2      # P1 was reached in this period, P2 never


# ------------------------------------------------------------------ lead-level intent

def test_high_intent_and_payment_ready_are_lead_level():
    t1, t2 = NOW - timedelta(hours=10), NOW - timedelta(hours=5)
    calls = [call(lead="L40", at=t1, s=sem(80)), call(user="u3", lead="L40", at=t2, s=sem(20)),  # cooled down
             call(lead="L41", at=t1, s=sem(30)), call(user="u3", lead="L41", at=t2, s=sem(75)),
             call(lead="L42", at=t1, s=sem(40, payment_step="link_sent")),
             call(lead="L43", at=t1, s=sem(90)),                                                 # already enrolled
             call(lead="L44", at=t1, s=sem(85)), call(lead="L44", at=t2, s=sem(88)),            # once per lead
             call(lead="L45", at=t1, s=sem(90)), call(lead="L45", at=t2, s=sem(0, bnd="unclear")),  # thin later call
             call(lead="L46", at=t1, s=sem(80)), call(lead="L46", at=t2)]                       # later call not read
    v = views(calls, leads={"L43": {"stage": ENROLLED}})
    a, m = caller(v, "u1"), caller(v, "u3")
    assert (a["highIntentUnconverted"], a["paymentReady"]) == (4, 1)          # L42, L44, L45, L46
    assert (m["highIntentUnconverted"], m["paymentReady"]) == (1, 0)          # L41
    assert v["org"]["highIntentUnconverted"] == 5 and team(v, ALPHA)["highIntentUnconverted"] == 5


def test_readiness_comes_only_from_claudes_reading_and_unclear_is_not_measured():
    assert readiness({"sem": sem(0, bnd="unclear")}) == (None, "unclear", "semantic")
    assert readiness({"sem": sem(42)}) == (42, "cool", "semantic")
    assert readiness({"sem": sem(80)}) == (80, "hot", "semantic")
    assert readiness({"sem": {"summary": "malformed"}}) == (None, None, None)
    assert readiness({"sem": None}) == (None, None, None)                    # not read by Claude yet
    assert readiness({}) == (None, None, None)


# ------------------------------------------------------------------ quality, coverage, objections, integrity

def test_quality_and_objections_from_claudes_reading():
    calls = [call(lead="L50", s=sem(overall=None, objections=("price", "time"))),            # no skill scored
             call(user="u3", lead="L51",
                  s=sem(overall=6, dims={"questioning": 4, "closing": 6}, objections=("price", "price", "trust"))),
             call(user="u3", lead="L52", s=sem(overall=8, dims={"closing": 8}, objections=("budget",))),
             call(user="u3", lead="L53")]                                                    # not read yet
    v = views(calls)
    a, m = caller(v, "u1"), caller(v, "u3")
    assert a["qualityAvg"] is None and set(a["qualityByDim"].values()) == {None}
    assert a["objections"] == {"price": 1, "time": 1}
    assert m["qualityAvg"] == 7.0 and m["qualityByDim"]["questioning"] == 4.0 and m["qualityByDim"]["closing"] == 7.0
    assert m["qualityByDim"]["payment_guidance"] is None
    assert m["objections"] == {"other": 1, "price": 1, "trust": 1}            # once per call; unknown -> other
    assert v["org"]["objections"] == {"price": 2, "other": 1, "time": 1, "trust": 1}
    assert set(v["org"]["qualityByDim"]) == set(S.QUALITY_DIMENSIONS)
    assert team(v, ALPHA)["vsOrg"]["qualityAvg"] == 0.0
    assert caller(v, "u1")["vsTeam"]["qualityAvg"] is None


def test_quality_overall_needs_a_basis():
    calls = [call(lead="Q1", s=sem(60, overall=8, dims={"closing": 8})),
             call(lead="Q2", s=sem(0, bnd="unclear", overall=0, dims={"closing": 2})),   # wrong number: no basis
             call(lead="Q3", s=sem(40, overall=3)),                                        # no skill scored
             call(lead="Q4", s=sem(50, overall=None, dims={"closing": 5}))]                # no overall given
    a = caller(views(calls), "u1")
    assert a["qualityAvg"] == 8.0 and a["qualityByDim"]["closing"] == 5.0
    assert caller(views(calls[1:]), "u1")["qualityAvg"] is None


def test_coverage_counts_and_integrity_flags():
    calls = [call(status="NotAnswered", dur=0, lead="L60"),
             call(lead="L61", s=sem(), flags=[{"flag": "under_3_min", "tier": "short"}]),
             call(lead="L62", state=S.T_FOUND, flags=[{"flag": "machine", "tier": "suspect", "reason": "IVR"}]),  # unread
             call(lead="L63", state=S.T_NOT_FOUND,
                  flags=[{"flag": "repeat", "tier": "pattern"}, {"flag": "under_3_min", "tier": "short"}]),
             call(lead="L64", status="NotAnswered", dur=0, state=S.T_FOUND)]   # transcript turned up anyway
    a = caller(views(calls), "u1")
    assert (a["transcriptsExpected"], a["transcriptsFound"], a["analyzed"], a["coveragePct"]) == (4, 3, 1, 25.0)
    assert a["integrityFlagged"] == 2 and a["integrityPct"] == round(200 / 3, 1)
    assert a["integrity"] == {"flagged": 2, "flaggedPct": 66.7, "byFlag": {"under_3_min": 2, "machine": 1, "repeat": 1}}


def test_vs_differences():
    calls = [call(lead="L1"), call(lead="L2", status="NotAnswered", dur=0),
             call(user="u3", lead="L3"), call(user="u2", lead="L4"), call(user="u2", lead="L5", status="NotAnswered", dur=0),
             call(user="u2", lead="L6", status="NotAnswered", dur=0), call(user="u2", lead="L7", status="NotAnswered", dur=0)]
    v = views(calls)
    alpha, org = team(v, ALPHA), v["org"]
    assert alpha["connectPct"] == round(200 / 3, 1) and org["connectPct"] == round(300 / 7, 1)
    assert alpha["vsOrg"]["connectPct"] == round(alpha["connectPct"] - org["connectPct"], 2)
    assert caller(v, "u1")["vsTeam"]["connectPct"] == round(50.0 - alpha["connectPct"], 2)
    assert alpha["vsOrg"]["qualityAvg"] is None and set(alpha["vsOrg"]) == set(caller(v, "u1")["vsTeam"])


def test_prior_period_metrics():
    calls = [call(lead="L1"), call(user="u3", lead="L2")]
    assert all(t["prior"] is None for t in views(calls)["teams"]) and views(calls)["org"]["prior"] is None
    prior = [call(lead="P1", at=NOW - timedelta(days=8)),
             call(lead="P2", at=NOW - timedelta(days=8), status="NotAnswered", dur=0)]
    v = views(calls, prior={"calls": prior, "enrolments": [enrol("P1", NOW - timedelta(days=7))]})
    a = caller(v, "u1")
    assert a["prior"]["dials"] == 2 and a["prior"]["connectPct"] == 50.0 and a["prior"]["enrolmentsOwner"] == 1
    assert caller(v, "u3")["prior"]["calls"] == 0 and caller(v, "u3")["prior"]["connectPct"] is None
    assert team(v, ALPHA)["prior"]["calls"] == 2 and v["org"]["prior"]["enrolmentsOwner"] == 1
    assert a["enrolmentsOwner"] == 0                                         # prior enrolments stay in the prior


def test_revenue_is_not_measurable_and_notes_explain():
    v = views([call()])
    for row in [v["org"], *v["teams"], *v["callers"]]:
        assert row["revenue"] is None and row["revenuePerCaller"] is None
    text = " ".join(v["notes"])
    assert "do not add up" in text and "3+ minutes" in text and "credited once" in text and "Revenue" in text


def test_build_views_is_fast():
    rows = []
    for i in range(60_000):
        u = ("u1", "u2", "u3", "rj")[i % 4]
        st = ("Answered", "NotAnswered", "NotAnswered", "CallFailure")[i % 4 if i % 7 else 0]
        rows.append(call(user=u, status=st, dur=(i % 600) + 1 if st == "Answered" else 0, lead=f"L{i % 20000}",
                         at=NOW - timedelta(minutes=i % 40000), s=sem(i % 100) if st == "Answered" and i % 3 else None))
    t0 = time.perf_counter()
    v = views(rows, prior={"calls": rows[:20000], "enrolments": []})
    compare(rows, [], {})
    assert time.perf_counter() - t0 < 3.0 and v["org"]["calls"] == 60_000


# ------------------------------------------------------------------ Zipteams comparison

@pytest.mark.parametrize("c,expected", [
    (dict(s=sem(70), z={"intent": "HIGH"}), True),
    (dict(s=sem(50), z={"intent": "HIGH"}), False),
    (dict(s=sem(40), z={"intent": "MODERATE"}), True),
    (dict(s=sem(65), z={"intent": "HIGH"}), True),                           # our level boundaries: 65 and 35
    (dict(s=sem(34), z={"intent": "MODERATE"}), False),
    (dict(s=sem(20), z={"intent": "LOW"}), True),
    (dict(s=sem(10), z={"intent": "NEUTRAL"}), True),
    (dict(s=sem(50), z={"intent": "NOT_AVAILABLE"}), None),
    (dict(s=sem(50), z=None), None),
    (dict(z={"intent": "HIGH"}), None),                                      # not read by Claude yet
    (dict(s=sem(0, bnd="unclear"), z={"intent": "HIGH"}), None),            # ours not measured
    (dict(s={"summary": "malformed"}, z={"intent": "LOW"}), None),          # a reading with no readiness
])
def test_zip_agrees(c, expected):
    assert zip_agrees(call(**c)) is expected


def test_compare_counts_baseline_examples_and_findings():
    calls = [call(lead="Z1", s=sem(80, buying=("fee_question",)), z={"intent": "HIGH"}),
             call(lead="Z2", s=sem(10), z={"intent": "HIGH"}),                                     # unsupported hot
             call(lead="Z3", s=sem(75, buying=(("payment_intent", "strong"),)), z={"intent": "LOW"}),  # missed hot
             call(lead="Z4", s=sem(50, objections=("price",)), z={"intent": "HIGH"}),              # level differs
             call(lead="Z5", s=sem(5, bnd="unclear"), z={"intent": "HIGH"}),                        # ours unclear
             call(lead="Z6", s=sem(50), z={"intent": "NOT_AVAILABLE"}),
             call(user="u2", lead="Z7", s=sem(60)), call(user="u2", lead="Z8", s=sem(30)),          # no Zipteams team
             call(user="u3", lead="Z9", z={"intent": "HIGH"})]                                     # Zipteams only
    out = compare(calls, [], {})
    assert {k: out[k] for k in ("compared", "agree", "disagree", "noBaseline", "zipNoIntent", "ourUnclear",
                                "unsupportedHot", "missedHot", "zipOnly", "analysed")} == {
        "compared": 4, "agree": 1, "disagree": 3, "noBaseline": 3, "zipNoIntent": 1, "ourUnclear": 1,
        "unsupportedHot": 1, "missedHot": 1, "zipOnly": 1, "analysed": 8}
    assert out["agreePct"] == 25.0 and out["byEngine"] == {"semantic": 4}
    beta = next(r for r in out["byTeam"] if r["team"] == BETA)
    assert beta["baseline"] is False and beta["disagree"] == 0 and beta["noBaseline"] == 2 and "not a disagreement" in beta["note"]
    assert next(r for r in out["byTeam"] if r["team"] == ALPHA)["baseline"] is True
    assert any(BETA in n for n in out["notes"])
    ex = out["examples"]
    assert [e["callId"] for e in ex] == sorted(e["callId"] for e in ex) and out["examplesTotal"] == 3
    assert {e["kind"] for e in ex} == {"unsupported_hot", "missed_hot", "level_differs"}
    hot = next(e for e in ex if e["kind"] == "missed_hot")
    assert hot["engine"] == "semantic" and hot["signals"] == ["payment_intent"] and hot["ourLevel"] == "high"
    assert next(e for e in ex if e["kind"] == "level_differs")["objections"] == ["price"]
    assert all("excerpt" not in e and "summary" not in e for e in ex)
    f = out["findings"]
    assert len(f) == out["findingsTotal"] == 3 and {x["category"] for x in f} == {"zip_disagreement"}
    assert {x["confidence"] for x in f} == {"medium"} and all(x["excerpt"] == "" for x in f)
    assert all("Claude's reading of the transcript puts readiness at" in x["reasoning"] for x in f)
    assert len(disagreements(calls)) == 3


def test_examples_are_capped_with_total():
    calls = [call(lead=f"Q{i}", s=sem(10), z={"intent": "HIGH"}) for i in range(EXAMPLES + 5)]
    out = compare(calls, [], {})
    assert len(out["examples"]) == EXAMPLES and out["examplesTotal"] == EXAMPLES + 5
    assert out["findingsTotal"] == EXAMPLES + 5 and len(disagreements(calls)) == EXAMPLES + 5


def test_benchmark_shares_with_small_sample_flag():
    t = NOW - timedelta(days=2)
    calls = [call(lead="A1", at=t, s=sem(80), z={"intent": "HIGH"}), call(lead="A2", at=t, s=sem(80), z={"intent": "HIGH"}),
             call(lead="B1", at=t, s=sem(10), z={"intent": "HIGH"}), call(lead="C1", at=t, s=sem(80), z={"intent": "LOW"}),
             call(lead="E1", at=t, s=sem(90), z={"intent": "HIGH"}),
             call(user="u2", lead="D1", at=t, s=sem(90))]                                 # no Zipteams rating
    enrolments = [enrol("A1", t + timedelta(days=1)), enrol("C1", t + timedelta(hours=3)),
                  enrol("B1", t - timedelta(days=1))]                                     # enrolled before the call
    b = compare(calls, enrolments, {"E1": {"stage": ENROLLED}})["benchmark"]
    assert b["zipHigh"] == {"leads": 2, "enrolled": 1, "pct": 50.0, "smallSample": True, "alreadyEnrolled": 2}
    assert b["ourHigh"] == {"leads": 3, "enrolled": 2, "pct": 66.7, "smallSample": True, "alreadyEnrolled": 1}
    assert b["ourHighAllCalls"]["leads"] == 4 and b["ourHighAllCalls"]["enrolled"] == 2
    many = compare([call(lead=f"H{i}", at=t, s=sem(80), z={"intent": "HIGH"}) for i in range(30)], [], {})["benchmark"]
    assert many["zipHigh"]["smallSample"] is False and many["zipHigh"]["pct"] == 0.0
