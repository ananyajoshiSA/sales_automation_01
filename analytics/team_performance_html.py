"""HTML for the daily team performance report and the plan tracker page.

Layout (Parameters v1.1, P62): page 1 answers "who converted best, who to recognise, what to do"
at a glance; page 2 carries the full scorecard, the evidence and the method. Every sentence is
generated from the analysed figures, and the verdict's numbers are returned so the validation gate
can confirm each one appears in the scorecard. Used by analytics.team_performance.
"""

from __future__ import annotations

import html
from datetime import datetime

from analytics.team_performance import VERSION, short

e = html.escape
CSS = """
@page{size:A4;margin:11mm 12mm} body{font-family:Arial,Helvetica,sans-serif;font-size:9.4pt;color:#1d2433;line-height:1.35;margin:0}
h1{font-size:17pt;margin:0;color:#0f2b5b} h2{font-size:11.5pt;color:#0f2b5b;margin:11px 0 4px}
.sub{color:#5b6475;font-size:8.4pt;margin:2px 0 8px} .page2{break-before:page}
.kpis{display:flex;gap:6px;margin-bottom:8px} .k{flex:1;background:#f5f7fb;border:1px solid #dfe5f0;border-radius:5px;padding:6px 8px;font-size:8.2pt;color:#5b6475}
.k b{font-size:15pt;color:#0f2b5b;display:block;line-height:1.2}
.box{background:#eef3fb;border-left:4px solid #0f5bd8;padding:7px 10px;margin:6px 0 4px;font-size:9.6pt}
table{border-collapse:collapse;width:100%;font-size:8.3pt} th{background:#0f2b5b;color:#fff;padding:3px 5px;text-align:left;font-weight:600}
td{padding:3px 5px;border-bottom:1px solid #e1e6ef;vertical-align:middle} td.n{text-align:right;white-space:nowrap} th.n{text-align:right}
tr.top td{background:#e9f7ee} .full{font-size:7.6pt} .full td{padding:2px 4px}
.bar{display:inline-block;height:8px;background:#0f5bd8;border-radius:2px;vertical-align:middle;margin-right:5px}
.bar.warm{background:#c99a1c} .w{font-size:6.8pt;background:#fff1c9;color:#7a5a00;border-radius:3px;padding:0 3px;margin-left:3px}
.cols{display:flex;gap:14px} .cols>div{flex:1} ol{margin:3px 0 0 16px;padding:0} li{margin:3px 0} ul{margin:3px 0 3px 15px;padding:0}
.note{font-size:7.6pt;color:#5b6475;margin-top:3px} .ok{color:#1d6b3a;font-weight:bold}
"""


def f(v, s=""):
    return "–" if v is None else f"{v}{s}"


def _verdict(A: dict, gap: str | None) -> tuple[str, list[str]]:
    """Verdict sentence plus every scorecard number it quotes (for validation check 4)."""
    T, r, nums = A["teams"], A["rank"], []
    if not r:
        return "<b>Verdict.</b> No team met the ranking rule (3+ callers and 25+ real conversations).", nums
    best = T[r[0]]
    nums += [str(best["callers"]), str(best["real"]), str(best["credited"]), str(best["conv_pct"])]
    s = (f"<b>{e(short(r[0]))}</b> converted best: <b>{best['conv_pct']}%</b> of the leads its {best['callers']} callers "
         f"reached ({best['credited']} enrolments from {best['real']} real conversations).")
    if A["runner_up"]:
        ru = T[A["runner_up"]]
        nums += [str(ru["credited"]), str(ru["conv_pct"])]
        s += f" Runner-up: <b>{e(short(A['runner_up']))}</b>, {ru['conv_pct']}% ({ru['credited']} enrolments)."
    warm = [t for t in r[:2] if T[t]["warm"]]
    if warm:
        s += f" {' and '.join(e(short(t)) for t in warm)} mainly call{'s' if len(warm) == 1 else ''} warm leads."
    bf = A["best_front_line"]
    if bf and bf not in r[:2]:
        nums += [str(T[bf]["credited"]), str(T[bf]["conv_pct"])]
        s += f" Best front-line team: <b>{e(short(bf))}</b>, {T[bf]['conv_pct']}% ({T[bf]['credited']} enrolments)."
    busy = sorted(r, key=lambda t: -T[t]["real"])[:2]
    nums += [str(T[t][k]) for t in busy for k in ("real", "conv_pct")]
    s += (" The busiest teams, " + " and ".join(f"{e(short(t))} ({T[t]['real']} conversations)" for t in busy)
          + ", converted only " + " and ".join(f"{T[t]['conv_pct']}%" for t in busy) + ".")
    if gap:
        s += f" Biggest gap: <b>{e(gap.lower())}</b>."
    return "<b>Verdict.</b> " + s, nums


