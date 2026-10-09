"""Tie each Zipteams analysis ("Zipteams Notes", activity 237) to the LeadSquared call it analysed.

The rule (CLAUDE.md, P40): a note belongs to the lead's last answered call at or before the note.
On 88 notes from 5 Oct 2026 every note was written as its call ended (0 to 36 s before start + duration),
and that call was always the lead's last answered call, so the rule names the exact call.

A call that runs past midnight IST gets its note on the next day. Notes are therefore fetched for
``ZIP_LATE_WINDOW`` after the day, and such a note is kept only when it lands within ``ZIP_END_MINUTES``
of the end of one of the day's calls, so a next-day call's note is never pinned on the day.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from analytics.call_markers import summary_has_payment_step
from analytics.lead_priority import strip_html
from analytics.team_report import zip_score
from integrations.timeutil import utc

ZIP_LATE_WINDOW = timedelta(hours=2)
ZIP_END_MINUTES = 5


def zip_analysis(a: dict) -> dict:
    """The parts of a note the reports use: intent, the three pass/fail scores and whether a payment step was set up."""
    return {"intent": (a.get("mx_Custom_1") or "NOT_AVAILABLE").upper(), "probe": zip_score(a.get("mx_Custom_5")),
            "pitch": zip_score(a.get("mx_Custom_4")), "obj": zip_score(a.get("mx_Custom_6")),
            "payment_step": summary_has_payment_step(strip_html(a.get("ActivityEvent_Note")) + " " + (a.get("mx_Custom_2") or ""))}


def match_notes(zips: list[dict], calls: list[dict], day_end: datetime | None = None) -> tuple[list[tuple[dict, dict]], int, int]:
    """(note, call) pairs, notes with no answered call before them, and notes written after ``day_end`` that
    belong to no call of the day. ``calls`` need ``lead_id``, ``t`` (start), ``ans`` and ``duration``."""
    by_lead = defaultdict(list)
    for c in calls:
        if c["ans"] and c["t"]:
            by_lead[c["lead_id"]].append(c)
    for v in by_lead.values():
        v.sort(key=lambda c: c["t"])
    pairs, dropped, other_day = [], 0, 0
    for a in sorted(zips, key=lambda a: a.get("CreatedOn") or ""):
        t = utc(a.get("CreatedOn"))
        prior = [c for c in by_lead.get(a.get("RelatedProspectId"), []) if t and c["t"] <= t]
        if day_end and t and t >= day_end:
            end = prior[-1]["t"] + timedelta(seconds=prior[-1].get("duration") or 0) if prior else None
            if not end or abs((t - end).total_seconds()) > ZIP_END_MINUTES * 60:
                other_day += 1
                continue
        elif not prior:
            dropped += 1
            continue
        pairs.append((a, prior[-1]))
    return pairs, dropped, other_day


def attach(zips: list[dict], calls: list[dict], day_end: datetime | None = None) -> tuple[int, int]:
    """Put each call's Zipteams analysis on the call as ``zip`` (the latest note if there are two).
    Returns (notes with no call, notes that belong to another day)."""
    pairs, dropped, other_day = match_notes(zips, calls, day_end)
    for a, c in pairs:
        c["zip"] = {**zip_analysis(a), "note_id": a.get("ProspectActivityId")}
    return dropped, other_day
