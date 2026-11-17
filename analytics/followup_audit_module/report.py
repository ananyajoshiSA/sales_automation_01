"""The day's files: the audit workbook, one feedback PDF per lead (plus a summary PDF), a ZIP and a CSV.

Built like the discovery audit's (``analytics.discovery_audit_module.report``, whose helpers it reuses), with the
follow-up window, the background discovery call, the discovery audit's earlier H/M/L, a per-caller table and the
7-day enrolment check of the leads audited a week earlier.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import zipfile
from collections import Counter, defaultdict
from datetime import timedelta

from openpyxl import Workbook

from analytics.discovery_audit_module import reading as dr
from analytics.discovery_audit_module.report import CSS, _style, ddmmyyyy, e, mmss, mobile, print_pdf, safe, when
from analytics.followup_audit_module import reading
from analytics.followup_audit_module.find import REAL_SECS, STAGE, real
from integrations.timeutil import IST, now_ist, utc

MAX_TRIES = 3  # the run for the day itself plus the next two mornings
POT = {"H": "High", "M": "Medium", "L": "Low"}


def lead_name(ent: dict) -> str:
    return " ".join(x for x in (ent["lead"].get("FirstName"), ent["lead"].get("LastName")) if x)


def callers(entry: dict) -> str:
    """Who made the scored calls, from the call records (never the stage-change name, often "System")."""
    if entry.get("moved_without_call") and entry.get("caller_hint"):
        return entry["caller_hint"]
    named = [n for n in dict.fromkeys((c.get("caller") or "").strip() for c in entry["calls"]) if n]
    if named:
        return ", ".join(named)
    if entry.get("caller_hint"):
        return entry["caller_hint"]
    ids = {c.get("user_id") for c in entry["calls"] if c.get("user_id")}
    return f"Unverified (user id {', '.join(sorted(ids))})" if ids else "Unverified"


def result_of(entry: dict, folder: str) -> dict | None:
    p = f"{folder}/results/{reading.lead_key(entry)}.json"
    return json.load(open(p)) if reading.finished(p, reading.lead_text(entry)) else None


RECENT_DAYS = 2  # a recording still missing for an older call is not coming: score without it, and say so


def waiting(entry: dict) -> list[dict]:
    """Real calls whose transcript the lead waits for: any from the last two days before the move, or every real call
    when none has one. An older call still without a recording is scored around and listed as missing."""
    reals = [c for c in entry["calls"] if real(c)]
    gone = [c for c in reals if not c.get("transcript")]
    if gone and len(gone) == len(reals):
        return gone
    since = utc(entry["moved_utc"]) - timedelta(days=RECENT_DAYS)
    return [c for c in gone if utc(c["start_utc"]) >= since]


def status(entry: dict, folder: str) -> tuple[str, str]:
    """(status, reason) for one lead."""
    if entry.get("moved_without_call"):
        return "MOVED_WITHOUT_CALL", moved_text(entry)[0]
    missing = waiting(entry)
    if missing:
        if entry.get("tries", 1) >= MAX_TRIES:
            return "TRANSCRIPT_NOT_FOUND", f"No transcript for {len(missing)} call(s) of 2+ min after {MAX_TRIES} daily tries; not retried again"
        return "TRANSCRIPT_NOT_FOUND", f"{len(missing)} call(s) of 2+ min have no transcript yet (try {entry.get('tries', 1)} of {MAX_TRIES})"
    r = result_of(entry, folder)
    if r is not None:
        return ("ANALYZED", "") if reading.real_left(entry, r) else ("MOVED_WITHOUT_CALL", moved_text(entry, r)[0])
    if os.path.exists(f"{folder}/results/{reading.lead_key(entry)}.json"):
        return "ANALYSIS_FAILED", "Result did not pass the checks"
    return "PENDING_ANALYSIS", "Not read yet"


def moved_text(entry: dict, result: dict | None = None) -> tuple[str, str]:
    """(what happened, what to do) for a lead moved to the stage with no real conversation in its window."""
    if result is not None:
        return ("The 2+ minute calls before the move were voicemail, IVR or one-sided (Claude's reading of the transcripts)",
                "Check with the caller whether a real follow-up conversation happened before the move")
    before, after = entry.get("last_call_before"), entry.get("first_call_after")
    if before:
        return (f"No conversation since {reading.start_text(entry)}; the last real call was {when(before)}",
                "Ask the caller why the lead was moved to closure without a follow-up conversation")
    if after:
        return (f"Moved before the call: the first real call came after the move ({when(after)})",
                "Ask the caller why the stage was moved before the conversation")
    if entry["calls"]:
        return (f"Only short calls (under {REAL_SECS // 60} min) before the move",
                "Check whether the closing conversation really happened")
    return ("No real call with this lead found at all", "Check whether anyone ever spoke to this lead")


def needs_check(entry: dict, st: str) -> tuple[str, str]:
    if st == "TRANSCRIPT_NOT_FOUND":
        if entry.get("tries", 1) >= MAX_TRIES:
            return (f"Call recording still missing after {MAX_TRIES} tries", "Check the call in the dialer and Salesa, or listen to it by hand")
        return (f"Call recording not ready yet (try {entry.get('tries', 1)} of {MAX_TRIES})", "Nothing; it is checked again tomorrow")
    return ("Not read yet" if st == "PENDING_ANALYSIS" else "Reading did not pass the checks", "Read again in the next run")


def discovery_label(entry: dict, ddir: str) -> str:
    """The discovery audit's H/M/L for this lead (latest on or before the move day), if its files are on this machine."""
    pid = entry["lead"]["ProspectID"][:8]
    best = None
    for p in glob.glob(f"{ddir}/*/reading/results/*_{pid}.json"):
        day = os.path.basename(p)[:10]
        if day <= entry["calls_day"] and (best is None or day > best[0]):
            best = (day, p)
    if not best:
        return "Not in the discovery audit"
    try:
        s = json.load(open(best[1]))["subscores"]
    except (OSError, ValueError, KeyError):
        return "Not in the discovery audit"
    return f"{dr.relaxed_label(dr.totals(s)[0])} ({ddmmyyyy(best[0])})"


