"""Weekly review: every connected call of the week, its transcript and Claude's reading of it, rolled up into why
enrollments did or did not come through, what went wrong on the calls and what to fix next week.

The calls come from the week's daily pulls in ``data/daily`` (one row per activity, newest pull wins). Each connected
call is matched to its Salesa recording through ``match_to_calls``; numbers never searched are searched once
(at most 9 requests of 10 numbers per run). Every lead with a connected call gets one dossier with the whole week of
calls, transcripts included, and Claude reads every call in it (docs/weekly_brief.md). The call ledger
(``calls.jsonl``) keeps each connected call with its transcript status and, once read, its reading.

    python -m analytics.daily_plan weekly-prepare 2026-10-08 2026-10-16
    python -m analytics.daily_plan weekly-build 2026-10-08 2026-10-16
"""

from __future__ import annotations

import glob
import html
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.daily_plan import dossiers as dz
from analytics.daily_plan.common import Snap, ist
from integrations.timeutil import now_utc
from integrations.transcripts import normalize_phone
from integrations.transcripts.client import Call, match_to_calls

MAX_TX_CHARS = 9000  # one call's transcript in a dossier; longer ones are cut in the middle
REASONS = {
    "L1": "Not given to a caller in time", "L2": "Dialled late or never", "L3": "Never had a real conversation",
    "L4": "Lead's call not returned", "L5": "No dated next step", "L6": "Follow-up missed", "L7": "Closed without a real talk",
    "L9": "Payment started, not completed", "L10": "Course not matched", "L11": "Objection not answered",
    "L12": "Hot lead not prioritised", "L13": "Lost in reassignment",
    "ASK": "Real interest, no fee or ask made", "DECIDER": "Decider not involved", "BUDGET": "Can't pay now",
    "TIMING": "Wants to start later", "NOT_INTERESTED": "Clear no, with a reason", "DECIDING": "Still deciding, live",
}
OUTCOMES = ("enrolled", "paying", "open", "lost", "not a prospect")
MISTAKES = ("none", "no ask", "no discovery", "wrong or inconsistent fact", "overpromise", "objection missed",
            "no next step fixed", "callback not kept", "wrong course", "tone", "line or audio problem")


def key(start: str, end: str) -> str:
    return f"{start}_{end}"


def gather(root: str, start: str, end: str) -> dict:
    """One snapshot for the week from every daily pull: users and leads from the newest pull, calls by activity id."""
    files = [f for f in glob.glob(os.path.join(root, "*", "*.json"))
             if os.path.basename(f).startswith(("snap", "status_")) and not f.endswith("_enrolled.json")]
    snaps = []
    for f in files:
        try:
            s = json.load(open(f))
        except (ValueError, OSError):
            continue
        if isinstance(s, dict) and "calls" in s and s.get("fetched_at"):
            snaps.append(s)
    snaps.sort(key=lambda s: s["fetched_at"])
    users, leads, calls, zips = {}, {}, {}, {}
    for s in snaps:
        users.update({u["ID"]: u for u in s["users"]})
        leads.update({l["ProspectID"]: l for l in s["leads"]})
        calls.update({c["activity_id"]: c for c in s["calls"]})
        zips.update({z["ProspectActivityId"]: z for z in s.get("zip_activities", [])})
    keep = lambda t: t and start <= t.strftime("%Y-%m-%d") <= end  # noqa: E731
    return {"group": snaps[-1]["group"] if snaps else "", "start": start, "end": end,
            "fetched_at": snaps[-1]["fetched_at"] if snaps else now_utc().isoformat(),
            "users": list(users.values()), "leads": list(leads.values()),
            "calls": [c for c in calls.values() if keep(ist(c.get("start_utc")))],
            "zip_activities": [z for z in zips.values() if keep(ist(z.get("CreatedOn")))], "payments": []}


def connected(snap: Snap) -> list[dict]:
    """Answered calls made by a team member or to a team lead, oldest first."""
    return sorted((c for c in snap.calls if c["status"] == "Answered" and (c.get("user_id") in snap.users or c["lead_id"] in snap.leads)),
                  key=lambda c: c["t"])


