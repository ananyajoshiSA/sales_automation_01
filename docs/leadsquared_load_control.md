# LeadSquared load control

Every LeadSquared request the Python code makes goes through one gate in `LeadSquaredClient.request`
(`integrations/leadsquared/admission.py`). The gate decides when each HTTP attempt, retries included, may
start. It is shared by every thread, client and process on one machine through a small SQLite file in the
git-ignored `data/` folder. This page says what it does, how to set it, what it doesn't cover, and how to undo it.

## The account's limits

From LeadSquared's API usage page (Super plan, read by the user on 15 Oct 2026):

- **30 requests every 5 seconds** for the whole account (the user's figure on 16 Oct 2026; an earlier reading
  of the page said 35).
- **474,000 requests a day** (100,000 plus 1,000 per user).
- On 15 Oct 2026 the account had made 72,170 requests by 16:23 IST (15% of the day). This repo's scheduled jobs
  made roughly 5,000 of them; the rest come from other tools connected to the account (dialers, Zipteams,
  TimePay, lead forms and so on), which this gate can't see or limit. That traffic averages about 6 requests
  every 5 seconds, so it shares the 30 with whatever this repo sends.

Both limits apply to the account as a whole: every machine, chat and tool that uses it counts towards them.

## What the gate does

- **At most two requests at once** (`LEADSQUARED_MAX_IN_FLIGHT`, default 2) across every Python process using
  the same store, for the same LeadSquared account. Only code that sends requests in parallel (the lead-history
  reads in `analytics/daily_plan/revival.py` and `scripts/fetch_lead_histories.py`) or two scripts running
  together use the second slot; everything else still sends one request after another.
- **A half-second pause** after each request finishes before the next one starts (`LEADSQUARED_COOLDOWN_SECONDS`,
  default 0.5). The pause is shared: when either request ends, nothing starts for 0.5 s, so two parallel readers
  start in pairs. It spreads requests out instead of letting them go back to back, and on its own allows at
  most one start every 0.5 s per slot. The pause is kept in the store, so it survives a restart and is shared
  by processes.
- **At most 10 requests start in any 5 seconds** (`LEADSQUARED_MAX_REQUESTS_PER_WINDOW`,
  `LEADSQUARED_WINDOW_SECONDS`), retries included, on a rolling window. With requests taking about 1 s, one
  reader runs at about 3 per 5 s and two parallel readers at about 6-7 per 5 s (estimates), so the ceiling binds
  only when replies come back very fast. LeadSquared's own account limit is 30 per 5 s, so this keeps 20 per 5 s
  free for other users of the account. It is per machine like the rest of the gate: two machines running at once
  can each send 10.
- **The company-wide activity read starts at most once every 2 seconds** (2.5 per 5 s,
  `LEADSQUARED_ENDPOINT_MAX_PER_WINDOW=activity_bulk=2.5`, the default since 16 Oct 2026), retries included. This
  is `RetrieveByActivityEvent`, the read behind every day's call log, Zipteams notes and payments; LeadSquared
  allows 2.5 per 5 s on it. Other reads are not slowed by it. A day's report needs roughly 200 of these reads
  (estimate), about 7 minutes at this rate. Per machine like the rest of the gate: two chats fetching at once
  each send 2.5 per 5 s, so run one fetch at a time.
