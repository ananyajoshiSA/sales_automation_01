"""Management report for a collection team's head and AD (Puja Malik and Anmol Gakhar for Anmol's team, asked
25 Nov 2026): no lead lists, only the data points where they need to act, in plain words, at most three pages.
Page 1: what to do today (five points from fixed rules), this month so far, and what worked and what did not since
yesterday morning's pool sheet. Page 2: each team leader's team against its aims, money waiting on a decision, and
money still to collect by bootcamp. Page 3: callers who need attention, compliance, records to fix, and what leads
are telling us. The money views follow Puja's own daily analysis (8 Oct 2026), this month's bootcamps only.

Every number shared with this morning's team leader reports is checked against them before the PDF is written
(``checks.py``); a difference stops the build.

    python -m analytics.collection_daily.management <run_dir> <day YYYY-MM-DD> <previous-day pool.xlsx> <week-ago pool.xlsx> <out_dir> [--side "Puja / Anmol"] [--compliance file.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from analytics.collection_daily import facts, morning, review
from analytics.collection_daily.__main__ import judged_on
from analytics.collection_daily.pdf import (CSS, EXTRA_CSS, TARGET, TEAM_ADS, TeamPDF, e, lakh, load_callerwise, load_feedback,
                                            objection_kind, pct, promised, route, same_person, table, tl_mis)
from analytics.collection_reports import write_pdf
from analytics.collections.pool_mis import load_mis, load_students
from analytics.collections.tags import tag_date
from integrations.timeutil import IST

SETTLED_DAYS = 21  # a bootcamp this old has had most of its collection time: fair to set against October's final figure
HEADS = {"Puja / Anmol": "Puja Malik and Anmol Gakhar", "Mayur": "Mayur Sachdeva, Sahil Arora and Raunak Mandilwar"}
MGMT_CSS = """
body { font-size:10px; line-height:1.45; } h2 { font-size:13px; margin:14px 0 6px; } h3 { font-size:10.5px; margin:4px 0; }
table { font-size:9px; margin:2px 0 6px; } td, th { padding:4px 6px; } .band { padding:12px 16px; margin-bottom:8px; }
.band h1 { font-size:18px; } .tiles6 .tile b { font-size:13px; } .tiles6 .tile span { font-size:8.6px; }
.two { display:grid; grid-template-columns: 1fr 1fr; gap:12px; } ol.acts { margin:4px 0 6px 18px; padding:0; } ol.acts li { margin:5px 0; }
ul.notes { margin:2px 0 4px 16px; padding:0; color:#52514e; font-size:8.6px; } ul.notes li { margin:1px 0; }
.amber { padding:5px 9px; margin:4px 0; } tr { page-break-inside: avoid; } h2 { page-break-after: avoid; }
"""
RECOVERED = ("disbursed", "one shot", "installments")  # the MIS counts these as recovered (checked 25 Nov: 228 = 145 + 39 + 44)
OPEN = "booking fees received"


def sheet_rows(path: str, month: str | None, side: str) -> list[dict]:
    """The pool sheet's Mastersheet rows of ``month`` ("November 2026", or every month when None) on ``side``."""
    from analytics.collections.sheets import read_bootcamp
    key = datetime.strptime(month, "%B %Y").strftime("%Y-%m") if month else None
    rows, _ = read_bootcamp(path)
    return [r for r in rows if (not key or r["month"] == key) and (r.get("vertical") or "").strip() and r["vertical"].lower() in side.lower()]


def cash_moves(new: list[dict], old: list[dict]) -> list[tuple[str, str, float]]:
    """(owner, bootcamp, rupees) received between two downloads of the pool sheet, per lead already in the older one.
    A lead added since then is left out: its booking fee was paid at the sale, not collected by the team."""
    before, now, who = defaultdict(float), defaultdict(float), {}
    for r in old:
        before[(r["phone"], r["bootcamp_raw"])] += r["received"] or 0
    for r in new:
        k = (r["phone"], r["bootcamp_raw"])
        now[k] += r["received"] or 0
        who[k] = (r["owner"] or "", r["bootcamp_raw"])
    return [(*who[k], now[k] - before[k]) for k in now if k in before and abs(now[k] - before[k]) >= 1]


def short(bootcamp: str) -> str:
    """"Bootcamp | Independent Director - Nov 14, Nov 15, Nov 16 , 2026" -> "Independent Director · 14 Nov"."""
    d = tag_date(bootcamp)
    name = bootcamp.removeprefix("Bootcamp | ").split(" - ")[0].strip()
    return f"{name} · {datetime.strptime(d, '%Y-%m-%d'):%d %b}" if d else name


SINGULAR = [(r"(?<![\d,.])1 leads have", "1 lead has"), (r"(?<![\d,.])1 leads are", "1 lead is"),
            (r"(?<![\d,.])1 leads still owe", "1 lead still owes"), (r"(?<![\d,.])1 leads", "1 lead"),
            (r"(?<![\d,.])1 callers who have had", "1 caller who has had"), (r"(?<![\d,.])1 callers", "1 caller"),
            (r"(?<![\d,.])1 real talks", "1 real talk"), (r"(?<![\d,.])1 calls\b", "1 call")]


def singular(html: str) -> str:
    """"1 leads" -> "1 lead" (and the like) everywhere in the finished page."""
    import re
    for a, b in SINGULAR:
        html = re.sub(a, b, html)
    return html


def first(name: str) -> str:
    return name.split()[0] if name else "—"


def red(v: str, bad: bool) -> str:
    return f'<span class="bad">{v}</span>' if bad else v


def settled(rows: list[dict], day: date) -> list[dict]:
    """Caller-wise MIS rows of bootcamps held SETTLED_DAYS+ days before ``day``: this month's newer bootcamps are still
    being collected, so set against a finished month they would always look worse."""
    return [r for r in rows if (d := tag_date(r["bootcamp"])) and d <= (day - timedelta(days=SETTLED_DAYS)).isoformat()]


def untouched(leads: list[dict], rows: list[tuple], day: date) -> int:
    """Open leads, not cold and not waiting, given to the caller before the last 3 days and dialled on none of them.
    A lead given yesterday and not yet dialled is a fresh lead (records section), not an untouched one."""
    since = day - timedelta(days=3)
    judged = {l["lead_id"]: j for l, j, _ in rows}
    return sum(1 for l in leads if (j := judged.get(l["lead_id"])) and j["status"] != "cold"
               and not (j.get("waiting_until") or "") > day.isoformat()
               and l["allocated"].astimezone(IST).date() < since
               and (not l["last_dial"] or l["last_dial"].astimezone(IST).date() < since))


def leads_dialled(leads: list[dict], caller: str, day: date) -> int:
    """Unique collection leads the caller dialled on ``day`` (from the same leads the dials are counted on)."""
    return sum(1 for l in leads if any(c["out"] and c["by"] == caller and review.on(c["t"], day) for c in l["calls"]))


def reached_payment(j: dict) -> bool:
    return j["status"] == "hot" or (j["status"] in ("counsel", "connected") and route(j.get("payment")) != "Not decided")


def recovery(rows: list[dict], callers) -> int | None:
    mine = [r for r in rows if any(same_person(r["caller"], c) for c in callers)]
    n = sum(r["all_teams"] for r in mine)
    return pct(sum(r["recovered"] for r in mine), n) if n else None


class Management:
    def __init__(self, side: str, run_dir: str, day: date, prev_pool: str, week_ago_pool: str, compliance: list[dict],
                 paid_as_enrolled: bool = False):
        self.side, self.day, self.yday = side, day, day - timedelta(days=1)
        self.cut = facts.read_at(run_dir)
        leads = [l for l in facts.collect(run_dir, self.cut) if l["side"] == side]
        judged = judged_on(os.path.join(run_dir, "judgements.json"), self.cut.astimezone(IST).date())
        self.rows = morning.plan(leads, judged, day, self.cut)
        self.leads = leads
        pool = os.path.join(run_dir, "bootcamp_pool.xlsx")
        _, self.as_of, mis = load_mis(pool, f"{day:%B %Y}")
        self.mis = [r for r in mis if r["side"].lower() in side.lower()]
        _, self.week_as_of, week = load_mis(week_ago_pool, f"{day:%B %Y}")
        self.week = [r for r in week if r["side"].lower() in side.lower()]
        self.cw_now = load_callerwise(pool, day)
        self.cw_settled = settled(self.cw_now, day)
        self.cw_oct = load_callerwise(pool, (day.replace(day=1) - timedelta(days=1)).replace(day=1))
        self.students = load_students(pool, f"{day:%B %Y}", side)
        self.sheet = sheet_rows(pool, f"{day:%B %Y}", side)
        # every month of the earlier sheet: on 24 Nov ~38 November rows carried a mistyped year (2028-11 … 2098-11), fixed
        # a day later; matched on this month only, they read as new leads and their money was left out
        prev = sheet_rows(prev_pool, None, side)
        self.moves = cash_moves(self.sheet, prev)
        self.in_prev = {(r["phone"], r["bootcamp_raw"]) for r in prev}
        read = lambda f: datetime.fromtimestamp(os.path.getmtime(f), IST)
        self.pool_read, self.prev_read = read(pool), read(prev_pool)
        self.window = (f"yesterday {self.prev_read:%H:%M}" if self.prev_read.date() == day - timedelta(days=1)
                       else f"{self.prev_read:%a %d %b %H:%M}")
        # sheet problems are shown at the top of the report, not worked around quietly
        odd = lambda rows: sum(1 for r in rows if r["month"] and (r["month"] == "unknown" or r["month"] > f"{day.year + 1}"))
        self.data_notes = []
        if gaps := self.pool_vs_mis():
            self.data_notes.append(f"The pool sheet's MIS tab (as of {self.as_of}) is behind its Mastersheet for "
                                   + "; ".join(gaps) + " (Mastersheet against MIS). Lead counts and money follow the MIS, as in the "
                                   "team leaders' reports; the bootcamp table and money received follow the Mastersheet.")
        for rows, when in ((sheet_rows(pool, None, side), self.pool_read), (prev, self.prev_read)):
            if k := odd(rows):
                self.data_notes.append(f"The pool sheet downloaded {when:%a %d %b %H:%M} has {k} rows with an impossible date (a year after "
                                       f"{day.year + 1}, or none). They were matched to their leads by phone number and bootcamp"
                                       + (", and the sheet should be corrected." if when == self.pool_read else "; today's download is correct."))
        self.compliance = [c for c in compliance if c.get("side") == side]
        args = (leads, self.rows, {"day": "", "leads": {}}, self.cut, day, load_feedback(run_dir),
                (self.as_of, mis), self.students, {"transcripts": "", "pool_read": "", "prev_cut": ""})
        self.team = TeamPDF(side, *args)
        # leads paid in full per the pool sheet but still open in LeadSquared, counted as enrolled when asked (the user,
        # 28 Nov 2026); the checks allow for exactly these against the team leaders' reports
        self.moved: dict[str, str] = {}
        self.moved_status: Counter = Counter()
        if paid_as_enrolled:
            self.moved = {l["lead_id"]: l["tl"] for l, *_ in self.rows if self.team.balance(l) == 0}
            self.moved_status = Counter(j["status"] for l, j, _ in self.rows if l["lead_id"] in self.moved)
            self.rows = [r for r in self.rows if r[0]["lead_id"] not in self.moved]
            for l in leads:
                if l["lead_id"] in self.moved:
                    l["enrolled"] = True
            args = (leads, self.rows, *args[2:])
            self.team = TeamPDF(side, *args)
            if self.moved:
                self.data_notes.append(f"{len(self.moved)} leads paid in full according to the pool sheet, but still open in LeadSquared, "
                                       "are counted as enrolled in this report, so open leads are that many fewer than in the team "
                                       "leaders' reports.")
        self.tls = sorted({l["tl"] for l in leads})
        self.by_tl = {tl: TeamPDF(side, *args, tl=tl, callerwise=self.cw_now) for tl in self.tls}
        # each TL's numbers come from that TL's own PDF (TeamPDF.stats): a caller's calls on any of the team's leads

    # -- helpers ----------------------------------------------------------------------------------------------------
    def away(self, caller: str) -> bool:
        """On leave yesterday or today (``data/coll_daily/leave.json``): not judged, not asked for collections."""
        from analytics.collection_daily.caller_trend import load_leave, on_leave
        leave = load_leave()
        return any(on_leave(leave, c, d) for c in leave if same_person(c, caller) for d in (self.yday, self.day))

    def tl_of(self, caller: str) -> str:
        return next((l["tl"] for l in self.leads if same_person(l["caller"], caller)), "")

    def cash_by_caller(self) -> Counter:
        """Rupees since the previous day's sheet for every caller holding November leads in the sheet (0 included)."""
        c = Counter({r["owner"]: 0 for r in self.sheet if r["owner"]})
        for owner, _, amt in self.moves:
            c[owner] += amt
        return c

    def camps(self) -> list[dict]:
        """One row per bootcamp: leads, recovered, open leads and rupees, cash since the previous sheet, who holds the open money."""
        by: dict[str, dict] = {}
        for r in self.sheet:
            b = by.setdefault(r["bootcamp_raw"], {"name": short(r["bootcamp_raw"]), "day": tag_date(r["bootcamp_raw"]) or "", "n": 0,
                                                  "rec": 0, "open": 0, "open_rs": 0.0, "cash": 0.0, "holders": defaultdict(lambda: [0, 0.0]),
                                                  "tracked": 0})
            b["tracked"] += (r["phone"], r["bootcamp_raw"]) in self.in_prev
            st = (r["status"] or "").lower()
            b["n"] += 1
            b["rec"] += st in RECOVERED
            if st == OPEN:
                b["open"] += 1
                b["open_rs"] += r["balance"] or 0
                h = b["holders"][r["owner"] or "—"]
                h[0] += 1
                h[1] += r["balance"] or 0
        for _, camp, amt in self.moves:
            if camp in by:
                by[camp]["cash"] += amt
        return sorted(by.values(), key=lambda b: -b["open_rs"])

    def judged(self) -> list[dict]:
        """Bootcamps fair to judge on a day's cash and on recovery: held a week or more ago and already in the previous sheet
        (a bootcamp added to the sheet today has had no chance to bring in money since the last one)."""
        week = (self.day - timedelta(days=7)).isoformat()
        return [b for b in self.camps() if b["day"] and b["day"] <= week and 2 * b["tracked"] >= b["n"] and b["n"] >= 10]

    def untouched(self, ls) -> int:
        return untouched(ls, self.rows, self.day)

    def money_groups(self, t: TeamPDF) -> dict:
        """Due today or overdue and refund asks exactly as on the TL's page 1 (sections 1 and 3); loans stuck and the
        week's promises from the same rows."""
        pay, _, brink, paid = t.today_groups()
        bal = lambda l: t.balance(l) or 0
        g = {"due": [bal(l) for l, *_ in pay], "refund": [bal(l) for l, j, _, why in brink if why.startswith("Asked for a refund")],
             "loan": [], "week": Counter()}
        skip = {l["lead_id"] for l, *_ in paid} | {l["lead_id"] for l, j, _, why in brink if why.startswith("Asked for a refund")}
        for l, j, p in t.rows:
            if l["lead_id"] in skip:
                continue
            d = promised(j.get("payment"), self.day.year)
            if j["status"] == "hot" and route(j.get("payment")) == "Loan / finance" and (not d or d < self.day):
                g["loan"].append(bal(l))
            if j["status"] in ("hot", "counsel") and d and self.day < d <= self.day + timedelta(days=6):
                g["week"][d] += bal(l)
        return g

    # -- numbers shared by the pages and the checks -----------------------------------------------------------------
    def tl_numbers(self, t: TeamPDF) -> dict:
        ls = t.leads
        past = [l for l in ls if self.cut > l["deadline"]]
        st = Counter()
        for c in t.callers:
            st.update(t.stats.get(c, Counter()))
        talked = [j for l, j, _ in t.rows if l["real_talks"]]
        g = self.money_groups(t)
        _, _, _, paid = t.today_groups()
        return {"open": len(t.rows), "enrolled": st["enrolled"], "met48": sum(l["met48"] for l in past), "past48": len(past),
                "dials": st["dials"], "real": st["real"], "fu_kept": st["fu_kept"], "fu_due": st["fu_due"],
                "in_unreturned": st["in_unreturned"], "in_missed": st["in_missed"],
                "saved": sum(1 for l in ls for f in l["follow_ups"] if f.get("source", "lsq") == "lsq" and f["set_at"].astimezone(IST).date() == self.yday),
                "untouched": self.untouched(ls), "talked": len(talked), "agreed": sum(map(reached_payment, talked)),
                "rec_old": recovery(self.cw_settled, t.callers), "rec_oct": recovery(self.cw_oct, t.callers),
                "due_n": len(g["due"]), "due_rs": sum(g["due"]), "paid": len(paid),
                "moved": sum(1 for tl in self.moved.values() if tl == t.tl),
                "callers": {c: (t.stats.get(c, Counter())["dials"], t.stats.get(c, Counter())["real"]) for c in t.callers}}

    def sheet_totals(self) -> dict:
        sh = self.sheet
        st = lambda r: (r["status"] or "").lower()
        return {"n": len(sh), "price": sum(r["price"] or 0 for r in sh), "got": sum(r["received"] or 0 for r in sh),
                "rec": sum(1 for r in sh if st(r) in RECOVERED), "open": sum(1 for r in sh if st(r) == OPEN),
                "open_rs": sum(r["balance"] or 0 for r in sh if st(r) == OPEN),
                "inst": sum(1 for r in sh if st(r) == "installments" and (r["balance"] or 0) > 0),
                "inst_rs": sum(r["balance"] or 0 for r in sh if st(r) == "installments"),
                "drop": sum(1 for r in sh if st(r) == "drop"), "drop_rs": sum(r["balance"] or 0 for r in sh if st(r) == "drop")}

    # -- page 1 -----------------------------------------------------------------------------------------------------
    def month_totals(self) -> dict:
        """Leads and money as the pool sheet's MIS tab gives them, the same figures as the TL reports; the pool's
        price, instalments due and money lost on drops come from the Mastersheet (the MIS has no such columns)."""
        s = self.sheet_totals()
        mis = lambda k: sum(r[k] for r in self.mis)
        return {**s, "n": mis("all_teams"), "rec": mis("recovered"), "open": mis("pending"), "drop": mis("dropped"),
                "got": mis("recovered_amount"), "open_rs": mis("pending_amount")}

    def pool_vs_mis(self) -> list[str]:
        """Bootcamps where the pool sheet's Mastersheet and its MIS tab disagree (the MIS is refreshed less often)."""
        from analytics.collections.pool_mis import course_of
        by: dict = defaultdict(Counter)
        for r in self.sheet:
            st = (r["status"] or "").lower()
            b = by[(tag_date(r["bootcamp_raw"]), course_of(r["bootcamp_raw"]))]
            b["leads"] += 1
            b["dropped"] += st == "drop"
            b["pending"] += st == OPEN
            b["rupees"] += r["received"] or 0
        out = []
        for m in self.mis:
            b = by.pop((m["day"], m["course"]), Counter())
            diff = [f"{k} {b[k]:,.0f} against {v:,.0f}" for k, v in
                    (("leads", m["all_teams"]), ("dropped", m["dropped"]), ("pending", m["pending"]))  # new payments: the cash section
                    if round(b[k]) != round(v)]
            if diff:
                out.append(f"{short(m['name'])} ({', '.join(diff)})")
        out += [f"{k[1]} · {k[0]}: in the Mastersheet, not in the MIS" for k in by]
        return out

    def glance(self) -> str:
        s = self.month_totals()
        end = (self.day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        days_left = (end - self.day).days + 1
        need = max(0, math.ceil(TARGET * s["n"] / 100) - s["rec"])
        names = {r["name"] for r in self.week}
        now = [r for r in self.mis if r["name"] in names]
        wk = pct(sum(r["recovered"] for r in self.week), sum(r["all_teams"] for r in self.week))
        nw = pct(sum(r["recovered"] for r in now), sum(r["all_teams"] for r in now))
        com = [l for l in self.leads if l.get("community")]
        tiles = [(f"{s['rec']} of {s['n']} leads recovered ({pct(s['rec'], s['n'])}%)",
                  f"Target {TARGET}%. A week ago, the same bootcamps were at {wk}%; now they are at {nw}%."),
                 (f"{need} more leads to recover", f"to reach {TARGET}% by {end:%a %d %b}: about {math.ceil(need / days_left)} a day for {days_left} days."),
                 (f"{lakh(s['got'])} collected", f"out of {lakh(s['price'])} for this month's bootcamps ({pct(s['got'], s['price'])}%)."),
                 (f"{lakh(s['open_rs'])} still to collect", f"from {s['open']} leads who have paid only the booking fee. Another "
                  f"{lakh(s['inst_rs'])} is due on {s['inst']} instalment plans."),
                 (f"{s['drop']} leads dropped", f"{lakh(s['drop_rs'])} lost this month."),
                 (f"{sum(l['enrolled'] for l in com)} of {len(com)} community leads enrolled", "November community bookings (not in the pool sheet).")]
        return ("<h2>This month so far</h2>" + '<div class="tiles6">'
                + "".join(f'<div class="tile"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in tiles) + "</div>")

    def actions(self) -> str:
        """Five action points for the head of the team, each from a fixed rule over this report's own numbers."""
        cash = self.cash_by_caller()
        held = Counter()
        for r in self.sheet:
            if (r["status"] or "").lower() == OPEN:
                held[r["owner"]] += r["balance"] or 0
        zero = sorted((o for o, v in cash.items() if v <= 0 and held[o] > 0 and not self.away(o)), key=lambda o: -held[o])
        out = []
        if zero:
            out.append(f"<b>Speak to the {len(zero)} callers who have had no payment since {e(self.window)}.</b> Ask each one what they "
                       "will collect today. Start with those who have the most money still to collect: "
                       + ", ".join(f"{e(first(o))} ({lakh(held[o])})" for o in zero) + ".")
        due = {tl: self.money_groups(t) for tl, t in self.by_tl.items()}
        n, rs = sum(len(g["due"]) for g in due.values()), sum(sum(g["due"]) for g in due.values())
        if n:
            out.append(f"<b>{n} leads ({lakh(rs)}) promised to pay by today, or are late.</b> At 12:00, ask each team leader how many have paid: "
                       + ", ".join(f"{e(tl)} {len(g['due'])} leads ({lakh(sum(g['due']))})" for tl, g in sorted(due.items(), key=lambda x: -sum(x[1]["due"]))
                                   if g["due"])
                       + ".")
        rf = sum(len(g["refund"]) for g in due.values())
        if rf:
            out.append(f"<b>{rf} leads ({lakh(sum(sum(g['refund']) for g in due.values()))}) asked for a refund or to cancel.</b> Decide today: "
                       "keep them with instalments or a later batch, or let them go. Their names are " + ("in each team leader's section of the "
                       "team report (priority leads)." if self.side == "Mayur" else "on page 1 of each team leader's report."))
        stuck = [b for b in self.judged() if b["cash"] <= 0 and b["open"]]
        if stuck:
            b = stuck[0]
            top = sorted(b["holders"].items(), key=lambda h: -h[1][1])[:3]
            out.append(f"<b>{e(b['name'])}: {b['open']} leads still owe {lakh(b['open_rs'])}, and no payment has come in since {e(self.window)}.</b> "
                       "Most of this money is with " + ", ".join(f"{e(first(o))} ({h[0]} leads, {lakh(h[1])})" for o, h in top) + ".")
        fresh = [l for l in self.leads if l["allocated"].astimezone(IST).date() == self.yday
                 and not any(c["out"] and c["t"] >= l["allocated"] for c in l["calls"])]
        if fresh:
            per = Counter(l["tl"] for l in fresh)
            out.append(f"<b>{len(fresh)} leads given to callers on {self.yday:%A} had not been called by {self.cut.astimezone(IST):%H:%M} today</b> ("
                       + ", ".join(f"{k} in {e(tl)}'s team" for tl, k in per.most_common()) + "). Get them called by 11:00.")
        return '<h2>What to do today</h2><ol class="acts">' + "".join(f"<li>{x}</li>" for x in out[:5]) + "</ol>"

    def worked(self) -> str:
        cash = self.cash_by_caller()
        total = sum(a for *_, a in self.moves)
        best = [(o, v) for o, v in cash.most_common() if v > 0]
        camps = sorted(self.camps(), key=lambda b: -b["cash"])
        enrolled = Counter(l["caller"] for l in self.leads if review.on(l.get("enrolled_at"), self.yday))
        good = [("Money received", f"{lakh(total)} since {e(self.window)}"),
                ("Brought in the most", ", ".join(f"{e(first(o))} {lakh(v)}" for o, v in best[:4]) or "No one"),
                ("Bootcamp that paid the most", f"{e(camps[0]['name'])}: {lakh(camps[0]['cash'])}" if camps and camps[0]["cash"] > 0 else "None"),
                (f"Enrolled on {self.yday:%A}", f"{sum(enrolled.values())} leads: " + ", ".join(f"{e(first(c))} {k}" for c, k in enrolled.most_common())
                 if enrolled else "None")]
        zero = sorted(o for o, v in cash.items() if v <= 0 and not self.away(o))
        stuck = [b for b in self.judged() if b["cash"] <= 0 and b["open"]]
        low = [b for b in self.judged() if b["open"]]
        low = min(low, key=lambda b: (b["rec"] / b["n"], -b["open_rs"])) if low else None
        bad = [("No payment received", f"{len(zero)} of {len(cash)} callers: " + ", ".join(e(first(o)) for o in zero) if zero else "Every caller had a payment"),
               ("Lowest recovery", f"{e(low['name'])}: {pct(low['rec'], low['n'])}% of leads recovered; {low['open']} leads still owe {lakh(low['open_rs'])}"
                if low else "—"),
               ("Bootcamps with no payment", "<br>".join(f"{e(b['name'])}: {lakh(b['open_rs'])} still to collect" for b in stuck[:3]) or "None")]
        tb = lambda rows: ('<table>' + "".join(f'<tr><td style="width:36%"><b>{e(a)}</b></td><td>{b}</td></tr>' for a, b in rows) + "</table>")
        return ('<h2>Since yesterday morning</h2><div class="two"><div><h3 class="ok">What worked</h3>' + tb(good)
                + '</div><div><h3 class="bad">What did not work</h3>' + tb(bad) + "</div></div>"
                + f'<p class="muted">Money received means payments entered in the pool sheet between {self.prev_read:%a %d %b %H:%M} and '
                  f'{self.pool_read:%a %d %b %H:%M} IST, the two morning downloads. Finance enters them, so a payment made but not yet entered '
                  'is not counted. Booking fees of new leads are not counted. Enrolments are from '
                  + ("the team's enrolment sheet and LeadSquared" if self.side == "Mayur" else "LeadSquared")
                  + '. Bootcamps held in the last 7 days, and those with fewer than 10 leads, are left out of "lowest recovery" '
                  'and "no payment".</p>')

    # -- page 2 -----------------------------------------------------------------------------------------------------
    def scorecard(self) -> str:
        n = {tl: self.tl_numbers(t) for tl, t in self.by_tl.items()}
        tls = list(n)
        yd = self.yday.strftime("%A")

        def row(label, aim, cell):
            return [label] + [cell(n[tl]) for tl in tls] + [aim]
        rows = [row("Open leads to work today", "", lambda x: str(x["open"])),
                row(f"Enrolled on {yd}", "", lambda x: str(x["enrolled"])),
                row("New leads who had a real talk within 48 hours", "90% or more",
                    lambda x: red(f"{pct(x['met48'], x['past48'])}%", pct(x["met48"], x["past48"]) < 90) + f" <span class=muted>({x['met48']} of {x['past48']})</span>"),
                row("Leads not called for 3 days or more", "none", lambda x: red(str(x["untouched"]), x["untouched"] > 0)),
                row(f"Leads who called us on {yd} and were not called back", "none",
                    lambda x: red(f"{x['in_unreturned']} of {x['in_missed']}", x["in_unreturned"] > 0)),
                row(f"Next call times saved in LeadSquared after a talk on {yd}", "", lambda x: f"{x['saved']} <span class=muted>(after {x['real']} real talks)</span>"),
                row(f"Follow-ups due on {yd} (saved in LeadSquared or agreed on a call) made on time", "all", lambda x: red(f"{x['fu_kept']} of {x['fu_due']}", x["fu_kept"] < x["fu_due"])),
                row("Leads who talked and agreed how they will pay", "",
                    lambda x: f"{pct(x['agreed'], x['talked'])}% <span class=muted>({x['agreed']} of {x['talked']})</span>"),
                row("Recovery on the bootcamps of 24 and 31 Oct", "October or better",
                    lambda x: red(f"{x['rec_old']}%" if x["rec_old"] is not None else "—",
                                  x["rec_old"] is not None and x["rec_oct"] is not None and x["rec_old"] < x["rec_oct"] - 5)),
                row("Recovery in October, same callers (final)", "", lambda x: f"{x['rec_oct']}%" if x["rec_oct"] is not None else "—")]
        return ("<h2>How each team leader's team is doing</h2>"
                + table([("", "34%")] + [(e(tl) + (" (on leave)" if self.away(tl) else ""), "16%") for tl in tls] + [("Aim", "18%")], rows,
                        {i: "n" for i in range(1, len(tls) + 1)})
                + '<ul class="notes"><li>Red means the team is short of the aim.</li>'
                  '<li>A real talk is an answered call of 2 minutes or more.</li>'
                  '<li>"Not called for 3 days or more" counts open leads, given at least 3 days ago, that are not cold or waiting for a later date.</li>'
                  '<li>"Agreed how they will pay" counts leads who talked and are Hot, or have chosen a loan, card or UPI. A cold lead never counts.</li>'
                  f'<li>Recovery uses the bootcamps held at least {SETTLED_DAYS // 7} weeks ago, so it can be compared with October\'s final figure. '
                  'Newer bootcamps are still being collected. Red means 5 or more points below October.</li></ul>')

    def money(self) -> str:
        rows, tot = [], {"due": [], "refund": [], "loan": [], "week": Counter()}
        for tl, t in self.by_tl.items():
            g = self.money_groups(t)
            for k in ("due", "refund", "loan"):
                tot[k] += g[k]
            tot["week"].update(g["week"])
            rows.append([f"<b>{e(tl)}</b>" + (" (on leave)" if self.away(tl) else "")] + [f"{len(g[k])} leads · {lakh(sum(g[k]))}" for k in ("due", "refund", "loan")]
                        + [lakh(sum(g["week"].values()))])
        rows.append(["<b>Whole team</b>"] + [f"<b>{len(tot[k])} leads · {lakh(sum(tot[k]))}</b>" for k in ("due", "refund", "loan")]
                    + [f"<b>{lakh(sum(tot['week'].values()))}</b>"])
        week = ", ".join(f"{lakh(v)} on {d:%A}" for d, v in sorted(tot["week"].items()))
        return ("<h2>Money waiting on a decision</h2>"
                + table([("Team leader", "16%"), ("Promised to pay by today, or late", "22%"), ("Asked for a refund or to cancel: you decide", "22%"),
                         ("Loan not done, and the date has passed or was never given", "22%"), ("Promised later this week", "18%")], rows)
                + '<ul class="notes">'
                + (f"<li>Promised later this week: {e(week)}.</li>" if week else "")
                + "<li>A loan lead whose date has passed is counted in both the first and the third column.</li>"
                  "<li>The first two columns are the same leads as page 1 of each team leader's report. Amounts are what each lead still owes.</li></ul>")

    def bootcamps(self) -> str:
        rows, week = [], (self.day - timedelta(days=7)).isoformat()
        for b in sorted(self.camps(), key=lambda b: b["day"]):
            top = sorted(b["holders"].items(), key=lambda h: -h[1][1])[:3]
            new = 2 * b["tracked"] < b["n"]
            rows.append([f"<b>{e(b['name'])}</b>", str(b["n"]), red(f"{pct(b['rec'], b['n'])}%", pct(b["rec"], b["n"]) < 50 and b["day"] <= week),
                         f"{b['open']} · {lakh(b['open_rs'])}", "new" if new else red(lakh(b["cash"]), b["cash"] <= 0 and b["open"] > 0),
                         ", ".join(f"{e(first(o))} {h[0]} ({lakh(h[1])})" for o, h in top) or "—"])
        return (f"<h2>Money still to collect, by bootcamp</h2>"
                + table([("Bootcamp", "25%"), ("Leads", "7%"), ("Recovered", "9%"), ("Still to collect (leads · ₹)", "15%"),
                         (f"Received since {e(self.window)}", "12%"), ("Callers with the most to collect: leads (₹)", "32%")], rows, {i: "n" for i in range(1, 5)})
                + '<ul class="notes"><li>"Still to collect" counts leads who have paid only the booking fee. "Recovered" counts leads whose loan was '
                  'disbursed, who paid in one go, or who are on instalments, as in the pool sheet MIS.</li>'
                  '<li>Red means under 50% recovered (for bootcamps held a week ago or more), or no payment since yesterday morning.</li></ul>')

    # -- page 3 -----------------------------------------------------------------------------------------------------
    def callers(self) -> str:
        rows, cash = [], self.cash_by_caller()
        yd = self.yday.strftime("%A")
        for tl, t in self.by_tl.items():
            for c in t.callers:
                if self.away(c):
                    continue  # on leave: other callers cover the leads
                s = t.stats.get(c, Counter())
                mine = [l for l in t.leads if l["caller"] == c]
                past = [l for l in mine if self.cut > l["deadline"]]
                s48 = pct(sum(l["met48"] for l in past), len(past)) if past else None
                unt = self.untouched(mine)
                ans = pct(s["answered"], s["dials"]) if s["dials"] else None
                rn, ro = recovery(self.cw_settled, [c]), recovery(self.cw_oct, [c])
                why = []
                if s["dials"] < 20:
                    why.append(f"Made {s['dials']} calls on {yd}, fewer than 20.")
                if s48 is not None and s48 < 80:
                    why.append(f"Had a real talk with only {s48}% of new leads within 48 hours.")
                if unt >= 4:
                    why.append(f"{unt} leads not called for 3 days or more.")
                if ans is not None and s["dials"] >= 20 and ans < 30:
                    why.append(f"Only {ans}% of calls were answered.")
                if rn is not None and ro is not None and rn <= ro - 10:
                    why.append(f"Recovery on the 24 and 31 Oct bootcamps is {rn}%, against {ro}% in October.")
                if why:
                    got = sum(v for o, v in cash.items() if same_person(o, c))
                    rows.append([f"<b>{e(c)}</b><br><span class=muted>{e(tl)}</span>", str(s["dials"]), str(leads_dialled(self.leads, c, self.yday)),
                                 str(s["real"]), red(lakh(got), got <= 0), "<br>".join(why)])
        return ("<h2>Callers who need attention</h2>"
                + (table([("Caller and team leader", "19%"), (f"Calls on {yd}", "8%"), ("Different leads called", "9%"), ("Real talks", "7%"),
                          (f"Received since {e(self.window)}", "11%"), ("Why", "46%")], rows, {1: "n", 2: "n", 3: "n", 4: "n"})
                   if rows else "<p>No caller is below the limits.</p>")
                + '<p class="muted">A caller is listed for any of these: fewer than 20 calls in the day; a real talk with under 80% of new leads within '
                  '48 hours; 4 or more leads not called for 3 days; under 30% of calls answered; recovery 10 or more points below October.</p>')

    def compliance_box(self) -> str:
        if not self.compliance:
            return "<h2>Compliance</h2><p>No checked call advised a lead to give a finance partner false information.</p>"
        items = "".join(f'<li><b>{e(c["caller"])}</b> ({e(c["date"])}): {e(c["what"])}</li>' for c in self.compliance)
        return (f"<h2>Compliance: {len(self.compliance)} call{'s' if len(self.compliance) != 1 else ''} to review</h2><ol>{items}</ol>"
                '<p class="muted">Each call was checked against its recording transcript. Lead details are available on request.</p>')

    def records(self) -> str:
        paid = sum(n["paid"] for n in (self.tl_numbers(t) for t in self.by_tl.values()))
        enrolled_pending = 0
        for l in self.leads:
            s = self.students.get("".join(ch for ch in l["phone"] if ch.isdigit())[-10:])
            if l["enrolled"] and s and (s.get("status") or "").lower() in ("pending", "", "booking fees received"):
                enrolled_pending += 1
        fresh = [l for l in self.leads if l["allocated"].astimezone(IST).date() == self.yday]
        not_tried = sum(1 for l in fresh if not any(c["out"] and c["t"] >= l["allocated"] for c in l["calls"]))
        items = []
        if paid:
            items.append(f"{paid} leads have paid in full according to the pool sheet but are still open in LeadSquared. Finance should confirm, "
                         "then move them to Course Enrolled.")
        if self.moved:
            items.append(f"{len(self.moved)} leads paid in full according to the pool sheet are counted as enrolled in this report. Finance "
                         "should confirm, then move them to Course Enrolled in LeadSquared.")
        if enrolled_pending:
            items.append(f"{enrolled_pending} leads are enrolled, but the pool sheet still shows only their booking fee. The pool sheet needs updating.")
        if any("enrolled_sheet" in l for l in self.leads):  # Mayur's team: the enrolment sheet against LeadSquared
            # one person can be both a bootcamp and a community lead under the same LeadSquared lead: count the lead once
            ahead = len({l["lead_id"] for l in self.leads if l.get("enrolled_sheet") and not l.get("enrolled_lsq")})
            behind = len({l["lead_id"] for l in self.leads if l.get("enrolled_lsq") and "enrolled_sheet" in l and not l["enrolled_sheet"]})
            if ahead:
                items.append(f"{ahead} leads have a payment in the enrolment sheet but are not Course Enrolled in LeadSquared yet. "
                             "Their stage should be moved.")
            if behind:
                items.append(f"{behind} leads are enrolled in LeadSquared but have no payment in the enrolment sheet. Check each payment "
                             "and add it to the sheet. These leads are counted as enrolled.")
        items.append(f"{not_tried} of the {len(fresh)} leads given to callers on {self.yday:%A} had not been called by "
                     f"{self.cut.astimezone(IST):%H:%M} today.")
        return "<h2>Records to fix</h2><ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"

    REASONS = {"No card or loan route": "have no credit card or cannot get a loan", "Wants later or no time": "want to pay later or say they have no time",
               "Money or price": "say they are short of money or the price is too high", "Refund or cancel": "asked for a refund or to cancel",
               "Family decides": "are waiting for their family to decide", "Doubts about the course": "have doubts about the course",
               "Other": "gave another reason"}

    def patterns(self) -> str:
        ob = Counter(objection_kind(j.get("objection")) for _, j, _ in self.rows if j.get("objection") and j["status"] in ("counsel", "cold"))
        st = Counter(j["status"] for _, j, _ in self.rows)
        reasons = [f"{v} {self.REASONS.get(k, k.lower())}" for k, v in ob.most_common()]
        said = (", ".join(reasons[:-1]) + " and " + reasons[-1]) if len(reasons) > 1 else "".join(reasons)
        return ("<h2>What leads are telling us</h2><ul>"
                f"<li>Of the {len(self.rows)} open leads, {st['hot']} are ready to pay, {st['counsel']} need more counselling, {st['connected']} have "
                f"talked but have not discussed payment, {st['callback']} asked us to call back, {st['notconnected']} have not picked up yet and "
                f"{st['cold']} have gone cold.</li>"
                f"<li>Of the {sum(ob.values())} leads who need more counselling or have gone cold and gave a reason: {e(said)}.</li></ul>")

    def html(self, checked: str = "") -> str:
        head = (f'<div class="band"><small>Collections · {e(TEAM_ADS[self.side])} · for {e(HEADS[self.side])}</small>'
                f'<h1>Where to act · {self.day:%A %d %B %Y}</h1><p>Calls and leads from LeadSquared, read {self.cut.astimezone(IST):%a %d %b %H:%M} IST. '
                f'Money from the pool sheet downloaded {self.pool_read:%a %d %b %H:%M} IST. No lead lists: the team leaders have those in their own reports.'
                + (f" {e(checked)}" if checked else "") + "</p></div>")
        notes = "".join(f'<div class="amber">{e(x)}</div>' for x in self.data_notes)
        p1 = '<div class="page">' + head + notes + self.actions() + self.glance() + self.worked() + "</div>"
        # pages 2 and 3 flow as one, so a team with more team leaders or bootcamps still fits on three pages
        p2 = ('<div class="page">' + self.scorecard() + self.money() + self.bootcamps() + self.callers() + self.compliance_box()
              + self.records() + self.patterns() + "</div>")
        p3 = ""
        return singular(f'<!doctype html><html><head><meta charset="utf-8"><title>Where to act</title>'
                        f"<style>{CSS}{EXTRA_CSS}{MGMT_CSS}</style></head><body>{p1}{p2}{p3}</body></html>")


def main() -> None:
    from analytics.collection_daily.checks import against_morning
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir"); ap.add_argument("day"); ap.add_argument("prev_pool"); ap.add_argument("week_ago_pool"); ap.add_argument("out_dir")
    ap.add_argument("--side", default="Puja / Anmol")
    ap.add_argument("--compliance", help="verified calls: [{side, caller, date, what}]")
    ap.add_argument("--paid-as-enrolled", action="store_true", help="count leads paid in full per the pool sheet as enrolled")
    a = ap.parse_args()
    comp = json.load(open(a.compliance)) if a.compliance else []
    day = date.fromisoformat(a.day)
    m = Management(a.side, a.run_dir, day, a.prev_pool, a.week_ago_pool, comp, a.paid_as_enrolled)
    n, wrong = against_morning(m, a.out_dir)
    for w in wrong:
        print("MISMATCH:", w)
    if wrong:
        raise SystemExit(f"{len(wrong)} of {n} numbers differ from this morning's reports: no PDF written")
    print(f"{n} numbers checked against this morning's reports and the pool sheet: all match")
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"Collections - where to act - {TEAM_ADS[a.side].split(' (')[0]} - {day:%a %d %b %Y}.pdf")
    write_pdf(m.html(f"Checked before sending: {n} numbers match this morning's team leader reports and the pool sheet's MIS."), path)
    print(path)


if __name__ == "__main__":
    main()
