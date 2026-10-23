---
name: funnel-icp-report
description: Leads and sales per ICP for any bootcamp campaign, from GrowthX funnels and the enrolment sheet. Use when someone asks "ICP report for <bootcamp> <month>", "which ICP converted", "leads and sales per ICP/funnel", or "which ads brought teachers/retired/etc."
---

# Funnel → ICP report

Gives, for one bootcamp campaign: leads, sales, conversion and money received per ICP, plus the same per
funnel. Code: `analytics/funnel_icp.py`. ICP maps: `analytics/funnel_icp_maps/<bootcamp>.json`.

## What to ask the person (only if missing)
1. Which bootcamp and campaign (e.g. "US Accounting, 14–16 Nov").
2. The enrolment sheet link, if it is not the usual one (Drive id `1f_QkbW58PbyBhNfqxl2dqxGLctSBWrV8-rLHhs73rIs`).

## Steps
1. **Find the GrowthX leadtype.** It is named per campaign (`independent_director_oct`, `accounting_oct`).
   If unsure, fetch a day of leads with `GrowthXClient().leads(day, day)` and list `leadtype` values.
2. **Pick or create the map.** `independent_director.json` and `us_accounting.json` exist. For a new
   bootcamp, create `<bootcamp>.json` with `icps` copied from that bootcamp's TimePay discovery agent
   prompt, plus `General`. Use only ICPs the agent uses; add a non-agent ICP (like "Teachers / educators")
   only when funnels target it on purpose or the person asks.
3. **List unmapped pages:**
   `python -m analytics.funnel_icp pages <bootcamp> <leadtype> data/funnel_icp`
4. **Read every unmapped page yourself** (`page_text` in the output; open the URL if the text is thin) and
   add `"<slug>": "<ICP>"` to the map's `pages`. Map by **who the page's headline targets**, not the funnel
   name. Rules used so far:
   - "Retired X" → Retired, except retired educators → Teachers, retired armed forces → Defence,
     retired women professionals → Senior women.
   - Working professionals with 10–15+ years in corporate (including doctors, HR, CFO, CS, CA, auditors) →
     Corporate. CEOs/CXOs → General (the ID agent's corporate ICP excludes CXO titles).
   - "You didn't have to run the company" (VP/GM pitch) → Corporate; "ran a business 25+ years",
     founders → Business owners.
   - US Acc: anyone in an accounts job now (payroll, AP/AR, Tally, evenings free, day job, WFH ended, small
     town accountant) → Employed; CA/accountant moms → Women restarting; CS / tax consultants → Tax.
   - Brand search, generic "from home", pay chart pages → General.
   Tell the person which pages were ambiguous and how you mapped them.
5. **Get the sales sheet:** download the enrolment sheet as xlsx with the Google Drive connector into
   `data/` (never commit it). Its bootcamp tab is in the map's `sales_tab_hint`, or ask.
6. **Run the report:**
   `python -m analytics.funnel_icp report <bootcamp> <leadtype> data/enr.xlsx "<tab>" exports [--from YYYY-MM-DD --to YYYY-MM-DD]`
   It stops if any page is still unmapped. Output: `exports/ICP_funnel_report_<leadtype>.xlsx`.
7. **Commit the updated map** (it holds no lead data) so the next run starts from it.

## How to present it
Lead with the table per ICP (leads, sales, conversion, money received), then the 2–3 funnels driving the
best and worst ICP. Say how many sales did not match a GrowthX lead. Leave out ICPs with no leads.
Sales come from the sheet's status (Disbursed, One shot, Installments, Booking fees received); say so.
