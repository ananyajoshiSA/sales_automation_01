"""Wednesday check and Friday review PDFs for one bootcamp collection team (one PDF per team per day).

Every lead that paid the booking fee during a bootcamp of the month is listed under its caller until it enrols or
drops, latest weekend first. Call facts (first dial within 12 working hours, tries, enrolment) come from
LeadSquared through ``analytics.collection_cycle``; hot / warm / cold, the reason, payment intent, call-backs and
the next step come from a judgements file written after reading each lead's LSQ notes and Salesa transcripts.
Booking fees per bootcamp, prices and money collected come from the Bootcamp pool sheet (``pool`` in the config).

Every build first downloads the latest pool sheet (``BOOTCAMP_POOL_SHEET_ID``) next to the config and refreshes the
config's MIS figures from the month's "Main MIS Teamwise Split" tab (``analytics.collections.pool_mis``), saving
them back into the config. ``--saved`` builds from the config as it is (no download). A failed download stops.

    python -m analytics.collection_reports wed|fri <config.json> "<YYYY-MM-DD HH:MM cut-off, IST>" <out.pdf> [--saved]
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

from analytics.collection_cycle import WINDOW_HOURS, cycle_leads, state
from analytics.collection_scorecard import load
from analytics.collections.pool_mis import load_mis, refresh_pool
from integrations.gsheets import download_xlsx, sheet_id
from integrations.timeutil import IST, now_utc, utc

CHROME = "/opt/pw-browsers/chromium"
e = lambda s: html.escape(str(s))
COL = {"enrolled": "#2a78d6", "hot": "#b3440f", "warm": "#eb6834", "cold": "#f6b38f", "notreached": "#d6d9de", "dropped": "#7b8089"}
INK = {"enrolled": "#fff", "hot": "#fff", "warm": "#1d1d1b", "cold": "#1d1d1b", "notreached": "#1d1d1b", "dropped": "#fff"}
LABEL = {"enrolled": "Enrolled", "hot": "Hot", "warm": "Warm", "cold": "Cold", "notreached": "DNP/No real conversation", "dropped": "Dropped"}
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
        return {**r, "status": "dropped", "why": j.get("drop_reason") or s["stage"], "later": j.get("later_batch_offered", "—"),
                "followup": j.get("wed_hot_followup", ""), "outcome": j.get("wed_hot_outcome", "")}
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


def read_at(data_dir: str) -> datetime:
    """When LeadSquared was read for this weekend: the report cannot know anything later, whatever its cut-off."""
    meta = json.load(open(os.path.join(data_dir, "meta.json")))
    return utc(meta["fetched_at_utc"]) if meta.get("fetched_at_utc") else datetime.max.replace(tzinfo=IST)


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


def digits10(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def sheet_gaps(rows, pool: dict, cut: datetime) -> tuple[list, list, list, list[str]]:
    """Leads the pool sheet's enrolled lists and LeadSquared disagree on, for bootcamps whose list was given.

    Returns (sheet enrolled but open in LSQ, enrolled in LSQ but not in the sheet, sheet entries matched by name only
    (number differs), bootcamps checked). A list newer than the cut-off is not used: its later enrolments would look
    like gaps."""
    as_of = pool.get("lists_as_of")
    if not as_of or cut.date().isoformat() < as_of:
        return [], [], [], []
    sheet_only, lsq_only, by_name, checked = [], [], [], []
    for b, p in pool["bootcamps"].items():
        if not p.get("enrolled_list"):
            continue
        checked.append(b)
        team = [(l, s) for l, s in rows if l["bootcamp"] == b]
        by_phone = {digits10(l["phone"]): (l, s) for l, s in team}
        by_nm = {l["name"].strip().lower(): (l, s) for l, s in team}
        seen = set()
        for name, phone in p["enrolled_list"]:
            hit = by_phone.get(digits10(phone))
            if not hit and name.strip().lower() in by_nm:
                hit = by_nm[name.strip().lower()]
                by_name.append((hit[0], hit[1], phone))
            if not hit:
                continue  # another team's lead
            seen.add(hit[0]["lead_id"])
            if hit[1]["status"] != "enrolled":
                sheet_only.append(hit)
        lsq_only += [(l, s) for l, s in team if s["status"] == "enrolled" and l["lead_id"] not in seen]
    return sheet_only, lsq_only, by_name, checked


def counts(rows) -> dict:
    c = Counter(s["status"] for _, s in rows)
    return {k: c.get(k, 0) for k in ORDER}


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
.tiles { display:grid; grid-template-columns: repeat(5,1fr); gap:5px; margin:6px 0 8px; }
.tile { border:1px solid #d9d6cf; border-radius:5px; padding:5px 7px; } .tile b { font-size:16px; display:block; color:#14213D; }
.tile span { font-size:8px; color:#52514e; } .tile.alert b { color:#a3390b; }
.act { border-left:4px solid #b3440f; background:#FBF1EA; padding:6px 9px; margin:4px 0 8px; } .act ol { margin:2px 0 0 15px; padding:0; }
.act li { margin-bottom:2px; }
.note { border-left:4px solid #2a78d6; background:#EEF4FC; padding:5px 9px; margin:5px 0; font-size:8.5px; }
table { border-collapse:collapse; width:100%; margin:2px 0 6px; font-size:8.2px; table-layout:fixed; }
th { background:#EDEBE4; text-align:left; font-size:7.8px; color:#14213D; } th,td { border:1px solid #d9d6cf; padding:2px 4px; vertical-align:top; }
td.n, th.n { text-align:right; } tr.sub td { background:#F6F5F1; font-weight:bold; } tr { page-break-inside: avoid; }
.ph { font-family:'DejaVu Sans Mono', monospace; white-space:nowrap; font-size:7.8px; }
.chip { display:inline-block; padding:0 4px; border-radius:6px; font-size:7.5px; font-weight:bold; }
.legend span { display:inline-block; margin-right:9px; font-size:8px; } .legend i { display:inline-block; width:8px; height:8px; margin-right:3px;
  vertical-align:-1px; border-radius:2px; }
.wk { font-size:9.5px; color:#fff; background:#14213D; padding:2px 6px; margin:8px 0 3px; font-weight:bold; }
.wk.old { background:#52514e; }
.muted { color:#52514e; font-size:8px; } .bad { color:#a3390b; font-weight:bold; } .good { color:#1d5fae; font-weight:bold; }
"""


