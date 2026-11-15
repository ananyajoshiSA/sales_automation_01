"""Second reading of every compliance decision, as in the discovery audit, on the follow-up calls."""

from __future__ import annotations

from pathlib import Path

from analytics.discovery_audit_module import review as dreview
from analytics.followup_audit_module import reading

NOTE = """
Follow-up audit: each transcript starts with a BACKGROUND section (the discovery call) that is never scored. Judge
compliance only on the SCORED CALLS section: a claim heard only in the background is not flagged here.
"""


def prepare(entries: list[dict], folder: str) -> list[str]:
    paths = dreview.prepare(entries, folder, text=reading.lead_text)
    Path(f"{folder}/review/REVIEW.md").write_text(dreview.REVIEW + NOTE)
    return paths


def apply(entries: list[dict], folder: str) -> dict:
    return dreview.apply(entries, folder, text=reading.lead_text, recheck=reading.check)
