"""Shared helpers: snapshots, phone numbers, calls by lead, the previous working day."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta

from integrations.timeutil import IST, utc

TEAM = "Team Elite Calling"
LEADER = "Shivangi Sahu"
REAL_SECS = 120
CLOSED = ("Course Enrolled",)
DEAD = ("Not Interested", "Irrelevant", "Invalid", "Duplicate")


def p10(x) -> str | None:
    """Last 10 digits of a phone number, or None."""
    d = re.sub(r"\D", "", str(x or ""))
    return d[-10:] if len(d) >= 10 else None


def ist(s: str | None) -> datetime | None:
    t = utc(s)
    return t.astimezone(IST) if t else None


def name_of(u: dict) -> str:
    return f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip()


def load_snapshots(paths: list[str]) -> dict:
    """Merge fetch_team_data snapshots; the latest wins for leads and users."""
    snaps = sorted((json.load(open(p)) for p in paths), key=lambda s: s["fetched_at"])
    leads, calls, zips = {}, {}, {}
    for s in snaps:
        leads.update({l["ProspectID"]: l for l in s["leads"]})
        calls.update({c["activity_id"]: c for c in s["calls"]})
        zips.update({z.get("ProspectActivityId") or id(z): z for z in s.get("zip_activities", [])})
    return {"users": snaps[-1]["users"], "fetched_at": snaps[-1]["fetched_at"], "leads": list(leads.values()),
            "calls": list(calls.values()), "zip_activities": list(zips.values())}


class Snap:
    """A snapshot with the lookups every step needs."""

    def __init__(self, snap: dict, leader: str = LEADER):
        self.raw = snap
        self.users = {u["ID"]: name_of(u) for u in snap["users"]}
        self.callers = sorted(n for n in self.users.values() if n != leader)
        self.leads = {l["ProspectID"]: l for l in snap["leads"]}
        self.fetched = datetime.fromisoformat(snap["fetched_at"]).astimezone(IST)
        self.calls = [dict(c, t=ist(c["start_utc"])) for c in snap["calls"] if ist(c.get("start_utc"))]
        self.by_lead = defaultdict(list)
        for c in self.calls:
            self.by_lead[c["lead_id"]].append(c)
        for v in self.by_lead.values():
            v.sort(key=lambda c: c["t"])
        self.phone_lead = {}
        for lid, l in self.leads.items():
            if p10(l.get("Phone")):
                self.phone_lead.setdefault(p10(l["Phone"]), lid)

    def phone(self, c: dict) -> str | None:
        return p10(c.get("lead_number")) or p10((self.leads.get(c["lead_id"]) or {}).get("Phone"))

    def day(self, date: str) -> list[dict]:
        return [c for c in self.calls if c["t"].strftime("%Y-%m-%d") == date]

    def lead_name(self, lid: str) -> str:
        l = self.leads.get(lid) or {}
        return f"{l.get('FirstName') or ''} {l.get('LastName') or ''}".strip() or "Unnamed lead"


def previous_working_day(snap: Snap, today: str, min_dials: int = 50) -> str:
    """The latest day before ``today`` on which the team made at least ``min_dials`` dials (skips Sundays/holidays)."""
    d = datetime.strptime(today, "%Y-%m-%d")
    for k in range(1, 8):
        day = (d - timedelta(days=k)).strftime("%Y-%m-%d")
        if sum(1 for c in snap.day(day) if c["direction"] == "outbound" and c.get("user_id") in snap.users) >= min_dials:
            return day
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")
