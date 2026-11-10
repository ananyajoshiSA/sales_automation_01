"""Community collection check: one PDF per collection team, in the bootcamp report's format (``collection_reports``).

What differs from the bootcamp report (agreed with the user, 5 Nov 2026):
- leads book all week, so they are grouped by booking month (latest first) and by batch (community + group number,
  "Independent Director G88–89") instead of bootcamp weekends; each lead shows its booking date instead of Day 1/2;
- money is the Community Webinar Collection Sheet's own per lead: amount paid and balance, not an estimate;
- a check (page 1, TL pages) plus one page on the earlier month's performance by batch against the target, the drops
  and the leads at risk of dropping.
Call facts come from LeadSquared (``analytics.community_cycle``); hot / warm / cold and the next step from a
judgements file written after reading each lead's LSQ notes and Salesa transcripts, as for bootcamps.

    python -m analytics.community_reports <config.json> "<YYYY-MM-DD HH:MM cut-off, IST>" <out.pdf>
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime

from analytics.collection_reports import (Report, bootcamp_label, bullet_svg, chip, counts, e, ist, lakh, org, pct,
                                          row, sheet_gaps, when, write_pdf)
from analytics.collection_cycle import WINDOW_HOURS
from analytics.community_cycle import SHEET_ENROLLED, community_leads, load_hist, sheet_rows
from integrations.timeutil import IST


def month_label(m: str) -> str:
    return datetime.strptime(m, "%Y-%m").strftime("%B")


def money(lead: dict, status: str) -> float:
    """What the sheet says about this lead's money: paid so far for an enrolled lead, the balance otherwise."""
    return lead["received"] if status == "enrolled" else lead["balance"]


def sheet_view(rows) -> dict:
    """The sheet's own counts per batch, in the shape ``sheet_gaps`` and the reconciliation read."""
    out = {}
    for l, _ in rows:
        b = out.setdefault(l["bootcamp"], {"recovered": 0, "recovered_amount": 0.0, "dropped": 0, "enrolled_list": []})
        if l["sheet_outcome"] in SHEET_ENROLLED:
            b["recovered"] += 1
            b["recovered_amount"] += l["received"]
            b["enrolled_list"].append([l["name"], l["phone"]])
        b["dropped"] += l["sheet_outcome"] == "Dropped"
    return out


