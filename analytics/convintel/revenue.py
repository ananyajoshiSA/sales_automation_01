"""Revenue intelligence: what the payment records can measure, enrolments credited two ways, and the leads
where an enrolment is being left on the table.

Rupee figures come only from LeadSquared payment records (event 213) that carry a readable amount. Until
such a record exists, revenue is "not measurable" with the reason, and enrolments are shown instead. An
amount is never estimated or defaulted: the user deferred an average fee. ``payment_fields`` lists the fields
the records carry (names, how many records fill each, value types; never values), so the amount field can be
chosen once records arrive.

Each first-time enrolment is credited once per view (analytics/convintel/attribution.py): to the team of
the lead's owner at enrolment, and to the team of the last person whose answered call on the lead came at or
before it. Neither known -> "unattributed", with the reason.

``opportunities`` names one next step per (kind, lead), most valuable first, from Claude's reading of the lead's
calls. Enrolled leads are skipped. Evidence is
built from signals, categories and scores only: never transcript words or lead numbers.

    python -m analytics.convintel.revenue PAYMENTS.json
"""

from __future__ import annotations

import json
import re
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.accountability import AUTOMATION, SHARED_ACCOUNTS
from analytics.convintel.attribution import BOT_KIND, PERSON, SHARED, Directory, credit_enrolments, user_name
from analytics.convintel.schema import OBJECTION_CATEGORIES, REAL_CALL, SIGNAL_TYPES
from analytics.definitions import DEAD_STAGES, DIALER_FAILURE_SHARE, ENROLLED
from analytics.revenue import AMOUNT_KEY
from analytics.team_performance import BOT
from integrations.leadsquared import parse_activity_note
from integrations.timeutil import IST, ist_day, utc

HIGH_READINESS = 70                     # "payment ready" / high intent: the same bar as the team view
FOLLOW_UP_READINESS = 50                # a warm call that should end with a dated next step
LINK_WAIT = timedelta(hours=24)         # payment link sent, still no enrolment this long after
CALLBACK_WINDOW = timedelta(hours=2)    # a missed inbound call should be returned within this
FOLLOW_UP_WINDOW = timedelta(hours=48)  # a warm lead should be spoken to again within this
REPEATED_DIALS = 6                      # dials in the period with no 3+ minute conversation
NOT_REAL_FLAGS = {"no_content", "machine", "loop"}   # signs in Claude's reading that void a call's signals
DAY_PARTS = (("morning", 12, "in the morning (10:00-12:00 IST)"),       # (name, IST hour it ends, when to try)
             ("afternoon", 17, "in the afternoon (14:00-16:00 IST)"), ("evening", 24, "in the evening (after 18:00 IST)"))

# Most valuable first: a lead ready to pay, then money already in motion, then leads that asked for us,
# then leads the record undersells, then fixable blockers, then weak follow-up and wasted dialling.
KINDS = ("payment_ready_unconverted", "link_sent_unpaid", "missed_callback", "high_intent_marked_low",
         "emi_friction", "unresolved_objection", "course_unavailable", "weak_follow_up",
         "repeated_dials_no_conversation")
ROW_KEYS = ("kind", "leadId", "callId", "callerId", "caller", "team", "day", "evidence", "nextAction", "confidence")
_CONF = {"high": 0, "medium": 1, "low": 2}
_STEPS = ("link_sent", "amount_and_date_agreed", "amount_quoted", "emi_discussed", "unclear")   # reading's outcome
# Objections that are closures or have their own kind are not "unresolved objections".
_OWN_KIND = {"not_interested", "joined_elsewhere", "emi_or_finance", "course_unavailable"}
OBJECTION_ACTIONS = {
    "price": "Call back with the fee broken down, the EMI and part-payment options and what the course leads to; "
             "ask what amount works for them.",
    "time": "Show how the course fits their week (class timings, recordings) and agree a start date.",
    "value_doubt": "Share concrete outcomes (what learners can do after the course, alumni results) and ask what "
                   "proof would help them decide.",
    "trust": "Send proof they can check: reviews, an alumnus they can speak to, the refund and certificate terms.",
    "family_approval": "Offer a short call with the family member who decides, at a time they choose.",
    "course_fit": "Ask what they need the course for and match it to the right course or module before the next call.",
    "job_or_placement": "Explain exactly what career support the course includes, without promising more.",
    "language": "Tell them the class language and support options and offer a sample class.",
    "technical": "Walk them through the login or payment step on the call.",
    "other": "Call back, ask what is still holding them back and answer it on the call.",
}
OWNER, LAST = "ownerAtEnrolment", "lastAnsweredCaller"
NO_TEAM = "(no team recorded)"
NOT_MEASURABLE_EMPTY = "LeadSquared returned no payment records for this period; enrolments are shown instead"
SECTIONS = ("top", "activityFields", "data", "note")