def _gap(tx: dict | None) -> list[tuple[str, int, int]]:
    """Markers sorted by how much more often converting calls had them."""
    if not tx or not tx.get("converted_n"):
        return []
    return sorted(((k, v["converted"], v["not_converted"]) for k, v in tx["markers"].items()
                   if v.get("converted") is not None and v.get("not_converted") is not None), key=lambda x: x[2] - x[1])


def scorecard_cells(A: dict) -> set[str]:
    return {str(s[k]) for t in A["rank"] for s in [A["teams"][t]] for k in
            ("callers", "dials", "answer_pct", "real", "talk_min", "credited", "conv_pct", "same_day_owner", "probe", "pitch",
             "obj", "hi_mod_pct") if s[k] is not None}


def _glance_table(A: dict) -> str:
    T, r = A["teams"], A["rank"]
    top = max((T[t]["conv_pct"] for t in r), default=0) or 1
    rows = "".join(
        f"<tr{' class=\"top\"' if i <= 3 else ''}><td>{i}</td><td>{e(short(t))}{'<span class=\"w\">warm</span>' if T[t]['warm'] else ''}</td>"
        f"<td><span class='bar{' warm' if T[t]['warm'] else ''}' style='width:{max(2, round(110 * T[t]['conv_pct'] / top))}px'></span>"
        f"<b>{T[t]['conv_pct']}%</b></td><td class='n'>{T[t]['credited']}</td><td class='n'>{T[t]['real']}</td></tr>"
        for i, t in enumerate(r, 1))
    return (f"<table><tr><th>#</th><th>Team</th><th>Conversion (enrolled ÷ leads reached)</th><th class='n'>Enrolled</th>"
            f"<th class='n'>Real convs</th></tr>{rows}</table>")


def _people(A: dict) -> str:
    rows = "".join(f"<tr><td><b>{e(p['name'])}</b></td><td>{e(short(p['team']))}</td><td class='n'>{p['credited']}</td>"
                   f"<td class='n'>{p['real']}</td></tr>" for p in A["rec"])
    cc = A["coach_case"]
    if cc:
        rows += (f"<tr><td><b>{e(cc['name'])}</b> <span class='w'>coach</span></td><td>{e(short(cc['team']))}</td>"
                 f"<td class='n'>{cc['credited']}</td><td class='n'>{cc['real']}</td></tr>")
    return f"<table><tr><th>Caller</th><th>Team</th><th class='n'>Enrolled</th><th class='n'>Real convs</th></tr>{rows}</table>"