def chip(st: str) -> str:
    # the short label fits the narrow status column; the legend keeps the full one
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
        # a review made on another day keeps the Friday format but names its own day
        self.title = "Wednesday check" if kind == "wed" else f"{cut.astimezone(IST):%A} review"
        self.rows = snapshot(cfg, cut)
        self.latest = max(l["wk"] for l, _ in self.rows)
        self.org = org(self.rows)
        self.wk_label = {l["wk"]: l["wk_label"] for l, _ in self.rows}
        self.gaps = sheet_gaps(self.rows, cfg["pool"], cut)
        self.data_to = min([cut] + [read_at(w["data_dir"]) for w in cfg["weekends"]])
        for _, s in self.gaps[0]:
            s["sheet_enrolled"] = True

    # -- shared blocks ---------------------------------------------------------------------------------------
    def header(self, part: str) -> str:
        wks = " · ".join(f'Weekend {n} ({e(lab)}){" — latest" if n == self.latest else ""}' for n, lab in sorted(self.wk_label.items()))
        pool = self.cfg["pool"]["as_of"]
        if self.cfg["pool"].get("read_at_ist"):
            pool += f', downloaded {self.cfg["pool"]["read_at_ist"]} IST'
        return (f'<h1>{e(self.cfg["team"])} · {self.title} · {e(part)}</h1>'
                f'<div class="meta">{e(self.cfg["month"])} collections so far: {wks}. LeadSquared calls and stages up to '
                f'{when(self.data_to, "%a %d %b %Y, %H:%M")} IST; Salesa transcripts of those calls; Bootcamp pool sheet MIS of {e(pool)}.</div>')

    def tiles(self) -> str:
        rows, n = self.rows, len(self.rows)
        open_ = [(l, s) for l, s in rows if s["status"] not in ("enrolled", "dropped")]
        not_called = [(l, s) for l, s in open_ if not s["called"]]
        over = sum(1 for _, s in not_called if s["overdue_wh"] > 0)
        c = counts(rows)
        latest_n = sum(1 for l, _ in rows if l["wk"] == self.latest)
        pool = self.cfg["pool"]
        mis = sum(b["recovered_amount"] for b in pool["bootcamps"].values())
        mis_n = sum(b["recovered"] for b in pool["bootcamps"].values())
        t = [("Booking-fee leads", n, f"{latest_n} from the latest weekend"),
             ("Not called yet", len(not_called), f"{over} past the 12-hour window"),
             ("Hot leads", c["hot"], f'{c["warm"]} warm · {c["cold"]} cold · {c["notreached"]} DNP/No real conversation'),
             ("Enrolled", f'{c["enrolled"]} ({pct(c["enrolled"], n)}%)', f'{c["dropped"]} dropped · target {self.cfg["target"]}%+'),
             ("Collected (pool MIS)", lakh(mis), f'{mis_n} leads, MIS {pool["as_of"]} · {lakh(sum(s["value"] for _, s in open_))} still open')]
        alert = {"Not called yet": bool(not_called), "Enrolled": c["enrolled"] < self.cfg["target"] / 100 * n}
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
        if self.gaps[0] or self.gaps[1]:
            items.append(f"<b>Pool sheet and LeadSquared disagree on {len(self.gaps[0]) + len(self.gaps[1])} leads</b> — TLs to fix the LSQ "
                         "stage or the sheet (listed on the last page).")
        never = [(l, s) for l, s in open_ if s["status"] == "notreached" and s["dials"] >= 10]
        if never:
            items.append(f"<b>{len(never)} leads DNP/No real conversation after 10+ dials</b>: send a WhatsApp, try another number or the TL's line "
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
             '<th class="n">Within 12 h</th><th class="n">Hot</th><th class="n">Warm</th><th class="n">Cold</th>'
             '<th class="n">DNP/No real conversation</th><th class="n">Enrolled</th><th class="n">Dropped</th><th class="n">Call-backs kept</th>'
             '<th class="n" style="width:8%">Enrolled value (est.)</th><th class="n" style="width:8%">Still open</th></tr>']
        cut = self.cut

        def line(label, x, sub=False):
            n, c = len(x), counts(x)
            due = [(l, s) for l, s in x if due_passed(l, s, cut)]
            ot = sum(s["on_time"] for _, s in due)
            otp = pct(ot, len(due))
            kept, prom = sum(s["cb"][0] for _, s in x), sum(s["cb"][1] for _, s in x)
            nc = sum(not s["called"] and s["status"] not in ("enrolled", "dropped") for _, s in x)
            return (f'<tr class="{"sub" if sub else ""}"><td>{label}</td><td class="n">{n}</td><td class="n">{f"<span class=bad>{nc}</span>" if nc else 0}</td>'
                    f'<td class="n"><span{" class=bad" if due and otp < 70 else ""}>{otp}%</span></td>'
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
             '<th style="width:4.5%">Tries / days</th><th style="width:9%">Status</th>'
             '<th style="width:24%">Why (LSQ notes and transcripts)</th><th style="width:10.5%">Payment intent</th><th style="width:5%">Call-backs kept</th>'
             '<th style="width:14.5%">Next step</th></tr>']
        rank = {"hot": 1, "warm": 2, "notreached": 3, "cold": 4}
        key = lambda t: (0, -t[1]["overdue_wh"]) if not t[1]["called"] else (rank[t[1]["status"]], t[0]["assigned"])
        for i, (l, s) in enumerate(sorted(x, key=key), 1):
            cb = f'{s["cb"][0]}/{s["cb"][1]}' if s["cb"][1] else "—"
            if s["cb"][0] < s["cb"][1]:
                cb = f'<span class="bad">{cb}</span>'
            why = e(s["why"]) + (f'<br><span class="bad">At risk: {e(s["risk"])}</span>' if s["risk"] else "") \
                + ('<br><span class="bad">Pool sheet shows enrolled — confirm payment, update LSQ</span>' if s.get("sheet_enrolled") else "")
            H.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(bootcamp_label(l))}</td>'
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
        bl, prow = [], ['<table><tr><th style="width:24%">Bootcamp</th><th class="n">Leads (D1+D2)</th>'
                        '<th class="n">Enrolled</th><th class="n">Recovery</th><th class="n">Dropped</th><th class="n">Open</th>'
                        '<th class="n">Collected (pool MIS)</th><th class="n">Still open</th></tr>']
        for wk in sorted({l["wk"] for l, _ in self.rows}, reverse=True):
            head = f'{"Latest — " if wk == self.latest else ""}Weekend {wk} ({self.wk_label[wk]})'
            bl.append((None, 0, head))
            prow.append(f'<tr class="sub"><td colspan="8">{e(head)}</td></tr>')
            names = Counter(l["bootcamp"] for l, _ in self.rows if l["wk"] == wk)
            for b, _ in names.most_common():
                x = [(l, s) for l, s in self.rows if l["wk"] == wk and l["bootcamp"] == b]
                c, p = counts(x), pool["bootcamps"].get(b, {})
                d = Counter(l["day"] for l, _ in x)
                bl.append((b, pct(c["enrolled"], len(x)), f'{pct(c["enrolled"], len(x))}% ({c["enrolled"]} of {len(x)})'))
                prow.append(f'<tr><td>{e(b)}</td><td class="n">{len(x)} ({d[1]}+{d[2]})</td>'
                            f'<td class="n">{c["enrolled"]}</td><td class="n">{pct(c["enrolled"], len(x))}%</td><td class="n">{c["dropped"]}</td>'
                            f'<td class="n">{len(x) - c["enrolled"] - c["dropped"]}</td>'
                            f'<td class="n">{lakh(p["recovered_amount"]) + " (" + str(p["recovered"]) + ")" if p else "—"}</td>'
                            f'<td class="n">{lakh(sum(s["value"] for _, s in x if s["status"] not in ("enrolled", "dropped")))}</td></tr>')
        m = counts(self.rows)
        n = len(self.rows)
        mis = sum(b["recovered_amount"] for b in pool["bootcamps"].values())
        prow.append(f'<tr class="sub"><td>Month so far</td><td class="n">{n}</td><td class="n">{m["enrolled"]}</td>'
                    f'<td class="n">{pct(m["enrolled"], n)}%</td><td class="n">{m["dropped"]}</td><td class="n">{n - m["enrolled"] - m["dropped"]}</td>'
                    f'<td class="n">{lakh(mis)}</td>'
                    f'<td class="n">{lakh(sum(s["value"] for _, s in self.rows if s["status"] not in ("enrolled", "dropped")))}</td></tr>')
        mis_n = sum(b["recovered"] for b in pool["bootcamps"].values())
        note = f'<p class="muted">{self.reconcile(short=True)} Still open = course price (pool sheet, by day) minus the ₹{self.cfg["fee"]:,} booking fee for each ' \
               'open lead.</p>'
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
                          f'<td>{e(s["why"])}</td><td>{e(str(s.get("later", "—")).capitalize())}</td><td>{s["real_talks"]}</td></tr>')
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

    def reconcile(self, short: bool = False) -> str:
        """LeadSquared against the pool sheet MIS, bootcamp by bootcamp: the two are updated by different people."""
        pool = self.cfg["pool"]
        diff = []
        for b, p in pool["bootcamps"].items():
            x = [s for l, s in self.rows if l["bootcamp"] == b]
            c = counts([(None, s) for s in x])
            if c["enrolled"] != p["recovered"]:
                diff.append(f'{e(b)}: {c["enrolled"]} enrolled in LSQ, {p["recovered"]} recovered in the MIS')
            if "dropped" in p and c["dropped"] != p["dropped"]:
                diff.append(f'{e(b)}: {c["dropped"]} dropped in LSQ, {p["dropped"]} in the MIS')
        head = (f'Enrolled, dropped and recovery are from LeadSquared up to {when(self.data_to)} IST; collected money is the pool sheet MIS of '
                f'{e(pool["as_of"])}.')
        if not diff:
            return head + " The two agree on every bootcamp."
        if short:
            return head + ' <span class="bad">They differ</span> — see the last page.'
        return (head + ' <span class="bad">They differ</span> — ' + "; ".join(diff) + ". TLs to update the LSQ stage or the sheet "
                "so both show the same leads.")

    def gaps_page(self) -> str:
        sheet_only, lsq_only, by_name, checked = self.gaps
        out = ['<div class="brk">' + self.header("pool sheet vs LeadSquared — gaps to fix"),
               f'<p>{self.reconcile()}</p>']
        if not checked:
            out.append('<p class="muted">Lead by lead check not done: no enrolled list from the pool sheet dated on or after this report. '
                       'Share the list (name and number per bootcamp) to see which leads differ.</p>')
        else:
            unchecked = [b for b in self.cfg["pool"]["bootcamps"] if b not in checked]
            out.append(f'<p class="muted">Checked lead by lead against the pool sheet’s enrolled list of {e(self.cfg["pool"]["lists_as_of"])} for: '
                       f'{e(", ".join(checked))}.' + (f' No list for: {e(", ".join(unchecked))}.' if unchecked else "") + "</p>")

            def table(title, x, fix):
                if not x:
                    return f"<h2>{title} (0)</h2><p>None.</p>"
                H = [f"<h2>{title} ({len(x)})</h2><table><tr><th style=\"width:3%\">#</th><th style=\"width:11%\">Number</th><th style=\"width:12%\">Lead</th>"
                     '<th style="width:16%">Caller (TL)</th><th style="width:15%">Bootcamp · day</th><th style="width:15%">LSQ stage now</th><th>Fix</th></tr>']
                for i, (l, s) in enumerate(sorted(x, key=lambda t: (t[0]["tl"], t[0]["caller"])), 1):
                    H.append(f'<tr><td>{i}</td><td class="ph">{e(l["phone"])}</td><td>{e(l["name"])}</td><td>{e(l["caller"])} ({e(l["tl"])})</td>'
                             f'<td>{e(bootcamp_label(l))}</td><td>{e(s["stage"] or l["stage_now"])}</td><td>{fix}</td></tr>')
                return "".join(H) + "</table>"
            out.append(table("Enrolled in the pool sheet, still open in LeadSquared", sheet_only,
                             "Confirm the payment; caller to move the LSQ stage to Collections done / Course Enrolled. "
                             "Until then this report counts the lead as open."))
            out.append(table("Enrolled in LeadSquared, missing from the pool sheet", lsq_only,
                             "Check the payment; add the lead to the pool sheet, or correct the LSQ stage."))
            if by_name:
                out.append("<h2>Matched by name only — number differs in the sheet</h2><ul>" + "".join(
                    f'<li>{e(l["name"])}: LSQ <span class="ph">{e(l["phone"])}</span>, sheet <span class="ph">{e(ph)}</span> — correct the sheet.</li>'
                    for l, _, ph in by_name) + "</ul>")
        return "".join(out) + "</div>"

    def defs(self) -> str:
        return f"""<div class="brk note"><b>How to read this.</b> <b>Within 12 h</b>: first dial within {WINDOW_HOURS} working hours
(10:00–20:00 IST, every day) of the lead's assignment to the caller; a dial by a team member before LeadSquared shows the assignment counts
as on time. <b>wh</b> = working hours. Leads whose 12 hours have not run out yet are left out of the percentage. <b>Tries / days</b>: dials so
far / different days with a dial. <b>Call-backs kept</b>: call-backs promised to the lead (in transcripts and saved follow-ups) / kept near the
promised time. <b>Payment intent</b>: the mode and date the lead gave. <b>Hot</b>: a real talk (answered, 2 min+) and a buying step — payment
date or mode agreed, link asked for, loan documents shared, or the lead called us back. <b>Warm</b>: a real talk, interested, with an open
objection or no date. <b>Cold</b>: "not now" or a deferral with no date, or unanswered since the last talk. A lead we failed to dial (dialer
failures) keeps its grade; the failure shows in the act box. <b>DNP/No real conversation</b>: no real conversation (answered, 2 min+) so far — the lead did not pick up, or every call was under 2 minutes. <b>Enrolled</b>: LeadSquared stage
Course Enrolled or Collections done, or Loan pending saved as "loan done" / "loan process done". <b>Enrolled value (est.)</b>: course price from the pool sheet minus the booking fee, for each enrolled
lead — an estimate; LeadSquared holds no paid amount for these leads, so actual money is the pool sheet MIS total. Hot / warm / cold and the
reasons were read from each lead's LeadSquared notes and Salesa call transcripts; transcripts have no speaker labels, so check the call
recording before quoting a lead.</div>"""

    def pages(self) -> list[str]:
        if self.kind == "wed":
            return [self.summary_page([])] + self.tl_pages() + [self.defs(), self.gaps_page()]
        wed = snapshot(self.cfg, ist(self.cfg["wed_cut"]))
        page, extra = self.friday_page(wed)
        return [self.summary_page(extra), page] + self.tl_pages() + [self.defs(), self.gaps_page()]

    def html(self) -> str:
        return f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(self.pages())}</body></html>'


