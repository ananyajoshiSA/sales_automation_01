"""Who a call or an enrolment belongs to: one person and one team, so nothing is counted twice.

A call belongs to the user who made or took it and to that user's team when the call was inventoried (the
first LeadSquared group, as in the calling report). Shared admin logins (``SHARED_ACCOUNTS``, e.g. Rinku
Jhala's) are never a person and LeadSquared automation is a bot: both stay in organisation totals and
coverage but never in caller rankings.

An enrolment is credited once per view: to the lead owner's team at enrolment, and separately to the last
person whose answered call on the lead came at or before it. When neither is known it is "unattributed".
"""

from __future__ import annotations

import re
from bisect import bisect_right
from datetime import datetime

from analytics.accountability import AUTOMATION, SHARED_ACCOUNTS
from analytics.team_performance import BOT
from integrations.timeutil import utc

UNASSIGNED = "Unassigned"
NOT_A_USER = "Not a user"
PERSON, SHARED, BOT_KIND, NOT_USER = "person", "shared", "bot", "not_a_user"
NUMBER_NAME = "(a phone number, not a LeadSquared user)"
_DIGITS = re.compile(r"\d")


def safe_name(name: str | None) -> str | None:
    """Some calls name their caller by a phone number (a dialer line that is not a LeadSquared user); that name
    is replaced, so no phone number travels with caller names into snapshots or the dashboard."""
    return NUMBER_NAME if name and len(_DIGITS.findall(name)) >= 7 else name


def user_name(u: dict) -> str:
    return f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip()


def user_team(u: dict | None) -> str:
    gs = (u or {}).get("MemberOfGroups") or []
    return gs[0].strip() if gs and gs[0].strip() else UNASSIGNED


class Directory:
    """LeadSquared users by id and by name, with each one's team and kind."""

    def __init__(self, users: list[dict], shared=SHARED_ACCOUNTS, automation=AUTOMATION):
        self.by_id = {u["ID"]: u for u in users if u.get("ID")}
        self.by_name = {user_name(u): u for u in users if user_name(u)}
        self.shared = {s.strip() for s in shared if s.strip()}
        self.automation = set(automation)

    def find(self, user_id: str | None, name: str | None = None) -> dict | None:
        return self.by_id.get(user_id or "") or self.by_name.get((name or "").strip())

    def kind(self, name: str | None, user: dict | None) -> str:
        name = (name or "").strip()
        if name in self.shared:
            return SHARED
        if name in self.automation or (name and BOT.search(name)):
            return BOT_KIND
        return PERSON if user else NOT_USER

    def person(self, user_id: str | None, name: str | None = None) -> dict:
        """{"id", "name", "team", "kind"} for a call's user; unknown users keep the name the record gave."""
        u = self.find(user_id, name)
        nm = user_name(u) if u else safe_name((name or "").strip()) or "(unknown)"
        return {"id": (u or {}).get("ID") or user_id, "name": nm,
                "team": user_team(u) if u else NOT_A_USER, "kind": self.kind(nm, u)}


def credit_enrolments(enrolments: list[dict], calls: list[dict], directory: Directory) -> list[dict]:
    """Each enrolment plus ``owner_team`` and the last answered call by a person at or before it
    (``last_call_id``, ``last_caller_id``, ``last_caller``, ``last_caller_team``; None when there is none).

    ``enrolments``: {"lead_id", "at_utc", "owner_id", "owner_name", ...}; ``calls``: registry rows with
    ``answered``, ``start_utc``, ``caller_id``, ``caller_name``, ``caller_kind`` and ``team``."""
    by_lead: dict[str, list[tuple[datetime, dict]]] = {}
    for c in calls:
        t = c.get("t") or utc(c.get("start_utc"))
        if c.get("answered") and t and c.get("caller_kind") == PERSON and c.get("lead_id"):
            by_lead.setdefault(c["lead_id"], []).append((t, c))
    for v in by_lead.values():
        v.sort(key=lambda x: x[0])
    out = []
    for e in enrolments:
        at = utc(e.get("at_utc"))
        owner = directory.find(e.get("owner_id"), e.get("owner_name"))
        owner_kind = directory.kind(user_name(owner) if owner else e.get("owner_name"), owner)
        hist = by_lead.get(e.get("lead_id"), [])
        i = bisect_right([t for t, _ in hist], at) if at else 0
        last = hist[i - 1][1] if i else None
        out.append({**e,
                    "owner_team": user_team(owner) if owner and owner_kind == PERSON else None,
                    "owner_kind": owner_kind if owner else None,
                    "last_call_id": last["call_id"] if last else None,
                    "last_caller_id": last["caller_id"] if last else None,
                    "last_caller": last["caller_name"] if last else None,
                    "last_caller_team": last["team"] if last else None})
    return out