def load_tx(*dirs: str) -> tuple[dict, dict]:
    """Every saved transcript search under ``dirs`` (any depth): ({number: raw}, {number: when searched}), newest wins."""
    tx, when = {}, {}
    files = sorted((f for d in dirs for f in glob.glob(os.path.join(d, "**", "tx*", "run_*.json"), recursive=True)), key=os.path.getmtime)
    for f in files:
        try:
            raw = json.load(open(f))
        except (ValueError, OSError):
            continue
        t = datetime.fromtimestamp(os.path.getmtime(f)).astimezone()
        tx.update(raw)
        when.update({n: t for n in raw})
    return tx, when


def to_search(snap: Snap, calls: list[dict], when: dict) -> list[str]:
    """Numbers whose last search came before one of their connected calls ended: longest call first."""
    out = []
    for c in sorted(calls, key=lambda c: -(c.get("duration") or 0)):
        n = normalize_phone(snap.phone(c))
        if n and n not in out and (n not in when or when[n] < c["t"] + timedelta(seconds=(c.get("duration") or 0) + 600)):
            out.append(n)
    return out


def match(snap: Snap, calls: list[dict], tx: dict) -> dict[str, Call]:
    """activity_id -> the recording that belongs to it (each recording to one call, closest first)."""
    api = [Call.from_api(n, kind, r) for n, raw in tx.items() for kind, k in (("sales", "sales_call"), ("support", "support_calls"))
           for r in (raw or {}).get(k) or []]
    found = match_to_calls([a for a in api if a.has_transcript], [dict(c, lead_number=snap.phone(c)) for c in calls])
    used, out = set(), {}
    for aid, (x, _) in sorted(found.items(), key=lambda kv: abs(kv[1][1])):
        ident = (x.phone, x.start_time, x.kind)
        if ident not in used:
            used.add(ident)
            out[aid] = x
    return out


def ledger(snap: Snap, calls: list[dict], recs: dict[str, Call], searched: set[str]) -> list[dict]:
    rows = []
    for c in calls:
        n = normalize_phone(snap.phone(c))
        x = recs.get(c["activity_id"])
        status = "transcript" if x else ("no recording found" if n in searched else "not searched")
        rows.append({"call_id": c["activity_id"], "lead_id": c["lead_id"], "lead": snap.lead_name(c["lead_id"]),
                     "caller": snap.users.get(c.get("user_id")) or c.get("caller") or "?", "t": c["t"].strftime("%Y-%m-%d %H:%M"),
                     "direction": c["direction"], "secs": c["duration"], "transcript": status, "chars": len(x.transcript) if x else 0})
    return rows


