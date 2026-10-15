"""First-ever "Course Enrolled" times, read from LeadSquared once per lead and then kept.

A lead's first enrolment does not change once it has happened, yet the daily report fetch, conversation
intelligence and the D1 backfill each re-read the stage history of every lead in Course Enrolled edited since
the start of their window, on every run. ``scan_since`` does that scan once for all three: it lists the leads
now in Course Enrolled, newest edit first, back to ``d0``, and reads a lead's stage history (one request each)
only when the store doesn't already know its first enrolment.

Only a complete history (fewer rows than one page) that shows an enrolment is stored. A lead with no
enrolment in its history, or with a page full of stage changes, is read again next time, as before.

The store is data/leadsquared/first_enrolments.sqlite3 (git-ignored: it holds lead ids), one row per account
and lead. ``LEADSQUARED_ENROLMENT_STORE`` moves it; ``off`` turns it off, so every history is read as before.
Deleting the file is safe: it is rebuilt on the next scans.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator

from analytics.definitions import ENROLLED
from integrations.env import REPO_ROOT
from integrations.leadsquared.client import format_datetime
from integrations.timeutil import now_utc, utc

STAGE_CHANGE = 3002
HISTORY_ROWS = 100
DEFAULT_STORE = os.path.join(REPO_ROOT, "data", "leadsquared", "first_enrolments.sqlite3")
DDL = """
CREATE TABLE IF NOT EXISTS first_enrolment (
    account TEXT NOT NULL, lead_id TEXT NOT NULL, at_utc TEXT NOT NULL, set_by TEXT, recorded_utc TEXT NOT NULL,
    PRIMARY KEY (account, lead_id))
"""


def first_enrolment(history: list[dict]) -> tuple[datetime, str | None] | None:
    """(time, who set it) of the earliest stage change to Course Enrolled in stage-change activities (event 3002)."""
    firsts = sorted(((t, {d.get("Key"): d.get("Value") for d in h.get("Data") or []})
                     for h in history if (t := utc(h.get("CreatedOn")))), key=lambda x: x[0])
    first = next(((t, d) for t, d in firsts if d.get("CurrentStage") == ENROLLED), None)
    return (first[0], (first[1].get("CreatedBy") or "").strip() or None) if first else None


class EnrolmentStore:
    """Known first enrolments for one LeadSquared account."""

    def __init__(self, path: str, account: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path, self.account = path, account
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA busy_timeout = 30000")
        self.db.execute(DDL)
        self.db.commit()

    def get(self, lead_id: str) -> tuple[datetime, str | None] | None:
        row = self.db.execute("SELECT at_utc, set_by FROM first_enrolment WHERE account = ? AND lead_id = ?",
                              (self.account, lead_id)).fetchone()
        return (utc(row[0]), row[1]) if row else None

    def put(self, lead_id: str, at: datetime, set_by: str | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO first_enrolment VALUES (?, ?, ?, ?, ?)",
                        (self.account, lead_id, format_datetime(at), set_by, format_datetime(now_utc())))
        self.db.commit()

    def close(self) -> None:
        self.db.close()


def open_store(client) -> EnrolmentStore | None:
    """The store for the client's account, or None when ``LEADSQUARED_ENROLMENT_STORE`` is ``off``."""
    path = os.environ.get("LEADSQUARED_ENROLMENT_STORE", "").strip() or DEFAULT_STORE
    if path.lower() == "off":
        return None
    account = getattr(getattr(client, "admission", None), "account", None) or "default"
    return EnrolmentStore(path, account)


@dataclass
class ScanStats:
    scanned: int = 0      # leads now in Course Enrolled, edited since d0
    known: int = 0        # first enrolment taken from the store (no request)
    read: int = 0         # stage histories read from LeadSquared

    def __str__(self) -> str:
        return f"scanned {self.scanned} enrolled leads ({self.known} from the store, {self.read} histories read)"


def scan_since(client, d0: datetime, columns: Iterable[str], stats: ScanStats | None = None,
               store: EnrolmentStore | None = None) -> Iterator[tuple[dict, datetime | None, str | None]]:
    """(lead row, first enrolment time, set by) for each lead now in Course Enrolled and last edited at or after
    ``d0``; the time is None when the history shows no enrolment. A lead first enrolled after ``d0`` was edited
    then too, so the scan stops at the first lead last edited before ``d0``. Callers keep the window they need."""
    stats = stats if stats is not None else ScanStats()
    own = store is None
    store = open_store(client) if own else store
    try:
        for lead in client.iter_leads("ProspectStage", ENROLLED, columns=list(columns), sort_by="ModifiedOn",
                                      page_size=1000):
            mod = utc(lead.get("ModifiedOn"))
            if mod and mod < d0:
                break
            stats.scanned += 1
            lid = lead["ProspectID"]
            first = store.get(lid) if store else None
            if first:
                stats.known += 1
            else:
                stats.read += 1
                hist = (client.get_lead_activities(lid, activity_event=STAGE_CHANGE, row_count=HISTORY_ROWS) or {}).get(
                    "ProspectActivities") or []
                first = first_enrolment(hist)
                if first and store and len(hist) < HISTORY_ROWS:
                    store.put(lid, *first)
            yield (lead, *first) if first else (lead, None, None)
    finally:
        if own and store:
            store.close()
