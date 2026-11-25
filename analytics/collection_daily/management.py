"""Two-page management report for a collection team's head and AD (Puja Malik and Anmol Gakhar for Anmol's team,
asked 25 Nov 2026): no lead lists, only the data points where they need to act. Where the month stands against 85%,
a scorecard per TL, money waiting on a decision, callers to look at, compliance, records to fix, and patterns for
the sales team. Built from the same run as the daily PDFs (facts, grades, the pool sheet's MIS tabs).

    python -m analytics.collection_daily.management <run_dir> <day YYYY-MM-DD> <week-ago pool.xlsx> <out_dir> [--side "Puja / Anmol"] [--compliance file.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
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
"""


def red(v: str, bad: bool) -> str:
    return f'<span class="bad">{v}</span>' if bad else v


def settled(rows: list[dict], day: date) -> list[dict]:
    """Caller-wise MIS rows of bootcamps held SETTLED_DAYS+ days before ``day``: this month's newer bootcamps are still
    being collected, so set against a finished month they would always look worse."""
    return [r for r in rows if (d := tag_date(r["bootcamp"])) and d <= (day - timedelta(days=SETTLED_DAYS)).isoformat()]


def recovery(rows: list[dict], callers) -> int | None:
    mine = [r for r in rows if any(same_person(r["caller"], c) for c in callers)]
    n = sum(r["all_teams"] for r in mine)
    return pct(sum(r["recovered"] for r in mine), n) if n else None


