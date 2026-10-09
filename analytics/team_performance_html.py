"""HTML for the daily calling report and the plan tracker page.

Layout (Parameters v1.4, P62): page 1 is a one-page summary anyone can read in a minute: the day from
calls to enrolments, what happened, the teams ranked, the callers to recognise and what to do next.
The pages after it explain every figure in plain language, each chart or table followed by what it
means, and end with how the report was made and checked and every caller's figures. Every sentence is
generated from the analysed figures, and the team figures quoted on page 1 are returned so the validation
gate can confirm each one appears in the team tables. Used by analytics.team_performance.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime, timedelta

from analytics.report_charts import BLUE, GRAY, ORANGE, bar, columns, legend, paired_bars, stacked_bar
from analytics.team_performance import ASSET_MIN_NOTES, IST, LOW_ANSWER_SHARE, VERSION, WARM, short
from integrations.timeutil import now_ist

e = html.escape
PAGE1_TEAMS = (15, 12, 10, 8, 5, 3)  # ranked teams drawn on page 1: the first that lets the summary fit (P61)
PAGE1_CALLERS = 8    # callers recognised on page 1, plus the coaching case
PAGE1_NAME = 32      # characters of a team name drawn on page 1; section 3 has the full name
HOUR_MIN_SHARE = 2   # % of the day's dials an hour needs before its answer rate is called best or worst
HOURS_PER_TABLE = 12  # hours per block of the hour table, so it never runs wider than the page
SECTIONS = ["Teams ranked", "Callers to recognise", "What to do next", "1. How to read this report",
            "2. From calls to enrolments", "3. Teams compared", "4. Weak spots of the day", "5. Team by team",
            "6. Callers", "7. What the converting calls had in common", "8. Call quality scores (Zipteams)",
            "9. When the calls happened", "10. Calls to review", "11. How this report was made and checked",
            "Appendix: every caller"]
MARKER_PLAIN = {
    "Price / fee / EMI": "Talked about the fee or EMI",
    "Discount / scholarship / offer": "Mentioned a discount, scholarship or offer",
    "Payment step (link, pay now, balance)": "Asked for payment (link, pay now, balance)",
    "Urgency (deadline, seats, last date)": "Gave a deadline (last date, few seats)",
    "Discovery questions": "Asked about the lead's background and goals",
    "Batch / LMS / onboarding": "Explained the batch or online classroom (LMS)",
    "Career / ROI": "Talked about career, jobs or earnings",
    "Fixed next step (date/time)": "Fixed the next call (a date or time)",
}
FLAG_PLAIN = {"overlap": ("Overlaps another call", "the caller was on two answered calls at the same time"),
              "repeat": ("3+ long calls to one lead", "one caller had three or more 2-minute calls with the same lead in a day"),
              "just_over": ("Bunched just over 2 minutes", "a caller with 10+ long calls whose share of 2:00–2:29 calls was at least "
                            "twice the share across all callers that day"),
              "no_content": ("No words in the recording", "the transcript had under 30 words"),
              "thin": ("Little talk for the time", "under 60 words a minute; people speak about 150"),
              "machine": ("Recorded message", "an IVR, voicemail or hold message at the start"),
              "loop": ("One phrase repeating", "the same few words make up 15% or more of the call")}
TRANSCRIPT_FLAGS = ("no_content", "thin", "machine", "loop")  # need a transcript; "–" when none was read

CSS = """
@page{size:A4;margin:12mm 13mm 15mm;@bottom-left{content:"%FOOT%";font:7.5pt Arial,Helvetica,sans-serif;color:#5b6475}
@bottom-right{content:"Page " counter(page) " of " counter(pages);font:7.5pt Arial,Helvetica,sans-serif;color:#5b6475}}
body{font-family:Arial,Helvetica,sans-serif;font-size:9.6pt;color:#1d2433;line-height:1.4;margin:0}
h1{font-size:18pt;margin:0;color:#0f2b5b} h2{font-size:13pt;color:#0f2b5b;margin:16px 0 6px;padding-bottom:3px;border-bottom:2px solid #dfe5f0;break-after:avoid}
h3{font-size:10.4pt;color:#0f2b5b;margin:11px 0 4px;break-after:avoid} .p1 h3{margin:9px 0 3px}
.sub{color:#5b6475;font-size:8.6pt;margin:3px 0 8px} .newpage{break-before:page} .newpage>h2:first-child{margin-top:0}
.sample{background:#fff1c9;border:1px solid #e7c76a;color:#5c4400;padding:5px 9px;border-radius:4px;font-size:8.8pt;margin:0 0 8px}
.funnel{display:flex;align-items:stretch;margin:4px 0 8px} .arrow{align-self:center;color:#8a93a6;font-size:12pt;padding:0 3px}
.step{flex:1;background:#f5f7fb;border:1px solid #dfe5f0;border-radius:6px;padding:5px 7px}
.step b{display:block;font-size:15pt;color:#0f2b5b;line-height:1.15} .step span{display:block;font-size:8pt}
.step em{display:block;font-style:normal;font-size:7.5pt;color:#5b6475;margin-top:2px}
.box{background:#eef3fb;border-left:4px solid #2a78d6;padding:6px 11px;margin:6px 0} .box ul{margin:0;padding-left:15px} .box li{margin:2px 0}
.means{border-left:3px solid #c3c2b7;background:#f7f7f4;padding:5px 10px;margin:7px 0;break-inside:avoid}
.means b.h{color:#0f2b5b} .means ul{margin:2px 0 0;padding-left:15px} .means li{margin:2px 0}
table{border-collapse:collapse;width:100%;font-size:8.6pt} thead{display:table-header-group}
th{background:#0f2b5b;color:#fff;padding:4px 5px;text-align:left;font-weight:600;font-size:8pt;vertical-align:bottom}
td{padding:3px 5px;border-bottom:1px solid #e1e6ef;vertical-align:middle} tr{break-inside:avoid}
td.n,th.n{text-align:right} td.n{white-space:nowrap;font-variant-numeric:tabular-nums} tr.top td{background:#eef7f0}
.bar{display:inline-block;height:9px;border-radius:0 2px 2px 0;vertical-align:middle;margin-right:5px}
.stack{white-space:nowrap} .stack .bar{margin-right:2px;border-radius:0}
.tag{font-size:7pt;border-radius:3px;padding:0 4px;margin-left:4px;white-space:nowrap;font-weight:normal}
.tag.warm{background:#fde3d6;color:#8a3510} .tag.coach{background:#e2ebfa;color:#1c4f95} .tag.dialer{background:#fff1c9;color:#6b4e00}
.legend{font-size:8pt;color:#5b6475;margin:3px 0} .legend span{margin-right:14px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
svg.chart{display:block;margin:2px 0} svg.chart text{font-family:Arial,Helvetica,sans-serif}
.tick{font-size:9px;fill:#5b6475} .val{font-size:9px;fill:#1d2433} .lab{font-size:10px;fill:#1d2433}
.cols{display:flex;gap:16px} .cols>div{flex:1;min-width:0}
.p1 table{font-size:8.4pt} .p1 td{padding:2px 5px} .p1 .box{font-size:9.2pt;padding:5px 11px} .p1 .box li{margin:1px 0}
td.nw{white-space:nowrap} .fig{break-inside:avoid;margin:6px 0} .keep{break-inside:avoid}
table.fixed{table-layout:fixed} table.fixed td{overflow-wrap:anywhere} tr.div td{background:#f5f7fb;color:#5b6475;font-size:7.6pt;font-weight:bold;padding:2px 5px}
.cards{display:flex;flex-wrap:wrap;gap:10px} .card{width:calc(50% - 5px);box-sizing:border-box;border:1px solid #dfe5f0;border-radius:6px;padding:7px 9px;break-inside:avoid}
.card h3{margin:0 0 4px} .card ul{margin:2px 0 0 14px;padding:0} .card li{margin:1px 0;font-size:8.6pt}
.mini{display:flex;gap:4px;margin:3px 0 5px} .mini div{flex:1;background:#f5f7fb;border-radius:4px;padding:3px 4px;font-size:7.2pt;color:#5b6475}
.mini b{display:block;font-size:10.5pt;color:#0f2b5b} .next{margin-top:5px;font-size:8.6pt;background:#eef3fb;border-radius:4px;padding:3px 6px}
.note{font-size:8pt;color:#5b6475;margin-top:4px} .ok{color:#1d6b3a;font-weight:bold} ol{margin:3px 0 0 16px;padding:0} li{margin:3px 0}
dl.terms{display:grid;grid-template-columns:155px 1fr;gap:5px 12px;margin:4px 0} dl.terms dt{font-weight:bold;color:#0f2b5b} dl.terms dd{margin:0}
.toc{columns:2;margin:4px 0;padding-left:0;list-style:none} .app td{font-size:7.8pt;padding:2px 4px} .app tr.team td{background:#eef3fb;font-weight:bold;color:#0f2b5b}
.hours{margin-bottom:6px} .hours td,.hours th{font-size:7.4pt;padding:2px 3px;text-align:right} .hours td:first-child,.hours th:first-child{text-align:left}
"""
TRACKER_CSS = """
@page{size:A4;margin:11mm 12mm} body{font-family:Arial,Helvetica,sans-serif;font-size:9.4pt;color:#1d2433;line-height:1.35;margin:0}
h1{font-size:17pt;margin:0;color:#0f2b5b} h2{font-size:11.5pt;color:#0f2b5b;margin:11px 0 4px}
.sub{color:#5b6475;font-size:8.4pt;margin:2px 0 8px}
.box{background:#eef3fb;border-left:4px solid #0f5bd8;padding:7px 10px;margin:6px 0 4px;font-size:9.6pt}
table{border-collapse:collapse;width:100%;font-size:8.3pt} th{background:#0f2b5b;color:#fff;padding:3px 5px;text-align:left;font-weight:600}
td{padding:3px 5px;border-bottom:1px solid #e1e6ef;vertical-align:middle} td.n{text-align:right;white-space:nowrap} th.n{text-align:right}
.full{font-size:7.6pt} .full td{padding:2px 4px} .note{font-size:7.6pt;color:#5b6475;margin-top:3px}
"""


def f(v, s=""):
    return "–" if v is None else f"{v}{s}"


def n(v: int | None) -> str:
    return "–" if v is None else f"{v:,}"


def share(a: float, b: float) -> int | None:
    return round(100 * a / b) if b else None


def plural(k: int, one: str, many: str | None = None) -> str:
    return f"{k:,} {one if k == 1 else many or one + 's'}"


def span(a, b, unit: str = "%") -> str:
    return f"{a}{unit}" if a == b else f"{a}{unit} to {b}{unit}"


def joined(xs: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return " and ".join(xs) if len(xs) < 3 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _when(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso).astimezone(IST) if iso else None


def _day(d: datetime) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def _dm(d: datetime) -> str:
    return f"{d.day} {d:%b}"


def _clip(team: str) -> str:
    """A team name short enough for page 1's narrow columns; section 3 has the full name."""
    s = short(team)
    return s if len(s) <= PAGE1_NAME else s[:PAGE1_NAME - 1] + "…"


def _plain(marker: str) -> str:
    return MARKER_PLAIN.get(marker, marker)


def _brief(marker: str) -> str:
    """The plain marker without its trailing example list, starting in lower case, for a sentence."""
    s = re.sub(r"\s*\([^)]*\)$", "", _plain(marker))
    return s[:1].lower() + s[1:]


def _count(pct: float, total: int) -> int:
    """Calls behind a transcript-sample percentage (the sample is small, so the count reads better)."""
    return round(pct * total / 100)


def _shifts(s: dict) -> str:
    """How many transcript clocks were right, or 5 h 30 m off, against the LeadSquared call."""
    off = s.get("api_5h30_early", 0) + s.get("api_5h30_late", 0)
    return f"{s.get('on_time', 0)} transcript times matched the call log, {off} were 5 h 30 m off and were corrected"


def _gap(tx: dict | None) -> list[tuple[str, int, int]]:
    """Markers sorted by how much more often converting calls had them."""
    if not tx or not tx.get("converted_n") or not tx.get("not_converted_n"):
        return []
    return sorted(((k, v["converted"], v["not_converted"]) for k, v in tx["markers"].items()
                   if v.get("converted") is not None and v.get("not_converted") is not None), key=lambda x: x[2] - x[1])


def _top_gaps(gaps: list) -> list[tuple[str, int, int]]:
    """The markers tied for the biggest lead of converting calls (at most 3), or none when nothing leads."""
    if not gaps or gaps[0][1] <= gaps[0][2]:
        return []
    top = gaps[0][1] - gaps[0][2]
    return [g for g in gaps if g[1] - g[2] == top][:3]


def scorecard_cells(A: dict) -> set[str]:
    return {str(s[k]) for t in A["rank"] for s in [A["teams"][t]] for k in
            ("callers", "dials", "answer_pct", "real", "reached", "talk_min", "credited", "conv_pct", "same_day_owner", "probe",
             "pitch", "obj", "hi_mod_pct") if s[k] is not None}


def _tag(kind: str, text: str | None = None) -> str:
    return f"<span class='tag {kind}'>{e(text or kind)}</span>"


def _team(t: str, T: dict, clip: bool = False) -> str:
    return e(_clip(t) if clip else short(t)) + (_tag("warm") if T.get(t, {}).get("warm") else "")


def _account(A: dict) -> dict:
    """The day's reference figures that the team stories compare against."""
    tot, F, Z = A["totals"], A["funnel"], A["_zip"]
    mean = lambda k: (round(sum(z[k] for z in Z if z[k] is not None) / m) if (m := sum(z[k] is not None for z in Z)) else None)  # noqa: E731
    return {"conv": F["conv_pct"], "answer": round(100 * tot["answered_out"] / tot["outbound"], 1) if tot["outbound"] else None,
            "probe": mean("probe"), "pitch": mean("pitch"), "obj": mean("obj")}


def _dialer_issues(A: dict) -> list[dict]:
    return sorted((p for p in A["people"] if p["dialer_issue"]), key=lambda p: (-p["failed_pct"], -p["dials"], p["name"]))


def _most_flagged(A: dict) -> dict[str, dict]:
    """Callers (10+ long calls) with half or more of their long calls flagged in section 10."""
    I = A.get("integrity") or {}
    return {r["caller"]: r for r in I.get("callers", []) if r["ranked"] and r["flagged_pct"] >= 50}


def _phone_note(A: dict, brief: bool) -> str:
    """P11a: calling-software groups that LeadSquared lists first for some callers, and their callers' sales teams."""
    G = {t: g for t, g in A.get("phone_groups", {}).items() if not brief or t in A["rank"]}
    if not G:
        return ""
    one = len(G) == 1
    names = joined(sorted(e(short(t)) for t in G))
    if brief:
        others = Counter()
        for g in G.values():
            others.update({k: v for k, v in g.items() if k != "no other group"})
        top = [e(short(k)) for k, _ in others.most_common(2)]
        return (f" {names} {'is a group' if one else 'are groups'} for the calling software, not {'a sales team' if one else 'sales teams'}"
                + (f": {'its' if one else 'their'} callers belong to sales teams such as {joined(top)} (section 3)." if top else "."))
    return (f"<div class='note'>{names} {'is a group' if one else 'are groups'} for the calling software, not "
            f"{'a sales team' if one else 'sales teams'}. LeadSquared lists {'it' if one else 'them'} first for some callers, so those "
            "callers count under " + ("it" if one else "them") + " (rule P11). Their sales teams: "
            + "; ".join(f"{e(short(t))}: " + ", ".join(f"{e(short(k))} ({v})" for k, v in g.items()) for t, g in sorted(G.items())) + ".</div>")


# ------------------------------------------------------------------ page 1

def _verdict(A: dict, gaps: list, tx: dict | None) -> tuple[list[str], list[str]]:
    """What happened, as short plain sentences, plus every team figure they quote (validation check 4)."""
    T, r, nums = A["teams"], A["rank"], []
    if not r:
        return ["No team met the ranking rule (3 or more callers and 25 or more real conversations)."], nums
    cw = _when(A["window"]["cw_end"])
    warm = " It mainly calls warm leads, who enrol more easily than front-line leads."
    best, items = T[r[0]], []
    if best["credited"]:
        nums += [str(best[k]) for k in ("conv_pct", "credited", "reached", "callers")]
        items.append(f"<b>Best team: {e(_clip(r[0]))}.</b> {best['conv_pct']}% conversion: {plural(best['credited'], 'enrolment')} for "
                     f"{plural(best['reached'], 'lead')} reached, by {plural(best['callers'], 'caller')}." + (warm if best["warm"] else ""))
    else:
        items.append(f"<b>No ranked team had a credited enrolment</b> up to {_dm(cw)} {cw:%H:%M} IST.")
    for label, t in (("Runner-up", A["runner_up"]),
                     ("Best front-line team", A["best_front_line"] if A["best_front_line"] not in r[:2] else None)):
        if t and T[t]["credited"]:
            nums += [str(T[t]["conv_pct"]), str(T[t]["credited"])]
            items.append(f"<b>{label}: {e(_clip(t))}</b>, {T[t]['conv_pct']}% ({plural(T[t]['credited'], 'enrolment')})."
                         + (warm if T[t]["warm"] and label == "Runner-up" else ""))
    busy = sorted(r, key=lambda t: (-T[t]["real"], t))[:2]
    nums += [str(T[t][k]) for t in busy for k in ("real", "conv_pct")]
    if len(busy) == 1:
        t = busy[0]
        items.append(f"<b>The busiest team</b>, {e(_clip(t))} ({plural(T[t]['real'], 'real conversation')}), had a conversion of {T[t]['conv_pct']}%.")
    else:
        a, b = busy
        items.append(f"<b>The busiest teams</b>, {e(_clip(a))} ({plural(T[a]['real'], 'real conversation')}) and {e(_clip(b))} "
                     f"({T[b]['real']}), had conversions of {T[a]['conv_pct']}% and {T[b]['conv_pct']}%.")
    top = _top_gaps(gaps)
    if top:
        cn, nn = tx["converted_n"], tx["not_converted_n"]
        items.append(f"<b>Biggest gap{'s' if len(top) > 1 else ''} in {cn + nn} recorded calls:</b> when the lead enrolled, callers more "
                     "often " + joined([f"{_brief(k)} ({_count(c, cn)} of {cn} calls, against {_count(x, nn)} of {nn})" for k, c, x in top])
                     + " (section 7).")
    return items, nums


def _funnel(A: dict) -> str:
    F = A["funnel"]
    steps = [(F["calls"], "calls made or received", "from the call log"),
             (F["answered"], "answered", f"{f(share(F['answered'], F['calls']), '%')} of all calls"),
             (F["real"], "real conversations", f"2+ min: {f(share(F['real'], F['answered']), '%')} of answered"),
             (F["reached"], "leads reached", "different people"),
             (F["credited"], "enrolments credited", f"{F['conv_pct']} per 100 leads reached")]
    return ("<div class='funnel'>" + "<div class='arrow'>›</div>".join(
        f"<div class='step'><b>{n(v)}</b><span>{e(label)}</span><em>{e(cap)}</em></div>" for v, label, cap in steps) + "</div>")


def _ranked_rows(A: dict, teams: list[str]) -> str:
    T = A["teams"]
    top = max((T[t]["conv_pct"] for t in teams), default=0) or 1
    return "".join(
        f"<tr{' class=\"top\"' if A['rank'].index(t) < 3 else ''}><td>{A['rank'].index(t) + 1}</td><td>{_team(t, T, clip=True)}</td>"
        f"<td class='nw'>{bar(T[t]['conv_pct'], top, 120, ORANGE if T[t]['warm'] else BLUE)}<b>{T[t]['conv_pct']}%</b></td>"
        f"<td class='n'>{T[t]['credited']}</td><td class='n'>{T[t]['real']}</td></tr>" for t in teams)


def _glance_table(A: dict, k: int = PAGE1_TEAMS[0]) -> str:
    r = A["rank"]
    if not r:
        return "<p>No team met the ranking rule (3 or more callers and 25 or more real conversations).</p>"
    more = len(r) - k
    return (legend([(BLUE, "front-line team"), (ORANGE, "warm-lead team")])
            + "<table><thead><tr><th>#</th><th>Team</th><th>Conversion: enrolments per 100 leads reached</th><th class='n'>Enrolled</th>"
            f"<th class='n'>Real conversations</th></tr></thead>{_ranked_rows(A, r[:k])}</table>"
            + (f"<div class='note'>{plural(more, 'more ranked team')} {'is' if more == 1 else 'are'} in section 3.</div>" if more > 0 else ""))


def _people(A: dict) -> str:
    row = lambda p: (f"<tr><td><b>{e(p['name'])}</b></td><td>{e(_clip(p['team']))}</td><td class='n'>{p['credited']}</td>"  # noqa: E731
                     f"<td class='n'>{p['real']}</td></tr>")
    rows = "".join(row(p) for p in A["rec"][:PAGE1_CALLERS]) or "<tr><td colspan=4>No caller has a credited enrolment yet.</td></tr>"
    if A["coach_case"]:
        rows += "<tr class='div'><td colspan=4>To coach</td></tr>" + row(A["coach_case"])
    return ("<table class='fixed'><colgroup><col style='width:37%'><col style='width:35%'><col style='width:13%'><col style='width:15%'></colgroup>"
            "<thead><tr><th>Caller</th><th>Team</th><th class='n'>Enrolled</th><th class='n'>Real conv.</th></tr></thead>"
            f"{rows}</table>")


def _actions(A: dict, gaps: list, tx: dict | None) -> str:
    T, r, cc = A["teams"], A["rank"], A["coach_case"]
    items, issues = [], _dialer_issues(A)
    if issues:
        w = issues[0]
        items.append(f"<b>Fix the phones first:</b> {plural(len(issues), 'caller')} had half or more of their dials fail, worst "
                     f"{e(w['name'])} ({w['dials']:,} dials, {w['failed_pct']}% failed); judge them after that (section 6).")
    pay = next(((c, x) for k, c, x in gaps if k.startswith("Payment step")), None)
    first = ("<b>End every real conversation with a payment step:</b> the amount, EMI options, the payment link sent "
             "during the call and a date to pay.")
    if pay and pay[0] > pay[1] and not any(k.startswith("Payment step") for k, _, _ in _top_gaps(gaps)):  # else page 1 says it above
        cn, nn = tx["converted_n"], tx["not_converted_n"]
        first += (f" In the recordings checked, {_count(pay[0], cn)} of {cn} calls with leads who enrolled had one, against "
                  f"{_count(pay[1], nn)} of {nn} long calls with leads who did not.")
    items.append(first)
    scored = [t for t in r if T[t]["pitch"] is not None]
    if scored:
        lo, hi = min(T[t]["pitch"] for t in scored), max(T[t]["pitch"] for t in scored)
        items.append(f"<b>Explain the course fully on more calls:</b> Zipteams found a full pitch on {span(lo, hi)} of "
                     f"{'the ranked team' if len(scored) == 1 else 'each ranked team'}'s checked calls."
                     + (" Play the team a call by a caller who enrolled leads (section 6)." if A["rec"] else ""))
    elif A["rec"]:
        items.append("<b>Share what works:</b> ask the callers who enrolled the most (section 6) to play their team one call "
                     "that ended in an enrolment.")
    if cc:
        fl = _most_flagged(A).get(cc["name"])
        items.append(f"<b>Coach the callers with the most real conversations but few enrolments</b>, starting with {e(cc['name'])} "
                     f"({plural(cc['real'], 'real conversation')}, {cc['credited']} enrolled"
                     + (f"; {fl['flagged']} of their {fl['long_calls']} calls of 2+ minutes are flagged in section 10, so listen to those first"
                        if fl else "") + ").")
    return "<ol>" + "".join(f"<li>{i}</li>" for i in items) + "</ol>"


def _summary(A: dict, verdict: list[str], gaps: list, tx: dict | None, page1_teams: int = PAGE1_TEAMS[0]) -> str:
    tot = A["totals"]
    d0, cw_end, fetched = _when(A["window"]["d0"]), _when(A["window"]["cw_end"]), _when(A["window"].get("fetched"))
    title_day = f"{d0:%A} {d0.day} {d0:%B %Y}"  # P65: weekday computed from the date
    read = f" · data read {_dm(fetched)} {fetched:%H:%M} IST" if fetched else ""
    sample = f"<div class='sample'><b>Sample:</b> {e(A['sample'])}</div>" if A.get("sample") else ""
    w = tot["enroll_window"]
    credit = (f"{tot['enroll_credited']} of the {plural(w, 'lead')} who enrolled from {_dm(d0)} to {_dm(cw_end)} "
              f"{'was' if tot['enroll_credited'] == 1 else 'were'} credited to the caller who spoke to the lead on {_dm(d0)}."
              if w else f"No lead enrolled from {_dm(d0)} to {_dm(cw_end)}.")
    return f"""<div class="p1">
<h1>Calling report · {title_day}</h1>
<div class="sub">Which teams and callers turned the calls of {_dm(d0)} into enrolments. Leads who enrolled up to {_dm(cw_end)} {cw_end:%H:%M} IST are counted, because people often pay a few days after the call{read} · Parameters v{VERSION} · prepared {now_ist():%d %b %Y}</div>
{sample}{_funnel(A)}
<div class="box"><ul>{''.join(f'<li>{i}</li>' for i in verdict)}</ul></div>
<h3>Teams ranked</h3>
{_glance_table(A, page1_teams)}
<div class="note">Teams with 3 or more callers and 25 or more real conversations, ranked by enrolments, then conversion. Green rows are the top three. {credit}{_phone_note(A, brief=True)} Terms are explained in section 1.</div>
<div class="cols"><div><h3>Callers to recognise</h3>{_people(A)}</div>
<div><h3>What to do next</h3>{_actions(A, gaps, tx)}</div></div>
</div>"""


# ------------------------------------------------------------------ sections

def _how_to_read(A: dict) -> str:
    d0, cw_end, conv = _when(A["window"]["d0"]), _when(A["window"]["cw_end"]), A["funnel"]["conv_pct"]
    ex = conv if conv else 10
    warm = joined(sorted(e(short(t)) for t in WARM))
    terms = [
        ("Call", "One phone call in LeadSquared's call log, made by a caller (outbound) or received (inbound). Automated welcome, reminder and webinar calls are left out."),
        ("Answered", "The other person picked up. Page 1 counts all calls; the answer rates in the team tables count only the calls callers made (dials)."),
        ("Real conversation", "An answered call that lasted 2 minutes or more."),
        ("Lead reached", "A person with at least one real conversation on the day, counted once however often they were called."),
        ("Enrolment", f"The first time a lead ever moved to the \"Course Enrolled\" stage, between {_dm(d0)} 00:00 and {_dm(cw_end)} {cw_end:%H:%M} IST. Existing students tagged again do not count."),
        ("Credited to", "Each enrolment goes to the caller who talked longest with that lead on the day, on answered calls of any length. A lead that no LeadSquared user spoke to that day is credited to no one."),
        ("Conversion", f"Enrolments credited for every 100 leads reached: {ex}% means about {round(ex)} enrolments for every 100 leads reached. A lead can enrol after a call shorter than 2 minutes, so a few enrolments come from leads not counted as reached."),
        ("Warm-lead team", f"A team that mostly calls people who already registered for a bootcamp or enrolled before ({warm}). Their rates run higher, so compare them with each other."),
        ("Ranked team", "A team with 3 or more callers and 25 or more real conversations, ranked by enrolments credited, then conversion, then real conversations. Smaller teams are listed but not ranked."),
        ("Zipteams scores", "An automatic review of recorded calls, for some teams only. For each checked call it records whether the caller asked about the lead's needs, explained the course fully and answered the lead's concerns well, and how likely the lead seemed to buy. A team's figure is the share of its checked calls where Zipteams found the skill. One input, never the final word."),
        ("Dialer issue", "Half or more of a caller's dials failed to connect, on a day with 20+ dials. That is a phone-system problem, so the caller's numbers are not judged."),
        ("–", "No data, which is not the same as zero."),
    ]
    if A.get("phone_groups"):
        terms.insert(9, ("Calling-software group", f"{joined(sorted(e(short(t)) for t in A['phone_groups']))}: groups for the calling software, not sales teams. LeadSquared lists them first for some callers, so in this report those callers count under them (section 3 shows their sales teams)."))
    toc = "".join(f"<li>{e(s)}</li>" for s in SECTIONS[4:])
    return f"""<div class="newpage"><h2>{SECTIONS[3]}</h2>
<p>This report shows how one day of sales calls turned into enrolments, team by team and caller by caller. Page 1 is the summary; the sections below explain each figure, show it as a chart or table, and say what it means and what to do. All times are India time (IST). Every figure was checked before the report was made (section 11).</p>
<h3>Words used in this report</h3>
<dl class="terms">{''.join(f'<dt>{e(k)}</dt><dd>{v}</dd>' for k, v in terms)}</dl>
<h3>What is inside</h3><ul class="toc">{toc}</ul>
<div class="note">Bars: blue = front-line team, orange = warm-lead team, gray = for comparison. Green rows are the top three teams.</div></div>"""


def _calls_to_enrolments(A: dict) -> str:
    F, tot, days = A["funnel"], A["totals"], A["enrol_days"]
    d0, cw_end = _when(A["window"]["d0"]), _when(A["window"]["cw_end"])
    top = max((d["credited"] + d["not_credited"] for d in days), default=0) or 1
    after = lambda i: "(the day)" if i == 0 else f"(+{plural(i, 'day')})"  # noqa: E731
    rows = "".join(
        f"<tr><td>{_day(datetime.fromisoformat(d['day']))} {after(i)}</td>"
        f"<td>{stacked_bar([d['credited'], d['not_credited']], top, [BLUE, GRAY])}</td>"
        f"<td class='n'>{d['credited']}</td><td class='n'>{d['not_credited']}</td><td class='n'>{d['credited'] + d['not_credited']}</td></tr>"
        for i, d in enumerate(days))
    uncredited = tot["enroll_window"] - tot["enroll_credited"]
    until = f"{_dm(cw_end)} {cw_end:%H:%M} IST"
    means = [f"Of {n(F['calls'])} calls, {n(F['answered'])} ({f(share(F['answered'], F['calls']), '%')}) were answered and "
             f"{plural(F['real'], 'call')} became real conversations with {plural(F['reached'], 'different lead')}."]
    if F["credited"]:
        short_only = F["credited"] - F.get("credited_reached", F["credited"])
        means.append(f"{plural(F['credited'], 'enrolment')} {'was' if F['credited'] == 1 else 'were'} credited to callers by {until}: "
                     f"about 1 for every {round(F['reached'] / F['credited'])} leads reached ({F['conv_pct']}%)."
                     + (f" {F['credited_reached']} of these leads had a real conversation on {_dm(d0)}; {short_only} enrolled after only "
                        f"{'a shorter call' if short_only == 1 else 'shorter calls'}." if short_only else ""))
    else:
        means.append(f"No enrolment was credited to a caller by {until}.")
    if uncredited:
        means.append(f"Another {plural(uncredited, 'lead')} enrolled in the window with no answered call by a LeadSquared user that day, "
                     f"so no caller is credited with {'it' if uncredited == 1 else 'them'}.")
    most = max((d["credited"] for d in days), default=0)
    peak = [d for d in days if d["credited"] == most]
    if most and len(peak) == 1:
        means.append(f"The busiest day for enrolments was {_day(datetime.fromisoformat(peak[0]['day']))} ({most} of {F['credited']} credited).")
    elif most:
        means.append(f"{joined([_day(datetime.fromisoformat(d['day'])) for d in peak])} had the most credited enrolments ({most} each).")
    means.append(f"LeadSquared has {plural(tot['payments'], 'record')} of \"Payment Successful\" for these days"
                 + (", so enrolments, not rupees, are the measure of success here." if not tot["payments"] else "; they are shown as a count only."))
    k = (cw_end.date() - d0.date()).days
    full = cw_end >= d0 + timedelta(days=4) - timedelta(seconds=1)
    window = ("the day and the 3 days after it." if full else
              "the day" + (f" and the {plural(k, 'day')} after it" if k else "") + f" so far. Leads can still enrol up to "
              f"{_dm(d0 + timedelta(days=3))} 23:59 IST, so these counts can still rise.")
    return f"""<div class="keep"><h2>{SECTIONS[4]}</h2>
<p>Each step is a smaller number than the one before it. The day starts with every call made or received and ends with the enrolments credited to callers.</p>
{_funnel(A)}
<h3>When the enrolments happened</h3>
<div class="fig">{legend([(BLUE, "credited to the caller who talked longest with the lead that day"), (GRAY, "not credited: no answered call by a LeadSquared user that day")])}
<table><thead><tr><th>Day</th><th></th><th class='n'>Credited</th><th class='n'>Not credited</th><th class='n'>All</th></tr></thead>{rows}</table></div>
<div class="means"><b class="h">What this means</b><ul>{''.join(f'<li>{m}</li>' for m in means)}</ul></div>
<div class="note">Enrolments are counted from {_dm(d0)} 00:00 to {until}: {window}</div></div>"""


def _teams_compared(A: dict) -> str:
    T, r = A["teams"], A["rank"]
    d0 = _when(A["window"]["d0"])
    owner = f"Enrolled on {_dm(d0)} (owner's team)"
    if r:
        top = max(T[t]["conv_pct"] for t in r) or 1
        rows = "".join(
            f"<tr{' class=\"top\"' if i <= 3 else ''}><td>{i}</td><td>{_team(t, T)}</td><td class='n'>{s['callers']}</td>"
            f"<td class='n'>{s['dials']:,}</td><td class='n'>{s['answer_pct']}%</td><td class='n'>{s['real']}</td>"
            f"<td class='n'>{s['reached']}</td><td class='n'>{s['talk_min']:,}</td><td class='n'><b>{s['credited']}</b></td>"
            f"<td class='nw'>{bar(s['conv_pct'], top, 50, ORANGE if s['warm'] else BLUE)}<b>{s['conv_pct']}%</b></td>"
            f"<td class='n'>{s['same_day_owner']}</td></tr>"
            for i, (t, s) in enumerate(((t, T[t]) for t in r), 1))
        front, warm = [t for t in r if not T[t]["warm"]], [t for t in r if T[t]["warm"]]
        rng = lambda ts: span(min(T[t]["conv_pct"] for t in ts), max(T[t]["conv_pct"] for t in ts))  # noqa: E731
        means = []
        if front:
            means.append(f"Front-line teams had conversions of {rng(front)}" + (f"; warm-lead teams {rng(warm)}." if warm else "."))
        elif warm:
            means.append(f"Every ranked team calls warm leads; their conversions were {rng(warm)}.")
        lo, hi = min(r, key=lambda t: (T[t]["answer_pct"], t)), max(r, key=lambda t: (T[t]["answer_pct"], t))
        means.append(f"Every ranked team answered {T[lo]['answer_pct']}% of dials." if T[lo]["answer_pct"] == T[hi]["answer_pct"] else
                     f"Answer rates ran from {T[lo]['answer_pct']}% ({e(short(lo))}) to {T[hi]['answer_pct']}% ({e(short(hi))}).")
        most = min(r, key=lambda t: (-T[t]["real"], t))
        m = T[most]
        means.append(f"{e(short(most))} had the most real conversations ({m['real']}): {plural(m['credited'], 'enrolment')} for "
                     f"{plural(m['reached'], 'lead')} reached, a conversion of {m['conv_pct']}%.")
        ranked = f"""<div class="keep"><h2>{SECTIONS[5]}</h2><p>Every ranked team, best first. The bars make the conversion column easy to compare: the longer the bar, the higher the figure.</p>
<div class="fig"><table><thead><tr><th>#</th><th>Team</th><th class='n'>Callers</th><th class='n'>Dials</th><th class='n'>Answer rate</th><th class='n'>Real conversations</th><th class='n'>Leads reached</th><th class='n'>Talk minutes</th><th class='n'>Enrolled (credited)</th><th>Conversion</th><th class='n'>{owner}</th></tr></thead>{rows}</table></div></div>
<div class="means"><b class="h">What this means</b><ul>{''.join(f'<li>{x}</li>' for x in means)}</ul></div>
<h3>How to read each column</h3>
<ul><li><b>Callers</b>: people who made at least one outbound dial. <b>Dials</b>: calls they made. <b>Answer rate</b>: share of dials answered.</li>
<li><b>Real conversations</b>: answered calls of 2 minutes or more, made or received. <b>Leads reached</b>: different people with a real conversation. <b>Talk minutes</b>: total time on answered calls.</li>
<li><b>Enrolled (credited)</b>: enrolments credited to the team's callers, each to the caller who talked longest with the lead that day. <b>Conversion</b>: enrolments per 100 leads reached.</li>
<li><b>{owner}</b>: only enrolments on {_dm(d0)} itself, counted for the team that owns the lead, even if another team made the call. The ranking uses Enrolled (credited).</li></ul>"""
    else:
        ranked = f"<h2>{SECTIONS[5]}</h2><p>No team met the ranking rule (3 or more callers and 25 or more real conversations), so no team is ranked. Every team is listed below.</p>"
    small = sorted((t for t in T if t not in r and t not in ("Unassigned", "Not a user")), key=lambda t: (-T[t]["credited"], -T[t]["real"], t))
    small_rows = "".join(f"<tr><td>{_team(t, T)}</td><td class='n'>{T[t]['callers']}</td><td class='n'>{T[t]['dials']:,}</td>"
                         f"<td class='n'>{T[t]['real']}</td><td class='n'>{T[t]['credited']}</td></tr>" for t in small)
    ua, nu = T.get("Unassigned", {}), T.get("Not a user", {})
    table = ('<table class="keep"><thead><tr><th>Team</th><th class="n">Callers</th><th class="n">Dials</th><th class="n">Real conversations</th>'
             f'<th class="n">Enrolled</th></tr></thead>{small_rows}</table>' if small else "<p>Every team with a team name was ranked.</p>")
    return f"""{ranked}
<h3>Teams not ranked</h3>
{table}
<div class="note">These teams had fewer than 3 people dialling or fewer than 25 real conversations, too few to rank fairly. Also in the totals: callers with no team ("Unassigned"): {plural(ua.get('callers', 0), 'caller')} who dialled, {plural(ua.get('real', 0), 'real conversation')}, {ua.get('credited', 0)} enrolled; calls by people who are not LeadSquared users: {n(nu.get('dials', 0) + nu.get('inbound', 0))}.</div>
{_phone_note(A, brief=False)}"""


def _weak_spots(A: dict) -> str:
    T, r, acc = A["teams"], A["rank"], _account(A)
    d0, items = _when(A["window"]["d0"]), []
    scored = [t for t in r if T[t]["probe"] is not None and acc["probe"] is not None and T[t]["probe"] < acc["probe"]]
    weak = sorted(scored, key=lambda t: (T[t]["probe"], t))[:2]
    if weak:
        items.append(("Weakest probing (ranked teams)",
                      "; ".join(f"{e(short(t))}: callers asked about the lead's needs on {T[t]['probe']}% of the calls Zipteams checked"
                                + (f", and Zipteams rated the lead's interest high or moderate on {T[t]['hi_mod_pct']}% of them"
                                   if T[t]["hi_mod_pct"] is not None else "") for t in weak)
                      + f" (all checked calls: {acc['probe']}%).",
                      "Coach callers to ask about the lead's goal, background and budget before describing the course."))
    zero = [t for t in r if T[t]["credited"] == 0]
    if zero:
        items.append(("No enrolments despite conversations",
                      "; ".join(f"{e(short(t))} ({plural(T[t]['real'], 'real conversation')})" for t in zero) + ".",
                      "The team leader listens to three of the longest calls and checks whether each ended with a payment step."))
    if r and acc["answer"] is not None:
        la = min(r, key=lambda t: (T[t]["answer_pct"], t))
        if T[la]["answer_pct"] < LOW_ANSWER_SHARE * acc["answer"]:
            items.append(("Low answer rate", f"{e(short(la))}: {T[la]['answer_pct']}% of {T[la]['dials']:,} dials answered, below "
                          f"{round(100 * LOW_ANSWER_SHARE)}% of the day's {acc['answer']}%.",
                          "Check the dialer and the lead list before judging the callers."))
    if r:
        busy = min(r, key=lambda t: (-T[t]["talk_min"], t))
        if busy != r[0] and T[busy]["conv_pct"] < acc["conv"]:
            items.append(("Most talk time, low return", f"{e(short(busy))}: {T[busy]['talk_min']:,} minutes of talk, the most of any ranked "
                          f"team, for a conversion of {T[busy]['conv_pct']}% (the day: {acc['conv']}%).",
                          "Listen to three of the team's longest calls that did not enrol and check that each ended with a payment step: "
                          "amount, EMI, the link sent on the call and a date to pay."))
    issues = _dialer_issues(A)
    if issues:
        items.append(("Dialer problems", f"{plural(len(issues), 'caller')} had half or more of their dials fail (listed in section 6).",
                      "Fix the phones first: half or more of these callers' dials never connected, so they reached fewer leads than "
                      "their effort should have."))
    body = "".join(f"<tr><td><b>{e(k)}</b></td><td>{v}</td><td>{e(w)}</td></tr>" for k, v, w in items)
    return (f"<h2>{SECTIONS[6]}</h2><p>The biggest problems in the day's calling, and the first thing to do about each.</p>"
            + (f"<table><thead><tr><th style='width:20%'>Where</th><th style='width:44%'>What happened</th><th>What to do</th></tr></thead>{body}</table>"
               if items else f"<p>Nothing stood out on {_dm(d0)}.</p>"))


SKILL = {"probe": ("asked about the lead's needs", "asking about the lead's needs"),
         "pitch": ("explained the course fully", "explaining the course fully"),
         "obj": ("answered the lead's concerns well", "answering the lead's concerns")}


def _team_story(A: dict, t: str, acc: dict) -> tuple[list[str], str]:
    """Plain sentences on one ranked team, and one suggested next step, from its figures against the day's."""
    s, d0 = A["teams"][t], _when(A["window"]["d0"])
    team_people = sorted((p for p in A["people"] if p["team"] == t), key=lambda p: (-p["credited"], -p["real"], -p["talk_min"], p["name"]))
    stars = [p for p in team_people if p["credited"]][:3]
    low_answer = acc["answer"] is not None and s["answer_pct"] < LOW_ANSWER_SHARE * acc["answer"]
    cmp = "higher than" if s["conv_pct"] > acc["conv"] else "lower than" if s["conv_pct"] < acc["conv"] else "the same as"
    lines = [f"{s['conv_pct']}% conversion: {plural(s['credited'], 'enrolment')} for the {plural(s['reached'], 'lead')} it reached, "
             f"{cmp} the day's {acc['conv']}%" + (" (it mainly calls warm leads)." if s["warm"] else ".")]
    lines.append(f"{s['answer_pct']}% of {s['dials']:,} dials were answered (the day: {f(acc['answer'], '%')})"
                 + ("; that is low." if low_answer else "."))
    lines.append(f"{plural(s['real'], 'real conversation')} and {s['talk_min']:,} minutes of talk, about "
                 f"{round(s['real'] / s['callers']) if s['callers'] else '–'} real conversations per caller.")
    scores = {k: s[k] for k in ("probe", "pitch", "obj") if s[k] is not None}
    if s["zip_n"] and scores:
        lines.append(f"Zipteams checked {plural(s['zip_n'], 'call')}: " + ", ".join(f"{v}% {SKILL[k][0]}" for k, v in scores.items()) + ".")
    if s["dialer_issue_callers"]:
        lines.append(f"{plural(s['dialer_issue_callers'], 'caller')} had a dialer issue.")
    lines.append("Top callers: " + joined([f"{e(p['name'])} ({p['credited']} enrolled)" for p in stars]) + "." if stars
                 else f"No caller enrolled a lead from the calls of {_dm(d0)}.")
    weakest = min(scores, key=lambda k: (scores[k] - (acc[k] or 0), k)) if scores else None
    if low_answer:
        step = "Check the dialer and the lead list: few dials were answered."
    elif not s["credited"]:
        step = "Listen to three of the longest calls that did not enrol and check that each ended with a payment step."
    elif weakest and acc[weakest] is not None and scores[weakest] < acc[weakest]:
        step = (f"Coach on {SKILL[weakest][1]}: done well on {scores[weakest]}% of this team's checked calls, against "
                f"{acc[weakest]}% across all checked calls.")
    elif s["conv_pct"] >= acc["conv"] and stars:
        step = f"Ask {e(stars[0]['name'])} to play the team a recording of a call that enrolled, so the others can copy what works."
    else:
        step = "End every real conversation with a payment step: amount, EMI, the link sent on the call and a date to pay."
    if s["dialer_issue_callers"]:
        stuck = [e(p["name"]) for p in team_people if p["dialer_issue"]]
        who = joined(stuck[:3] + ([plural(len(stuck) - 3, "more caller")] if len(stuck) > 3 else []))
        step = f"First fix the dialer for {who} (half or more of their dials failed). Then: {step[:1].lower()}{step[1:]}"
    return lines, step


def _team_cards(A: dict) -> str:
    T, acc = A["teams"], _account(A)
    cards = []
    for i, t in enumerate(A["rank"], 1):
        s = T[t]
        lines, step = _team_story(A, t, acc)
        mini = "".join(f"<div><b>{v}</b>{k}</div>" for k, v in (("callers", s["callers"]), ("dials", f"{s['dials']:,}"),
                       ("answered", f"{s['answer_pct']}%"), ("real conv.", s["real"]), ("enrolled", s["credited"]),
                       ("conversion", f"{s['conv_pct']}%")))
        cards.append(f"<div class='card'><h3>#{i} {_team(t, T)}</h3><div class='mini'>{mini}</div>"
                     f"<ul>{''.join(f'<li>{x}</li>' for x in lines)}</ul><div class='next'><b>Suggested next step:</b> {step}</div></div>")
    return (f"<div class='{'newpage' if cards else ''}'><h2>{SECTIONS[7]}</h2><p>One card per ranked team: its figures, what they mean against the whole day, "
            "and one suggested next step for the team leader.</p>"
            + (f"<div class='cards'>{''.join(cards)}</div>" if cards else "<p>No team met the ranking rule.</p>") + "</div>")


def _callers(A: dict) -> str:
    rec, sup, assets = A["rec"], A["support"], A["assets"]
    d0, ties = _when(A["window"]["d0"]), A.get("ties_left_out", {})
    more = lambda k: f" {plural(k, 'more caller')} had the same figures as the last one listed (see the appendix)." if k else ""  # noqa: E731
    top = max((p["credited"] for p in rec), default=0) or 1
    rec_rows = "".join(f"<tr><td><b>{e(p['name'])}</b></td><td>{e(short(p['team']))}</td>"
                       f"<td class='nw'>{bar(p['credited'], top, 60)}<b>{p['credited']}</b></td><td class='n'>{p['real']}</td>"
                       f"<td class='n'>{p['reached']}</td></tr>" for p in rec)
    sup_rows = "".join(f"<tr><td><b>{e(p['name'])}</b>{_tag('coach', 'to coach') if p is A['coach_case'] else ''}</td>"
                       f"<td>{e(short(p['team']))}</td><td class='n'>{p['real']}</td><td class='n'>{p['talk_min']:,}</td>"
                       f"<td class='n'>{p['credited']}</td><td class='n'>{f(p['pitch'], '%')}</td></tr>" for p in sup)
    if sup:
        flagged = _most_flagged(A)
        fl = [p for p in sup if p["name"] in flagged]
        sup_note = (f"These {plural(len(sup), 'caller')} had the most real conversations among callers with at most one enrolment: "
                    f"{sum(p['real'] for p in sup)} real conversations and {plural(sum(p['credited'] for p in sup), 'enrolment')} between them. "
                    "Listen to two of each one's calls with their team leader to find what is missing."
                    + (f" {joined([e(p['name']) + ' (' + str(flagged[p['name']]['flagged']) + ' of ' + str(flagged[p['name']]['long_calls']) + ')' for p in fl])} "
                       f"{'has' if len(fl) == 1 else 'have'} most of their calls of 2+ minutes flagged in section 10: check those calls first."
                       if fl else "")
                    + more(ties.get("support", 0)) + " Callers with a dialer issue are left out until their phones are fixed.")
        sup_means = f'<div class="means"><b class="h">What this means</b> {sup_note}</div>'
    else:
        sup_means = ""
    issues = _dialer_issues(A)
    iss_rows = "".join(f"<tr><td>{e(p['name'])}</td><td>{e(short(p['team']))}</td><td class='n'>{p['dials']:,}</td>"
                       f"<td class='n'>{p['failed_pct']}%</td></tr>" for p in issues)
    asset_rows = "".join(f"<tr><td><b>{e(p['name'])}</b></td><td>{e(short(p['team']))}</td><td class='n'>{p['zip_n']}</td>"
                         f"<td class='n'>{f(p['probe'], '%')}</td><td class='n'>{f(p['pitch'], '%')}</td><td class='n'>{f(p['obj'], '%')}</td>"
                         f"<td class='n'>{p['credited']}</td></tr>" for p in assets)
    zero = [e(p["name"]) for p in assets if not p["credited"]]
    both = [e(p["name"]) for p in assets if any(p is q for q in sup)]
    asset_note = (f"Callers with {ASSET_MIN_NOTES} or more calls checked by Zipteams and the best scores for asking about needs, explaining "
                  "the course and answering concerns."
                  + ((" None of them" if len(zero) == len(assets) else f" {joined(zero)}") + f" had a credited enrolment from the calls of "
                     f"{_dm(d0)}, so use their calls to teach questioning and explaining the course, not closing." if zero else "")
                  + (f" {joined(both)} {'is' if len(both) == 1 else 'are'} also under Callers who need support." if both else ""))
    return f"""<h2>{SECTIONS[8]}</h2>
<h3>Callers to recognise</h3>
<table class="keep"><thead><tr><th>Caller</th><th>Team</th><th>Enrolled (credited)</th><th class='n'>Real conversations</th><th class='n'>Leads reached</th></tr></thead>{rec_rows or '<tr><td colspan=5>No caller has a credited enrolment yet.</td></tr>'}</table>
<div class="note">The callers with the most enrolments credited to them, then the most real conversations. A lead can enrol after a short call, so a caller's enrolments can exceed the leads they reached.{more(ties.get('rec', 0))} Every caller's figures are in the appendix.</div>
<h3>Callers who need support</h3>
<table class="keep"><thead><tr><th>Caller</th><th>Team</th><th class='n'>Real conversations</th><th class='n'>Talk minutes</th><th class='n'>Enrolled</th><th class='n'>Full pitch (share of checked calls)</th></tr></thead>{sup_rows or '<tr><td colspan=6>No caller qualified.</td></tr>'}</table>
{sup_means}
<h3>Callers with a dialer issue</h3>
{'<table class="keep"><thead><tr><th>Caller</th><th>Team</th><th class="n">Dials</th><th class="n">Dials that failed to connect</th></tr></thead>' + iss_rows + '</table><div class="note">Half or more of these callers&#39; dials failed to connect. Fix the phone system before judging their numbers.</div>' if issues else '<p>No caller had half or more of their dials fail.</p>'}
<h3>Good questioning and explaining (Zipteams scores)</h3>
{'<table class="keep"><thead><tr><th>Caller</th><th>Team</th><th class="n">Calls checked</th><th class="n">Asked about needs</th><th class="n">Explained the course</th><th class="n">Answered concerns</th><th class="n">Enrolled</th></tr></thead>' + asset_rows + f'</table><div class="note">{asset_note}</div>' if assets else f'<p>No caller had {ASSET_MIN_NOTES} or more calls checked by Zipteams.</p>'}"""


def _why_won(gaps: list, tx: dict | None) -> str:
    head = f"<h2>{SECTIONS[9]}</h2>"
    if not tx:
        return head + "<p>No recorded calls were read for this report.</p>"
    cn, nn = tx.get("converted_n", 0), tx.get("not_converted_n", 0)
    if not cn or not nn:
        return head + (f"<p>Transcripts were read for {plural(cn, 'call')} with leads who enrolled and {plural(nn, 'long call')} with "
                       "leads who did not. Both kinds are needed for a comparison, so there is none for this day.</p>")
    rows = sorted(((k, v.get("converted"), v.get("not_converted")) for k, v in tx["markers"].items()),
                  key=lambda x: -((x[1] or 0) - (x[2] or 0)))
    chart = paired_bars([(_plain(k), c, x) for k, c, x in rows],
                        (f"calls with leads who enrolled ({plural(cn, 'call')})", f"long calls with leads who did not enrol ({plural(nn, 'call')})"))
    more = [(k, c, x) for k, c, x in gaps if c > x][:3]
    same = [(k, c, x) for k, c, x in gaps if c <= x][:2]
    means = [f"<b>{e(_plain(k))}</b>: {_count(c, cn)} of {cn} calls with leads who enrolled, against {_count(x, nn)} of {nn} long calls "
             "with leads who did not." for k, c, x in more]
    if same:
        means.append("As common or more common in calls with leads who did not enrol, so not decisive: "
                     + "; ".join(f"{e(_brief(k))} ({_count(c, cn)} of {cn} against {_count(x, nn)} of {nn})" for k, c, x in same) + ".")
    a, b = tx.get("converted_median_min"), tx.get("not_converted_median_min")
    if a is not None and b is not None:
        means.append(f"Both kinds of call read had a median length of {a} minutes." if a == b else
                     f"The calls read with leads who enrolled were {'longer' if a > b else 'shorter'} (median {a} minutes, against {b} for the "
                     f"others), so they had {'more' if a > b else 'less'} time to include each step.")
    return head + f"""<p>We searched the written transcripts of {plural(cn, 'call')} with leads who enrolled and {plural(nn, 'long call')} (5 minutes or more) with leads who did not, for words that show eight things a caller can do. Each pair of bars shows how often each kind of call had it.</p>
<div class="fig">{chart}</div>
<div class="means"><b class="h">What this means</b><ul>{''.join(f'<li>{m}</li>' for m in means) or '<li>No clear difference between the two kinds of call.</li>'}</ul></div>
<div class="note">A small sample: the figures show a pattern to coach on, not proof. Words are matched in the transcript text (English and Hinglish keywords).</div>"""


def _quality(A: dict) -> str:
    T, tot, r = A["teams"], A["totals"], A["rank"]
    d0, head = _when(A["window"]["d0"]), f"<h2>{SECTIONS[10]}</h2>"
    if not tot["zip_attr"]:
        return head + f"<p>No Zipteams notes were matched to a call of {_dm(d0)}, so there are no scores.</p>"
    row = lambda t: (f"<tr><td>{_team(t, T)}</td><td class='n'>{T[t]['zip_n']}</td>"  # noqa: E731
                     + "".join(f"<td class='nw'>{bar(T[t][k], 100, 50)}{f(T[t][k], '%')}</td>" for k in ("probe", "pitch", "obj", "hi_mod_pct"))
                     + "</tr>")
    others = sorted(t for t in T if t not in r and t not in ("Unassigned", "Not a user") and T[t]["zip_n"])
    rows = "".join(row(t) for t in r) + ("<tr class='div'><td colspan=6>Teams not ranked</td></tr>" + "".join(row(t) for t in others)
                                        if others else "")
    hidden = sum(T[t]["zip_n"] for t in ("Unassigned", "Not a user") if t in T)
    missing = [short(t) for t in r if T[t]["zip_n"] == 0]
    scored = [t for t in r if T[t]["zip_n"]]
    means = []
    for k, name in (("probe", "asking about the lead's needs"), ("pitch", "a full pitch")):
        vals = [t for t in scored if T[t][k] is not None]
        if len(vals) > 1:
            lo, hi = min(vals, key=lambda t: (T[t][k], t)), max(vals, key=lambda t: (T[t][k], t))
            means.append(f"Among ranked teams, {name} ranged from {T[lo][k]}% ({e(short(lo))}) to {T[hi][k]}% ({e(short(hi))})."
                         if T[lo][k] != T[hi][k] else f"Every ranked team scored {T[lo][k]}% for {name}.")
    if missing:
        means.append(f"No Zipteams scores for {joined([e(t) for t in missing])} (shown as \"–\").")
    means.append("Zipteams is one input, never the answer: a lead it rates low can still enrol, and the other way round.")
    return head + f"""<p>Zipteams reviews recorded calls automatically and marks each one. It checked {plural(tot['zip_attr'], 'call')} from {_dm(d0)}{f"; {hidden} of them, by callers with no team, are not shown" if hidden else ""}. Each bar is the share of a team's checked calls where Zipteams found the skill or, in the last column, rated the lead's interest high or moderate.</p>
<table><thead><tr><th>Team</th><th class='n'>Calls checked</th><th>Asked about needs</th><th>Explained the course fully</th><th>Answered concerns well</th><th>Lead's interest high or moderate</th></tr></thead>{rows}</table>
<div class="means"><b class="h">What this means</b><ul>{''.join(f'<li>{m}</li>' for m in means)}</ul></div>"""


def _hours(A: dict) -> str:
    H = A["hours"]
    head = f"<h2>{SECTIONS[11]}</h2>"
    total = sum(h["dials"] for h in H)
    if not total:
        return head + "<p>No dials on this day.</p>"
    labels = [f"{h['hour']:02d}:00" for h in H]
    busiest = max(range(len(H)), key=lambda i: H[i]["dials"])
    enough = [i for i in range(len(H)) if H[i]["dials"] and 100 * H[i]["dials"] / total >= HOUR_MIN_SHARE]
    best = max(enough, key=lambda i: H[i]["answer_pct"]) if enough else None
    worst = min(enough, key=lambda i: H[i]["answer_pct"]) if enough else None
    most_real = max(range(len(H)), key=lambda i: H[i]["real"])
    hour = lambda i: f"{H[i]['hour']:02d}:00–{H[i]['hour'] + 1:02d}:00"  # noqa: E731
    means = [f"The busiest hour was {hour(busiest)} with {H[busiest]['dials']:,} dials ({share(H[busiest]['dials'], total)}% of the day's)."]
    if best is not None and worst is not None and H[best]["answer_pct"] != H[worst]["answer_pct"]:
        means.append(f"Leads answered most often at {hour(best)} ({H[best]['answer_pct']}% of dials) and least often at "
                     f"{hour(worst)} ({H[worst]['answer_pct']}%).")
    means.append(f"Most real conversations happened at {hour(most_real)} ({H[most_real]['real']}).")
    every = set(range(len(H))) if len(H) <= 14 else None  # every bar labelled while the labels fit
    marks = {busiest} | ({best, worst} if best is not None else set())
    answered_at = {i for i in (every or marks) if H[i]["answer_pct"] is not None}

    def block(hs: list[dict]) -> str:
        return ("<table class='hours keep'><thead><tr><th>Hour (IST)</th>" + "".join(f"<th>{h['hour']:02d}:00</th>" for h in hs) + "</tr></thead>"
                + "".join(f"<tr><td>{name}</td>" + "".join(f"<td>{fmt(h)}</td>" for h in hs) + "</tr>" for name, fmt in (
                    ("Dials", lambda h: f"{h['dials']:,}"), ("Share answered", lambda h: f(h["answer_pct"], "%")),
                    ("Real conversations", lambda h: h["real"]), ("Inbound calls", lambda h: h["inbound"]))) + "</table>")

    tables = "".join(block(H[i:i + HOURS_PER_TABLE]) for i in range(0, len(H), HOURS_PER_TABLE))
    return head + f"""<p>Calls in each hour of the day (India time). Use it to plan the hours when callers dial most.</p>
<div class="fig"><h3>Dials made each hour</h3>{legend([(BLUE, "busiest hour"), (GRAY, "other hours")])}{columns(labels, [h['dials'] for h in H], label_at=every or {busiest}, highlight={busiest})}</div>
<div class="fig"><h3>Share of dials answered each hour</h3>{columns(labels, [h['answer_pct'] for h in H], unit='%', label_at=answered_at, height=110)}</div>
{tables}
<div class="means"><b class="h">What this means</b><ul>{''.join(f'<li>{m}</li>' for m in means)}</ul></div>
<div class="note">Best and worst hours count only hours with at least {HOUR_MIN_SHARE}% of the day's dials, so a quiet hour can't top the list by chance.</div>"""


def _integrity(A: dict) -> str:
    """P67-P74: long calls flagged for review, never proof."""
    head = f"<h2>{SECTIONS[12]}</h2>"
    I, F = A.get("integrity"), A["funnel"]
    if not I:
        return head + "<p>The call checks were not run for this day.</p>"
    if not I["long_calls"]:
        return head + "<p>There were no answered calls of 2 minutes or more to check.</p>"
    sampled = I.get("sampled")
    vals = {k: None if k in TRANSCRIPT_FLAGS and not sampled else v for k, v in I["by_flag"].items() if k in FLAG_PLAIN}
    top = max((v for v in vals.values() if v), default=0) or 1
    rows = "".join(f"<tr><td><b>{e(FLAG_PLAIN[k][0])}</b></td><td>{e(FLAG_PLAIN[k][1])}</td><td class='nw'>{bar(v, top, 80)}{f(v)}</td></tr>"
                   for k, v in vals.items())
    who = [r for r in I["callers"] if r["ranked"] and r["flagged"]][:5]
    who_rows = "".join(f"<tr><td>{e(r['caller'])}</td><td>{e(short(r['team']))}</td><td class='n'>{r['long_calls']}</td>"
                       f"<td class='n'>{r['flagged']}</td><td class='n'>{r['flagged_pct']}%</td></tr>" for r in who)
    checked = (f"All {plural(I['long_calls'], 'real conversation')} were checked" if I["long_calls"] == F["real"] else
               f"Of the {plural(F['real'], 'real conversation')}, {I['long_calls']:,} were made or taken by LeadSquared users and checked")
    tx = (f"Transcripts were read for {I['transcripts_matched']} of {plural(sampled, 'sampled call')}." if sampled else
          "The last four checks need call transcripts, which were not read for this report, so they show \"–\" (not checked).")
    return head + f"""<p>Some long calls may not be real sales conversations: a call left running, two calls at once, or a recorded message. {checked}; <b>{I['flagged_calls']}</b> of them {'was' if I['flagged_calls'] == 1 else 'were'} flagged for a team leader to listen to. A flag is a reason to listen, not proof of anything.</p>
<table class="keep"><thead><tr><th>Flag</th><th>What it means</th><th>Calls</th></tr></thead>{rows}</table>
<div class="note">{tx} A call can carry more than one flag.</div>
<div class="keep"><h3>Callers with the most flagged calls</h3>
{'<table><thead><tr><th>Caller</th><th>Team</th><th class="n">Calls of 2+ min</th><th class="n">Flagged</th><th class="n">Share flagged</th></tr></thead>' + who_rows + '</table>' if who else '<p>No caller with 10 or more long calls had a flagged call.</p>'}
<div class="note">Only callers with 10 or more calls of 2+ minutes are listed. The call-by-call list (call IDs, no lead details) is in integrity_calls.csv in the data folder.</div></div>"""


def _method(A: dict, tx: dict | None, validation: str, checks: list[dict] | None) -> str:
    tot, T = A["totals"], A["teams"]
    d0, cw_end = _when(A["window"]["d0"]), _when(A["window"]["cw_end"])
    no_zip = [short(t) for t in A["rank"] if T[t]["zip_n"] == 0]
    days, multi = tot.get("edit_margin_days", 0), tot["multi_group_callers"]
    if not tx:
        tx_note = "<b>Recorded calls:</b> none were read for this report."
    else:
        tx_note = (f"<b>Recorded calls:</b> {plural(tx['numbers'], 'lead')} searched: {tx['sample_conv']} who enrolled, and "
                   f"{tx['sample_non']} who did not but had a call of 5+ minutes (up to {tx.get('per_team', 10)} chosen at random from each of "
                   + (joined([e(short(t)) for t in tx["top5"]]) if tx.get("top5") else "no team, as none was ranked")
                   + f"); {plural(tx['requests'], 'request')} to the transcript service"
                   + (f", {tx['failed_chunks']} failed" if tx.get("failed_chunks") else "")
                   + "; for each lead the longest transcript of 2+ minutes that matches one of the day's LeadSquared calls"
                   + (f" ({_shifts(tx['time_shifts'])})" if tx.get("time_shifts") else "") + ".")
    method = [
        f"<b>Day:</b> {_dm(d0)} 00:00–23:59 IST. Calls are LeadSquared phone calls that started on the day"
        + (f", read again up to {plural(days, 'day')} later so calls edited after the day are kept "
           f"({tot.get('late_edits_recovered', 0)} were)" if days else "")
        + f". {plural(tot['bots_excluded'], 'automated call')} and {plural(tot['outside_window_excluded'], 'call')} that started on "
        "another day were left out.",
        "<b>Real conversation:</b> answered and 120 seconds or longer.",
        f"<b>Enrolment:</b> a lead's first-ever stage change to \"Course Enrolled\" from {_dm(d0)} 00:00 to {_dm(cw_end)} {cw_end:%H:%M} IST "
        f"({tot['enroll_window']} found), credited to the caller with the most answered talk time on that lead on the day "
        f"({tot['enroll_credited']} credited). Conversion = credited ÷ leads reached.",
        f"<b>Payments:</b> {plural(tot['payments'], 'record')} of \"Payment Successful\" in the window"
        + (", so enrolments are the measure of success." if not tot["payments"] else ", shown as a count only."),
        (f"<b>Zipteams:</b> {tot['zip_attr']:,} of {plural(tot['zip_total'], 'note')} matched to the call they analysed, the lead's last "
         f"answered call before the note ({tot['zip_dropped']} had no such call; {tot.get('zip_after_day_kept', 0)} were written after "
         "midnight for a late call)" + (f". No Zipteams notes for {joined([e(t) for t in no_zip])} (\"–\")." if no_zip else ".")
         if tot["zip_total"] else "<b>Zipteams:</b> no Zipteams notes were written for this day, so section 8 has no scores."),
        f"<b>Teams:</b> each caller counts in the first group LeadSquared lists for them (rule P11); {plural(multi, 'caller')} "
        f"{'belongs' if multi == 1 else 'belong'} to more than one group. Warm-lead teams "
        f"({joined(sorted(e(short(t)) for t in WARM))}) are marked \"warm\".",
        tx_note,
    ]
    status = lambda ok: "<td class='ok nw'>✓ Passed</td>" if ok else "<td class='nw'>✗ Failed</td>"  # noqa: E731
    rows = "".join(f"<tr>{status(c['ok'])}<td>{e(c['check'])}</td><td>{e(c['detail'])}</td></tr>" for c in (checks or []))
    return f"""<h2>{SECTIONS[13]}</h2>
<p>How the figures were counted, and the checks every figure passed before this report was made.</p>
<ul>{''.join(f'<li>{m}</li>' for m in method)}</ul>
<h3>Checks run before this report was made</h3>
<p class="ok">{e(validation)}.</p>
<div class="note">Any failed check stops the report: no PDF is written until the data is fixed. Parameters v{VERSION}.</div>
{'<table><thead><tr><th style="width:9%">Result</th><th style="width:30%">Check</th><th>Detail</th></tr></thead>' + rows + '</table>' if rows else ''}"""


def _appendix(A: dict) -> str:
    T, r = A["teams"], A["rank"]
    rec = {id(p) for p in A["rec"]}
    order = r + sorted(t for t in T if t not in r and t not in ("Unassigned", "Not a user")) + (["Unassigned"] if "Unassigned" in T else [])
    body = []
    for t in order:
        ps = sorted((p for p in A["people"] if p["team"] == t), key=lambda p: (-p["credited"], -p["real"], p["name"]))
        if not ps:
            continue
        took = sum(1 for p in ps if not p["dials"])
        body.append(f"<tr class='team'><td colspan='9'>{e(short(t))}{' (warm leads)' if T[t]['warm'] else ''} · "
                    f"{plural(T[t]['callers'], 'caller')} who dialled" + (f", {took} who only took calls" if took else "") + "</td></tr>")
        for p in ps:
            tags = (_tag("coach", "recognised") if id(p) in rec else "") + (_tag("coach", "to coach") if p is A["coach_case"] else "") \
                + (_tag("dialer", "dialer issue") if p["dialer_issue"] else "")
            body.append(f"<tr><td>{e(p['name'])}{tags}</td><td class='n'>{p['dials']:,}</td>"
                        f"<td class='n'>{f(p['answer_pct'], '%') if p['dials'] else '–'}</td><td class='n'>{p['real']}</td>"
                        f"<td class='n'>{p['talk_min']:,}</td><td class='n'>{p['credited']}</td>"
                        f"<td class='n'>{p['reached']}</td><td class='n'>{p['zip_n'] or '–'}</td>"
                        f"<td class='n'>{f(p['failed_pct'], '%')}</td></tr>")
    return f"""<div class="newpage"><h2>{SECTIONS[14]}</h2>
<p>Every caller who made or took a call, grouped by team (ranked teams first), most enrolments first. "Dials failed" is the share of dials that did not connect; half or more on 20+ dials is a dialer issue.</p>
<table class="app"><thead><tr><th>Caller</th><th class='n'>Dials</th><th class='n'>Answer rate</th><th class='n'>Real conv.</th><th class='n'>Talk min</th><th class='n'>Enrolled</th><th class='n'>Leads reached</th><th class='n'>Calls checked by Zipteams</th><th class='n'>Dials failed</th></tr></thead>{''.join(body)}</table></div>"""


def report_html(A: dict, tx: dict | None, validation: str = "", checks: list[dict] | None = None,
                summary_only: bool = False, page1_teams: int = PAGE1_TEAMS[0]) -> tuple[str, list[str]]:
    """Returns (html, the team figures quoted on page 1). ``checks`` are the validation results printed in
    section 11; ``summary_only`` renders page 1 alone, which the report uses to check that it fits on one page,
    and ``page1_teams`` caps the ranked teams drawn there (the rest are in section 3)."""
    d0 = _when(A["window"]["d0"])
    gaps = _gap(tx)
    verdict, nums = _verdict(A, gaps, tx)
    foot = f"Calling report · {d0:%a} {d0.day} {d0:%b %Y} · Parameters v{VERSION}" + (" · SAMPLE, partial data" if A.get("sample") else "")
    css = CSS.replace("%FOOT%", foot.replace("\\", "\\\\").replace('"', '\\"'))
    body = _summary(A, verdict, gaps, tx, page1_teams)
    if not summary_only:
        body += (_how_to_read(A) + _calls_to_enrolments(A) + _teams_compared(A) + _weak_spots(A) + _team_cards(A) + _callers(A)
                 + _why_won(gaps, tx) + _quality(A) + _hours(A) + _integrity(A) + _method(A, tx, validation, checks) + _appendix(A))
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Calling report {A['date']}</title><style>{css}</style></head><body>
{body}
</body></html>"""
    return page, nums


def sections_present(doc: str) -> list[str]:
    """P62 sections missing from the report or out of order (empty when all are there in order)."""
    pos, missing = 0, []
    for s in SECTIONS:
        i = doc.find(f">{e(s)}</h", pos)
        if i < 0:
            missing.append(s)
        else:
            pos = i
    return missing


def tracker_html(P: dict) -> str:
    acc = P["account"]
    head = ("<tr><th>{}</th><th class='n'>Zip-scored calls</th><th class='n'>Payment step (Zip summary)</th><th class='n'>Full pitch</th>"
            "<th class='n'>Missed inbound leads</th><th class='n'>Called back same day</th><th class='n'>Median callback</th><th class='n'>Dial failures</th>{}</tr>")
    row = lambda name, x, extra="": (f"<tr><td>{e(name)}</td><td class='n'>{x['zip_n']}</td><td class='n'>{f(x['payment_step_zip_pct'], '%')}</td>"  # noqa: E731
                                     f"<td class='n'>{f(x['full_pitch_pct'], '%')}</td><td class='n'>{x['missed_inbound_leads']}</td>"
                                     f"<td class='n'>{f(x['called_back_same_day_pct'], '%')}</td><td class='n'>{f(x['median_callback_min'], ' min')}</td>"
                                     f"<td class='n'>{f(x['dial_failure_pct'], '%')}</td>{extra}</tr>")
    teams = "".join(row(short(t["team"]), t, f"<td class='n'>{f(t['payment_step_transcript_pct'], '%')} (n={t['transcripts_n']})</td>")
                    for t in sorted(P["teams"], key=lambda t: -t["zip_n"]))
    callers = "".join(row(f"{c['caller']} · {short(c['team'])}", c) for c in
                      sorted((c for c in P["callers"] if c["zip_n"] >= 5), key=lambda c: (c["payment_step_zip_pct"] or 0, -c["zip_n"]))[:30])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Plan tracker {P['date']}</title><style>{TRACKER_CSS}</style></head><body>
<h1>Revenue plan tracker — {P['date']}</h1>
<div class="sub">Who is adopting the plan's levers. Separate from the Parameters v{VERSION} report, whose figures it never changes.</div>
<div class="box">Account: payment step in {f(acc['payment_step_zip_pct'], '%')} of Zipteams-scored calls; full pitch {f(acc['full_pitch_pct'], '%')};
{f(acc['called_back_same_day_pct'], '%')} of {acc['missed_inbound_leads']} leads with a missed inbound call were called back the same day (median {f(acc['median_callback_min'], ' min')});
dial failures {f(acc['dial_failure_pct'], '%')}. Transcript sample: payment step in {f(acc['payment_step_transcript_converted_pct'], '%')} of converting vs {f(acc['payment_step_transcript_not_converted_pct'], '%')} of long non-converting calls.</div>
<h2>Teams</h2><table class="full">{head.format('Team', "<th class='n'>Payment step (transcripts)</th>")}{teams}</table>
<h2>Callers with 5+ Zipteams-scored calls, lowest payment-step rate first</h2><table class="full">{head.format('Caller', '')}{callers}</table>
<div class="note">Payment step (Zip summary) = the Zipteams summary says a payment was set up or made on the call (payment link, NEFT/UPI/scanner payment, booking or token amount, seat block, balance amount; analytics/call_markers.py); the transcript column uses the report's transcript sample, so its n is small. Full pitch = Zipteams mx_Custom_4. A missed inbound call counts once per lead and belongs to the caller whose line it rang on; called back = an outbound dial or answered call to that lead later the same IST day. Dial failures = CallFailure ÷ dials; 50%+ on a day is a dialer problem, not the caller.</div>
</body></html>"""