def row(entry: dict, result: dict, ddir: str) -> dict:
    s = result["subscores"]
    relaxed, strict = reading.totals(s)
    nac = set(result.get("not_a_conversation") or [])
    talk = sum(c.get("duration") or 0 for c in entry["calls"] if reading.label(c) not in nac)
    sheet = {**result["sheet"], "Call Date": ddmmyyyy(entry["calls_day"]), "Duration": mmss(talk),
             "Mobile Number": mobile(entry), "Conversion Potential (H,M,L)": reading.relaxed_label(relaxed)}
    return {"sheet": {c: sheet[c] for c in reading.SHEET_COLUMNS}, "relaxed": relaxed, "strict": strict,
            "strict_label": reading.strict_label(strict), "band": reading.pdf_band(strict), "result": result, "entry": entry,
            "discovery": discovery_label(entry, ddir)}


# ------------------------------------------------------------------ trend (per caller, across audited days)


def update_history(rows: list[dict], path: str) -> dict[str, list[dict]]:
    hist = {}
    if os.path.exists(path):
        for line in open(path):
            if line.strip():
                h = json.loads(line)
                hist[h["lead_key"]] = h
    for r in rows:
        k = r["result"]["lead_key"]
        hist[k] = {"lead_key": k, "day": r["entry"]["calls_day"], "caller": callers(r["entry"]),
                   "strengths": r["result"]["tags"].get("strengths") or [], "gaps": r["result"]["tags"].get("gaps") or []}
    with open(path, "w") as f:
        for h in sorted(hist.values(), key=lambda h: (h["day"], h["lead_key"])):
            f.write(json.dumps(h) + "\n")
    by = defaultdict(list)
    for h in hist.values():
        by[h["caller"]].append(h)
    return by


