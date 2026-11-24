"""The daily team PDF for the AD and team leaders of one bootcamp collection team (Mayur's or Anmol's): the previous
day's review, the priority leads and checks for today, each caller's feedback, the 48-hour tracker, money against the
85% target, the payment pipeline, objections and the IIT add-on, leads going cold, records to fix, and the day's
routine. Layout agreed with the user on 19 Nov 2026 from the sales team's call-plan PDF; callers keep the Excel.

Inputs: a run folder (facts, judgements), the previous day's plan (``plan_<day>.json`` from the morning build), the
caller feedback the readers wrote (``<run_dir>/feedback/out*.json``, see docs/collection_feedback_brief.md) and the
Bootcamp pool sheet downloaded into the run folder (``bootcamp_pool.xlsx``) for money.

Groups of payment route, objection and IIT answer are read from the readers' own written fields (status, payment,
objection, iit_pitch); the PDF says so where it counts them.

    python -m analytics.collection_daily.pdf <run_dir> <day YYYY-MM-DD> <previous plan.json> <out_dir> ["note"] [--side "Puja / Anmol"] [--by-tl]
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta

from analytics.collection_daily import facts, morning, review
from analytics.collection_daily.__main__ import judged_on
from analytics.collection_reports import CSS, e, lakh, pct, write_pdf
from analytics.collections.pool_mis import course_of, load_mis, load_students
from analytics.collections.tags import tag_date
from integrations.timeutil import IST

TARGET = 85
TEAM_ADS = {"Mayur": "Mayur's team (Elite Changemakers)", "Puja / Anmol": "Anmol's team (Puja Malik)"}

EXTRA_CSS = """
.band { background:#1F4E46; color:#fff; border-radius:7px; padding:14px 18px; margin-bottom:10px; }
.band small { letter-spacing:1px; font-size:8px; text-transform:uppercase; opacity:.85; } .band h1 { color:#fff; font-size:20px; margin:4px 0; }
.band p { margin:0; font-size:9px; opacity:.95; }
.tiles6 { display:grid; grid-template-columns: repeat(3,1fr); gap:6px; margin:6px 0 10px; }
.tiles6 .tile b { font-size:18px; }
.red { border-left:4px solid #a3390b; background:#FBEDEA; padding:5px 9px; margin:4px 0; }
.amber { border-left:4px solid #c08a12; background:#FFF6E0; padding:5px 9px; margin:4px 0; }
.green { border-left:4px solid #1F4E46; background:#EEF4F2; padding:5px 9px; margin:4px 0; }
.card { border:1px solid #d9d6cf; border-radius:5px; padding:6px 9px; margin:5px 0; page-break-inside: avoid; }
.card h4 { margin:0 0 2px; font-size:10.5px; color:#1F4E46; } .card .nums { color:#52514e; font-size:7.8px; margin-bottom:3px; }
.card ol { margin:2px 0 0 16px; padding:0; }
tr.grp td { background:#1F4E46; color:#fff; font-weight:bold; }
"""

DATE = re.compile(r"(?<!since )\b(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+)?(\d{1,2})\s+(Nov|Dec|Jan)\b", re.I)
ROUTES = [("Loan / finance", re.compile(r"loan|financ|nbfc|bajaj|eduvanz|shopse|credence|propelld|fibe|document emi|e-?mandate|paper", re.I)),
          ("Card EMI", re.compile(r"card", re.I)), ("One shot", re.compile(r"one[- ]?(shot|time)|full payment|lump", re.I)),
          ("Instalments", re.compile(r"instal|install", re.I))]
OBJECTIONS = [("Refund or cancel", re.compile(r"refund|cancel|drop|withdraw", re.I)),
              ("No card or loan route", re.compile(r"card|credit|cibil|loan|co-?applicant|financ|eligib|limit", re.I)),
              ("Money or price", re.compile(r"price|fee|expens|budget|cost|afford|fund|money|income|salary|job|cash", re.I)),
              ("Wants later or no time", re.compile(r"later|next batch|defer|decemb|januar|time|busy|hold|postpone|exam", re.I)),
              ("Family decides", re.compile(r"family|husband|wife|parent|father|mother|brother|spouse", re.I)),
              ("Doubts about the course", re.compile(r"doubt|value|quality|result|placement|course|trust|unclear|review", re.I))]


def route(payment: str | None) -> str:
    p = payment or ""
    if not p.strip(" —-") or re.match(r"\s*undecided", p, re.I):
        return "Not decided"
    return next((name for name, rx in ROUTES if rx.search(p)), "Not decided")


def promised(payment: str | None, year: int) -> date | None:
    """The first date written in the reader's payment field (a "since" date is not a promise)."""
    m = DATE.search(payment or "")
    if not m:
        return None
    mon = {"nov": 11, "dec": 12, "jan": 1}[m.group(2).lower()]
    try:
        return date(year + (mon == 1), mon, int(m.group(1)))
    except ValueError:  # "31 Nov": not a date, so not a promise the list can hold anyone to
        return None


def objection_kind(text: str | None) -> str:
    return next((name for name, rx in OBJECTIONS if rx.search(text or "")), "Other")


def iit_kind(text: str | None) -> str:
    t = (text or "").lower()
    if not t or t.startswith("not read"):
        return "Not read yet"
    if t.startswith("not pitched"):
        return "Not pitched"
    if re.search(r"declin|not interested|refus|no interest|not now|wants base|only the base", t):
        return "Declined"
    if re.search(r"interest|will add|added|agreed|keen|wants it|yes", t):
        return "Interested"
    return "Pitched, undecided"


def table(head: list[tuple[str, str]], rows: list[list], cls: dict[int, str] | None = None) -> str:
    """``head``: (label, width) pairs; ``rows``: cells (already escaped HTML) or ("grp", text) separator rows."""
    h = "".join(f'<th style="width:{w}"{" class=n" if (cls or {}).get(i) == "n" else ""}>{e(t)}</th>' for i, (t, w) in enumerate(head))
    body = []
    for r in rows:
        if r and r[0] == "grp":
            body.append(f'<tr class="grp"><td colspan="{len(head)}">{e(r[1])}</td></tr>')
            continue
        body.append("<tr>" + "".join(f'<td{" class=n" if (cls or {}).get(i) == "n" else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>")
    return f"<table><tr>{h}</tr>{''.join(body)}</table>"


def lead_cell(l: dict) -> str:
    return f'<b>{e(l["name"] or "—")}</b> <span class="ph">{e(l["phone"])}</span><br><span class="muted">{e(l["caller"])} · {e(l["bootcamp"])}</span>'


def day_result(l: dict, day: date) -> str:
    calls = [c for c in l["calls"] if review.on(c["t"], day)]
    if review.on(l.get("enrolled_at"), day):
        return "Enrolled"
    if any(c["answered"] and c["dur"] >= facts.REAL_SECS for c in calls):
        return "Real talk"
    if any(c["answered"] for c in calls):
        return "Short pick-up"
    return "Not reached" if any(c["out"] for c in calls) else "Not called"


def load_callerwise(path: str, month: date) -> list[dict]:
    """The pool sheet's "Callerwise Split <Mon> <yy>" tab: one row per bootcamp and caller, for a team leader's PDF."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        tab = next((n for n in wb.sheetnames if n.lower().startswith("callerwise split") and month.strftime("%b").lower() in n.lower()
                    and month.strftime("%y") in n), None)
        if not tab:
            return []
        out, head = [], False
        for r in wb[tab].iter_rows(values_only=True):
            r = list(r) + [None] * 16
            if r[0] == "Team":
                head = True
                continue
            name = str(r[3] or "").strip()
            if not head or not name or name.lower() in ("total", "grand total") or not isinstance(r[4], (int, float)):
                continue
            out.append({"bootcamp": str(r[2] or r[1] or ""), "caller": name, "all_teams": int(r[4] or 0), "recovered": int(r[6] or 0),
                        "pending": int(r[7] or 0), "dropped": int(r[8] or 0), "recovered_amount": float(r[10] or 0),
                        "pending_amount": float(r[13] or 0)})
        return out
    finally:
        wb.close()


def same_person(a: str, b: str) -> bool:
    """LeadSquared and the pool sheet spell some callers differently ("Yamini Billore", "Yamini Hitesh Billore")."""
    x, y = set(a.lower().split()), set(b.lower().split())
    return bool(x) and bool(y) and (x <= y or y <= x)


def tl_mis(rows: list[dict], callers: set[str]) -> list[dict]:
    """The caller-wise MIS rows of a team leader's callers, added up per bootcamp in the team MIS's shape."""
    by: dict[str, dict] = {}
    for r in rows:
        if not any(same_person(r["caller"], c) for c in callers):
            continue
        b = by.setdefault(r["bootcamp"], {"side": "", "name": r["bootcamp"], "course": course_of(r["bootcamp"]), "day": tag_date(r["bootcamp"]),
                                          "all_teams": 0, "recovered": 0, "pending": 0, "dropped": 0, "recovered_amount": 0.0,
                                          "pending_amount": 0.0})
        for k in ("all_teams", "recovered", "pending", "dropped", "recovered_amount", "pending_amount"):
            b[k] += r[k]
    return list(by.values())


class TeamPDF:
    def __init__(self, side: str, leads: list[dict], rows: list[tuple], prev: dict, cut: datetime, day: date,
                 feedback: dict, mis: tuple[str, list[dict]], students: dict, notes: dict, tl: str | None = None,
                 callerwise: list[dict] | None = None):
        # with ``tl``: one team leader's callers only (the user, 23 Nov: one PDF per TL); money from the caller-wise MIS
        self.side, self.tl = side, tl
        self.team = TEAM_ADS[side] + (f" · TL {tl}" if tl else "")
        self.leads = [l for l in leads if l["side"] == side and (not tl or l["tl"] == tl)]
        self.by_id = {l["lead_id"]: l for l in self.leads}
        self.rows = [r for r in rows if r[0]["side"] == side and (not tl or r[0]["tl"] == tl)]
        self.cut, self.day, self.yday = cut, day, day - timedelta(days=1)
        # a list built for another day (none was built for the previous day) is not compared with the previous day's calls
        self.has_list = prev.get("day") == self.yday.isoformat()
        self.prev = {k: v for k, v in prev.get("leads", {}).items() if k in self.by_id} if self.has_list else {}
        self.feedback, (self.mis_as_of, mis_rows), self.students, self.notes = feedback, mis, students, notes
        self.mis = ([r for r in mis_rows if r["side"].lower() in side.lower()] if not tl else
                    tl_mis(callerwise or [], {l["caller"] for l in self.leads} | {tl}))
        self.bootcamp_leads = [l for l in self.leads if not l.get("community")]
        self.community_leads = [l for l in self.leads if l.get("community")]
        self.stats = review.day_stats(self.leads, self.yday)
        self.stats_before = review.day_stats(self.leads, self.yday - timedelta(days=1))
        self.ads = sorted({l["ad"] for l in self.leads})
        self.callers = sorted({l["caller"] for l in self.leads}, key=lambda c: (self.by_id_tl(c), c))

    def by_id_tl(self, caller: str) -> tuple:
        l = next(l for l in self.leads if l["caller"] == caller)
        return (l["ad"], l["tl"])

    # -- shared numbers -----------------------------------------------------------------------------------------
    def team_day(self, stats: dict) -> Counter:
        t = Counter()
        for s in stats.values():
            t.update(s)
        t["dialled"] = sum(1 for s in stats.values() if s["dials"])
        return t

    def score48(self) -> tuple[int, int]:
        past = [l for l in self.leads if self.cut > l["deadline"]]
        return sum(l["met48"] for l in past), len(past)

    # -- pages --------------------------------------------------------------------------------------------------
    def cover(self) -> str:
        rec, n = sum(r["recovered"] for r in self.mis), sum(r["all_teams"] for r in self.mis)
        y = self.team_day(self.stats)
        met, past = self.score48()
        g = Counter(p["group"] for _, _, p in self.rows)
        enrolled = [l for l in self.leads if review.on(l.get("enrolled_at"), self.yday)]
        tiles = [(f"{rec} of {n}", f"leads recovered this month ({pct(rec, n)}%; target {TARGET}%) · pool sheet MIS of {self.mis_as_of}"),
                 (lakh(sum(r["recovered_amount"] for r in self.mis)), f"collected so far · {lakh(sum(r['pending_amount'] for r in self.mis))} still pending (MIS)"),
                 (str(len(enrolled)), f"enrolled on {self.yday:%A} (LeadSquared stage)"),
                 (f"{y['real']} of {y['dials']}", f"real talks (2 min+) of dials on {self.yday:%A}"),
                 (f"{pct(met, past)}%", f"48-hour score: real talk within 48 h, {met} of {past} leads past their deadline (target 90%)"),
                 (f"{len(self.rows)}", f"open leads to call today · {g[1]} follow-ups first, {g[2]} on the 48-hour clock")]
        contents = [f"{self.yday:%A} review: what worked, what didn't, missed opportunities", "Priority leads for today",
                    "What you check, and when", "Team overview", "Feedback to give each caller", "48-hour tracker",
                    "Money and the payment pipeline", "Objections and the IIT add-on", "Leads going cold, waiting list, records to fix",
                    "The day, hour by hour · on every call"]
        return ('<div class="page"><div class="band"><small>Bootcamp collections · ' + e(self.team) + " · for "
                + e(", ".join(self.ads)) + f' (AD) and the team leaders</small><h1>Call plan for {self.day:%A %d %B %Y}</h1>'
                f'<p>A review of {self.yday:%A} first, then today\'s priority leads and checks, each caller\'s feedback, the 48-hour '
                'tracker, money against the target, and the routine. Callers work from the Excel call list.</p></div>'
                '<div class="tiles6">' + "".join(f'<div class="tile"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in tiles) + "</div>"
                + (self.act_today() + '<p class="muted">Then: ' + " · ".join(e(c) for c in contents) + ".</p>" if self.tl else
                   "<h2>Contents</h2><ol>" + "".join(f"<li>{e(c)}</li>" for c in contents) + "</ol>")
                + f'<p class="muted">Data: LeadSquared read {self.cut.astimezone(IST):%a %d %b %H:%M} IST (calls, stage comments, disposition notes and '
                f'call-back times, comment fields, Notes). {self.notes.get("transcripts", "")} Bootcamp pool sheet MIS of {self.mis_as_of}, '
                f'downloaded {self.notes.get("pool_read", "")} IST. Only calls to November bootcamp collection leads and November community collection leads (marked "(Community)") are counted. '
                'Statuses, payment plans, objections and the IIT answer are read from each lead\'s calls, transcripts, comments and Notes.</p></div>')

    def balance(self, l: dict) -> float | None:
        """Balance still to collect: the community sheet's for a community lead, the pool sheet's for a bootcamp lead."""
        if l.get("community"):
            return l.get("balance")
        s = self.students.get(re.sub(r"\D", "", l["phone"])[-10:])
        return s.get("balance") if s else None

    def act_today(self) -> str:
        """Page 1 for a team leader: the leads where money moves or is lost today, each with its amount, date and step."""
        day = self.day
        rs = lambda x: "—" if x is None else f"₹{x:,.0f}"
        pay, booked, brink, paid, seen = [], [], [], [], set()
        for l, j, p in self.rows:
            if self.balance(l) == 0:  # the sheet has it fully paid: not a lead to chase, a record to fix
                paid.append((l, j, p))
                seen.add(l["lead_id"])
                continue
            kind = objection_kind(j.get("objection"))
            d = promised(j.get("payment"), day.year)
            quiet = (self.cut - l["last_real"]).days if l["last_real"] else None
            if kind == "Refund or cancel" or (j["status"] == "cold" and l["real_talks"]) or (
                    j["status"] in ("hot", "counsel") and quiet is not None and quiet >= 5 and l["dials"] and not (j.get("waiting_until") or "") > day.isoformat()):
                why = ("Asked for a refund or to cancel" if kind == "Refund or cancel" else "Cold after a real talk" if j["status"] == "cold"
                       else f"{morning.STATUS[j['status']]} but no real talk for {quiet} days")
                brink.append((l, j, p, why))
                seen.add(l["lead_id"])
            elif j["status"] in ("hot", "counsel") and d and d <= day:
                pay.append((l, j, p, d))
                seen.add(l["lead_id"])
        for l, j, p in self.rows:
            if l["lead_id"] not in seen and p["group"] == 1 and p["due"] and j["status"] in ("hot", "counsel", "connected", "callback"):
                booked.append((l, j, p))
        pay.sort(key=lambda r: (r[3], r[0]["caller"]))
        booked.sort(key=lambda r: r[2]["due"])

        def who(l):
            return f'<b>{e(l["name"] or "—")}</b> <span class="ph">{e(l["phone"])}</span><br><span class="muted">{e(l["caller"])} · {e(l["bootcamp"])}</span>'
        t1 = table([("#", "3%"), ("Lead", "25%"), ("Balance", "9%"), ("Promised", "12%"), ("Payment plan · last talk", "29%"), ("Do today", "22%")],
                   [[str(i), who(l), rs(self.balance(l)), e(f"{d:%a %d %b}" + (" (overdue)" if d < day else " (today)")),
                     e(f'{j.get("payment") or "—"} · {j.get("last_talk") or "—"}'), e(j.get("next") or "—")]
                    for i, (l, j, p, d) in enumerate(pay, 1)], {2: "n"})
        t2 = table([("Time", "8%"), ("Lead", "25%"), ("Balance", "9%"), ("Status", "13%"), ("Last talk", "27%"), ("Do today", "18%")],
                   [[f'<b>{p["due"].astimezone(IST):%H:%M}</b>', who(l), rs(self.balance(l)), e(morning.STATUS[j["status"]]),
                     e(j.get("last_talk") or "—"), e(j.get("next") or "—")] for l, j, p in booked], {2: "n"})
        t3 = table([("#", "3%"), ("Lead", "25%"), ("Balance", "9%"), ("Why at risk", "18%"), ("What the lead said", "25%"), ("Do today", "20%")],
                   [[str(i), who(l), rs(self.balance(l)), f'<span class="bad">{e(why)}</span>', e(j.get("objection") or j.get("last_talk") or "—"),
                     e(j.get("next") or "Keep the seat; offer instalments or a named later batch")]
                    for i, (l, j, p, why) in enumerate(brink, 1)], {2: "n"})
        total = sum(self.balance(l) or 0 for l, *_ in pay)
        return (f'<h2>1 · Will pay today or already overdue ({len(pay)}) · {rs(total)} to collect</h2>'
                '<p class="muted">Payment date as the lead gave it on a call or in a Note. Send the link while the lead is on the line and stay on '
                'until it goes through. Check by 12:00.</p>' + (t1 if pay else "<p>None today.</p>")
                + f'<h2>2 · Call-backs fixed for today ({len(booked)})</h2>' + (t2 if booked else "<p>None booked.</p>")
                + f'<h2>3 · On the brink of dropping ({len(brink)})</h2>'
                '<p class="muted">Asked for a refund or to cancel, gone cold after a real talk, or Hot / To be counselled with no real talk for 5+ days '
                'despite calls. The team leader calls each one by 12:00.</p>' + (t3 if brink else "<p>None.</p>")
                + (f'<h2>4 · Paid in full per the sheet, still open in LeadSquared ({len(paid)})</h2>'
                   '<p class="muted">Confirm the payment with finance and move the stage to Course Enrolled; do not chase these leads.</p>'
                   + table([("Lead", "34%"), ("Status in the lists", "18%"), ("Last talk", "48%")],
                           [[who(l), e(morning.STATUS[j["status"]]), e(j.get("last_talk") or "—")] for l, j, p in paid]) if paid else ""))

    def review_page(self) -> str:
        y, b = self.team_day(self.stats), self.team_day(self.stats_before)
        yd, bd = f"{self.yday:%a %d %b}", f"{self.yday - timedelta(days=1):%a %d %b}"
        enrolled = [l for l in self.leads if review.on(l.get("enrolled_at"), self.yday)]
        # the green summary box is gone (the user, 19 Nov): its numbers are the scorecard's; only the names were its own
        who = ('<p class="muted">Enrolled on ' + f'{self.yday:%A}: ' + ", ".join(f'{e(l["name"])} ({e(l["caller"])})' for l in enrolled) + ".</p>"
               if enrolled else "")

        def m(s: Counter, k: str) -> str:
            return str(s[k])
        score = [("Enrolled (LeadSquared stage)", m(b, "enrolled"), m(y, "enrolled")),
                 ("Callers who dialled", f'{b["dialled"]} of {len(self.callers)}', f'{y["dialled"]} of {len(self.callers)}'),
                 ("Dials", m(b, "dials"), m(y, "dials")), ("Answer rate", f'{pct(b["answered"], b["dials"])}%', f'{pct(y["answered"], y["dials"])}%'),
                 ("Real talks (2 min+)", m(b, "real"), m(y, "real")),
                 ("Talk time", f'{round(b["talk_secs"] / 60)} min', f'{round(y["talk_secs"] / 60)} min'),
                 ("Follow-ups kept (saved call-backs due that day)", f'{b["fu_kept"]} of {b["fu_due"]}', f'{y["fu_kept"]} of {y["fu_due"]}'),
                 ("Missed calls from leads, not called back", f'{b["in_unreturned"]} of {b["in_missed"]}', f'{y["in_unreturned"]} of {y["in_missed"]}'),
                 ("New leads whose 48 hours ended that day: real talk in time", f'{b["d48_met"]} of {b["d48_due"]}', f'{y["d48_met"]} of {y["d48_due"]}')]
        sc = table([("Measure", "46%"), (bd, "27%"), (yd, "27%")], [[e(a), e(c), f"<b>{e(d)}</b>"] for a, c, d in score])

        # what the list asked vs what happened
        res = {k: day_result(self.by_id[k], self.yday) for k in self.prev}
        asked = []
        for g in (1, 2, 3, 4):
            ids = [k for k, v in self.prev.items() if v["group"] == g]
            if not ids:
                continue
            c = Counter(res[k] for k in ids)
            asked.append([e(morning.GROUPS[g]), str(len(ids)), str(c["Enrolled"]), str(c["Real talk"]), str(c["Short pick-up"]),
                          str(c["Not reached"]), f'<span class="bad">{c["Not called"]}</span>' if c["Not called"] else "0"])
        ask = table([("On the list", "34%"), ("Leads", "8%"), ("Enrolled", "10%"), ("Real talk, not enrolled", "13%"),
                     ("Short pick-up", "11%"), ("Not reached", "11%"), ("Not called", "13%")], asked, {i: "n" for i in range(1, 7)})

        perf = []
        for c in self.callers:
            s = self.stats[c]
            perf.append([e(c), str(s["dials"]), f'{pct(s["answered"], s["dials"])}%' if s["dials"] else "—", str(s["real"]),
                         str(round(s["talk_secs"] / 60)), f'{review.hhmm(s["first"])}–{review.hhmm(s["last"])}' if s["dials"] else "—",
                         f'{s["fu_kept"]}/{s["fu_due"]}', f'{s["in_unreturned"]}/{s["in_missed"]}', f'{s["d48_met"]}/{s["d48_due"]}',
                         str(s["enrolled"] or "—")])
        # numbers only: the readers' one-liner here was too terse to read (the user, 19 Nov); the feedback cards say it in full
        pt = table([("Caller", "18%"), ("Dials", "7%"), ("Answered", "8%"), ("Real talks", "8%"), ("Talk min", "8%"), ("First–last dial", "12%"),
                    ("Follow-ups kept", "9%"), ("Missed calls not called back", "11%"), ("48 hours ended: talked in time", "11%"), ("Enrolled", "8%")],
                   perf, {i: "n" for i in range(1, 10)})
        pt += '<p class="muted">What went well, what to fix and each caller\'s three leads for today: section 5.</p>'

        # leads from the list that were not called or not reached
        order = {"Not called": 0, "Not reached": 1}
        now_open = {l["lead_id"] for l, _, _ in self.rows}  # a lead closed since is no longer anyone's job
        miss = sorted([(k, v) for k, v in self.prev.items() if v["group"] <= 3 and res[k] in order and k in now_open],
                      key=lambda kv: (order[res[kv[0]]], kv[1]["group"], kv[1]["caller"]))
        now = {l["lead_id"]: (j, p) for l, j, p in self.rows}
        mrows = []
        for k, v in miss[:45]:
            l = self.by_id[k]
            j, p = now.get(k, ({}, {}))
            mrows.append([lead_cell(l), e(v["why"] or morning.GROUPS[v["group"]].split(":")[0]),
                          f'<span class="bad">{res[k]}</span>' if res[k] == "Not called" else res[k],
                          e(morning.STATUS.get(j.get("status"), "Closed") + (f' · {j.get("next")}' if j.get("next") else ""))])
        mt = (table([("Lead", "30%"), (f"On {self.yday:%A}'s list as", "24%"), ("Result", "10%"), ("Now · next step", "36%")], mrows)
              + (f'<p class="muted">{len(miss) - 45} more not shown.</p>' if len(miss) > 45 else ""))

        return ('<div class="page">' + f'<h1>1. {self.yday:%A} review: what worked, what didn\'t, missed opportunities</h1>'
                + f'<h2>Scorecard: {bd} vs {yd}</h2>' + sc + who
                + ((f'<h2>What {self.yday:%A}\'s list asked vs what happened</h2>'
                    + f'<p class="muted">{self.yday:%A}\'s list was built from the LeadSquared read of {e(self.notes.get("prev_cut", ""))}; '
                      'each lead counts once: enrolled that day, else a real talk (2 min+), else a short pick-up, else dialled but not reached, else not called.</p>' + ask)
                   if self.has_list else f'<p class="muted">No call list was built for {self.yday:%A}, so there is no list to compare the day with.</p>')
                + f'<h2>Team performance by caller ({yd})</h2>' + pt
                + (f'<h2>{self.yday:%A}\'s follow-ups, 48-hour and Hot leads that were not called or not reached ({len(miss)})</h2>' + mt
                   if self.has_list else "")
                + self.missed() + "</div>")

    def missed(self) -> str:
        yday, rows = self.yday, {l["lead_id"]: (j, p) for l, j, p in self.rows}
        fu_missed = [l for l in self.leads if any(review.on(f["due"], yday) and f["kept"] is False for f in l["follow_ups"])]
        unreturned = [l for l in self.leads if (ms := [c["t"] for c in l["calls"] if not c["out"] and not c["answered"] and review.on(c["t"], yday)])
                      and not any(c["out"] and c["t"] > ms[-1] for c in l["calls"])]
        long_no_plan = [(l, max(c["dur"] for c in l["calls"] if c["answered"] and review.on(c["t"], yday)))
                        for l in self.leads if l["lead_id"] in rows and rows[l["lead_id"]][0].get("status") == "connected"
                        and any(c["answered"] and c["dur"] >= 300 and review.on(c["t"], yday) for c in l["calls"])]
        d48 = [l for l in self.leads if review.on(l["deadline"], yday) and not l["met48"] and not l["enrolled"] and not l["dropped"]]
        names = lambda ls: ", ".join(f'{e(l["name"] or l["phone"])} ({e(l["caller"])})' for l in ls[:25]) + (f" and {len(ls) - 25} more" if len(ls) > 25 else "")
        boxes = [("Follow-ups not kept", fu_missed), ("Missed calls from leads, not called back", unreturned),
                 ("48-hour deadline missed", d48)]
        out = ["<h2>Missed opportunities</h2>"]
        for title, ls in boxes:
            if ls:
                out.append(f'<div class="red"><b>{title} ({len(ls)}).</b> {names(ls)}.</div>')
        if long_no_plan:
            out.append(f'<div class="red"><b>Long talks that ended without a payment plan ({len(long_no_plan)}).</b> '
                       + ", ".join(f'{e(l["name"])} {round(d / 60)} min ({e(l["caller"])})' for l, d in long_no_plan) + ".</div>")
        idle = [c for c in self.callers if not self.stats[c]["dials"]]
        pts = []
        if idle:
            pts.append(f"{', '.join(idle)} made no calls to collection leads on {yday:%A}. Who is in today, and who covers their follow-ups?")
        if unreturned:
            pts.append(f"{len(unreturned)} missed calls from leads were not called back. Who calls them back by 10:30?")
        if fu_missed:
            pts.append(f"{len(fu_missed)} follow-ups were not kept. Are call-back times being set when the caller can keep them?")
        if d48:
            pts.append(f"{len(d48)} leads missed the 48-hour talk on {yday:%A}. Each caller names a time for them this morning.")
        if pts:
            out.append("<h2>Points for the team leaders' huddle</h2><ol>" + "".join(f"<li>{e(p)}</li>" for p in pts) + "</ol>")
        return "".join(out)

    def priority(self) -> str:
        groups = {"pay": [], "loan": [], "risk": []}
        soon = self.day + timedelta(days=1)
        for l, j, p in self.rows:
            if objection_kind(j.get("objection")) == "Refund or cancel":
                groups["risk"].append((l, j, p))
                continue
            d = promised(j.get("payment"), self.day.year)
            if j["status"] in ("hot", "counsel") and d and d <= soon:
                groups["pay"].append((l, j, p, d))
            elif j["status"] == "hot" and route(j.get("payment")) == "Loan / finance":
                groups["loan"].append((l, j, p))
        rows = []
        spec = [("pay", f"1 · Payment promised by {soon:%a %d %b} or already overdue: the caller sends the link while the lead is on the "
                        "line and stays on until it goes through", "12:00"),
                ("loan", "2 · Loan or finance in progress (Hot): the missing documents named and collected today; the finance partner "
                         "pushed if already submitted", "13:00"),
                ("risk", "3 · Asked about a refund or cancelling: you call, keep the seat, offer instalments or a named later batch "
                         "with the fee carried over", "12:00 (you call)")]
        n = 0
        for key, title, role in spec:
            items = groups[key]
            if not items:
                continue
            rows.append(("grp", f"{title} ({len(items)}) · check by {role}"))
            for it in items:
                l, j = it[0], it[1]
                n += 1
                when = f'{it[3]:%a %d %b}' + (" — overdue" if it[3] < self.day else "") if key == "pay" else ""
                rows.append([str(n), lead_cell(l), e((j.get("last_talk") or "—") + (f' · Payment: {j["payment"]}' if j.get("payment") else "")),
                             e((f"{when}: " if when else "") + (j.get("next") or "—"))])
        intro = (f"<p>Every lead where money can move today. The caller makes the call; <b>you own the outcome</b>. "
                 "Payment dates are the ones written in each lead's payment plan.</p>")
        return ('<div class="page"><h1>2. Priority leads for today</h1>' + intro
                + table([("#", "3%"), ("Lead", "25%"), ("Why this lead", "44%"), ("What the caller must do", "28%")], rows)
                + "</div>")

    def checks(self) -> str:
        g = Counter(p["group"] for _, _, p in self.rows)
        unret = sum(1 for _, _, p in self.rows if "not called back" in p["why"])
        idle = [c for c in self.callers if not self.stats[c]["dials"]]
        morning_fu = sum(1 for _, _, p in self.rows if p["group"] == 1 and p["due"] and p["due"].astimezone(IST).hour < 15)
        rows = [("09:45", "Payment links, EMI sheets and the callers' lists ready.", "Missing → get them from finance before 10:00."),
                ("10:00", "Attendance and dialler: every caller logged in and dialling." + (f" No calls yesterday: {', '.join(idle)}." if idle else ""),
                 "Absent → their follow-ups and 48-hour leads go to a caller present, with a note."),
                ("10:30", f"Missed calls from leads called back first ({unret} on the lists).", "Not returned by 10:30 → call it yourself or reassign."),
                ("12:00", "Payments promised for today done (priority group 1).", "Not paid → join the next call; fix the exact time today."),
                ("13:00", f"Every 48-hour lead dialled at least twice ({g[2]} on the lists).", "Not reached → WhatsApp and a third dial after 16:00."),
                ("15:00", f"Morning follow-ups kept ({morning_fu} booked before 15:00).", "Missed → call within the hour and set a new time with the lead."),
                ("17:00", "Hot leads with no talk today; counselling leads spoken to.", "Untouched → caller calls before 19:00; you sit on the call."),
                ("19:30", "A disposition and next call-back time saved after every talk; WhatsApp replies written as Notes with date and channel.",
                 "Missing → fill before 20:00, so tomorrow's list is right."),
                ("20:00", "Day close: payments received and stages moved to Course Enrolled.", "Post the count with names.")]
        return ('<div class="page"><h1>3. What you check, and when</h1>'
                + table([("Time", "8%"), ("Check", "50%"), ("If it fails", "42%")], [[f"<b>{a}</b>", e(b), e(c)] for a, b, c in rows])
                + '<div class="green">10:00 huddle (5 minutes): each caller reads out their three for today (section 5) and the time they will call them.</div></div>')

    def feedback_page(self) -> str:
        if not self.feedback:  # e.g. after a Sunday with a handful of dials: say so rather than print empty cards
            y = self.team_day(self.stats)
            return ('<div class="page"><h1>5. Feedback to give each caller</h1>'
                    f'<p>No caller feedback today: {self.yday:%A} had {y["dials"]} dials to collection leads ({y["real"]} real talks). '
                    'Each caller\'s numbers for the day are in the caller table (section 1).</p></div>')
        cards = []
        for c in self.callers:
            s, f = self.stats[c], self.feedback.get(c)
            if not s["dials"] and not f:
                continue
            today = "".join(f"<li>{e(x)}</li>" for x in (f or {}).get("today", []))
            cards.append(f'<div class="card"><h4>{e(c)} <span class="muted">· TL {e(self.by_id_tl(c)[1])}</span></h4>'
                         f'<div class="nums">{self.yday:%a}: {e(review.stats_line(s))}</div>'
                         + (f'<b>Went well:</b> {e(f.get("went_well", "—"))}<br><b>Fix:</b> {e(f.get("fix", "—"))}'
                            f'<br><b>Their three for today:</b><ol>{today}</ol>' if f else '<span class="muted">No feedback written.</span>') + "</div>")
        idle = [c for c in self.callers if not self.stats[c]["dials"] and not self.feedback.get(c)]
        return ('<div class="page"><h1>5. Feedback to give each caller</h1>'
                f'<p class="muted">From {self.yday:%A}\'s calls, transcripts, comments and Notes on collection leads. Give it at the huddle, '
                'one caller at a time, two minutes each.</p>' + "".join(cards)
                + (f'<p class="muted">No calls to collection leads on {self.yday:%A}: {e(", ".join(idle))}.</p>' if idle else "") + "</div>")

    def tracker(self) -> str:
        # the per-caller table is gone (the user, 19 Nov): its scores are in the team overview (section 4)
        lst = sorted([(l, j, p) for l, j, p in self.rows if l["due48"]], key=lambda r: (r[0]["deadline"], r[0]["caller"]))
        lr = [[lead_cell(l), f'{l["allocated"].astimezone(IST):%a %d %b %H:%M}',
               (f'<span class="bad">missed {l["deadline"].astimezone(IST):%a %d %b}</span>' if self.cut > l["deadline"]
                else f'{l["deadline"].astimezone(IST):%a %d %b %H:%M}'),
               f'{l["dials"]} / {l["dial_days"]}', e(morning.STATUS[j["status"]] + (f' · {j["last_talk"]}' if j.get("last_talk") not in (None, "—") else ""))]
              for l, j, p in lst]
        return ('<div class="page"><h1>6. 48-hour tracker</h1><p class="muted">One real talk (answered, 2 min+) by 20:00 IST two days after '
                'the lead reaches the caller. Target 90%. Each caller\'s score and pick-up rate are in the team overview (section 4).</p>'
                + f'<h2>Leads still without a real talk ({len(lst)}), earliest deadline first</h2>'
                + table([("Lead", "28%"), ("Given to caller", "14%"), ("48-hour deadline", "14%"), ("Calls / days", "9%"), ("Status · last contact", "35%")], lr)
                + "</div>")

    def money(self) -> str:
        rows, tot = [], Counter()
        for r in sorted(self.mis, key=lambda r: (r["day"] or "", r["course"])):
            n = r["all_teams"]
            need = max(0, -(-TARGET * n // 100) - r["recovered"])
            tot.update({"n": n, "rec": r["recovered"], "pen": r["pending"], "drop": r["dropped"], "need": need})
            tot["ra"] += r["recovered_amount"]
            tot["pa"] += r["pending_amount"]
            p = pct(r["recovered"], n)
            rows.append([e(f'{r["course"]} · {datetime.strptime(r["day"], "%Y-%m-%d"):%d %b}' if r["day"] else r["name"]), str(n), str(r["recovered"]),
                         f'<span class="bad">{p}%</span>' if p < TARGET else f"{p}%", str(r["pending"]), str(r["dropped"]), str(need),
                         lakh(r["recovered_amount"]), lakh(r["pending_amount"])])
        rows.append([f"<b>Month so far</b>", f'<b>{tot["n"]}</b>', f'<b>{tot["rec"]}</b>', f'<b>{pct(tot["rec"], tot["n"])}%</b>', f'<b>{tot["pen"]}</b>',
                     f'<b>{tot["drop"]}</b>', f'<b>{tot["need"]}</b>', f'<b>{lakh(tot["ra"])}</b>', f'<b>{lakh(tot["pa"])}</b>'])
        mt = table([("Bootcamp", "26%"), ("Leads", "7%"), ("Recovered", "9%"), ("Recovery", "8%"), ("Pending", "8%"), ("Dropped", "8%"),
                    (f"More needed for {TARGET}%", "10%"), ("Collected", "12%"), ("Pending amount", "12%")], rows, {i: "n" for i in range(1, 9)})
        pipe = Counter((route(j.get("payment")), j["status"]) for _, j, _ in self.rows)
        routes = ["Loan / finance", "Card EMI", "One shot", "Instalments", "Not decided"]
        sts = ["hot", "counsel", "connected", "callback", "notconnected", "cold"]
        pr = [[e(rt)] + [str(pipe[(rt, s)] or "") for s in sts] + [str(sum(pipe[(rt, s)] for s in sts))] for rt in routes]
        pt = table([("Payment route", "22%")] + [(morning.STATUS[s], "11%") for s in sts] + [("All", "12%")], pr, {i: "n" for i in range(1, 8)})
        return ('<div class="page"><h1>7. Money and the payment pipeline</h1>'
                f'<h2>Recovery against the {TARGET}% target (pool sheet {"caller-wise " if self.tl else ""}MIS of {e(self.mis_as_of)})</h2>' + mt
                + f'<p class="muted">Recovered = disbursed, one shot or instalments, as the MIS counts it; collected includes booking fees on pending and '
                  f'dropped leads. LeadSquared has {len(self.rows)} open leads for this team; records that differ from the pool sheet are listed in section 9.</p>'
                + self.community_money()
                + "<h2>Open leads by payment route and status</h2>" + pt
                + '<p class="muted">Route read from each lead\'s payment plan as the readers wrote it. Leads with a payment date due '
                  'are in section 2 (the payment-dates calendar was removed on 19 Nov: it repeated that list).</p></div>')

    def community_money(self) -> str:
        """Community leads' own money from the Community Webinar Collection Sheet (the pool sheet MIS is bootcamps only)."""
        c = self.community_leads
        if not c:
            return ""
        by: dict[str, list] = {}
        for l in c:
            by.setdefault(l["bootcamp"], []).append(l)
        rows = []
        for b, ls in sorted(by.items()):
            done = sum(l["enrolled"] for l in ls)
            rows.append([e(b), str(len(ls)), str(done), f"{pct(done, len(ls))}%", str(sum(l["dropped"] for l in ls)),
                         lakh(sum(l["received"] for l in ls)), lakh(sum(l["balance"] for l in ls if not l["enrolled"] and not l["dropped"]))])
        done = sum(l["enrolled"] for l in c)
        rows.append(["<b>Community, November bookings</b>", f"<b>{len(c)}</b>", f"<b>{done}</b>", f"<b>{pct(done, len(c))}%</b>",
                     f'<b>{sum(l["dropped"] for l in c)}</b>', f'<b>{lakh(sum(l["received"] for l in c))}</b>',
                     f'<b>{lakh(sum(l["balance"] for l in c if not l["enrolled"] and not l["dropped"]))}</b>'])
        return ("<h2>Community leads (Community Webinar Collection Sheet)</h2>"
                + table([("Community batch", "34%"), ("Leads", "9%"), ("Enrolled", "10%"), ("Recovery", "10%"), ("Dropped", "9%"),
                         ("Received", "14%"), ("Balance on open leads", "14%")], rows, {i: "n" for i in range(1, 7)})
                + '<p class="muted">Enrolled from LeadSquared (Course Enrolled, Collections done or loan done); money is the community sheet\'s '
                  'own per lead.</p>')

    def overview(self) -> str:
        rows = []
        for c in self.callers:
            mine = [(l, j, p) for l, j, p in self.rows if l["caller"] == c]
            g = Counter(p["group"] for _, _, p in mine)
            past = [l for l in self.leads if l["caller"] == c and self.cut > l["deadline"]]
            iit = Counter(iit_kind(j.get("iit_pitch")) for _, j, _ in mine)
            rows.append([e(c), e(self.by_id_tl(c)[1]), str(len(mine))] + [str(g.get(k, 0) or "") for k in range(1, 10)]
                        + [f'{pct(sum(l["met48"] for l in past), len(past))}%' if past else "—",
                           f'{pct(sum(bool(l["first_pickup"] and l["first_pickup"] <= l["deadline"]) for l in past), len(past))}%' if past else "—",
                           str(sum(iit[k] for k in ("Interested", "Declined", "Pitched, undecided")))])
        head = ([("Caller", "12%"), ("TL", "9%"), ("Open", "5%")] + [(str(k), "4.3%") for k in range(1, 10)]
                + [("48-h score: real talk", "7%"), ("48 h: answered at all", "8%"), ("IIT pitched", "7%")])
        key = " · ".join(f"{k} {morning.GROUP_COLS[k]}" for k in morning.GROUPS)
        return ('<div class="page"><h2>4. Team overview</h2>' + table(head, rows, {i: "n" for i in range(2, 15)})
                + f'<p class="muted">Groups: {e(key)}.</p></div>')

    def objections(self) -> str:
        obj: dict[str, list] = {}
        for l, j, _ in self.rows:
            if j.get("objection") and j["status"] in ("counsel", "cold"):
                obj.setdefault(objection_kind(j["objection"]), []).append(l)
        orow = [[f"<b>{e(k)}</b>", str(len(v)), e(", ".join(f"{c} {n}" for c, n in Counter(l["caller"] for l in v).most_common(4)))]
                for k, v in sorted(obj.items(), key=lambda kv: -len(kv[1]))]
        kinds = ["Interested", "Pitched, undecided", "Declined", "Not pitched", "Not read yet"]
        irow = []
        for c in self.callers:
            k = Counter(iit_kind(j.get("iit_pitch")) for l, j, _ in self.rows if l["caller"] == c)
            if sum(k.values()):
                irow.append([e(c)] + [str(k[x] or "") for x in kinds])
        tot = Counter(iit_kind(j.get("iit_pitch")) for _, j, _ in self.rows)
        irow.append(["<b>Team</b>"] + [f"<b>{tot[x]}</b>" for x in kinds])
        interested = [(l, j) for l, j, _ in self.rows if iit_kind(j.get("iit_pitch")) == "Interested"]
        return ('<div class="page"><h1>8. Objections and the IIT add-on</h1>'
                "<h2>What is stopping the To be counselled and Cold leads</h2>"
                + table([("Objection", "30%"), ("Leads", "10%"), ("Callers with the most", "60%")], orow, {1: "n"})
                + '<p class="muted">Grouped from the objection the readers wrote for each lead; a lead counts once, under its first matching group.</p>'
                + "<h2>IIT add-on: pitched or not, by caller (open leads)</h2>"
                + table([("Caller", "30%")] + [(k, "14%") for k in kinds], irow, {i: "n" for i in range(1, 6)})
                + (f"<h2>Interested in the IIT add-on ({len(interested)})</h2>"
                   + table([("Lead", "30%"), ("What was said", "70%")], [[lead_cell(l), e(j["iit_pitch"])] for l, j in interested]) if interested else "")
                + "</div>")

    def cold_waiting_records(self) -> str:
        cold = [(l, j, p) for l, j, p in self.rows if p["group"] == 8]
        wait = sorted([(l, j) for l, j, p in self.rows if p["group"] == 9], key=lambda r: r[1].get("waiting_until") or "")
        # a refund or cancel request is handled in priority group 3; here it is only marked, not repeated (the user, 19 Nov)
        cr = [[lead_cell(l), '<i>Asked about a refund or cancelling: see section 2, group 3.</i>', "", ""]
              if objection_kind(j.get("objection")) == "Refund or cancel" else
              [lead_cell(l), e(p["why"] or j.get("last_talk") or "—"), e(j.get("objection") or "—"), e(j.get("next") or "WhatsApp first")]
              for l, j, p in cold]
        wr = [[lead_cell(l), e(datetime.strptime(j["waiting_until"][:10], "%Y-%m-%d").strftime("%a %d %b")), e(j.get("last_talk") or "—")] for l, j in wait]
        recs = []
        for l in self.bootcamp_leads:  # the pool sheet holds bootcamp students only
            s = self.students.get(re.sub(r"\D", "", l["phone"])[-10:])
            if not s:
                continue
            done = (s.get("status") or "").lower().startswith(("recovered", "disbursed", "one shot", "instal")) or (s.get("outcome") or "").lower() in ("recovered",)
            if done and not l["enrolled"] and not l["dropped"]:
                recs.append([lead_cell(l), e(f'Pool sheet: {s.get("status") or s.get("outcome")}'), "LeadSquared stage not Course Enrolled: move the stage."])
            elif l["enrolled"] and not done and (s.get("status") or "").lower() in ("pending", ""):
                recs.append([lead_cell(l), "LeadSquared: Course Enrolled", e(f'Pool sheet: {s.get("status") or "pending"}: update the sheet with the payment.')])
        missing = [l for l in self.bootcamp_leads if not l["enrolled"] and not l["dropped"] and not self.students.get(re.sub(r"\D", "", l["phone"])[-10:])]
        return ('<div class="page"><h2>9. Leads going cold, waiting list, records to fix</h2>'
                + f"<h2>Cold ({len(cold)}): WhatsApp first, then one call</h2>"
                + table([("Lead", "28%"), ("Why cold", "30%"), ("Objection", "20%"), ("Next step", "22%")], cr)
                + f"<h2>Waiting until an agreed date ({len(wait)}): do not call before it</h2>"
                + table([("Lead", "30%"), ("Until", "12%"), ("Last talk", "58%")], wr)
                + f"<h2>Records to fix ({len(recs)})</h2>"
                + (table([("Lead", "30%"), ("Found", "30%"), ("Fix", "40%")], recs) if recs else "<p>LeadSquared stages and the pool sheet agree for this team.</p>")
                + (f'<p class="muted">{len(missing)} open LeadSquared leads are not in the pool sheet\'s November list for this team (matched by phone).</p>'
                   if missing else "") + "</div>")

    def routine(self) -> str:
        day = [("09:45", "Log in; test your line with one call.", "Lists printed; payment links and EMI sheets ready."),
               ("10:00", "Huddle: read out your three for today.", ""),
               ("10:00–10:30", "Return every lead's call first.", "Check by 10:30."),
               ("10:30–12:00", "Follow-ups at their times; payments promised today.", "12:00: payments promised today done?"),
               ("12:00–13:30", "48-hour leads: first real talk; two dials, WhatsApp if unanswered.", "13:00: every 48-hour lead dialled twice?"),
               ("14:30–16:30", "Hot: payment mode and date on every talk; loan documents.", "15:00: morning follow-ups kept?"),
               ("16:30–18:00", "To be counselled; call-back requests; connected leads.", "17:00: Hot leads untouched today?"),
               ("18:00–20:00", "Evening follow-ups; second round on unreached 48-hour leads; cold leads on WhatsApp.", "19:30: dispositions and call-backs saved; 20:00 day close.")]
        rules = ["Check the lead's last talk and Notes before dialling; never ask again what the lead already told us.",
                 "Confirm the payment route and the date: one shot, card EMI, loan or instalments, and the day it will be paid.",
                 "For a loan, name the missing documents and the day they will be sent.",
                 "Pitch the IIT add-on where it fits; note the answer.",
                 "A refund or cancel question goes to your team leader the same day.",
                 "Save the disposition and the next call-back time before the next dial.",
                 "Anything the lead says on WhatsApp or email goes in a LeadSquared Note with the date and the channel.",
                 "Paid on the call? Move the lead to Course Enrolled and tell the team leader."]
        return ('<div class="page"><h1>10. The day, hour by hour</h1>'
                + table([("Time", "15%"), ("What callers do", "85%")], [[f"<b>{a}</b>", e(b)] for a, b, _ in day])
                + '<p class="muted">The team leader\'s checks and times are in section 3.</p>'
                + "<h1>On every call</h1><ol>" + "".join(f"<li>{e(r)}</li>" for r in rules) + "</ol></div>")

    def fresh_page(self) -> str:
        """Leads given to callers on the previous day: how many each caller has not dialled yet (Mayur's ask, 24 Nov 2026)."""
        fresh = [l for l in self.leads if l["allocated"].astimezone(IST).date() == self.yday]
        tried = lambda l: any(c["out"] and c["t"] >= l["allocated"] for c in l["calls"])
        by: dict[tuple, list] = {}
        for l in fresh:
            by.setdefault((l["tl"], l["caller"]), []).append(l)
        rows = []
        for (tl, c), ls in sorted(by.items()):
            n_not = sum(1 for l in ls if not tried(l))
            rows.append([e(tl), e(c), str(len(ls)), str(len(ls) - n_not), f'<span class="bad">{n_not}</span>' if n_not else "0",
                         str(sum(1 for l in ls if l["first_pickup"])), str(sum(1 for l in ls if l["real_talks"])), str(sum(l["enrolled"] for l in ls))])
        if not fresh:
            return f'<div class="page"><h1>Fresh leads given on {self.yday:%A %d %b}</h1><p>No leads were given to callers that day.</p></div>'
        not_tried = sorted((l for l in fresh if not tried(l) and not l["enrolled"] and not l["dropped"]), key=lambda l: (l["tl"], l["caller"], l["allocated"]))
        tot = [str(len(fresh)), str(sum(1 for l in fresh if tried(l))), f'<b>{len(fresh) - sum(1 for l in fresh if tried(l))}</b>',
               str(sum(1 for l in fresh if l["first_pickup"])), str(sum(1 for l in fresh if l["real_talks"])), str(sum(l["enrolled"] for l in fresh))]
        rows.append(["<b>Team</b>", ""] + [f"<b>{x}</b>" for x in tot])
        lst = table([("#", "4%"), ("Lead", "34%"), ("Team leader", "18%"), ("Given to caller", "16%"), ("Bootcamp", "28%")],
                    [[str(i), f'<b>{e(l["name"] or "—")}</b> <span class="ph">{e(l["phone"])}</span><br><span class="muted">{e(l["caller"])}</span>',
                      e(l["tl"]), f'{l["allocated"].astimezone(IST):%a %d %b %H:%M}', e(l["bootcamp"])] for i, l in enumerate(not_tried, 1)])
        return (f'<div class="page"><h1>Fresh leads given on {self.yday:%A %d %b}: {len(fresh)} leads, '
                f'{len(fresh) - sum(1 for l in fresh if tried(l))} not attempted yet</h1>'
                f'<p class="muted">Attempted = at least one dial after the lead reached the caller, up to the LeadSquared read of '
                f'{self.cut.astimezone(IST):%a %d %b %H:%M}. Their 48-hour deadline is 20:00 on {self.yday + timedelta(days=2):%a %d %b}.</p>'
                + table([("Team leader", "17%"), ("Caller", "19%"), ("Given", "8%"), ("Attempted", "10%"), ("Not attempted", "11%"),
                         ("Answered", "10%"), ("Real talk", "10%"), ("Enrolled", "9%")], rows, {i: "n" for i in range(2, 8)})
                + (f"<h2>Not attempted yet and still open ({len(not_tried)}): action these first</h2>" + lst if not_tried else "<p>Every fresh lead has been dialled.</p>")
                + "</div>")

    def body(self, banner: str = "") -> list[str]:
        """The sections after the cover and before the routine, for one team leader in a team-wise PDF."""
        def join(*parts: str) -> str:
            inner = [p.removeprefix('<div class="page">').removesuffix("</div>") for p in parts]
            return '<div class="page">' + "".join(inner) + "</div>"
        first = self.review_page().replace('<div class="page">', '<div class="page">' + banner, 1)
        return [first, self.priority(), join(self.checks(), self.overview()), self.feedback_page(), self.tracker(), self.money(),
                join(self.objections(), self.cold_waiting_records())]

    def html(self) -> str:
        def join(*parts: str) -> str:  # short sections share a page
            inner = [p.removeprefix('<div class="page">').removesuffix("</div>") for p in parts]
            return '<div class="page">' + "".join(inner) + "</div>"
        pages = [self.cover(), self.review_page(), self.priority(), join(self.checks(), self.overview()), self.feedback_page(),
                 self.tracker(), self.money(), join(self.objections(), self.cold_waiting_records()), self.routine()]
        return (f'<!doctype html><html><head><meta charset="utf-8"><title>{e(self.team)} call plan</title>'
                f'<style>{CSS}{EXTRA_CSS}</style></head><body>{"".join(pages)}</body></html>')


def teamwise_html(side: str, args: tuple, callerwise: list[dict]) -> str:
    """One PDF for a team, divided by team leader (Mayur's ask, 24 Nov 2026): the team cover, the fresh leads not yet
    attempted, then each TL's sections under a TL banner, and the routine once at the end."""
    team = TeamPDF(side, *args)
    parts = [team.cover(), team.fresh_page()]
    for tl in sorted({l["tl"] for l in team.leads}, key=lambda t: (next(l["ad"] for l in team.leads if l["tl"] == t), t)):
        t = TeamPDF(side, *args, tl=tl, callerwise=callerwise)
        callers = ", ".join(t.callers)
        banner = (f'<div class="band"><small>{e(team.team)} · AD {e(t.ads[0])}</small><h1>TL {e(tl)}</h1>'
                  f'<p>{len(t.rows)} open leads · callers: {e(callers)}</p></div>')
        parts += t.body(banner)
    parts.append(team.routine())
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{e(team.team)} call plan</title>'
            f'<style>{CSS}{EXTRA_CSS}</style></head><body>{"".join(parts)}</body></html>')


def load_feedback(run_dir: str) -> dict:
    out = {}
    for f in sorted(glob.glob(os.path.join(run_dir, "feedback", "out*.json"))):
        out.update(json.load(open(f)))
    return out


def build(run_dir: str, day: date, prev_plan: str, out_dir: str, transcripts_note: str = "", side: str | None = None,
          by_tl: bool = False, teamwise: bool = False) -> list[str]:
    cut = facts.read_at(run_dir)
    leads = [l for l in facts.collect(run_dir, cut) if not side or l["side"] == side]  # one team's run grades that team only
    judged = judged_on(os.path.join(run_dir, "judgements.json"), cut.astimezone(IST).date())
    rows = morning.plan(leads, judged, day, cut)
    prev = json.load(open(prev_plan))
    pool = os.path.join(run_dir, "bootcamp_pool.xlsx")
    _, as_of, mis = load_mis(pool, f"{day:%B %Y}")
    notes = {"transcripts": transcripts_note, "pool_read": datetime.fromtimestamp(os.path.getmtime(pool), IST).strftime("%d %b %H:%M"),
             "prev_cut": datetime.fromisoformat(prev["cut"]).astimezone(IST).strftime("%a %d %b %H:%M IST")}
    feedback = load_feedback(run_dir)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    callerwise = load_callerwise(pool, day) if by_tl or teamwise else []
    for s, team in TEAM_ADS.items():
        if side and s != side:
            continue
        students = load_students(pool, f"{day:%B %Y}", s)
        if teamwise:
            path = os.path.join(out_dir, f"Bootcamp collections plan - {team.split(' (')[0]} - {day:%a %d %b %Y}.pdf")
            write_pdf(teamwise_html(s, (leads, rows, prev, cut, day, feedback, (as_of, mis), students, notes), callerwise), path)
            paths.append(path)
            continue
        for tl in (sorted({l["tl"] for l in leads if l["side"] == s}) if by_tl else [None]):
            doc = TeamPDF(s, leads, rows, prev, cut, day, feedback, (as_of, mis), students, notes, tl, callerwise).html()
            name = team.split(" (")[0] + (f" - TL {tl}" if tl else "")
            # the team PDF keeps its name; per-TL PDFs (Anmol's team, from 23 Nov) are named for the TL
            path = os.path.join(out_dir, (f"Collections plan - {name}" if tl else f"Bootcamp collections plan - {name}") + f" - {day:%a %d %b %Y}.pdf")
            write_pdf(doc, path)
            paths.append(path)
    return paths


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir"); ap.add_argument("day"); ap.add_argument("prev_plan"); ap.add_argument("out_dir")
    ap.add_argument("transcripts_note", nargs="?", default="")
    ap.add_argument("--side", help='"Mayur" or "Puja / Anmol": one team only')
    ap.add_argument("--by-tl", action="store_true", help="one PDF per team leader, with today's act-now leads on page 1")
    ap.add_argument("--teamwise", action="store_true", help="one PDF divided by team leader, with the fresh leads not yet attempted")
    a = ap.parse_args()
    for p in build(a.run_dir, date.fromisoformat(a.day), a.prev_plan, a.out_dir, a.transcripts_note, a.side, a.by_tl, a.teamwise):
        print(p)
