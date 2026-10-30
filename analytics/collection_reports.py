"""Wednesday check and Friday review PDFs for one bootcamp collection team (one PDF per team per day).

Every lead that paid the booking fee during a bootcamp of the month is listed under its caller until it enrols or
drops, latest weekend first. Call facts (first dial within 12 working hours, tries, enrolment) come from
LeadSquared through ``analytics.collection_cycle``; hot / warm / cold, the reason, payment intent, call-backs and
the next step come from a judgements file written after reading each lead's LSQ notes and Salesa transcripts.
Booking fees per bootcamp, prices and money collected come from the Bootcamp pool sheet (``pool`` in the config).

    python -m analytics.collection_reports wed|fri <config.json> "<YYYY-MM-DD HH:MM cut-off, IST>" <out.pdf>
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

from analytics.collection_cycle import WINDOW_HOURS, cycle_leads, state
from analytics.collection_scorecard import load
from integrations.timeutil import IST

CHROME = "/opt/pw-browsers/chromium"
e = lambda s: html.escape(str(s))
COL = {"enrolled": "#2a78d6", "hot": "#b3440f", "warm": "#eb6834", "cold": "#f6b38f", "notreached": "#d6d9de", "dropped": "#7b8089"}
INK = {"enrolled": "#fff", "hot": "#fff", "warm": "#1d1d1b", "cold": "#1d1d1b", "notreached": "#1d1d1b", "dropped": "#fff"}
LABEL = {"enrolled": "Enrolled", "hot": "Hot", "warm": "Warm", "cold": "Cold", "notreached": "Not reached yet", "dropped": "Dropped"}
ORDER = ["enrolled", "hot", "warm", "cold", "notreached", "dropped"]
GRADED = {"hot", "warm", "cold", "notreached"}
DIALER_SHARE = 0.5  # CLAUDE.md: 50%+ of dials ending in CallFailure is a dialer issue


def ist(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)


def when(t: datetime | None, fmt: str = "%a %d %b %H:%M") -> str:
    return t.astimezone(IST).strftime(fmt) if t else "—"


def price(pool: dict, lead: dict) -> int | None:
    b = pool["bootcamps"].get(lead["bootcamp"])
    return b["price"][lead["day"] - 1] if b else None


def row(lead: dict, cut: datetime, judged: dict | None, pool: dict, fee: int) -> dict:
    """One lead as of ``cut``: LSQ facts plus the judgement made for this report."""
    s = state(lead, cut)
    p = price(pool, lead)
    r = {**s, "called": bool(s["first_dial"]), "cb": (0, 0), "intent": "—", "nxt": "—", "risk": "", "followup": "", "outcome": "",
         "value": (p - fee) if p else 0}
    if s["enrolled"]:
        j = judged or {}
        return {**r, "status": "enrolled", "why": f"Enrolled {when(s['enrolled_at'], '%a %d %b')}",
                "intent": j.get("intent") or "—", "cb": (j.get("cb_kept", 0), j.get("cb_promised", 0)),
                "followup": j.get("wed_hot_followup", ""), "outcome": j.get("wed_hot_outcome", "")}
    if s["dropped"]:
        j = judged or {}
        return {**r, "status": "dropped", "why": j.get("drop_reason") or s["stage"], "later": j.get("later_batch_offered", "—")}
    if not judged:
        raise ValueError(f"no judgement for open lead {lead['phone']} ({lead['caller']})")
    st = judged["status"]
    if st not in GRADED:
        raise ValueError(f"{lead['phone']}: judged {st} but LSQ shows it open")
    if (st == "notreached") != (s["real_talks"] == 0):
        raise ValueError(f"{lead['phone']}: judged {st} with {s['real_talks']} real talks")
    return {**r, "status": st, "why": judged["why"], "intent": judged.get("intent") or "—", "nxt": judged.get("next") or "—",
            "cb": (judged.get("cb_kept", 0), judged.get("cb_promised", 0)), "risk": judged.get("risk", ""),
            "followup": judged.get("wed_hot_followup", ""), "outcome": judged.get("wed_hot_outcome", "")}


def snapshot(cfg: dict, cut: datetime) -> list[tuple[dict, dict]]:
    """(lead, row) for every lead of the month assigned by ``cut``; the lead carries its weekend number."""
    out = []
    for w in cfg["weekends"]:
        leads, hist, meta = load(w["data_dir"])
        judged = json.load(open(w["judgements"])) if os.path.exists(w["judgements"]) else {}
        for l in cycle_leads(leads, hist, meta, w["bootcamp_day"], cfg["side"]):
            if l["assigned"] > cut:
                continue
            j = (judged.get(l["lead_id"]) or {}).get(cut.date().isoformat())
            out.append(({**l, "wk": w["n"], "wk_label": w["label"]}, row(l, cut, j, cfg["pool"], cfg["fee"])))
    return out


def mode_of(intent: str) -> str:
    """The payment mode group of a judged intent, for the one-line summary (the full text stays on the lead row)."""
    t = intent.lower()
    if t in ("", "—"):
        return "No mode yet"
    if " or " in t or "undecided" in t or "not settled" in t:
        return "Undecided"
    for key, group in (("rejected", "Loan rejected, other route"), ("instal", "Instalments"), ("loan", "Loan"), ("paper finance", "Loan"),
                       ("one shot", "One shot"), ("card", "Card EMI / card"), ("emi", "EMI, card or loan open")):
        if key in t:
            return group
    return "Other"


def counts(rows) -> dict:
    c = Counter(s["status"] for _, s in rows)
    return {k: c.get(k, 0) for k in ORDER}


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def pct(a, b) -> int:
    return round(100 * a / b) if b else 0


def rs(x) -> str:
    n = f"{int(round(x))}"
    if len(n) <= 3:
        return "₹" + n
    head, tail, parts = n[:-3], n[-3:], []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(([head] if head else []) + parts) + "," + tail


def lakh(x) -> str:
    return f"₹{x / 1e5:.2f} L"


def due_passed(l, s, cut) -> bool:
    return s["called"] or cut > l["due"]


def dialer_issues(rows, since: datetime | None, cut: datetime) -> list[tuple[str, int, int]]:
    """Callers whose dials on these leads (since ``since``) ended in CallFailure 50%+ of the time."""
    by = defaultdict(lambda: [0, 0])
    for l, _ in rows:
        for c in l["calls"]:
            if c["out"] and c.get("team") and c["t"] <= cut and (since is None or c["t"] > since):
                by[c["by"]][0] += 1
                by[c["by"]][1] += c["status"] == "CallFailure"
    return sorted(((k, f, n) for k, (n, f) in by.items() if n >= 5 and f / n >= DIALER_SHARE), key=lambda x: -x[1] / x[2])


def org(rows) -> dict:
    o = defaultdict(lambda: defaultdict(set))
    for l, _ in rows:
        o[l["ad"]][l["tl"]].add(l["caller"])
    return {ad: {tl: sorted(cs, key=lambda c: (c != tl, c)) for tl, cs in sorted(tls.items())} for ad, tls in sorted(o.items())}


CSS = """
@page { size: A4; margin: 11mm 10mm 12mm 10mm; }
body { font-family: 'DejaVu Sans', Arial, sans-serif; color:#1d1d1b; font-size:9.5px; line-height:1.35; margin:0; }
.page { page-break-after: always; } .brk { page-break-before: always; }
h1 { font-size:17px; margin:0 0 2px; color:#14213D; } h2 { font-size:12.5px; margin:8px 0 4px; color:#14213D; }
h3 { font-size:11px; margin:8px 0 3px; color:#14213D; background:#EDEBE4; padding:3px 6px; }
.meta { color:#52514e; font-size:9px; margin-bottom:6px; }
.tiles { display:grid; grid-template-columns: repeat(6,1fr); gap:5px; margin:6px 0 8px; }
.tile { border:1px solid #d9d6cf; border-radius:5px; padding:5px 7px; } .tile b { font-size:16px; display:block; color:#14213D; }
.tile span { font-size:8px; color:#52514e; } .tile.alert b { color:#a3390b; }
.act { border-left:4px solid #b3440f; background:#FBF1EA; padding:6px 9px; margin:4px 0 8px; } .act ol { margin:2px 0 0 15px; padding:0; }
.act li { margin-bottom:2px; }
.note { border-left:4px solid #2a78d6; background:#EEF4FC; padding:5px 9px; margin:5px 0; font-size:8.5px; }
table { border-collapse:collapse; width:100%; margin:2px 0 6px; font-size:8.2px; table-layout:fixed; }
th { background:#EDEBE4; text-align:left; font-size:7.8px; color:#14213D; } th,td { border:1px solid #d9d6cf; padding:2px 4px; vertical-align:top; }
td.n, th.n { text-align:right; } tr.sub td { background:#F6F5F1; font-weight:bold; } tr { page-break-inside: avoid; }
.ph { font-family:'DejaVu Sans Mono', monospace; white-space:nowrap; font-size:7.8px; }
.chip { display:inline-block; padding:0 4px; border-radius:6px; font-size:7.5px; font-weight:bold; white-space:nowrap; }
.legend span { display:inline-block; margin-right:9px; font-size:8px; } .legend i { display:inline-block; width:8px; height:8px; margin-right:3px;
  vertical-align:-1px; border-radius:2px; }
.wk { font-size:9.5px; color:#fff; background:#14213D; padding:2px 6px; margin:8px 0 3px; font-weight:bold; }
.wk.old { background:#52514e; }
.muted { color:#52514e; font-size:8px; } .bad { color:#a3390b; font-weight:bold; } .good { color:#1d5fae; font-weight:bold; }
"""


def chip(st: str) -> str:
    return f'<span class="chip" style="background:{COL[st]};color:{INK[st]}">{LABEL[st]}</span>'


def legend() -> str:
    return '<div class="legend">' + "".join(f'<span><i style="background:{COL[k]}"></i>{LABEL[k]}</span>' for k in ORDER) + "</div>"


def bootcamp_label(l) -> str:
    return f'{l["bootcamp"]} · D{l["day"]}'


def stack_svg(groups) -> str:
    """Bar length = number of leads, so bootcamps of 5 and 100+ stay comparable; segments = status, labelled where they fit."""
    mx = max([sum(c.values()) for _, _, c in groups if c] or [1])
    w, rowh, lab = 470, 18, 190
    out, y = [], 2
    for label, sub, c in groups:
        if label is None:
            out.append(f'<text x="0" y="{y + 11}" font-size="9" font-weight="bold" fill="#14213D">{e(sub)}</text>')
            y += 16
            continue
        out.append(f'<text x="0" y="{y + 12}" font-size="8.6">{e(label)}</text>')
        x = lab
        for k in ORDER:
            v = c.get(k, 0)
            if not v:
                continue
            ww = w * v / mx
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(ww - 1.5, .8):.1f}" height="{rowh}" rx="2" fill="{COL[k]}"><title>{LABEL[k]}: {v}</title></rect>')
            if ww >= 16:
                out.append(f'<text x="{x + ww / 2:.1f}" y="{y + 12.5}" font-size="8" text-anchor="middle" fill="{INK[k]}" font-weight="bold">{v}</text>')
            x += ww
        out.append(f'<text x="{x + 5:.1f}" y="{y + 12.5}" font-size="8" fill="#52514e">{e(sub)}</text>')
        y += rowh + 5
    return f'<svg width="{lab + w + 150}" height="{y}" aria-label="Lead status by bootcamp">{"".join(out)}</svg>'


def bullet_svg(rows, target: int) -> str:
    w, rowh, lab = 380, 14, 200
    out, y = [], 14
    for name, p, txt in rows:
        if name is None:
            out.append(f'<text x="0" y="{y + 10}" font-size="9" font-weight="bold" fill="#14213D">{e(txt)}</text>')
            y += 15
            continue
        out.append(f'<text x="0" y="{y + 10}" font-size="8.6">{e(name)}</text>')
        out.append(f'<rect x="{lab}" y="{y}" width="{w}" height="{rowh}" rx="2" fill="#EDEBE4"/>')
        out.append(f'<rect x="{lab}" y="{y}" width="{max(w * p / 100, 1.5):.1f}" height="{rowh}" rx="2" fill="#2a78d6"/>')
        out.append(f'<text x="{lab + w + 6}" y="{y + 10}" font-size="8" font-weight="bold">{e(txt)}</text>')
        y += rowh + 6
    tx = lab + w * target / 100
    out.append(f'<line x1="{tx}" y1="8" x2="{tx}" y2="{y}" stroke="#14213D" stroke-width="2"/>')
    out.append(f'<text x="{tx + 3}" y="9" font-size="8" fill="#14213D">{target}% target</text>')
    return f'<svg width="{lab + w + 150}" height="{y + 2}" aria-label="Recovery against the target">{"".join(out)}</svg>'


class Report:
    def __init__(self, cfg: dict, kind: str, cut: datetime):
        self.cfg, self.kind, self.cut = cfg, kind, cut
        self.title = "Wednesday check" if kind == "wed" else "Friday review"
        self.rows = snapshot(cfg, cut)
        self.latest = max(l["wk"] for l, _ in self.rows)
        self.org = org(self.rows)
        self.wk_label = {l["wk"]: l["wk_label"] for l, _ in self.rows}

    # -- shared blocks ---------------------------------------------------------------------------------------
    def header(self, part: str) -> str:
        wks = " · ".join(f'Weekend {n} ({e(lab)}){" — latest" if n == self.latest else ""}' for n, lab in sorted(self.wk_label.items()))
        pool = self.cfg["pool"]["as_of"]
        return (f'<h1>{e(self.cfg["team"])} · {self.title} · {e(part)}</h1>'
                f'<div class="meta">{e(self.cfg["month"])} collections so far: {wks}. LeadSquared calls and stages up to '
                f'{when(self.cut, "%a %d %b %Y, %H:%M")} IST; Salesa transcripts of those calls; Bootcamp pool sheet MIS of {e(pool)}.</div>')

    def tiles(self) -> str:
        rows, n, cut = self.rows, len(self.rows), self.cut
        open_ = [(l, s) for l, s in rows if s["status"] not in ("enrolled", "dropped")]
        not_called = [(l, s) for l, s in open_ if not s["called"]]
        over = sum(1 for _, s in not_called if s["overdue_wh"] > 0)
        judged = [(l, s) for l, s in rows if due_passed(l, s, cut)]
        ot = sum(s["on_time"] for _, s in judged)
        c = counts(rows)
        latest_n = sum(1 for l, _ in rows if l["wk"] == self.latest)
        pool = self.cfg["pool"]
        mis = sum(b["recovered_amount"] for b in pool["bootcamps"].values())
        mis_n = sum(b["recovered"] for b in pool["bootcamps"].values())
        med = median([s["delay_wh"] for _, s in rows if s["called"]])
        t = [("Booking-fee leads", n, f"{latest_n} from the latest weekend"),
             ("Not called yet", len(not_called), f"{over} past the 12-hour window"),
             ("Called within 12 h", f"{pct(ot, len(judged))}%", f"{ot} of {len(judged)} due so far · median {med:.1f} working h"),
             ("Hot leads", c["hot"], f'{c["warm"]} warm · {c["cold"]} cold · {c["notreached"]} not reached'),
             ("Enrolled", f'{c["enrolled"]} ({pct(c["enrolled"], n)}%)', f'{c["dropped"]} dropped · target {self.cfg["target"]}%+'),
             ("Collected (pool MIS)", lakh(mis), f'{mis_n} leads, MIS {pool["as_of"]} · {lakh(sum(s["value"] for _, s in open_))} still open')]
        alert = {"Not called yet": bool(not_called), "Called within 12 h": ot < .85 * len(judged),
                 "Enrolled": c["enrolled"] < self.cfg["target"] / 100 * n}
        return '<div class="tiles">' + "".join(
            f'<div class="tile{" alert" if alert.get(k) else ""}"><span>{e(k)}</span><b>{e(v)}</b><span>{e(sub)}</span></div>' for k, v, sub in t) + "</div>"

    def act_items(self) -> list[str]:
        rows, cut = self.rows, self.cut
        open_ = [(l, s) for l, s in rows if s["status"] not in ("enrolled", "dropped")]
        items = []
        not_called = sorted([(l, s) for l, s in open_ if not s["called"]], key=lambda t: -t[1]["overdue_wh"])
        if not_called:
            items.append(f"<b>{len(not_called)} lead{'s' if len(not_called) > 1 else ''} not called yet — call now</b>: " + "; ".join(
                f'<span class="ph">{e(l["phone"])}</span> ({e(l["caller"])}, {e(bootcamp_label(l))}, '
                + (f'<span class="bad">{s["overdue_wh"]:.1f} working h past the 12-hour window</span>' if s["overdue_wh"] else "within the window")
                + ")" for l, s in not_called) + ".")
        hot = Counter(l["caller"] for l, s in open_ if s["status"] == "hot")
        if hot:
            items.append(f"<b>{sum(hot.values())} hot leads to close</b>: " + ", ".join(f"{e(k)} {v}" for k, v in hot.most_common()) + ".")
        cb_missed = Counter(l["caller"] for l, s in open_ if s["cb"][1] > s["cb"][0])
        if cb_missed:
            items.append(f"<b>{sum(cb_missed.values())} open leads with a missed call-back</b> (promised time not kept): "
                         + ", ".join(f"{e(k)} {v}" for k, v in cb_missed.most_common()) + ". Call them first today and say sorry.")
        late = [(c, sum(s["on_time"] for l, s in rows if l["caller"] == c and due_passed(l, s, cut)),
                 sum(1 for l, s in rows if l["caller"] == c and due_passed(l, s, cut))) for c in {l["caller"] for l, _ in rows}]
        late = sorted([x for x in late if x[2] and x[1] / x[2] < .7], key=lambda x: x[1] / x[2])
        if late:
            items.append("<b>Called within 12 h below 70%</b>: " + ", ".join(f"{e(c)} {pct(a, b)}%" for c, a, b in late) + ".")
        since = ist(self.cfg["wed_cut"]) if self.kind == "fri" else None
        dial = dialer_issues(rows, since, cut)
        if dial:
            items.append("<b>Dialer problem — fix before judging the caller</b>: " + ", ".join(
                f"{e(k)} {f} of {n} dials failed ({pct(f, n)}%)" for k, f, n in dial)
                + (" since Wednesday." if since else " on these leads."))
        never = [(l, s) for l, s in open_ if s["status"] == "notreached" and s["dials"] >= 10]
        if never:
            items.append(f"<b>{len(never)} leads not reached after 10+ dials</b>: send a WhatsApp, try another number or the TL's line "
                         "(listed on the TL pages).")
        return items

    def act_box(self, extra: list[str]) -> str:
        return '<div class="act"><b>Act on these first</b><ol>' + "".join(f"<li>{x}</li>" for x in self.act_items() + extra) + "</ol></div>"

    def bootcamp_groups(self):
        g = []
        for wk in sorted({l["wk"] for l, _ in self.rows}, reverse=True):
            g.append((None, f'{"LATEST — " if wk == self.latest else ""}Weekend {wk} ({self.wk_label[wk]})', None))
            names = Counter(l["bootcamp"] for l, _ in self.rows if l["wk"] == wk)
            for b, _ in names.most_common():
                x = [(l, s) for l, s in self.rows if l["wk"] == wk and l["bootcamp"] == b]
                c = counts(x)
                g.append((b, f'{len(x)} leads · {pct(c["enrolled"], len(x))}% enrolled', c))
        return g

    def caller_table(self) -> str:
        H = ['<table><tr><th style="width:17%">AD → TL → caller</th><th class="n">Leads</th><th class="n">Not called</th>'
             '<th class="n">Within 12 h</th><th class="n">Median 1st call (wh)</th><th class="n">Hot</th><th class="n">Warm</th><th class="n">Cold</th>'
             '<th class="n">Not reached</th><th class="n">Enrolled</th><th class="n">Dropped</th><th class="n">Call-backs kept</th>'
             '<th class="n" style="width:8%">Enrolled value (est.)</th><th class="n" style="width:8%">Still open</th></tr>']
        cut = self.cut

        def line(label, x, sub=False):
            n, c = len(x), counts(x)
            due = [(l, s) for l, s in x if due_passed(l, s, cut)]
            ot = sum(s["on_time"] for _, s in due)
            otp = pct(ot, len(due))
            med = median([s["delay_wh"] for _, s in x if s["called"]])
            kept, prom = sum(s["cb"][0] for _, s in x), sum(s["cb"][1] for _, s in x)
            nc = sum(not s["called"] and s["status"] not in ("enrolled", "dropped") for _, s in x)
            return (f'<tr class="{"sub" if sub else ""}"><td>{label}</td><td class="n">{n}</td><td class="n">{f"<span class=bad>{nc}</span>" if nc else 0}</td>'
                    f'<td class="n"><span{" class=bad" if due and otp < 70 else ""}>{otp}%</span></td><td class="n">{"—" if med is None else f"{med:.1f}"}</td>'
                    f'<td class="n">{c["hot"]}</td><td class="n">{c["warm"]}</td><td class="n">{c["cold"]}</td><td class="n">{c["notreached"]}</td>'
                    f'<td class="n">{c["enrolled"]} ({pct(c["enrolled"], n)}%)</td><td class="n">{c["dropped"]}</td>'
                    f'<td class="n"><span{" class=bad" if kept < prom else ""}>{kept}/{prom}</span></td>'
                    f'<td class="n">{lakh(sum(s["value"] for _, s in x if s["status"] == "enrolled"))}</td>'
                    f'<td class="n">{lakh(sum(s["value"] for _, s in x if s["status"] not in ("enrolled", "dropped")))}</td></tr>')
        for ad, tls in self.org.items():
            H.append(line(f"AD {e(ad)}", [(l, s) for l, s in self.rows if l["ad"] == ad], True))
            for tl, cs in tls.items():
                H.append(line(f"&nbsp;&nbsp;TL {e(tl)}", [(l, s) for l, s in self.rows if l["tl"] == tl], True))
                for c in cs:
                    H.append(line(f"&nbsp;&nbsp;&nbsp;&nbsp;{e(c)}", [(l, s) for l, s in self.rows if l["caller"] == c]))
        H.append(line("Team total", self.rows, True))
        return "".join(H) + "</table>"

    def intent_block(self) -> str:
        hw = [s for _, s in self.rows if s["status"] in ("hot", "warm")]
        mode = Counter(mode_of(s["intent"]) for s in hw)
        return (f'<h2>Payment intent of the {len(hw)} hot and warm leads</h2><p>' + " · ".join(f"<b>{e(k)}</b> {v}" for k, v in mode.most_common())
                + '</p><p class="muted">Line up finance-partner slots for loan leads and card-EMI links '
                'before the calls. Exact mode and date per lead are on the TL pages.</p>')

    def summary_page(self, extra_items: list[str]) -> str:
        return ('<div class="page">' + self.header("one-glance summary") + self.tiles() + self.act_box(extra_items)
                + '<h2>Where each bootcamp’s leads stand (bar length = number of leads)</h2>' + stack_svg(self.bootcamp_groups()) + legend()
                + '<h2>Callers by AD and TL</h2>' + self.caller_table() + self.intent_block() + "</div>")

    # -- lead lists -------------------------------------------------------------------------------------------
    def lead_rows(self, x) -> str:
        H = ['<table><tr><th style="width:2.5%">#</th><th style="width:10%">Number</th><th style="width:9%">Lead</th><th style="width:11%">Bootcamp · day</th>'
             '<th style="width:6.5%">1st call</th><th style="width:4.5%">Tries / days</th><th style="width:7%">Status</th>'
             '<th style="width:19.5%">Why (LSQ notes and transcripts)</th><th style="width:10.5%">Payment intent</th><th style="width:5%">Call-backs kept</th>'
             '<th style="width:14.5%">Next step</th></tr>']
        rank = {"hot": 1, "warm": 2, "notreached": 3, "cold": 4}
        key = lambda t: (0, -t[1]["overdue_wh"]) if not t[1]["called"] else (rank[t[1]["status"]], t[0]["assigned"])
        for i, (l, s) in enumerate(sorted(x, key=key), 1):
            if s["called"]:
                fc = f'{s["delay_wh"]:.1f} wh' + (' <span class="bad">late</span>' if not s["on_time"] else "")
            else:
                fc = f'<span class="bad">overdue {s["overdue_wh"]:.1f} wh</span>' if s["overdue_wh"] else f'due {when(l["due"], "%a %H:%M")}'
            cb = f'{s["cb"][0]}/{s["cb"][1]}' if s["cb"][1] else "—"
            if s["cb"][0] < s["cb"][1]:
                cb = f'<span class="bad">{cb}</span>'
            why = e(s["why"]) + (f'<br><span class="bad">At risk: {e(s["risk"])}</span>' if s["risk"] else "")
            H.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(bootcamp_label(l))}</td><td>{fc}</td>'
                     f'<td>{s["dials"]} / {s["dial_days"]}</td><td>{chip(s["status"])}</td><td>{why}</td><td>{e(s["intent"])}</td>'
                     f'<td>{cb}</td><td>{e(s["nxt"])}</td></tr>')
        return "".join(H) + "</table>"

    def tl_pages(self) -> list[str]:
        P = []
        open_ = [(l, s) for l, s in self.rows if s["status"] not in ("enrolled", "dropped")]
        for ad, tls in self.org.items():
            for tl, cs in tls.items():
                x = [(l, s) for l, s in open_ if l["tl"] == tl]
                out = ['<div class="brk">' + self.header(f"open leads · TL {tl} (AD {ad})"),
                       f'<p class="muted">{len(x)} open leads. Every open lead of the month stays listed until it is enrolled or dropped. '
                       'Order: not called (most overdue first), hot, warm, not reached, cold.</p>']
                for c in cs:
                    y = [(l, s) for l, s in x if l["caller"] == c]
                    if not y:
                        continue
                    cc = counts(y)
                    out.append(f'<h3>{e(c)} — {len(y)} open · {sum(not s["called"] for _, s in y)} not called · {cc["hot"]} hot · '
                               f'{cc["warm"]} warm · {cc["cold"]} cold · {cc["notreached"]} not reached</h3>')
                    for wk in sorted({l["wk"] for l, _ in y}, reverse=True):
                        z = [(l, s) for l, s in y if l["wk"] == wk]
                        title = (f"Latest weekend — Weekend {wk} ({self.wk_label[wk]})" if wk == self.latest
                                 else f"Earlier weekend — Weekend {wk} ({self.wk_label[wk]}) · still open")
                        out.append(f'<div class="wk{"" if wk == self.latest else " old"}">{e(title)} · {len(z)} leads</div>' + self.lead_rows(z))
                if not x:
                    out.append("<p>No open leads.</p>")
                out.append("</div>")
                P.append("".join(out))
        return P

    # -- Friday page --------------------------------------------------------------------------------------------
    def friday_page(self, wed_rows) -> tuple[str, list[str]]:
        now = {l["lead_id"]: s for l, s in self.rows}
        wed_hot = [(l, s) for l, s in wed_rows if s["status"] == "hot"]
        out = Counter(now[l["lead_id"]]["status"] for l, _ in wed_hot)
        fu = Counter(now[l["lead_id"]]["followup"] for l, _ in wed_hot)
        target, pool = self.cfg["target"], self.cfg["pool"]
        enrolled_since = [(l, s) for l, s in self.rows if s["status"] == "enrolled" and s["enrolled_at"] > ist(self.cfg["wed_cut"])]
        drops = [(l, s) for l, s in self.rows if s["status"] == "dropped"]
        risk = [(l, s) for l, s in self.rows if s["risk"] and s["status"] not in ("enrolled", "dropped")]
        extra = [f"<b>Wednesday’s {len(wed_hot)} hot leads</b>: {out['enrolled']} enrolled, {out['hot']} still hot, {out['warm']} cooled to warm, "
                 f"{out['cold']} cold, {out['dropped']} dropped. {fu['no']} were not called at the time promised to them, "
                 f"{fu['none agreed']} had no call-back time agreed (page 2).",
                 f"<b>{len(enrolled_since)} enrolled since Wednesday</b>: " + ", ".join(f"{e(k)} {v}" for k, v in Counter(l['caller'] for l, _ in enrolled_since).most_common()) + ".",
                 f"<b>{len(drops)} dropped this month</b>; {len(risk)} more at risk of dropping (asked for refund, later batch or "
                 "cannot proceed) — TL to call each today (page 2)."]
        # bootcamp performance
        bl, prow = [], ['<table><tr><th style="width:21%">Bootcamp</th><th class="n">Booking fees, all teams</th><th class="n">To this team (D1+D2)</th>'
                        '<th class="n">Enrolled</th><th class="n">Recovery</th><th class="n">Dropped</th><th class="n">Open</th>'
                        '<th class="n">Median days to pay</th><th class="n">Collected (pool MIS)</th><th class="n">Still open</th></tr>']
        for wk in sorted({l["wk"] for l, _ in self.rows}, reverse=True):
            head = f'{"Latest — " if wk == self.latest else ""}Weekend {wk} ({self.wk_label[wk]})'
            bl.append((None, 0, head))
            prow.append(f'<tr class="sub"><td colspan="10">{e(head)}</td></tr>')
            names = Counter(l["bootcamp"] for l, _ in self.rows if l["wk"] == wk)
            for b, _ in names.most_common():
                x = [(l, s) for l, s in self.rows if l["wk"] == wk and l["bootcamp"] == b]
                c, p = counts(x), pool["bootcamps"].get(b, {})
                d = Counter(l["day"] for l, _ in x)
                days = median([(s["enrolled_at"] - l["assigned"]).total_seconds() / 86400 for l, s in x if s["status"] == "enrolled"])
                bl.append((b, pct(c["enrolled"], len(x)), f'{pct(c["enrolled"], len(x))}% ({c["enrolled"]} of {len(x)})'))
                prow.append(f'<tr><td>{e(b)}</td><td class="n">{p.get("all_teams", "—")}</td><td class="n">{len(x)} ({d[1]}+{d[2]})</td>'
                            f'<td class="n">{c["enrolled"]}</td><td class="n">{pct(c["enrolled"], len(x))}%</td><td class="n">{c["dropped"]}</td>'
                            f'<td class="n">{len(x) - c["enrolled"] - c["dropped"]}</td><td class="n">{"—" if days is None else f"{days:.1f}"}</td>'
                            f'<td class="n">{lakh(p["recovered_amount"]) + " (" + str(p["recovered"]) + ")" if p else "—"}</td>'
                            f'<td class="n">{lakh(sum(s["value"] for _, s in x if s["status"] not in ("enrolled", "dropped")))}</td></tr>')
        m = counts(self.rows)
        n = len(self.rows)
        mis = sum(b["recovered_amount"] for b in pool["bootcamps"].values())
        prow.append(f'<tr class="sub"><td>Month so far</td><td></td><td class="n">{n}</td><td class="n">{m["enrolled"]}</td>'
                    f'<td class="n">{pct(m["enrolled"], n)}%</td><td class="n">{m["dropped"]}</td><td class="n">{n - m["enrolled"] - m["dropped"]}</td>'
                    f'<td></td><td class="n">{lakh(mis)}</td>'
                    f'<td class="n">{lakh(sum(s["value"] for _, s in self.rows if s["status"] not in ("enrolled", "dropped")))}</td></tr>')
        mis_n = sum(b["recovered"] for b in pool["bootcamps"].values())
        note = (f'<p class="muted">Enrolled and recovery are from LeadSquared up to {when(self.cut)} IST. Collected is the pool sheet MIS of '
                f'{e(pool["as_of"])} ({mis_n} leads); the {m["enrolled"] - mis_n} leads enrolled after it are not in that amount yet. '
                f'Still open = course price (pool sheet, by day) minus the ₹{self.cfg["fee"]:,} booking fee for each open lead. '
                'Median days to pay counts from assignment to enrolment.</p>' if m["enrolled"] != mis_n else "")
        # Wednesday's hot leads
        H = ['<table><tr><th style="width:3%">#</th><th style="width:10.5%">Number</th><th style="width:9%">Lead</th><th style="width:9%">Caller</th>'
             '<th style="width:12%">Bootcamp · day</th><th style="width:18%">Wednesday: hot because</th><th style="width:8%">Called at promised time?</th>'
             '<th style="width:7.5%">Now</th><th>What happened since Wednesday</th></tr>']
        fo = {"no": 0, "none agreed": 1, "yes": 2}
        so = {"dropped": 0, "cold": 1, "warm": 2, "notreached": 2, "hot": 3, "enrolled": 4}
        for i, (l, s) in enumerate(sorted(wed_hot, key=lambda t: (fo.get(now[t[0]["lead_id"]]["followup"], 3), so[now[t[0]["lead_id"]]["status"]])), 1):
            f = now[l["lead_id"]]
            ans = {"yes": "<span class=good>Yes</span>", "no": "<span class=bad>No</span>", "none agreed": "No time agreed"}.get(f["followup"], "—")
            H.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])}</td><td>{e(bootcamp_label(l))}</td>'
                     f'<td>{e(s["why"])}</td><td>{ans}</td><td>{chip(f["status"])}</td><td>{e(f["outcome"] or f["why"])}</td></tr>')
        H.append("</table>")
        # drops and leads at risk
        D_ = [f'<h2>Drops this month ({len(drops)}) and leads at risk of dropping ({len(risk)})</h2>']
        if not drops:
            D_.append("<p>No lead has been marked dropped in LeadSquared this month.</p>")
        else:
            D_.append('<table><tr><th style="width:3%">#</th><th style="width:11%">Number</th><th style="width:10%">Lead</th><th style="width:10%">Caller</th>'
                      '<th style="width:13%">Bootcamp · day</th><th>Drop reason</th><th style="width:10%">Later batch offered?</th><th style="width:6%">Talks</th></tr>')
            for i, (l, s) in enumerate(drops, 1):
                D_.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])}</td><td>{e(bootcamp_label(l))}</td>'
                          f'<td>{e(s["why"])}</td><td>{e(s.get("later", "—"))}</td><td>{s["real_talks"]}</td></tr>')
            D_.append("</table>")
        if risk:
            D_.append('<table><tr><th style="width:3%">#</th><th style="width:11%">Number</th><th style="width:10%">Lead</th><th style="width:10%">Caller</th>'
                      '<th style="width:13%">Bootcamp · day</th><th style="width:22%">Risk</th><th style="width:7%">Status</th><th>Next step</th></tr>')
            for i, (l, s) in enumerate(risk, 1):
                D_.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])}</td><td>{e(bootcamp_label(l))}</td>'
                          f'<td>{e(s["risk"])}</td><td>{chip(s["status"])}</td><td>TL call today: keep the seat, offer instalments or a named later batch '
                          f'(fee carried over). Caller’s step: {e(s["nxt"])}</td></tr>')
            D_.append("</table>")
        page = ('<div class="page">' + self.header("bootcamps, Wednesday’s hot leads, drops")
                + f'<h2>Recovery by bootcamp against the {target}% target</h2>' + bullet_svg(bl, target) + "".join(prow) + "</table>" + note
                + f'<h2>Wednesday’s hot leads — what happened ({len(wed_hot)})</h2><p class="muted">Not called at the promised time first.</p>'
                + "".join(H) + "".join(D_) + "</div>")
        return page, extra

    def defs(self) -> str:
        return f"""<div class="brk note"><b>How to read this.</b> <b>Within 12 h</b>: first dial within {WINDOW_HOURS} working hours
(10:00–20:00 IST, every day) of the lead's assignment to the caller; a dial by a team member before LeadSquared shows the assignment counts
as on time. <b>wh</b> = working hours. Leads whose 12 hours have not run out yet are left out of the percentage. <b>Tries / days</b>: dials so
far / different days with a dial. <b>Call-backs kept</b>: call-backs promised to the lead (in transcripts and saved follow-ups) / kept near the
promised time. <b>Payment intent</b>: the mode and date the lead gave. <b>Hot</b>: a real talk (answered, 2 min+) and a buying step — payment
date or mode agreed, link asked for, loan documents shared, or the lead called us back. <b>Warm</b>: a real talk, interested, with an open
objection or no date. <b>Cold</b>: "not now" or a deferral with no date, or unanswered since the last talk. A lead we failed to dial (dialer
failures) keeps its grade; the failure shows in the act box. <b>Not reached yet</b>: no real talk so far. <b>Enrolled</b>: LeadSquared stage
Course Enrolled or Collections done. <b>Enrolled value (est.)</b>: course price from the pool sheet minus the booking fee, for each enrolled
lead — an estimate; LeadSquared holds no paid amount for these leads, so actual money is the pool sheet MIS total. Hot / warm / cold and the
reasons were read from each lead's LeadSquared notes and Salesa call transcripts; transcripts have no speaker labels, so check the call
recording before quoting a lead.</div>"""

    def pages(self) -> list[str]:
        if self.kind == "wed":
            return [self.summary_page([])] + self.tl_pages() + [self.defs()]
        wed = snapshot(self.cfg, ist(self.cfg["wed_cut"]))
        page, extra = self.friday_page(wed)
        return [self.summary_page(extra), page] + self.tl_pages() + [self.defs()]

    def html(self) -> str:
        return f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(self.pages())}</body></html>'


def write_pdf(doc: str, path: str) -> None:
    hp = os.path.splitext(path)[0] + ".html"
    with open(hp, "w") as f:
        f.write(doc)
    subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(path)}", os.path.abspath(hp)], check=True, capture_output=True, timeout=180)


def main(kind: str, config: str, cut: str, out: str) -> None:
    rep = Report(json.load(open(config)), kind, ist(cut))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    write_pdf(rep.html(), out)
    print(f"{len(rep.rows)} leads → {out}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
