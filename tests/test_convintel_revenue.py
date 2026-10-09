"""Revenue intelligence and the accountability bridge (analytics/convintel/revenue.py, responsibility.py).

Synthetic data only: fake people, ids and numbers; SECRET stands in for transcript words that must never
reach an opportunity row."""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from analytics.accountability import People, apply_corrections, lead_actions
from analytics.convintel import responsibility as A
from analytics.convintel import revenue as R
from analytics.convintel.classify import classify
from integrations.timeutil import ist_day, utc

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)   # 17:30 IST
USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "Rao", "MemberOfGroups": ["Team Alpha +Neel"]},
         {"ID": "u2", "FirstName": "Ravi", "LastName": "Iyer", "MemberOfGroups": ["Team Beta"]},
         {"ID": "u3", "FirstName": "Kanishka", "LastName": "", "MemberOfGroups": ["Team Gamma"]},
         {"ID": "rj", "FirstName": "Rinku", "LastName": "Jhala", "MemberOfGroups": ["Admins"]},
         {"ID": "ad", "FirstName": "Admin", "LastName": "", "MemberOfGroups": []}]
WHO = {"u1": ("Asha Rao", "person", "Team Alpha +Neel"), "u2": ("Ravi Iyer", "person", "Team Beta"),
       "rj": ("Rinku Jhala", "shared", "Admins"), "sys": ("System", "bot", "Not a user")}
SECRET = "kitne ka hai fees, kal pay karunga"
NUMBER = "919000000001"


