"""Shareable copies of a report for email and Google Drive: lead phone numbers removed, caller lists left out.

    python -m analytics.daily_plan share exports/daily/2026-10-12/Elite_status_1349.html [--doc]

Writes <name>.share.html next to the report, and <name>.doc.html (bare HTML for a Google Doc) with --doc. The full report, with phone numbers, stays in exports/ and the chat."""

from __future__ import annotations

import re

PHONE = re.compile(r"(?:\+?91[-\s]?)?\d{10,}")  # any run of 10+ digits: lead numbers, malformed ones, our line numbers
CUT = ("<h2>9. Team overview",)  # the morning report's per-caller lists hold every lead: not for sharing
NOTE = ("<p style='color:#555;font-size:11px'>Shared copy: lead phone numbers are removed (dial from LeadSquared) and the "
        "per-caller lists are left out. The full report is in the Claude chat.</p>")


def redact(html: str) -> str:
    return PHONE.sub("—", html)


def shareable(html: str) -> str:
    for marker in CUT:
        i = html.find(marker)
        if i >= 0:
            html = html[:i] + "</body></html>"
    j = html.find("<body>")
    html = html[:j + 6] + NOTE + html[j + 6:] if j >= 0 else NOTE + html
    return redact(html)


def lean(html: str) -> str:
    """Bare HTML for a Google Doc upload: no stylesheet, classes or inline styles (Docs re-styles it anyway)."""
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    html = re.sub(r"\s(?:class|style)=(?:'[^']*'|\"[^\"]*\"|[^\s>]+)", "", html)
    return re.sub(r">\s+<", "><", html)


def write(path: str, doc: bool = False) -> str:
    out = (path[:-5] if path.endswith(".html") else path) + (".doc.html" if doc else ".share.html")
    html = shareable(open(path).read())
    open(out, "w").write(lean(html) if doc else html)
    return out