class Management:
    def __init__(self, side: str, run_dir: str, day: date, week_ago_pool: str, compliance: list[dict]):
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
        self.compliance = [c for c in compliance if c.get("side") == side]
        args = (leads, self.rows, {"day": "", "leads": {}}, self.cut, day, load_feedback(run_dir),
                (self.as_of, mis), self.students, {"transcripts": "", "pool_read": "", "prev_cut": ""})
        self.team = TeamPDF(side, *args)
        self.tls = sorted({l["tl"] for l in leads})
        self.by_tl = {tl: TeamPDF(side, *args, tl=tl, callerwise=self.cw_now) for tl in self.tls}
        self.stats = review.day_stats(leads, self.yday)

    # -- helpers ----------------------------------------------------------------------------------------------------
    def untouched(self, ls) -> int:
        """Open leads, not cold and not waiting, with no dial for 3+ days."""
        rows = {l["lead_id"]: j for l, j, _ in self.rows}
        return sum(1 for l in ls if l["lead_id"] in rows and rows[l["lead_id"]]["status"] != "cold"
                   and not (rows[l["lead_id"]].get("waiting_until") or "") > self.day.isoformat()
                   and (not l["last_dial"] or (self.cut - l["last_dial"]).days >= 3))

    def money_groups(self, rows) -> dict:
        g = {"due": [], "refund": [], "loan": [], "week": Counter()}
        for l, j, p in rows:
            bal = self.team.balance(l) or 0
            if bal == 0 and self.team.balance(l) is not None:
                continue
            kind = objection_kind(j.get("objection"))
            d = promised(j.get("payment"), self.day.year)
            if kind == "Refund or cancel":
                g["refund"].append(bal)
            elif j["status"] in ("hot", "counsel") and d and d <= self.day:
                g["due"].append(bal)
            if j["status"] == "hot" and route(j.get("payment")) == "Loan / finance" and (not d or d < self.day):
                g["loan"].append(bal)
            if j["status"] in ("hot", "counsel") and d and self.day < d <= self.day + timedelta(days=6):
                g["week"][d] += bal
        return g

    # -- page 1 -----------------------------------------------------------------------------------------------------
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
                 (f"{lakh(sum(r['recovered_amount'] for r in self.mis))}", f"collected · {lakh(sum(r['pending_amount'] for r in self.mis))} still pending (MIS)"),
                 (f"{pct(sn_rec, sn_n)}% vs {pct(wk_rec, wk_n)}%",
                  f"recovered on the bootcamps that were in the MIS a week ago: now vs then (MIS of {self.week_as_of}: {wk_rec} of {wk_n})"),
                 (f"{drop} dropped", f"this month ({pct(drop, n)}% of leads) · bootcamps of 24 and 31 Oct at {recovery(self.cw_settled, callers)}% "
                  f"recovered; October finished at {pct(o_rec, o_n)}%"),
                 (f"{sum(l['enrolled'] for l in com)} of {len(com)}", "community leads (November bookings) enrolled · not in the pool MIS")]
        return ("<h2>1 · Where the month stands</h2>" + '<div class="tiles6">'
                + "".join(f'<div class="tile"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in tiles) + "</div>")

    def scorecard(self) -> str:
        rows = []
        for tl, t in self.by_tl.items():
            ls = t.leads
            past = [l for l in ls if self.cut > l["deadline"]]
            s48 = pct(sum(l["met48"] for l in past), len(past)) if past else None
            st = Counter()
            for c in t.callers:
                st.update(self.stats.get(c, Counter()))
            op_rows = t.rows
            talked = [j for l, j, _ in op_rows if l["real_talks"]]
            reached_pay = sum(1 for j in talked if j["status"] in ("hot",) or (j.get("payment") and route(j.get("payment")) != "Not decided"))
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
        return ("<h2>2 · Team leaders: where each is off track</h2>"
                + table([("TL", "13%"), ("Open leads", "7%"), (f"Enrolled {self.yday:%a}", "8%"), ("48-hour score (90%)", "9%"),
                         ("Untouched 3+ days", "9%"), (f"Missed calls not called back ({self.yday:%a})", "11%"),
                         (f"Call-backs ({self.yday:%a})", "14%"), ("Talks that reached payment", "10%"), ("Recovery, older Nov bootcamps", "9%"),
                         ("October final", "10%")], rows, {i: "n" for i in range(1, 10)})
                + f'<p class="muted">Red = below target, or recovery 5+ points under October. "Talks that reached payment" = open leads with a real '
                  'talk that are Hot or have a payment route agreed (loan, card, UPI). Call-backs: times saved in LeadSquared after a talk; '
                  f'most callers save none. Recovery compares like with like: November bootcamps held {SETTLED_DAYS}+ days ago (24 and 31 Oct) '
                  'against October\'s final figure, from the caller-wise MIS; the newer bootcamps are still being collected.</p>')

    def money(self) -> str:
        rows, tot = [], {"due": [], "refund": [], "loan": [], "week": Counter()}
        for tl, t in self.by_tl.items():
            g = self.money_groups(t.rows)
            for k in ("due", "refund", "loan"):
                tot[k] += g[k]
            tot["week"].update(g["week"])
            rows.append([f"<b>{e(tl)}</b>"] + [f"{len(g[k])} · {lakh(sum(g[k]))}" for k in ("due", "refund", "loan")]
                        + [lakh(sum(g["week"].values()))])
        rows.append(["<b>Team</b>"] + [f"<b>{len(tot[k])} · {lakh(sum(tot[k]))}</b>" for k in ("due", "refund", "loan")]
                    + [f"<b>{lakh(sum(tot['week'].values()))}</b>"])
        week = " · ".join(f"{d:%a %d}: {lakh(v)}" for d, v in sorted(tot["week"].items()))
        return ("<h2>3 · Money waiting on a decision (number of leads · balance)</h2>"
                + table([("TL", "16%"), ("Due today or overdue", "21%"), ("Refund or cancel asked: your call", "23%"),
                         ("Loans stuck (date passed or none)", "21%"), ("Promised rest of week", "19%")], rows)
                + f'<p class="muted">Promised by day: {e(week) or "none"}. A loan lead past its date is in both the first and third columns. Balances from the pool sheet (bootcamp) and the community sheet; '
                  'payment dates as each lead gave them.</p>')

    # -- page 2 -----------------------------------------------------------------------------------------------------
    def callers(self) -> str:
        rows = []
        for tl, t in self.by_tl.items():
            for c in t.callers:
                s = self.stats.get(c, Counter())
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
                    rows.append([e(c), e(tl), f"{s['dials']} / {s['real']}", "; ".join(flags)])
        return ("<h2>4 · Callers to look at</h2>"
                + (table([("Caller", "18%"), ("TL", "16%"), (f"Dials / real talks {self.yday:%a}", "13%"), ("Why", "53%")], rows)
                   if rows else "<p>No caller is below the thresholds.</p>")
                + '<p class="muted">Thresholds: under 20 collection dials, 48-hour score under 80%, 4+ untouched leads, answer rate under 30%, '
                  'recovery on older November bootcamps 10+ points below October.</p>')

    def compliance_box(self) -> str:
        if not self.compliance:
            return "<h2>5 · Compliance</h2><p>No checked call advised a lead to give a finance partner false information.</p>"
        items = "".join(f'<li><b>{e(c["caller"])}</b> ({e(c["date"])}): {e(c["what"])}</li>' for c in self.compliance)
        return (f"<h2>5 · Compliance: {len(self.compliance)} call(s) to review</h2><ol>{items}</ol>"
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
        return "<h2>6 · Records and handovers</h2><ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"

    def patterns(self) -> str:
        ob = Counter(objection_kind(j.get("objection")) for _, j, _ in self.rows if j.get("objection") and j["status"] in ("counsel", "cold"))
        st = Counter(j["status"] for _, j, _ in self.rows)
        top = " · ".join(f"{k} {v}" for k, v in ob.most_common(5))
        return ("<h2>7 · Patterns for the sales team</h2><ul>"
                f"<li>Objections on open leads: {e(top)}.</li>"
                f"<li>{ob['Refund or cancel']} open leads have asked for a refund or to cancel; the TL PDFs give what each one said.</li>"
                f"<li>Open leads by status: Hot {st['hot']} · To be counselled {st['counsel']} · Talked, no payment yet {st['connected']} · "
                f"Call-back {st['callback']} · Not connected {st['notconnected']} · Cold {st['cold']}.</li></ul>")

    def html(self) -> str:
        head = (f'<div class="band"><small>Collections · {e(TEAM_ADS[self.side])} · for {e(HEADS[self.side])}</small>'
                f'<h1>Where to act · {self.day:%A %d %B %Y}</h1><p>LeadSquared read {self.cut.astimezone(IST):%a %d %b %H:%M} IST · pool sheet '
                f'MIS of {e(self.as_of)}. No lead lists: the team leaders have those in their own PDFs.</p></div>')
        p1 = '<div class="page">' + head + self.month() + self.scorecard() + self.money() + "</div>"
        p2 = '<div class="page">' + self.callers() + self.compliance_box() + self.records() + self.patterns() + "</div>"
        return (f'<!doctype html><html><head><meta charset="utf-8"><title>Where to act</title>'
                f"<style>{CSS}{EXTRA_CSS}{MGMT_CSS}</style></head><body>{p1}{p2}</body></html>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir"); ap.add_argument("day"); ap.add_argument("week_ago_pool"); ap.add_argument("out_dir")
    ap.add_argument("--side", default="Puja / Anmol")
    ap.add_argument("--compliance", help="verified calls: [{side, caller, date, what}]")
    a = ap.parse_args()
    comp = json.load(open(a.compliance)) if a.compliance else []
    m = Management(a.side, a.run_dir, date.fromisoformat(a.day), a.week_ago_pool, comp)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"Collections - where to act - {TEAM_ADS[a.side].split(' (')[0]} - {date.fromisoformat(a.day):%a %d %b %Y}.pdf")
    write_pdf(m.html(), path)
    print(path)


if __name__ == "__main__":
    main()