def at(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def call(cid, lead, start, status="Answered", dur=240, direction="outbound", user="u1", kw=None, sem=None, zip_intent=None):
    name, kind, team = WHO[user]
    t = utc(start)
    return {"call_id": cid, "lead_id": lead, "number": NUMBER, "lead_number": NUMBER, "direction": direction,
            "call_status": status, "answered": int(status == "Answered"), "start_utc": start, "ist_day": ist_day(t),
            "duration_s": dur, "call_class": classify(status, dur)[0], "caller_id": user, "caller_name": name,
            "caller_kind": kind, "team": team, "kw": kw, "sem": sem,
            "zip": {"intent": zip_intent} if zip_intent else None, "t": t}


def kw(score=30, band="cool", buying=(), objections=(), payment_step=False, dated=False, findings=(), courses=()):
    return {"language": {"primary": "hinglish", "devanagari_share": 0.0, "hinglish_markers": 3, "code_switching": True},
            "word_level": {"words": 400, "wpm": 120, "categories": {}, "repeated_phrases": []},
            "sentence_level": {"sentences": 30, "questions": 5, "labelled_total": 1,
                               "labelled": [{"index": 0, "offset": 0, "excerpt": SECRET, "labels": ["buying"]}]},
            "signals": {"buying": list(buying), "objections": list(objections), "negative": [],
                        "payment_step": payment_step, "dated_next_step": dated, "callback_requested": False,
                        "amounts": [25000], "course_mentions": list(courses), "readiness_score": score,
                        "readiness_band": band, "markers": []},
            "integrity": {"words": 400, "wpm": 120, "loop_share": 0.0, "machine_text": "", "flags": []},
            "findings": [{"category": f, "excerpt": SECRET, "offset": 0, "confidence": "medium", "reasoning": "r",
                          "recommended_action": "a"} for f in findings]}


def sem(score=50, band="warm", objections=(), signals=(), payment_step="none", dated=False, findings=()):
    return {"intent": {"readiness_score": score, "readiness_band": band, "evidence": [SECRET]},
            "objections": [{"category": c, "excerpt": SECRET, "handled": h, "caller_response_excerpt": "", "note": ""}
                           for c, h in objections],
            "buying_signals": [{"type": s, "excerpt": SECRET, "strength": "strong", "caller_acted_on_it": "yes"}
                               for s in signals],
            "outcome": {"next_step_agreed": dated, "next_step": SECRET, "dated": dated, "payment_step": payment_step,
                        "course_discussed": ""},
            "findings": [{"category": f, "excerpt": SECRET, "offset": 0, "confidence": "high", "reasoning": "r",
                          "recommended_action": "a"} for f in findings],
            "summary": SECRET}


def opps(calls, leads=None, enrolments=()):
    rows = R.opportunities(calls, leads or {}, list(enrolments), NOW)
    assert all(tuple(r) == R.ROW_KEYS for r in rows)
    keys = [(r["kind"], r["leadId"]) for r in rows]
    assert len(keys) == len(set(keys)), "one row per (kind, lead)"
    return {k: r for k, r in zip(keys, rows)}


# ------------------------------------------------------------------ payment records and the revenue section

PAYMENTS = [
    {"ProspectActivityId": "PAYX1", "RelatedProspectId": "LEADX1", "CreatedOn": at(4), "mx_Custom_1": "UPI-REF-7781",
     "ActivityEvent_Note": "Amount{=}25,000{next}Mode{=}UPI",
     "Data": [{"Key": "Amount", "Value": "Rs. 25,000"}, {"Key": "Payer", "Value": "Asha Rao"}],
     "ActivityFields": {"mx_Custom_2": "", "PaidOn": "2026-10-05 10:00:00"}},
    {"ProspectActivityId": "PAYX2", "RelatedProspectId": "LEADX9", "CreatedOn": at(4), "mx_Custom_1": None,
     "Data": [{"Key": "Amount", "Value": "12,500.50"}]},
    {"ProspectActivityId": "PAYX3", "RelatedProspectId": "LEADX1", "CreatedOn": at(3), "Data": [{"Key": "Amount", "Value": ""}]},
    {"ProspectActivityId": "PAYX1", "RelatedProspectId": "LEADX1", "CreatedOn": at(4)},   # the same record read twice
]


def test_payment_field_inventory_counts_names_and_types_never_values():
    f = R.payment_fields(PAYMENTS)
    assert f["records"] == 3
    got = {x.pop("field"): x for x in copy.deepcopy(f["fields"])}
    assert got["top.mx_Custom_1"] == {"section": "top", "present": 2, "filled": 1, "types": {"text": 1, "empty": 1}}
    assert got["top.CreatedOn"] == {"section": "top", "present": 3, "filled": 3, "types": {"date": 3}}
    assert got["data.Amount"] == {"section": "data", "present": 3, "filled": 2, "types": {"number": 2, "empty": 1}}
    assert got["data.Payer"]["types"] == {"text": 1} and got["note.Amount"]["types"] == {"number": 1}
    assert got["activityFields.mx_Custom_2"]["filled"] == 0
    assert got["activityFields.PaidOn"]["types"] == {"date": 1}   # named like "paid" but a date
    assert f["amountCandidates"] == [{"field": "data.Amount", "readable": 2}, {"field": "note.Amount", "readable": 1}]
    text = json.dumps(f, ensure_ascii=False)
    for value in ("UPI-REF-7781", "25,000", "12,500", "Asha Rao", "LEADX", "PAYX", "2026-10-05", "UPI\""):
        assert value not in text


@pytest.mark.parametrize("raw,want", [
    ("25000", 25000), ("25,000", 25000), ("Rs. 25,000", 25000), ("₹25000/-", 25000), ("INR 1,50,000", 150000),
    (18000, 18000), ("12,500.50", 12500.5), ("0", None), ("", None), (None, None), ("2026-10-05", None),
    ("abc", None), (True, None), (-5, None)])
def test_amounts_are_read_strictly(raw, want):
    assert R.amount(raw) == want


ENROLMENTS = [
    {"lead_id": "LEADX1", "at_utc": at(5), "ist_day": ist_day(utc(at(5))), "owner_id": "u1", "owner_name": "Asha Rao"},
    {"lead_id": "LEADX2", "at_utc": at(5), "ist_day": ist_day(utc(at(5))), "owner_id": "rj", "owner_name": "Rinku Jhala"},
    {"lead_id": "LEADX3", "at_utc": at(5), "ist_day": ist_day(utc(at(5))), "owner_id": "u2", "owner_name": "Ravi Iyer"},
]
CALLS = [call("c1", "LEADX1", at(30), user="u2"),          # last answered caller for LEADX1: Ravi (Team Beta)
         call("c2", "LEADX3", at(30), user="rj"),          # a shared login is never the last caller
         call("c3", "LEADX1", at(1), user="u1")]           # after the enrolment: not credited


def test_not_measurable_without_payments_and_enrolments_credited_once_per_view():
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, [])
    assert (s["measurable"], s["reason"], s["paymentEvents"], s["total"], s["amounts"]) == (
        False, R.NOT_MEASURABLE_EMPTY, 0, None, None)
    e = s["enrolments"]
    assert e["total"] == 3
    assert e["byTeam"] == {"ownerAtEnrolment": {"Team Alpha +Neel": 1, "Team Beta": 1},
                           "lastAnsweredCaller": {"Team Beta": 1}}
    assert e["unattributed"] == {"ownerAtEnrolment": 1, "lastAnsweredCaller": 2}
    assert e["unattributedReasons"]["ownerAtEnrolment"] == {"owner was a shared login, which is not one person": 1}
    assert s["byCaller"] == {"ownerAtEnrolment": {"Asha Rao": 1, "Ravi Iyer": 1}, "lastAnsweredCaller": {"Ravi Iyer": 1}}
    owners = {r["caller"]: r for r in s["byCallerDetail"]["ownerAtEnrolment"]}
    assert owners["Asha Rao"] == {"callerId": "u1", "caller": "Asha Rao", "team": "Team Alpha +Neel", "enrolments": 1,
                                  "revenue": None}
    assert s["fields"]["records"] == 0 and s["amountField"] is None


