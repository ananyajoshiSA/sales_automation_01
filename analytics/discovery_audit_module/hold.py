"""Leads held "under review" on an audit day, a late run's label, and the attention note for the day's email.

A daily run that meets something only the owner can decide (for example many leads moved in one batch by one
person) never holds the whole email and never waits on a question: it marks those leads under review, builds and
emails on time, and asks the owner in a plain message. The build shades those rows, opens the summary PDF with a
note, and writes attention.txt in the day's folder; the email step passes that file to
scripts/email_report.py --attention-file, so the email itself warns its readers. A run that audits an older day
(after a missed morning) is labelled late the same way. Both audit modules use this file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from integrations.timeutil import IST, now_ist, utc

FILE = "under_review.json"
NOTE = "attention.txt"
MAX_REASON = 200
DONT_ACT = "Don't act yet: wait for the decision"


def path(day_folder: str) -> str:
    return f"{day_folder}/{FILE}"


def load(day_folder: str) -> dict[str, str]:
    """lead_key -> the reason it is under review."""
    p = path(day_folder)
    return json.load(open(p)) if os.path.exists(p) else {}


def save(day_folder: str, held: dict[str, str]) -> None:
    if held:
        json.dump(held, open(path(day_folder), "w"), indent=1)
    elif os.path.exists(path(day_folder)):
        os.remove(path(day_folder))


def add(day_folder: str, reason: str, keys: list[str], known: set[str]) -> dict[str, str]:
    reason = " ".join(reason.split())
    if not reason:
        raise ValueError("say why these leads are under review (--reason)")
    if len(reason) > MAX_REASON:
        raise ValueError(f"the reason is {len(reason)} characters; keep it under {MAX_REASON}")
    if not keys:
        raise ValueError("no lead matched: name lead keys or a --moved-between window that holds some")
    if unknown := sorted(set(keys) - known):
        raise ValueError(f"not leads of this day: {', '.join(unknown[:5])}{' ...' if len(unknown) > 5 else ''}")
    held = load(day_folder)
    held.update({k: reason for k in keys})
    save(day_folder, held)
    return held


def clear(day_folder: str, keys: list[str] | None = None) -> dict[str, str]:
    held = load(day_folder)
    held = {k: v for k, v in held.items() if keys and k not in keys}
    save(day_folder, held)
    return held


def moved_between(entries: list[dict], window: str, moved_field: str) -> list[dict]:
    """Entries whose stage change fell in an IST window "HH:MM-HH:MM" (both ends included, to the minute)."""
    try:
        a, b = (datetime.strptime(x.strip(), "%H:%M").time() for x in window.split("-"))
    except ValueError:
        raise ValueError(f"--moved-between must look like 13:04-13:41, not {window!r}") from None
    out = []
    for e in entries:
        t = utc(e[moved_field]).astimezone(IST).time().replace(second=0, microsecond=0)
        if a <= t <= b:
            out.append(e)
    return out


def groups(held: dict[str, str], keys: list[str]) -> list[tuple[str, int]]:
    """(reason, how many of these leads) for the leads of this report that are held, in first-seen order."""
    count: dict[str, int] = {}
    for k in keys:
        if k in held:
            count[held[k]] = count.get(held[k], 0) + 1
    return list(count.items())


def late_note(day: str, today: str | None = None) -> str:
    """A label for a run that audits an older day than yesterday, else ''."""
    today = today or now_ist().strftime("%Y-%m-%d")
    gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(day, "%Y-%m-%d")).days
    if gap <= 1:
        return ""
    dm = lambda d: datetime.strptime(d, "%Y-%m-%d").strftime("%-d %b")
    return f"LATE: this is the audit of {dm(day)}, sent on {dm(today)} because an earlier daily run did not finish."


def attention(late: str, grouped: list[tuple[str, int]], rows_word: str = "lead") -> str:
    """The email's attention note (plain text, under the mailer's 600 characters), or '' when the day needs none."""
    lines = [late] if late else []
    for reason, n in grouped:
        lines.append(f"UNDER REVIEW: {n} {rows_word}{'s' if n != 1 else ''} ({reason}). Don't act on these rows yet; "
                     "a corrected copy will follow if the decision changes them. Everything else is final.")
    text = "\n".join(lines)
    return text if len(text) <= 600 else text[:597].rstrip() + "..."


def write_note(day_folder: str, text: str) -> str:
    """Always rewritten, so an old day's note is never sent with a new day's email."""
    p = f"{day_folder}/{NOTE}"
    open(p, "w", encoding="utf-8").write(text + ("\n" if text else ""))
    return p
