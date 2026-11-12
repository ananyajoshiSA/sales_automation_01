"""The Bootcamp pool sheet's MIS tab ("Main MIS Teamwise Split - Nov26"): price, recovered, pending and dropped per
bootcamp and team, as the Wednesday check and Friday review use them (``pool`` in their config).

The MIS rows name a bootcamp in full ("Bootcamp | Technology Law - October 24, October 25, October 26, 2026") and
the side in its first column ("Mayur", "Anmol"); the config names it short ("Technology Law 24 Oct"). A config
bootcamp is refreshed only when exactly one MIS row of its side, dated the same day, carries its course; anything
else keeps the config's figures and is named in the notes. Prices and the sheet's enrolled lists stay as the config
has them (the MIS has copied a wrong price at least once, 4 Nov 2026).
"""

from __future__ import annotations

import re
from datetime import datetime

import openpyxl

from analytics.bootcamp_collections import course_family
from analytics.collections.sheets import money, norm_header, text
from analytics.collections.tags import tag_date

PREFIX = "main mis teamwise split"
FIELDS = {  # config field -> MIS header
    "all_teams": "total enrollments",
    "recovered": "total recovered enrollments",
    "pending": "total pending leads",
    "dropped": "drop leads",
    "recovered_amount": "recovered amount (total)",
    "pending_amount": "pending amount (total)",
    "dropped_amount": "dropped amount",
}
COUNTS = ("all_teams", "recovered", "pending", "dropped")
PRICE = re.compile(r"day\s*1\s*-\s*([\d,]+).*?day\s*2\s*-\s*([\d,]+)", re.I | re.S)
TITLE = re.compile(r"bootcamp pool mis\s*-\s*(\d{1,2})-([a-z]{3})[a-z]*-(\d{4})", re.I)
SHORT = re.compile(r"^(.*\S)\s+(\d{1,2} [A-Z][a-z]{2})$")


def mis_tab(names: list[str], month: str) -> str | None:
    """The MIS tab for ``month`` ("November 2026"): tab names are cut short ("- Nov26", "- June2", "- Augus")."""
    mon, year = month.split()[0][:3].lower(), month.split()[-1][-2:]
    hits = [n for n in names if norm_header(n).startswith(PREFIX) and mon in norm_header(n)[len(PREFIX):]]
    return next((n for n in hits if year in norm_header(n)[len(PREFIX):]), hits[0] if hits else None)


def course_of(name: str) -> str:
    """"Technology Law" from "Bootcamp | Technology Law - October 24, ...", keeping "| Senior Professionals" parts."""
    s = re.sub(r"^\s*bootcamp\s*\|\s*", "", name, flags=re.I)
    return re.split(r"\s+-\s+", s, maxsplit=1)[0].strip()


def read_mis(rows, month: str = "") -> tuple[str, list[dict]]:
    """(as-of date "3 Nov 2026", one dict per bootcamp row) from the MIS tab's cell rows; total rows are skipped."""
    as_of, head, out = "", None, []
    for row in rows:
        cells = [text(c) for c in row]
        if not as_of and (m := next((TITLE.search(c) for c in cells if TITLE.search(c)), None)):
            as_of = datetime.strptime(f"{m.group(1)} {m.group(2).title()} {m.group(3)}", "%d %b %Y").strftime("%-d %b %Y")
        if head is None:
            names = [norm_header(c) for c in cells]
            if "bootcamp name" in names:
                head = {h: i for i, h in reversed(list(enumerate(names))) if h}
                name_col = names.index("bootcamp name")
            continue
        side, name = cells[name_col - 1] if name_col else "", cells[name_col]
        if not side or not name:
            continue
        price = PRICE.search(cells[head["price"]]) if "price" in head else None
        r = {"side": side, "name": name, "course": course_of(name), "day": tag_date(name),
             "price": [int(price.group(1).replace(",", "")), int(price.group(2).replace(",", ""))] if price else None}
        for key, h in FIELDS.items():
            v = money(row[head[h]]) if h in head else 0.0
            r[key] = int(round(v)) if key in COUNTS else round(v, 2)
        out.append(r)
    if head is None:
        raise ValueError(f"no 'Bootcamp Name' header in the MIS tab{f' for {month}' if month else ''}")
    return as_of, out


def load_mis(path: str, month: str) -> tuple[str, str, list[dict]]:
    """(tab name, as-of date, rows) from a downloaded pool sheet."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        tab = mis_tab(wb.sheetnames, month)
        if not tab:
            raise ValueError(f"the pool sheet has no 'Main MIS Teamwise Split' tab for {month}")
        as_of, rows = read_mis(wb[tab].iter_rows(values_only=True), month)
    finally:
        wb.close()
    return tab, as_of, rows


def same_course(family: str, course: str) -> bool:
    if course_family(course).lower() == family.lower():
        return True
    words = set(re.findall(r"[a-z0-9&]+", course.lower()))
    return all(w in words for w in re.findall(r"[a-z0-9&]+", family.lower()))


def load_students(path: str, month: str, side: str) -> dict[str, dict]:
    """The pool sheet's students of ``month`` ("November 2026") on ``side``, by the last 10 digits of their phone.

    The MIS "Recovered amount (Total)" is every rupee received on the month's students, booking fees of pending and
    dropped leads included (checked 12 Nov 2026: the Mastersheet's "Money received" adds up to it), so per-lead money
    comes from here, not from an estimate.
    """
    from analytics.collections.sheets import read_bootcamp
    key = datetime.strptime(month, "%B %Y").strftime("%Y-%m")
    rows, _ = read_bootcamp(path)
    out = {}
    for r in rows:
        if r["month"] == key and r["phone"] and (r.get("vertical") or "").lower() and (r["vertical"] or "").lower() in side.lower():
            out[r["phone"]] = {"name": r["name"], "status": r["status"], "outcome": r["outcome"], "received": r["received"] or 0.0,
                               "balance": r["balance"] or 0.0, "price": r["price"] or 0.0}
    return out


def refresh_pool(pool: dict, mis: list[dict], as_of: str, side: str) -> list[str]:
    """Update ``pool`` in place from the MIS rows of ``side`` ("Mayur", "Puja / Anmol"); returns notes for people."""
    mine = [r for r in mis if r["side"].lower() in side.lower()]
    used, notes = set(), []
    for key, b in pool["bootcamps"].items():
        m = SHORT.match(key)
        family, day = (m.group(1), m.group(2)) if m else (key, "")
        on_day = [r for r in mine if r["day"] and datetime.strptime(r["day"], "%Y-%m-%d").strftime("%-d %b") == day]
        hits = [r for r in on_day if same_course(family, r["course"])]
        if len(hits) > 1:
            hits = [r for r in hits if course_family(r["course"]).lower() == family.lower()]
        if len(hits) != 1:
            notes.append(f"{key}: kept the earlier MIS figures ({len(hits) or 'no'} MIS rows match)")
            continue
        r = hits[0]
        used.add(id(r))
        b.update({k: r[k] for k in FIELDS})
        if not b.get("price") and r["price"]:
            b["price"] = r["price"]
    notes += [f"In the MIS but not in this report: {r['name']}" for r in mine if id(r) not in used]
    if as_of:
        pool["as_of"] = as_of
    return notes
