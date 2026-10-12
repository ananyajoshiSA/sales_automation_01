"""Email one completed report (PDF, Excel or CSV) to the approved recipients, only when asked (docs/email_report.md).

The /email-report skill runs this when someone types /email-report or asks "email this report".
Nothing runs it when a report is generated. The Google Apps Script mailer (email_report/Code.gs) holds the
approved recipients, the off/test/live switch, the daily cap and the sent log, so the same file is never
emailed twice and a request can never choose who receives it.

Only finished reports in the report folders (/mnt/project-files/reports, exports/) are sent. Without a
FILE it takes the newest one, and stops to ask instead when that choice is unclear: several files
finished in the same run, or nothing finished in the last 24 hours. Drafts are never picked.

Needs REPORT_EMAIL_URL (the web app's /exec address) and REPORT_EMAIL_TOKEN in the root .env or the
cloud environment's settings.

    PYTHONPATH=. python scripts/email_report.py --list                      # finished reports; sends nothing
    PYTHONPATH=. python scripts/email_report.py [FILE] --check             # what would be sent, to how many; sends nothing
    PYTHONPATH=. python scripts/email_report.py [FILE] [--resend]          # email it
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
import time
from datetime import datetime

import requests

import integrations  # noqa: F401  (loads .env)
from integrations.http import ApiError, request_json
from integrations.timeutil import IST

ROOTS = ("/mnt/project-files/reports", "exports")  # where finished reports are written (team_performance --out)
TYPES = {".pdf": ("PDF", b"%PDF-"), ".xlsx": ("Excel", b"PK\x03\x04"), ".csv": ("CSV", None)}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,150}\.(pdf|xlsx|csv)$", re.I)  # the mailer's own rule
NOT_FINISHED = re.compile(r"(?<![a-z])(draft|partial|tmp|temp)(?![a-z])", re.I)  # report_draft.pdf, not attempts.csv
MAX_BYTES = 20 * 1024 * 1024  # Gmail takes 25 MB of attachments per email; the mailer stays below it
SETTLE_SECS = 10  # a file changed this recently may still be being written
SAME_RUN_SECS = 300  # files finished this close together came from one run, so "the latest" is unclear
STALE_SECS = 24 * 3600
URL = re.compile(r"^https://script\.google\.com/macros/s/[\w-]+/exec$")
OK = {"sent", "duplicate"}


class Unclear(Exception):
    """The latest report can't be picked safely; the person must name the file."""


def roots() -> list[str]:
    return [os.path.realpath(r) for r in ROOTS if os.path.isdir(r)]


