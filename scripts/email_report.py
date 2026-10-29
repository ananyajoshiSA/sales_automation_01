"""Email the latest set of reports (PDF, Excel, CSV, Word, PowerPoint, images, HTML) in one email, only when asked
(docs/email_report.md).

The /email-report skill runs this when someone types /email-report or asks "email this report". Nothing runs
it when a report is generated. The Google Apps Script mailer (email_report/Code.gs) holds the approved
recipients, the off/test/live switch, the daily cap and the sent log, so the same file is never emailed twice
and a request can never choose who receives it.

Files can be anywhere the chat saved them (its scratch folder, the repo, the project's reports folder), except
the raw lead data in the repo's data/ folder and hidden files such as .env. Without FILE it takes the latest
set: the newest report and every report finished shortly before it, since this machine's last email and
within 24 hours. Drafts are never picked. Every file of a set goes in one email.

With --to-list NAME the email goes to the mailer's list LIST_<NAME> (set in the Apps Script settings) instead
of its default RECIPIENTS; the request names the list, never addresses.

Needs REPORT_EMAIL_URL (the web app's /exec address) and REPORT_EMAIL_TOKEN in the root .env or the
cloud environment's settings.

    PYTHONPATH=. python scripts/email_report.py --list                      # reports it can see; sends nothing
    PYTHONPATH=. python scripts/email_report.py [FILE ...] --check         # what would be sent, to how many; sends nothing
    PYTHONPATH=. python scripts/email_report.py [FILE ...] [--resend]      # email them, together in one email
    PYTHONPATH=. python scripts/email_report.py [FILE ...] --to-list elite # to the mailer's LIST_ELITE
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import requests

import integrations  # noqa: F401  (loads .env)
from integrations.http import ApiError, request_json
from integrations.timeutil import IST

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT_REPORTS = "/mnt/project-files/reports"  # team_performance's default output folder
BLOCKED = os.path.join(REPO, "data")  # raw lead data: never emailed
LIST_NAME = re.compile(r"^[a-z0-9_]{1,40}$")  # the mailer's own rule for list names
LAST_SENT = os.path.join(BLOCKED, "email_report", "last_sent.json")  # this machine's last email (the file isn't a report)
SCRATCH = [os.path.join(t, "claude-*", "*", "*", "scratchpad") for t in dict.fromkeys((tempfile.gettempdir(), "/tmp"))]  # Claude's scratch folders
SKIP_DIRS = {"data", "node_modules", "__pycache__", "venv", "uploads"}  # plus hidden folders such as .git and .venv
ZIP, OLE = b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
TYPES = {".pdf": ("PDF", b"%PDF-"), ".xlsx": ("Excel", ZIP), ".xls": ("Excel", OLE), ".csv": ("CSV", None),
         ".docx": ("Word", ZIP), ".pptx": ("PowerPoint", ZIP), ".png": ("PNG image", b"\x89PNG"),
         ".jpg": ("JPEG image", b"\xff\xd8\xff"), ".jpeg": ("JPEG image", b"\xff\xd8\xff"), ".html": ("HTML", None)}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,150}\.(pdf|xlsx|xls|csv|docx|pptx|png|jpe?g|html)$", re.I)  # the mailer's own rule
NOT_FINISHED = re.compile(r"(?<![a-z])(draft|partial|tmp|temp)(?![a-z])", re.I)  # report_draft.pdf, not attempts.csv
MAX_BYTES = 20 * 1024 * 1024  # Gmail takes 25 MB of attachments per email; the mailer stays below it
MAX_FILES = 10
SETTLE_SECS = 10  # a file changed this recently may still be being written
SET_GAP_SECS = 30 * 60  # reports finished within 30 min of the next one belong to the same set
STALE_SECS = 24 * 3600
URL = re.compile(r"^https://script\.google\.com/macros/s/[\w-]+/exec$")
OK = {"sent", "duplicate"}
RETRY_SECS = 5  # wait before the one retry of a Google 404 or HTML error page


class Unclear(Exception):
    """The latest set can't be picked safely; the person must name the files."""