def trend_lines(caller: str, history: dict[str, list[dict]], this: dict) -> list[str]:
    hs = history.get(caller) or []
    n = len(hs)
    first = min((h["day"] for v in history.values() for h in v), default=None)
    st, gp = Counter(t for h in hs for t in h["strengths"]), Counter(t for h in hs for t in h["gaps"])
    pretty = lambda t: t.replace("_", " ")
    out = [f"{n} follow-up lead{'s' if n != 1 else ''} audited for {caller} so far"
           + (f" (this audit's records start on {ddmmyyyy(first)})." if first else ".")]
    out += [f"✔ STRENGTH: {pretty(t)} in {k} of {n}" for t, k in st.most_common(3) if k >= 2 or n == 1]
    out += [f"REPEATING GAP: {pretty(t)} in {k} of {n}" + (" (again in these calls)" if t in this["tags"].get("gaps", []) else "")
            for t, k in gp.most_common(3) if k >= 2]
    return out


# ------------------------------------------------------------------ PDFs


def _li_call(c: dict, nac: set[str] = frozenset()) -> str:
    mark = " · NOT A CONVERSATION (voicemail/IVR/one-sided)" if reading.label(c) in nac else (
        " · transcript" if c.get("transcript") else " · no transcript")
    return f"<li>{e(reading.ist(c['start_utc'], '%d-%m %H:%M'))} IST · {mmss(c.get('duration') or 0)} · {e(c.get('caller') or 'no caller logged')}{mark}</li>"


def lead_pdf_html(r: dict, history: dict, fresh: str) -> str:
    sh, res, p, ent = r["sheet"], r["result"], r["result"]["pdf"], r["entry"]
    nac = set(res.get("not_a_conversation") or [])
    calls = "".join(_li_call(c, nac) for c in ent["calls"])
    bg = "".join(_li_call(c) for c in ent.get("background") or [])
    nr = ent.get("not_read") or {}
    flag = "" if res["compliance_flags"] == "NONE" else f"<div class=flag>COMPLIANCE FLAG — {e(res['compliance_flags'])}</div>"
    bp = "✔ IDENTIFIED" if res["burning_problem"] == "IDENTIFIED" else "✘ NOT IDENTIFIED"
    li = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>FOLLOW-UP CALL FEEDBACK REPORT — LawSikho / SkillArbitrage</h1>