_NUM = re.compile(r"^(?:₹|rs\.?|inr)?\s*(-?\d[\d,]*(?:\.\d+)?)\s*(?:/-|inr)?$", re.I)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?Z?$"
                   r"|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:,? \d{1,2}:\d{2}(?::\d{2})?(?: ?[AP]M)?)?$", re.I)


# ------------------------------------------------------------------ payment records

def _unique(payments: list[dict] | None) -> list[dict]:
    seen, out = set(), []
    for a in payments or []:
        k = a.get("ProspectActivityId") or a.get("Id")
        if k and k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


def _sections(a: dict) -> dict[str, dict]:
    """A payment record's fields, by where they sit: top level, ActivityFields, Data Key/Value pairs, note."""
    af = a.get("ActivityFields") if isinstance(a.get("ActivityFields"), dict) else {}
    data = {str(d["Key"]): d.get("Value") for d in a.get("Data") or [] if isinstance(d, dict) and d.get("Key")}
    raw = a.get("ActivityEvent_Note") or af.get("ActivityEvent_Note")
    note = {**parse_activity_note(raw if isinstance(raw, str) else None),
            **(a["note"] if isinstance(a.get("note"), dict) else {})}
    top = {k: v for k, v in a.items() if k not in ("ActivityFields", "Data", "note")}
    return {"top": top, "activityFields": af, "data": data, "note": note}


def value_type(v) -> str:
    """number | date | text | empty (other for nested lists or objects). Only the type is ever reported."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return "empty"
    if isinstance(v, bool):
        return "text"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, (dict, list)):
        return "other" if v else "empty"
    s = str(v).strip()
    return "date" if _DATE.match(s) else "number" if _NUM.match(s) else "text"


def amount(v) -> float | None:
    """A positive rupee amount from 25000, "25,000", "Rs. 25,000", "₹25000/-"; None otherwise (never 0)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    m = _NUM.match(str(v).strip())
    x = float(m.group(1).replace(",", "")) if m else None
    return x if x and x > 0 else None


def payment_fields(payments: list[dict]) -> dict:
    """Which fields the payment records carry: one row per field ("section.name", e.g. "data.Amount") with how
    many records have it, how many fill it, and the value types. Names and counts only; no value is copied."""
    pays = _unique(payments)
    acc: dict[str, dict[str, dict]] = {s: {} for s in SECTIONS}
    for a in pays:
        for sec, fields in _sections(a).items():
            for k, v in fields.items():
                f = acc[sec].setdefault(str(k), {"present": 0, "filled": 0, "types": Counter()})
                t = value_type(v)
                f["present"] += 1
                f["filled"] += t != "empty"
                f["types"][t] += 1
    return {"records": len(pays),
            "fields": [{"field": f"{s}.{k}", "section": s, **f, "types": dict(f["types"].most_common())}
                       for s in SECTIONS for k, f in sorted(acc[s].items())],
            "amountCandidates": _candidates([_sections(a) for a in pays]),
            "note": "Field names, how many records fill each and value types only; values are never shown."}


def _candidates(secs: list[dict[str, dict]]) -> list[dict]:
    """Fields named like an amount (amount, fee, price, paid) that hold a readable amount in at least one record."""
    n = Counter()
    for s in secs:
        for sec in SECTIONS:
            for k, v in s[sec].items():
                if AMOUNT_KEY.search(str(k)) and amount(v) is not None:
                    n[f"{sec}.{k}"] += 1
    return [{"field": f, "readable": c} for f, c in sorted(n.items(), key=lambda x: (-x[1], x[0]))]


