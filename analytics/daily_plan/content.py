"""Everything the workbook and PDF show, from the rows, the review facts and Claude's narrative (narrative.json).

The narrative carries only judgement: the headline, each caller's 'went well / fix / 3 rules', lessons, talking
points and dialling changes. Numbers, tables, the priority list and roles are computed here. A missing narrative
falls back to neutral defaults so the build never fails."""

from __future__ import annotations

import re
from datetime import datetime

from analytics.daily_plan.common import LEADER, TEAM

SLOTS = {"P": "10:00–11:30, balance first", "M": "10:00–10:30; new missed calls within 15 min",
         "A": "10:30–11:30, redial in 10–15 min, retry 15:00 & 19:00", "B": "11:30–13:00, retry 15:30",
         "F": "14:00–15:00, retry 17:30", "R": "16:00–17:00, WhatsApp first", "C": "17:00–18:30, WhatsApp first"}
GROUPS = ["1 · Payment promised for today — close on the call", "2 · Can close today (Tier A)",
          "3 · Callback booked for today — make sure it happens on time", "4 · Hot follow-ups (10%+) — the ask must be made",
          "5 · Leads who rang us — return first", "6 · Asked for a later day — do not call today, keep the date"]
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
AB_RULES = [
    "Check it is not an existing student before pitching. If they already paid or had a welcome call: Outcome 'Already a student', route to support, leave the stage.",
    "Confirm the course (the sheet shows the one confirmed on the call; LeadSquared is often wrong).",
    "In the first 3 minutes ask who decides and how they will pay. If a parent, spouse or brother decides, offer a 3-way call now or this evening, and send the link to them directly.",
    "Say the fee in the first minute if asked. Send the payment link while they are on the line: UPI, debit card, no-card EMI, NBFC loan, or the Rs 3,000 seat block. Stay on until it goes through, or fix the exact time today.",
    "No income figures, guarantees or 'listed company' claims. Use the fee sheet and batch dates on screen.",
    "Respect 'call on <day>' and 'WhatsApp only'. Send the WhatsApp and keep the date the lead gave.",
    "Paid on the call? Outcome 'Paid – new enrollment' (a note of what they paid is enough) and move the lead to Course Enrolled.",
    "Write a note (who decides, blocker, next step, time) before the next dial.",
]
HOURS = [
    ["09:30", "Log in; test your line with one call.", "Payment links, EMI sheets and seat-block links ready."],
    ["09:45", "Huddle: read out your 3 rules and your P/M/A names.", "Attendance; reassign absentees' P/A leads; check lines."],
    ["10:00–10:30", "Return every missed call (M).", "Line up payment links for the leads who promised to pay today."],
    ["10:30–11:30", "Tier A only. Link sent on the call. No answer → WhatsApp + redial in 10–15 min.", "Sit on the biggest A calls; approve EMI/discount asks live."],
    ["11:30–13:00", "Tier B. Ask for the Rs 3,000 seat block or EMI application on the call.", "11:30: every A dialled twice? 12:00: payments check."],
    ["13:00–14:00", "Lunch in two shifts; missed calls always covered.", "13:00: share of dials on the sheet."],
    ["14:00–15:00", "F: new leads, one program, fixed callback.", "14:00 status report arrives: act on its to-do list."],
    ["15:00–16:00", "Round 2 on every unreached A and B.", "Links sent on every A/B conversation?"],
    ["16:00–18:30", "R and C (WhatsApp first). Old leads only if every A/B row has an outcome.", "17:00 status report; 18:00 stage hygiene."],
    ["18:30–20:30", "Evening round: unreached A/B and booked callbacks.", "19:30 day close; post the count with names."],
]


def _cut(s, n):
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _hhmm(txt):
    m = re.search(r"(\d{1,2})[:.](\d{2})", txt or "")
    if not m:
        return None
    h = int(m.group(1))
    return f"{h + 12 if h < 9 else h:02d}:{m.group(2)}"


