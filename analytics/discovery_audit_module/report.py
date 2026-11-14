"""The day's files: the audit workbook, one feedback PDF per lead (plus a summary PDF), a ZIP and a CSV."""

from __future__ import annotations

import csv
import glob
import html
import json
import os
import re
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from analytics.discovery_audit_module import reading
from analytics.discovery_audit_module.find import MIN_CONVERSATION_SECS, is_conversation, ist_hm
from integrations.timeutil import IST, now_ist, utc

e = lambda s: html.escape(str(s if s is not None else ""))


def chrome() -> str:
    return os.environ.get("CHROME_BIN") or next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))), "chromium")


def mmss(secs: int) -> str:
    return f"{secs // 60:02d}:{secs % 60:02d}"


def ddmmyyyy(day: str) -> str:
    return datetime.strptime(day, "%Y-%m-%d").strftime("%d-%m-%Y")


def mobile(entry: dict) -> str:
    raw = entry["lead"].get("Phone") or entry["lead"].get("Mobile") or next((c.get("lead_number") for c in entry["calls"] if c.get("lead_number")), "")
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if digits else "Not discussed"


def callers(entry: dict) -> str:
    """Who made the answered calls, from the call records (never the stage-change name, often "System")."""
    if entry.get("moved_without_call") and entry.get("caller_hint"):
        return entry["caller_hint"]   # the caller of the real call nearest the move
    names = list(dict.fromkeys((c.get("caller") or "").strip() for c in entry["calls"] if c.get("status") == "Answered"))
    named = [n for n in names if n]
    if named:
        return ", ".join(named)
    if entry.get("caller_hint"):
        return entry["caller_hint"]
    ids = {c.get("user_id") for c in entry["calls"] if c.get("status") == "Answered" and c.get("user_id")}
    return f"Unverified (user id {', '.join(sorted(ids))})" if ids else "Unverified"


MAX_TRIES = 3  # the run for the day itself plus the next two mornings


def when(c: dict | None) -> str:
    if not c:
        return "None"
    return f"{utc(c['start_utc']).astimezone(IST).strftime('%d %b, %I:%M %p').replace(' 0', ' ')} ({mmss(c.get('duration') or 0)}, {c.get('caller') or 'no caller logged'})"


def moved_without_call(entry: dict) -> tuple[str, str]:
    """(what happened, what to do) for a lead moved to the stage with no conversation before the move that day."""
    before, after = entry.get("last_call_before"), entry.get("first_call_after")
    short = any(c.get("status") == "Answered" for c in entry["calls"])
    if before:  # a conversation before the move that day would have been audited, so this one is from an earlier day
        return (f"Moved a day after the last real call ({when(before)})",
                "Ask the caller why the stage was moved a day late; the discovery call and the move should be the same day")
    if after:
        return (f"Moved before the call: the first real call that day came after the move ({when(after)})",
                "Ask the caller why the stage was moved before the discovery call")
    if short:
        return ("Only short or unanswered calls before the move",
                f"Check if the discovery really happened (no answered call of {MIN_CONVERSATION_SECS}+ seconds before the move)")
    return ("No real call found that day or the day before", "Check if a discovery call ever happened")


def status(entry: dict, folder: str) -> tuple[str, str]:
    """(status, reason) for one lead."""
    if entry.get("moved_without_call") or not any(is_conversation(c) for c in entry["calls"]):
        return "MOVED_WITHOUT_CALL", moved_without_call(entry)[0]
    conv = [c for c in entry["calls"] if is_conversation(c)]
    if not any(c.get("transcript") for c in conv):
        if entry.get("tries", 1) >= MAX_TRIES:
            return "TRANSCRIPT_NOT_FOUND", (f"No transcript after {MAX_TRIES} daily tries; not retried again. Team leader: check the "
                                            "recording in the dialer and Salesa, or review this call by hand")
        return "TRANSCRIPT_NOT_FOUND", (f"Answered call(s) found, but Salesa holds no transcript yet (try {entry.get('tries', 1)} of "
                                        f"{MAX_TRIES}); retried in the next run")
    p = f"{folder}/results/{reading.lead_key(entry)}.json"
    if reading.finished(p, reading.day_text(entry)):
        return "ANALYZED", ""
    if os.path.exists(p):
        return "ANALYSIS_FAILED", "Result did not pass the checks"
    return "PENDING_ANALYSIS", "Not read yet"


