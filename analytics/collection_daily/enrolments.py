"""Enrolments of Mayur's team (Elite Changemakers) from their live enrolment sheet, not from LeadSquared.

The user, 26 Nov 2026: LeadSquared's Course Enrolled stage is updated late for this team, while the team's own sheet
(one row per payment, bootcamp and community) is live; Anmol's team keeps LeadSquared. A payment in the sheet is a
"Bootcamp collections" or "Community Collections" balance-payment row (an instalment, a loan amount received or a
one-shot payment); booking fees and new sales are not enrolments.

A Mayur lead is enrolled when the sheet has its payment or LeadSquared shows it enrolled, from the earlier of the two.
Checked on 25 Nov data: the sheet had 18 enrolments LeadSquared did not yet show (paid 17–25 Nov), and missed some
LeadSquared showed that the pool sheet confirms paid (a one shot of 29 Oct, a loan disbursed on 19 Nov), so neither
source alone is complete. Leads on which they disagree are kept for the reports to show.

The sheet is downloaded tab by tab (``download``); ``apply`` replaces ``enrolled`` / ``enrolled_at`` on the Mayur leads
and records where the sheet and LeadSquared disagree, so the reports can say so.

    python -m analytics.collection_daily.enrolments <run_dir>      # downloads the November and October tabs
"""

from __future__ import annotations

import csv
import io
import os
import re
import sys
from datetime import date, datetime

import requests

from analytics.collections.tags import tag_date
from integrations.timeutil import IST

SHEET_ENV = "MAYUR_ENROLMENT_SHEET_ID"  # the sheet's ID, in .env or the environment settings (shared by link: never in code)
TABS = {"2026-11": 435335311}  # the tab the user linked ("Changemaker", November)
OCT_TAB = "Changemakers_oct"  # read from the whole-sheet download: October's payments of the 24 and 31 Oct bootcamps
SIDE = "Mayur"
COLLECTION = re.compile(r"^(bootcamp|community) collections - balance payments", re.I)
NOT_ENROLMENT = re.compile(r"booking fee", re.I)


def phone10(v) -> str:
    d = re.sub(r"\D", "", str(v or "").split(".")[0])
    return d[-10:] if len(d) >= 10 else ""


def parse_day(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v).strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def payments(rows: list[dict]) -> list[dict]:
    """Collection balance payments: {phone, day, course, kind, amount, caller}, booking fees and new sales left out."""
    out = []
    for r in rows:
        kind = (r.get("kind") or "").strip()
        if not COLLECTION.match(r.get("enrollment") or "") or NOT_ENROLMENT.search(kind):
            continue
        p, d = phone10(r.get("phone")), parse_day(r.get("date"))
        if p and d:
            out.append({"phone": p, "day": d, "course": r.get("course") or "", "enrollment": r.get("enrollment") or "", "kind": kind,
                        "amount": r.get("amount"),
                        "caller": (r.get("caller") or "").strip()})
    return out


def read_csv(text: str) -> list[dict]:
    """The November tab as exported to CSV."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        out.append({"date": r.get("Date"), "caller": r.get("Couseller Name"), "phone": r.get("Contact No"), "course": r.get("Course name"),
                    "enrollment": r.get("Enrollment"), "kind": r.get("Financial status (LawSikho/Bajaj/Credence/Eduvanz)"),
                    "amount": r.get("Revenue Collected")})
    return out


def read_oct(path: str) -> list[dict]:
    """The October tab of the whole-sheet .xlsx (different columns from November's)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if OCT_TAB not in wb.sheetnames:
            return []
        it = wb[OCT_TAB].iter_rows(values_only=True)
        head = [str(h or "").strip() for h in next(it)]
        col = {h: i for i, h in enumerate(head)}
        out = []
        for r in it:
            g = lambda h: r[col[h]] if h in col and col[h] < len(r) else None
            out.append({"date": g("Date"), "caller": g("Caller name         (for ops) for enrollment link circulation") or g(head[15] if len(head) > 15 else ""),
                        "phone": g("Contact No"), "course": g("Course"), "enrollment": g("Enrollment"),
                        "kind": g("Full/Installment (Welcome callers MIS)"), "amount": g("Fee paid")})
        return out
    finally:
        wb.close()


def download(run_dir: str) -> str:
    d = os.path.join(run_dir, "enrol")
    os.makedirs(d, exist_ok=True)
    from integrations.gsheets.client import download_xlsx, sheet_id
    sheet = sheet_id(SHEET_ENV)
    download_xlsx(sheet, os.path.join(d, "mayur_enrolments.xlsx"))
    for month, gid in TABS.items():
        r = requests.get(f"https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid={gid}", timeout=120)
        r.raise_for_status()
        with open(os.path.join(d, f"changemaker_{month}.csv"), "wb") as fh:
            fh.write(r.content)
    return d


def load(run_dir: str) -> list[dict] | None:
    """Every collection balance payment in the downloaded sheet, or None when the sheet was not downloaded."""
    d = os.path.join(run_dir, "enrol")
    if not os.path.isdir(d):
        return None
    rows = []
    for month in TABS:
        p = os.path.join(d, f"changemaker_{month}.csv")
        if os.path.exists(p):
            rows += read_csv(open(p, encoding="utf-8").read())
    x = os.path.join(d, "mayur_enrolments.xlsx")
    if os.path.exists(x):
        rows += read_oct(x)
    return payments(rows)


def matches(lead: dict, pay: dict) -> bool:
    """Same person and the same programme: a bootcamp lead's payment names its bootcamp's start day; a community lead's
    payment is a community one."""
    if lead.get("community"):
        return pay["course"].lower().startswith("community")
    if not pay["course"].lower().startswith("bootcamp"):
        return pay["enrollment"].lower().startswith("bootcamp")  # moved to another course and paid for it there
    return tag_date(pay["course"]) == lead.get("bootcamp_day")


def apply(leads: list[dict], pays: list[dict], cut: datetime) -> dict:
    """Set enrolled / enrolled_at on Mayur's leads from the sheet and LeadSquared (first matching payment, read as noon
    IST of its day and never after ``cut``, or LeadSquared's enrolment, whichever is earlier). Each lead keeps
    ``enrolled_sheet`` and ``enrolled_lsq``; returns where the two disagree, lead ids by direction."""
    by: dict[str, list[dict]] = {}
    for p in pays:
        by.setdefault(p["phone"], []).append(p)
    diff = {"sheet_only": [], "lsq_only": []}
    for l in leads:
        if l["side"] != SIDE:
            continue
        mine = [p for p in by.get(phone10(l.get("phone")), []) if matches(l, p)]
        first = min((p["day"] for p in mine), default=None)
        at = min(datetime(first.year, first.month, first.day, 12, tzinfo=IST), cut) if first else None
        lsq, lsq_at = bool(l.get("enrolled")), l.get("enrolled_at")
        if at and not lsq:
            diff["sheet_only"].append(l["lead_id"])
        if lsq and not at:
            diff["lsq_only"].append(l["lead_id"])
        l["enrolled_sheet"], l["enrolled_lsq"] = bool(at), lsq
        when = [t for t in (at, lsq_at if lsq else None) if t]
        l["enrolled"], l["enrolled_at"] = bool(when), min(when) if when else None
        if when:
            l["dropped"] = False
    return diff


if __name__ == "__main__":
    print(download(sys.argv[1]))
