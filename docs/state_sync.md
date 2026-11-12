# Team chat saves: keep a chat's working files across machine resets

**Why:** each team chat (Deepanshi, Sana, Darshan, Inayat, Women AI/DSV) runs on a cloud machine that is replaced
when the chat sits idle. The conversation and a fresh copy of the repo come back; nothing else does: the scratchpad,
the repo's `data/` and `exports/`, scripts never pushed. On 11 Nov 2026 the Sana chat lost its files this way and on
12 Nov the Deepanshi chat lost its build scripts, its LeadSquared history since 1 Sep and its readings of 2,790
leads. Each loss meant a missed report and hours of LeadSquared reads and lead re-reading to rebuild.

**What it does:** at the end of every run a chat saves its working folders to a private Google Drive folder; before
any work, scheduled or asked for in the chat, it loads them back. A reset then costs a few minutes, not the history.

**Everything is kept (owner's rule, 12 Nov 2026):** Drive holds every file a chat ever made, and every changed version
of it, forever. A save uploads only the files that are new or changed since the team's last save, plus a list of every
file the chat has and which save holds its bytes. So saves are quick, Drive grows only by real new work, and any older
day can be loaded back into a spare folder.

**Lever:** keeps every team's daily call plan going out (calling the right leads first), with no rebuild days.

## How it works

```
chat (any branch)                          report mailer (Apps Script)            Google Drive
state_sync.py save deepanshi <folders> ──► state_put (8 MB parts, checked) ──► Team chat saves (private)/deepanshi/
                                          state_commit (all parts arrived)        <save>.part000 … <save>.manifest.json
state_sync.py load deepanshi           ◄── state_get (manifest, then parts) ◄──  newest finished save
```

- **Private:** the folder `Team chat saves (private)` belongs to the mailer's Google account and is never shared,
  unlike the `Sales reports` archive (which the whole company domain can view).
- **Whole or nothing:** a save can be loaded only once every part has arrived and matched its checksum. A load
  checks every part, every save it reads and every file it restores before it writes a single file.
- **Kept forever:** no save is ever deleted. Only parts of a save that never finished (left over a day) go to the
  Drive bin. `STATE_KEEP` is no longer read.
- **What a load restores:** every file the chat had at its last save (files the chat deleted before that save stay in
  Drive but don't come back). When a restore would read more than 30 earlier saves, the next save uploads everything
  again, which keeps a restore quick. The first save after an old-style save (one bundle, the first format) also uploads
  everything once.
- **Never saved:** `.git`, virtual environments, caches and `.env` files. Lead data goes only to the private
  folder; nothing goes to GitHub.
- **Never emails:** saves don't send anything and don't count toward `MAX_SENDS_PER_DAY`.
- **Any branch:** `scripts/state_sync.py` imports nothing from this repo, so a chat on its own branch runs main's
  copy straight from git without switching or merging:

```bash
git fetch -q origin main && git show origin/main:scripts/state_sync.py | python3 - load deepanshi
```

## Commands

```bash
S=<this chat's scratchpad>; R=/home/user/sales_automation_01
# before any work (the daily run and anything asked in the chat): bring back what a wipe removed
git -C $R fetch -q origin main && git -C $R show origin/main:scripts/state_sync.py | python3 - load deepanshi
# end of every run, and after any one-off work: save the working folders (only new or changed files go up)
git -C $R show origin/main:scripts/state_sync.py | python3 - save deepanshi $S $R/data $R/exports --label "13 Nov run"
# the team's saves, newest first (--limit N for more); changes nothing
git -C $R show origin/main:scripts/state_sync.py | python3 - list deepanshi
# an older day, or one file, into a spare folder (nothing in the working folders is touched)
git -C $R show origin/main:scripts/state_sync.py | python3 - load deepanshi --as-of 2026-12-05 --into /tmp/old_day
git -C $R show origin/main:scripts/state_sync.py | python3 - load deepanshi --as-of 2026-12-05 --only $R/data/x.json --into /tmp/old_file
```

Team names are lower case with `_`: `deepanshi`, `sana`, `darshan`, `inayat`, `women_ai`.

| Exit | Meaning | What the run does |
|---|---|---|
| 0 | done; prints files, size, parts and seconds | carry on |
| 2 | `STOP:` no finished save for this team | stop and tell the person; never build from nothing |
| 1 | `FAILED:` with the reason (mailer down, saves off, a damaged part, a folder already has files) | stop and tell the person |

**Nothing is ever deleted or overwritten.** Saving only reads the folders. Loading brings back only saved folders
that are missing or empty (a wipe); a folder that is still here with its files is left exactly as it is, and when the
machine wasn't wiped at all the load downloads nothing. So the same load command is safe at the start of every run.
`--replace` also writes saved files over files of the same name (other files stay); `--into DIR` is for a test load
into an empty spare folder. `--version`, `--as-of` and `--only` load only with `--into`.

## Setup (once)

1. Update the mailer first: paste `email_report/Code.gs` from the pull request into the Apps Script project and deploy
   a new version (docs/email_report.md, "Update the mailer"). The web address stays the same. It still accepts
   old-style saves, so nothing breaks before the merge.
2. Merge the pull request into `main`. Chats pick it up on their next run; their commands don't change.
3. Script Properties: `STATE_SAVES` = `on` (already set since 12 Nov). `STATE_KEEP` can be deleted.
4. Run `checkStateSaves` from the editor: it should say `ON, every save kept forever` and `sharing: PRIVATE`.

## Setup message for each team chat

Replace `<team>` with `deepanshi`, `sana`, `darshan`, `inayat` or `women_ai`:

> Set up Drive save and load for this chat. Stay on your current branch; don't merge or push anything for this.
> 1. Load first, before any work (the daily run and anything I ask in this chat): `git fetch -q origin main && git show origin/main:scripts/state_sync.py | python3 - load <team>`.
>    If it exits 2 (no save yet) or 1 (failed), stop and tell me; never build from nothing.
> 2. Save last, at the end of every run and after any one-off work I ask for: the same command with
>    `save <team>` followed by every folder you work from (your scratchpad, and the repo's data/ and exports/ if you
>    use them). Include your scripts as well as your data. Quote the `saved ...` line in your summary.
> 3. Update your own daily scheduled task with steps 1 and 2.
> 4. Do a first save now, then a test load with `--into` a spare folder, and tell me both worked and how long each took.

## Limits

- Work done after the last save is lost; a reset in the middle of a run restarts that run from the last save.
- If Google Drive or Apps Script is down, a daily Google quota is reached, or the mailer's Drive is full, the run
  stops and says so. Nothing already saved is lost.
- One save is at most 100 parts of 8 MB (about 800 MB compressed). A full save (the first one, or after 30 saves)
  must fit; raw LeadSquared pulls a run can fetch again can be left out if a team grows near that.
- Drive grows by every new or changed file. The mailer's Google account needs room for it: check
  drive.google.com/settings/storage.
- Tests: `.venv/bin/python -m pytest tests/test_state_sync.py` and `node --test email_report/code.test.mjs`
  (fake Google services; nothing is saved or emailed).