def _actions(A: dict, gaps: list) -> str:
    T, r, cc = A["teams"], A["rank"], A["coach_case"]
    pay = next(((c, n) for k, c, n in gaps if k.startswith("Payment step")), None)
    first = ("<b>End every real conversation with a payment step</b>: amount, EMI split, payment link sent on the call, a date to pay"
             + (f" (in {pay[0]}% of converting calls vs {pay[1]}% of others)." if pay else "."))
    scored = [t for t in r if T[t]["pitch"] is not None]
    second = "<b>Close the pitch gap</b>: use the coaching-asset calls as model recordings; target 50% full pitch in Zipteams"
    if scored:
        second += f" (today {min(T[t]['pitch'] for t in scored)}–{max(T[t]['pitch'] for t in scored)}%)."
    third = "<b>Coach the high-volume, low-close callers first</b>"
    third += f", starting with {e(cc['name'])} ({cc['real']} conversations, {cc['credited']} enrolled)." if cc else "."
    return f"<ol><li>{first}</li><li>{second}</li><li>{third}</li></ol>"


def _why_won(gaps: list, tx: dict | None) -> str:
    if not gaps:
        return "<p class='note'>No transcript sample this run, so close behaviour was not compared.</p>"
    bullets = "".join(f"<li><b>{e(k)}:</b> {c}% of converting calls vs {n}% of long non-converting calls.</li>"
                      for k, c, n in gaps[:3] if c > n)
    low = [(k, c, n) for k, c, n in gaps if c <= n]
    if low:
        bullets += "<li><b>Talked about but not decisive:</b> " + "; ".join(f"{e(k.lower())} {c}% vs {n}%" for k, c, n in low[:2]) + ".</li>"
    rows = "".join(f"<tr><td>{e(k)}</td><td class='n'>{f(v.get('converted'), '%')}</td><td class='n'>{f(v.get('not_converted'), '%')}</td></tr>"
                   for k, v in tx["markers"].items())
    return (f"<div class='cols'><div><ul>{bullets}</ul></div><div><table><tr><th>Marker (one main call per lead)</th>"
            f"<th class='n'>Converted (n={tx['converted_n']})</th><th class='n'>Not conv. (n={tx['not_converted_n']})</th></tr>{rows}</table></div></div>")


