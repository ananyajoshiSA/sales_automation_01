---
name: email-report
description: Email the latest set of completed reports (PDF, Excel, CSV, Word, PowerPoint, images, HTML) in one email to the approved recipients through the Google Apps Script mailer. Use ONLY when the user's own message explicitly asks to email or send a report, e.g. "email this report", "email the report", "send this report by email", "mail the report to the team", or when they type /email-report. Never use it on your own, after generating a report, or because a report was mentioned.
argument-hint: "[report files] [resend]"
---

# Email a report, only when asked

Sends the latest set of finished report files, together in one email, to the predefined, approved
recipients. The Google Apps Script mailer (`email_report/Code.gs`) holds the recipient list, the off/test/live switch, a daily cap and a sent log.
Guide: `docs/email_report.md`.

## 0. Is this an explicit request?

Go on only if the user's latest message asks to email or send a report, or they typed `/email-report`.
Generating a report, finishing one, or talking about one is **not** a request. If you are unsure, ask
"Shall I email <file> to the approved recipients?" and wait.

## 1. Find the reports

Work from the repository root. Use `.venv/bin/python` if it exists, otherwise `python3`, always with
`PYTHONPATH=.`. Files can be in any folder: your scratchpad, the repo, `/mnt/project-files/reports/`.

1. If the user named files (or passed them as the argument), use exactly those.
2. Otherwise, the latest set is **every report file you made or rebuilt in this conversation since the last
   `/email-report` here** (or since the start of the conversation): the final PDF, Excel, CSV, Word,
   PowerPoint, image or HTML files you handed the user as results. Pass each one's full path. Leave out test
   runs, scratch notes, raw data and files you replaced with a newer version. `python -m analytics.team_performance`
   prints its file as `report: <path>`; its main report is `team_calling_report_DATE.pdf`, and
   `plan_tracker_DATE.pdf` belongs to the set only if the user asked for the tracker.
3. If you made none, or can't tell, leave the files out and let the script pick the latest set: the newest
   report and the ones finished shortly before it, since this machine's last email and within 24 hours.

## 2. Preview (sends nothing)

```bash
PYTHONPATH=. python3 scripts/email_report.py [FILE ...] --check
```

| Output | What to do |
|---|---|
| `unclear: ...` | Show the user the reason (`--list` shows the newest 15 reports) and ask which files. Stop. |
| `refused: ...` | Tell the user why. Stop. Never copy, rename or edit a file to get past a refusal. |
| `too_large: ...` | Say the set is over 20 MB and offer to share it as a Google Drive link with the approved recipients only. Do nothing until they agree. |
| `mailer: outdated ...` | Say the Apps Script takes one file per email until the new `Code.gs` is deployed (`docs/email_report.md`, "Update the mailer"). Offer to send the files one at a time. Stop. |
| `mailer: off ...` | Say delivery is switched off in the Apps Script settings (`DELIVERY_MODE`). Stop. |
| `error: REPORT_EMAIL_...` | Say this environment is missing the mailer settings (`docs/email_report.md`, step 7). Never ask for the token in chat. Stop. |
| `mailer: <mode> mode, N approved recipient(s)` | Go on to step 3. |

## 3. Send

Send the exact paths from the `file:` lines of the preview, all in one command (one email):

```bash
PYTHONPATH=. python3 scripts/email_report.py "<path 1>" "<path 2>" ...
```

Add `--resend` only when the user asked to send it again after being told it was already sent.

## 4. Tell the user, in two or three lines

- **sent:** the file names, when they finished (IST), and how many approved recipients got the email. If the
  result says files were left out as already emailed, name them. In test mode, say only the test address got it.
- **duplicate:** every file was already sent (say when) and nothing was sent again. They can say "send it again".
- **anything else:** quote the reason in plain words. Nothing was sent.

## Rules

- Send only through `scripts/email_report.py`. Never use Gmail, another email tool, SMTP or `curl`.
- Never add, change or guess recipients. If the user wants someone else included, tell them to add the
  address to `RECIPIENTS` in the Apps Script settings.
- Never print, echo or open `REPORT_EMAIL_TOKEN`, `REPORT_EMAIL_URL` or `.env`, and never commit them.
- Never edit report files or reporting code, and never generate a report just to email it. If there is
  no finished report, say so.
- Never send anything from the repo's `data/` folder (raw lead data) or a hidden file such as `.env`; the script
  refuses them too.
- One request sends one email. Never split a set into several emails unless the user agrees (an outdated mailer).