def test_payments_without_a_readable_amount_are_not_measurable():
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, [PAYMENTS[2]])
    assert s["measurable"] is False and s["paymentEvents"] == 1 and s["total"] is None and s["amounts"] is None
    assert "1 payment record," in s["reason"] and "enrolments are shown instead" in s["reason"]
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, PAYMENTS, amount_field="mx_Custom_7")
    assert s["measurable"] is False and "top.mx_Custom_7" in s["reason"]


def test_measurable_with_a_synthetic_amount_field():
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, PAYMENTS)
    assert s["measurable"] is True and s["amountField"] == "data.Amount" and s["paymentEvents"] == 3
    assert "data.Amount" in s["reason"] and "2 of 3" in s["reason"]
    rev = s["amounts"]
    assert (s["total"], rev["payments"], rev["withoutAmount"]) == (37500.5, 2, 1)
    assert rev["byTeam"] == {"ownerAtEnrolment": {"Team Alpha +Neel": 25000}, "lastAnsweredCaller": {"Team Beta": 25000}}
    assert rev["unattributed"] == {"ownerAtEnrolment": 12500.5, "lastAnsweredCaller": 12500.5}
    assert "no first-time enrolment" in next(iter(rev["unattributedReasons"]["ownerAtEnrolment"]))
    asha = next(r for r in s["byCallerDetail"]["ownerAtEnrolment"] if r["caller"] == "Asha Rao")
    assert (asha["enrolments"], asha["revenue"]) == (1, 25000)
    assert s["enrolments"]["total"] == 3      # enrolments are still counted, each once per view
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, [{**PAYMENTS[1], "mx_Custom_3": "40000"}], amount_field="mx_Custom_3")
    assert s["measurable"] and s["amountField"] == "top.mx_Custom_3" and s["total"] == 40000


# ------------------------------------------------------------------ opportunities

def test_payment_ready_semantic_and_keyword_and_dedupe():
    hot = sem(82, "hot", signals=("fee_question", "payment_intent"), payment_step="amount_and_date_agreed",
              findings=("payment_ready",))
    got = opps([call("a1", "L1", at(50), sem=hot), call("a2", "L1", at(30), sem=hot),
                call("b1", "L2", at(30), kw=kw(80, "hot", buying=("fee_question",), findings=("payment_ready",))),
                call("c1", "L3", at(30), kw=kw(40, "cool"))])
    r = got[("payment_ready_unconverted", "L1")]
    assert (r["callId"], r["callerId"], r["caller"], r["team"], r["confidence"]) == ("a2", "u1", "Asha Rao", "Team Alpha +Neel", "high")
    assert r["day"] == ist_day(utc(at(30))) and "readiness 82/100" in r["evidence"] and "fee question" in r["evidence"]
    assert got[("payment_ready_unconverted", "L2")]["confidence"] == "medium"   # keyword: never high
    assert not any(lead == "L3" for _, lead in got)


def test_link_sent_unpaid_waits_24_hours():
    link = sem(60, "warm", payment_step="link_sent")
    got = opps([call("a", "L4", at(30), sem=link), call("b", "L5", at(10), sem=link)])
    assert got[("link_sent_unpaid", "L4")]["confidence"] == "high" and "30 h later" in got[("link_sent_unpaid", "L4")]["evidence"]
    assert ("link_sent_unpaid", "L5") not in got


def test_missed_callback_judged_only_after_2_hours():
    missed = dict(status="Missed", dur=20, direction="inbound")
    got = opps([call("a", "L6", at(3), **missed),
                call("b", "L7", at(1), **missed),                                     # not yet judged
                call("c", "L8", at(5), **missed), call("d", "L8", at(4), dur=60),     # returned within 2 h
                call("e", "L9", at(10), **missed), call("f", "L9", at(6), dur=60)],    # returned 4 h later
               leads={"L6": {"owner_name": "Asha Rao"}})
    r = got[("missed_callback", "L6")]
    assert r["confidence"] == "high" and r["nextAction"].startswith("Asha Rao, call the lead back now")
    assert "IST" in r["evidence"]
    assert ("missed_callback", "L7") not in got and ("missed_callback", "L8") not in got
    assert got[("missed_callback", "L9")]["confidence"] == "medium" and "4 h later" in got[("missed_callback", "L9")]["evidence"]


def test_unresolved_objection_on_the_latest_real_call():
    got = opps([call("a", "L10", at(30), sem=sem(40, "cool", objections=(("time", "yes"), ("price", "no")))),
                call("b", "L11", at(30), sem=sem(40, "cool", objections=(("price", "yes"),))),
                call("c", "L12", at(30), kw=kw(40, "cool", objections=("trust",))),
                call("d", "L13", at(30), kw=kw(40, "cool", objections=("trust",), dated=True))])
    r = got[("unresolved_objection", "L10")]
    assert "price (answered: no)" in r["evidence"] and "time" not in r["evidence"].split("raised")[1]
    assert r["nextAction"] == R.OBJECTION_ACTIONS["price"] and r["confidence"] == "high"
    assert ("unresolved_objection", "L11") not in got and ("unresolved_objection", "L13") not in got
    assert got[("unresolved_objection", "L12")]["confidence"] == "low"