def _lost(A: dict) -> str:
    T, r = A["teams"], A["rank"]
    scored = [t for t in r if T[t]["pitch"] is not None]
    items = []
    weak = sorted((t for t in scored if T[t]["probe"] is not None), key=lambda t: T[t]["probe"])[:2]
    if weak:
        items.append("<b>Weakest probing:</b> " + "; ".join(
            f"{e(short(t))} {T[t]['probe']}% probing, {f(T[t]['hi_mod_pct'], '%')} high/moderate intent" for t in weak) + ".")
    zero = [t for t in r if T[t]["credited"] == 0]
    if zero:
        items.append("<b>No enrolments despite conversations:</b> " + "; ".join(f"{e(short(t))} ({T[t]['real']})" for t in zero) + ".")
    if r:
        la = min(r, key=lambda t: T[t]["answer_pct"])
        items.append(f"<b>Lowest answer rate:</b> {e(short(la))}, {T[la]['answer_pct']}% of {T[la]['dials']:,} dials; check the dialer before judging callers.")
        busy = max(r, key=lambda t: T[t]["talk_min"])
        if busy != r[0]:
            items.append(f"<b>Most talk time:</b> {e(short(busy))}, {T[busy]['talk_min']:,} talk-min for {T[busy]['conv_pct']}% conversion.")
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def report_html(A: dict, tx: dict | None, validation: str = "") -> tuple[str, list[str]]:
    """Returns (html, the verdict's numbers)."""
    T, tot, r = A["teams"], A["totals"], A["rank"]
    d0, cw_end = datetime.fromisoformat(A["window"]["d0"]), datetime.fromisoformat(A["window"]["cw_end"])
    title_day = f"{d0:%A} {d0.day} {d0:%B %Y}"  # P65: weekday computed from the date
    gaps = _gap(tx)
    verdict, nums = _verdict(A, gaps[0][0] if gaps and gaps[0][1] > gaps[0][2] else None)
    full = "".join(
        f"<tr{' class=\"top\"' if i <= 3 else ''}><td>{i}</td><td style='white-space:nowrap'>{e(short(t))}{'<span class=\"w\">warm</span>' if s['warm'] else ''}</td>"
        f"<td class='n'>{s['callers']}</td><td class='n'>{s['dials']:,}</td><td class='n'>{s['answer_pct']}%</td><td class='n'>{s['real']}</td>"
        f"<td class='n'>{s['talk_min']:,}</td><td class='n'><b>{s['credited']}</b></td><td class='n'><b>{s['conv_pct']}%</b></td>"
        f"<td class='n'>{s['same_day_owner']}</td><td class='n'>{f(s['probe'], '%')}</td><td class='n'>{f(s['pitch'], '%')}</td>"
        f"<td class='n'>{f(s['obj'], '%')}</td><td class='n'>{f(s['hi_mod_pct'], '%')}</td></tr>"
        for i, (t, s) in enumerate(((t, T[t]) for t in r), 1))
    small = sorted((t for t in T if t not in r and t not in ("Unassigned", "Not a user")), key=lambda t: -T[t]["credited"])
    small_txt = "; ".join(f"{e(short(t))} {T[t]['callers']} callers/{T[t]['real']} convs/{T[t]['credited']} enrolled" for t in small) or "none"
    ua, nu = T.get("Unassigned", {}), T.get("Not a user", {})
    no_zip = [short(t) for t in r if T[t]["zip_n"] == 0]
    assets = ", ".join(f"{e(p['name'])} ({e(short(p['team']))}, {p['probe']}/{p['pitch']}/{p['obj']})" for p in A["assets"]) or "none with 15+ notes"
    tx_note = ("Transcripts: not sampled this run." if not tx else
               f"Transcripts: {tx['numbers']} leads ({tx['sample_conv']} converted, {tx['sample_non']} non-converted 300 s+ calls, "
               f"10 per team from {', '.join(short(t) for t in tx['top5'])}, seed 5), {tx['requests']} API requests"
               + (f", {tx['failed_chunks']} failed chunk(s)" if tx.get("failed_chunks") else "")
               + ", longest target-day transcript of 120 s or more per lead.")
    cw = f"{d0.day} {d0:%b} 00:00 to {cw_end.day} {cw_end:%b %H:%M} IST"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Team calling report {A['date']}</title><style>{CSS}</style></head><body>
<h1>Who converted best — {title_day}</h1>
<div class="sub">Sales calling performance by team and caller · enrolments counted {cw} · Parameters v{VERSION} · prepared {datetime.now():%d %b %Y}</div>
<div class="kpis"><div class="k"><b>{tot['calls']:,}</b>calls ({tot['outbound']:,} out, {tot['inbound']:,} in)</div>
<div class="k"><b>{round(100 * tot['answered_out'] / tot['outbound'], 1) if tot['outbound'] else 0}%</b>outbound answered ({tot['answered_out']:,})</div>
<div class="k"><b>{tot['zip_attr']:,}</b>calls scored by Zipteams</div>
<div class="k"><b>{tot['enroll_credited']}</b>enrolments credited to a caller (of {tot['enroll_window']})</div></div>
<div class="box">{verdict}</div>
<h2>1. Teams ranked</h2>
{_glance_table(A)}
<div class="note">Ranked by enrolments credited to the team's callers, then conversion, then real conversations. <span class="w">warm</span> = mainly calls bootcamp registrants or post-enrolment leads. Full figures on page 2.</div>
<div class="cols"><div><h2>2. Callers to recognise</h2>{_people(A)}</div>
<div><h2>3. Do next</h2>{_actions(A, gaps)}</div></div>

