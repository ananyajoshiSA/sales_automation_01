# Email a report only when you ask: `/email-report`

In the Claude Code conversation where reports were made, type **`/email-report`** or say **"email this
report"**. Claude takes the latest set of reports it made (for example a plan PDF and its Excel workbook)
and emails them together, as attachments in **one email**, to your approved recipients through a free
Google Apps Script in your Google Workspace account.

Making a report never sends anything. An email goes out only when you ask for it.

**Lever:** the team leader gets the report the moment you decide it is ready, so stuck callers are coached
the same day (conversation quality, productive dialling). We will see it in how quickly coaching follows a
report, not directly in enrolments.

## Where the reports are

Reports are real files, not just chat text, and they can be in any folder of the conversation's machine:

| Report | File | Folder |
|---|---|---|
| Daily team calling report | `team_calling_report_DATE.pdf` | `/mnt/project-files/reports/` if it exists, else `exports/` |
| Revenue plan tracker | `plan_tracker_DATE.pdf` | same |
| Daily plan (PDF and workbook) | `<Team>_call_plan_<Day>.pdf`, `.xlsx` | `exports/daily/DATE/` |
| Nightly call plan | `call_plan_..._DATE.xlsx` | `exports/plans/` |
| Anything a chat builds by hand | PDF, Excel, CSV, Word, PowerPoint, PNG/JPEG, HTML | wherever it saved it, often its scratch folder |

They are never saved to GitHub (they name callers and leads) and they stay in the session that made them.
That is why the email is sent from **that same conversation**: it is the only place that can see the files.
The repo's `data/` folder (raw lead data) and hidden files such as `.env` are never emailed.

## What happens when you ask

1. Claude picks the files: the ones you named, or every report it made in this conversation since the last
   email. If it made none, the script takes the latest set it can find in the repo, Claude's scratch
   folders and `/mnt/project-files/reports/`: the newest report and every report finished within 30 minutes
   of the next one, since this machine's last email and within the last 24 hours.
2. It refuses drafts (`report_draft.pdf`), files still being written, raw data, and anything that isn't
   really the type its name says. If nothing new was made, or more than 10 files would go, it asks which
   files you mean.
3. It checks the mailer (nothing is sent yet). If delivery is off, or this environment is missing the
   settings, it says so and stops.
4. It sends the files together in one email and tells you what went out, when each finished (IST) and to
   how many people.

## How it stays safe

- **Approved people only.** Recipients live only in the Google settings and must be on a domain you
  allow, such as `lawsikho.in`. Nothing in a request can add a recipient. The limit is 50 recipients.
- **Never twice.** Each file's content is logged when sent. A file already emailed is left out of a later
  set; if every file was, the answer is "already sent at …" and nothing goes out, unless you say "send it
  again". A corrected report (new content) goes out normally.
- **Off, test or live.** It starts `off`. In `test`, only you get the email, marked `[TEST]`. In `live`,
  the approved list gets it. Set `off` to stop everything at once.
- **A daily cap.** At most 20 emails a day (you can change this), so a leaked password can't flood
  inboxes. One email counts once, however many files it carries (at most 10).
- **Size.** A set over 20 MB in total is not sent (Gmail allows 25 MB per email). Claude offers to share a Drive link
  with the approved recipients instead, and does nothing until you agree.
- **Secrets stay out of GitHub and out of the chat.** The web address and password live only in Google's
  settings and the Claude Code environment settings. The mailer only has permission to send email: it
  cannot read your Drive or your inbox.

## Setup, one step at a time (about 20 minutes, once)

Do all of this outside your original report conversation.

### Step 1. Create the script

1. Go to <https://script.google.com> and sign in with your work account.
2. Click **New project**. Click "Untitled project" and rename it `Report mailer`.

### Step 2. Paste the code

1. In the editor, open `Code.gs`, delete everything in it, and paste the whole text of
   `email_report/Code.gs` from this branch on GitHub. Press **Ctrl+S** (Cmd+S on a Mac).
2. Click the gear icon (**Project Settings**). Tick **Show "appsscript.json" manifest file in editor**.
3. Back in the editor (`< >` icon), open `appsscript.json`, replace everything with the text of
   `email_report/appsscript.json`, and save.

### Step 3. Enter the settings

In **Project Settings**, scroll to **Script Properties**, click **Add script property** for each row, then
**Save script properties**:

| Property | Value |
|---|---|
| `DELIVERY_MODE` | `off` |
| `ALLOWED_DOMAINS` | `lawsikho.in` (add others with commas only if a recipient needs them) |
| `TEST_RECIPIENT` | your own work email |
| `RECIPIENTS` | the approved addresses, separated by commas |
| `MAX_SENDS_PER_DAY` | leave out to use 20 (at most 100) |
| `LIST_<NAME>` | optional named lists, e.g. `LIST_ELITE`: a team's full set of addresses, separated by commas |

You can change these at any time with no redeploy: edit, save, and the next email uses them.

