"""Per-lead text dossiers for Claude to read: lead fields, yesterday's plan entry, calls, Zip notes, transcripts."""

from __future__ import annotations

import html
import json
import os
import re

from analytics.daily_plan.common import Snap, ist

ZIP_NOTES = "237"


def _strip(h: str | None) -> str:
    h = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", h or "")
    h = html.unescape(re.sub(r"<[^>]+>", "", h))
    return re.sub(r"\s*\n\s*", "\n", h).strip()


def _say(s: str | None) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def select(snap: Snap, day: str, prev_plan: dict) -> set[str]:
    """Leads with an answered team call on ``day`` plus the previous plan's P/M/A/B and priority leads."""
    sel = {c["lead_id"] for c in snap.day(day) if c["status"] == "Answered" and c.get("user_id") in snap.users}
    sel |= {lid for lid, e in prev_plan.items() if e["tier"] in "PMAB" or e.get("group") is not None or e.get("verify")}
    return {lid for lid in sel if lid in snap.leads}


def build(snap: Snap, day: str, lead_ids: set[str], prev_plan: dict, tx: dict, out_dir: str) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    zl = {}
    for z in snap.raw.get("zip_activities", []):
        if str(z.get("ActivityEvent")) == ZIP_NOTES and z.get("CreatedOn"):
            zl.setdefault(z["RelatedProspectId"], []).append(z)
    index = []
    for lid in sorted(lead_ids):
        l = snap.leads[lid]
        cs = snap.by_lead.get(lid, [])
        ph = next((snap.phone(c) for c in cs if snap.phone(c)), None) or (re.sub(r"\D", "", l.get("Phone") or "")[-10:] or None)
        created = ist(l.get("CreatedOn"))
        L = [f"LEAD {snap.lead_name(lid)} | phone +91-{ph} | lead_id {lid}",
             f"Owner: {l.get('OwnerIdName')} | Stage now: {l.get('ProspectStage')} | LSQ course: {l.get('mx_Enquired_Course')} | "
             f"Source: {l.get('Source')} | Created: {created.strftime('%d %b %Y') if created else '?'}",
             f"Next follow-up field: {l.get('mx_Next_follow_up_date') or l.get('mx_Follow_up_date_and_time') or '-'} (UTC) | "
             f"Zip intent field: {l.get('mx_Zip_Intent_Type') or '-'}"]
        p = prev_plan.get(lid)
        L.append(f"YESTERDAY'S PLAN: tier {p['tier']} on {p['owner']}'s sheet, chance {p.get('chance')}%"
                 + (" (marked Course Enrolled, payment to verify)" if p.get("verify") else "")
                 + (f", on the team leader's priority list (check by {p.get('check_by')})" if p.get("group") is not None else "")
                 if p else "YESTERDAY'S PLAN: not on the sheet")
        L.append("CALLS (IST):")
        for c in cs:
            note = (c.get("call_notes") or "").strip()
            L.append(f"  {c['t'].strftime('%a %d %b %H:%M')} {c['direction'][:3]} {c['status']} {c['duration']}s by "
                     f"{c.get('caller') or snap.users.get(c.get('user_id'), '?')} line {c.get('display_number') or '-'}"
                     + (f" | note: {note[:200]}" if note not in ("", "Missed", "Answered", "NotAnswered") else ""))
        zs = sorted(zl.get(lid, []), key=lambda z: z["CreatedOn"])
        if zs:
            L.append("ZIPTEAMS NOTES:")
            for z in zs[-4:]:
                L.append(f"  {ist(z['CreatedOn']).strftime('%a %d %b %H:%M')} intent {z.get('mx_Custom_1')} | {_strip(z.get('ActivityEvent_Note'))[:900]}")
        raw = (tx.get("91" + ph) or {}) if ph else {}
        items = sorted(((k, x) for k in ("sales_call", "support_calls") for x in raw.get(k) or []),
                       key=lambda kx: kx[1].get("start_time") or "", reverse=True)
        if items:
            L.append(f"TRANSCRIPTS (API times may be 5h30 off; newest first; {len(items)} on record):")
            for i, (kind, x) in enumerate(items[:6]):
                text = _say((x.get("transcript") or {}).get("text") or "").strip()
                lim = 9000 if i < 3 else 1500
                L.append(f"  -- {kind} start {x.get('start_time')} dur {x.get('call_duration')}s agent {x.get('agent_name')}")
                L.append("  " + ((text[:lim] + (" [...]" if len(text) > lim else "")) if text else "(no transcript text)"))
        else:
            L.append("TRANSCRIPTS: none fetched for this number")
        body = "\n".join(L)
        open(os.path.join(out_dir, f"{lid}.txt"), "w").write(body)
        index.append({"lead_id": lid, "owner": l.get("OwnerIdName"), "has_tx": bool(items), "size": len(body)})
    json.dump(index, open(os.path.join(out_dir, "_index.json"), "w"), indent=1)
    return index


def batches(index: list[dict], n: int = 8) -> list[list[dict]]:
    """Split dossiers into ``n`` batches of similar size, keeping each owner's leads together where possible."""
    index = sorted(index, key=lambda i: (i["owner"] or "", i["lead_id"]))
    per = sum(i["size"] for i in index) / max(n, 1)
    out, s = [[]], 0
    for i in index:
        if s > per * len(out) and len(out) < n:
            out.append([])
        out[-1].append(i)
        s += i["size"]
    return [b for b in out if b]