def _pick(secs: list[dict[str, dict]], field: str | None) -> str | None:
    if not field:
        c = _candidates(secs)
        return c[0]["field"] if c else None
    if "." in field and field.split(".", 1)[0] in SECTIONS:
        return field
    return next((f"{sec}.{field}" for s in secs for sec in SECTIONS if field in s[sec]), f"top.{field}")


def _read_amount(s: dict[str, dict], ref: str) -> float | None:
    sec, key = ref.split(".", 1)
    return amount(s[sec].get(key))


# ------------------------------------------------------------------ crediting enrolments and payments

def _owner_gap(e: dict, directory: Directory) -> str:
    if e.get("_no_enrolment"):
        return "no first-time enrolment for this lead in the period, so no owner at enrolment"
    # credit_enrolments leaves owner_kind empty when the owner is not a listed user; the name still tells a
    # shared login or an automation account apart from a missing user.
    kind = e.get("owner_kind") or (directory.kind(e.get("owner_name"), None) if e.get("owner_name") else None)
    if kind == SHARED:
        return "owner was a shared login, which is not one person"
    if kind == BOT_KIND:
        return "owner was an automation account"
    if e.get("owner_id") or e.get("owner_name"):
        return "owner not found among LeadSquared users"
    return "no owner recorded"


def _credit(credited: list[dict], directory: Directory, value=lambda e: 1) -> dict:
    """``value`` (1 per enrolment by default) per team and per person in both views, plus what is unattributed and why."""
    out = {"byTeam": {OWNER: Counter(), LAST: Counter()}, "people": {OWNER: {}, LAST: {}},
           "unattributed": {OWNER: 0, LAST: 0}, "reasons": {OWNER: Counter(), LAST: Counter()}}

    def add(view, team, pid, name, v):
        out["byTeam"][view][team] += v
        p = out["people"][view].setdefault(pid or name, {"callerId": pid, "caller": name, "team": team, "v": 0})
        p["v"] += v

    for e in credited:
        v = value(e)
        if e.get("owner_team"):
            u = directory.find(e.get("owner_id"), e.get("owner_name")) or {}
            add(OWNER, e["owner_team"], u.get("ID") or e.get("owner_id"), user_name(u) or e.get("owner_name"), v)
        else:
            out["unattributed"][OWNER] += v
            out["reasons"][OWNER][_owner_gap(e, directory)] += 1
        if e.get("last_call_id"):
            add(LAST, e.get("last_caller_team") or NO_TEAM, e.get("last_caller_id"), e.get("last_caller"), v)
        else:
            out["unattributed"][LAST] += v
            out["reasons"][LAST]["no answered call by a person on the lead at or before it, in the calls read"] += 1
    return out


def _money(x: float) -> int | float:
    return round(x) if abs(x - round(x)) < 0.005 else round(x, 2)