def test_emi_friction_and_course_unavailable():
    got = opps([call("a", "L14", at(30), kw=kw(45, "cool", objections=("emi_or_finance",), findings=("payment_friction",))),
                call("b", "L15", at(50), kw=kw(45, "cool", objections=("emi_or_finance",))),
                call("c", "L15", at(30), kw=kw(55, "warm", payment_step=True)),     # payment step after the friction
                call("d", "L16", at(30), kw=kw(45, "cool", findings=("course_unavailable",), courses=("diploma in space law",)))],
               leads={"L16": {"course": "Diploma in Contract Drafting"}})
    assert got[("emi_friction", "L14")]["confidence"] == "medium"
    assert ("emi_friction", "L15") not in got
    r = got[("course_unavailable", "L16")]
    assert "course on the lead record: Diploma in Contract Drafting" in r["evidence"] and r["confidence"] == "low"
    assert "space law" not in r["evidence"]          # transcript words never reach the evidence


def test_weak_follow_up_after_48_hours_only():
    warm = sem(60, "warm")
    got = opps([call("a", "L17", at(72), sem=warm),
                call("b", "L18", at(72), sem=sem(60, "warm", dated=True)),
                call("c", "L19", at(30), sem=warm),                                   # not yet judged
                call("d", "L20", at(72), sem=warm), call("e", "L20", at(60), dur=40)])  # spoken to again within 48 h
    assert got[("weak_follow_up", "L17")]["confidence"] == "medium"
    assert not any(("weak_follow_up", l) in got for l in ("L18", "L19", "L20"))


def test_high_intent_marked_low_by_stage_or_zipteams():
    got = opps([call("a", "L21", at(30), kw=kw(80, "hot")),
                call("b", "L22", at(30), sem=sem(75, "hot"), zip_intent="LOW"),
                call("c", "L23", at(30), sem=sem(75, "hot"), zip_intent="HIGH")],
               leads={"L21": {"stage": "Not Interested"}, "L23": {"stage": "Call Back Later"}})
    r = got[("high_intent_marked_low", "L21")]
    assert "stage is Not Interested" in r["evidence"] and r["confidence"] == "low"
    assert "Zipteams rated the call LOW" in got[("high_intent_marked_low", "L22")]["evidence"]
    assert ("high_intent_marked_low", "L23") not in got


def test_repeated_dials_without_a_conversation():
    def dials(lead, n, status="NotAnswered", user="u2"):
        return [call(f"{lead}-{i}", lead, at(20 - i), status=status, dur=0, user=user) for i in range(n)]
    got = opps(dials("L24", 6) + dials("L25", 5) + dials("L26", 6) + [call("x", "L26", at(2), dur=200)]
               + dials("L27", 6, status="CallFailure") + dials("L28", 6) + dials("L29", 6, user="sys"),
               leads={"L28": {"stage": "Not Interested"}})
    r = got[("repeated_dials_no_conversation", "L24")]
    assert (r["callerId"], r["caller"], r["confidence"], r["callId"]) == ("u2", "Ravi Iyer", "high", "L24-5")
    assert r["evidence"].startswith("6 dials in this period, 0 answered") and "time of day (IST)" in r["evidence"]
    assert "dialer" in got[("repeated_dials_no_conversation", "L27")]["nextAction"]
    assert not any(("repeated_dials_no_conversation", l) in got for l in ("L25", "L26", "L28", "L29"))


def test_repeated_dials_suggest_a_time_of_day_not_yet_tried():
    morning = [call(f"m{i}", "L40", f"2026-10-0{5 + i % 3} 0{4 + i % 2}:00:00", status="NotAnswered", dur=0) for i in range(6)]
    r = opps(morning)[("repeated_dials_no_conversation", "L40")]   # 09:30-10:30 IST
    assert "morning 6" in r["evidence"] and "evening (after 18:00 IST)" in r["nextAction"]
    spread = [call(f"s{i}", "L41", f"2026-10-0{5 + i % 3} {h}:00:00", status="NotAnswered", dur=0)
              for i, h in enumerate(("04", "08", "13", "04", "08", "13"))]          # 09:30, 13:30 and 18:30 IST
    r = opps(spread)[("repeated_dials_no_conversation", "L41")]
    assert "morning 2, afternoon 2, evening 2" in r["evidence"] and "another day" in r["nextAction"]


