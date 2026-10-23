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
   For every page mapped to General, also add `"<slug>": "<who the page targets>"` to `general_segments`
   (e.g. "Brand search", "Social-sector professionals"). Never add a new ICP for these.
   Tell the person which pages were ambiguous and how you mapped them.
5. **Get the sales sheet:** download the enrolment sheet as xlsx with the Google Drive connector into
   `data/` (never commit it). Its bootcamp tab is in the map's `sales_tab_hint`, or ask.
6. **Find the TimePay discovery campaign id(s)** for this batch (`TimePayClient().get('campaigns')` or the
   TimePay `list_campaigns` tool; read-only). Main discovery campaign only, not the Re or reminder ones,
   unless the person asks.
7. **Run the report:**
   `python -m analytics.funnel_icp report <bootcamp> <leadtype> data/enr.xlsx "<tab>" exports --timepay <id>[,<id>] --tp-from 2026-MM-DDT00:00:00 [--tp-to ..] [--from YYYY-MM-DD --to YYYY-MM-DD]`
   Output: `exports/ICP_funnel_report_<leadtype>.xlsx` with tabs By ICP (General split by segment),
   By funnel, Checks and Spot check.
8. **Double checks before sharing (all required):**
   - Exit code 0 and no FAIL in the Checks tab. A FAIL means fix and re-run, never share.
   - Read every WARN. Email-only matches with a different sheet phone: open those sheet rows and confirm
     it is the same person.
   - Hand-check the 20 Spot check rows: find each in the sheet (status) and in GrowthX (first funnel).
     Report how many you checked and any that were wrong; if any are wrong, find the cause and re-run.
   - Totals: sales matched + "not in GrowthX" must equal the sheet's sale rows for that tab.
9. **Commit the updated map** (it holds no lead data) so the next run starts from it.

## How to present it
Lead with the table per ICP (leads, TimePay connected, positive % of connected, sales, lead→sale %,
money received), General split by segment. Say how fresh the data is and the check results. Then the 2–3 funnels driving the
best and worst ICP. Say how many sales did not match a GrowthX lead. Leave out ICPs with no leads.
Sales come from the sheet's status (Disbursed, One shot, Installments, Booking fees received); say so.