def revenue_section(enrolments: list[dict], calls: list[dict], users: list[dict], payments: list[dict],
                    amount_field: str | None = None) -> dict:
    """The snapshot's "revenue" section. ``amount_field`` ("data.Amount", "top.mx_Custom_2" or a bare field
    name) picks the amount once event 213 has been inspected; otherwise a field named like an amount is used."""
    d = Directory(users)
    pays = _unique(payments)
    secs = [_sections(a) for a in pays]
    field = _pick(secs, amount_field) if pays else None
    amounts = [(a, x) for a, s in zip(pays, secs) if field and (x := _read_amount(s, field)) is not None]
    measurable = bool(amounts)
    if not pays:
        reason = NOT_MEASURABLE_EMPTY
    elif not measurable:
        what = (f"the field {field} holds no readable amount" if amount_field else
                "none has a field named like an amount (amount, fee, price, paid) holding a number")
        reason = (f"LeadSquared returned {len(pays)} payment record{'s' if len(pays) != 1 else ''}, but {what}; "
                  "enrolments are shown instead. See 'fields' to choose the amount field.")
    else:
        reason = f"Amounts read from the payment field {field} ({len(amounts)} of {len(pays)} payment records have one)."
        others = [c["field"] for c in _candidates(secs) if c["field"] != field]
        if others and not amount_field:   # a guessed field: say what else could be the amount
            reason += (f" Other fields named like an amount: {', '.join(others[:5])}. Check {field} is the amount "
                       "paid and set payment_amount_field if not.")

    # A first-time enrolment happens once per lead: a lead read twice still counts once in each view.
    first: dict[str, dict] = {}
    for e in sorted(enrolments or [], key=lambda e: e.get("at_utc") or ""):
        first.setdefault(e.get("lead_id") or f"_{id(e)}", e)
    enrolments = list(first.values())
    enrol = _credit(credit_enrolments(enrolments, calls, d), d)
    rev = None
    if measurable:
        pseudo = []
        for a, x in amounts:
            lead = a.get("RelatedProspectId") or a.get("ProspectId") or a.get("LeadId")
            fe = first.get(lead)
            pseudo.append({"lead_id": lead, "at_utc": a.get("CreatedOn"), "owner_id": (fe or {}).get("owner_id"),
                           "owner_name": (fe or {}).get("owner_name"), "_no_enrolment": fe is None, "_amount": x})
        rev = _credit(credit_enrolments(pseudo, calls, d), d, value=lambda e: e["_amount"])

    def detail(view):
        rows: dict = {}
        for src, key in ((enrol, "enrolments"), (rev, "revenue")):
            for k, p in (src["people"][view] if src else {}).items():
                r = rows.setdefault(k, {"callerId": p["callerId"], "caller": p["caller"], "team": p["team"],
                                        "enrolments": 0, "revenue": 0 if measurable else None})
                r[key] = _money(p["v"]) if key == "revenue" else r[key] + p["v"]
        return sorted(rows.values(), key=lambda r: (-(r["revenue"] or 0), -r["enrolments"], r["caller"] or ""))

    def teams(src):
        return {v: {t: _money(n) for t, n in src["byTeam"][v].most_common()} for v in (OWNER, LAST)}

    def credited(src):
        return {"byTeam": teams(src), "unattributed": {v: _money(src["unattributed"][v]) for v in (OWNER, LAST)},
                "unattributedReasons": {v: dict(src["reasons"][v].most_common()) for v in (OWNER, LAST)}}

    people = {v: detail(v) for v in (OWNER, LAST)}
    return {
        "measurable": measurable, "reason": reason, "paymentEvents": len(pays), "amountField": field if measurable else None,
        "total": _money(sum(x for _, x in amounts)) if measurable else None, "currency": "INR",
        "enrolments": {"total": len(enrolments), **credited(enrol)},
        "amounts": None if not measurable else {"payments": len(amounts), "withoutAmount": len(pays) - len(amounts),
                                                **credited(rev)},
        # name -> first-time enrolments credited; byCallerDetail adds ids, teams and (when measurable) revenue
        "byCaller": {v: {r["caller"]: r["enrolments"] for r in people[v] if r["enrolments"]} for v in (OWNER, LAST)},
        "byCallerDetail": people,
        "fields": payment_fields(pays),
        "notes": [
            "Revenue is counted only from LeadSquared payment records (event 213) with a readable amount; it is "
            "never estimated from enrolments or an average fee.",
            "Each first-time enrolment is credited once per view: to the team of the lead owner recorded with the "
            "enrolment (the owner's current LeadSquared group), and to the team of the last person with an answered "
            "call on the lead at or before it (the team stamped on that call). Otherwise it is unattributed.",
            "Payments are credited the same way: the owner at the lead's first-time enrolment, and the last "
            "person with an answered call before the payment."],
    }


# ------------------------------------------------------------------ opportunities

def _t(c: dict) -> datetime | None:
    return c.get("t") or utc(c.get("start_utc"))


def _int(x) -> int | None:
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _findings(fs) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fs or []:
        if isinstance(f, dict) and f.get("category"):
            c = f.get("confidence") or "low"
            if f["category"] not in out or _CONF.get(c, 2) < _CONF.get(out[f["category"]], 2):
                out[f["category"]] = c
    return out


def _cat(x) -> str:
    """Only the fixed vocabulary reaches evidence text, so a malformed layer result can't carry words through."""
    return x if x in OBJECTION_CATEGORIES else "other"


