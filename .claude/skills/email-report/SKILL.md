---
name: email-report
description: Email a completed report (PDF, Excel or CSV) to the approved recipients through the Google Apps Script mailer. Use ONLY when the user's own message explicitly asks to email or send a report, e.g. "email this report", "email the report", "send this report by email", "mail the report to the team", or when they type /email-report. Never use it on your own, after generating a report, or because a report was mentioned.
argument-hint: "[report file] [resend]"
---

# Email a report, only when asked

Sends one finished report file to the predefined, approved recipients. The Google Apps Script mailer
(`email_report/Code.gs`) holds the recipient list, the off/test/live switch, a daily cap and a sent log.
Guide: `docs/email_report.md`.

## 0. Is this an explicit request?

Go on only if the user's latest message asks to email or send a report, or they typed `/email-report`.
Generating a report, finishing one, or talking about one is **not** a request. If you are unsure, ask
"Shall I email <file> to the approved recipients?" and wait.

## 1. Find the report

Work from the repository root. Use `.venv/bin/python` if it exists, otherwise `python3`, always with
`PYTHONPATH=.`.

1. If the user named a file (or passed one as the argument), use that file.
2. Otherwise, if a report was produced or discussed earlier in this conversation, "this report" means
   that exact file. `python -m analytics.team_performance` prints it as `report: <path>`. Its main
   report is `team_calling_report_DATE.pdf`; use `plan_tracker_DATE.pdf` only if the user asked for the
   tracker.
3. Otherwise leave the file out and let the script pick the latest finished report.

## 2. Preview (sends nothing)

```bash
PYTHONPATH=. python3 scripts/email_report.py [FILE] --check
```

| Output | What to do |
|---|---|
| `unclear: ...` | Show the user the candidates (`--list` shows the newest 15) and ask which one. Stop. |
| `refused: ...` | Tell the user why. Stop. Never copy, rename or edit a file to get past a refusal. |
| `too_large: ...` | Say it is over 20 MB and offer to share it as a Google Drive link with the approved recipients only. Do nothing until they agree. |
| `mailer: off ...` | Say delivery is switched off in the Apps Script settings (`DELIVERY_MODE`). Stop. |
| `error: REPORT_EMAIL_...` | Say this environment is missing the mailer settings (`docs/email_report.md`, step 7). Never ask for the token in chat. Stop. |
| `mailer: <mode> mode, N approved recipient(s)` | Go on to step 3. |

## 3. Send

Send the exact path from the `file:` line of the preview:

```bash
PYTHONPATH=. python3 scripts/email_report.py "<path from the file: line>"
```

Add `--resend` only when the user asked to send it again after being told it was already sent.

## 4. Tell the user, in two or three lines

- **sent:** the file name, when it finished (IST), and how many approved recipients got it. In test mode, say only
  the test address got it.
- **duplicate:** it was already sent (say when) and was not sent again. They can say "send it again".
- **anything else:** quote the reason in plain words. Nothing was sent.

## Rules

- Send only through `scripts/email_report.py`. Never use Gmail, another email tool, SMTP or `curl`.
- Never add, change or guess recipients. If the user wants someone else included, tell them to add the
  address to `RECIPIENTS` in the Apps Script settings.
- Never print, echo or open `REPORT_EMAIL_TOKEN`, `REPORT_EMAIL_URL` or `.env`, and never commit them.
- Never edit report files or reporting code, and never generate a report just to email it. If there is
  no finished report, say so.
- One request sends one file. Sending several files needs the user to name each one.