def priority(rows: dict, today: str, overrides: dict | None = None) -> list[dict]:
    """Every lead where an enrollment could come today, in six groups, with a computed role and check-by time."""
    overrides = overrides or {}
    d = datetime.strptime(today, "%Y-%m-%d")
    wd = DAYS[d.weekday()]
    today_rx = re.compile(rf"\b{wd}\w*|\btoday\b|\b{d.day} {d.strftime('%b')}", re.I)
    later_rx = re.compile(r"\b(" + "|".join(x for x in DAYS if x != wd) + r")\w*|tomorrow|next week", re.I)
    out = []
    for o, rs in rows["by_owner"].items():
        for r in rs:
            cb = r.get("callback_requested") or ""
            later = bool(later_rx.search(cb)) and not today_rx.search(cb)
            if later:
                g = 5 if r["tier"] in "AB" and r["chance"] >= 10 else None
            elif r["tier"] == "A" and re.search(r"pay|link|emi|block|token|today", (r.get("exact_ask") or "") + " " + cb, re.I) and r["chance"] >= 30:
                g = 0
            elif r["tier"] == "A":
                g = 1
            elif today_rx.search(cb) and r["chance"] >= 5:
                g = 2
            elif r["tier"] == "B" and r["chance"] >= 10:
                g = 3
            elif r["tier"] == "M" and r["chance"] >= 3:
                g = 4
            else:
                g = None
            if g is None:
                continue
            first = o.split()[0]
            role = {0: f"Payment promised today: make sure {first} sends the link while the lead is on the line and stays on until it goes through.",
                    1: f"Make sure {first} calls in the 10:30 A block and sends the link while the lead is on the line; no answer → redial within 15 min, again at 15:00 and 19:00.",
                    2: f"Callback agreed for today ({_cut(cb, 60)}). Make sure it is made on time, with the fee and the Rs 3,000 block link.",
                    3: "Check the ask is made on the call: fee, Rs 3,000 block link sent live, and a dated pay time. 'Thinking' → fix the date and who decides.",
                    4: "Return the call first; find why they rang and answer it on the call; if it's the fee or batch, send the link.",
                    5: f"Asked to be called {_cut(cb, 50)}. Do not call today; put the date in the calendar and make sure it happens."}[g]
            default = {0: "12:00", 1: "11:30", 2: "19:00", 3: "15:30", 4: "10:30", 5: "later"}[g]
            ck = _hhmm(cb) or default if g in (2, 5) else default
            ov = overrides.get(r["lead_id"], {})
            out.append({"group": ov.get("group", g), "lead_id": r["lead_id"], "owner": o, "name": r.get("name") or "Unnamed lead",
                        "phone": r.get("phone"), "course": r.get("course", ""), "chance": r["chance"],
                        "why": (r.get("why") or "")[:420], "ask": r.get("exact_ask", ""), "role": ov.get("role", role),
                        "check_by": ov.get("check_by", ck), "who_decides": r.get("who_decides", ""), "how_pay": r.get("how_pay", "")})
    return sorted(out, key=lambda f: (f["group"], -f["chance"]))


def _numbers_line(n: str, v: dict, q: dict, paid: list[str], label: str) -> str:
    if not v["dials"]:
        return f"{label}: no calls logged."
    qq = q.get(n, {})
    return (f"{label}: {v['dials']} dials · {v['answer_pct']}% answered · {v['talk_min']} min talk · {v['real']} real conversations (2 min+) · "
            f"{v['first']}–{v['last']} · {v['on_sheet_pct']}% of dials on the sheet · Tier A tried {v['a_tried']}/{v['a_total']} · "
            f"{v['missed_unreturned']} missed calls not returned · full ask in {qq.get('full_ask_made', 0)} of {qq.get('conv', 0)} conversations"
            + (f" · paid: {', '.join(paid)}" if paid else ""))


def _yn(ok, part):
    return "Yes" if ok else "Partly" if part else "No"