def search_roots() -> list[str]:
    """Where a chat's reports can be: the repo, the chat's scratch folders, the project's reports folder."""
    scratch = [p for g in SCRATCH for p in glob.glob(g)]
    found = [REPO, PROJECT_REPORTS, *scratch]
    return list(dict.fromkeys(os.path.realpath(p) for p in found if os.path.isdir(p)))


def tracked() -> set[str]:
    """Files committed to the repo, such as the coverage page's HTML: code, not reports."""
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {os.path.realpath(os.path.join(REPO, p)) for p in out.decode(errors="replace").split("\0") if p}


def problem(path: str, now: float | None = None) -> str | None:
    """Why this file can't be emailed as a finished report, or None if it can."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in TYPES:
        return "only PDF, Excel, CSV, Word, PowerPoint, PNG/JPEG image and HTML files can be emailed"
    if not NAME.match(name) or NOT_FINISHED.search(name):
        return "the name marks a draft or unfinished file, or uses characters the mailer refuses"
    real = os.path.realpath(path)
    if real == BLOCKED or real.startswith(BLOCKED + os.sep):
        return "files in the repo's data/ folder are raw lead data, never emailed"
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
    """Every finished report in the search folders, newest first."""
    skip = tracked()
    found: dict[str, str] = {}
    for root in search_roots():
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS]
            for f in files:
                p = os.path.join(d, f)
                if os.path.realpath(p) not in skip and not problem(p, now):
                    found.setdefault(os.path.realpath(p), p)
    return sorted(found.values(), key=os.path.getmtime, reverse=True)


def last_sent() -> float:
    try:
        return float(json.load(open(LAST_SENT))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def remember(paths: list[str], now: float) -> None:
    os.makedirs(os.path.dirname(LAST_SENT), exist_ok=True)
    json.dump({"at": now, "files": paths}, open(LAST_SENT, "w"), indent=1)


def latest_set(now: float | None = None) -> list[str]:
    """The newest report and every report finished within SET_GAP_SECS of the next, since the last email."""
    now = now or time.time()
    since = max(now - STALE_SECS, last_sent())
    reports = [p for p in finished(now) if os.path.getmtime(p) > since]
    if not reports:
        older = finished(now)
        if older:
            raise Unclear(f"no new report since {'the last email' if last_sent() > now - STALE_SECS else '24 hours ago'}; "
                          f"the newest is {older[0]}, finished {ago(now - os.path.getmtime(older[0]))} ago; name it to send it")
        raise Unclear("no finished report found in " + ", ".join(search_roots()))
    batch = reports[:1]
    for p in reports[1:]:
        if os.path.getmtime(batch[-1]) - os.path.getmtime(p) > SET_GAP_SECS:
            break
        batch.append(p)
    return batch


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


def call(url: str, body: dict, label: str, session: requests.Session | None = None, retry: bool = True) -> dict:
    """POST to the mailer. Google answers with a redirect that requests follows as a GET, so the token
    (in the body) only ever goes to script.google.com.

    Google now and then answers a working web app with a 404 or an HTML error page (2 in 35 calls on
    15 Oct 2026), so that gets one more try. Resending is safe: the mailer's sent log answers "duplicate"."""
    try:
        r = request_json(session or requests.Session(), "POST", url, label=label, json=body, timeout=180, max_retries=2)
    except ApiError as exc:
        if exc.status_code != 404 or not retry:
            raise
        r = None
    if isinstance(r, dict) and "status" in r:
        return r
    if retry:
        time.sleep(RETRY_SECS)
        return call(url, body, label, session, retry=False)
    raise ApiError("the mailer did not answer with JSON: check the web app is deployed with access 'Anyone'")


def check(url: str, token: str, to_list: str = "") -> dict:
    return call(url, {"token": token, "action": "check", **({"list": to_list} if to_list else {})}, "mailer check")


def upload(path: str) -> dict:
    data = open(path, "rb").read()
    return {"file_name": os.path.basename(path), "sha256": hashlib.sha256(data).hexdigest(),
            "content_b64": base64.b64encode(data).decode("ascii")}