### Step 4. Create the password and allow sending

1. In the editor, choose `createUploadToken` in the function list at the top and click **Run**.
2. Google asks for permission. Click **Review permissions** and choose your account. If you see "Google
   hasn't verified this app", that is normal for your own script: click **Advanced**, then **Go to Report
   mailer (unsafe)**, then **Allow**. The only permission it asks for is to send email as you.
3. The **Execution log** shows `New UPLOAD_TOKEN` and a long code. Keep that code for step 6. Never paste
   it into a chat, an email or GitHub.

### Step 5. Check the settings

Choose `checkSetup` and click **Run**. The log should say `Mode: OFF`, list your recipients and say
`Upload token: set`. If there is a mistake, the log names the setting to fix.

### Step 6. Publish the mailer

1. Click **Deploy → New deployment**. Click the gear next to "Select type" and choose **Web app**.
2. Description `v1`, **Execute as: Me**, **Who has access: Anyone**. Click **Deploy** and copy the **Web
   app URL** (it ends in `/exec`).

"Anyone" only lets a request reach the mailer. Without the password every request is refused, and
recipients can never come from a request. If *Anyone* is not offered, your Workspace admin has turned it
off. Ask them to allow it for this script.

### Step 7. Give Claude Code the address and password

In claude.ai/code, open the cloud environment menu in the session's title bar, choose the environment
your report conversation uses, click **Edit**, and add two **environment variables**:

```
REPORT_EMAIL_URL=the web app URL from step 6
REPORT_EMAIL_TOKEN=the code from step 4
```

Do not put them in GitHub or type them into a chat. A new session picks them up. On your own computer,
put the same two lines in the project's `.env` file instead.

### Step 8. Test with your own address

1. In Script Properties, set `DELIVERY_MODE` to `test` and save. Run `checkSetup`: it should say
   `Mode: TEST (only you@... is emailed)`.
2. In a **new** Claude Code session on this branch (not your original one), make or copy a small finished
   report into `exports/`, then type `/email-report`.
3. You should get one email marked `[TEST]` with the file attached. Try a PDF and an Excel file.
4. Type `/email-report` again for the same file: Claude should say it was already sent, and no second
   email arrives.
5. Generate a report without asking for email: no email should arrive.
6. Run `showSentLog` in the Apps Script editor to see what was sent.

### Step 9. Merge, then use it in your original conversation

1. Review and merge the pull request into `main`.
2. Your original conversation must load the new code and skill. A cloud session works on its own copy
   of the code, so an older session does not see the merge by itself. Type `/email-report` there
   while the mailer is still in `test` mode:
   - If Claude recognises it and emails you a `[TEST]` copy, the original conversation is ready.
   - If `/email-report` is not recognised, that session hasn't loaded the new code. Asking it to run
     `git pull origin main` brings the code in, and a running session picks up a new skill as soon as
     its files arrive (seen while building this). If it is still not recognised, use a new session.
   - If Claude says `REPORT_EMAIL_...` is missing, that session started before step 7. Environment
     settings reach new sessions, so make reports that you want to email in a new session.
3. When the test email arrives from the conversation you will use, set `DELIVERY_MODE` to `live`.

## Everyday use

| You say | What happens |
|---|---|
| "email this report" or `/email-report` | every report made in this conversation since the last email goes in one email |
| `/email-report exports/plans/call_plan_Elite_2026-10-12.xlsx` | that file is sent |
| `/email-report plan.pdf plan.xlsx` | those files go together in one email |
| "send it again" (after "already sent") | the same files are sent again |

To add or remove a recipient, edit `RECIPIENTS` in Script Properties. To stop all email, set
`DELIVERY_MODE` to `off`.

### Recipient lists per chat

A chat can send to a named list instead of `RECIPIENTS`: "email this report to the deepanshi list" runs
`scripts/email_report.py ... --to-list deepanshi`, which emails everyone in `LIST_DEEPANSHI` (and only them).
The request names the list, never addresses; an unknown list, or a mailer older than the list `Code.gs`, sends
nothing. The Elite daily plan's `--mail` uses `LIST_ELITE` (`--mail-list`, default `elite`). Each list has its
own sent log, so the same report can go to two lists. In test mode every email still goes only to `TEST_RECIPIENT`.

### WhatsApp copies through WATI (optional, off until you switch it on)

**Lever:** callers and team leaders see the plan on their phones the moment it is built, so the first calls of
the day start sooner (speed to lead, calling the right leads first). We will see it in how early each caller's
first dials of the day come.

After a **live** email to a list, the mailer also sends the same new files on WhatsApp to that list's numbers,
using the approved WATI template `daily_report_pdf`: one message per file per number, so a plan PDF and its
workbook arrive as two messages. Nothing changes in the chats, the scheduled jobs or the Python code: whatever
emails a list also WhatsApps it. A file already emailed is never sent again on WhatsApp either. Email is never
held up by WhatsApp: a WhatsApp problem only adds a "WhatsApp: not sent ..." line to the result.