def read(c: dict) -> dict | None:
    """One call's reading by Claude in one shape. None when Claude has not read the call, or when the reading says
    it was not a real conversation or names IVR, voicemail, looping or empty text (even on a doubtful verdict):
    such calls belong in the integrity view, not in opportunities. Readiness is None when the reading itself calls
    it unclear."""
    sem = c.get("sem")
    if not (isinstance(sem, dict) and sem):
        return None
    integ = sem.get("integrity") or {}
    if integ.get("real_conversation") == "no" or set(integ.get("flags") or ()) & NOT_REAL_FLAGS:
        return None
    intent, out = sem.get("intent") or {}, sem.get("outcome") or {}
    band = intent.get("readiness_band")
    return {"engine": "semantic", "band": band,
            "readiness": _int(intent.get("readiness_score")) if band != "unclear" else None,
            "objections": [(_cat(o.get("category")), o.get("handled")) for o in sem.get("objections") or []
                           if isinstance(o, dict) and o.get("category")],
            "signals": [b.get("type") for b in sem.get("buying_signals") or []
                        if isinstance(b, dict) and b.get("type") in SIGNAL_TYPES],
            "payment_step": out.get("payment_step") if out.get("payment_step") in _STEPS else "none",
            "dated": bool(out.get("dated")),
            "findings": _findings(sem.get("findings"))}


def _conf(r: dict, strong: bool) -> str:
    """High when two pieces of evidence in the reading agree, else medium."""
    return "high" if strong else "medium"


def _label(x: str) -> str:
    return (x or "").replace("_", " ")


def _when(t: datetime) -> str:
    return t.astimezone(IST).strftime("%d %b %H:%M IST").lstrip("0")


def _dur(c: dict) -> str:
    d = c.get("duration_s")
    if not c.get("answered"):
        return "not answered"
    if not d or d <= 0:
        return "length not recorded"
    return f"{d} s" if d < 60 else f"{d // 60} min {d % 60} s" if d % 60 else f"{d // 60} min"


def _intro(r: dict, t: datetime, c: dict) -> str:
    score = f"readiness {r['readiness']}/100" if r["readiness"] is not None else "readiness unclear"
    return f"Claude's reading of the {_when(t)} call ({_dur(c)}): {score}"


def _signals(r: dict) -> str:
    return f"; buying signals: {', '.join(_label(s) for s in dict.fromkeys(r['signals']))}" if r["signals"] else ""


def _is_person_name(name: str | None) -> bool:
    n = (name or "").strip()
    return bool(n) and n not in SHARED_ACCOUNTS and n not in AUTOMATION and not BOT.search(n)


def _row(kind: str, lid: str, t: datetime, c: dict, evidence: str, action: str, confidence: str,
         score: int | None = None, caller: dict | None = None) -> dict:
    c2 = caller or c
    person = c2.get("caller_kind") == PERSON
    return {"kind": kind, "leadId": lid, "callId": c.get("call_id"),
            "callerId": c2.get("caller_id") if person else None, "caller": c2.get("caller_name") if person else None,
            "team": c2.get("team") if person else None, "day": ist_day(t), "evidence": evidence, "nextAction": action,
            "confidence": confidence,
            "_sort": (KINDS.index(kind), _CONF[confidence], -(score if score is not None else -1), -t.timestamp(), lid)}


def _agreed(r: dict) -> bool:
    return "payment_ready" in r["findings"] or r["payment_step"] in ("amount_and_date_agreed", "link_sent")


def _payment_step_taken(r: dict) -> bool:
    return r["payment_step"] in ("link_sent", "amount_and_date_agreed")


