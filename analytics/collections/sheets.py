"""Read the two collection sheets into one row per student enrolment, with the same fields for both.

Community sheet: one tab per conversion month ("August 2026"; some months hidden) plus combined tabs
("Combined_Sheet_Aquib", "Combined", "old Aquib") that repeat those rows. For each month the month's own tab is used;
a month with no tab of its own (May to July 2026 on 2 Nov) comes from the combined tab holding most rows for it.
So no student is counted twice and a new month's tab is picked up as soon as it appears. Summary tabs (MIS, pivots,
snapshots) have no phone column and are skipped.

Bootcamp sheet: every student is a row of "Mastersheet"; the other tabs are pivots and MIS built from it.

Amounts are rupees. ``received`` is the money that reached us (after loan subvention), ``balance`` what is left.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime

import openpyxl

from analytics.collections.tags import MONTHS, tag_date

ALIASES = {
    "name": ("name", "student name"),
    "email": ("email id", "email"),
    "phone": ("contact no", "phone no.", "phone no", "phone"),
    "date": ("date", "dates", "date of conversion"),
    "community": ("community name",),
    "course": ("course paid for",),
    "price": ("course price r.s", "course price"),
    "received": ("amount paid", "money received"),
    "subvention": ("subvention", "subvention cost"),
    "discount": ("discount",),
    "balance": ("pending revenue", "balance due", "pending balance"),
    "status": ("pending status", "status of the lead"),
    "stage": ("lead stage", "status of the lead level 1"),
    "stage2": ("status of the lead level 2",),
    "owner": ("owner", "elite changemakers", "balance collectors"),
    "team": ("revised team", "tls name", "team"),
    "vertical": ("verticals", "vertical head"),
    "payment_mode": ("enrollment stage", "financial status (lawsikho/bajaj/credence/eduvanz)", "mode of payment l.t"),
    "comment": ("comments", "counselor comments level 2", "counselor comments level 1"),
    "month_text": ("month",),
    "start": ("start date",),
    "bootcamp": ("bootcamp name",),
    "expected_payment": ("expected payment date",),
}
MONTH_TAB = re.compile(r"^\s*([a-z]+)\s+(20\d\d)\s*$", re.I)


def norm_header(h) -> str:
    return re.sub(r"\s+", " ", str(h or "").replace("\n", " ")).strip().lower()


def phone10(v) -> str:
    """Last 10 digits of a phone number ("9030369779.0", "+91-9876543210", 918105863313); "" if fewer."""
    s = re.sub(r"\.0+$", "", str(v or "").strip())
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else ""


def email_key(v) -> str:
    s = str(v or "").strip().lower()
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else ""


def money(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v or ""))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def text(v) -> str:
    s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    return "" if s in ("#N/A", "#REF!", "None", "--", "#VALUE!") else s


def month_of(v) -> str:
    """YYYY-MM from a date cell or text ("2026-08-01", "April '2026", "August 2026"); "" if unreadable."""
    if isinstance(v, (datetime, date)):
        return f"{v.year:04d}-{v.month:02d}"
    s = text(v)
    if m := re.match(r"(20\d\d)-(\d\d)", s):
        return f"{m.group(1)}-{m.group(2)}"
    if m := re.match(r"([a-z]+)\W*(20\d\d)", s, re.I):
        mon = MONTHS.get(m.group(1)[:3].lower())
        return f"{m.group(2)}-{mon:02d}" if mon else ""
    if m := re.match(r"(\d{1,2})/(\d{1,2})/(20\d\d)", s):
        return f"{m.group(3)}-{int(m.group(1)):02d}"
    return ""


def outcome(status: str) -> str:
    """The sheet's own status in five groups: Collected, Instalments, Pending, Dropped, Not stated (or Other)."""
    s = status.lower()
    if not s:
        return "Not stated"
    if "drop" in s:
        return "Dropped"
    if "disburs" in s or "one shot" in s or "oneshot" in s:
        return "Collected"
    if "install" in s or "instal" in s:
        return "Instalments"
    if "pending" in s or "booking" in s:
        return "Pending"
    return "Other"


def _header(rows: list[tuple]) -> tuple[int, dict[str, int]] | None:
    """The header row (within the first five) and the column of each field; None for a tab with no student rows."""
    for i, r in enumerate(rows[:5]):
        cols = {}
        heads = [norm_header(c) for c in r]
        for field, names in ALIASES.items():
            for n in names:
                if n in heads:
                    cols[field] = heads.index(n)
                    break
        if "phone" in cols and "price" in cols and ("name" in cols or "email" in cols):
            return i, cols
    return None