class CommunityReport(Report):
    UNIT_HEAD = "Batch · booked on"
    SHEET = "Community sheet"
    VALUE_HEAD = "Collected (sheet)"
    BARS_HEAD = "Where each batch’s leads stand (bar length = number of leads)"
    BAR_ROW, BAR_LABEL = 12, 235

    def collected(self, x) -> float:
        """Amount paid per the sheet on leads it marks paid: the same money as the tile and the performance page."""
        return sum(l["received"] for l, _ in x if l["sheet_outcome"] in SHEET_ENROLLED)

    def __init__(self, cfg: dict, cut: datetime):
        self.cfg, self.kind, self.cut = cfg, "wed", cut
        self.title = f"{cut.astimezone(IST):%A} check"
        months = tuple(cfg["months"])
        srows, leads = sheet_rows(cfg["data_dir"], months)
        hist = load_hist(os.path.join(cfg["data_dir"], "hist.jsonl"))
        groups = json.load(open(cfg["groups_meta"]))["member_groups"]
        built, self.left = community_leads(srows, leads, hist, groups, cfg["side"], months)
        judged = json.load(open(cfg["judgements"])) if os.path.exists(cfg["judgements"]) else {}
        wk = {m: i for i, m in enumerate(sorted(months), 1)}
        self.rows = []
        for l in built:
            if l["assigned"] > cut:
                continue
            j = (judged.get(l["lead_id"]) or {}).get(cut.date().isoformat())
            s = row(l, cut, j, {"bootcamps": {}}, 0)
            s["value"] = money(l, s["status"])
            self.rows.append(({**l, "wk": wk[l["month"]], "wk_label": f"{month_label(l['month'])} bookings"}, s))
        self.latest = max(l["wk"] for l, _ in self.rows)
        self.org = org(self.rows)
        self.wk_label = {l["wk"]: l["wk_label"] for l, _ in self.rows}
        self.starts = {}
        for l, _ in self.rows:
            if l["batch_start"]:
                self.starts.setdefault(l["bootcamp"], l["batch_start"])
        cfg["pool"] = {"as_of": cfg["sheet_read_at"], "lists_as_of": cut.date().isoformat(), "bootcamps": sheet_view(self.rows)}
        self.gaps = sheet_gaps(self.rows, cfg["pool"], cut)
        self.data_to = min(cut, ist(cfg["lsq_read_at"]))
        for _, s in self.gaps[0]:
            s["sheet_enrolled"] = True

    def header(self, part: str) -> str:
        n = Counter(l["wk"] for l, _ in self.rows)
        wks = " · ".join(f'{e(lab)}: {n[k]}{" — latest" if k == self.latest else ""}' for k, lab in sorted(self.wk_label.items()))
        return (f'<h1>{e(self.cfg["team"])} · Community collections · {self.title} · {e(part)}</h1>'
                f'<div class="meta">Booking-fee leads of {e(", ".join(month_label(m) for m in sorted(self.cfg["months"])))} 2026 — {wks}. '
                f'LeadSquared calls and stages up to {when(self.data_to, "%a %d %b %Y, %H:%M")} IST; Salesa transcripts of those calls; '
                f'Community Webinar Collection Sheet downloaded {e(self.cfg["sheet_read_at"])} IST.</div>')

    def tiles(self) -> str:
        rows, n = self.rows, len(self.rows)
        open_ = [(l, s) for l, s in rows if s["status"] not in ("enrolled", "dropped")]
        not_called = [(l, s) for l, s in open_ if not s["called"]]
        over = sum(1 for _, s in not_called if s["overdue_wh"] > 0)
        c = counts(rows)
        latest_n = sum(1 for l, _ in rows if l["wk"] == self.latest)
        paid = [l for l, _ in rows if l["sheet_outcome"] in SHEET_ENROLLED]
        t = [("Booking-fee leads", n, f"{latest_n} booked in {self.wk_label[self.latest].split()[0]}"),
             ("Not called yet", len(not_called), f"{over} past the 12-hour window"),
             ("Hot leads", c["hot"], f'{c["warm"]} warm · {c["cold"]} cold · {c["notreached"]} DNP/No real conversation'),
             ("Enrolled", f'{c["enrolled"]} ({pct(c["enrolled"], n)}%)', f'{c["dropped"]} dropped · target {self.cfg["target"]}%+'),
             ("Collected (community sheet)", lakh(sum(l["received"] for l in paid)),
              f'{len(paid)} leads paid per the sheet · {lakh(sum(s["value"] for _, s in open_))} balance on open leads')]
        alert = {"Not called yet": bool(not_called), "Enrolled": c["enrolled"] < self.cfg["target"] / 100 * n}
        return '<div class="tiles">' + "".join(
            f'<div class="tile{" alert" if alert.get(k) else ""}"><span>{e(k)}</span><b>{e(v)}</b><span>{e(sub)}</span></div>' for k, v, sub in t) + "</div>"

    def batch_name(self, b: str) -> str:
        return f"{b} (from {self.starts[b]})" if self.starts.get(b) else b

    def bootcamp_groups(self):
        g = []
        for wk in sorted({l["wk"] for l, _ in self.rows}, reverse=True):
            g.append((None, f'{"LATEST — " if wk == self.latest else ""}{self.wk_label[wk]}', None))
            names = Counter(l["bootcamp"] for l, _ in self.rows if l["wk"] == wk)
            for b, _ in names.most_common():
                x = [(l, s) for l, s in self.rows if l["wk"] == wk and l["bootcamp"] == b]
                c = counts(x)
                g.append((self.batch_name(b), f'{len(x)} leads · {pct(c["enrolled"], len(x))}% enrolled', c))
        return g

    def tl_pages(self) -> list[str]:
        P = []
        open_ = [(l, s) for l, s in self.rows if s["status"] not in ("enrolled", "dropped")]
        for ad, tls in self.org.items():
            for tl, cs in tls.items():
                x = [(l, s) for l, s in open_ if l["tl"] == tl]
                out = ['<div class="brk">' + self.header(f"open leads · TL {tl} (AD {ad})"),
                       f'<p class="muted">{len(x)} open leads. Every open lead stays listed until it is enrolled or dropped. '
                       'Order: not called (most overdue first), hot, warm, DNP/No real conversation, cold.</p>']
                for c in cs:
                    y = [(l, s) for l, s in x if l["caller"] == c]
                    if not y:
                        continue
                    cc = counts(y)
                    out.append(f'<h3>{e(c)} — {len(y)} open · {sum(not s["called"] for _, s in y)} not called · {cc["hot"]} hot · '
                               f'{cc["warm"]} warm · {cc["cold"]} cold · {cc["notreached"]} DNP/No real conversation</h3>')
                    for wk in sorted({l["wk"] for l, _ in y}, reverse=True):
                        z = [(l, s) for l, s in y if l["wk"] == wk]
                        title = (f"Latest — {self.wk_label[wk]}" if wk == self.latest else f"Earlier — {self.wk_label[wk]} · still open")
                        out.append(f'<div class="wk{"" if wk == self.latest else " old"}">{e(title)} · {len(z)} leads</div>' + self.lead_rows(z))
                if not x:
                    out.append("<p>No open leads.</p>")
                out.append("</div>")
                P.append("".join(out))
        return P

    def performance_page(self) -> str:
        """The earlier month's result by batch against the target, the month so far, drops and leads at risk."""
        target = self.cfg["target"]
        bl, prow = [], ['<table><tr><th style="width:30%">Batch</th><th class="n">Leads</th><th class="n">Enrolled</th><th class="n">Recovery</th>'
                        '<th class="n">Dropped</th><th class="n">Open</th><th class="n">Collected (sheet)</th><th class="n">Balance on open leads</th></tr>']

        def line(label, x, sub=False):
            c, n = counts(x), len(x)
            return (f'<tr class="{"sub" if sub else ""}"><td>{e(label)}</td><td class="n">{n}</td><td class="n">{c["enrolled"]}</td>'
                    f'<td class="n">{pct(c["enrolled"], n)}%</td><td class="n">{c["dropped"]}</td><td class="n">{n - c["enrolled"] - c["dropped"]}</td>'
                    f'<td class="n">{lakh(sum(l["received"] for l, _ in x if l["sheet_outcome"] in SHEET_ENROLLED))}</td>'
                    f'<td class="n">{lakh(sum(s["value"] for _, s in x if s["status"] not in ("enrolled", "dropped")))}</td></tr>')
        for wk in sorted({l["wk"] for l, _ in self.rows}):
            m = [(l, s) for l, s in self.rows if l["wk"] == wk]
            head = self.wk_label[wk] + (" — month so far" if wk == self.latest else "")
            bl.append((None, 0, head))
            prow.append(f'<tr class="sub"><td colspan="8">{e(head)}</td></tr>')
            for b, _ in Counter(l["bootcamp"] for l, _ in m).most_common():
                x = [(l, s) for l, s in m if l["bootcamp"] == b]
                c = counts(x)
                bl.append((self.batch_name(b), pct(c["enrolled"], len(x)), f'{pct(c["enrolled"], len(x))}% ({c["enrolled"]} of {len(x)})'))
                prow.append(line(self.batch_name(b), x))
            prow.append(line(f"{self.wk_label[wk]} total", m, True))
        drops = [(l, s) for l, s in self.rows if s["status"] == "dropped"]
        risk = [(l, s) for l, s in self.rows if s["risk"] and s["status"] not in ("enrolled", "dropped")]
        D_ = [f'<h2>Drops ({len(drops)}) and leads at risk of dropping ({len(risk)})</h2>']
        if drops:
            D_.append('<table><tr><th style="width:3%">#</th><th style="width:11%">Number</th><th style="width:10%">Lead</th><th style="width:10%">Caller</th>'
                      '<th style="width:20%">Batch · booked on</th><th>Drop reason (LSQ stage)</th><th style="width:6%">Talks</th></tr>')
            for i, (l, s) in enumerate(drops, 1):
                D_.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])}</td><td>{e(bootcamp_label(l))}</td>'
                          f'<td>{e(s["why"])}</td><td>{s["real_talks"]}</td></tr>')
            D_.append("</table>")
        if risk:
            D_.append('<table><tr><th style="width:3%">#</th><th style="width:11%">Number</th><th style="width:10%">Lead</th><th style="width:10%">Caller</th>'
                      '<th style="width:16%">Batch · booked on</th><th style="width:22%">Risk</th><th style="width:7%">Status</th><th>Next step</th></tr>')
            for i, (l, s) in enumerate(risk, 1):
                D_.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])}</td><td>{e(bootcamp_label(l))}</td>'
                          f'<td>{e(s["risk"])}</td><td>{chip(s["status"])}</td><td>TL call today: keep the seat, offer instalments or a later batch. '
                          f'Caller’s step: {e(s["nxt"])}</td></tr>')
            D_.append("</table>")
        earlier = sorted(self.wk_label)[0]
        return ('<div class="brk">' + self.header(f"{self.wk_label[earlier].split()[0]} performance, drops and leads at risk")
                + f'<h2>Recovery by batch against the {target}% target</h2>' + bullet_svg(bl, target) + "".join(prow) + "</table>"
                + f'<p class="muted">{self.reconcile(short=True)} Collected = amount paid per the sheet on leads it marks collected or in '
                'instalments; balance = the sheet’s pending amount on leads still open in LeadSquared. Recent bookings have had less time to '
                'pay, so the latest month’s recovery is not comparable with the earlier one yet.</p>' + "".join(D_) + "</div>")

    def reconcile(self, short: bool = False) -> str:
        diff = []
        for b, p in self.cfg["pool"]["bootcamps"].items():
            c = counts([(l, s) for l, s in self.rows if l["bootcamp"] == b])
            if c["enrolled"] != p["recovered"]:
                diff.append(f'{e(b)}: {c["enrolled"]} enrolled in LSQ, {p["recovered"]} paid in the sheet')
            if c["dropped"] != p["dropped"]:
                diff.append(f'{e(b)}: {c["dropped"]} dropped in LSQ, {p["dropped"]} in the sheet')
        head = (f'Enrolled, dropped and recovery are from LeadSquared up to {when(self.data_to)} IST; money is the Community Webinar Collection '
                f'Sheet downloaded {e(self.cfg["sheet_read_at"])} IST.')
        if not diff:
            return head + " The two agree on every batch."
        if short:
            return head + ' <span class="bad">They differ</span> — see the last page.'
        return head + ' <span class="bad">They differ</span> — ' + "; ".join(diff) + ". TLs to update the LSQ stage or the sheet."

    def gaps_page(self) -> str:
        sheet_only, lsq_only, by_name, _ = self.gaps
        out = ['<div class="brk">' + self.header("community sheet vs LeadSquared — gaps to fix"), f'<p>{self.reconcile()}</p>',
               '<p class="muted">Checked lead by lead: every lead in this report is a row of the community sheet, matched to LeadSquared by '
               'phone, then email. Paid per the sheet = Disbursed, One shot or Instalments.</p>']

        def table(title, x, fix, stage=True):
            if not x:
                return f"<h2>{title} (0)</h2><p>None.</p>"
            H = [f"<h2>{title} ({len(x)})</h2><table><tr><th style=\"width:3%\">#</th><th style=\"width:10%\">Number</th><th style=\"width:10%\">Lead</th>"
                 '<th style="width:14%">Caller (TL)</th><th style="width:16%">Batch · booked on</th><th style="width:11%">LSQ stage now</th>'
                 '<th style="width:8%">Sheet status</th><th>Fix</th></tr>']
            for i, (l, s) in enumerate(sorted(x, key=lambda t: (t[0]["tl"], t[0]["caller"])), 1):
                H.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])} ({e(l["tl"])})</td>'
                         f'<td>{e(bootcamp_label(l))}</td><td>{e(s["stage"] or l["stage_now"])}</td><td>{e(l["sheet_status"])}</td><td>{fix}</td></tr>')
            return "".join(H) + "</table>"
        out.append(table("Paid per the community sheet, still open in LeadSquared", sheet_only,
                         "Confirm the payment; caller to move the LSQ stage to Collections done / Course Enrolled. Until then this report counts the "
                         "lead as open."))
        out.append(table("Enrolled in LeadSquared, not marked paid in the community sheet", lsq_only,
                         "Check the payment; update the sheet, or correct the LSQ stage."))
        dropped_gap = [(l, s) for l, s in self.rows if (l["sheet_outcome"] == "Dropped") != (s["status"] == "dropped")
                       and s["status"] != "enrolled" and l["sheet_outcome"] not in SHEET_ENROLLED]
        out.append(table("Dropped in one of the two only", dropped_gap, "TL to confirm whether the lead has dropped; update the other side."))
        by_sheet = [(l, s) for l, s in self.rows if l["credited_by"] == "sheet"]
        if by_sheet:
            out.append(table("Not with a collection caller in LeadSquared — credited to the sheet’s owner", by_sheet,
                             "Assign the lead in LeadSquared to the caller the sheet names."))
        if self.left:
            out.append(f"<h2>In the sheet but not in this report ({len(self.left)})</h2><ul>" + "".join(
                f'<li><span class="ph">{e(r["phone"])}</span> {e(r["name"])} — {e(r["owner"])}: {e(r["why_left"])}. Fix the number in the sheet or '
                'LeadSquared.</li>' for r in self.left) + "</ul>")
        return "".join(out) + "</div>"

    def defs(self) -> str:
        return f"""<div class="brk note"><b>How to read this.</b> <b>Lead</b>: a row of the Community Webinar Collection Sheet booked in these
months (its date), matched to LeadSquared, credited to the last collection caller who held it after booking. <b>Batch</b>: community and group
number from the sheet's "Course paid for"; "from" is the batch start date. <b>Within 12 h</b>: first dial within {WINDOW_HOURS} working hours
(10:00–20:00 IST, every day) of the lead's assignment to the caller after booking (the first owner change to a team member, or the booking
stage); a dial by a team member before LeadSquared shows the assignment counts as on time. <b>Tries / days</b>: dials so far / different days
with a dial; below it, the date of the last real talk. <b>Call-backs kept</b>: call-backs promised to the lead (in transcripts and saved follow-ups) / kept near the promised time.
<b>Payment intent</b>: the mode and date the lead gave. <b>Hot</b>: a real talk (answered, 2 min+) and a buying step — payment date or mode
agreed, link asked for, loan documents shared, or the lead called us back. <b>Warm</b>: a real talk, interested, with an open objection or no
date. <b>Cold</b>: "not now" or a deferral with no date, or unanswered since the last talk; also any hot or warm lead that did not pick up for
7 days despite 2+ dials a day (unless it asked us to wait for a date, such as an exam result). <b>At risk of dropping</b>: asked to cancel or
for a refund, said it won't continue, deferred to a later batch with no date, or has no payment route left. <b>DNP/No real conversation</b>: no answered call of
2 min or more so far. <b>Enrolled</b>: LeadSquared stage Course Enrolled or Collections done, or Loan pending saved as "loan done" / "loan
process done"; LeadSquared decides the counts and the sheet's differences are listed on the last page. <b>Collected (sheet)</b>: amount paid per
the community sheet (after loan subvention) on leads it marks paid. <b>Balance</b>: the sheet's pending amount on leads still open. Hot / warm /
cold and the reasons were read from each lead's LeadSquared notes and Salesa call transcripts; transcripts have no speaker labels, so check the
call recording before quoting a lead.</div>"""

    def pages(self) -> list[str]:
        return [self.summary_page([]), self.performance_page()] + self.tl_pages() + [self.defs(), self.gaps_page()]


def main(config: str, cut: str, out: str) -> None:
    rep = CommunityReport(json.load(open(config)), ist(cut))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    write_pdf(rep.html(), out)
    print(f"{len(rep.rows)} leads → {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