class _Lead:
    """One lead's calls in time order, with what every opportunity check needs."""

    def __init__(self, lid: str, cs: list[tuple[datetime, dict]], lead: dict, now: datetime, seen_until: datetime):
        self.lid, self.now, self.seen_until = lid, now, seen_until
        self.analysed, self.answered, self.missed, self.dials, self.has_real = [], [], [], [], False
        for t, c in cs:     # one pass: this runs for every lead of a 30-day period
            if c.get("sem") and (r := read(c)):
                self.analysed.append((t, c, r))
            if c.get("answered"):
                if c.get("caller_kind") != BOT_KIND:    # an automated call is nobody speaking to the lead
                    self.answered.append(t)
            elif c.get("direction") == "inbound":
                self.missed.append((t, c))
            if c.get("direction") == "outbound" and c.get("caller_kind") != BOT_KIND:
                self.dials.append((t, c))
            self.has_real = self.has_real or c.get("call_class") == REAL_CALL
        self.real = [x for x in self.analysed if x[1].get("call_class") == REAL_CALL]
        # The call that says where the lead stands: the latest real call, else the latest analysed call; a call
        # whose readiness is "unclear" (too thin to read) and that agreed nothing is skipped, never read as cold.
        told = lambda x: x[2]["readiness"] is not None or _agreed(x[2])  # noqa: E731
        self.key = next((x for x in reversed(self.real) if told(x)), None) or next(
            (x for x in reversed(self.analysed) if told(x)), None)
        self.stage = (lead.get("stage") or "").strip()
        self.course = (lead.get("course") or "").strip()
        self.owner = lead.get("owner_name") if _is_person_name(lead.get("owner_name")) else None

    def answered_within(self, t: datetime, window: timedelta) -> bool:
        i = bisect_right(self.answered, t)
        return i < len(self.answered) and self.answered[i] <= t + window

    def latest(self, test) -> tuple | None:
        return next((x for x in reversed(self.analysed) if test(x[2])), None)


def _payment_ready(L: _Lead) -> dict | None:
    if not L.key:
        return None
    t, c, r = L.key
    hot = (r["readiness"] or 0) >= HIGH_READINESS or r["band"] == "hot"
    agreed = _agreed(r)
    if not (hot or agreed):
        return None
    step = f"; payment step: {_label(r['payment_step'])}" if r["payment_step"] not in ("none", "unclear") else ""
    return _row("payment_ready_unconverted", L.lid, t, c, f"{_intro(r, t, c)}{_signals(r)}{step}. Not enrolled yet.",
                "Call today, confirm the amount and the EMI or full-payment option, send the payment link on the call "
                "and stay on until the payment goes through or a payment time is fixed.",
                _conf(r, hot and agreed), r["readiness"])


def _link_sent(L: _Lead) -> dict | None:
    if not L.analysed:
        return None
    x = L.latest(lambda r: r["payment_step"] == "link_sent")
    if not x or L.now - x[0] < LINK_WAIT:      # the latest link is still fresh: not judged yet
        return None
    t, c, r = x
    sent = ("a payment link was sent on the call" if r["payment_step"] == "link_sent"
            else "payment-step words (payment link, pay now) came up on the call")
    return _row("link_sent_unpaid", L.lid, t, c,
                f"{_intro(r, t, c)}; {sent}. {int((L.now - t).total_seconds() // 3600)} h later the lead has not enrolled.",
                "Call to check whether the payment went through and what stopped it (link expired, UPI or card failure, "
                "EMI paperwork); resend the link on the call if needed.",
                "high" if r["payment_step"] == "link_sent" else "low", r["readiness"])


def _missed_callback(L: _Lead) -> dict | None:
    missed = [(t, c) for t, c in L.missed
              if L.seen_until - t >= CALLBACK_WINDOW and not L.answered_within(t, CALLBACK_WINDOW)]
    if not missed:
        return None
    t, c = missed[-1]
    later = next((a for a in L.answered if a > t), None)
    times = f"rang in {len(missed)} times without an answer, last" if len(missed) > 1 else "rang in without an answer"
    ev = f"The lead {times} at {_when(t)}; no answered call to the lead in the 2 h after."
    if later:
        ev += f" A call was answered {int((later - t).total_seconds() // 3600)} h later."
        act = "Check the later call covered what the lead rang about, and return missed inbound calls within 2 h."
    else:
        act = (f"{L.owner + ', c' if L.owner else 'C'}all the lead back now: they called us and no answered call "
               "to them is on record since.")
    return _row("missed_callback", L.lid, t, c, ev, act, "medium" if later else "high")


