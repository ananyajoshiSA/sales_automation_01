"""Two-week review of collection callers (asked 26 Nov 2026): who keeps missing follow-ups, who does not call back
leads whose calls we missed, and who dials too little on collection leads. Each measure is taken per week, so
"consistently" means in both weeks, not one bad day.

Measures, per caller (the lead's current owner for follow-ups and missed calls; the person who dialled for dials):
- dials on collection leads per day worked (a day with at least one dial), against 70% of the team's median
  (the lagging rule in CLAUDE.md); days under 20 dials (a working day is 20+ dials);
- follow-ups: call-backs saved after a talk that fell due in the week, and how many were kept (a dial from 15 minutes
  before to 2 hours after); a caller who saves none after many real talks is shown too;
- missed calls: a lead's own call we did not answer, and whether anyone called the lead back the same day.
A caller with half or more of their dials ending in CallFailure has a dialer problem and is not judged on dials.

    python -m analytics.collection_daily.caller_trend <run_dir> <first day YYYY-MM-DD> <last day YYYY-MM-DD> <out.xlsx>
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from analytics.collection_daily import facts
from analytics.collection_daily.facts import SIDES
from integrations.timeutil import IST

LOW_DIALS = 20          # a working day is 20+ dials (CLAUDE.md)
LAG = 0.7               # below 70% of the team median (CLAUDE.md)
MIN_DUE, KEPT_BAR = 5, 0.5
MIN_MISSED, RETURN_BAR = 5, 0.5
MIN_TALKS, SAVE_BAR = 20, 0.1


def day_of(t: datetime) -> date:
    return t.astimezone(IST).date()


def week_stats(leads: list[dict], first: date, last: date) -> dict[str, Counter]:
    """caller -> counts between ``first`` and ``last`` (IST days, inclusive) on these leads."""
    callers = {l["caller"] for l in leads}
    out: dict[str, Counter] = {c: Counter() for c in callers}
    days: dict[str, set] = defaultdict(set)
    per_day: dict[tuple, int] = Counter()
    inside = lambda t: first <= day_of(t) <= last
    for l in leads:
        for c in l["calls"]:
            if not inside(c["t"]) or c["by"] not in callers:
                continue
            s = out[c["by"]]
            if c["out"]:
                s["dials"] += 1
                s["failed"] += (c.get("status") or "") == "CallFailure"
                days[c["by"]].add(day_of(c["t"]))
                per_day[(c["by"], day_of(c["t"]))] += 1
            if c["answered"] and c["out"]:
                s["answered"] += 1
            if c["answered"] and c["dur"] >= facts.REAL_SECS:
                s["real"] += 1
        s = out[l["caller"]]
        for f in l["follow_ups"]:
            if inside(f["set_at"]):
                s["fu_saved"] += 1
            if inside(f["due"]) and f["kept"] is not None:
                s["fu_due"] += 1
                s["fu_kept"] += bool(f["kept"])
        for c in l["calls"]:
            if c["out"] or c["answered"] or not inside(c["t"]):
                continue
            s["missed"] += 1
            s["returned"] += any(d["out"] and d["t"] > c["t"] and day_of(d["t"]) == day_of(c["t"]) for d in l["calls"])
    for c, s in out.items():
        s["days"] = len(days[c])
        s["low_days"] = sum(1 for d in days[c] if per_day[(c, d)] < LOW_DIALS)
    return out


def flags(s: Counter, median: float) -> list[str]:
    """Plain-language reasons a caller falls short in one week (empty when none)."""
    if s["dials"] and s["failed"] * 2 >= s["dials"]:
        return ["dialer problem: half or more of the calls failed"]
    out = []
    per_day = s["dials"] / s["days"] if s["days"] else 0
    if s["days"] and median and per_day < LAG * median:
        out.append(f"dials {per_day:.0f} a day against a team median of {median:.0f}")
    if s["fu_due"] >= MIN_DUE and s["fu_kept"] < KEPT_BAR * s["fu_due"]:
        out.append(f"kept {s['fu_kept']} of {s['fu_due']} follow-ups")
    if s["real"] >= MIN_TALKS and s["fu_saved"] < SAVE_BAR * s["real"]:
        out.append(f"saved {s['fu_saved']} call-backs after {s['real']} real talks")
    if s["missed"] >= MIN_MISSED and s["returned"] < RETURN_BAR * s["missed"]:
        out.append(f"called back {s['returned']} of {s['missed']} missed calls the same day")
    return out


def review(leads: list[dict], first: date, last: date) -> dict:
    """side -> caller -> {"tl", "weeks": [(first, last, Counter, flags)], "both": the kinds flagged in every week}."""
    weeks = []
    d = first
    while d <= last:
        weeks.append((d, min(d + timedelta(days=6), last)))
        d += timedelta(days=7)
    result = {}
    for side in SIDES:
        ls = [l for l in leads if l["side"] == side]
        tl = {l["caller"]: l["tl"] for l in ls}
        rows = {c: {"tl": tl[c], "weeks": []} for c in tl}
        for a, b in weeks:
            st = week_stats(ls, a, b)
            rates = [s["dials"] / s["days"] for s in st.values() if s["days"] and s["failed"] * 2 < s["dials"]]
            med = statistics.median(rates) if rates else 0
            for c, s in st.items():
                rows[c]["weeks"].append((a, b, s, flags(s, med), med))
        for r in rows.values():
            kinds = [{f.split(" ")[0] for f in w[3]} for w in r["weeks"]]
            r["both"] = sorted(set.intersection(*kinds)) if kinds else []
        result[side] = rows
    return result


def write(result: dict, path: str) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    names = {"dials": "Dials less", "kept": "Misses follow-ups", "saved": "Saves no call-backs", "called": "Does not call back missed calls",
             "dialer": "Dialer problem"}
    for side, rows in result.items():
        ws = wb.create_sheet(("Anmol's team" if side != "Mayur" else "Mayur's team"))
        w = next(iter(rows.values()))["weeks"]
        head = ["Caller", "TL", "Short in BOTH weeks"]
        for a, b, *_ in w:
            p = f"{a:%d %b}–{b:%d %b}: "
            head += [p + "days dialled", p + "dials a day", p + "days under 20 dials", p + "follow-ups kept", p + "call-backs saved / real talks",
                     p + "missed calls called back same day", p + "what fell short"]
        ws.append(head)
        order = sorted(rows.items(), key=lambda kv: (-len(kv[1]["both"]), kv[1]["tl"], kv[0]))
        for c, r in order:
            line = [c, r["tl"], ", ".join(names.get(k, k) for k in r["both"]) or "—"]
            for a, b, s, f, med in r["weeks"]:
                line += [s["days"], round(s["dials"] / s["days"]) if s["days"] else 0, s["low_days"], f"{s['fu_kept']} of {s['fu_due']}",
                         f"{s['fu_saved']} / {s['real']}", f"{s['returned']} of {s['missed']}", "; ".join(f) or "—"]
            ws.append(line)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E46")
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for i, col in enumerate(ws.columns, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 30 if i in (3,) or (i - 3) % 7 == 0 else 14
        ws.freeze_panes = "C2"
    wb.save(path)


if __name__ == "__main__":
    run_dir, a, b, out = sys.argv[1:5]
    cut = facts.read_at(run_dir)
    res = review(facts.collect(run_dir, cut), date.fromisoformat(a), date.fromisoformat(b))
    write(res, out)
    for side, rows in res.items():
        print(side)
        for c, r in sorted(rows.items(), key=lambda kv: (-len(kv[1]["both"]), kv[0])):
            print(f"  {c:26} {r['tl']:18} both weeks: {', '.join(r['both']) or '-'}")
            for a_, b_, s, f, med in r["weeks"]:
                print(f"      {a_:%d %b}-{b_:%d %b}: days {s['days']} dials/day {s['dials'] / s['days'] if s['days'] else 0:.0f} (median {med:.0f}) "
                      f"low {s['low_days']} fu {s['fu_kept']}/{s['fu_due']} saved {s['fu_saved']}/{s['real']} missed {s['returned']}/{s['missed']} "
                      f"failed {s['failed']}/{s['dials']} | {'; '.join(f)}")
    print(out)
