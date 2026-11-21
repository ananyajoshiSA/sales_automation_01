"""How each caller's day went on the November bootcamp collection leads: the numbers behind the PDF's review (dials,
pick-ups, real talks, follow-ups kept, leads' calls returned, 48-hour deadlines met, enrolments) and one reading file per
caller for the readers who write each caller's feedback (what went well, what to fix, three named leads for today).

Only calls to collection leads count: a caller's other calls are not in these files.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, timedelta

from analytics.collection_daily.facts import REAL_SECS, hist_of
from analytics.collection_daily.timelines import events
from integrations.timeutil import IST


def on(t: datetime | None, day: date) -> bool:
    return bool(t) and t.astimezone(IST).date() == day


def day_stats(leads: list[dict], day: date) -> dict[str, Counter]:
    """caller -> counts for ``day``: dials, answered, real, talk_secs, first/last dial (minutes after midnight), follow-ups
    due and kept, missed calls from leads and those not called back, 48-hour deadlines due and met, enrolments."""
    callers = {l["caller"] for l in leads}
    out: dict[str, Counter] = {c: Counter() for c in callers}
    for l in leads:
        for c in l["calls"]:
            if not on(c["t"], day) or c["by"] not in callers:
                continue
            s = out[c["by"]]
            if c["out"]:
                s["dials"] += 1
                m = c["t"].astimezone(IST).hour * 60 + c["t"].astimezone(IST).minute
                s["first"] = min(s["first"] or m, m)
                s["last"] = max(s["last"], m)
            if c["answered"]:
                s["answered"] += c["out"]
                s["talk_secs"] += c["dur"]
                s["real"] += c["dur"] >= REAL_SECS
        s = out[l["caller"]]
        for f in l["follow_ups"]:
            if on(f["due"], day):
                s["fu_due"] += 1
                s["fu_kept"] += bool(f["kept"])
        missed = [c["t"] for c in l["calls"] if not c["out"] and not c["answered"] and on(c["t"], day)]
        if missed:
            s["in_missed"] += 1
            s["in_unreturned"] += not any(c["out"] and c["t"] > missed[-1] for c in l["calls"])
        if on(l["deadline"], day):
            s["d48_due"] += 1
            s["d48_met"] += l["met48"]
        s["enrolled"] += on(l.get("enrolled_at"), day)
    return out


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}" if m else "—"


def stats_line(s: Counter) -> str:
    ans = f"{round(100 * s['answered'] / s['dials'])}%" if s["dials"] else "—"
    return (f"{s['dials']} dials · {ans} answered · {s['real']} real talks (2 min+) · {round(s['talk_secs'] / 60)} min talk · "
            f"{hhmm(s['first'])}–{hhmm(s['last'])} · follow-ups kept {s['fu_kept']} of {s['fu_due']} · "
            f"missed calls from leads, not called back {s['in_unreturned']} of {s['in_missed']} · 48 hours ended for {s['d48_due']} new leads, {s['d48_met']} talked in time"
            + (f" · enrolled {s['enrolled']}" if s["enrolled"] else ""))


def digests(leads: list[dict], hists: dict, notes: dict, day: date, cut: datetime, tx_dirs: list[str], today: list[tuple],
            out_dir: str, cap: int = 2500) -> list[str]:
    """One file per caller: the day's numbers, every event of the day on their leads (transcripts cut to ``cap``
    characters), and their list for the next day (group, why, status, last talk, payment). Returns the paths."""
    os.makedirs(out_dir, exist_ok=True)
    stats = day_stats(leads, day)
    start = datetime(day.year, day.month, day.day, tzinfo=IST)
    paths = []
    for caller in sorted({l["caller"] for l in leads}):
        mine = [l for l in leads if l["caller"] == caller]
        lines = [f"CALLER {caller} (TL {mine[0]['tl']}, AD {mine[0]['ad']}) — {day:%a %d %b %Y}", "NUMBERS: " + stats_line(stats[caller]), ""]
        for l in mine:
            acts = [a for a in (hists.get(l["data_dir"]) or hist_of(l["data_dir"])).get(l["lead_id"]) or []]
            ev = [(t, x) for t, x in events(l, acts, notes.get(l["lead_id"], []), cut, tx_dirs) if start <= t < start + timedelta(days=1)]
            if not ev:
                continue
            lines.append(f"== LEAD {l['name']} · {l['phone']} · {l['bootcamp']} · real talks so far {l['real_talks']}"
                         + (" · ENROLLED" if l["enrolled"] else " · DROPPED" if l["dropped"] else ""))
            for t, x in ev:
                if "TRANSCRIPT" in x and len(x) > cap:
                    x = x[:cap] + " …[cut]"
                lines.append(f"{t.astimezone(IST):%H:%M}  {x}")
            lines.append("")
        lines.append(f"LIST FOR THE NEXT DAY (top of {caller}'s sheet):")
        for l, j, p in today:
            if l["caller"] == caller and p["group"] <= 4:
                lines.append(f"- group {p['group']} · {l['name']} · {p['why'] or ''} · {j['status']} · last talk: {j.get('last_talk') or '—'} · "
                             f"payment: {j.get('payment') or '—'} · next: {j.get('next') or '—'}")
        path = os.path.join(out_dir, caller.replace(" ", "_") + ".txt")
        open(path, "w").write("\n".join(lines) + "\n")
        paths.append(path)
    return paths
