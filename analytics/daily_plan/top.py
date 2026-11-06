"""The team's top leads by conversion potential, for the team leader: one list, no caller sheets.

Ranks the morning's lead reads (highest estimated 3-day chance first, then tier, then the latest real conversation),
leaves out leads that are enrolled, collection, do-not-call or a clear no, and reads each listed lead again from
LeadSquared for its current owner, stage and last-called time. Writes a workbook and a PDF; both hold lead details,
so they go under exports/.

    python -m analytics.daily_plan --team "<group>" --leader "<name>" top <date> [--n 50] [--mail]
"""

from __future__ import annotations

import html

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from analytics.daily_plan.common import Snap, ist, name_of, p10

DROP = {"paid_new", "bootcamp", "already_student", "collection", "dnc", "21day", "irrelevant", "not_interested", "support"}
CLOSED_STAGES = ("Course Enrolled", "Not Interested", "Invalid", "Invalid lead", "Irrelevant lead", "Duplicate",
                 "Collections done", "Booking fees received")
TIER = {t: i for i, t in enumerate("APBMRFC")}
REAL_SECS = 120
SPARE = 10  # extra candidates read, in case LeadSquared now shows some as enrolled or closed


def _last_real(snap: Snap, lid: str):
    cs = [c for c in snap.by_lead.get(lid, []) if c["status"] == "Answered" and c["duration"] >= REAL_SECS]
    return cs[-1]["t"] if cs else None


def rank(snap: Snap, reads: dict, n: int = 50) -> list[dict]:
    """Open leads, best first: chance, then tier, then the most recent real conversation. Returns n + SPARE reads."""
    cands = [r for r in reads.values() if r.get("status") not in DROP and r.get("tier") != "D"
             and (snap.leads.get(r["lead_id"]) or {}).get("ProspectStage") not in CLOSED_STAGES]

    def key(r):
        t = _last_real(snap, r["lead_id"])
        return -int(r.get("chance") or 0), TIER.get(r.get("tier"), 9), -(t.timestamp() if t else 0)

    return sorted(cands, key=key)[: n + SPARE]


def map_current(snap: Snap, ranked: list[dict], get_lead, n: int = 50) -> list[dict]:
    """Each ranked lead with its owner, stage and last-called time as LeadSquared has them now; closed ones are skipped."""
    rows = []
    for r in ranked:
        l = get_lead(r["lead_id"]) or {}
        stage = l.get("ProspectStage") or ""
        if stage in CLOSED_STAGES:
            continue
        lc, lr = ist(l.get("mx_Last_Called_Date_DT")), _last_real(snap, r["lead_id"])
        rows.append({"name": name_of(l) or r.get("name") or "Unnamed lead", "phone": p10(l.get("Phone")) or "",
                     "owner": l.get("OwnerIdName") or "", "stage": stage,
                     "last_called": lc.strftime("%a %d %b %H:%M") if lc else "never",
                     "last_real": lr.strftime("%a %d %b") if lr else "none on record",
                     "tier": r.get("tier") or "", "chance": int(r.get("chance") or 0),
                     "course": r.get("course") or l.get("mx_Enquired_Course") or "", "why": r.get("why") or "",
                     "next": r.get("next_step") or "", "callback": r.get("callback_requested") or "", "lead_id": r["lead_id"]})
        if len(rows) == n:
            break
    return rows


COLS = [("Rank", 6), ("Lead", 22), ("Phone", 13), ("Current owner (LSQ)", 20), ("Current stage (LSQ)", 18),
        ("Last called (LSQ, IST)", 16), ("Last 2+ min conversation", 15), ("Tier", 6), ("Chance (3 days, estimate)", 10),
        ("Course", 24), ("Why this lead", 55), ("Next step", 45), ("Callback asked for", 22), ("Lead ID", 38)]
FILL = {"A": "DCEFE6", "B": "E8F0FA"}


