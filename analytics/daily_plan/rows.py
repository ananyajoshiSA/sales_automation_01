"""Today's lead rows per caller, from Claude's re-reads, yesterday's plan, fresh LeadSquared data and today so far."""

from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict
from datetime import timedelta

from analytics.daily_plan.common import CLOSED, DEAD, REAL_SECS, Snap, ist, p10

TIERS = "MABFRC"
PAID = {"paid_new", "enrolled_no_proof"}  # enrolled_no_proof: older reads, before stage = enrolled
EXCLUDE = {"already_student", "dnc", "support", "21day", "irrelevant", "not_interested"}
CARRY_CHANCE = {"A": 20, "B": 8, "F": 4, "R": 3, "C": 2, "M": 3}


def load_reads(directory: str) -> dict:
    reads = {}
    for f in sorted(glob.glob(os.path.join(directory, "out_*.jsonl"))):
        for ln in open(f):
            ln = ln.strip()
            if ln:
                try:
                    r = json.loads(ln)
                    reads[r["lead_id"]] = r
                except (ValueError, KeyError):
                    pass
    return reads


def _today_summary(cs) -> str:
    if not cs:
        return ""
    o = [c for c in cs if c["direction"] == "outbound"]
    a = [c for c in cs if c["status"] == "Answered"]
    bits = [f"{len(o)} dial(s)"]
    bits.append("answered " + ", ".join(f"{c['t']:%H:%M} ({c['duration'] // 60}m{c['duration'] % 60:02d}s)" for c in a) if a else "not answered")
    if any(c["direction"] == "inbound" for c in cs):
        bits.append(f"lead rang {sum(c['direction'] == 'inbound' for c in cs)}x")
    return "Today so far: " + ", ".join(bits)