<div class="page2">
<h2>4. Team scorecard</h2>
<table class="full"><tr><th>#</th><th>Team</th><th class='n'>Callers</th><th class='n'>Dials</th><th class='n'>Answer %</th><th class='n'>Real convs</th><th class='n'>Talk min</th><th class='n'>Enrolled (credited)</th><th class='n'>Conversion %</th><th class='n'>Enrolled same day (owner)</th><th class='n'>Probing</th><th class='n'>Pitch</th><th class='n'>Objection</th><th class='n'>High/mod intent</th></tr>{full}</table>
<div class="note">Not ranked (under 3 callers or 25 real conversations): {small_txt}. Also in totals: Unassigned {ua.get('callers', 0)} callers, {ua.get('real', 0)} convs, {ua.get('credited', 0)} enrolled; Not a user {nu.get('dials', 0) + nu.get('inbound', 0)} calls. Coaching assets (15+ Zipteams notes, probing/pitch/objection %): {assets}.</div>
<h2>5. Why the top teams won</h2>
{_why_won(gaps, tx)}
<h2>6. Where other teams lost revenue</h2>
{_lost(A)}
<h2>Method and limits</h2>
<div class="note"><span class="ok">{e(validation)}</span>. Window {d0.day} {d0:%b} 00:00–23:59 IST. Calls: LeadSquared events 21/22 started in the window; {tot['bots_excluded']} automated calls and {tot['outside_window_excluded']} calls that started on another day (returned because they were edited on the day) excluded. Real conversation = answered and 120 s or longer. Enrolment = a lead's first-ever stage change to "Course Enrolled" from {cw} ({tot['enroll_window']} found), credited to the caller with the most answered talk time on that lead on the target day ({tot['enroll_credited']} credited). Conversion = credited ÷ leads reached. "Payment Successful" activities in the window: {tot['payments']}{', so enrolments are the conversion measure' if not tot['payments'] else ''}. Zipteams: {tot['zip_attr']:,} of {tot['zip_total']:,} notes attributed to the caller of the last answered call before the note ({tot['zip_dropped']} dropped){'; no Zipteams notes for ' + ', '.join(e(t) for t in no_zip) + ' ("–")' if no_zip else ''}. Team = caller's first LeadSquared group; {tot['multi_group_callers']} callers belong to more than one group. {tx_note} Parameters v{VERSION}.</div>
</div>
</body></html>"""
    return page, nums


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
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Plan tracker {P['date']}</title><style>{CSS}</style></head><body>
<h1>Revenue plan tracker — {P['date']}</h1>
<div class="sub">Who is adopting the plan's levers. Separate from the Parameters v{VERSION} report, whose figures it never changes.</div>
<div class="box">Account: payment step in {f(acc['payment_step_zip_pct'], '%')} of Zipteams-scored calls; full pitch {f(acc['full_pitch_pct'], '%')};
{f(acc['called_back_same_day_pct'], '%')} of {acc['missed_inbound_leads']} leads with a missed inbound call were called back the same day (median {f(acc['median_callback_min'], ' min')});
dial failures {f(acc['dial_failure_pct'], '%')}. Transcript sample: payment step in {f(acc['payment_step_transcript_converted_pct'], '%')} of converting vs {f(acc['payment_step_transcript_not_converted_pct'], '%')} of long non-converting calls.</div>
<h2>Teams</h2><table class="full">{head.format('Team', "<th class='n'>Payment step (transcripts)</th>")}{teams}</table>
<h2>Callers with 5+ Zipteams-scored calls, lowest payment-step rate first</h2><table class="full">{head.format('Caller', '')}{callers}</table>
<div class="note">Payment step = the report's payment-step marker (payment link, pay now, make the payment, balance amount) in the Zipteams summary of the call; the transcript column uses the report's transcript sample, so its n is small. Full pitch = Zipteams mx_Custom_4. A missed inbound call counts once per lead and belongs to the caller whose line it rang on; called back = an outbound dial or answered call to that lead later the same IST day. Dial failures = CallFailure ÷ dials; 50%+ on a day is a dialer problem, not the caller.</div>
</body></html>"""