def write_xlsx(rows: list[dict], team: str, pulled: str, path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = f"Top {len(rows)}"
    ws["A1"] = f"{team}: top {len(rows)} leads by conversion potential"
    ws["A1"].font = Font(name="Arial", bold=True, size=14)
    ws["A2"] = (f"Sales leads only (collection leads left out). Owner, stage and last-called time read from LeadSquared {pulled} IST. "
                "Chance is an estimate from reading each lead's calls and transcripts, not a target.")
    ws["A2"].font = Font(name="Arial", italic=True, size=9, color="555555")
    thin = Side(style="thin", color="D0D5D2")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, (h, w) in enumerate(COLS, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = border
        ws.column_dimensions[get_column_letter(i)].width = w
    for k, r in enumerate(rows, 1):
        vals = [k, r["name"], r["phone"], r["owner"], r["stage"], r["last_called"], r["last_real"], r["tier"], r["chance"] / 100,
                r["course"], r["why"], r["next"], r["callback"], r["lead_id"]]
        for i, v in enumerate(vals, 1):
            c = ws.cell(row=4 + k, column=i, value=v)
            c.font = Font(name="Arial", size=9, bold=i in (1, 2))
            c.alignment = Alignment(wrap_text=i in (2, 10, 11, 12, 13), vertical="top")
            c.fill = PatternFill("solid", fgColor=FILL.get(r["tier"], "F2F2F2"))
            c.border = border
        ws.cell(row=4 + k, column=9).number_format = "0%"
        ws.row_dimensions[4 + k].height = 60
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(COLS))}{4 + len(rows)}"
    wb.save(path)


def html_doc(rows: list[dict], team: str, leader: str, day_label: str, pulled: str) -> str:
    e = lambda s: html.escape(str(s or ""))
    body = "".join(
        f"<tr class='t{e(r['tier'])}'><td>{k}</td><td><b>{e(r['name'])}</b><div class='s'>{e(r['course'])}</div></td>"
        f"<td>{e(r['owner'])}</td><td>{e(r['stage'])}</td><td>{e(r['last_called'])}</td><td>{e(r['last_real'])}</td>"
        f"<td>{e(r['tier'])} · {r['chance']}%</td><td>{e(r['why'])}<div class='s'><b>Next:</b> {e(r['next'])}</div></td></tr>"
        for k, r in enumerate(rows, 1))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(team)} top {len(rows)} – {e(day_label)}</title><style>
@page {{ size: A4 landscape; margin: 12mm; }} body {{ font-family: Arial, sans-serif; font-size: 8.5pt; color: #1b1b1b; }}
h1 {{ font-size: 16pt; margin: 0 0 4pt; color: #1f4e46; }} p.n {{ color: #555; margin: 0 0 8pt; }}
table {{ border-collapse: collapse; width: 100%; }} th {{ background: #1f4e46; color: #fff; text-align: left; padding: 4pt; }}
td {{ border-bottom: 1px solid #d0d5d2; padding: 4pt; vertical-align: top; }} tr {{ page-break-inside: avoid; }}
tr.tA td {{ background: #dcefe6; }} tr.tB td {{ background: #eef3fa; }} .s {{ color: #555; font-size: 7.5pt; margin-top: 2pt; }}
</style></head><body><h1>{e(team)}: top {len(rows)} leads by conversion potential, {e(day_label)}</h1>
<p class="n">For {e(leader)}. Highest estimated chance of enrolling in the next 3 days first. Sales leads only. Owner, stage and
last-called time read from LeadSquared {e(pulled)} IST; "last 2+ min talk" is from the call log. Chances are estimates, not targets.</p>
<table><tr><th>#</th><th>Lead</th><th>Current owner</th><th>Current stage</th><th>Last called</th><th>Last 2+ min talk</th>
<th>Tier · chance</th><th>Why this lead / next step</th></tr>{body}</table></body></html>"""