def build(rows: dict, facts: dict, narrative: dict | None, team: str = TEAM, leader: str = LEADER) -> dict:
    N = narrative or {}
    today = rows["today"]
    d = datetime.strptime(today, "%Y-%m-%d")
    prev = facts["day"]
    pd = datetime.strptime(prev, "%Y-%m-%d")
    pl = pd.strftime("%A")
    st, q, T = facts["stats"], facts["quality"], facts["stats"]["team"]
    callers = sorted(st["callers"], key=lambda n: (-st["callers"][n]["dials"], n))
    paid_by = {}
    for r in rows["paid"]:
        paid_by.setdefault(r["owner"], []).append(r.get("name") or "")
    fb_n = N.get("feedback", {})
    feedback = []
    for n in callers:
        v = st["callers"][n]
        f = fb_n.get(n, {})
        feedback.append({"caller": n, "numbers": _numbers_line(n, v, q, paid_by.get(n, []), pd.strftime("%a")),
                         "good": f.get("good", "—"), "fix": f.get("fix", "—"), "one_line": f.get("one_line", ""),
                         "rules": f.get("rules") or ["Return missed calls within 15 minutes.", "Tier A in the 10:30 block; redial within 15 minutes.",
                                                     "Every A/B call ends with the link sent while the lead is on the line."]})
    pri = priority(rows, today, N.get("priority_overrides"))
    S = rows["by_owner"]
    conv = sum(x.get("conv", 0) for x in q.values())
    ask = sum(x.get("full_ask_made", 0) for x in q.values())
    link = sum(x.get("link_sent_on_call", 0) for x in q.values())
    enr = facts["enrollments"]
    confirmed = enr
    sb = facts.get("stats_before") or {}
    TB = sb.get("team", {}) if sb else {}
    M = facts["missed"]
    lines = M["lines"]
    followed = [
        ["09:45 Attendance", _yn(not T["absent"], len(T["absent"]) <= 1),
         ("Everyone dialled." if not T["absent"] else f"No calls from: {', '.join(T['absent'])}.")],
        ["09:50 Working lines", _yn(not lines, False),
         ("No line problems seen." if not lines else "Most non-answers logged as 0 seconds (line may be failing): "
          + ", ".join(f"{k} {v}%" for k, v in lines.items()) + ".")],
        ["10:00 Return missed calls first", _yn(T["inbound_missed"] <= T["inbound"] * 0.3, T["inbound_missed"] < T["inbound"] * 0.7),
         f"{T['inbound_missed']} of {T['inbound']} calls from leads were missed."],
        ["10:30–11:30 Tier A block, redial in 10–15 min", _yn(T["a_total"] and T["a_tried"] == T["a_total"] and T["a_redial15"] >= T["a_first_unanswered"] * 0.7, T["a_tried"] >= T["a_total"] * 0.6),
         f"{T['a_tried']} of {T['a_total']} A leads dialled. {T['a_first_unanswered']} first dials unanswered; {T['a_redial15']} redialled within 15 minutes."],
        ["11:30 Every Tier A dialled twice", _yn(T["a_total"] and T["a_twice"] >= T["a_total"] * 0.9, T["a_twice"] >= T["a_total"] * 0.5), f"{T['a_twice']} of {T['a_total']}."],
        ["13:00 60%+ of dials on the sheet", _yn(all(st["callers"][n]["on_sheet_pct"] >= 60 for n in callers if st["callers"][n]["dials"]),
                                                  any(st["callers"][n]["on_sheet_pct"] >= 60 for n in callers)),
         "; ".join(f"{n.split()[0]} {st['callers'][n]['on_sheet_pct']}%" for n in callers if st["callers"][n]["dials"])
         + f". Before 16:00, {T['before16'] - T['before16_on_sheet']} of {T['before16']} dials went to leads not on the sheet."],
        ["15:00 Second round on unreached A", _yn(T["a_unreached"] and T["a_retry_15"] >= len(T["a_unreached"]) * 0.8, T["a_retry_15"] > 0),
         f"Of {len(T['a_unreached'])} A leads not reached, {T['a_retry_15']} {'was' if T['a_retry_15'] == 1 else 'were'} retried 15:00–16:00 and {T['a_retry_evening']} after 18:30."],
        ["Every A/B call ends with the link sent on the call", _yn(conv and link >= conv * 0.6, link >= conv * 0.2), f"Link sent on the call {link} times in {conv} real conversations read."],
        ["Tier B coverage", _yn(T["b_total"] and T["b_tried"] >= T["b_total"] * 0.9, T["b_tried"] >= T["b_total"] * 0.5), f"{T['b_tried']} of {T['b_total']} B leads dialled, {T['b_reached']} reached."],
    ] if facts.get("has_plan") else [["Plan adherence", "—", "No saved plan for the previous day, so adherence could not be measured."]]
    scorecard = [
        ["Enrollments (Course Enrolled or paid on the call)", "—", str(len(confirmed)), ", ".join(f"{e['name']} ({e['owner'].split()[0]})" for e in confirmed[:8])],
        ["Callers who dialled", f"{TB.get('callers_dialling', '—')} of {TB.get('callers', '—')}" if TB else "—", f"{T['callers_dialling']} of {T['callers']}",
         ", ".join(T["absent"]) + (" absent" if T["absent"] else "")],
        ["Dials", f"{TB['dials']:,}" if TB else "—", f"{T['dials']:,}", ""],
        ["Answer rate", f"{TB['answer_pct']}%" if TB else "—", f"{T['answer_pct']}%", ""],
        ["Talk time (team)", f"{TB['talk_min']:,} min" if TB else "—", f"{T['talk_min']:,} min", ""],
        ["Real conversations (2 min+)", str(TB["real"]) if TB else "—", str(T["real"]), ""],
        ["Full ask made (price + link + date)", "—", f"{ask} of {conv}", ""],
        ["Inbound calls missed", f"{TB['inbound_missed']} of {TB['inbound']}" if TB else "—", f"{T['inbound_missed']} of {T['inbound']}", ""],
        ["Tier A dialled", "—", f"{T['a_tried']} of {T['a_total']}", f"{T['a_total'] - T['a_tried']} never dialled" if T["a_total"] else ""],
    ]
    caller_rows = [[n, str(st["callers"][n]["dials"]), f"{st['callers'][n]['answer_pct']}%" if st["callers"][n]["dials"] else "—",
                    str(st["callers"][n]["real"]), str(st["callers"][n]["talk_min"]), f"{st['callers'][n]['on_sheet_pct']}%" if st["callers"][n]["dials"] else "—",
                    f"{st['callers'][n]['a_tried']}/{st['callers'][n]['a_total']}", f"{q.get(n, {}).get('full_ask_made', 0)} of {q.get(n, {}).get('conv', 0)}",
                    ", ".join(paid_by.get(n, [])) or "—", fb_n.get(n, {}).get("one_line", "")] for n in callers]
    missed_list = []
    if M["not_dialled"]:
        missed_list.append(("Hot leads never dialled", f"{len(M['not_dialled'])} of {T['a_total']} P/A leads: " + "; ".join(f"{a} ({b})" for a, b in M["not_dialled"][:15]) + "."))
    if M["long_no_ask"]:
        missed_list.append(("Long conversations that ended without an ask", "; ".join(f"{x['name']} {x['min']} min ({x['caller']})" for x in M["long_no_ask"][:12])
                            + f". {len(M['long_no_ask'])} calls of 5+ minutes on A/B leads had no full ask."))
    if M["broken_callbacks"]:
        missed_list.append(("Callbacks promised and not kept", "; ".join(f"{x['name']} ({x['owner']})" for x in M["broken_callbacks"][:10]) + "."))
    if M["unreturned"]:
        missed_list.append(("Leads who rang us and were not called back", f"{T['inbound_missed']} of {T['inbound']} inbound calls missed. Unreturned by owner: "
                            + ", ".join(f"{k} {v}" for k, v in sorted(M["unreturned"].items(), key=lambda kv: -kv[1])) + "."))
    if lines:
        missed_list.append(("Lines", "Callers whose non-answers mostly log as 0 seconds: " + ", ".join(lines) + ". Move them to a line that shows failures."))
    missed_list += [tuple(x) for x in N.get("missed_extra", [])]
    promised = [p for p in pri if p["group"] == 0]
    cbs = [p for p in pri if p["group"] == 2]
    checks = [
        ["09:30", "Payment links, EMI sheets and the Rs 3,000 seat-block link ready for every program on the P/A lists.", "Get missing links from ops before 10:00."],
        ["09:45", "Attendance and lines." + (f" Yesterday no calls from {', '.join(T['absent'])}." if T["absent"] else "")
         + (f" Lines hiding failures: {', '.join(lines)}." if lines else ""), "Absent → their P/A leads go to the callers present, with a note. Bad line → telephony now."],
        ["10:00", f"Missed calls returned first ({sum(1 for rs in S.values() for r in rs if r.get('missed'))} leads in the M rows).", "Unreturned at 10:30 → reassign."],
        ["11:30", "Every Tier A lead dialled at least twice (2nd dial within 15 min of the first).", "Filter each sheet: Tier = A, attempts blank → ask why, now."],
        ["12:00", "Payments promised for today done? " + (", ".join(p["name"] for p in promised[:10]) or "Group 1 leads") + ".",
         "Not paid → you join the next call; fix the exact time today."],
        ["13:00", "Share of dials on the sheet (yesterday: " + ", ".join(f"{n.split()[0]} {st['callers'][n]['on_sheet_pct']}%" for n in callers if st["callers"][n]["dials"]) + ").",
         "Below 60% → stop old-lead dialling until 16:00."],
        ["14:00", "Status report arrives by email: first-half numbers and the second-half to-do list.", "Act on its to-do list at once."],
        ["15:00", "Second A/B round started; payment link sent on every A/B conversation.", "Check Notes: 'link sent' on every A/B answered call."],
        ["17:00", "Status report 2: who is behind, what is left for the evening.", "Reassign untouched P/A leads."],
        ["18:00", "Stage hygiene: no Not Interested under 2 min of talk; every lead who paid moved to Course Enrolled.", "Reopen wrong ones and note why."],
        ["19:00", "Evening callbacks: " + (", ".join(f"{p['name']} {p['check_by']}" for p in cbs[:8]) or "booked callbacks") + ".", "Unreached → WhatsApp + one more dial by 20:00."],
        ["19:30", "Day close: count rows with Status 'Enrolled' and leads moved to Course Enrolled today.", "Post the count with names."],
    ]
    dropped = rows["dropped"]
    cnt = lambda *s: sum(1 for x in dropped if x.get("status") in s)  # noqa: E731
    C = {
        "day_label": d.strftime("%a %-d %b %Y"), "team": team, "leader": leader,
        "shivangi_title": f"{leader} (TL) — {d.strftime('%A %-d %b %Y')}: your leads, your checks, your feedback",
        "shivangi_intro": N.get("intro") or (f"{pl}: {len(confirmed)} enrollment(s)"
                                             + f". {len(pri)} leads below are where today's enrollments can come from. Chances are estimates."),
        "priority": pri, "priority_groups": GROUPS, "checks": checks, "feedback": feedback,
        "changes": N.get("changes") or ["Return every missed call within 15 minutes.", "Tier A only from 10:30 to 11:30; redial within 10–15 minutes; retry at 15:00 and 19:00.",
                                        "Every A/B conversation ends with the payment link sent while the lead is on the line.",
                                        "Move every lead who paid to Course Enrolled the same day.",
                                        "Old/recycled leads only after 16:00 and only when every A/B row has an outcome."],
        "slots": SLOTS, "owner_order": sorted(S, key=lambda o: (-sum(1 for r in S[o] if r["tier"] in "PMAB"), o)),
        "caller_note": "Work top to bottom: P (collect balance) and M (missed calls) first, then A, then B. Yellow cells are yours to fill after every attempt. "
                       "Status shows 'Enrolled' when Outcome is 'Paid – new enrollment' or Payment status is 'Paid'. Leads already enrolled are never on this sheet.",
        "targets": {},
        "how_to": ["Who this is for:", f"{leader} uses the first sheet all day. Each caller uses their own sheet. Team summary updates by itself.",
                   "Order of work:", "P = paid booking, collect the balance. M = the lead called us and nobody called back. A = can close today. B = hot follow-up. "
                   "F = new lead, first real conversation. R = closure-stage lead gone quiet (WhatsApp first). C = nurture (WhatsApp first).",
                   "After every attempt:", "Fill the attempt time and outcome. After the call: Next step date/time, Payment status, Notes (who decides, blocker, next step, time).",
                   "Counting enrollments:", "Choose Outcome 'Paid – new enrollment' when the lead says they have paid (a note of what they paid is enough), then move the lead to Course Enrolled. Leads already enrolled never appear on the sheets. Existing students: 'Already a student', send to support, don't change the stage.",
                   "Chances:", "'Est. chance (3 days)' is a judgement-based estimate from the lead's calls and transcripts, not a target.",
                   "Data:", f"LeadSquared pulled {rows['built_at']} IST; {rows['reads']} leads re-read from {pl}'s calls, Zipteams notes and Salesa transcripts."],
        "pdf_title": f"Elite call plan {d.strftime('%a %-d %b')}", "cover_kicker": f"{team} · for {leader}, team leader",
        "cover_h1": f"Call plan and workflow {d.strftime('%A %-d %B %Y')}",
        "cover_sub": f"A review of {pl} first (what worked, what did not, missed opportunities), then your priority leads and checks, the feedback to give each caller, the dialling changes, then each caller's list.",
        "contents": [f"{pl} review: what worked, what didn't, missed opportunities", "Your priority leads", "What you check, and when", "Feedback to give each caller",
                     f"What {pl} taught us", "Dialling pattern: what changes today", "The day, hour by hour", "On every A/B call", "Team overview", "Caller-wise lists"],
        "priority_intro": ("This is every lead where an enrollment could come today, in groups: money already moving, Tier A, callbacks booked for today, hot follow-ups, "
                           "leads who rang us, and leads who asked for a later day. The caller makes the call; <b>you own the outcome</b>."),
        "huddle": "09:45 huddle (5 minutes): each caller reads out their 3 rules and their P/M/A names with the time they'll call. Then: 'Every A/B call ends with the link sent while the lead is on the line.'",
        "tiles": [[str(len(confirmed)), f"enrollments on {pl} (Course Enrolled or paid on the call)"],
                  [f"{T['callers_dialling']} of {T['callers']}", f"callers who dialled on {pl}"],
                  [str(len(pri)), "leads on your priority list today"],
                  [str(sum(1 for rs in S.values() for r in rs if r["tier"] in "PA")), "P + A leads across the team"],
                  [f"{ask} of {conv}", f"real conversations on {pl} with the full ask"],
                  [f"{T['inbound_missed']} of {T['inbound']}", f"calls from leads missed on {pl}"]],
        "data_note": N.get("data_note") or f"Data: LeadSquared pulled {rows['built_at']} IST. Chances are estimates. An enrollment is a lead in Course Enrolled or one who said on a call that they paid.",
        "lessons": {"worked": N.get("lessons", {}).get("worked", []), "count": N.get("enrollment_summary", ""),
                    "didnt": N.get("lessons", {}).get("didnt", []), "plan_wrong": N.get("lessons", {}).get("plan_wrong", [])},
        "numbers_table": [
            ["Full ask made in real conversations", f"{ask} of {conv}", "Fee + Rs 3,000 block link + pay time on every real conversation"],
            ["Inbound calls missed", f"{T['inbound_missed']} of {T['inbound']}", "Missed column first; return within 15 min"],
            ["Leads with one unanswered dial only", f"{T['single_unanswered']} of {T['leads_dialled']}", "A = 4 attempts, B = 3, first redial within 15 min"],
            ["Answered when redialled within 30 min / after 2 h+",
             f"{round(100 * T['redial']['<=30m'][1] / max(T['redial']['<=30m'][0], 1))}% / {round(100 * T['redial']['>2h'][1] / max(T['redial']['>2h'][0], 1))}%", "Redial unanswered A/B within 10–15 min"],
            ["Best pickup hours", ", ".join(f"{h}:00 ({round(100 * v[1] / v[0])}%)" for h, v in sorted(T["hours"].items(), key=lambda kv: -kv[1][1] / max(kv[1][0], 1))[:2] if v[0] >= 20),
             "A/B rounds in those hours"],
        ],
        "hours_note": "Standard day. The 14:00 and 17:00 status reports arrive by email with the to-do list for the next block.",
        "hours": HOURS, "ab_rules": AB_RULES,
        "excluded_note": (f"Removed from the call order after re-reading {pl}'s calls: {cnt('not_interested')} clear not-interested, {cnt('irrelevant')} irrelevant/invalid, "
                          f"{cnt('21day')} 21-day course (Rs 100 community — pass to support), {cnt('already_student')} existing students (support), {cnt('dnc', 'support')} do-not-call/support."),
        "overview_note": "'Likely today' takes about 60% of each A lead's 3-day chance, 35% of B and 30% of the rest; P leads have already paid a booking and count 0. Judgement-based estimates.",
        "review": {"title": f"{pl} review: what worked, what didn't, missed opportunities",
                   "headline": N.get("headline") or f"<b>{pl}: {len(confirmed)} confirmed enrollment(s).</b> {T['dials']:,} dials, {T['answer_pct']}% answered, {T['real']} real conversations; full ask in {ask} of {conv}.",
                   "before_label": datetime.strptime(facts["before"], "%Y-%m-%d").strftime("%a %-d %b") if facts.get("before") else "Before",
                   "day_label": pd.strftime("%a %-d %b"), "scorecard": scorecard, "followed": followed, "callers": caller_rows,
                   "prev_priority": [[f"{x['name']} ({x['owner'].split()[0] if x['owner'] else '?'})", _cut(x["what"], 200), x["result"]] for x in facts["priority_outcomes"]],
                   "missed": missed_list, "talking": N.get("talking") or [f"Attendance: {', '.join(T['absent']) or 'everyone'} — who covers their A leads?",
                                                                           f"The ask: {ask} of {conv} conversations had price + link + date. Make 'link sent on the call' a checked rule.",
                                                                           "Stage discipline: every lead who paid moved to Course Enrolled the same day.",
                                                                           "Callbacks: every promised time on a phone alarm; checked at 19:00."]},
    }
    return C