def build_rows(snap: Snap, today: str, reads: dict, prev_plan: dict, enrolled: list[dict]) -> dict:
    now = snap.fetched
    first_enr = {l["ProspectID"]: l.get("first_enrolled") for l in enrolled}
    team = set(snap.callers)
    rows, dropped, enrolled_leads = {}, [], []
    for lid, r in reads.items():
        l = snap.leads.get(lid, {})
        owner = l.get("OwnerIdName") or r.get("owner")
        stage = l.get("ProspectStage") or ""
        st = r.get("status", "")
        # Enrolled = Course Enrolled in LeadSquared, or the lead said on a call that they paid. Never back on a sheet.
        if (stage in CLOSED and st != "bootcamp") or st in PAID:
            enrolled_leads.append({**r, "owner": owner, "stage": stage, "first_enrolled": first_enr.get(lid)})
            continue
        if st in EXCLUDE or r.get("tier") == "D":
            dropped.append({**r, "owner": owner, "stage": stage})
            continue
        if owner not in team:
            continue
        rows[lid] = {**r, "owner": owner, "lead_id": lid, "stage": stage, "source": "re-read"}
        if st == "bootcamp":  # Rs 10 bootcamp registration: still a prospect for the course, whatever the stage says
            rows[lid]["why"] = "Paid only the Rs 10 bootcamp registration (not a course enrollment). " + (r.get("why") or "")
    yesterday = max((c["t"] for c in snap.calls if c["t"].strftime("%Y-%m-%d") < today), default=now).strftime("%Y-%m-%d")
    for lid, p in prev_plan.items():  # yesterday's sheet leads that were not re-read (mostly F/R/C)
        if lid in rows or lid in reads or lid not in snap.leads:
            continue
        l = snap.leads[lid]
        owner = l.get("OwnerIdName")
        stage = l.get("ProspectStage") or ""
        if owner not in team or stage in CLOSED + DEAD:
            continue
        if p["tier"] == "P" or p.get("verify"):
            continue
        tier = p["tier"] if p["tier"] in TIERS else "C"
        dials = [c for c in snap.by_lead.get(lid, []) if c["direction"] == "outbound" and c["t"].strftime("%Y-%m-%d") == yesterday]
        ch = min(int(p.get("chance") or CARRY_CHANCE[tier]), CARRY_CHANCE[tier])
        note = f"Yesterday: {len(dials)} dial(s), not answered. " if dials else ("Not dialled yesterday. " if tier in "AB" else "")
        if dials:
            ch = max(1, ch - (3 if tier in "AB" else 1))
        rows[lid] = {"owner": owner, "lead_id": lid, "name": snap.lead_name(lid), "phone": p10(l.get("Phone")),
                     "course": l.get("mx_Enquired_Course") or "", "tier": tier, "chance": ch, "stage": stage, "source": "carried",
                     "why": note + f"Carried from yesterday's {tier} list; stage {stage or '(blank)'}"
                            + (f", Zip intent {l['mx_Zip_Intent_Type']}" if l.get("mx_Zip_Intent_Type") else "") + ".",
                     "opening_line": "", "exact_ask": "", "best_time": "", "who_decides": "", "how_pay": "", "real_blocker": "",
                     "objection": "", "callback_requested": "", "whatsapp_only": False}
    for lid, l in snap.leads.items():  # new leads with no real conversation yet
        owner = l.get("OwnerIdName")
        created = ist(l.get("CreatedOn"))
        if owner not in team or lid in rows or lid in reads or not created or created < now - timedelta(days=4):
            continue
        if (l.get("ProspectStage") or "") in CLOSED + DEAD:
            continue
        cs = snap.by_lead.get(lid, [])
        if any(c["status"] == "Answered" and c["duration"] >= REAL_SECS for c in cs):
            continue
        rows[lid] = {"owner": owner, "lead_id": lid, "name": snap.lead_name(lid), "phone": p10(l.get("Phone")),
                     "course": l.get("mx_Enquired_Course") or "", "tier": "F", "chance": 4, "stage": l.get("ProspectStage") or "",
                     "source": "new", "why": f"New {l.get('Source') or ''} lead from {created:%a %d %b %H:%M}; "
                                             f"{sum(c['direction'] == 'outbound' for c in cs)} dial(s) so far, no real conversation yet.",
                     "opening_line": "", "best_time": "", "who_decides": "", "how_pay": "", "real_blocker": "", "objection": "",
                     "exact_ask": "Discovery: background, goal, who decides, how they'd pay; pitch ONE program; fix a dated callback or send the fee sheet on WhatsApp.",
                     "callback_requested": "", "whatsapp_only": False}
    for lid, r in rows.items():
        cs = snap.by_lead.get(lid, [])
        l = snap.leads.get(lid, {})
        r["phone"] = p10(r.get("phone")) or p10(l.get("Phone"))
        r["today"] = _today_summary([c for c in cs if c["t"].strftime("%Y-%m-%d") == today])
        missed = [c for c in cs if c["direction"] == "inbound" and c["status"] != "Answered" and c["t"] >= now - timedelta(days=3)
                  and not any(d["t"] > c["t"] and (d["direction"] == "outbound" or d["status"] == "Answered") for d in cs)]
        r["missed"] = len(missed)
        r["missed_last"] = missed[-1]["t"].strftime("%a %H:%M") if missed else ""
        if missed and r["tier"] != "A":
            r["tier_orig"], r["tier"] = r["tier"], "M"
        real = [c for c in cs if c["status"] == "Answered" and c["duration"] >= REAL_SECS]
        r["last_conv"] = real[-1]["t"].strftime("%d %b %H:%M") if real else ""
        if not r.get("best_time"):
            hrs = [c["t"].hour for c in cs if c["status"] == "Answered" and c["duration"] >= 30]
            h = Counter(hrs).most_common(1)[0][0] if hrs else None
            r["best_time"] = f"{h:02d}:00–{h + 1:02d}:00 (picked up then)" if h is not None else "anytime"
        if not (r.get("opening_line") or "").strip():
            fn = r["name"].split()[0].title() if r.get("name") and not r["name"].startswith(("+", "Unnamed", "(")) else ""
            r["opening_line"] = (f"“Hi{(' ' + fn) if fn else ''}, this is {r['owner'].split()[0]} from LawSikho — "
                                 + {"F": "you enquired about our course. Is this a good time for two minutes?”",
                                    "M": "you tried to reach us, sorry we missed you. How can I help?”"}.get(
                                     r["tier"], "we spoke recently about the program. Is now a good time for two minutes?”"))
        r["chance"] = int(r.get("chance") or 0)
    by_owner = defaultdict(list)
    for r in rows.values():
        if r["tier"] in TIERS:
            by_owner[r["owner"]].append(r)
    for o in by_owner:
        by_owner[o].sort(key=lambda r: (TIERS.index(r["tier"]), -r["chance"]))
    return {"built_at": now.strftime("%Y-%m-%d %H:%M"), "today": today, "by_owner": dict(by_owner), "paid": enrolled_leads,
            "dropped": dropped, "reads": len(reads)}
