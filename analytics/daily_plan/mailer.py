"""Email the reports. Sends only when SMTP settings are in the environment; otherwise says so and does nothing.

    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (defaults to SMTP_USER)
    REPORT_TO           full reports with lead phone numbers (team leader, callers), comma-separated
    REPORT_TO_SUMMARY   summary only, no attachments and no lead details (leadership), comma-separated
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
import sys
from email.message import EmailMessage


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


def send(subject: str, html: str, text: str, to: list[str], attachments=(), smtp_factory=smtplib.SMTP) -> bool:
    if not to:
        print(f"mail: no recipients for '{subject}', not sent", file=sys.stderr)
        return False
    if not configured():
        print(f"mail: SMTP settings missing, '{subject}' not sent", file=sys.stderr)
        return False
    msg = build_message(subject, html, text, to, attachments)
    with smtp_factory(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587), timeout=60) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    print(f"mail: '{subject}' sent to {len(to)} recipient(s)", file=sys.stderr)
    return True