<div class=mut>Counsellor(s): {e(sh['Caller Name'])} (LeadSquared: {e(callers(ent))}) · moved to {STAGE} {e(reading.ist(ent['moved_utc']))} IST · follow-up calls read together</div>
{flag}
<h2>SECTION 1 — CALL INFORMATION</h2><table>
<tr><th>Moved to {STAGE}</th><td>{e(reading.ist(ent['moved_utc']))} IST</td><th>Lead name</th><td>{e(sh['Lead Name'])} <span class=mut>(LeadSquared: {e(lead_name(ent))})</span></td></tr>
<tr><th>Mobile number</th><td>{e(sh['Mobile Number'])}</td><th>Talk time (scored calls)</th><td>{e(sh['Duration'])}</td></tr>
<tr><th>Calls scored from</th><td>{e(reading.start_text(ent))}</td><th>Discovery audit</th><td>{e(r['discovery'])}</td></tr>
<tr><th>Stage of call</th><td colspan=3>{e(p['stage_of_call'])}</td></tr>
<tr><th>Scored calls</th><td colspan=3><ul>{calls}</ul><div class=mut>{nr.get('unanswered', 0)} unanswered and {nr.get('short', 0)} under-30-second calls in the window were not read.{(' ' + e(ent['note'])) if ent.get('note') else ''}</div></td></tr>
{f'<tr><th>Background (not scored)</th><td colspan=3><ul>{bg}</ul></td></tr>' if bg else ''}</table>
<h2>SECTION 2 — LEAD PROFILE</h2><table>
<tr><th>Education and work</th><td>{e(p['education'])}</td></tr><tr><th>Immediate goal</th><td>{e(p['immediate_goal'])}</td></tr>
<tr><th>Current plan</th><td>{e(p['current_plan'])}</td></tr><tr><th>Current income</th><td>{e(p['current_income'])}</td></tr>
<tr><th>Burning problem</th><td><b>{bp}</b> — {e(p['burning_problem_note'])}</td></tr></table>
<h2>SECTION 3 — CONVERSION PROBABILITY SCORE</h2>
<div class=score>{r['strict']:g}/10 {r['band']} <span class=mut style="font-size:11px;font-weight:normal">· sheet: {sh['Conversion Potential (H,M,L)']} (relaxed, the prompt's) · strict {r['strict_label']} · discovery audit: {e(r['discovery'])}</span></div>
<div>{e(p['score_reason'])}</div>
<h2>SECTION 4 — CALL SUMMARY</h2><div>{e(p['call_summary'])}</div>
<div class=hl><b>Strongest moment —</b> {e(p['highlight_strongest'])}</div>
<div class=hl><b>Biggest miss —</b> {e(p['highlight_miss'])}</div>
<div class=hl><b>Where it stood at the move —</b> {e(p['highlight_end'])}</div>
<h2>SECTION 5 — WHAT WENT WRONG</h2><ul>{li(p['what_went_wrong'])}</ul>
<h2>SECTION 6 — IMPROVEMENT FOR NEXT CALL</h2><ul>{li(p['improvements'])}</ul>
<h2>SECTION 7 — TREND</h2><ul>{li(trend_lines(callers(ent), history, res))}</ul>
<div class=foot>Data read {e(fresh)} IST. Audit by Claude against Final_Prompt_Oct_2026 (unchanged; the follow-up window is set in its Custom Instructions);
every quote checked word for word; compliance re-checked by a second reading. Score out of 10 uses the strict objections rule; the sheet's H/M/L uses the prompt's relaxed rule. {e(res.get('quality_note') or '')}</div>
</body></html>"""


def summary_html(day: str, rows: list[dict], missing: list, moved: list, fresh: str, n_found: int, by_caller: list[list]) -> str:
    def name(r: dict) -> str:
        n = r["sheet"]["Lead Name"]
        return n if n != "Not Evident" else lead_name(r["entry"]) or n

    trs = "".join(f"<tr><td>{e(name(r))}</td><td>{e(callers(r['entry']))}</td><td><b>{POT[r['sheet']['Conversion Potential (H,M,L)']]}</b></td>"
                  f"<td>{r['strict']:g}/10 {r['band']}</td><td>{e(r['discovery'])}</td><td>{'CRITICAL' if r['result']['compliance_flags'] != 'NONE' else '—'}</td>"
                  f"<td>{e(r['result']['pdf']['highlight_end'])}</td></tr>" for r in rows)
    nas = "".join(f"<tr><td>{e(lead_name(m))}</td><td>{e(callers(m))}</td><td>{e(needs_check(m, st)[0])}</td><td>{e(needs_check(m, st)[1])}</td></tr>" for m, st, _ in missing)
    mvs = "".join(f"<tr><td>{e(lead_name(x))}</td><td>{e(callers(x))}</td><td>{e(reading.ist(x['moved_utc'], '%I:%M %p').lstrip('0'))}</td>"
                  f"<td>{e(moved_text(x, res)[0])}</td><td>{e(moved_text(x, res)[1])}</td></tr>" for x, res in moved)
    cal = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in rw) + "</tr>" for rw in by_caller)
    crit = [r for r in rows if r["result"]["compliance_flags"] != "NONE"]
    by_mover = Counter(callers(x) for x, _ in moved)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>Follow-up Call Audit — {ddmmyyyy(day)}</h1><div class=mut>All teams · leads moved to "{STAGE}" that day · their follow-up calls up to the move read together (from Discovery Call Done or their previous move into the stage, at most 30 days back) · data read {e(fresh)} IST</div>
<div class=hl><b>For team leaders:</b> {f"{len(crit)} lead{'s' if len(crit) != 1 else ''} heard a prohibited claim; coach those callers today ({e(', '.join(sorted({callers(r['entry']) for r in crit})))})." if crit else "No prohibited claims heard."}
{f"{len(moved)} lead{'s were' if len(moved) != 1 else ' was'} moved to closure without a follow-up conversation ({e(', '.join(f'{c} {k}' for c, k in by_mover.most_common()))}): ask the caller why." if moved else ""}
{f"{len(missing)} lead{'s need' if len(missing) != 1 else ' needs'} a check (listed at the end)." if missing else ""}</div>
<p>Leads moved: <b>{n_found}</b> · audited: <b>{len(rows)}</b> · moved without a conversation: <b>{len(moved)}</b> · need a check: <b>{len(missing)}</b></p>
<h2>BY CALLER</h2><table class=list><tr><th>Caller</th><th>Leads</th><th>High</th><th>Medium</th><th>Low</th><th>CRITICAL</th><th>Moved without a conversation</th></tr>{cal}</table>
<h2>AUDITED LEADS (highest potential first)</h2><table class=list><colgroup><col style="width:13%"><col style="width:14%"><col style="width:8%"><col style="width:10%"><col style="width:12%"><col style="width:9%"><col></colgroup>
<tr><th>Lead</th><th>Caller</th><th>Sheet</th><th>Score /10</th><th>Discovery audit</th><th>Compliance</th><th>Where it stood at the move</th></tr>{trs}</table>
{f'<h2>MOVED WITHOUT A CONVERSATION</h2><table class=list><tr><th>Lead</th><th>Caller</th><th>Moved</th><th>What happened</th><th>What to do</th></tr>{mvs}</table>' if moved else ''}
{f'<h2>NEEDS A CHECK</h2><table class=list><tr><th>Lead</th><th>Caller</th><th>What happened</th><th>What to do</th></tr>{nas}</table>' if missing else ''}
<div class=foot>Sheet = the prompt's relaxed H/M/L. Score = strict total out of 10 (HOT 7+, WARM 4–6.9, COLD below 4). Discovery audit = that audit's H/M/L when the lead reached Discovery Call Done (if its files were on this machine).</div></body></html>"""


# ------------------------------------------------------------------ workbook

FRONT = ["#", "Lead Name (LeadSquared)", "Current Stage (LeadSquared)", "Lead Owner (LeadSquared)", "Caller(s) in LeadSquared",
         f"Moved to {STAGE} (IST)", "Calls scored from", "Audit Status"]
EXTRA = ["Discovery audit (H/M/L)", "Strict Conversion Potential (H,M,L)", "Score /10 (PDF, strict)", "Compliance Flags", "Burning Problem",
         "Subscores (interest/pain/signals/objections/commitment)", "Scored calls", "Not a conversation (Claude)",
         "Background calls (not scored)", "Compliance re-check", "Note"]


SIDE = FRONT[:2] + ["Mobile Number"] + FRONT[2:-1]  # the other tabs: no audit status, but searchable by number


def side(i: int, ent: dict) -> list:
    """A lead's columns on the tabs other than the audit: the audit tab's front columns, with the lead's number."""
    f = front(i, ent, "")[:-1]
    return f[:2] + [mobile(ent)] + f[2:]


def front(i: int, ent: dict, st: str) -> list:
    l = ent["lead"]
    return [i, lead_name(ent), l.get("ProspectStage"), l.get("OwnerIdName"), callers(ent), reading.ist(ent["moved_utc"]),
            reading.start_text(ent), st]


def by_caller_rows(rows: list[dict], moved: list) -> list[list]:
    by, mv = defaultdict(list), Counter(callers(x) for x, _ in moved)
    for r in rows:
        by[callers(r["entry"])].append(r)
    out = []
    for c in sorted(set(by) | set(mv), key=lambda c: -(len(by.get(c, [])) + mv.get(c, 0))):
        rs = by.get(c, [])
        cnt = Counter(r["sheet"]["Conversion Potential (H,M,L)"] for r in rs)
        out.append([c, len(rs), cnt.get("H", 0), cnt.get("M", 0), cnt.get("L", 0),
                    sum(r["result"]["compliance_flags"] != "NONE" for r in rs), mv.get(c, 0)])
    return out


def build(day: str, entries: list[dict], folder: str, out_dir: str, history_path: str, ddir: str = "data/discovery_audit",
          outcomes: list[dict] | None = None, title_day: str | None = None) -> dict:
    fresh = now_ist().strftime("%d %b %Y %H:%M")
    rows, missing, moved = [], [], []
    for ent in entries:
        st, why = status(ent, folder)
        if st == "ANALYZED":
            rows.append(row(ent, result_of(ent, folder), ddir))
        elif st == "MOVED_WITHOUT_CALL":
            moved.append((ent, result_of(ent, folder)))
        else:
            missing.append((ent, st, why))
    rows.sort(key=lambda r: (-r["relaxed"], -r["strict"], callers(r["entry"])))
    history = update_history(rows, history_path)
    os.makedirs(out_dir, exist_ok=True)
    tag = ddmmyyyy(day).replace("-", "") if title_day is None else title_day

    wb = Workbook()
    ws = wb.active
    ws.title = "Follow-up Call Audit"
    ws.append(FRONT + reading.SHEET_COLUMNS + EXTRA)
    for i, r in enumerate(rows, 1):
        s, res, ent = r["result"]["subscores"], r["result"], r["entry"]
        ws.append(front(i, ent, "Audited") + [r["sheet"][c] for c in reading.SHEET_COLUMNS] + [
            r["discovery"], r["strict_label"], f"{r['strict']:g}/10 {r['band']}", res["compliance_flags"], res["burning_problem"],
            f"{s['interest']:g}/{s['burning_problem']:g}/{s['buying_signals']:g}/{(1.5 if not s['objection_raised'] else s['objections']):g}/{s['commitment']:g}",
            len(ent["calls"]), ", ".join(res.get("not_a_conversation") or []) or "None", len(ent.get("background") or []),
            (res.get("review") or {}).get("verdict", "not re-checked"), " ".join(x for x in (ent.get("note"), res.get("quality_note")) if x)])
    _style(ws, [4, 18, 16, 16, 18, 16, 30, 10, 14, 18, 11, 8, 12, 11, 40, 40, 24, 40, 36, 40, 13, 50, 45, 60, 16, 11, 12, 30, 14, 14, 8, 16, 10, 11, 30])
    ws.freeze_panes = "J2"

    sm = wb.create_sheet("Summary")
    n_found = len(entries)
    rel, stc = Counter(r["sheet"]["Conversion Potential (H,M,L)"] for r in rows), Counter(r["strict_label"] for r in rows)
    lines = [[f"Follow-up Call Audit — {ddmmyyyy(day)} (all teams). Data read {fresh} IST."],
             [f"Leads moved to {STAGE}: {n_found}. Audited: {len(rows)}. Moved without a conversation: {len(moved)}. "
              f"Need a check: {len(missing)} (see those tabs)."],
             [], ["Conversion potential (next 48 h)", "Relaxed (the prompt's)", "Strict"]]
    lines += [[k, rel.get(x, 0), stc.get(x, 0)] for k, x in (("High", "H"), ("Medium", "M"), ("Low", "L"))]
    lines += [[], ["IIT Compliance", "Leads"]] + [[k, sum(r['sheet']['IIT Compliance'] == k for r in rows)] for k in sorted(reading.IIT)]
    lines += [[], ["Leads with a prohibited claim (CRITICAL)", sum(r['result']['compliance_flags'] != 'NONE' for r in rows)], [],
              ["By caller", "Leads", "High", "Medium", "Low", "CRITICAL", "Moved without a conversation"]]
    cal = by_caller_rows(rows, moved)
    for ln in lines + cal:
        sm.append(ln)
    sm.column_dimensions["A"].width = 60

    mv = wb.create_sheet("Moved without a conversation")
    mv.append(SIDE + ["Last real call before the window", "First real call after the move", "What happened", "What to do"])
    for i, (ent, res) in enumerate(moved, 1):
        what, todo = moved_text(ent, res)
        mv.append(side(i, ent) + [when(ent.get("last_call_before")), when(ent.get("first_call_after")), what, todo])
    _style(mv, [4, 20, 13, 18, 18, 18, 18, 30, 30, 30, 45, 55])

    na = wb.create_sheet("Needs a Check")
    na.append(SIDE + ["What happened", "What to do"])
    for i, (ent, st, _) in enumerate(missing, 1):
        what, todo = needs_check(ent, st)
        na.append(side(i, ent) + [what, todo])
    _style(na, [4, 20, 13, 18, 18, 18, 18, 30, 45, 50])

    cl = wb.create_sheet("Calls")
    cl.append(["Lead (LeadSquared)", "Mobile Number", "Part", "Call start (IST)", "Direction", "Talk time", "Caller (LeadSquared)", "Transcript"])
    for ent in entries:
        for part, cs in (("Background (not scored)", ent.get("background") or []), ("Scored", ent["calls"])):
            for c in cs:
                cl.append([lead_name(ent), mobile(ent), part, reading.ist(c["start_utc"]), c.get("direction"), mmss(c.get("duration") or 0),
                           c.get("caller") or "", "Yes" if c.get("transcript") else "Not found"])
    _style(cl, [22, 13, 18, 17, 10, 9, 20, 11])

    oc = wb.create_sheet("7-day outcome")
    if outcomes is None:
        oc.append(["No audit from 7 days earlier on this machine, so no outcome check this time."])
    else:
        oc.append(["Lead (LeadSquared)", "Mobile Number", "Caller(s)", "Audited H/M/L", f"Moved to {STAGE} (IST)", "Enrolled since the move"])
        for o in outcomes:
            oc.append([o["lead"], o.get("mobile", ""), o["caller"], o["label"], o["moved"], o["enrolled"]])
        oc.append([])
        for lab in ("H", "M", "L"):
            got = [o for o in outcomes if o["label"] == lab]
            oc.append([f"{POT[lab]}: {sum(o['enrolled'] == 'Yes' for o in got)} of {len(got)} enrolled (first-time enrolment, Course Enrolled)"])
    _style(oc, [24, 13, 20, 12, 18, 30])

    how = wb.create_sheet("How This Was Made")
    for t in HOW:
        how.append([t])
    how.column_dimensions["A"].width = 150
    xlsx = f"{out_dir}/Followup_Call_Audit_{tag}.xlsx"
    wb.save(xlsx)

    pdf_dir = f"{folder}/pdf"
    os.makedirs(pdf_dir, exist_ok=True)
    pdfs = []
    for r in rows:
        nm = r["sheet"]["Lead Name"] if r["sheet"]["Lead Name"] != "Not Evident" else r["entry"]["lead"].get("FirstName")
        name = f"{safe(nm)}_{r['sheet']['Mobile Number']}_{safe(callers(r['entry']).split(',')[0])}_{tag}.pdf"
        print_pdf(lead_pdf_html(r, history, fresh), f"{pdf_dir}/{name}.html", f"{pdf_dir}/{name}")
        pdfs.append(f"{pdf_dir}/{name}")
    summary_pdf = f"{pdf_dir}/00_Followup_Summary_{tag}.pdf"
    print_pdf(summary_html(day, rows, missing, moved, fresh, n_found, cal), f"{summary_pdf}.html", summary_pdf)
    csv_path = f"{pdf_dir}/Batch_Summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["#", "Counsellor", "Lead", "Sheet (relaxed)", "Score", "Discovery audit", "Compliance Flag", "PDF filename"])
        for i, (r, p) in enumerate(zip(rows, pdfs), 1):
            w.writerow([i, callers(r["entry"]), r["sheet"]["Lead Name"], r["sheet"]["Conversion Potential (H,M,L)"],
                        f"{r['strict']:g}/10 {r['band']}", r["discovery"], r["result"]["compliance_flags"], os.path.basename(p)])
    zpath = f"{out_dir}/Followup_Feedback_Batch_{tag}_{len(rows)}leads.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in [summary_pdf, csv_path] + pdfs:
            z.write(p, os.path.basename(p))
    return {"found": n_found, "audited": len(rows), "moved_without_conversation": len(moved),
            "needs_check": Counter(st for _, st, _ in missing), "xlsx": xlsx, "zip": zpath, "summary_pdf": summary_pdf, "relaxed": dict(rel)}


HOW = [
    "How this audit was made",
    f"Leads: every lead whose LeadSquared stage changed to \"{STAGE}\" on the day (IST), all teams, found from the stage history "
    f"of every lead now in that stage and edited since the day (and of leads in a later stage called that day or the day before). "
    "LeadSquared was only read, never written to.",
    f"Calls scored: every answered call of 30+ seconds from the start of the window up to the moment of the move (a call in progress "
    f"at that moment counts), read together as one conversation, one row per lead. Calls after the move are never read. The window "
    f"starts at the latest of: the last move to Discovery Call Done that had an answered call in the 2 hours before it (a stage set "
    f"without a conversation, such as the bulk update of 13 Oct 2026, does not count); the lead's previous move into {STAGE}; and "
    f"30 days before the move (never further back).",
    "Background, not scored: the discovery call (the calls on the day of that Discovery Call Done move, up to the move), so the reader "
    "knows the lead's profile, pain and what was promised. The discovery audit scores that call; it is not scored twice.",
    f"Moved without a conversation: a lead with no answered call of {REAL_SECS // 60}+ minutes in its window, or whose only such calls were "
    "voicemail, an IVR menu, a recorded message or one-sided (Claude's reading), is not scored. It is listed with its last real call "
    "and its first one after the move, so the team leader can ask the caller why.",
    "Transcripts: Salesa, searched by the number on each call plus the lead's phone fields, matched to each LeadSquared call by start "
    "time and length (Salesa clocks can be 5 h 30 min off; the call's time is LeadSquared's).",
    "Caller in LeadSquared: the callers on the call records, in order. The stage change is often recorded as \"System\" or done by an "
    "automation, so it is never used to credit anyone; a call with no caller is marked Unverified.",
    "Audit: Claude read every lead's calls in full against Final_Prompt_Oct_2026, unchanged (no keyword rules, no model API). The "
    "follow-up window is explained to the reader in the prompt's own Custom Instructions slot. One reading gives both the sheet row and "
    "the PDF, with the same five subscores.",
    "Conversion Potential (the prompt's relaxed rule): no objection raised = full 1.5; H 6.5+, M 4.0-6.4, L below 4.0. Strict column: "
    "no objection but something left open = 0.75; H 7.0+. The PDF score out of 10 is the strict total (HOT 7+, WARM 4-6.9, COLD below 4).",
    "Discovery audit column: that audit's H/M/L for the same lead, when its files were on this machine; otherwise \"Not in the discovery audit\".",
    "Checks: every quoted phrase was matched word for word against the transcripts (a row that fails is redone), and every compliance "
    "decision was re-checked by a second Claude reading against the prompt's fixed bar.",
    f"Needs a check: a lead whose 2+ minute call has no recording in Salesa yet. It is tried again on the next two mornings; after the "
    f"third try it is marked final for a team leader to check by hand.",
    "7-day outcome: the leads audited a week earlier, and whether each has a first-time Course Enrolled stage change since its move.",
    "The workbook holds lead phone numbers: share it only inside the sales team.",
]