def problem(path: str, now: float | None = None) -> str | None:
    """Why this file can't be emailed as a finished report, or None if it can."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in TYPES:
        return "only PDF, Excel (.xlsx) and CSV files can be emailed"
    if not NAME.match(name) or NOT_FINISHED.search(name):
        return "the name marks a draft or unfinished file, or uses characters the mailer refuses"
    real = os.path.realpath(path)
    if not any(real.startswith(r + os.sep) for r in roots()):
        return f"only finished reports in {' or '.join(ROOTS)} are emailed"
    if not os.path.isfile(real):
        return "file not found"
    size = os.path.getsize(real)
    if size == 0:
        return "the file is empty"
    if (now or time.time()) - os.path.getmtime(real) < SETTLE_SECS:
        return "the file changed seconds ago and may still be being written; try again in a moment"
    head = open(real, "rb").read(4096)
    magic = TYPES[ext][1]
    if magic and not head.startswith(magic) or not magic and b"\0" in head:
        return f"the content is not a real {TYPES[ext][0]} file"
    return None


def finished(now: float | None = None) -> list[str]:
    """Every finished report, newest first."""
    found = [os.path.join(d, f) for r in roots() for d, _, files in os.walk(r) for f in files]
    return sorted((p for p in found if not problem(p, now)), key=os.path.getmtime, reverse=True)


def latest(now: float | None = None) -> str:
    now = now or time.time()
    reports = finished(now)
    if not reports:
        raise Unclear(f"no finished PDF, Excel or CSV report in {' or '.join(ROOTS)}")
    newest = os.path.getmtime(reports[0])
    if now - newest > STALE_SECS:
        raise Unclear(f"the newest report, {reports[0]}, finished {ago(now - newest)} ago; name it to send it anyway")
    run = [p for p in reports if newest - os.path.getmtime(p) <= SAME_RUN_SECS]
    if len(run) > 1:
        raise Unclear("these finished together, so which one is 'this report' is unclear: " + ", ".join(run))
    return reports[0]


def ago(secs: float) -> str:
    m = int(secs // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h" if m < 48 * 60 else f"{m // 1440} days"


def describe(path: str, now: float | None = None) -> str:
    t = os.path.getmtime(path)
    when = datetime.fromtimestamp(t, IST).strftime("%-d %b %Y, %-I:%M %p").replace("AM", "am").replace("PM", "pm")
    return (f"{path} ({TYPES[os.path.splitext(path)[1].lower()][0]}, {os.path.getsize(path) / 1e6:.1f} MB, "
            f"finished {when} IST, {ago((now or time.time()) - t)} ago)")


def settings() -> tuple[str, str]:
    url = os.environ.get("REPORT_EMAIL_URL", "").strip()
    token = os.environ.get("REPORT_EMAIL_TOKEN", "").strip()
    if not URL.match(url):
        raise ApiError("REPORT_EMAIL_URL must be the mailer's web app address, https://script.google.com/macros/s/.../exec")
    if len(token) < 32:
        raise ApiError("REPORT_EMAIL_TOKEN is missing or too short; copy UPLOAD_TOKEN from the Apps Script log")
    return url, token


def call(url: str, body: dict, label: str, session: requests.Session | None = None) -> dict:
    """POST to the mailer. Google answers with a redirect that requests follows as a GET, so the token
    (in the body) only ever goes to script.google.com."""
    r = request_json(session or requests.Session(), "POST", url, label=label, json=body, timeout=180, max_retries=2)
    if not isinstance(r, dict) or "status" not in r:
        raise ApiError("the mailer did not answer with JSON: check the web app is deployed with access 'Anyone'")
    return r


def check(url: str, token: str) -> dict:
    return call(url, {"token": token, "action": "check"}, "mailer check")


def send(path: str, url: str, token: str, resend: bool = False) -> dict:
    data = open(path, "rb").read()
    body = {"token": token, "action": "send", "file_name": os.path.basename(path), "resend": resend,
            "sha256": hashlib.sha256(data).hexdigest(), "content_b64": base64.b64encode(data).decode("ascii")}
    return call(url, body, f"send {os.path.basename(path)}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", nargs="?", help="the report to send (default: the latest finished report)")
    ap.add_argument("--list", action="store_true", help="list finished reports, newest first; send nothing")
    ap.add_argument("--check", action="store_true", help="say what would be sent and to how many; send nothing")
    ap.add_argument("--resend", action="store_true", help="email a file that was already sent, again")
    a = ap.parse_args(argv)
    now = time.time()
    if a.list:
        reports = finished(now)[:15]
        print("\n".join(describe(p, now) for p in reports) or f"No finished reports in {' or '.join(ROOTS)}.")
        return 0
    try:
        path = a.file or latest(now)
    except Unclear as exc:
        print(f"unclear: {exc}")
        return 1
    if why := problem(path, now):
        print(f"refused: {path}: {why}")
        return 2
    print("file: " + describe(path, now))
    if os.path.getsize(path) > MAX_BYTES:
        print("too_large: over 20 MB, above what Gmail can attach. Nothing was sent. Instead, put it in Google Drive "
              "and share it only with the approved recipients, then email them the link.")
        return 2
    try:
        url, token = settings()
        c = check(url, token)
        if c.get("status") == "ready":
            print(f"mailer: {c['mode']} mode, {c['recipients']} approved recipient(s)"
                  + (" (only the test address)" if c["mode"] == "test" else ""))
        elif c.get("status") == "off":
            print("mailer: off - DELIVERY_MODE is off in the Apps Script settings, so nothing can be emailed")
            return 2
        else:
            print(f"mailer: {c.get('status')} - {c.get('detail', '')}".rstrip(" -"))
            return 2
        if a.check:
            return 0
        r = send(path, url, token, a.resend)
    except ApiError as exc:
        print(f"error: {exc}")
        return 2
    print(f"result: {r.get('status')} - {r.get('detail', '')}".rstrip(" -"))
    return 0 if r.get("status") in OK else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