def send(paths: list[str], url: str, token: str, resend: bool = False, to_list: str = "") -> dict:
    """One file goes in the original request shape, which every mailer version takes; several go as `files`."""
    files = [upload(p) for p in paths]
    body = {"token": token, "action": "send", "resend": resend, **(files[0] if len(files) == 1 else {"files": files}),
            **({"list": to_list} if to_list else {})}
    return call(url, body, f"send {len(files)} file(s)")


def distinct(paths: list[str]) -> list[str]:
    """Each file once: the same content named twice (a copy in another folder) is attached only once."""
    seen, out = set(), []
    for p in paths:
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(p)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*", help="the reports to send together (default: the latest set)")
    ap.add_argument("--list", action="store_true", help="list finished reports, newest first; send nothing")
    ap.add_argument("--check", action="store_true", help="say what would be sent and to how many; send nothing")
    ap.add_argument("--resend", action="store_true", help="email files that were already sent, again")
    ap.add_argument("--to-list", default="", metavar="NAME", help="send to the mailer's LIST_<NAME> instead of RECIPIENTS")
    a = ap.parse_args(argv)
    now = time.time()
    to_list = a.to_list.strip().lower()
    if to_list and not LIST_NAME.match(to_list):
        print(f"refused: the list name {a.to_list!r} must use only letters, digits and _")
        return 2
    if a.list:
        reports = finished(now)[:15]
        print("\n".join(describe(p, now) for p in reports) or "No finished reports in " + ", ".join(search_roots()) + ".")
        return 0
    try:
        paths = a.files or latest_set(now)
    except Unclear as exc:
        print(f"unclear: {exc}")
        return 1
    bad = [(p, why) for p in paths if (why := problem(p, now))]
    for p, why in bad:
        print(f"refused: {p}: {why}")
    if bad:
        return 2
    paths = distinct(paths)
    names = [os.path.basename(p) for p in paths]
    if dup := sorted({n for n in names if names.count(n) > 1}):
        print(f"refused: two different files are both named {', '.join(dup)}; name the one to send")
        return 2
    if len(paths) > MAX_FILES:
        print(f"unclear: {len(paths)} reports, more than {MAX_FILES} in one email; name the ones to send: " + ", ".join(paths))
        return 1
    for p in paths:
        print("file: " + describe(p, now))
    if sum(os.path.getsize(p) for p in paths) > MAX_BYTES:
        print("too_large: over 20 MB together, above what Gmail can attach. Nothing was sent. Instead, put them in "
              "Google Drive and share them only with the approved recipients, then email them the link.")
        return 2
    try:
        url, token = settings()
        c = check(url, token, to_list)
        if to_list and c.get("status") in ("ready", "off") and "lists" not in c:
            print("mailer: outdated - this Apps Script has no recipient lists, so it would email its default RECIPIENTS. "
                  "Paste the new email_report/Code.gs and deploy a new version (docs/email_report.md). Nothing was sent.")
            return 2
        if c.get("status") == "ready":
            print(f"mailer: {c['mode']} mode, {c['recipients']} approved recipient(s)" + (f" on list {to_list}" if to_list else "")
                  + (" (only the test address)" if c["mode"] == "test" else ""))
        elif c.get("status") == "off":
            print("mailer: off - DELIVERY_MODE is off in the Apps Script settings, so nothing can be emailed")
            return 2
        else:
            print(f"mailer: {c.get('status')} - {c.get('detail', '')}".rstrip(" -"))
            return 2
        if len(paths) > 1 and not c.get("multi"):
            print("mailer: outdated - this Apps Script takes one file per email. Paste the new email_report/Code.gs and "
                  "deploy a new version (docs/email_report.md), or send the files one at a time. Nothing was sent.")
            return 2
        if a.check:
            return 0
        r = send(paths, url, token, a.resend, to_list)
    except ApiError as exc:
        print(f"error: {exc}")
        return 2
    print(f"result: {r.get('status')} - {r.get('detail', '')}".rstrip(" -"))
    if r.get("status") in OK:
        remember(paths, now)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