def _cut(t: str) -> str:
    return t if len(t) <= MAX_TX_CHARS else t[:MAX_TX_CHARS // 2] + "\n[… middle of the call cut …]\n" + t[-MAX_TX_CHARS // 2:]


def build_dossiers(snap: Snap, calls: list[dict], recs: dict[str, Call], out_dir: str, enrolled: dict) -> list[dict]:
    """One text file per lead with a connected call: the lead, every call of the week, each connected call's transcript."""
    os.makedirs(out_dir, exist_ok=True)
    conn = defaultdict(list)
    for c in calls:
        conn[c["lead_id"]].append(c)
    zips = defaultdict(list)
    for z in snap.raw.get("zip_activities", []):
        if str(z.get("ActivityEvent")) == dz.ZIP_NOTES:
            zips[z.get("RelatedProspectId")].append(z)
    index = []
    for lid, cs in conn.items():
        l = snap.leads.get(lid) or {}
        owner = l.get("OwnerIdName") or snap.users.get(cs[-1].get("user_id")) or "?"
        all_calls = snap.by_lead.get(lid, [])
        unanswered = Counter(c["t"].strftime("%a %d") for c in all_calls if c["status"] != "Answered")
        L = [f"LEAD {snap.lead_name(lid)} | lead_id {lid} | owner {owner} | stage now {l.get('ProspectStage') or '?'} | "
             f"LSQ course {l.get('mx_Enquired_Course') or '?'} | source {l.get('Source') or '?'} | created {(l.get('CreatedOn') or '?')[:10]}",
             f"First moved to Course Enrolled: {enrolled.get(lid) or 'never'}",
             "Unanswered or failed calls this week by day: " + (", ".join(f"{d} {n}" for d, n in sorted(unanswered.items())) or "none"),
             f"CONNECTED CALLS THIS WEEK ({len(cs)}; read every one):"]
        for c in cs:
            x = recs.get(c["activity_id"])
            who = snap.users.get(c.get("user_id")) or c.get("caller") or "?"
            L.append(f"\n--- CALL {c['activity_id']} | {c['t']:%a %d %b %H:%M} IST | {c['direction']} | {c['duration']}s | {who}")
            note = (c.get("call_notes") or "").strip()
            if note and note not in ("Answered", "CallFailure"):
                L.append(f"LeadSquared note: {note[:300]}")
            L.append(("TRANSCRIPT:\n" + _cut(x.transcript.strip())) if x else "TRANSCRIPT: none found for this call")
        zs = sorted(zips.get(lid, []), key=lambda z: z.get("CreatedOn") or "")
        if zs:
            L.append("\nZIPTEAMS NOTES THIS WEEK (one input only):")
            L += [f"  {ist(z['CreatedOn']):%a %d %H:%M} | {dz._strip(z.get('ActivityEvent_Note'))[:700]}" for z in zs[-4:]]
        text = "\n".join(L)
        path = os.path.join(out_dir, f"{lid}.txt")
        open(path, "w").write(text)
        index.append({"lead_id": lid, "owner": owner, "path": path, "size": len(text), "call_ids": [c["activity_id"] for c in cs]})
    return index


def write_batches(index: list[dict], reads_dir: str, max_bytes: int = 200_000) -> list[str]:
    os.makedirs(reads_dir, exist_ok=True)
    n = max(1, -(-sum(i["size"] for i in index) // max_bytes))
    files = []
    for k, b in enumerate(dz.batches(index, n)):
        f = os.path.join(reads_dir, f"batch_{k:02d}.txt")
        open(f, "w").write("\n".join(i["path"] for i in b) + "\n")
        files.append(f)
    return files


def load_reads(reads_dir: str) -> dict[str, dict]:
    out = {}
    for f in sorted(glob.glob(os.path.join(reads_dir, "out_*.jsonl"))):
        for ln in open(f):
            if ln.strip():
                try:
                    r = json.loads(ln)
                    out[r["lead_id"]] = r
                except (ValueError, KeyError):
                    continue
    return out


def check(index: list[dict], reads: dict[str, dict]) -> dict:
    """Which leads and calls still lack a reading: every connected call must be read once."""
    missing_leads, missing_calls = [], []
    for i in index:
        r = reads.get(i["lead_id"])
        if not r:
            missing_leads.append(i["lead_id"])
            continue
        got = {c.get("call_id") for c in r.get("calls") or []}
        missing_calls += [cid for cid in i["call_ids"] if cid not in got]
    return {"leads": len(index), "read": len(index) - len(missing_leads), "missing_leads": missing_leads, "missing_calls": missing_calls}


def roll_up(rows: list[dict], reads: dict[str, dict]) -> dict:
    """Coverage, outcomes (one per lead, adding up to every lead with a connected call), reasons, call habits."""
    by_call = {c.get("call_id"): c for r in reads.values() for c in r.get("calls") or []}
    tx = [r for r in rows if r["transcript"] == "transcript"]
    out = Counter(r.get("outcome") if r.get("outcome") in OUTCOMES else "open" for r in reads.values())
    reasons = Counter(r.get("primary_reason") for r in reads.values() if r.get("outcome") in ("open", "lost") and r.get("primary_reason"))
    per = defaultdict(lambda: Counter())
    for r in rows:
        p = per[r["caller"]]
        p["connected"] += 1
        p["real"] += r["secs"] >= 120
        x = by_call.get(r["call_id"])
        if x:
            p["read"] += 1
            if r["secs"] >= 120:
                p["real_read"] += 1
                p["ask"] += bool(x.get("ask_made"))
                p["link"] += bool(x.get("link_sent"))
                p["next"] += bool(x.get("next_step_fixed"))
            if (x.get("mistake") or "none") != "none":
                p["m:" + x["mistake"]] += 1
    mistakes = Counter(x.get("mistake") for x in by_call.values() if (x.get("mistake") or "none") != "none")
    return {"connected": len(rows), "with_transcript": len(tx), "read": sum(1 for r in tx if r["call_id"] in by_call),
            "real": sum(r["secs"] >= 120 for r in rows), "outcomes": dict(out), "reasons": reasons.most_common(),
            "mistakes": mistakes.most_common(), "per_caller": {k: dict(v) for k, v in per.items()}}


CSS = """@page { size: A4 landscape; margin: 10mm; } body { font-family: Arial, sans-serif; font-size: 9.5px; color: #1d2b28; }
h1 { font-size: 20px; color: #1f4e46; } h2 { font-size: 14px; color: #1f4e46; margin-top: 16px; } h3 { font-size: 11px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; } th, td { border: 1px solid #c9d6d2; padding: 3px 5px; vertical-align: top; text-align: left; }
th { background: #e8f0ed; } .box { background: #f2f7f5; border-left: 4px solid #1f4e46; padding: 6px 10px; margin: 8px 0; }
.small { font-size: 8px; color: #555; } .bad { color: #b42318; } li { margin: 2px 0; }"""


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "—"


def html_doc(R: dict, N: dict, days: list[dict], leads: list[dict], meta: dict) -> str:
    e = html.escape
    H = [f"<!doctype html><html><head><meta charset='utf-8'><title>Weekly review</title><style>{CSS}</style></head><body>",
         f"<h1>{e(meta['team'])}: weekly review, {e(meta['period'])}</h1>",
         f"<p class='small'>Built {e(meta['built'])} IST. Every connected call of the week from LeadSquared, matched to its call recording "
         "transcript and read by Claude; enrollments counted as first-time Course Enrolled or a lead saying on a call that they paid. "
         "Reasons are judgements from the calls, one per lead, not prices: rupee figures wait for payment data.</p>"]
    if N.get("headline"):
        H.append(f"<div class='box'>{N['headline']}</div>")
    H.append(f"<p><b>Calls read:</b> {R['connected']:,} connected calls; {R['with_transcript']:,} had a recording transcript and "
             f"{R['read']:,} of those were read ({_pct(R['read'], R['with_transcript'])}). "
             f"{R['connected'] - R['with_transcript']:,} had no transcript (mostly calls of a few seconds); their leads were still judged from "
             "the rest of the week.</p>")
    if N.get("fixes"):
        H.append("<h2>Fix next week</h2><ol>" + "".join(f"<li>{x}</li>" for x in N["fixes"]) + "</ol>")
    H.append("<h2>The week day by day</h2><table><tr><th>Day</th><th>Callers dialling</th><th>Dials</th><th>Real conversations</th>"
             "<th>Calls from leads not returned</th><th>Dials on the sheet (median caller)</th><th>Leads tried once and dropped</th></tr>")
    for d in days:
        H.append(f"<tr><td>{e(d['label'])}</td><td>{d['callers']}</td><td>{d['dials']:,}</td><td>{d['real']}</td><td>{d['unret']}</td>"
                 f"<td>{d['sheet']}</td><td>{d['once']}</td></tr>")
    H.append("</table>")
    total = sum(R["outcomes"].values())
    H.append(f"<h2>Every lead with a connected call ({total:,})</h2><table><tr>"
             + "".join(f"<th>{e(o.capitalize())}</th>" for o in OUTCOMES) + "</tr><tr>"
             + "".join(f"<td>{R['outcomes'].get(o, 0):,}</td>" for o in OUTCOMES) + "</tr></table>"
             "<p class='small'>Enrolled = first-time enrollment this week; paying = payment started (link, seat block, EMI) and not finished; "
             "open = still a prospect; lost = no longer a prospect this month; not a prospect = existing student, support, job seeker, wrong number.</p>")
    if N.get("why"):
        H.append("<h2>Why enrollments did not come through</h2>" + "".join(f"<p>{x}</p>" for x in N["why"]))
    if R["reasons"]:
        H.append("<h3>Main reason per open or lost lead (one per lead)</h3><table><tr><th>Reason</th><th>Leads</th><th>Examples</th></tr>")
        for k, n in R["reasons"]:
            ex = [l for l in leads if l.get("primary_reason") == k and l.get("outcome") in ("open", "lost")][:6]
            H.append(f"<tr><td>{e(REASONS.get(k, k))}</td><td>{n}</td><td>" + "; ".join(
                f"{e(x.get('name') or '?')} ({e((x.get('owner') or '?').split()[0])}): {e((x.get('what_would_have_closed') or '')[:120])}" for x in ex) + "</td></tr>")
        H.append("</table>")
    if N.get("calls"):
        H.append("<h2>What happens on the calls</h2>" + "".join(f"<p>{x}</p>" for x in N["calls"]))
    H.append("<h3>By caller (calls of 2 min+)</h3><table><tr><th>Caller</th><th>Connected</th><th>Read</th><th>2 min+</th><th>Fee and ask</th>"
             "<th>Link sent on call</th><th>Dated next step</th><th>Most common slip</th></tr>")
    for name, p in sorted(R["per_caller"].items(), key=lambda kv: -kv[1].get("connected", 0)):
        m = sorted(((k[2:], v) for k, v in p.items() if k.startswith("m:")), key=lambda kv: -kv[1])
        H.append(f"<tr><td>{e(name)}</td><td>{p.get('connected', 0)}</td><td>{p.get('read', 0)}</td><td>{p.get('real', 0)}</td>"
                 f"<td>{_pct(p.get('ask', 0), p.get('real_read', 0))}</td><td>{_pct(p.get('link', 0), p.get('real_read', 0))}</td>"
                 f"<td>{_pct(p.get('next', 0), p.get('real_read', 0))}</td><td>{e(m[0][0]) + f' ({m[0][1]})' if m else '—'}</td></tr>")
    H.append("</table>")
    if R["mistakes"]:
        H.append("<h3>Slips across all read calls</h3><p>" + "; ".join(f"{e(k)} {v}" for k, v in R["mistakes"]) + ".</p>")
    best = [l for l in leads if l.get("best_call_why")][:12]
    if best:
        H.append("<h2>Calls to copy</h2><ul>" + "".join(
            f"<li><b>{e(l.get('name') or '?')}</b> ({e(l.get('owner') or '?')}): {e(l['best_call_why'])}</li>" for l in best) + "</ul>")
    for title_, k in (("Callers", "callers"), ("For the team leader", "leader")):
        if N.get(k):
            H.append(f"<h2>{title_}</h2>" + "".join(f"<p>{x}</p>" for x in N[k]))
    H.append("</body></html>")
    return "\n".join(H)


def workbook(leads: list[dict], rows: list[dict], reads: dict[str, dict], out: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(["Lead", "Caller", "Outcome", "Main reason", "What would have closed it", "Week in short", "Next step"])
    for l in sorted(leads, key=lambda x: (OUTCOMES.index(x.get("outcome")) if x.get("outcome") in OUTCOMES else 2, x.get("owner") or "")):
        ws.append([l.get("name"), l.get("owner"), l.get("outcome"), REASONS.get(l.get("primary_reason") or "", l.get("primary_reason") or ""),
                   l.get("what_would_have_closed"), l.get("week_summary"), l.get("next_step")])
    by_call = {c.get("call_id"): c for r in reads.values() for c in r.get("calls") or []}
    cs = wb.create_sheet("Calls")
    cs.append(["When (IST)", "Caller", "Lead", "Direction", "Seconds", "Transcript", "What happened", "Fee and ask", "Link sent",
               "Next step fixed", "Objection", "Slip", "Could have done better"])
    for r in rows:
        x = by_call.get(r["call_id"]) or {}
        cs.append([r["t"], r["caller"], r["lead"], r["direction"], r["secs"], r["transcript"], x.get("what_happened"), x.get("ask_made"),
                   x.get("link_sent"), x.get("next_step_fixed"), x.get("objection"), x.get("mistake"), x.get("better")])
    for sheet, widths in ((ws, (22, 16, 12, 26, 50, 60, 40)), (cs, (15, 16, 20, 9, 8, 14, 60, 9, 9, 9, 30, 18, 50))):
        for c in sheet[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E46")
        for i, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = w
        for row in sheet.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "B2"
    wb.save(out)
    return out
