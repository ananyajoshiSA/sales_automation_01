# Collection reports (community and bootcamp)

Two reports for the collection chats: **Community collections** and **Bootcamp collections**. Each one says who to
call first, how much has been received and how much is still to collect. It breaks those numbers down by month,
team, collector and community or bootcamp, and it ends with the data checks. Each report is a PDF plus a workbook
listing every student. Lever: returning leads' calls and calling the right leads first, applied to the balance after
a booking fee. These are the first reports in the repo with **rupee amounts**, because both sheets record course
price, money received and balance due (GOAL.md gap G1).

```bash
python -m scripts.fetch_collections data/collections/2026-11-03            # sheets + LeadSquared (read-only)
python -m analytics.collections data/collections/2026-11-03 exports/collections/2026-11-03
```

Add `community` or `bootcamp` as a third argument to the second command to build one report only.

## Sources

| Source | What it gives | How it is read |
|---|---|---|
| Community Webinar Collection Sheet (Google Sheet) | One row per community enrolment: course price, amount paid, subvention, discount, pending revenue, status, owner, team | Whole workbook as .xlsx, hidden tabs included; ID in `COMMUNITY_COLLECTION_SHEET_ID` |
| Bootcamp Pool data (Google Sheet) | Tab "Mastersheet": one row per bootcamp student, with the same money fields, collector ("Elite Changemakers" column) and team leader | Same; ID in `BOOTCAMP_POOL_SHEET_ID` |
| LeadSquared | Leads whose **Bootcamp collections** field is a community collection ("Community Webinar Collections") or a bootcamp collection (bootcamp name and date), with phone, email, owner, stage and **Funnel name** (the community type) | `Leads.Get` per tag, lead fields only (about 135 requests); through the admission gate |

The sheets are shared "anyone with the link", so no Google sign-in is needed. Their IDs open student data, so they
live in the environment settings (or `.env`), never in the repo. If a sheet stops being shared by link, the fetch
stops with a clear error. It never produces an empty report.

## How the sheets are read (no manual step)

- **Community sheet:** each month comes from its own tab ("August 2026", including the hidden ones). A month with no tab of
  its own (May to July 2026 on 2 Nov) comes from the combined tab that holds the most rows for it. So the same student is
  never counted twice, and a new month's tab is used as soon as it appears. Summary tabs (MIS, pivots, snaps) have
  no phone column and are skipped. The PDF's last check lists how many rows were left out of each tab and why.
- **Bootcamp sheet:** only "Mastersheet". The other tabs are pivots built from it. Rows with a bootcamp and dates but
  no student are skipped and counted.
- **Status** comes from the sheet's own column, grouped as Collected (Disbursed, One shot), Instalments, Pending
  (Pending, Booking fees received), Dropped (Drop), and Not stated/Other.
- **Money:** Received = money that reached us after loan subvention ("Amount Paid" / "Money received"). Still to
  collect = the balance on Pending and Instalments students. Lost to drops = the balance on Dropped students.

## How the sheets are joined to LeadSquared (no manual step)

1. **Students:** matched on the last 10 digits of the phone number (LeadSquared Phone or Alternate Number), then on
   email. If a number belongs to two leads, the lead tagged for the same kind of collection wins. If it is still
   unclear, the student is "Not matched in LeadSquared" with the reason. On 3 Nov: community 3,101 of 3,275 matched,
   bootcamp 6,713 of 6,742. Unmatched students keep their amounts in every total.
2. **Bootcamps:** each sheet bootcamp is paired to the LeadSquared tag that more than half of its matched students
   carry. Failing that, it goes to the one tag with the same bootcamp date and the best course-name match. Anything
   else is "Not paired". On 3 Nov all 92 bootcamps were paired by their students.
3. **Community type:** taken from LeadSquared's Funnel name ("57/CONTRACT/DRAFTING" becomes Contract Drafting), or
   from the sheet's Community Name when Funnel name is empty, or Other. When the two name different communities,
   LeadSquared is used and the count is shown (290 on 3 Nov; usually a student who joined a second community later).

## Checks in every report

- **Sheet and LeadSquared disagree:** the sheet says collected but LeadSquared shows a lost stage or is still at
  Booking fees received; the sheet says dropped but LeadSquared shows Collections done or Course Enrolled; or
  LeadSquared says Collections done while the sheet still shows a balance. These are flagged in the call list and in
  the workbook sheet "Sheet vs LeadSquared". The report never picks a side.
- **In LeadSquared, not in the sheet:** tagged leads still being worked (booking received, loan pending, follow-up,
  call back) that no sheet row matched.

## Files

`integrations/gsheets/` (download), `scripts/fetch_collections.py` (fetch), `analytics/collections/`
(`tags`, `sheets`, `match`, `report`, `render`, `__main__`), `tests/test_collections.py`. Inputs stay under `data/`
and outputs under `exports/`, both git-ignored. To email a report to a team, add a `LIST_<NAME>` in the mailer's
Script Properties and send with `scripts/email_report.py --to-list <name>`.