- **Retries go through the gate too.** A retry waits for the later of its own backoff (1, 2, 4 s) and the
  cooldown. A 429's `Retry-After` (seconds or an HTTP date) holds every caller, not just the one that got it.
  A hold is only ever lengthened, never shortened. Which statuses are retried is unchanged (429, and 5xx
  replies that aren't LeadSquared API errors, up to 3 retries).
- **Optional budgets**, off unless an operator sets them: attempts per hour, attempts per day, records asked
  for per day, and attempts per hour for one kind of endpoint. Budgets use rolling windows (the last 60
  minutes, the last 24 hours), not calendar hours.
- **Every admitted attempt is counted** when it is admitted. An attempt that crashes or is interrupted
  afterwards is still counted, never refunded.
- **Bulk reads by lead ID** are limited to 250 IDs per request (`LEADSQUARED_MAX_IDS_PER_BULK_REQUEST`). A
  request with more is refused with an error, not split behind the caller's back.
- **Kinds of endpoint** are told apart by path, never by HTTP verb (LeadSquared reads often use POST):

  | Category | Paths |
  |---|---|
  | `lead_history` | `ProspectActivity.svc/Retrieve` (one lead's activities) |
  | `activity_bulk` | `ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent` |
  | `lead_bulk_ids` | `LeadManagement.svc/Leads/Retrieve/ByIds` |
  | `lead_read` | `Leads.Get`, `Leads.GetById`, `Leads.GetByEmailaddress`, `RetrieveLeadByPhoneNumber` |
  | `metadata` | `LeadsMetaData.Get`, `ActivityTypes.Get`, `Users.Get` |
  | `write` | `Lead.Create`, `Lead.Update`, `Lead.CreateOrUpdate`, `ProspectActivity.svc/Create` |
  | `other` | anything else |

## When the gate says "not now"

The gate never waits without limit. If the next start is more than `LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS`
(default 300) away, or a budget is used up, the request is not sent and the client raises
`AdmissionDeferred`. Its message names the reason and the next time a request can go, for example
`LeadSquared request deferred: hourly request budget; next eligible at 2026-10-14 12:00:00 UTC`. `reason` and
`next_eligible` are also attributes, so scripts can act on them.

What each fetch does with it:

| Fetch | On a deferral |
|---|---|
| `scripts/fetch_lead_histories.py` (also used by `fetch_accountability.py`, `fetch_bootcamp_collections.py`) | Stops, keeps every lead already written, prints when to rerun, exits with code 75. Rerunning the same command resumes. |
| `python -m analytics.convintel inventory` | Marks the run failed with the reason and keeps the calls of every block already read. A rerun reads the period again and stores only what changed. |
| First-enrolment scans (`fetch_report_day.py`, convintel) | Stop with the error. Every first enrolment found before the stop is already stored, so the rerun is shorter. |
| Every other fetch | Stops with the error; rerun the command. Nothing is skipped silently. |

If the store can't be used (a bad path, a broken file), the client raises `CoordinatorUnavailable` and
sends nothing. The message names the file and the `LEADSQUARED_ADMISSION_DB` setting.

## Settings

All are optional. None of the hourly or daily budgets has a value in this repo: with the account's 474,000 a day
(above) the daily limit is far off, so an operator sets budgets only if they want a tighter share. A malformed value stops the client with
an error naming the variable.

| Variable | Default | Meaning |
|---|---|---|
| `LEADSQUARED_COOLDOWN_SECONDS` | `0.5` | Pause after each request finishes. `0` = no pause. Was `6` until 16 Oct 2026. |
| `LEADSQUARED_MAX_REQUESTS_PER_WINDOW` | `10` | Most requests (retries included) that start in any window. Whole number, or `off`. |
| `LEADSQUARED_WINDOW_SECONDS` | `5` | Length of that window in seconds. Above 0. |
| `LEADSQUARED_ENDPOINT_MAX_PER_WINDOW` | `activity_bulk=2.5` | Starts per window for one endpoint category, kept evenly spaced (2.5 per 5 s = one every 2 s). Fractions allowed; `off` removes it. |
| `LEADSQUARED_MAX_IN_FLIGHT` | `2` | Requests allowed at once across processes. Whole number, 1 or more. Was `1` until 16 Oct 2026. |
| `LEADSQUARED_MAX_REQUESTS_PER_HOUR` | off | Attempts in any 60 minutes. Whole number, or `off`. |
| `LEADSQUARED_MAX_REQUESTS_PER_DAY` | off | Attempts in any 24 hours. Whole number, or `off`. |
| `LEADSQUARED_MAX_RECORDS_PER_DAY` | off | Records asked for in any 24 hours (IDs in a bulk read, else the page size, else 1). |
| `LEADSQUARED_ENDPOINT_HOURLY_BUDGETS` | none | Per-category hourly limits, e.g. `lead_history=600,activity_bulk=200`. |
| `LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS` | `300` | Longest the gate sleeps before it defers instead. |
| `LEADSQUARED_LEASE_SECONDS` | `120` | How long a request's slot is held without a heartbeat (it renews every third of this while the process lives). |
| `LEADSQUARED_MAX_IDS_PER_BULK_REQUEST` | `250` | Most lead IDs in one bulk read. |
| `LEADSQUARED_ACCOUNT_LABEL` | from the keys | Name the gate uses for the account (letters, digits, `.`, `_`, `-`). Set the same label where two key pairs belong to one LeadSquared account so they share one gate. |
| `LEADSQUARED_ADMISSION_DB` | `data/leadsquared/admission.sqlite3` | Where the gate's store lives. Keep it on local disk. |
| `LEADSQUARED_ENROLMENT_STORE` | `data/leadsquared/first_enrolments.sqlite3` | First-enrolment store (below); `off` turns it off. |

Without a label, the account is named `key-` plus a 16-character hash of the host and access key, so the key
itself is never stored or printed.

To see the gate's state: `python -m integrations.leadsquared admission`. It prints the settings, requests in
the last hour and 24 hours by kind and outcome, what's left of each budget, average wait and duration, and
the last deferral. It prints no keys, URLs or lead details.

## Less work, not just slower work

The gate spreads requests out; it doesn't reduce how many there are. These changes reduce them:

- **First enrolments are read once per lead** (`analytics/enrolment_store.py`). The daily report fetch and
  conversation intelligence each scan the leads now in Course Enrolled that were edited
  since the start of their window, and used to read every one's stage history on every run. A first
  enrolment never changes once it happens, so it is now stored the first time it is read and reused by
  both. Only complete histories that show an enrolment are stored; anything else is read again next time,
  as before. Each scan prints how many leads came from the store and how many histories it read.
- **Bulk lead reads take 250 IDs per request** instead of 200 in conversation intelligence.
- **Repeated lead IDs are fetched once** in `fetch_lead_histories.py`.

These lower the request count for repeated runs over overlapping days. How much depends on how many
enrolled leads are edited each day; it has not been measured against LeadSquared.

Not done yet: incremental lead sync with a saved watermark. Lead `ModifiedOn` misses activity-only changes,
merges and deletes, so it needs a decision on which fields and readers it would serve before it can replace
full reads safely.

## What it does not cover

- **Other machines.** The store is a file on one machine. A fetch running on the user's computer and another
  in a cloud session don't see each other. Don't put the store on `/mnt/project-files` or any network
  mount: SQLite locking there is unreliable. Run LeadSquared fetches from one machine at a time.
- **The old dashboard Worker on Ananya's Cloudflare account** still polls with bad keys; only deleting it
  on Cloudflare stops it. (The dashboard code was removed from this repo on 15 Oct 2026.)
- **Requests made outside `LeadSquaredClient`.** There are none in the Python code today; keep it that way.
- **Code on other branches.** The Team Elite Calling jobs run from branch `ccr-a0c7f8a2-aj2dly`, whose client
  has no gate (it only limits history reads to 5 a second), so neither the pause nor the 10-per-5-seconds
  ceiling applies to them.
- **A slow reply.** `requests`' timeout applies to each socket read, not the whole reply. The slot stays
  held (its lease is renewed) as long as the process is alive; a process that dies frees it when the
  lease runs out.

## Turning it down or off

- **Back to the old pace (before 16 Oct 2026):** `LEADSQUARED_COOLDOWN_SECONDS=6` and
  `LEADSQUARED_MAX_IN_FLIGHT=1`: one request at a time, about one every 7 s.
- **No pause:** `LEADSQUARED_COOLDOWN_SECONDS=0` and leave the budgets unset. The 10-per-5-seconds ceiling still applies; `LEADSQUARED_MAX_REQUESTS_PER_WINDOW=off` removes it.
- **Re-read every enrolment history:** `LEADSQUARED_ENROLMENT_STORE=off`, or delete the file.
- **Forget the gate's history:** delete `data/leadsquared/admission.sqlite3` while no fetch is running.
- **Undo the code:** `git revert` the commit that added this page. That brings back the earlier per-process
  pacer (one at a time, 4 s cooldown, not shared between processes).