def _row(r: tuple, cols: dict[str, int], **extra) -> dict:
    get = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
    status = text(get("status"))
    stage = text(get("stage2")) or text(get("stage"))
    return {
        "name": text(get("name")), "phone": phone10(get("phone")), "email": email_key(get("email")),
        "community_raw": text(get("community")), "course": text(get("course")),
        "bootcamp_raw": text(get("bootcamp")), "start": text(get("start")),
        "price": money(get("price")), "received": money(get("received")), "subvention": money(get("subvention")),
        "discount": money(get("discount")), "balance": money(get("balance")),
        "status": status, "outcome": outcome(status), "sheet_stage": stage,
        "owner": text(get("owner")), "team": text(get("team")), "vertical": text(get("vertical")),
        "payment_mode": text(get("payment_mode")), "comment": text(get("comment"))[:300],
        "expected_payment": text(get("expected_payment"))[:20],
        "date": month_of(get("date")) and str(get("date"))[:10], **extra,
    }


def _tabs(path: str):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for name in wb.sheetnames:
        ws = wb[name]
        rows = [r for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
        yield name, ws.sheet_state, rows
    wb.close()


def _is_student(r: dict) -> bool:
    return bool(r["phone"] or r["email"] or r["name"])


def read_community(path: str) -> tuple[list[dict], dict]:
    """Every community enrolment once; plus a note of which tab each month came from and what was skipped."""
    by_tab: dict[str, list[dict]] = {}
    monthly: dict[str, str] = {}
    skipped = Counter()
    for name, state, rows in _tabs(path):
        found = _header(rows)
        if not found:
            continue
        hi, cols = found
        m = MONTH_TAB.match(name)
        tab_month = month_of(f"{m.group(1)} {m.group(2)}") if m else ""
        if tab_month:
            monthly[tab_month] = name
        out = []
        for r in rows[hi + 1:]:
            row = _row(r, cols, source="community", tab=name, hidden=state != "visible")
            if not _is_student(row):
                skipped["blank rows"] += 1
                continue
            month = tab_month or (month_of(r[cols["date"]]) if "date" in cols else "") \
                or (month_of(r[cols["month_text"]]) if "month_text" in cols else "")
            row["month"] = month
            out.append(row)
        by_tab[name] = out
    chosen: list[dict] = []
    source_of_month: dict[str, str] = {}
    months = {r["month"] for rs in by_tab.values() for r in rs if r["month"]}
    for month in sorted(months):
        tab = monthly.get(month) or max(
            (t for t in by_tab if t not in monthly.values()),
            key=lambda t: sum(1 for r in by_tab[t] if r["month"] == month))
        source_of_month[month] = tab
        chosen += [r for r in by_tab[tab] if r["month"] == month]
    seen = {r["phone"] or r["email"] for r in chosen}
    for tab, rs in by_tab.items():
        for r in rs:
            if r["month"] and source_of_month.get(r["month"]) != tab:
                skipped[f"repeated in {tab}"] += 1
            elif not r["month"]:
                key = r["phone"] or r["email"]
                if key and key not in seen:
                    r["month"] = "unknown"
                    chosen.append(r)
                    seen.add(key)
                else:
                    skipped[f"no date, already listed ({tab})"] += 1
    return chosen, {"month_sources": source_of_month, "skipped": dict(skipped),
                    "tabs_read": sorted(by_tab), "rows": len(chosen)}


def read_bootcamp(path: str, tab: str = "Mastersheet") -> tuple[list[dict], dict]:
    for name, state, rows in _tabs(path):
        if name != tab:
            continue
        found = _header(rows)
        if not found:
            raise ValueError(f"'{tab}' has no header row with phone and course price")
        hi, cols = found
        out, skipped = [], Counter()
        for r in rows[hi + 1:]:
            row = _row(r, cols, source="bootcamp", tab=name, hidden=state != "visible")
            if not (row["phone"] or row["email"] or row["name"]):
                skipped["rows with no student (bootcamp and dates only)"] += 1
                continue
            row["month"] = month_of(r[cols["month_text"]]) if "month_text" in cols else ""
            row["month"] = row["month"] or "unknown"
            row["start_date"] = tag_date(row["start"]) or tag_date(row["bootcamp_raw"])
            out.append(row)
        return out, {"skipped": dict(skipped), "tabs_read": [tab], "rows": len(out)}
    raise ValueError(f"the bootcamp sheet has no '{tab}' tab")


def months_count(rows: list[dict]) -> dict[str, int]:
    c = defaultdict(int)
    for r in rows:
        c[r["month"]] += 1
    return dict(sorted(c.items()))
