"""Email the reports, with the PDF and workbook attached. Sends only when a mail route is configured; otherwise says so.

Route 0, the project's report mailer (email_report/Code.gs, the one /email-report uses): REPORT_EMAIL_URL and
REPORT_EMAIL_TOKEN. The Apps Script holds the approved recipients, the off/test/live switch, the daily cap and the
sent log, so ``to`` is ignored and a file already emailed comes back "duplicate". The report's files go in one email
(one per file on a mailer older than the multi-file Code.gs), and the email body is the mailer's own.
Route 1, the Gmail relay (docs/mail_relay.gs, an Apps Script web app in the sender's Google account; works where SMTP
ports are blocked, as in the cloud sessions): MAIL_RELAY_URL and MAIL_RELAY_KEY. The relay holds the recipient list,
so REPORT_TO may be left empty; if set, the relay only sends to addresses on its own list.
Route 2, SMTP: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (defaults to SMTP_USER).

    REPORT_TO           full reports with lead phone numbers (team leader, callers), comma-separated
    REPORT_TO_SUMMARY   summary only, no attachments and no lead details (leadership), comma-separated
"""

from __future__ import annotations

import base64
import mimetypes
import os
import smtplib
import sys
import time
from email.message import EmailMessage

import requests


def recipients(var: str) -> list[str]:
    return [x.strip() for x in (os.environ.get(var) or "").split(",") if x.strip()]


def configured() -> bool:
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def build_message(subject: str, html: str, text: str, to: list[str], attachments=(), sender: str | None = None) -> EmailMessage:
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, sender or os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", ""), ", ".join(to)
    m.set_content(text)
    m.add_alternative(html, subtype="html")
    for path in attachments:
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        m.add_attachment(open(path, "rb").read(), maintype=main, subtype=sub, filename=os.path.basename(path))
    return m


def relay_configured() -> bool:
    return bool(os.environ.get("MAIL_RELAY_URL") and os.environ.get("MAIL_RELAY_KEY"))


def send_relay(subject: str, html: str, text: str, to: list[str], attachments=(), post=requests.post) -> bool:
    """Hand the message and its files to the Gmail relay, which sends it from the owner's Gmail."""
    files = [{"filename": os.path.basename(p), "mimeType": mimetypes.guess_type(p)[0] or "application/octet-stream",
              "content": base64.b64encode(open(p, "rb").read()).decode()} for p in attachments]
    r = post(os.environ["MAIL_RELAY_URL"], json={"key": os.environ["MAIL_RELAY_KEY"], "to": to, "subject": subject,
                                                  "html": html, "text": text, "attachments": files}, timeout=120)
    ok = r.status_code == 200 and r.text.strip().startswith("ok")
    print(f"mail: '{subject}' {'sent via the Gmail relay' if ok else 'NOT sent, relay said: ' + r.text.strip()[:120]}", file=sys.stderr)
    return ok


def report_mailer_configured() -> bool:
    return bool(os.environ.get("REPORT_EMAIL_URL") and os.environ.get("REPORT_EMAIL_TOKEN"))


def send_report_mailer(subject: str, attachments=(), sleep=time.sleep) -> bool:
    """Hand the report's files to the project's report mailer, the way /email-report does, and mark them as this
    machine's last email so a later /email-report doesn't pick them again."""
    from scripts import email_report as er
    from integrations.http import ApiError

    paths = [os.path.realpath(p) for p in attachments]
    if not paths:
        print(f"mail: '{subject}' NOT sent, the report mailer sends files and this message has none", file=sys.stderr)
        return False
    wait = max((er.SETTLE_SECS + 1 - (time.time() - os.path.getmtime(p)) for p in paths if os.path.isfile(p)), default=0)
    if wait > 0:  # the files were just written, and the mailer refuses a file changed seconds ago
        sleep(wait)
    now = time.time()
    if bad := [f"{os.path.basename(p)}: {why}" for p in paths if (why := er.problem(p, now))]:
        print(f"mail: '{subject}' NOT sent, " + "; ".join(bad), file=sys.stderr)
        return False
    try:
        url, token = er.settings()
        c = er.check(url, token)
        if c.get("status") != "ready":
            print(f"mail: '{subject}' NOT sent, mailer {c.get('status')}: {c.get('detail', '')}".rstrip(": "), file=sys.stderr)
            return False
        groups = [paths] if c.get("multi") or len(paths) == 1 else [[p] for p in paths]
        results = [er.send(g, url, token).get("status") for g in groups]
    except ApiError as exc:
        print(f"mail: '{subject}' NOT sent, error: {exc}", file=sys.stderr)
        return False
    ok = all(r in er.OK for r in results)
    if ok:
        er.remember(paths, now)
    print(f"mail: '{subject}' {'sent via the report mailer' if ok else 'NOT all sent'} ({c['mode']} mode, "
          f"{c['recipients']} recipient(s), {len(groups)} email(s): {', '.join(map(str, results))})", file=sys.stderr)
    return ok


def send(subject: str, html: str, text: str, to: list[str], attachments=(), smtp_factory=smtplib.SMTP, post=requests.post) -> bool:
    if report_mailer_configured():  # the mailer keeps its own recipient list, switch and sent log
        return send_report_mailer(subject, attachments)
    if relay_configured():  # the relay keeps its own recipient list, so an empty ``to`` is fine
        return send_relay(subject, html, text, to, attachments, post)
    if not to:
        print(f"mail: no recipients for '{subject}', not sent", file=sys.stderr)
        return False
    if not configured():
        print(f"mail: no mail route set (MAIL_RELAY_URL/KEY or SMTP settings), '{subject}' not sent", file=sys.stderr)
        return False
    msg = build_message(subject, html, text, to, attachments)
    with smtp_factory(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587), timeout=60) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    print(f"mail: '{subject}' sent to {len(to)} recipient(s)", file=sys.stderr)
    return True