def test_enrolled_leads_skipped_ordering_and_no_leaks():
    hot = sem(90, "hot", findings=("payment_ready",), payment_step="amount_and_date_agreed")
    calls = [call("a", "L30", at(30), sem=hot), call("b", "L31", at(30), sem=hot),
             call("c", "L32", at(30), sem=hot),
             *[call(f"d{i}", "L33", at(20 - i), status="NotAnswered", dur=0) for i in range(6)],
             call("e", "L34", at(30), kw=kw(75, "hot", findings=("payment_ready",)))]
    rows = R.opportunities(calls, {"L31": {"stage": "Course Enrolled"}}, [{"lead_id": "L30", "at_utc": at(1)}], NOW)
    assert {r["leadId"] for r in rows} == {"L32", "L33", "L34"}
    assert [(r["kind"], r["leadId"]) for r in rows] == [
        ("payment_ready_unconverted", "L32"), ("payment_ready_unconverted", "L34"),
        ("repeated_dials_no_conversation", "L33")]
    text = json.dumps(rows, ensure_ascii=False)
    assert SECRET not in text and NUMBER not in text and "kitne" not in text
    assert all(r["confidence"] != "high" for r in rows if r["leadId"] == "L34")


def test_calls_that_may_not_be_real_conversations_raise_no_opportunity():
    ivr = kw(85, "hot", findings=("payment_ready",))
    ivr["integrity"]["flags"] = ["machine"]
    not_real = {**sem(85, "hot", findings=("payment_ready",)), "integrity": {"real_conversation": "no", "flags": ["machine"]}}
    doubtful = {**sem(85, "hot", findings=("payment_ready",)), "integrity": {"real_conversation": "doubtful", "flags": []}}
    got = opps([call("a", "L37", at(30), kw=ivr), call("b", "L38", at(30), sem=not_real), call("c", "L39", at(30), sem=doubtful)])
    assert set(got) == {("payment_ready_unconverted", "L39")}


def test_opportunities_handle_calls_without_analysis_or_time():
    assert R.opportunities([call("a", "L35", at(5)), {"call_id": "z", "lead_id": "L36"}, {"call_id": "y"}], {}, [], NOW) == []


# ------------------------------------------------------------------ accountability bridge

def assigned(i, lead, t, prev, to, by):
    return {"Id": f"{lead}{i}", "EventCode": 3001, "EventName": "LeadAssigned", "CreatedOn": t,
            "Data": [{"Key": "PreviousOwner", "Value": prev}, {"Key": "CurrentOwner", "Value": to},
                     {"Key": "CreatedBy", "Value": by}]}


def lsq_call(i, t, inbound, who, status):
    key = "Reciever" if inbound else "Caller"
    return {"Id": f"K{i}", "EventCode": 21 if inbound else 22, "CreatedOn": t,
            "ActivityFields": {"ActivityEvent_Note": f"{key}{{=}}{who}{{next}}Status{{=}}{status}"}}


def action_rows():
    """Real analytics.accountability rows (string values, as read from actions.csv) for two synthetic leads."""
    people = People(USERS)
    d0, d1 = datetime(2026, 10, 4, tzinfo=timezone.utc), datetime(2026, 10, 10, tzinfo=timezone.utc)
    la = {"ProspectID": "LA", "OwnerIdName": "Ravi Iyer", "CreatedOn": "2026-01-01 00:00:00", "CreatedByName": "Asha Rao",
          "mx_Assigned_By": "Asha Rao", "mx_Assigned_On": "2026-10-06 05:00:30"}
    lb = {"ProspectID": "LB", "OwnerIdName": "Asha Rao", "CreatedOn": "2026-01-01 00:00:00", "CreatedByName": "Asha Rao"}
    rows = lead_actions(la, [assigned(1, "A", "2026-10-05 05:00:00", "", "Asha Rao", "Kanishka"),
                             assigned(2, "A", "2026-10-06 05:00:00", "Asha Rao", "Ravi Iyer", "Rinku Jhala")],
                        people, d0, d1, NOW)
    rows += lead_actions(lb, [assigned(1, "B", "2026-10-06 05:00:00", "Ravi Iyer", "Rinku Jhala", "Rinku Jhala"),
                              lsq_call(1, "2026-10-06 04:00:00", False, "Ravi Iyer", "Answered"),
                              lsq_call(2, "2026-10-07 05:00:00", True, "Rinku Jhala", "Missed"),
                              assigned(2, "B", "2026-10-08 05:00:00", "Rinku Jhala", "Asha Rao", "System")],
                         people, d0, d1, NOW)
    return rows


DATA_CALLS = [call("x1", "LB", "2026-10-06 04:00:00", user="u2"), call("x2", "LB", "2026-10-06 04:30:00", user="rj"),
              call("x3", "LB", "2026-10-07 09:00:00", user="u1")]
ENROLLED = [{"lead_id": "LA", "at_utc": "2026-10-08 10:00:00", "owner_id": "u2", "owner_name": "Ravi Iyer"}]


