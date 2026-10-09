# Lead accountability: who actually did it

Set by the user on 9 Oct 2026. A lead sitting in an admin's account, or a change made through a shared
admin login, never makes that admin accountable. Each assignment, transfer, follow-up and related update
goes to the person the evidence names, or is marked **Unverified**.

Lever: productive dialling and speed to lead, through fair per-person reporting. Team leaders can see who
moved a lead, who left it unworked, and which changes nobody can be held to yet.

## What LeadSquared records (checked live, 9 Oct 2026)

- **Owner-change log** (activity `LeadAssigned`, event 3001, only readable per lead): previous owner, new
  owner, and `CreatedBy`, the login that made the change. This is the per-change "assigned by".
- **Assigned By** (`mx_Assigned_By`, a pick-list on the lead) and **Assigned On** (`mx_Assigned_On`): one
  value per lead, overwritten. Assigned On is also restamped by automation after calls on leads in Rinku
  Jhala's account (74 of 92 checked leads were moved into her account more than a day before their Assigned
  On), and Assigned By usually still names an earlier assigner (32 of 92 named someone other than the login
  that made the latest change; 42 were blank).
- **Calls**: the dialler's user. An inbound call on a lead in a shared account rings that account.
- **Stage changes**: the login that made them. `System` is LeadSquared automation.

Rinku Jhala's account (Administrator, no team) holds about 376,000 leads: it is the pool leads are parked
in. Pratik Sarkar, S Karunakarareddy and others move leads into it from their own logins; some changes and
bulk uploads are made from her login itself.

## The rules in code

`analytics/accountability.py` (history, any date range) and `dashboard/src/accountability.ts` (live) apply
the same rules. Evidence, strongest first:

1. **Corrected**: a team leader confirmed who did it (`--corrections`, a CSV of `key,performed_by,confirmed_by,note`).
2. **Verified**: the CRM's own record names a personal login (owner-change log, stage log, call log).
3. **Verified (Assigned By)**: a shared login made the change, and the lead's Assigned By can be tied to
   it: it is the lead's latest owner change, Assigned On was stamped at or after it, and the named person
   made no earlier change on the lead. Otherwise the field probably predates the change.
4. **Unverified**: a shared login, a blank or stale Assigned By, or a lead created from a shared login. The
   name the field holds, or the last person who called the lead, is shown as a *name to check*, not proof.
5. **Automated**: `System`.

Shared logins default to `Rinku Jhala` and `Admin` (`--shared`, or `SHARED_ACCOUNTS` on the Worker).

| Action | Accountable |
|---|---|
| Assignment (from no owner or a shared account to a person) | Verified "Assigned By" (the person resolved above) |
| Transfer (person to person) or move into a shared account | Verified "Transferred By" |
| Missed follow-up (no dial within 2 h of the due time) | Whoever owned the lead when it was due; Unverified if a shared account held it |
| Inbound call that rang a shared account, call or stage change from a shared login | Unverified |
| Admin oversight | Only supervisors listed explicitly (`--supervisors`, a JSON map of person to supervisor) |

Each row carries the rule-4 fields: lead ID, account owner, login used, assigned by, assigned to,
transferred by, action performed by, current responsible person, action time (IST) and status.

## Audit trail

`analytics.accountability` appends every new attribution and every later change (a correction, Assigned By
filled in) to `audit.jsonl`, with the previous answer; the file is never rewritten. The Worker keeps the
first answer next to the current one (`account_arrival.first_put_by`, `first_status`).

## Running it

```bash
PYTHONPATH=. python scripts/fetch_accountability.py 2026-10-04 2026-10-09 data/accountability/rj --owner "Rinku Jhala"
PYTHONPATH=. python -m analytics.accountability data/accountability/rj/leads.json data/accountability/rj/histories.jsonl \
    data/accountability/rj/users.json 2026-10-04 2026-10-09 data/accountability/rj/out --audit data/accountability/audit.jsonl
```

Without `--owner` it reads every lead whose Assigned On falls in the range (about 5,000 a day, one history
request each). Output stays under `data/`: `actions.csv`, `leads.csv` (who put each lead in its account),
`summary.md`.

The dashboard checks shared-account arrivals at :15 and :45 past each hour (up to 8 leads a run), shows
who put leads into each shared account, keeps shared logins out of the caller list, and labels enrolments
on leads in a shared account as such.
