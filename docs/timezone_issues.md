# Time zone issues in the call data (checked 9 Oct 2026)

**Bottom line.** IST conversion itself is correct: every report reads the call start from LeadSquared's
`CreatedOn`, which is UTC, and converts it to IST. The risk is in three other places: which calls get
fetched for an IST day, how transcript times are read, and a few loose ends that make the next mistake
easy. Three fixes below close all of them.

**Status (9 Oct 2026): all three fixes are implemented on editor_ananya** and the report rules moved to
Parameters v1.3 (v1.4, also 9 Oct, changed only the report's layout; v1.5 changed how callers in several groups are given a team). `iter_activities_started` (LeadSquared client) does fix 1, `match_to_calls` (transcripts
client) does fix 2, and `integrations/timeutil.py` plus `tests/test_timeutil.py` do fix 3.
Live check on 6 minutes of 5 Oct calls (19:00–19:06 IST): the old query found 219 of the 254 calls that
started then, and the new one found all 254 (35 had been edited after the window). The 10 transcripts
checked all matched their call, 2 of them after correcting a 5 h 30 m clock error. A 6-minute window
exaggerates the share of late edits, so this does not size the full-day effect.

**Follow-up (9 Oct): Zipteams analysis on its own call, and a paging gap.**
- On 88 Zipteams notes created 14:00–14:30 IST on 5 Oct, every note was written as its call ended
  (0 to 36 s before start + duration), and that call was always the lead's last answered call. So the
  attribution rule names the exact call. The analysis (intent, scores, payment step) is now kept on the
  call (`analytics/zip_calls.py`) and written per call to `data/report_{D}/zip_calls.csv`, the transcript
  sample, the call-integrity file and the coaching sample. A note written after midnight is kept only
  for a day call that ended within 5 minutes of it.
- Reading a 4.5-hour window of outbound calls three times through LeadSquared's paging returned 12,203
  rows each time but 1 to 3 of them twice, so as many real calls were skipped, differently on each run.
  `iter_activities_started` now cuts any window bigger than one page into smaller windows instead of
  paging.

## What was checked

All read-only, on a small sample, with lead data kept under `data/tz_check/` (git-ignored):

- LeadSquared call activities (events 21 and 22) from a few 2-minute windows on 5 to 7 Oct: 183 for the
  date-filter check, 430 for calls edited on a later day, 244 for the start-time fields.
- 1 transcript API request (10 numbers from answered 2-minute-plus calls at 19:00 IST on 5 Oct).
- 4 leads, to compare their date fields with their call log.

## The issues

**1. LeadSquared picks calls for a day by when they were last edited, not when they started.**
The activity API's date filter is on `ModifiedOn`. In the sample, all 183 calls from three windows had
`ModifiedOn` inside the window, but 58 (32%) had `CreatedOn` (the call start) outside it.
`map_calls` (`analytics/team_performance.py:79`) then keeps only calls that started on the day. So:
- a call edited on a later day (notes or disposition added the next morning) is fetched with the later
  day and dropped there as "started on another day", and is never fetched for its own day. The 5 Oct report
  removed 669 calls that went the other way (started earlier, edited on the 5th); the 5 Oct calls edited
  later are missing from its 26,737 and are likely of the same order (estimate, not measured);
- a call that starts before midnight IST and ends after it is lost from both days (none were seen in the
  sample: no calls between 23:30 and 23:59 IST on 5 Oct);
- re-running a past day can give a different count, because a call edited after the first run moves out.

`scripts/fetch_all_calls.py`, `fetch_user_calls.py` and `fetch_team_data.py` don't re-check the start at
all, so they file old calls under the day they were edited. Zipteams notes and payments are fetched the
same way, so a note or payment edited after the day is missed.

**2. Transcript API times come in three conventions, and the client only recognises two.**
Every time ends in `Z` (UTC). The 10 recordings that matched a call in the sample were:

| What the API sent | Recordings | Current handling |
|---|---|---|
| Real UTC (2 Acefone, 1 S3 `/audio/`) | 3 | Read correctly |
| IST clock time labelled UTC | 4 | Detected as IST by `detect_call_timezone` (`integrations/transcripts/client.py:79`) |
| UTC minus a further 5 h 30 m (converted twice) | 3 | Not detected: read as 5 h 30 m earlier than the call |