def needs_check(entry: dict, st: str, why: str) -> tuple[str, str]:
    """(what happened, what to do) in plain words for a lead that has no score yet."""
    if st == "TRANSCRIPT_NOT_FOUND":
        if entry.get("tries", 1) >= MAX_TRIES:
            return (f"Call recording still missing after {MAX_TRIES} tries",
                    "Check the call in the dialer and Salesa, or listen to it by hand")
        return (f"Call recording not ready yet (try {entry.get('tries', 1)} of {MAX_TRIES})", "Nothing; it is checked again tomorrow")
    return ("Not read yet" if st == "PENDING_ANALYSIS" else "Reading did not pass the checks", "Read again in the next run")


def row(entry: dict, result: dict) -> dict:
    s = result["subscores"]
    relaxed, strict = reading.totals(s)
    talk = sum(c.get("duration") or 0 for c in entry["calls"] if c.get("status") == "Answered")
    sheet = {**result["sheet"], "Call Date": ddmmyyyy(entry["calls_day"]), "Duration": mmss(talk),
             "Mobile Number": mobile(entry), "Conversion Potential (H,M,L)": reading.relaxed_label(relaxed)}
    return {"sheet": {c: sheet[c] for c in reading.SHEET_COLUMNS}, "relaxed": relaxed, "strict": strict,
            "strict_label": reading.strict_label(strict), "band": reading.pdf_band(strict), "result": result, "entry": entry}


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
    st, gp = Counter(t for h in hs for t in h["strengths"]), Counter(t for h in hs for t in h["gaps"])
    pretty = lambda t: t.replace("_", " ")
    out = [f"{n} lead-day{'s' if n != 1 else ''} audited for {caller} so far (this audit started on 14 Nov 2026)."]
    out += [f"✔ STRENGTH: {pretty(t)} in {k} of {n}" for t, k in st.most_common(3) if k >= 2 or n == 1]
    out += [f"REPEATING GAP: {pretty(t)} in {k} of {n}" + (" (again in this call)" if t in this["tags"].get("gaps", []) else "")
            for t, k in gp.most_common(3) if k >= 2]
    return out


# ------------------------------------------------------------------ PDFs

CSS = """@page{size:A4;margin:10mm 11mm} body{font-family:'DejaVu Sans',Arial,sans-serif;font-size:10px;color:#1d2433}
h1{font-size:16px;margin:0} .mut{color:#5b6475} h2{font-size:11px;margin:12px 0 4px;color:#fff;background:#1F3A5F;padding:4px 7px;border-radius:3px}
table{border-collapse:collapse;width:100%} td,th{border:1px solid #e1e5ec;padding:4px 6px;vertical-align:top;text-align:left} th{background:#f3f5f9;width:20%}
.flag{background:#fde8e7;border-left:4px solid #b3261e;padding:6px 8px;margin:8px 0;font-weight:bold}
.score{font-size:20px;font-weight:bold;margin:4px 0} .hl{background:#f3f5f9;border-left:3px solid #1F3A5F;padding:5px 8px;margin:6px 0}
ul{margin:2px 0 0 16px;padding:0} li{margin:2px 0} table.list th{width:auto} .foot{color:#5b6475;font-size:8.5px;margin-top:10px}"""