def test_accountability_mapping_keeps_shared_logins_out_and_maps_statuses():
    f = {r["finding_id"]: r for r in A.findings(action_rows(), DATA_CALLS, ENROLLED, USERS, now=NOW)}
    assert set(f) == {"acc:assign:A1", "acc:assign:A2", "acc:assign:B1", "acc:call:K2", "acc:assign:B2"}
    assert all(tuple(r) == A.COLUMNS for r in f.values())
    for r in f.values():
        for col in ("responsible_person", "action_performed_by", "assigned_by", "transferred_by", "followup_owner"):
            assert r[col] not in ("Rinku Jhala", "Admin")
    a1 = f["acc:assign:A1"]
    assert (a1["status"], a1["action_performed_by"], a1["responsible_person"], a1["assigned_by"]) == (
        "VERIFIED", "Kanishka", "Kanishka", "Kanishka")
    assert (a1["action_timestamp_utc"], a1["team_at_action_time"], a1["followup_owner"]) == (
        "2026-10-05 05:00:00", "Team Gamma", "Asha Rao")
    assert "current team" in a1["evidence"] and a1["owner_at_enrolment"] == "Ravi Iyer"
    a2 = f["acc:assign:A2"]
    assert (a2["status"], a2["action_performed_by"], a2["transferred_by"], a2["transferred_to"]) == (
        "VERIFIED_ASSIGNED_BY", "Asha Rao", "Asha Rao", "Ravi Iyer")
    assert (a2["source_attribution"]["login_used"], a2["source_attribution"]["assigned_by_field"]) == ("Rinku Jhala", "Asha Rao")
    b1 = f["acc:assign:B1"]
    assert (b1["status"], b1["action_performed_by"], b1["responsible_person"], b1["followup_owner"]) == (
        "UNVERIFIED", None, None, None)
    assert b1["last_answered_caller"] == "Ravi Iyer" and b1["account_owner"] == "Rinku Jhala"
    k2 = f["acc:call:K2"]
    assert k2["status"] == "UNVERIFIED" and "name to check with the team (not proof): Ravi Iyer" in k2["evidence"]
    assert k2["last_answered_caller"] == "Ravi Iyer"     # Rinku's login call never counts; Asha's came later
    b2 = f["acc:assign:B2"]
    assert (b2["status"], b2["action_performed_by"], b2["responsible_person"], b2["team_at_action_time"]) == (
        "AUTOMATED", "System (automation)", None, None)
    assert b2["last_answered_caller"] == "Asha Rao" and b2["owner_at_enrolment"] is None


def test_corrections_keep_original_and_corrected_attribution():
    rows = action_rows()
    before = {r["key"]: copy.deepcopy(r) for r in rows}
    apply_corrections(rows, [{"key": "assign:B1", "performed_by": "Ravi Iyer", "confirmed_by": "Neel", "note": "TL checked"},
                             {"key": "assign:A1", "performed_by": "Asha Rao", "confirmed_by": "Neel", "note": ""}])
    audit = [{"key": "assign:A1", "performed_by": "Kanishka", "status": "Verified", "change": "first seen"},
             {"key": "assign:A1", "performed_by": "Asha Rao", "status": "Corrected", "change": "re-attributed",
              "previous": {"performed_by": "Kanishka", "status": "Verified"}}]
    f = {r["finding_id"]: r for r in A.findings(rows, DATA_CALLS, ENROLLED, USERS, audit=audit, now=NOW)}
    b1 = f["acc:assign:B1"]
    assert (b1["status"], b1["action_performed_by"], b1["responsible_person"], b1["transferred_by"]) == (
        "CORRECTED", "Ravi Iyer", "Ravi Iyer", "Ravi Iyer")
    assert b1["corrected_attribution"] == {"named": "Ravi Iyer", "accepted": True, "confirmed_by": "Neel", "note": "TL checked"}
    assert b1["source_attribution"]["evidence"] == before["assign:B1"]["evidence"]
    assert b1["source_attribution"]["performed_by"] is None and "audit" in b1["source_attribution"]["note"]
    a1 = f["acc:assign:A1"]
    assert (a1["source_attribution"]["performed_by"], a1["source_attribution"]["status"]) == ("Kanishka", "VERIFIED")
    assert (a1["action_performed_by"], a1["corrected_attribution"]["confirmed_by"]) == ("Asha Rao", "Neel")
    assert a1["team_at_action_time"] == "Team Alpha +Neel"