Each file is kept for good in the Drive folder **Sales reports / <team> / <date>**, which only people at your
company can open (on a personal Google account it stays private to you). WATI collects the file through a link
that is closed again after 3 days; people keep the file itself in their WhatsApp chat.

| Property | Value |
|---|---|
| `WHATSAPP_MODE` | `off` (default) or `live` |
| `WATI_TOKEN` | the WATI API key (WATI → Connector → API) |
| `WATI_CLIENT_ID` | the WATI client (tenant) ID |
| `WA_LIST_<NAME>` | numbers for the matching `LIST_<NAME>`, comma-separated, with country code: `9198xxxxxxxx` |
| `WA_TEST_NUMBER` | your own number, the only one `sendWhatsAppTest` messages |
| `WATI_TEMPLATE` | leave out to use `daily_report_pdf` |
| `WATI_CHANNEL_NUMBER` | leave out unless WATI asks for the sender number |
| `WA_MAX_PER_DAY` | leave out to use 200 messages a day (at most 1000) |

WATI numbers the template's variables. The mailer fills `{{1}}` (header document link), `{{2}}` (report, from
the file name, e.g. "Status at 13:50 (PDF)"), `{{3}}` (team, "Elite team") and `{{4}}` (the dates in the file name,
else the day it is sent, IST). The message greets "Hello team". Any new team or report works with the same template: add its `WA_LIST_<NAME>`.

To switch it on: paste the new `Code.gs` **and** `appsscript.json` (it adds Drive, outside requests and a
clean-up timer), deploy a new version and allow the new permissions. Run `checkWhatsApp`: it checks the key and
client ID with WATI and says whether the template is APPROVED, sending nothing. Run `sendWhatsAppTest`: one
sample PDF with no lead data goes to `WA_TEST_NUMBER`. When it arrives, set `WHATSAPP_MODE` to `live`.
`showSentLog` lists WhatsApp sends too. To stop WhatsApp at once, set `WHATSAPP_MODE` to `off`.

## Update the mailer (after `Code.gs` changes)

Sending several files in one email and recipient lists (both 21 Oct 2026) need the new `Code.gs`. Until it is
deployed, single files to `RECIPIENTS` still go out, and a set or a send to a list stops with "mailer: outdated".

1. Open the `Report mailer` project at <https://script.google.com>, open `Code.gs`, select everything and
   delete it, paste the whole text of `email_report/Code.gs` from `main` on GitHub, and save (Ctrl+S).
2. **Deploy → Manage deployments → edit (pencil) → Version: New version → Deploy.** The web address stays
   the same, so nothing changes in the Claude Code environment settings.
3. Run `checkSetup` once: it should still list your mode and recipients.

## When something goes wrong

| Claude says | Meaning and fix |
|---|---|
| delivery is off | `DELIVERY_MODE` is `off`. Set `test` or `live` |
| `unauthorized` | the password in the environment settings doesn't match `UPLOAD_TOKEN`; copy it again |
| did not answer with JSON | the web app isn't deployed with access *Anyone*, or the URL is wrong |
| `REPORT_EMAIL_...` is missing | this session can't see the step 7 settings; use a new session |
| no new report / more than 10 files | name the files you want, e.g. "email the calling report" |
| `mailer: outdated` | the Apps Script is the one-file version; follow "Update the mailer" above |
| outside `ALLOWED_DOMAINS` | a recipient's domain isn't allowed; fix the address or add the domain |
| `unknown_list` | the chat named a list that isn't in Script Properties; add `LIST_<NAME>` or fix the name |
| `limit` | the daily cap is reached; raise `MAX_SENDS_PER_DAY` or wait until tomorrow (IST) |
| WhatsApp: not sent: ... | a WhatsApp setting is wrong (named in the message); the email still went out |
| WhatsApp: ... failed: ... (Drive: ...) | Drive refused the file or its link; your Workspace may block "anyone with the link" sharing |
| WhatsApp: ... (HTTP 401) | WATI refused the key; check `WATI_TOKEN` and `WATI_CLIENT_ID` with `checkWhatsApp` |
| too many times | Google's daily email quota is used up; try again tomorrow. Nothing was marked sent |

Free limits: Google Workspace scripts can email about 1,500 recipients a day, at most 50 per email, with
25 MB of attachments per email.

## For developers

- `.claude/skills/email-report/SKILL.md`: when Claude may send, and how it picks the files.
- `scripts/email_report.py`: picks and checks the files, asks the mailer, sends them in one request (uses
  `requests` only). It remembers this machine's last email in `data/email_report/last_sent.json`.
- `email_report/Code.gs`, `email_report/appsscript.json`: the mailer. Its one permission is
  `script.send_mail`.
- Tests: `.venv/bin/python -m pytest tests/test_email_report.py` and `node --test email_report/code.test.mjs`
  (fake Google services, nothing is emailed).
