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
    names = list(dict.fromkeys((c.get("caller") or "").strip() for c in entry["calls"] if c.get("status") == "Answered"))
    named = [n for n in names if n]
    if named:
        return ", ".join(named)
    ids = {c.get("user_id") for c in entry["calls"] if c.get("status") == "Answered" and c.get("user_id")}
    return f"Unverified (user id {', '.join(sorted(ids))})" if ids else "Unverified"


def status(entry: dict, folder: str) -> tuple[str, str]:
    """(status, reason) for one lead."""
    conv = [c for c in entry["calls"] if is_conversation(c)]
    if not conv:
        return "NOT_AUDITABLE", f"No answered call of {MIN_CONVERSATION_SECS}+ seconds that day (voicemail, screening or very short calls only)"
    if not any(c.get("transcript") for c in conv):
        return "TRANSCRIPT_NOT_FOUND", "Answered call(s) found, but Salesa holds no transcript yet; retried in the next run"
    p = f"{folder}/results/{reading.lead_key(entry)}.json"
    if reading.finished(p):
        return "ANALYZED", ""
    if os.path.exists(p):
        return "ANALYSIS_FAILED", "Result did not pass the checks"
    return "PENDING_ANALYSIS", "Not read yet"


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
<div class=mut>Counsellor: {e(sh['Caller Name'])} (LeadSquared: {e(callers(ent))}) · Date: {e(sh['Call Date'])} · all of that day's calls read together</div>
{flag}
<h2>SECTION 1 — CALL INFORMATION</h2><table>
<tr><th>Date of call</th><td>{e(sh['Call Date'])}</td><th>Lead name</th><td>{e(sh['Lead Name'])} <span class=mut>(LeadSquared: {e(' '.join(x for x in (ent['lead'].get('FirstName'), ent['lead'].get('LastName')) if x))})</span></td></tr>
<tr><th>Mobile number</th><td>{e(sh['Mobile Number'])}</td><th>Talk time (day)</th><td>{e(sh['Duration'])}</td></tr>
<tr><th>Stage of call</th><td>{e(p['stage_of_call'])}</td><th>Moved to Discovery Call Done</th><td>{e(utc(ent['stage_changed_utc']).astimezone(IST).strftime('%d-%m-%Y %H:%M'))} IST</td></tr>
<tr><th>Calls that day</th><td colspan=3><ul>{calls}</ul>{('<div class=mut>' + e(ent['note']) + '</div>') if ent.get('note') else ''}</td></tr></table>
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
<div class=hl><b>End of day —</b> {e(p['highlight_end'])}</div>
<h2>SECTION 5 — WHAT WENT WRONG</h2><ul>{li(p['what_went_wrong'])}</ul>
<h2>SECTION 6 — IMPROVEMENT FOR NEXT CALL</h2><ul>{li(p['improvements'])}</ul>
<h2>SECTION 7 — TREND</h2><ul>{li(trend_lines(callers(ent), history, res))}</ul>
<h2>CLOSER BRIEFING</h2><div>{e(sh['Closer Briefing'])}</div>
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
         "Subscores (interest/pain/signals/objections/commitment)", "Calls that day", "Compliance re-check", "Note"]
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
    rows, missing = [], []
    for ent in entries:
        st, why = status(ent, folder)
        if st == "ANALYZED":
            rows.append(row(ent, json.load(open(f"{folder}/results/{reading.lead_key(ent)}.json"))))
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
             [f"Leads moved to Discovery Call Done: {n_found}. Audited: {len(rows)}. Not audited: {len(missing)} (reasons on the Not Audited tab)."],
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

    na = wb.create_sheet("Not Audited")
    na.append(FRONT + ["Calls that day", "Reason"])
    for i, (ent, st, why) in enumerate(missing, 1):
        na.append(front(i, ent, st) + [len(ent["calls"]), why])
    _style(na, [4, 20, 18, 18, 18, 18, 22, 10, 70])

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
    print_pdf(summary_html(day, rows, missing, fresh, n_found), f"{summary_pdf}.html", summary_pdf)
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
    return {"found": n_found, "audited": len(rows), "not_audited": Counter(st for _, st, _ in missing),
            "xlsx": xlsx, "zip": zpath, "summary_pdf": summary_pdf, "relaxed": dict(rel)}