def test_shared_login_named_by_a_row_or_a_correction_is_unverified():
    rows = [{"key": "x1", "lead_id": "LC", "action": "assignment", "action_time_ist": "2026-10-06 10:30",
             "accountability_status": "Verified", "action_performed_by": "Rinku Jhala", "assigned_by": "Rinku Jhala",
             "assigned_to": "Asha Rao", "account_owner": "Asha Rao", "evidence": "owner-change log"},
            {"key": "x2", "lead_id": "LC", "action": "transfer", "action_time_ist": "2026-10-06 11:00",
             "accountability_status": "Corrected", "action_performed_by": "Admin", "transferred_by": "Admin",
             "evidence": "confirmed by Neel: guess (was: owner-change log: made from Admin's shared login)"},
            {"key": "x3", "lead_id": "LC", "action": "assignment", "action_time_ist": "", "accountability_status": "Odd",
             "action_performed_by": "Asha Rao", "evidence": ""}]
    f = {r["finding_id"]: r for r in A.findings(rows, [], [], USERS, now=NOW)}
    x1, x2, x3 = f["acc:x1"], f["acc:x2"], f["acc:x3"]
    assert (x1["status"], x1["action_performed_by"], x1["assigned_by"], x1["responsible_person"]) == ("UNVERIFIED", None, None, None)
    assert x1["action_timestamp_utc"] == "2026-10-06 05:00:00" and "not one person" in x1["evidence"]
    assert (x2["status"], x2["action_performed_by"], x2["transferred_by"]) == ("UNVERIFIED", None, None)
    assert x2["corrected_attribution"]["accepted"] is False and x2["corrected_attribution"]["named"] == "Admin"
    assert x2["source_attribution"]["evidence"] == "owner-change log: made from Admin's shared login"
    assert (x3["status"], x3["action_timestamp_utc"]) == ("UNVERIFIED", None) and "Odd" in x3["evidence"]


def test_accountability_summary():
    rows = action_rows()
    apply_corrections(rows, [{"key": "assign:B1", "performed_by": "Ravi Iyer", "confirmed_by": "Neel", "note": ""}])
    s = A.summary(A.findings(rows, DATA_CALLS, ENROLLED, USERS, now=NOW))
    assert s["rows"] == 5 and s["unverified"] == 1
    assert s["byStatus"] == {"VERIFIED": 1, "VERIFIED_ASSIGNED_BY": 1, "UNVERIFIED": 1, "AUTOMATED": 1, "CORRECTED": 1}
    assert s["byPerson"] == [["Asha Rao", 1], ["Kanishka", 1], ["Ravi Iyer", 1]]
    assert any("no team history" in n for n in s["notes"]) and any("corrected" in n for n in s["notes"])
    assert A.summary([])["rows"] == 0 and any("No accountability actions" in n for n in A.summary([])["notes"])


# ------------------------------------------------------------------ review fixes: counting, windows, honesty

def test_each_enrolment_counts_once_per_view_even_when_read_twice():
    twice = ENROLMENTS + [dict(ENROLMENTS[0]), {**ENROLMENTS[2], "at_utc": at(2)}]
    s = R.revenue_section(twice, CALLS, USERS, [])
    e = s["enrolments"]
    assert e["total"] == 3
    for view in ("ownerAtEnrolment", "lastAnsweredCaller"):
        assert sum(e["byTeam"][view].values()) + e["unattributed"][view] == e["total"]


def test_a_shared_owner_missing_from_the_user_list_is_still_named_as_shared():
    s = R.revenue_section([{**ENROLMENTS[1], "owner_id": "zz"}], [], [u for u in USERS if u["ID"] != "rj"], [])
    assert s["enrolments"]["unattributedReasons"]["ownerAtEnrolment"] == {
        "owner was a shared login, which is not one person": 1}


def test_a_guessed_amount_field_names_the_other_candidates():
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, PAYMENTS)
    assert "note.Amount" in s["reason"] and "payment_amount_field" in s["reason"]
    s = R.revenue_section(ENROLMENTS, CALLS, USERS, PAYMENTS, amount_field="data.Amount")
    assert "Other fields" not in s["reason"] and s["total"] == 37500.5


MINUTE = 1 / 60


def test_time_windows_at_their_exact_boundaries():
    missed = dict(status="Missed", dur=20, direction="inbound")
    link, warm = sem(60, "warm", payment_step="link_sent"), sem(60, "warm")
    got = opps([call("a", "B1", at(2), **missed), call("b", "B2", at(2 - MINUTE), **missed),
                call("c", "B3", at(24), sem=link), call("d", "B4", at(24 - MINUTE), sem=link),
                call("e", "B5", at(48), sem=warm), call("f", "B6", at(48 - MINUTE), sem=warm)])
    assert ("missed_callback", "B1") in got and ("missed_callback", "B2") not in got
    assert ("link_sent_unpaid", "B3") in got and ("link_sent_unpaid", "B4") not in got
    assert ("weak_follow_up", "B5") in got and ("weak_follow_up", "B6") not in got


def test_windows_running_past_the_calls_read_are_not_judged():
    calls = [call("a", "B7", at(3), status="Missed", dur=20, direction="inbound"),
             call("b", "B8", at(72), sem=sem(60, "warm")), call("c", "B9", at(30), sem=sem(60, "warm", payment_step="link_sent"))]
    got = {(r["kind"], r["leadId"]) for r in R.opportunities(calls, {}, [], NOW, calls_until=NOW - timedelta(hours=30))}
    assert ("missed_callback", "B7") not in got and ("weak_follow_up", "B8") not in got
    assert ("link_sent_unpaid", "B9") in got        # judged on enrolments, which are read past the period
    got = {(r["kind"], r["leadId"]) for r in R.opportunities(calls, {}, [], NOW, calls_until=NOW + timedelta(days=1))}
    assert {("missed_callback", "B7"), ("weak_follow_up", "B8")} <= got     # never later than now


