"""HTML for the daily team performance report (P62 section order) and the plan tracker page.

Every sentence is generated from the analysed figures, so a verdict number always appears in the
scorecard (validation check 4). Used by analytics.team_performance.
"""

from __future__ import annotations

import html
from datetime import datetime

from analytics.call_markers import PAYMENT_STEP
from analytics.team_performance import VERSION, short

e = html.escape
CSS = """
@page{size:A4;margin:11mm 11mm} body{font-family:Arial,Helvetica,sans-serif;font-size:9.2pt;color:#1d2433;line-height:1.33}
h1{font-size:15.5pt;margin:0 0 2px;color:#0f2b5b} h2{font-size:11pt;color:#0f2b5b;margin:9px 0 3px;border-bottom:1.5px solid #c9d3e6;padding-bottom:2px}
.sub{color:#5b6475;font-size:8.4pt;margin-bottom:6px} .box{background:#eef3fb;border-left:4px solid #0f5bd8;padding:6px 9px;margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:7.8pt;margin-top:3px} th{background:#0f2b5b;color:#fff;padding:3px 4px;text-align:left;font-weight:600}
td{padding:2px 4px;border-bottom:1px solid #e1e6ef} tr.top td{background:#e9f7ee} ul{margin:3px 0 3px 15px;padding:0} li{margin:2px 0}
.k{display:inline-block;width:23.5%;background:#f5f7fb;border:1px solid #dfe5f0;border-radius:4px;padding:4px 6px;margin-right:1%;box-sizing:border-box;vertical-align:top;font-size:8.4pt}
.k b{font-size:12.5pt;color:#0f2b5b;display:block} .note{font-size:7.5pt;color:#5b6475} .w{font-size:6.8pt;background:#fff1c9;color:#7a5a00;border-radius:3px;padding:0 3px}
.two{display:flex;gap:12px} .two>div{flex:1}
"""


def f(v, s=""):
    return "–" if v is None else f"{v}{s}"