def _marked_low(L: _Lead) -> dict | None:
    if not L.analysed:
        return None
    # The lead's readiness as the journey view reads it: the latest score Claude gave.
    x = L.latest(lambda r: r["readiness"] is not None)
    if not x or x[2]["readiness"] < HIGH_READINESS:
        return None
    t, c, r = x
    zi = ((c.get("zip") or {}).get("intent") or "").upper()
    dead, low_zip = L.stage in DEAD_STAGES, zi in ("LOW", "NEUTRAL")
    if not (dead or low_zip):
        return None
    why = " and ".join(w for w in (f"the LeadSquared stage is {L.stage}" if dead else "",
                                   f"Zipteams rated the call {zi}" if low_zip else "") if w)
    return _row("high_intent_marked_low", L.lid, t, c, f"{_intro(r, t, c)}{_signals(r)}; but {why}.",
                f"Team leader: review the {_when(t)} call; if the lead is still interested, correct the stage and "
                "give the lead a call today.", _conf(r, dead and low_zip), r["readiness"])


def _emi_friction(L: _Lead) -> dict | None:
    hit = lambda r: "payment_friction" in r["findings"] or any(  # noqa: E731
        cat == "emi_or_finance" and h != "yes" for cat, h in r["objections"])
    i = next((i for i in range(len(L.analysed) - 1, -1, -1) if hit(L.analysed[i][2])), None)
    if i is None or any(_payment_step_taken(r) for _, _, r in L.analysed[i + 1:]):
        return None
    t, c, r = L.analysed[i]
    both = "payment_friction" in r["findings"] and any(cat == "emi_or_finance" for cat, _ in r["objections"])
    return _row("emi_friction", L.lid, t, c,
                f"{_intro(r, t, c)}; the lead raised EMI, loan or payment-method trouble and no payment step followed.",
                "Call back with the EMI and part-payment options and complete the application on the call; if their "
                "finance was refused, offer the card or other lender option.", _conf(r, both), r["readiness"])


def _unresolved_objection(L: _Lead) -> dict | None:
    if not L.real:
        return None
    t, c, r = L.real[-1]
    open_ = sorted(((cat, h) for cat, h in r["objections"] if h in ("no", "partly") and cat not in _OWN_KIND),
                   key=lambda x: x[1] != "no")
    if not open_:
        return None
    cats = ", ".join(f"{_label(cat)} (answered: {h})" if h else _label(cat) for cat, h in dict.fromkeys(open_))
    return _row("unresolved_objection", L.lid, t, c, f"{_intro(r, t, c)}; the lead raised {cats}.",
                OBJECTION_ACTIONS.get(open_[0][0], OBJECTION_ACTIONS["other"]),
                _conf(r, any(h == "no" for _, h in open_)), r["readiness"])


def _course_unavailable(L: _Lead) -> dict | None:
    if not L.analysed:
        return None
    x = L.latest(lambda r: "course_unavailable" in r["findings"]
                 or any(cat == "course_unavailable" for cat, _ in r["objections"]))
    if not x:
        return None
    t, c, r = x
    both = "course_unavailable" in r["findings"] and any(cat == "course_unavailable" for cat, _ in r["objections"])
    # The course comes from the lead record, never from the transcript's words.
    course = f" (course on the lead record: {L.course[:60]})" if L.course else ""
    return _row("course_unavailable", L.lid, t, c,
                f"{_intro(r, t, c)}; the lead asked for a course or batch we may not offer{course}.",
                "Check with the team leader whether this course or batch runs; call back with its start date or the "
                "closest course we offer.", _conf(r, both), r["readiness"])


def _weak_follow_up(L: _Lead) -> dict | None:
    if not L.real:
        return None
    t, c, r = L.real[-1]
    sc = r["readiness"]
    if (sc is None or sc < FOLLOW_UP_READINESS or r["dated"] or L.seen_until - t < FOLLOW_UP_WINDOW
            or L.answered_within(t, FOLLOW_UP_WINDOW)):
        return None
    return _row("weak_follow_up", L.lid, t, c,
                f"{_intro(r, t, c)}; no dated next step was agreed and nobody spoke to the lead in the 48 h after.",
                "Call today and end the call with a fixed date and time for the next step (payment or decision).",
                _conf(r, sc >= HIGH_READINESS), sc)


