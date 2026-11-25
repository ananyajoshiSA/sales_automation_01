"""Checks run before the management report is written (the user, 25 Nov 2026: "recheck all the data, it should match
the morning's report"). Every number the report shares with this morning's team leader reports is read back from those
reports' HTML and compared; the pool sheet's lead and money totals are compared with its own MIS tab; the bootcamp table
and the money received are added up again. The report is not written when anything differs.

Used by ``python -m analytics.collection_daily.management``; not run on its own.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter

TILE = re.compile(r'<div class="tile"><b>(.*?)</b><span>(.*?)</span>', re.S)


def morning_numbers(html: str) -> dict:
    """The numbers a morning team leader report printed: its tiles, page 1 counts, scorecard and caller table."""
    out: dict = {}
    for b, span in TILE.findall(html):
        if span.startswith("open leads to call today"):
            out["open"] = int(b)
        elif span.startswith("enrolled on"):
            out["enrolled"] = int(b)
        elif span.startswith("real talks (2 min+) of dials"):
            out["real"], out["dials"] = map(int, re.match(r"(\d+) of (\d+)", b).groups())
        elif "48-hour score" in span:
            out["met48"], out["past48"] = map(int, re.search(r"(\d+) of (\d+) leads past", span).groups())
    m = re.search(r"Will pay today or already overdue \((\d+)\) · ₹([\d,]+)", html)
    out["due_n"], out["due_rs"] = (int(m[1]), float(m[2].replace(",", ""))) if m else (0, 0.0)
    m = re.search(r"Paid in full per the sheet, still open in LeadSquared \((\d+)\)", html)
    out["paid"] = int(m[1]) if m else 0
    for key, label in (("fu", "Follow-ups kept (saved call-backs due that day)"), ("in", "Missed calls from leads, not called back")):
        m = re.search(re.escape(label) + r"</td>.*?<b>(\d+) of (\d+)</b>", html, re.S)
        if m:
            out[key] = (int(m[1]), int(m[2]))
    t = html[html.find("Team performance by caller"):]
    t = t[:t.find("</table>")]
    out["callers"] = {name: (int(d), int(r)) for name, d, r in
                      re.findall(r"<tr><td>([^<]+)</td><td class=n>(\d+)</td><td class=n>\d+%</td><td class=n>(\d+)</td>", t)}
    return out


def against_morning(m, out_dir: str) -> tuple[int, list[str]]:
    """(numbers checked, differences) between the management report ``m`` and the morning's reports and sheet."""
    n, wrong = 0, []

    def same(what: str, ours, theirs) -> None:
        nonlocal n
        n += 1
        if (round(ours) if isinstance(ours, float) else ours) != (round(theirs) if isinstance(theirs, float) else theirs):
            wrong.append(f"{what}: report {ours}, morning {theirs}")

    team = m.side.split(" / ")[-1]
    for tl, t in m.by_tl.items():
        path = os.path.join(out_dir, f"Collections plan - {team}'s team - TL {tl} - {m.day:%a %d %b %Y}.html")
        if not os.path.exists(path):
            wrong.append(f"{tl}: this morning's report not found ({os.path.basename(path)})")
            continue
        got, ours = morning_numbers(open(path, encoding="utf-8").read()), m.tl_numbers(t)
        for k in ("open", "enrolled", "dials", "real", "met48", "past48", "due_n", "due_rs", "paid"):
            same(f"{tl} · {k}", ours[k], got.get(k))
        same(f"{tl} · call-backs kept", (ours["fu_kept"], ours["fu_due"]), got.get("fu"))
        same(f"{tl} · missed calls not called back", (ours["in_unreturned"], ours["in_missed"]), got.get("in"))
        same(f"{tl} · callers", set(ours["callers"]), set(got["callers"]))
        for c, v in ours["callers"].items():
            same(f"{tl} · {c} calls and real talks", v, got["callers"].get(c))

    plan = os.path.join(out_dir, f"plan_{m.day.isoformat()}.json")
    if os.path.exists(plan):
        leads = {k: v for k, v in json.load(open(plan))["leads"].items() if v["side"] == m.side}
        same("open leads in the morning plan", len(m.rows), len(leads))
        same("open leads by status (morning plan)", dict(Counter(j["status"] for _, j, _ in m.rows)), dict(Counter(v["status"] for v in leads.values())))
    else:
        wrong.append(f"morning plan not found ({os.path.basename(plan)})")

    s = m.sheet_totals()
    mis = lambda k: sum(r[k] for r in m.mis)
    for ours, k in ((s["n"], "all_teams"), (s["rec"], "recovered"), (s["open"], "pending"), (s["drop"], "dropped"),
                    (s["got"], "recovered_amount"), (s["open_rs"], "pending_amount")):
        same(f"pool sheet vs its MIS · {k}", ours, mis(k))
    camps = m.camps()
    same("bootcamp table · leads", sum(b["n"] for b in camps), s["n"])
    same("bootcamp table · leads still to collect", sum(b["open"] for b in camps), s["open"])
    same("bootcamp table · money still to collect", sum(b["open_rs"] for b in camps), s["open_rs"])
    same("money received · callers add up to the team", sum(m.cash_by_caller().values()), sum(a for *_, a in m.moves))
    same("money received · bootcamps add up to the team", sum(b["cash"] for b in camps), sum(a for *_, a in m.moves))
    return n, wrong