def _day(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _verdict(A: dict, gap: str | None) -> str:
    T, r = A["teams"], A["rank"]
    if not r:
        return "<b>Verdict.</b> No team met the ranking rule (3+ callers and 25+ real conversations)."
    best = T[r[0]]
    s = (f"<b>Verdict.</b> <b>{e(short(r[0]))}</b> is the best team: its {best['callers']} callers held {best['real']} real "
         f"conversations and {best['credited']} of those leads enrolled, a <b>{best['conv_pct']}% conversion rate</b>.")
    if A["runner_up"]:
        ru = T[A["runner_up"]]
        s += f" <b>{e(short(A['runner_up']))}</b> is runner-up ({ru['credited']} enrollments, {ru['conv_pct']}%)."
    warm = [t for t in r[:2] if T[t]["warm"]]
    if warm:
        s += f" {' and '.join(e(short(t)) for t in warm)} mainly call{'s' if len(warm) == 1 else ''} warm leads."
    bf = A["best_front_line"]
    if bf and bf not in r[:2]:
        s += (f" The best front-line team is <b>{e(short(bf))}</b> ({T[bf]['credited']} enrollments, {T[bf]['conv_pct']}%"
              + (f", {T[bf]['hi_mod_pct']}% high/moderate intent" if T[bf]["hi_mod_pct"] is not None else "") + ").")
    busy = sorted(r, key=lambda t: -T[t]["real"])[:2]
    s += " The busiest teams, " + " and ".join(f"{e(short(t))} ({T[t]['real']} conversations)" for t in busy)
    s += ", converted " + " and ".join(f"{T[t]['conv_pct']}%" for t in busy) + "."
    if gap:
        s += f" The biggest gap in the transcripts is <b>{e(gap.lower())}</b> (section 2)."
    return s


def _why_won(tx: dict | None) -> tuple[str, str | None]:
    if not tx or not tx.get("converted_n"):
        return "<p class='note'>No transcript sample this run, so close behaviour was not compared.</p>", None
    M = tx["markers"]
    diffs = sorted(((k, v["converted"], v["not_converted"]) for k, v in M.items()
                    if v.get("converted") is not None and v.get("not_converted") is not None), key=lambda x: x[2] - x[1])
    bullets = "".join(f"<li><b>{e(k)}:</b> in {c}% of converting calls vs {n}% of long non-converting calls.</li>"
                      for k, c, n in diffs[:3] if c > n)
    low = [(k, c, n) for k, c, n in diffs if c <= n]
    if low:
        bullets += "<li><b>Talked about but not decisive:</b> " + "; ".join(f"{e(k.lower())} {c}% vs {n}%" for k, c, n in low[:2]) + ".</li>"
    rows = "".join(f"<tr><td>{e(k)}</td><td>{f(v.get('converted'), '%')}</td><td>{f(v.get('not_converted'), '%')}</td></tr>" for k, v in M.items())
    table = (f"<table><tr><th>Marker (one main call per lead)</th><th>Converted (n={tx['converted_n']})</th>"
             f"<th>Not conv. (n={tx['not_converted_n']})</th></tr>{rows}</table>")
    gap = diffs[0][0] if diffs and diffs[0][1] > diffs[0][2] else None
    return f"<div class='two'><div><ul>{bullets}</ul></div><div>{table}</div></div>", gap


def _lost(A: dict) -> str:
    T, r = A["teams"], A["rank"]
    scored = [t for t in r if T[t]["pitch"] is not None]
    items = []
    if scored:
        lo, hi = min(T[t]["pitch"] for t in scored), max(T[t]["pitch"] for t in scored)
        items.append(f"<b>Pitch gap:</b> a full product pitch in {lo}–{hi}% of Zipteams-scored calls across the ranked teams.")
        weak = sorted((t for t in scored if T[t]["probe"] is not None), key=lambda t: T[t]["probe"])[:2]
        if weak:
            items.append("<b>Weakest probing:</b> " + "; ".join(
                f"{e(short(t))} {T[t]['probe']}% probing, {f(T[t]['hi_mod_pct'], '%')} high/moderate intent" for t in weak) + ".")
    zero = [t for t in r if T[t]["credited"] == 0]
    if zero:
        items.append("<b>No enrollments despite conversations:</b> " + "; ".join(
            f"{e(short(t))} ({T[t]['real']} conversations)" for t in zero) + ".")
    if r:
        la = min(r, key=lambda t: T[t]["answer_pct"])
        items.append(f"<b>Lowest answer rate:</b> {e(short(la))} answered {T[la]['answer_pct']}% of {T[la]['dials']:,} dials; "
                     "check the dialer before judging the callers.")
        busy = max(r, key=lambda t: T[t]["talk_min"])
        if busy != r[0]:
            items.append(f"<b>Most talk time:</b> {e(short(busy))} ({T[busy]['talk_min']:,} talk-min) converted {T[busy]['conv_pct']}%.")
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _people(A: dict) -> str:
    prow = lambda p, note: (f"<tr><td><b>{e(p['name'])}</b></td><td>{e(short(p['team']))}</td><td>{p['credited']}</td><td>{p['real']}</td>"  # noqa: E731
                            f"<td>{p['talk_min']}</td><td>{p['answer_pct']}%</td><td>{e(note)}</td></tr>")
    rows = "".join(prow(p, f"{p['credited']} enrollment{'s' * (p['credited'] != 1)} from {p['real']} conversations"
                       + (f"; {p['obj']}% objection handling" if p["obj"] is not None and p["obj"] >= 70 else "")) for p in A["rec"])
    cc = A["coach_case"]
    if cc:
        rows += prow(cc, f"Coaching case: highest volume with ≤ 1 enrollment ({cc['real']} conversations, {cc['talk_min']} talk-min, "
                         f"{cc['credited']} enrollment{'s' * (cc['credited'] != 1)})")
    assets = ", ".join(f"{e(p['name'])} ({e(short(p['team']))}, {p['probe']}/{p['pitch']}/{p['obj']})" for p in A["assets"]) or "none with 15+ notes"
    return (f"<table><tr><th>Caller</th><th>Team</th><th>Enrolled</th><th>Real convs</th><th>Talk min</th><th>Answer %</th><th>Why</th></tr>{rows}</table>"
            f"<div class='note'>Coaching assets (best Zipteams scores, 15+ notes, probing/pitch/objection %): {assets}.</div>")


def report_html(A: dict, tx: dict | None) -> str:
    T, tot, r = A["teams"], A["totals"], A["rank"]
    d0, cw_end = _day(A["window"]["d0"]), _day(A["window"]["cw_end"])
    title_day = f"{d0:%A} {d0.day} {d0:%B %Y}"  # P65: weekday computed from the date
    cw = f"{d0.day} {d0:%b} 00:00 IST to {cw_end:%d %b %H:%M} IST"
    rows = ""
    for i, t in enumerate(r, 1):
        s = T[t]
        rows += (f"<tr{' class=\"top\"' if i <= 3 else ''}><td>{i}</td><td style='white-space:nowrap'>{e(short(t))}"
                 f"{' <span class=\"w\">warm</span>' if s['warm'] else ''}</td><td>{s['callers']}</td><td>{s['dials']:,}</td>"
                 f"<td>{s['answer_pct']}%</td><td>{s['real']}</td><td>{s['talk_min']:,}</td><td><b>{s['credited']}</b></td>"
                 f"<td><b>{s['conv_pct']}%</b></td><td>{s['same_day_owner']}</td><td>{f(s['probe'], '%')}</td><td>{f(s['pitch'], '%')}</td>"
                 f"<td>{f(s['obj'], '%')}</td><td>{f(s['hi_mod_pct'], '%')}</td></tr>")
    small = sorted((t for t in T if t not in r and t not in ("Unassigned", "Not a user")), key=lambda t: -T[t]["credited"])
    small_txt = "; ".join(f"{e(short(t))} {T[t]['callers']} callers/{T[t]['real']} convs/{T[t]['credited']} enrolled" for t in small) or "none"
    ua, nu = T.get("Unassigned", {}), T.get("Not a user", {})
    why, gap = _why_won(tx)
    no_zip = [short(t) for t in r if T[t]["zip_n"] == 0]
    cc = A["coach_case"]
    tx_note = ("Transcripts: not sampled this run." if not tx else
               f"Transcripts: {tx['numbers']} leads ({tx['sample_conv']} converted, {tx['sample_non']} non-converted 300 s+ calls, "
               f"10 per team from {', '.join(short(t) for t in tx['top5'])}, seed 5), {tx['requests']} API requests"
               + (f", {tx['failed_chunks']} failed chunk(s)" if tx.get("failed_chunks") else "")
               + ", longest target-day transcript of 120 s or more per lead.")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Team calling report {A['date']}</title><style>{CSS}</style></head><body>
<h1>Sales Calling Performance — {title_day}</h1>
<div class="sub">LeadSquared call log, Zipteams Notes, call transcripts and first-time enrollments · Parameters v{VERSION} · Prepared {datetime.now():%d %b %Y}</div>
<div><span class="k"><b>{tot['calls']:,}</b>calls ({tot['outbound']:,} out, {tot['inbound']:,} in; {tot['bots_excluded']} bot calls excluded)</span><span class="k"><b>{tot['answered_out']:,}</b>outbound answered ({round(100 * tot['answered_out'] / tot['outbound'], 1) if tot['outbound'] else 0}%)</span><span class="k"><b>{tot['zip_attr']:,}</b>calls scored by Zipteams</span><span class="k"><b>{tot['enroll_credited']}</b>credited enrollments (of {tot['enroll_window']} first-time enrollments in the window)</span></div>
<div class="box">{_verdict(A, gap)}</div>
<h2>1. Team scorecard</h2>
<table><tr><th>#</th><th>Team</th><th>Callers</th><th>Dials</th><th>Answer %</th><th>Real convs</th><th>Talk min</th><th>Enrolled (credited)</th><th>Conversion %</th><th>Enrolled same day (owner)</th><th>Probing</th><th>Pitch</th><th>Objection</th><th>High/mod intent</th></tr>{rows}</table>
<div class="note">Ranked by credited enrollments, then conversion %, then real conversations. Green = top 3. <span class="w">warm</span> = mainly calls bootcamp registrants or post-enrolment leads. Not ranked (under 3 callers or 25 real conversations): {small_txt}. Also in totals: Unassigned {ua.get('callers', 0)} callers, {ua.get('real', 0)} convs, {ua.get('credited', 0)} enrolled; Not a user {nu.get('dials', 0) + nu.get('inbound', 0)} calls.</div>
<h2>2. Why the top teams won</h2>
{why}
<h2>3. Where other teams lost revenue</h2>
{_lost(A)}
<h2>4. Team members to recognise</h2>
{_people(A)}
<h2>5. Actions to lift conversion</h2>
<ul>
<li><b>End every real conversation with a payment step:</b> the exact amount, the EMI split, the payment link sent during the call and a date to pay (docs/call_playbook.md).</li>
<li><b>Close the pitch gap:</b> use the coaching-asset calls as model recordings; target 50% pitch coverage in Zipteams.</li>
<li><b>Coach the high-volume, low-close callers first</b>{f' (e.g. {e(cc["name"])})' if cc else ''}, and check the dialer and lead quality of the lowest-answer teams before adding dials.</li>
</ul>
<h2>Method and limits</h2>
<div class="note">Window {d0.day} {d0:%b} 00:00–23:59 IST. Calls: LeadSquared events 21/22; {tot['bots_excluded']} automated calls excluded. Real conversation = answered and 120 s or longer. Enrollment = a lead's first-ever stage change to "Course Enrolled" in the conversion window ({cw}; {tot['enroll_window']} found), credited to the caller with the most answered talk time on that lead on the target day ({tot['enroll_credited']} credited). Conversion % = credited ÷ leads reached. "Payment Successful" activities in the window: {tot['payments']}{', so enrollments are the conversion measure' if not tot['payments'] else ''}. Zipteams: {tot['zip_attr']:,} of {tot['zip_total']:,} notes attributed to the caller of the last answered call before the note ({tot['zip_dropped']} dropped){'; no Zipteams notes for ' + ', '.join(e(t) for t in no_zip) + ' ("–")' if no_zip else ''}. Team = caller's first LeadSquared group; {tot['multi_group_callers']} callers belong to more than one group. {tx_note} Parameters v{VERSION}.</div>
</body></html>"""


def tracker_html(P: dict) -> str:
    acc = P["account"]
    head = ("<tr><th>{}</th><th>Zip-scored calls</th><th>Payment step (Zip summary)</th><th>Full pitch</th>"
            "<th>Missed inbound leads</th><th>Called back same day</th><th>Median callback</th><th>Dial failures</th>{}</tr>")
    row = lambda name, x, extra="": (f"<tr><td>{e(name)}</td><td>{x['zip_n']}</td><td>{f(x['payment_step_zip_pct'], '%')}</td>"  # noqa: E731
                                     f"<td>{f(x['full_pitch_pct'], '%')}</td><td>{x['missed_inbound_leads']}</td>"
                                     f"<td>{f(x['called_back_same_day_pct'], '%')}</td><td>{f(x['median_callback_min'], ' min')}</td>"
                                     f"<td>{f(x['dial_failure_pct'], '%')}</td>{extra}</tr>")
    teams = "".join(row(short(t["team"]), t, f"<td>{f(t['payment_step_transcript_pct'], '%')} (n={t['transcripts_n']})</td>")
                    for t in sorted(P["teams"], key=lambda t: -t["zip_n"]))
    callers = "".join(row(f"{c['caller']} · {short(c['team'])}", c) for c in
                      sorted((c for c in P["callers"] if c["zip_n"] >= 5), key=lambda c: (c["payment_step_zip_pct"] or 0, -c["zip_n"]))[:40])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Plan tracker {P['date']}</title><style>{CSS}</style></head><body>
<h1>Revenue plan tracker — {P['date']}</h1>
<div class="sub">Who is adopting the plan's levers. Separate from the Parameters v{VERSION} report, whose figures it never changes.</div>
<div class="box">Account: payment step in {f(acc['payment_step_zip_pct'], '%')} of Zipteams-scored calls; full pitch {f(acc['full_pitch_pct'], '%')};
{f(acc['called_back_same_day_pct'], '%')} of {acc['missed_inbound_leads']} leads with a missed inbound call were called back the same day (median {f(acc['median_callback_min'], ' min')});
dial failures {f(acc['dial_failure_pct'], '%')}. Transcript sample: payment step in {f(acc['payment_step_transcript_converted_pct'], '%')} of converting vs {f(acc['payment_step_transcript_not_converted_pct'], '%')} of long non-converting calls.</div>
<h2>Teams</h2><table>{head.format('Team', '<th>Payment step (transcripts)</th>')}{teams}</table>
<h2>Callers with 5+ Zipteams-scored calls, lowest payment-step rate first</h2><table>{head.format('Caller', '')}{callers}</table>
<div class="note">Payment step = the P33 marker (payment link, pay now, make the payment, balance amount) found in the Zipteams summary of the call; the transcript column uses the report's transcript sample, so its n is small. Full pitch = Zipteams mx_Custom_4. A missed inbound call counts once per lead and belongs to the caller whose line it rang on; called back = an outbound dial or answered call to that lead later the same IST day. Dial failures = CallFailure ÷ dials; 50%+ on a day is a dialer problem, not the caller.</div>
</body></html>"""