def write_pdf(doc: str, path: str) -> None:
    hp = os.path.splitext(path)[0] + ".html"
    with open(hp, "w") as f:
        f.write(doc)
    subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(path)}", os.path.abspath(hp)], check=True, capture_output=True, timeout=180)


def refresh_from_sheet(cfg: dict, folder: str) -> list[str]:
    """Download the pool sheet into ``folder`` and refresh ``cfg["pool"]`` from its MIS tab; returns notes."""
    path = os.path.join(folder, "bootcamp_pool.xlsx")
    download_xlsx(sheet_id("BOOTCAMP_POOL_SHEET_ID"), path)
    read_at = now_utc().astimezone(IST).strftime("%Y-%m-%d %H:%M")
    tab, as_of, mis = load_mis(path, cfg["month"])
    notes = refresh_pool(cfg["pool"], mis, as_of, cfg["side"])
    cfg["pool"]["read_at_ist"] = read_at
    return [f"Pool sheet downloaded {read_at} IST; MIS tab '{tab}' of {as_of or 'no date'}"] + notes


def main(kind: str, config: str, cut: str, out: str, saved: bool = False) -> None:
    cfg = json.load(open(config))
    if not saved:
        for note in refresh_from_sheet(cfg, os.path.dirname(os.path.abspath(config))):
            print(note, file=sys.stderr)
        with open(config, "w") as fh:
            json.dump(cfg, fh, indent=1, ensure_ascii=False)
    rep = Report(cfg, kind, ist(cut))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    write_pdf(rep.html(), out)
    print(f"{len(rep.rows)} leads → {out}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--saved"]
    main(*args[:4], saved="--saved" in sys.argv[1:])
