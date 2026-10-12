# Email a report only when you ask: `/email-report`

In the Claude Code conversation where a report was made, type **`/email-report`** or say **"email this
report"**. Claude finds the finished report file and emails it as an attachment to your approved
recipients through a free Google Apps Script in your Google Workspace account.

Making a report never sends anything. An email goes out only when you ask for it.

**Lever:** the team leader gets the report the moment you decide it is ready, so stuck callers are coached
the same day (conversation quality, productive dialling). We will see it in how quickly coaching follows a
report, not directly in enrolments.

## Where the reports are

Reports are real files, not just chat text:

| Report | File | Folder |
|---|---|---|
| Daily team calling report | `team_calling_report_DATE.pdf` | `/mnt/project-files/reports/` if it exists, else `exports/` |
| Revenue plan tracker | `plan_tracker_DATE.pdf` | same |
| Nightly call plan | `call_plan_..._DATE.xlsx` | `exports/plans/` |
| Other reports | `.csv` files | under `exports/` |

They are never saved to GitHub (they name callers and leads) and they stay in the session that made them.
That is why the email is sent from **that same conversation**: it is the only place that can see the file.

## What happens when you ask

1. Claude picks the file: the one you named, or the report made in this conversation, or the newest
   finished report.
2. It refuses drafts (`report_draft.pdf`), files still being written, files outside the report folders,
   and anything that isn't a real PDF, Excel or CSV file. If two reports finished in the same run, or
   nothing finished in the last 24 hours, it asks you which file you mean.
3. It checks the mailer (nothing is sent yet). If delivery is off, or this environment is missing the
   settings, it says so and stops.
4. It sends the file and tells you what went out, when the report finished (IST) and to how many people.

## How it stays safe

- **Approved people only.** Recipients live only in the Google settings and must be on a domain you
  allow, such as `lawsikho.in`. Nothing in a request can add a recipient. The limit is 50 recipients.
- **Never twice.** Each file's content is logged when sent. Asking again for the same file answers
  "already sent at …" and sends nothing, unless you say "send it again". A corrected report (new content)
  goes out normally.
- **Off, test or live.** It starts `off`. In `test`, only you get the email, marked `[TEST]`. In `live`,
  the approved list gets it. Set `off` to stop everything at once.
- **A daily cap.** At most 20 emails a day (you can change this), so a leaked password can't flood
  inboxes.
- **Size.** Files over 20 MB are not sent (Gmail allows 25 MB). Claude offers to share a Drive link
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
| `MAX_SENDS_PER_DAY` | leave out to use 20 |

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
| "email this report" or `/email-report` | the report made in this conversation, or the newest finished one, is sent |
| `/email-report exports/plans/call_plan_Elite_2026-10-12.xlsx` | that file is sent |
| "send it again" (after "already sent") | the same file is sent again |

To add or remove a recipient, edit `RECIPIENTS` in Script Properties. To stop all email, set
`DELIVERY_MODE` to `off`. After changing `Code.gs`, use **Deploy → Manage deployments → edit (pencil) →
Version: New version → Deploy**. The address stays the same.

## When something goes wrong

| Claude says | Meaning and fix |
|---|---|
| delivery is off | `DELIVERY_MODE` is `off`. Set `test` or `live` |
| `unauthorized` | the password in the environment settings doesn't match `UPLOAD_TOKEN`; copy it again |
| did not answer with JSON | the web app isn't deployed with access *Anyone*, or the URL is wrong |
| `REPORT_EMAIL_...` is missing | this session can't see the step 7 settings; use a new session |
| several reports finished together | name the one you want, e.g. "email the calling report" |
| outside `ALLOWED_DOMAINS` | a recipient's domain isn't allowed; fix the address or add the domain |
| `limit` | the daily cap is reached; raise `MAX_SENDS_PER_DAY` or wait until tomorrow (IST) |
| too many times | Google's daily email quota is used up; try again tomorrow. Nothing was marked sent |

Free limits: Google Workspace scripts can email about 1,500 recipients a day, at most 50 per email, with
25 MB of attachments per email.

## For developers

- `.claude/skills/email-report/SKILL.md`: when Claude may send, and how it picks the file.
- `scripts/email_report.py`: picks and checks the file, asks the mailer, sends (uses `requests` only).
- `email_report/Code.gs`, `email_report/appsscript.json`: the mailer. Its one permission is
  `script.send_mail`.
- Tests: `.venv/bin/python -m pytest tests/test_email_report.py` and `node --test email_report/code.test.mjs`
  (fake Google services, nothing is emailed).