def test_an_automated_call_is_not_a_callback():
    got = opps([call("a", "B10", at(5), status="Missed", dur=20, direction="inbound"),
                call("b", "B10", at(4.5), dur=60, user="sys")])
    r = got[("missed_callback", "B10")]
    assert r["confidence"] == "high" and "later" not in r["evidence"]


def test_unclear_readiness_is_not_measured_never_a_low_score():
    unclear = kw(0, "unclear")
    got = opps([call("a", "B11", at(50), kw=kw(80, "hot")), call("b", "B11", at(30), kw=unclear),
                call("c", "B12", at(50), kw=kw(80, "hot")), call("d", "B12", at(30), kw=unclear),
                call("e", "B13", at(60), kw=unclear)],
               leads={"B12": {"stage": "Not Interested"}, "B13": {"stage": "Not Interested"}})
    assert got[("payment_ready_unconverted", "B11")]["callId"] == "a"     # the thin later call does not cool it
    assert got[("high_intent_marked_low", "B12")]["callId"] == "c"
    assert not any(lead == "B13" for _, lead in got)


def test_values_outside_the_vocabulary_never_reach_evidence():
    odd = sem(80, "hot", objections=((SECRET, "no"),), signals=(SECRET, "fee_question"))
    odd["outcome"]["payment_step"] = SECRET
    rows = R.opportunities([call("a", "B14", at(30), sem=odd)], {}, [], NOW)
    text = json.dumps(rows, ensure_ascii=False)
    assert SECRET not in text and "kitne" not in text
    r = next(r for r in rows if r["kind"] == "unresolved_objection")
    assert "other (answered: no)" in r["evidence"]


def test_ambiguous_names_and_shared_suggestions_stay_unverified():
    users = USERS + [{"ID": "p1", "FirstName": "Priya", "LastName": "Shah", "MemberOfGroups": ["Team Alpha +Neel"]},
                     {"ID": "p2", "FirstName": "Priya", "LastName": "Shah", "MemberOfGroups": ["Team Beta"]}]
    rows = [{"key": "y1", "lead_id": "LD", "action": "assignment", "action_time_ist": "2026-10-06 10:30",
             "accountability_status": "Verified", "action_performed_by": "Priya Shah", "assigned_by": "Priya Shah",
             "account_owner": "Priya Shah", "evidence": "owner-change log: made from Priya Shah's own login"},
            {"key": "y2", "lead_id": "LD", "action": "inbound call missed", "action_time_ist": "2026-10-06 11:00",
             "accountability_status": "Unverified", "action_performed_by": "Unverified", "possible_actor": "Rinku Jhala",
             "evidence": "call log"}]
    f = {r["finding_id"]: r for r in A.findings(rows, [], [], users, now=NOW)}
    y1, y2 = f["acc:y1"], f["acc:y2"]
    assert (y1["status"], y1["action_performed_by"], y1["responsible_person"], y1["assigned_by"]) == (
        "UNVERIFIED", None, None, None)
    assert "2 LeadSquared users are named Priya Shah" in y1["evidence"] and y1["team_at_action_time"] is None
    assert y2["status"] == "UNVERIFIED" and "name to check" not in y2["evidence"]


def test_correction_split_and_missing_original_in_the_audit():
    rows = action_rows()
    before = {r["key"]: r["evidence"] for r in rows}
    apply_corrections(rows, [{"key": "assign:B1", "performed_by": "Ravi Iyer", "confirmed_by": "Neel",
                              "note": "TL heard it (was: on leave)"}])
    b1 = {r["finding_id"]: r for r in A.findings(rows, [], [], USERS, audit=[], now=NOW)}["acc:assign:B1"]
    assert b1["source_attribution"]["evidence"] == before["assign:B1"]
    assert b1["corrected_attribution"]["note"] == "TL heard it (was: on leave)"
    assert "no attribution from before this correction" in b1["source_attribution"]["note"]


def test_summary_groups_follow_ups_without_naming_who_dialled():
    rows = [{"key": f"f{i}", "lead_id": "LE", "action": a, "action_time_ist": "2026-10-06 10:00",
             "accountability_status": "Verified", "action_performed_by": "Asha Rao", "evidence": "e"}
            for i, a in enumerate(("follow-up done by Rinku Jhala", "follow-up done by Ravi Iyer", "stage change to Course Enrolled"))]
    s = A.summary(A.findings(rows, [], [], USERS, now=NOW))
    assert s["byAction"] == {"follow-up done by someone else": 2, "stage change": 1}