def summary_html(day: str, rows: list[dict], missing: list, fresh: str, n_found: int) -> str:
    pot = {"H": "High", "M": "Medium", "L": "Low"}
    def name(r: dict) -> str:
        n = r["sheet"]["Lead Name"]
        return n if n != "Not Evident" else " ".join(x for x in (r["entry"]["lead"].get("FirstName"), r["entry"]["lead"].get("LastName")) if x) or n

    trs = "".join(f"<tr><td>{e(name(r))}</td><td>{e(callers(r['entry']))}</td><td><b>{pot[r['sheet']['Conversion Potential (H,M,L)']]}</b></td>"
                  f"<td>{r['strict']:g}/10 {r['band']}</td><td>{'CRITICAL' if r['result']['compliance_flags'] != 'NONE' else '—'}</td>"
                  f"<td>{e(r['result']['pdf']['highlight_end'])}</td></tr>" for r in rows)
    nas = "".join(f"<tr><td>{e(' '.join(x for x in (m[0]['lead'].get('FirstName'), m[0]['lead'].get('LastName')) if x))}</td><td>{e(callers(m[0]))}</td><td>{e(m[1])}</td><td>{e(m[2])}</td></tr>" for m in missing)
    crit = [r for r in rows if r["result"]["compliance_flags"] != "NONE"]
    hi = [r for r in rows if r["sheet"]["Conversion Potential (H,M,L)"] == "H"]
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>
<h1>Discovery Call Audit — {ddmmyyyy(day)}</h1><div class=mut>All teams · leads moved to "Discovery Call Done" that day · every call of that day read together · data read {e(fresh)} IST</div>
<div class=hl><b>Do first:</b> closers, call the {len(hi)} High lead{'s' if len(hi) != 1 else ''} at the time each agreed (see each PDF's closer briefing).
{f"Team leaders: {len(crit)} lead{'s' if len(crit) != 1 else ''} heard a prohibited claim; coach those callers today ({e(', '.join(sorted({callers(r['entry']) for r in crit})))})." if crit else "No prohibited claims heard."}</div>
<p>Leads moved: <b>{n_found}</b> · audited: <b>{len(rows)}</b> · not audited: <b>{len(missing)}</b> (listed below with the reason).</p>
<h2>AUDITED LEADS (highest potential first)</h2><table class=list><colgroup><col style="width:13%"><col style="width:15%"><col style="width:8%"><col style="width:11%"><col style="width:10%"><col></colgroup>
<tr><th>Lead</th><th>Caller</th><th>Sheet</th><th>Score /10</th><th>Compliance</th><th>Next step</th></tr>{trs}</table>
{f'<h2>NOT AUDITED</h2><table class=list><tr><th>Lead</th><th>Caller</th><th>Status</th><th>Reason</th></tr>{nas}</table>' if missing else ''}
<div class=foot>Sheet = the prompt's relaxed H/M/L. Score = strict total out of 10 (HOT 7+, WARM 4–6.9, COLD below 4).</div></body></html>"""


HOW = [
    "How this audit was made",
    "Leads: every lead whose LeadSquared stage changed to \"Discovery Call Done\" on the day (IST), all teams. LeadSquared has no "
    "company-wide list of stage changes, so the day's calls (and the day before) were read, and the stage history of each called lead "
    "now in Discovery Call Done or a later stage was checked. LeadSquared was only read, never written to.",
    "Calls: every call to the lead that day, 00:00-23:59 IST, before and after the stage change, read together as one conversation "
    "(one row per lead). If the stage was set with no conversation that day, the previous day's calls are audited and the Note says so.",
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
    "Not audited: leads with only voicemail, screening or very short calls, or with no transcript yet (retried in the next run).",
    "The workbook holds lead phone numbers: share it only inside the sales team.",
]