def lead_pdf_html(r: dict, history: dict, fresh: str) -> str:
    sh, res, p = r["sheet"], r["result"], r["result"]["pdf"]
    ent = r["entry"]
    calls = "".join(f"<li>{ist_hm(c['start_utc'])} IST · {e(c.get('status'))} · {mmss(c.get('duration') or 0)} · {e(c.get('caller') or 'no caller logged')}"
                    f"{'' if c.get('status') != 'Answered' else (' · transcript' if c.get('transcript') else ' · no transcript')}</li>" for c in ent["calls"])
    flag = "" if res["compliance_flags"] == "NONE" else f"<div class=flag>COMPLIANCE FLAG — {e(res['compliance_flags'])}</div>"
    bp = "✔ IDENTIFIED" if res["burning_problem"] == "IDENTIFIED" else "✘ NOT IDENTIFIED"
    li = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>CALL FEEDBACK REPORT — LawSikho / SkillArbitrage</h1>
<div class=mut>Counsellor: {e(sh['Caller Name'])} (LeadSquared: {e(callers(ent))}) · Date: {e(sh['Call Date'])} · calls up to the move to Discovery Call Done, read together</div>
{flag}
<h2>SECTION 1 — CALL INFORMATION</h2><table>
<tr><th>Date of call</th><td>{e(sh['Call Date'])}</td><th>Lead name</th><td>{e(sh['Lead Name'])} <span class=mut>(LeadSquared: {e(' '.join(x for x in (ent['lead'].get('FirstName'), ent['lead'].get('LastName')) if x))})</span></td></tr>
<tr><th>Mobile number</th><td>{e(sh['Mobile Number'])}</td><th>Talk time (before the move)</th><td>{e(sh['Duration'])}</td></tr>
<tr><th>Stage of call</th><td>{e(p['stage_of_call'])}</td><th>Moved to Discovery Call Done</th><td>{e(utc(ent['stage_changed_utc']).astimezone(IST).strftime('%d-%m-%Y %H:%M'))} IST</td></tr>
<tr><th>Calls before the move</th><td colspan=3><ul>{calls}</ul>{('<div class=mut>' + e(ent['note']) + '</div>') if ent.get('note') else ''}</td></tr></table>
<h2>SECTION 2 — LEAD PROFILE</h2><table>
<tr><th>Education and work</th><td>{e(p['education'])}</td></tr><tr><th>Immediate goal</th><td>{e(p['immediate_goal'])}</td></tr>
<tr><th>Current plan</th><td>{e(p['current_plan'])}</td></tr><tr><th>Current income</th><td>{e(p['current_income'])}</td></tr>
<tr><th>Burning problem</th><td><b>{bp}</b> — {e(p['burning_problem_note'])}</td></tr></table>
<h2>SECTION 3 — CONVERSION PROBABILITY SCORE</h2>
<div class=score>{r['strict']:g}/10 {r['band']} <span class=mut style="font-size:11px;font-weight:normal">· sheet: {sh['Conversion Potential (H,M,L)']} (relaxed, the prompt's) · strict {r['strict_label']}</span></div>
<div>{e(p['score_reason'])}</div>
<h2>SECTION 4 — CALL SUMMARY</h2><div>{e(p['call_summary'])}</div>
<div class=hl><b>Strongest moment —</b> {e(p['highlight_strongest'])}</div>
<div class=hl><b>Biggest miss —</b> {e(p['highlight_miss'])}</div>
<div class=hl><b>Where it ended —</b> {e(p['highlight_end'])}</div>
<h2>SECTION 5 — WHAT WENT WRONG</h2><ul>{li(p['what_went_wrong'])}</ul>
<h2>SECTION 6 — IMPROVEMENT FOR NEXT CALL</h2><ul>{li(p['improvements'])}</ul>
<h2>SECTION 7 — TREND</h2><ul>{li(trend_lines(callers(ent), history, res))}</ul>
<div class=foot>Data read {e(fresh)} IST. Audit by Claude against Final_Prompt_Oct_2026; every quote checked word for word; compliance re-checked by a second reading.
Score out of 10 uses the strict objections rule; the sheet's H/M/L uses the prompt's relaxed rule. {e(res.get('quality_note') or '')}</div>
</body></html>"""


def print_pdf(doc: str, html_path: str, pdf_path: str) -> None:
    open(html_path, "w").write(doc)
    subprocess.run([chrome(), "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", f"file://{os.path.abspath(html_path)}"], check=True, capture_output=True)


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:30] or "NA"


# ------------------------------------------------------------------ workbook

EXTRA = ["Strict Conversion Potential (H,M,L)", "Score /10 (PDF, strict)", "Compliance Flags", "Burning Problem",
         "Subscores (interest/pain/signals/objections/commitment)", "Calls before the move", "Compliance re-check", "Note"]
FRONT = ["#", "Lead Name (LeadSquared)", "Current Stage (LeadSquared)", "Lead Owner (LeadSquared)", "Caller in LeadSquared",
         "Moved to Discovery Call Done (IST)", "Audit Status"]


def _style(ws, widths=None):
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for rw in ws.iter_rows(min_row=2):
        for c in rw:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for i, w in enumerate(widths or [], 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w


def front(i: int, ent: dict, st: str) -> list:
    l = ent["lead"]
    return [i, " ".join(x for x in (l.get("FirstName"), l.get("LastName")) if x), l.get("ProspectStage"), l.get("OwnerIdName"),
            callers(ent), utc(ent["stage_changed_utc"]).astimezone(IST).strftime("%d-%m-%Y %H:%M"), st]


def build(day: str, entries: list[dict], folder: str, out_dir: str, history_path: str) -> dict:
    fresh = now_ist().strftime("%d %b %Y %H:%M")
    rows, missing, moved = [], [], []
    for ent in entries:
        st, why = status(ent, folder)
        if st == "ANALYZED":
            rows.append(row(ent, json.load(open(f"{folder}/results/{reading.lead_key(ent)}.json"))))
        elif st == "MOVED_WITHOUT_CALL":
            moved.append(ent)
        else:
            missing.append((ent, st, why))
    rows.sort(key=lambda r: (-r["relaxed"], -r["strict"], callers(r["entry"])))
    history = update_history(rows, history_path)
    os.makedirs(out_dir, exist_ok=True)
    tag = ddmmyyyy(day).replace("-", "")

    wb = Workbook()
    ws = wb.active
    ws.title = "Discovery Call Audit"
    ws.append(FRONT + reading.SHEET_COLUMNS + EXTRA)
    for i, r in enumerate(rows, 1):
        s, res, ent = r["result"]["subscores"], r["result"], r["entry"]
        ws.append(front(i, ent, "Audited") + [r["sheet"][c] for c in reading.SHEET_COLUMNS] + [
            r["strict_label"], f"{r['strict']:g}/10 {r['band']}", res["compliance_flags"], res["burning_problem"],
            f"{s['interest']:g}/{s['burning_problem']:g}/{s['buying_signals']:g}/{(1.5 if not s['objection_raised'] else s['objections']):g}/{s['commitment']:g}",
            len(ent["calls"]), (res.get("review") or {}).get("verdict", "not re-checked"),
            " ".join(x for x in (ent.get("note"), res.get("quality_note")) if x)])
    _style(ws, [4, 18, 16, 16, 16, 16, 10, 14, 18, 11, 8, 12, 11, 40, 40, 24, 40, 36, 40, 13, 50, 45, 60, 11, 12, 30, 14, 14, 8, 11, 30])
    ws.freeze_panes = "I2"

    sm = wb.create_sheet("Summary")
    n_found = len(entries)
    rel, stc = Counter(r["sheet"]["Conversion Potential (H,M,L)"] for r in rows), Counter(r["strict_label"] for r in rows)
    lines = [[f"Discovery Call Audit — {ddmmyyyy(day)} (all teams). Data read {fresh} IST."],
             [f"Leads moved to Discovery Call Done: {n_found}. Audited: {len(rows)}. Moved without a call: {len(moved)}. "
              f"Need a check: {len(missing)} (see those tabs)."],
             [], ["Conversion potential (next 48 h)", "Relaxed (the prompt's)", "Strict"]]
    lines += [[k, rel.get(x, 0), stc.get(x, 0)] for k, x in (("High", "H"), ("Medium", "M"), ("Low", "L"))]
    lines += [[], ["IIT Compliance", "Calls"]] + [[k, sum(r['sheet']['IIT Compliance'] == k for r in rows)] for k in sorted(reading.IIT)]
    lines += [[], ["Burning problem identified", sum(r['result']['burning_problem'] == 'IDENTIFIED' for r in rows)],
              ["Leads with a prohibited claim (CRITICAL)", sum(r['result']['compliance_flags'] != 'NONE' for r in rows)], [],
              ["By caller", "Leads", "High", "Medium", "Low", "CRITICAL", "Burning problem"]]
    by = defaultdict(list)
    for r in rows:
        by[callers(r["entry"])].append(r)
    for c, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        cnt = Counter(r["sheet"]["Conversion Potential (H,M,L)"] for r in rs)
        lines.append([c, len(rs), cnt.get("H", 0), cnt.get("M", 0), cnt.get("L", 0),
                      sum(r["result"]["compliance_flags"] != "NONE" for r in rs), sum(r["result"]["burning_problem"] == "IDENTIFIED" for r in rs)])
    for ln in lines:
        sm.append(ln)
    sm.column_dimensions["A"].width = 60

    mv = wb.create_sheet("Moved without a call")
    mv.append(FRONT[:-1] + ["Last real call before the move", "First real call after the move", "What happened", "What to do"])
    for i, ent in enumerate(moved, 1):
        what, todo = moved_without_call(ent)
        mv.append(front(i, ent, "")[:-1] + [when(ent.get("last_call_before")), when(ent.get("first_call_after")), what, todo])
    _style(mv, [4, 20, 18, 18, 18, 18, 30, 30, 45, 55])

    na = wb.create_sheet("Needs a Check")
    na.append(FRONT[:-1] + ["What happened", "What to do"])
    for i, (ent, st, why) in enumerate(missing, 1):
        what, todo = needs_check(ent, st, why)
        na.append(front(i, ent, "")[:-1] + [what, todo])
    _style(na, [4, 20, 18, 18, 18, 18, 45, 50])

    cl = wb.create_sheet("Calls")
    cl.append(["Lead (LeadSquared)", "Call start (IST)", "Direction", "Status", "Talk time", "Caller (LeadSquared)", "Transcript"])
    for ent in entries:
        nm = " ".join(x for x in (ent["lead"].get("FirstName"), ent["lead"].get("LastName")) if x)
        for c in ent["calls"]:
            cl.append([nm, utc(c["start_utc"]).astimezone(IST).strftime("%d-%m-%Y %H:%M"), c.get("direction"), c.get("status"),
                       mmss(c.get("duration") or 0), c.get("caller") or "", "Yes" if c.get("transcript") else ("" if c.get("status") != "Answered" else "Not found")])
    _style(cl, [22, 17, 10, 12, 9, 20, 11])

    how = wb.create_sheet("How This Was Made")
    for t in HOW:
        how.append([t])
    how.column_dimensions["A"].width = 150
    xlsx = f"{out_dir}/Discovery_Call_Audit_{tag}.xlsx"
    wb.save(xlsx)

    pdf_dir = f"{folder}/pdf"
    os.makedirs(pdf_dir, exist_ok=True)
    pdfs = []
    for r in rows:
        name = f"{safe(r['sheet']['Lead Name'] if r['sheet']['Lead Name'] != 'Not Evident' else r['entry']['lead'].get('FirstName'))}_{r['sheet']['Mobile Number']}_{safe(callers(r['entry']).split(',')[0])}_{tag}.pdf"
        print_pdf(lead_pdf_html(r, history, fresh), f"{pdf_dir}/{name}.html", f"{pdf_dir}/{name}")
        pdfs.append(f"{pdf_dir}/{name}")
    summary_pdf = f"{pdf_dir}/00_Summary_{tag}.pdf"
    print_pdf(summary_html(day, rows, missing, moved, fresh, n_found), f"{summary_pdf}.html", summary_pdf)
    csv_path = f"{pdf_dir}/Batch_Summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["#", "Counsellor", "Lead", "Sheet (relaxed)", "Score", "Compliance Flag", "PDF filename"])
        for i, (r, p) in enumerate(zip(rows, pdfs), 1):
            w.writerow([i, callers(r["entry"]), r["sheet"]["Lead Name"], r["sheet"]["Conversion Potential (H,M,L)"],
                        f"{r['strict']:g}/10 {r['band']}", r["result"]["compliance_flags"], os.path.basename(p)])
    zpath = f"{out_dir}/Call_Feedback_Batch_{tag}_{len(rows)}leads.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in [summary_pdf, csv_path] + pdfs:
            z.write(p, os.path.basename(p))
    return {"found": n_found, "audited": len(rows), "moved_without_call": len(moved), "needs_check": Counter(st for _, st, _ in missing),
            "xlsx": xlsx, "zip": zpath, "summary_pdf": summary_pdf, "relaxed": dict(rel)}


def summary_html(day: str, rows: list[dict], missing: list, moved: list, fresh: str, n_found: int) -> str:
    pot = {"H": "High", "M": "Medium", "L": "Low"}
    def name(r: dict) -> str:
        n = r["sheet"]["Lead Name"]
        return n if n != "Not Evident" else " ".join(x for x in (r["entry"]["lead"].get("FirstName"), r["entry"]["lead"].get("LastName")) if x) or n

    trs = "".join(f"<tr><td>{e(name(r))}</td><td>{e(callers(r['entry']))}</td><td><b>{pot[r['sheet']['Conversion Potential (H,M,L)']]}</b></td>"
                  f"<td>{r['strict']:g}/10 {r['band']}</td><td>{'CRITICAL' if r['result']['compliance_flags'] != 'NONE' else '—'}</td>"
                  f"<td>{e(r['result']['pdf']['highlight_end'])}</td></tr>" for r in rows)
    nm = lambda ent: " ".join(x for x in (ent["lead"].get("FirstName"), ent["lead"].get("LastName")) if x)
    nas = "".join(f"<tr><td>{e(nm(m[0]))}</td><td>{e(callers(m[0]))}</td><td>{e(needs_check(*m)[0])}</td><td>{e(needs_check(*m)[1])}</td></tr>" for m in missing)
    mvs = "".join(f"<tr><td>{e(nm(x))}</td><td>{e(callers(x))}</td><td>{e(utc(x['stage_changed_utc']).astimezone(IST).strftime('%I:%M %p').lstrip('0'))}</td>"
                  f"<td>{e(moved_without_call(x)[0])}</td><td>{e(moved_without_call(x)[1])}</td></tr>" for x in moved)
    crit = [r for r in rows if r["result"]["compliance_flags"] != "NONE"]
    by_mover = Counter(callers(x) for x in moved)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>Discovery Call Audit — {ddmmyyyy(day)}</h1><div class=mut>All teams · leads moved to "Discovery Call Done" that day · their calls up to the move read together (later follow-ups left out) · data read {e(fresh)} IST</div>
<div class=hl><b>For team leaders:</b> {f"{len(crit)} lead{'s' if len(crit) != 1 else ''} heard a prohibited claim; coach those callers today ({e(', '.join(sorted({callers(r['entry']) for r in crit})))})." if crit else "No prohibited claims heard."}
{f"{len(moved)} lead{'s were' if len(moved) != 1 else ' was'} moved without a call first ({e(', '.join(f'{c} {k}' for c, k in by_mover.most_common()))}): ask the caller why." if moved else ""}
{f"{len(missing)} lead{'s need' if len(missing) != 1 else ' needs'} a check (listed at the end)." if missing else ""}</div>
<p>Leads moved: <b>{n_found}</b> · audited: <b>{len(rows)}</b> · moved without a call: <b>{len(moved)}</b> · need a check: <b>{len(missing)}</b></p>
<h2>AUDITED LEADS (highest potential first)</h2><table class=list><colgroup><col style="width:13%"><col style="width:15%"><col style="width:8%"><col style="width:11%"><col style="width:10%"><col></colgroup>
<tr><th>Lead</th><th>Caller</th><th>Sheet</th><th>Score /10</th><th>Compliance</th><th>Where it ended</th></tr>{trs}</table>
{f'<h2>MOVED WITHOUT A CALL</h2><table class=list><tr><th>Lead</th><th>Caller</th><th>Moved</th><th>What happened</th><th>What to do</th></tr>{mvs}</table>' if moved else ''}
{f'<h2>NEEDS A CHECK</h2><table class=list><tr><th>Lead</th><th>Caller</th><th>What happened</th><th>What to do</th></tr>{nas}</table>' if missing else ''}
<div class=foot>Sheet = the prompt's relaxed H/M/L. Score = strict total out of 10 (HOT 7+, WARM 4–6.9, COLD below 4).</div></body></html>"""


HOW = [
    "How this audit was made",
    "Leads: every lead whose LeadSquared stage changed to \"Discovery Call Done\" on the day (IST), all teams. LeadSquared has no "
    "company-wide list of stage changes, so the day's calls (and the day before) were read, and the stage history of each called lead "
    "now in Discovery Call Done or a later stage was checked. LeadSquared was only read, never written to.",
    "Calls: every call to the lead that day from 00:00 IST up to the moment the stage changed (a call in progress at that moment "
    "counts), read together as one conversation (one row per lead). Calls after the change are follow-ups and are not read.",
    f"Moved without a call: the discovery call and the stage change happen on the same day, so a lead with no answered call of "
    f"{MIN_CONVERSATION_SECS}+ seconds before the move that day is not scored. It is listed on that tab with its last real call before "
    "the move and its first one after it, so the team leader can ask the caller why.",
    "Transcripts: Salesa, searched by the number on each call plus the lead's phone fields, matched to each LeadSquared call by start "
    "time and length (Salesa clocks can be 5 h 30 min off; the call's time is LeadSquared's).",
    "Caller in LeadSquared: the caller on the call records. The stage change is often recorded as \"System\", so it is never used to credit "
    "anyone; a call with no caller is marked Unverified.",
    "Audit: Claude read every lead's day in full against Final_Prompt_Oct_2026 (no keyword rules, no model API). One reading gives both "
    "the sheet row and the PDF, with the same five subscores.",
    "Conversion Potential (the prompt's relaxed rule): no objection raised = full 1.5; H 6.5+, M 4.0-6.4, L below 4.0. Strict column: "
    "no objection but something left open = 0.75; H 7.0+. The PDF score out of 10 is the strict total (HOT 7+, WARM 4-6.9, COLD below 4).",
    "Checks: every quoted phrase was matched word for word against the transcripts (a row that fails is redone), and every compliance "
    "decision was re-checked by a second Claude reading against the prompt's fixed bar.",
    "Needs a check: a lead whose call recording is not in Salesa yet. It is tried again on the next two mornings (the lead then appears "
    "in that day's report); after the third try it is marked final for a team leader to check by hand.",
    "The workbook holds lead phone numbers: share it only inside the sales team.",
]