def _repeated_dials(L: _Lead) -> dict | None:
    dials = L.dials
    if len(dials) < REPEATED_DIALS or L.has_real or L.stage in DEAD_STAGES:
        return None
    t, c = dials[-1]
    n, answered = len(dials), [c2 for _, c2 in dials if c2.get("answered")]
    failed = sum(1 for _, c2 in dials if (c2.get("call_status") or "").replace(" ", "").lower() == "callfailure")
    longest = max((c2.get("duration_s") or 0 for c2 in answered), default=0)
    parts = Counter(next(p for p, end, _ in DAY_PARTS if t2.astimezone(IST).hour < end) for t2, _ in dials)
    ev = (f"{n} dials in this period, {len(answered)} answered" + (f" (longest {longest} s)" if longest else "")
          + ", no conversation of 3+ minutes; dials by time of day (IST): "
          + ", ".join(f"{p} {parts[p]}" for p, _, _ in DAY_PARTS if parts[p]) + ".")
    untried = [(p, when) for p, _, when in DAY_PARTS if not parts[p]]
    if failed / n >= DIALER_FAILURE_SHARE:   # fix the dialer before judging the caller
        ev += f" {failed} of {n} dials failed to connect."
        act = "Check the number and the dialer before dialling again: most dials failed to connect."
    elif untried:   # working people answer after office, so the evening is tried first
        act = f"Stop dialling at the same time of day: try once {untried[-1][1]}, and check the number is right."
    else:
        act = "Dials already cover the whole day: check the number is right, then try once on another day."
    top = Counter(c2.get("caller_id") for _, c2 in dials if c2.get("caller_kind") == PERSON).most_common(1)
    who = next((c2 for _, c2 in reversed(dials) if top and c2.get("caller_id") == top[0][0]), None)
    return _row("repeated_dials_no_conversation", L.lid, t, c, ev, act, "high", caller=who or {"caller_kind": None})


CHECKS = (_payment_ready, _link_sent, _missed_callback, _marked_low, _emi_friction, _unresolved_objection,
          _course_unavailable, _weak_follow_up, _repeated_dials)


def opportunities(calls: list[dict], leads: dict, enrolments: list[dict], now: datetime,
                  calls_until: datetime | None = None) -> list[dict]:
    """Opportunity rows for the snapshot: one per (kind, lead), most valuable first; enrolled leads skipped.
    Time-based kinds are judged only once their window has passed by ``now``. ``calls_until`` is where the calls
    read end (e.g. the end of a past period's last IST day): a 2 h callback or 48 h follow-up window that runs
    past it is not judged, because a call made after it would not be seen."""
    leads = leads or {}
    seen_until = min(now, calls_until) if calls_until else now
    enrolled = {e.get("lead_id") for e in enrolments or []} | {
        lid for lid, l in leads.items() if (l or {}).get("stage") == ENROLLED}
    by_lead: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    worth = set()   # leads some check could fire on; most leads only have a few unanswered dials
    for c in calls:
        lid = c.get("lead_id")
        if lid and lid not in enrolled and (t := _t(c)):
            by_lead[lid].append((t, c))
            if c.get("sem") or (c.get("direction") == "inbound" and not c.get("answered")):
                worth.add(lid)
    rows = []
    for lid, cs in by_lead.items():
        if lid not in worth and len(cs) < REPEATED_DIALS:
            continue
        cs.sort(key=lambda x: x[0])
        lead = _Lead(lid, cs, leads.get(lid) or {}, now, seen_until)
        rows += [r for check in CHECKS if (r := check(lead))]
    rows.sort(key=lambda r: r["_sort"])
    return [{k: r[k] for k in ROW_KEYS} for r in rows]


def main(argv: list[str] | None = None) -> None:
    path = (argv if argv is not None else sys.argv[1:])[:1]
    if not path:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    data = json.load(open(path[0], encoding="utf-8"))
    pays = data.get("payments") or [] if isinstance(data, dict) else data   # a payments list or a team snapshot
    print(json.dumps(payment_fields(pays), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
