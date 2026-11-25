"""Two-page management report for a collection team's head and AD (Puja Malik and Anmol Gakhar for Anmol's team,
asked 25 Nov 2026): no lead lists, only the data points where they need to act. Five action points, where the month
stands (leads and rupees), what worked and what did not since the previous day's pool sheet, a scorecard per TL,
money waiting on a decision, open money by bootcamp and caller, callers to look at, compliance, records to fix and
patterns for sales. The money views follow Puja's own daily analysis (8 Oct 2026), this month's bootcamps only.
Built from the same run as the daily PDFs (facts, grades, the pool sheet) plus two earlier downloads of the pool
sheet: the previous day's (cash collected since) and a week-old one (the week's movement).

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
HEADS = {"Puja / Anmol": "Puja Malik and Anmol Gakhar", "Mayur": "Mayur and the ADs"}
MGMT_CSS = """
body { font-size:8.6px; } h2 { font-size:11px; margin:6px 0 3px; } table { font-size:7.8px; margin:1px 0 4px; }
.tiles6 .tile b { font-size:15px; } .band { padding:9px 14px; margin-bottom:6px; } .band h1 { font-size:16px; }
.red, .amber, .green { padding:3px 8px; margin:2px 0; } td.flag { color:#a3390b; font-weight:bold; }
.tiles5 { display:grid; grid-template-columns: repeat(5,1fr); gap:5px; margin:4px 0 6px; } .tiles5 .tile b { font-size:13px; }
.tiles5 .tile span { font-size:7.4px; } .two { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
ol.acts { margin:2px 0 4px 16px; padding:0; } ol.acts li { margin:2px 0; }
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
    def __init__(self, side: str, run_dir: str, day: date, prev_pool: str, week_ago_pool: str, compliance: list[dict]):
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
        self.since = f"{self.prev_read:%a}'s sheet"
        self.compliance = [c for c in compliance if c.get("side") == side]
        args = (leads, self.rows, {"day": "", "leads": {}}, self.cut, day, load_feedback(run_dir),
                (self.as_of, mis), self.students, {"transcripts": "", "pool_read": "", "prev_cut": ""})
        self.team = TeamPDF(side, *args)
        self.tls = sorted({l["tl"] for l in leads})
        self.by_tl = {tl: TeamPDF(side, *args, tl=tl, callerwise=self.cw_now) for tl in self.tls}
        # each TL's numbers come from that TL's own PDF (TeamPDF.stats): a dial on another TL's lead is not counted, as there

    # -- helpers ----------------------------------------------------------------------------------------------------
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
        return [b for b in self.camps() if b["day"] and b["day"] <= week and 2 * b["tracked"] >= b["n"]]

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

    # -- page 1 -----------------------------------------------------------------------------------------------------
    def headline(self) -> str:
        sh = self.sheet
        price, got = sum(r["price"] or 0 for r in sh), sum(r["received"] or 0 for r in sh)
        rec = sum(1 for r in sh if (r["status"] or "").lower() in RECOVERED)
        op = [r for r in sh if (r["status"] or "").lower() == OPEN]
        inst = [r for r in sh if (r["status"] or "").lower() == "installments" and (r["balance"] or 0) > 0]
        drop = [r for r in sh if (r["status"] or "").lower() == "drop"]
        cash = sum(a for *_, a in self.moves)
        return (f'<div class="green"><b>{self.day:%B} pool {lakh(price)} · collected {lakh(got)} ({pct(got, price)}% by rupees; '
                f'{rec} of {len(sh)} leads recovered, {pct(rec, len(sh))}% by leads) · open {lakh(sum(r["balance"] or 0 for r in op))} across '
                f'{len(op)} leads, plus {lakh(sum(r["balance"] or 0 for r in inst))} still due on {len(inst)} instalment plans · written off '
                f'{lakh(sum(r["balance"] or 0 for r in drop))} ({len(drop)} drops) · cash since {e(self.since)}: {"+" if cash >= 0 else ""}{lakh(cash)}.</b></div>')

    def actions(self) -> str:
        """Five action points for the head of the team, each from a fixed rule over this report's own numbers."""
        cash = self.cash_by_caller()
        held = Counter()
        for r in self.sheet:
            if (r["status"] or "").lower() == OPEN:
                held[r["owner"]] += r["balance"] or 0
        zero = sorted((o for o, v in cash.items() if v <= 0 and held[o] > 0), key=lambda o: -held[o])
        out = []
        if zero:
            out.append(f"<b>Ask the {len(zero)} callers who brought in no money since {e(self.since)} what they collect today</b>, biggest open "
                       "book first: " + ", ".join(f"{e(first(o))} ({lakh(held[o])})" for o in zero) + ".")
        due = {tl: self.money_groups(t) for tl, t in self.by_tl.items()}
        n, rs = sum(len(g["due"]) for g in due.values()), sum(sum(g["due"]) for g in due.values())
        if n:
            out.append(f"<b>{n} leads ({lakh(rs)}) promised payment by today or are overdue</b>: ask each TL at 12:00 how many paid: "
                       + ", ".join(f"{e(tl)} {len(g['due'])} ({lakh(sum(g['due']))})" for tl, g in sorted(due.items(), key=lambda x: -sum(x[1]["due"]))) + ".")
        rf = sum(len(g["refund"]) for g in due.values())
        if rf:
            out.append(f"<b>Decide the {rf} refund or cancel asks ({lakh(sum(sum(g['refund']) for g in due.values()))}) today</b>: keep the seat "
                       "with instalments or a later batch, or let them go. The TLs have the names on page 1 of their PDFs.")
        stuck = [b for b in self.judged() if b["cash"] <= 0 and b["open"]]
        if stuck:
            b = stuck[0]
            top = sorted(b["holders"].items(), key=lambda h: -h[1][1])[:3]
            out.append(f"<b>Push {e(b['name'])}</b>: {b['open']} leads and {lakh(b['open_rs'])} open, {pct(b['rec'], b['n'])}% recovered, no money "
                       f"since {e(self.since)}. Most of it is with " + ", ".join(f"{e(first(o))} ({h[0]} leads, {lakh(h[1])})" for o, h in top) + ".")
        fresh = [l for l in self.leads if l["allocated"].astimezone(IST).date() == self.yday
                 and not any(c["out"] and c["t"] >= l["allocated"] for c in l["calls"])]
        if fresh:
            per = Counter(l["tl"] for l in fresh)
            out.append(f"<b>{len(fresh)} leads given on {self.yday:%a} were not dialled by {self.cut.astimezone(IST):%H:%M}</b>: get them dialled by "
                       "11:00 (" + ", ".join(f"{e(tl)}'s team {k}" for tl, k in per.most_common()) + ").")
        return "<h2>1 · Your action points for today</h2><ol class=\"acts\">" + "".join(f"<li>{x}</li>" for x in out[:5]) + "</ol>"

    def worked(self) -> str:
        cash = self.cash_by_caller()
        total = sum(a for *_, a in self.moves)
        best = [(o, v) for o, v in cash.most_common() if v > 0]
        camps = sorted(self.camps(), key=lambda b: -b["cash"])
        enrolled = Counter(l["caller"] for l in self.leads if review.on(l.get("enrolled_at"), self.yday))
        good = [("Cash collected", f"{'+' if total >= 0 else ''}{lakh(total)} since {self.since} ({self.prev_read:%a %H:%M})"),
                ("Best collectors", " · ".join(f"{e(first(o))} +{lakh(v)}" for o, v in best[:4]) or "none"),
                ("Fastest-paying bootcamp", f"{e(camps[0]['name'])} +{lakh(camps[0]['cash'])}" if camps and camps[0]["cash"] > 0 else "none"),
                (f"Enrolled on {self.yday:%a} (LeadSquared)", f"{sum(enrolled.values())}: " + " · ".join(f"{e(first(c))} {k}" for c, k in enrolled.most_common())
                 if enrolled else "0")]
        zero = [o for o, v in cash.items() if v <= 0]
        stuck = [b for b in self.judged() if b["cash"] <= 0 and b["open"]]
        low = [b for b in self.judged() if b["open"]]
        low = min(low, key=lambda b: (b["rec"] / b["n"], -b["open_rs"])) if low else None
        bad = [(f"{len(zero)} of {len(cash)} callers collected ₹0", " · ".join(e(first(o)) for o in sorted(zero)) or "none"),
               ("Lowest recovery (held 7+ days ago)", f"{e(low['name'])}: {pct(low['rec'], low['n'])}% recovered, {low['open']} open, {lakh(low['open_rs'])}" if low else "—"),
               ("Bootcamps with no cash", " · ".join(f"{e(b['name'])} ({lakh(b['open_rs'])} open)" for b in stuck[:3]) or "none")]
        tb = lambda rows: table([("What", "34%"), ("Number", "66%")], [[f"<b>{e(a)}</b>", b] for a, b in rows])
        return ('<h2>3 · Since yesterday</h2><div class="two"><div><b class="ok">What worked</b>' + tb(good) + '</div><div><b class="bad">What did not</b>'
                + tb(bad) + "</div></div>"
                + f'<p class="muted">Cash = rise in "money received" between the pool sheet downloaded {self.prev_read:%a %d %b %H:%M} and '
                  f'{self.pool_read:%a %d %b %H:%M} IST, on this month\'s bootcamp leads already in the earlier sheet: when finance updated the sheet, '
                  'not always the day the lead paid. New leads\' booking fees are not counted.</p>')

    def month(self) -> str:
        n, rec = sum(r["all_teams"] for r in self.mis), sum(r["recovered"] for r in self.mis)
        drop = sum(r["dropped"] for r in self.mis)
        end = (self.day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        days_left = (end - self.day).days + 1
        need = max(0, math.ceil(TARGET * n / 100) - rec)
        names = {r["name"] for r in self.week}
        same_now = [r for r in self.mis if r["name"] in names]
        wk_n, wk_rec = sum(r["all_teams"] for r in self.week), sum(r["recovered"] for r in self.week)
        sn_n, sn_rec = sum(r["all_teams"] for r in same_now), sum(r["recovered"] for r in same_now)
        callers = {l["caller"] for l in self.leads}
        oct_ = [r for r in self.cw_oct if any(same_person(r["caller"], c) for c in callers)]
        o_n, o_rec = sum(r["all_teams"] for r in oct_), sum(r["recovered"] for r in oct_)
        com = [l for l in self.leads if l.get("community")]
        tiles = [(f"{rec} of {n} ({pct(rec, n)}%)", f"leads recovered this month · target {TARGET}% · pool sheet MIS of {self.as_of}"),
                 (f"{need} more", f"recoveries needed for {TARGET}% by {end:%a %d %b}: about {math.ceil(need / days_left)} a day for {days_left} days"),
                 (f"{pct(sn_rec, sn_n)}% vs {pct(wk_rec, wk_n)}%",
                  f"recovered on the bootcamps that were in the MIS a week ago: now vs then (MIS of {self.week_as_of}: {wk_rec} of {wk_n})"),
                 (f"{drop} dropped", f"this month ({pct(drop, n)}% of leads) · bootcamps of 24 and 31 Oct at {recovery(self.cw_settled, callers)}% "
                  f"recovered; October finished at {pct(o_rec, o_n)}%"),
                 (f"{sum(l['enrolled'] for l in com)} of {len(com)}", "community leads (November bookings) enrolled · not in the pool MIS")]
        return ("<h2>2 · Where the month stands</h2>" + '<div class="tiles5">'
                + "".join(f'<div class="tile"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in tiles) + "</div>")

    def scorecard(self) -> str:
        rows = []
        for tl, t in self.by_tl.items():
            ls = t.leads
            past = [l for l in ls if self.cut > l["deadline"]]
            s48 = pct(sum(l["met48"] for l in past), len(past)) if past else None
            st = Counter()
            for c in t.callers:
                st.update(t.stats.get(c, Counter()))
            op_rows = t.rows
            talked = [j for l, j, _ in op_rows if l["real_talks"]]
            reached_pay = sum(map(reached_payment, talked))
            saved = sum(1 for l in ls for f in l["follow_ups"] if f["set_at"].astimezone(IST).date() == self.yday)
            unt = self.untouched(ls)
            rn, ro = recovery(self.cw_settled, t.callers), recovery(self.cw_oct, t.callers)
            rows.append([f"<b>{e(tl)}</b>", str(len(op_rows)), str(st["enrolled"]),
                         red(f"{s48}%" if s48 is not None else "—", s48 is not None and s48 < 90),
                         red(str(unt), unt >= 5), red(f'{st["in_unreturned"]} of {st["in_missed"]}', st["in_unreturned"] > 0),
                         red(f'{saved} saved · {st["fu_kept"]}/{st["fu_due"]} kept', saved < max(1, st["real"] // 2)),
                         red(f"{pct(reached_pay, len(talked))}%", pct(reached_pay, len(talked)) < 60),
                         red(f"{rn}%" if rn is not None else "—", rn is not None and ro is not None and rn < ro - 5),
                         f"{ro}%" if ro is not None else "—"])
        return ("<h2>4 · Team leaders: where each is off track</h2>"
                + table([("TL", "13%"), ("Open leads", "7%"), (f"Enrolled {self.yday:%a}", "8%"), ("48-hour score (90%)", "9%"),
                         ("Untouched 3+ days", "9%"), (f"Missed calls not called back ({self.yday:%a})", "11%"),
                         (f"Call-backs ({self.yday:%a})", "14%"), ("Talks that reached payment", "10%"), ("Recovery, older Nov bootcamps", "9%"),
                         ("October final", "10%")], rows, {i: "n" for i in range(1, 10)})
                + f'<p class="muted">Red = below target, or recovery 5+ points under October. "Talks that reached payment" = of the open leads with a '
                  'real talk, those Hot, or To be counselled / Talked with a payment route agreed (loan, card, UPI); a cold lead never counts. '
                  '"Untouched 3+ days": open leads, given 3+ days ago, not cold or waiting, with no dial on any of the last 3 days. Call-backs: times saved in LeadSquared after a talk; '
                  f'most callers save none. Recovery compares like with like: November bootcamps held {SETTLED_DAYS}+ days ago (24 and 31 Oct) '
                  'against October\'s final figure, from the caller-wise MIS; the newer bootcamps are still being collected.</p>')

    def money(self) -> str:
        rows, tot = [], {"due": [], "refund": [], "loan": [], "week": Counter()}
        for tl, t in self.by_tl.items():
            g = self.money_groups(t)
            for k in ("due", "refund", "loan"):
                tot[k] += g[k]
            tot["week"].update(g["week"])
            rows.append([f"<b>{e(tl)}</b>"] + [f"{len(g[k])} · {lakh(sum(g[k]))}" for k in ("due", "refund", "loan")]
                        + [lakh(sum(g["week"].values()))])
        rows.append(["<b>Team</b>"] + [f"<b>{len(tot[k])} · {lakh(sum(tot[k]))}</b>" for k in ("due", "refund", "loan")]
                    + [f"<b>{lakh(sum(tot['week'].values()))}</b>"])
        week = " · ".join(f"{d:%a %d}: {lakh(v)}" for d, v in sorted(tot["week"].items()))
        return ("<h2>5 · Money waiting on a decision (number of leads · balance)</h2>"
                + table([("TL", "16%"), ("Due today or overdue", "21%"), ("Refund or cancel asked: your call", "23%"),
                         ("Loans stuck (date passed or none)", "21%"), ("Promised rest of week", "19%")], rows)
                + f'<p class="muted">Promised by day: {e(week) or "none"}. A loan lead past its date is in both the first and third columns. Balances from the pool sheet (bootcamp) and the community sheet; '
                  'payment dates as each lead gave them.</p>')

    # -- page 2 -----------------------------------------------------------------------------------------------------
    def bootcamps(self) -> str:
        rows, week = [], (self.day - timedelta(days=7)).isoformat()
        for b in sorted(self.camps(), key=lambda b: b["day"]):
            top = sorted(b["holders"].items(), key=lambda h: -h[1][1])[:3]
            rows.append([f"<b>{e(b['name'])}</b>", str(b["n"]), red(f"{pct(b['rec'], b['n'])}%", pct(b["rec"], b["n"]) < 50 and b["day"] <= week),
                         str(b["open"]), lakh(b["open_rs"]),
                         (red(lakh(b["cash"]), b["cash"] <= 0 and b["open"] > 0) if 2 * b["tracked"] >= b["n"] else "new in the sheet"),
                         " · ".join(f"{e(first(o))} ({h[0]}, {lakh(h[1])})" for o, h in top) or "—"])
        return (f"<h2>6 · Open money by bootcamp and caller ({self.day:%B})</h2>"
                + table([("Bootcamp", "22%"), ("Leads", "6%"), ("Recovered", "8%"), ("Open leads", "7%"), ("Open ₹", "9%"),
                         (f"Cash since {self.since}", "10%"), ("Most open money with (leads, ₹)", "38%")], rows, {i: "n" for i in range(1, 6)})
                + '<p class="muted">From the pool sheet\'s Mastersheet: open = booking fee paid, rest not yet; recovered = loan disbursed, one shot '
                  'or on instalments, as the MIS counts it. Red = under 50% recovered (bootcamps held 7+ days ago), or open money with no cash since the previous sheet.</p>')

    def callers(self) -> str:
        rows, cash = [], self.cash_by_caller()
        for tl, t in self.by_tl.items():
            for c in t.callers:
                s = t.stats.get(c, Counter())
                mine = [l for l in t.leads if l["caller"] == c]
                past = [l for l in mine if self.cut > l["deadline"]]
                s48 = pct(sum(l["met48"] for l in past), len(past)) if past else None
                unt = self.untouched(mine)
                ans = pct(s["answered"], s["dials"]) if s["dials"] else None
                rn, ro = recovery(self.cw_settled, [c]), recovery(self.cw_oct, [c])
                flags = []
                if s["dials"] < 20:
                    flags.append(f"{s['dials']} collection dials {self.yday:%a}")
                if s48 is not None and s48 < 80:
                    flags.append(f"48-hour score {s48}%")
                if unt >= 4:
                    flags.append(f"{unt} leads untouched 3+ days")
                if ans is not None and s["dials"] >= 20 and ans < 30:
                    flags.append(f"answer rate {ans}%")
                if rn is not None and ro is not None and rn <= ro - 10:
                    flags.append(f"recovery {ro}% Oct → {rn}% on older Nov bootcamps")
                if flags:
                    got = sum(v for o, v in cash.items() if same_person(o, c))
                    rows.append([e(c), e(tl), f"{s['dials']} / {leads_dialled(t.leads, c, self.yday)} / {s['real']}", red(lakh(got), got <= 0),
                                 "; ".join(flags)])
        return ("<h2>7 · Callers to look at</h2>"
                + (table([("Caller", "16%"), ("TL", "13%"), (f"Dials / leads dialled / real talks {self.yday:%a}", "15%"), (f"Cash since {self.since}", "9%"),
                          ("Why", "47%")], rows)
                   if rows else "<p>No caller is below the thresholds.</p>")
                + '<p class="muted">Leads dialled = different collection leads the caller dialled. Thresholds: under 20 collection dials, 48-hour score under 80%, 4+ untouched leads, answer rate under 30%, '
                  'recovery on older November bootcamps 10+ points below October.</p>')

    def compliance_box(self) -> str:
        if not self.compliance:
            return "<h2>8 · Compliance</h2><p>No checked call advised a lead to give a finance partner false information.</p>"
        items = "".join(f'<li><b>{e(c["caller"])}</b> ({e(c["date"])}): {e(c["what"])}</li>' for c in self.compliance)
        return (f"<h2>8 · Compliance: {len(self.compliance)} call(s) to review</h2><ol>{items}</ol>"
                '<p class="muted">Each checked against its transcript. Lead details are with the analyst on request.</p>')

    def records(self) -> str:
        rows = {l["lead_id"]: (j, p) for l, j, p in self.rows}
        paid_open = sum(1 for l in self.leads if l["lead_id"] in rows and self.team.balance(l) == 0)
        enrolled_pending = 0
        for l in self.leads:
            s = self.students.get("".join(ch for ch in l["phone"] if ch.isdigit())[-10:])
            if l["enrolled"] and s and (s.get("status") or "").lower() in ("pending", ""):
                enrolled_pending += 1
        fresh = [l for l in self.leads if l["allocated"].astimezone(IST).date() == self.yday]
        not_tried = sum(1 for l in fresh if not any(c["out"] and c["t"] >= l["allocated"] for c in l["calls"]))
        items = [f"<b>{paid_open}</b> leads paid in full per the sheets but still open in LeadSquared: finance to confirm, stage to Course Enrolled.",
                 f"<b>{enrolled_pending}</b> leads Course Enrolled in LeadSquared but pending in the pool sheet: update the sheet.",
                 f"<b>{not_tried}</b> of {len(fresh)} leads given to callers on {self.yday:%a} not dialled by {self.cut.astimezone(IST):%a %H:%M}."]
        return "<h2>9 · Records and handovers</h2><ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"

    def patterns(self) -> str:
        ob = Counter(objection_kind(j.get("objection")) for _, j, _ in self.rows if j.get("objection") and j["status"] in ("counsel", "cold"))
        st = Counter(j["status"] for _, j, _ in self.rows)
        return (f'<h2>10 · Patterns for sales</h2><p>Objections on open leads: {e(" · ".join(f"{k} {v}" for k, v in ob.most_common(5)))}. '
                f"Open leads by status: Hot {st['hot']} · To be counselled {st['counsel']} · Talked, no payment yet {st['connected']} · "
                f"Call-back {st['callback']} · Not connected {st['notconnected']} · Cold {st['cold']}.</p>")

    def html(self) -> str:
        head = (f'<div class="band"><small>Collections · {e(TEAM_ADS[self.side])} · for {e(HEADS[self.side])}</small>'
                f'<h1>Where to act · {self.day:%A %d %B %Y}</h1><p>LeadSquared read {self.cut.astimezone(IST):%a %d %b %H:%M} IST · pool sheet '
                f'MIS of {e(self.as_of)}, downloaded {self.pool_read:%a %H:%M} IST. No lead lists: the team leaders have those in their own PDFs.</p></div>')
        p1 = '<div class="page">' + head + self.headline() + self.actions() + self.month() + self.worked() + self.scorecard() + "</div>"
        p2 = ('<div class="page">' + self.money() + self.bootcamps() + self.callers() + self.compliance_box() + self.records()
              + self.patterns() + "</div>")
        return (f'<!doctype html><html><head><meta charset="utf-8"><title>Where to act</title>'
                f"<style>{CSS}{EXTRA_CSS}{MGMT_CSS}</style></head><body>{p1}{p2}</body></html>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir"); ap.add_argument("day"); ap.add_argument("prev_pool"); ap.add_argument("week_ago_pool"); ap.add_argument("out_dir")
    ap.add_argument("--side", default="Puja / Anmol")
    ap.add_argument("--compliance", help="verified calls: [{side, caller, date, what}]")
    a = ap.parse_args()
    comp = json.load(open(a.compliance)) if a.compliance else []
    m = Management(a.side, a.run_dir, date.fromisoformat(a.day), a.prev_pool, a.week_ago_pool, comp)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"Collections - where to act - {TEAM_ADS[a.side].split(' (')[0]} - {date.fromisoformat(a.day):%a %d %b %Y}.pdf")
    write_pdf(m.html(), path)
    print(path)


if __name__ == "__main__":
    main()
