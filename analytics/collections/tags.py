"""The LeadSquared "Bootcamp collections" field: which values are collections, which kind, and their bootcamp date.

Bootcamp collections carry the bootcamp and its date ("US Accounting Bootcamp Collection - 24th October'26",
"Global Patent Analyst Bootcamp Collection - 13Th December'25", "AI for Business Growth | Women (Re) - Oct 17, Oct 18,
Oct 19, 2026)"). Community collections are "Community Webinar Collections" (and variants such as "...-31stmay"),
with the type of community in the lead's Funnel name.
"""

from __future__ import annotations

import re
from datetime import datetime

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_DAY_MONTH_YY = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z]{3})[a-z]*\s*[’']\s*(\d\d)\b", re.I)
_MONTH_DAY_YYYY = re.compile(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})\b.*?\b(20\d\d)\b", re.I)
_DAY_MONTH_YYYY = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*,?\s+(20\d\d)\b", re.I)


def tag_kind(tag: str | None) -> str:
    """"community", "bootcamp", or "" for a value that is not a collection (such as "Funnel Direct Payments")."""
    t = (tag or "").lower()
    if "community" in t:
        return "community"
    return "bootcamp" if "bootcamp" in t or "collection" in t or tag_date(tag) else ""


def is_collection_tag(tag: str | None) -> bool:
    return bool(tag_kind(tag))


def _date(year: int, month: str, day: int) -> str:
    mon = MONTHS.get(month[:3].lower())
    if not mon:
        return ""
    try:
        return datetime(year, mon, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def tag_date(text: str | None) -> str:
    """First date in a tag or a sheet's start-date text, as YYYY-MM-DD; "" if none.

    Reads "24th October'26", "13Th December'25", "28th March'26", "Oct 17, Oct 18, 2026" and "17 October 2026".
    """
    s = str(text or "")
    if m := _DAY_MONTH_YY.search(s):
        return _date(2000 + int(m.group(3)), m.group(2), int(m.group(1)))
    if m := _DAY_MONTH_YYYY.search(s):
        return _date(int(m.group(3)), m.group(2), int(m.group(1)))
    if m := _MONTH_DAY_YYYY.search(s):
        return _date(int(m.group(3)), m.group(1), int(m.group(2)))
    return ""


def tag_course(tag: str | None) -> str:
    """The bootcamp's course from a tag: "US Accounting" from "US Accounting Bootcamp Collection - 24th October'26"."""
    t = (tag or "").strip()
    return re.split(r"\s+bootcamp\b|\s+collections?\b|\s+-\s+|\s*\(", t, maxsplit=1, flags=re.I)[0].strip(" -|") or t