The detector also guesses: an IST record whose transcript was made more than 5 h 30 m after the call is
read as UTC and lands 5 h 30 m late, which moves any call after 18:30 IST to the next day. Its answer
also depends on the time the script runs (`now`).
Only `analytics/call_integrity.py:143` compensates (it accepts a ±5 h 30 m match when durations agree).
`team_performance.fetch_transcripts` picks transcripts by `start_time`'s IST date (`:251`) with no such
check, so late-evening calls can drop out of the report's transcript sample, or the next day's calls can
come in.

**3. Each call carries three start times in two zones and three formats, none labelled.**
On all 244 calls: `CreatedOn` is UTC; the note's `StartTime` is the same UTC time written as
`10/5/2026 2:29:02 PM`; the `SourceData` `StartTime` is IST, written as `2026-10-05 19:59:18` (204 calls)
or `10/5/2026, 7:59:02 PM` (40 calls). The code uses `CreatedOn` today, which is right, but nothing stops
the next script from reading a `StartTime` and being 5 h 30 m off.

**Smaller loose ends**
- The report header's "prepared" date uses the machine clock (`analytics/team_performance_html.py:197`),
  which is UTC in the cloud, so a report built between 00:00 and 05:30 IST shows yesterday's date.
- IST is defined separately in 7 files, and `utc()` (`analytics/team_report.py:75`) silently returns
  nothing for a time it can't read, so such a call is counted as "started on another day".

**Checked and fine:** lead date fields are UTC like the call log (`mx_Last_Called_Date_DT` was 0 to 10 minutes
after the last call's `CreatedOn` on all 4 leads), all report times are converted to IST, and the
nightly plan picks "tomorrow" in IST.

## The 3 fixes to implement

**Fix 1. Fetch by edit time with a margin, keep by start time (closes issue 1).**
One shared fetch for "calls that started on these IST days": query `ModifiedOn` from the day's start to
the day's end plus a margin (default 3 days, capped at now), keep calls whose `CreatedOn` falls in the
IST day, and drop duplicates by activity ID. Use it in `fetch_report_day` and the three bulk fetchers;
fetch Zipteams notes and payments with the same margin. Add a validation line: "N calls of this day were
edited later and recovered", and say in Method that calls edited more than 3 days later may be missing.
Impact: the day's call count becomes complete and stable on re-runs. Cost: about 3 more days of pages
per fetch (read-only, no transcript budget).

**Fix 2. Time each transcript by its LeadSquared call, not by its own clock (closes issue 2).**
Move call_integrity's matching into the transcripts client as one shared step: for each transcript,
find the call on the same number whose duration agrees and whose start is within 10 minutes at a shift
of 0, +5 h 30 m or −5 h 30 m; take the time from LeadSquared and record which shift matched. Use it in
`team_performance`, `coaching` and `call_integrity`. A transcript with no matching call is counted as
"time unknown" rather than placed on a guessed day. Add the counts (matched at 0 / +5:30 / −5:30 /
unmatched) to the validation summary.
Impact: every transcript sits on the right call and the right day, whatever convention its source uses.

**Fix 3. One time module and tests that guard it (closes issue 3 and the loose ends).**
A single `IST`, a strict UTC parser that counts unreadable times instead of hiding them, `ist_day()`,
an IST day window and `now_ist()`; use them everywhere, including the report header date. Write down in
CLAUDE.md that `CreatedOn` is the only call start and what each `StartTime` field really is. Add tests:
a call at 23:58 IST edited at 00:03 IST, a call edited two days later, each transcript convention, and a
check that fails if a module defines its own IST or calls `datetime.now()` without a zone.
Impact: the next report or script can't reintroduce a 5 h 30 m error unnoticed.

Order: Fix 1 first (it changes the call counts every report uses), then Fix 2, then Fix 3. All three are
Python-only. The dashboard Worker fetches by the same window and keeps by `CreatedOn` too
(`dashboard/src/tasks.ts:79`), so it likely has issue 1; it is out of scope and left alone.
