"""Accountability bridge: analytics/accountability.py's action rows as ``lead_accountability_findings`` rows.

Who assigned, transferred, changed the stage or owned a follow-up comes from analytics/accountability.py. It
credits owner changes from LeadSquared's owner-change log and counts the lead's Assigned By field only when
the change was made from a shared login and the field belongs to that change (user's decision, 9 Oct:
"Change log first"). This module keeps that output, keeps Assigned By as its own field, and adds what the
call data knows: the last person who spoke to the lead at or before the action and the owner at enrolment.

The rules are enforced again here: a shared admin login (Rinku Jhala, Admin) is never the person who acted
or the responsible person; a missing, ambiguous or shared-login-only actor is UNVERIFIED; no performer is
ever made up. A team leader's correction is kept next to the CRM's original attribution, never over it
(analytics/accountability.py overwrites the performer and keeps the old evidence in "(was: ...)"; its audit
log keeps the old performer, so pass it as ``audit`` to keep that too).

LeadSquared keeps no team history, so ``team_at_action_time`` is the responsible person's current team.
Nothing here writes to LeadSquared.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta

from analytics.accountability import (ASSIGNED_BY, AUTOMATED, CORRECTED, SHARED_ACCOUNTS, UNVERIFIED as A_UNVERIFIED,
                                      VERIFIED as A_VERIFIED)
from analytics.convintel.attribution import BOT_KIND, PERSON, SHARED, Directory, user_name, user_team
from analytics.convintel.store import ts
from integrations.timeutil import IST, now_utc, utc

VERIFIED, VERIFIED_ASSIGNED_BY, UNVERIFIED, AUTOMATED_S, CORRECTED_S = (
    "VERIFIED", "VERIFIED_ASSIGNED_BY", "UNVERIFIED", "AUTOMATED", "CORRECTED")
STATUSES = (VERIFIED, VERIFIED_ASSIGNED_BY, UNVERIFIED, AUTOMATED_S, CORRECTED_S)
STATUS_MAP = {A_VERIFIED: VERIFIED, ASSIGNED_BY: VERIFIED_ASSIGNED_BY, A_UNVERIFIED: UNVERIFIED,
              AUTOMATED: AUTOMATED_S, CORRECTED: CORRECTED_S}
NAMED = (VERIFIED, VERIFIED_ASSIGNED_BY, CORRECTED_S)     # statuses that name one person
AUTOMATION_LABEL = "System (automation)"
COLUMNS = ("finding_id", "lead_id", "action", "action_timestamp_utc", "account_owner", "assigned_by", "assigned_to",
           "transferred_by", "transferred_to", "action_performed_by", "responsible_person", "followup_owner",
           "team_at_action_time", "owner_at_enrolment", "last_answered_caller", "status", "evidence",
           "source_attribution", "corrected_attribution", "created_utc")
TEAM_NOTE = "LeadSquared keeps no team history, so the team shown is the responsible person's current team."
_WAS = " (was: "
_CONFIRMED = re.compile(r"^confirmed by (.*?)(?:: (.*))?$", re.S)


def _parse_ist(s: str | None) -> datetime | None:
    """accountability.py writes action times as IST 'YYYY-MM-DD HH:MM'."""
    try:
        return datetime.strptime((s or "").strip()[:16], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    except ValueError:
        return None


class _Names:
    def __init__(self, users: list[dict], shared):
        self.d = Directory(users, shared=shared)
        # The logs name a login by its display name only, so a name two users share is ambiguous.
        self.dup_names = {n: k for n, k in Counter(user_name(u) for u in users).items() if n and k > 1}

    def person(self, name: str | None) -> str | None:
        """The name when it is one person; None for blanks, placeholders, shared logins, automation and a name
        more than one LeadSquared user has."""
        n = (name or "").strip()
        if not n or n.lower() in ("unverified", AUTOMATION_LABEL.lower()) or n.lower().startswith("no one"):
            return None
        return n if n not in self.dup_names and self.d.kind(n, self.d.find(None, n)) not in (SHARED, BOT_KIND) else None

    def team(self, name: str | None) -> str | None:
        u = self.d.find(None, name)
        return user_team(u) if u and self.d.kind(name, u) == PERSON else None


def _originals(audit: list[dict] | None) -> dict[str, dict]:
    """Per action key, the last attribution in the audit log that was not a correction."""
    out: dict[str, dict] = {}
    for e in audit or []:
        if e.get("status") and e["status"] != CORRECTED:
            out[e.get("key")] = {"performed_by": e.get("performed_by"), "status": e["status"]}
        elif (p := e.get("previous")) and p.get("status") != CORRECTED:
            out[e.get("key")] = {"performed_by": p.get("performed_by"), "status": p.get("status")}
    return out


def _split_correction(evidence: str) -> tuple[str, str | None, str | None, str | None]:
    """(evidence before the correction, confirmed by, note, original evidence) from a Corrected row."""
    i = evidence.rfind(_WAS)
    if i < 0 or not evidence.endswith(")"):
        return evidence, None, None, None
    head, orig = evidence[:i], evidence[i + len(_WAS):-1]
    m = _CONFIRMED.match(head)
    return head, (m.group(1) or None) if m else None, (m.group(2) or None) if m else None, orig


def _last_callers(calls: list[dict]) -> dict[str, tuple[list[datetime], list[str]]]:
    by_lead: dict[str, list[tuple[datetime, str]]] = {}
    for c in calls:
        t = c.get("t") or utc(c.get("start_utc"))
        if c.get("answered") and t and c.get("caller_kind") == PERSON and c.get("lead_id"):
            by_lead.setdefault(c["lead_id"], []).append((t, c.get("caller_name")))
    out = {}
    for lid, v in by_lead.items():
        v.sort(key=lambda x: x[0])
        out[lid] = ([t for t, _ in v], [n for _, n in v])
    return out


def _g(a: dict, k: str) -> str | None:
    return (a.get(k) or "").strip() or None


def _status(a: dict, names: _Names) -> tuple[str, str | None, list[str]]:
    """(status, the person who acted or None, notes): a person-naming status that names no one person becomes
    UNVERIFIED, so a shared login, automation or a blank is never charged."""
    raw, named = _g(a, "accountability_status"), _g(a, "action_performed_by")
    status, notes = STATUS_MAP.get(raw, UNVERIFIED), []
    if raw not in STATUS_MAP:
        notes.append(f"status {raw or 'blank'} is not one the accountability report uses, so it is Unverified")
    performer = names.person(named) if status in NAMED else None
    if status in NAMED and not performer:
        if named in names.dup_names:
            notes.append(f"{names.dup_names[named]} LeadSquared users are named {named}, so it is not clear which "
                         "one; Unverified")
        else:
            notes.append(f"{named or 'no one'} is not one person (shared login, automation or blank), so it is Unverified")
        status = UNVERIFIED
    return status, performer, notes


def _attributions(a: dict, status: str, did: str | None, names: _Names, orig: dict[str, dict] | None
                  ) -> tuple[dict, dict | None]:
    """(what the CRM evidence said, the team leader's correction or None). Both are kept for a corrected row."""
    evidence = a.get("evidence") or ""
    source = {"status": STATUS_MAP.get(_g(a, "accountability_status")), "performed_by": did,
              "login_used": _g(a, "login_used"), "assigned_by_field": _g(a, "assigned_by_field"),
              "possible_actor": _g(a, "possible_actor"), "evidence": evidence}
    if _g(a, "accountability_status") != CORRECTED:
        return source, None
    _, by, note, was = _split_correction(evidence)
    o = (orig or {}).get(_g(a, "key")) or {}
    source.update(status=STATUS_MAP.get(o.get("status")), performed_by=names.person(o.get("performed_by")), evidence=was)
    if orig is None:
        source["note"] = "the original performer is kept only in the accountability audit log, which was not given"
    elif not o:
        source["note"] = "the accountability audit log has no attribution from before this correction"
    return source, {"named": _g(a, "action_performed_by"), "accepted": status == CORRECTED_S, "confirmed_by": by,
                    "note": note}


def findings(action_rows: list[dict], calls: list[dict], enrolments: list[dict], users: list[dict],
             audit: list[dict] | None = None, now: datetime | None = None, shared=SHARED_ACCOUNTS) -> list[dict]:
    """``lead_accountability_findings`` rows from analytics/accountability.py action rows (its COLUMNS, e.g.
    read from actions.csv). ``audit`` (its audit.jsonl entries) keeps a corrected action's original performer."""
    names = _Names(users, shared)
    created = ts(now or now_utc())
    orig = _originals(audit) if audit is not None else None
    spoke = _last_callers(calls)
    owner_at_enrol: dict[str, str | None] = {}
    for e in sorted(enrolments, key=lambda e: e.get("at_utc") or ""):
        owner_at_enrol.setdefault(e.get("lead_id"), (e.get("owner_name") or "").strip() or None)
    out = []
    for a in action_rows:
        lid, action, key = _g(a, "lead_id"), _g(a, "action") or "", _g(a, "key")
        at = _parse_ist(a.get("action_time_ist"))
        status, performer, notes = _status(a, names)
        did = performer if status in NAMED else AUTOMATION_LABEL if status == AUTOMATED_S else None
        source, corrected = _attributions(a, status, did, names, orig)
        if status == UNVERIFIED and (maybe := names.person(_g(a, "possible_actor"))):
            notes.append(f"name to check with the team (not proof): {maybe}")
        team = names.team(performer)
        if team:
            notes.append(f"team is {performer}'s current team (no team history in LeadSquared)")
        actor = lambda col: (None if not _g(a, col) else names.person(_g(a, col)) if status in NAMED  # noqa: E731
                             else AUTOMATION_LABEL if status == AUTOMATED_S else None)
        before = spoke.get(lid)
        i = bisect_right(before[0], at + timedelta(seconds=59)) if before and at else 0   # the log keeps minutes
        evidence = a.get("evidence") or ""
        out.append({
            "finding_id": f"acc:{key}" if key else "acc:" + hashlib.sha1(
                f"{lid}|{action}|{a.get('action_time_ist')}".encode()).hexdigest()[:16],
            "lead_id": lid, "action": action, "action_timestamp_utc": ts(at), "account_owner": _g(a, "account_owner"),
            "assigned_by": actor("assigned_by"), "assigned_to": _g(a, "assigned_to"),
            "transferred_by": actor("transferred_by"),
            "transferred_to": _g(a, "assigned_to") if action in ("transfer", "moved into shared account") else None,
            "action_performed_by": did, "responsible_person": performer,
            "followup_owner": names.person(_g(a, "account_owner")), "team_at_action_time": team,
            "owner_at_enrolment": owner_at_enrol.get(lid), "last_answered_caller": before[1][i - 1] if i else None,
            "status": status, "evidence": "; ".join(x for x in [evidence] + notes if x),
            "source_attribution": source, "corrected_attribution": corrected, "created_utc": created})
    return out


def _action_kind(action: str) -> str:
    """"stage change to X" -> "stage change"; "follow-up done by X" names who dialled, not who is credited."""
    a = (action or "").split(" to ")[0]
    return "follow-up done by someone else" if a.startswith("follow-up done by ") else a


def summary(rows: list[dict]) -> dict:
    """The snapshot's "accountability" section."""
    by_status = Counter(r["status"] for r in rows)
    people = Counter(r["responsible_person"] for r in rows if r.get("responsible_person"))
    notes = ["A shared admin login (Rinku Jhala, Admin) is never named as the person who acted; an action made "
             "through one is Unverified unless the lead's Assigned By field can be tied to that owner change or a "
             "team leader's correction names a person.",
             "A name that is missing, a placeholder or shared by two LeadSquared users is Unverified, not guessed.",
             "Owner changes are credited from LeadSquared's owner-change log; the Assigned By field counts only when "
             "the change was made from a shared login and the field belongs to that change.",
             TEAM_NOTE, "The last answered caller is read from the calls in this period only."]
    if by_status.get(CORRECTED_S):
        notes.append(f"{by_status[CORRECTED_S]} actions were corrected by a team leader; the CRM's original "
                     "attribution is kept next to each correction.")
    if not rows:
        notes.append("No accountability actions were given for this period (run analytics.accountability for it).")
    return {"rows": len(rows), "byStatus": {s: by_status.get(s, 0) for s in STATUSES},
            "byPerson": [[p, n] for p, n in sorted(people.items(), key=lambda x: (-x[1], x[0]))],
            "byAction": dict(Counter(_action_kind(r["action"]) for r in rows).most_common()),
            "unverified": by_status.get(UNVERIFIED, 0), "notes": notes}
