"""Rank a team's open leads by likelihood of enrolling soon.

Zipteams intent is one input, never the answer: it only sees a single call and
misses what happens around it (repeated no-answers, the lead calling us back,
a stage the agent set afterwards, an overdue follow-up). Every lead gets a
timeline built from LeadSquared calls + Zip notes + lead fields, a rule-based
pre-score, and explicit flags wherever Zip and LeadSquared disagree. The
shortlist is then meant for a human/LLM read of the summaries.

    python -m analytics.lead_priority data/snap1.json [data/snap2.json ...] --days 15 --out exports/priority
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from analytics.team_report import IST, utc, zip_score

CLOSED_STAGES = {"Course Enrolled", "Irrelevant lead", "Invalid lead", "Duplicate"}
NEGATIVE_STAGES = {"Not Interested"}
INTENT_RANK = {"HIGH": 3, "MODERATE": 2, "NEUTRAL": 1, "LOW": 0, "NOT_QUALIFIED": -1}

# phrases in Zip summaries/reasons that signal a buying decision is close
BUYING_SIGNALS = {
    "fee": r"\bfee|\bprice|cost|how much",
    "emi_or_loan": r"\bemi\b|instal|loan|financ",
    "payment": r"payment link|pay(ment)? (today|tomorrow|now)|will pay|make the payment|transfer",
    "decision_maker": r"parent|father|mother|husband|wife|family|discuss with",
    "start_date": r"batch|start date|when (does|will) (it|the course) start|next cohort|join(ing)? date",
    "enroll_intent": r"\benrol|\bregister|sign up|join the (course|program)",
    "refund_or_guarantee": r"refund|guarantee|placement|job assistance",
}
NEGATIVE_SIGNALS = {
    "joined_elsewhere": r"already (joined|enrolled|purchased|took)|another (course|institute)|other institute",
    "not_interested": r"not interested|no interest|do not call|don't call|stop calling",
    "no_budget": r"can'?t afford|no budget|too expensive|not able to pay",
    "wrong_person": r"wrong number|not (the )?(right|same) person|didn'?t (fill|enquire|register)",
}


def strip_html(h: str | None) -> str:
    t = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", h or "")
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def merge_snapshots(snaps: list[dict]) -> dict:
    """Combine snapshots covering different date ranges. Latest snapshot wins for leads/users."""
    snaps = sorted(snaps, key=lambda s: s["fetched_at"])
    out = {"users": snaps[-1]["users"], "fetched_at": snaps[-1]["fetched_at"]}
    leads = {}
    for s in snaps:
        leads.update({l["ProspectID"]: l for l in s["leads"]})
    out["leads"] = list(leads.values())
    for key, id_key in (("calls", "activity_id"), ("zip_activities", "ProspectActivityId")):
        seen = {}
        for s in snaps:
            for a in s.get(key, []):
                seen[a.get(id_key) or id(a)] = a
        out[key] = list(seen.values())
    return out


def build_timelines(snap: dict, days: int) -> list[dict]:
    users = {u["ID"]: f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip() for u in snap["users"]}
    now = datetime.fromisoformat(snap["fetched_at"])
    since = now - timedelta(days=days)
    leads = {l["ProspectID"]: l for l in snap["leads"]}

    calls = defaultdict(list)
    for c in snap["calls"]:
        t = utc(c.get("start_utc"))
        if t and t >= since and c.get("lead_id") in leads:
            calls[c["lead_id"]].append({**c, "t": t, "answered": c.get("status") == "Answered"})
    zips = defaultdict(list)
    for a in snap.get("zip_activities", []):
        t = utc(a.get("CreatedOn"))
        if str(a.get("ActivityEvent")) == "237" and t and t >= since and a.get("RelatedProspectId") in leads:
            zips[a["RelatedProspectId"]].append({
                "t": t,
                "intent": (a.get("mx_Custom_1") or "").upper(),
                "reason": (a.get("mx_Custom_2") or "").strip(),
                "summary": strip_html(a.get("ActivityEvent_Note")),
                "pitch": zip_score(a.get("mx_Custom_4")),
                "probing": zip_score(a.get("mx_Custom_5")),
                "objection_handling": zip_score(a.get("mx_Custom_6")),
            })

    out = []
    for lid in set(calls) | set(zips):
        lead = leads[lid]
        cs = sorted(calls[lid], key=lambda c: c["t"])
        zs = sorted(zips[lid], key=lambda z: z["t"])
        conv = [c for c in cs if c["answered"] and c["duration"] >= 60]
        if not conv and not zs:
            continue  # never had a real conversation in the window: not a closing candidate
        out.append(_features(lead, cs, zs, conv, users, now))
    return out


def _features(lead, cs, zs, conv, users, now):
    last_conv = conv[-1]["t"] if conv else (zs[-1]["t"] if zs else None)
    after = [c for c in cs if last_conv and c["t"] > last_conv]
    dials_after = [c for c in after if c["direction"] == "outbound"]
    unanswered_after = [c for c in dials_after if not c["answered"]]
    inbound = [c for c in cs if c["direction"] == "inbound"]
    inbound_after = [c for c in inbound if last_conv and c["t"] > last_conv]
    rated = [z for z in zs if z["intent"] in INTENT_RANK]
    last_intent = rated[-1]["intent"] if rated else None
    best_intent = max((z["intent"] for z in rated), key=INTENT_RANK.get, default=None)
    text = " ".join((z["reason"] + " " + z["summary"]) for z in zs).lower()
    buying = sorted(k for k, rx in BUYING_SIGNALS.items() if re.search(rx, text))
    negative = sorted(k for k, rx in NEGATIVE_SIGNALS.items() if re.search(rx, text))
    stage = lead.get("ProspectStage") or ""
    fu = utc(lead.get("mx_Next_follow_up_date")) or utc(lead.get("mx_Follow_up_date_and_time"))
    longest = max((c["duration"] for c in cs if c["answered"]), default=0)
    total_talk = sum(c["duration"] for c in cs if c["answered"])
    days_since = round((now - last_conv).total_seconds() / 86400, 1) if last_conv else None

    # ---- Zip vs LeadSquared cross-checks
    flags = []
    if best_intent in ("HIGH", "MODERATE") and len(unanswered_after) >= 4:
        flags.append(f"Zip {best_intent} but {len(unanswered_after)} unanswered dials since last talk — cooling")
    if best_intent in ("HIGH", "MODERATE") and stage in NEGATIVE_STAGES:
        flags.append(f"Zip {best_intent} but agent set stage '{stage}' — verify")
    if best_intent in ("LOW", "NEUTRAL", None) and (inbound_after or longest >= 900 or stage == "Follow Up For Closure"):
        why = []
        if inbound_after:
            why.append(f"lead called us {len(inbound_after)}x after last talk")
        if longest >= 900:
            why.append(f"{longest // 60}-min call")
        if stage == "Follow Up For Closure":
            why.append("stage Follow Up For Closure")
        flags.append(f"Zip {best_intent or 'n/a'} understates interest: " + ", ".join(why))
    if last_intent and best_intent and INTENT_RANK[last_intent] < INTENT_RANK[best_intent]:
        flags.append(f"intent dropped {best_intent} → {last_intent} on latest call")
    missed_inbound_open = [c for c in inbound if not c["answered"]
                           and not any(d["direction"] == "outbound" and d["t"] > c["t"] for d in cs)]
    if missed_inbound_open:
        flags.append(f"{len(missed_inbound_open)} missed call(s) from lead never returned")
    if fu and fu < now and not any(c["direction"] == "outbound" and c["t"] >= fu - timedelta(hours=2) for c in cs):
        flags.append("follow-up overdue and not called")

    # ---- pre-score (used only to shortlist; the final pick reads the summaries)
    score = 0.0
    score += {3: 30, 2: 20, 1: 8, 0: 0, -1: -20}.get(INTENT_RANK.get(best_intent, 0), 0)
    score += 6 * len(buying)
    score -= 15 * len(negative)
    score += min(longest / 60, 20)                 # up to 20 for a long conversation
    score += 12 * min(len(inbound), 2)             # lead reaching out is the strongest signal
    score += {"Follow Up For Closure": 20, "Call Back Later": 6}.get(stage, 0)
    score -= 25 if stage in NEGATIVE_STAGES else 0
    score -= 4 * max(len(unanswered_after) - 2, 0)  # going dark
    score -= 2 * max((days_since or 0) - 3, 0)      # stale
    score -= 60 if stage in CLOSED_STAGES else 0

    owner_id = lead.get("OwnerId")
    return {
        "lead_id": lead["ProspectID"],
        "name": f"{lead.get('FirstName') or ''} {lead.get('LastName') or ''}".strip(),
        "phone": lead.get("Phone"),
        "owner": lead.get("OwnerIdName") or users.get(owner_id, ""),
        "stage": stage,
        "source": lead.get("Source"),
        "course": lead.get("mx_Enquired_Course"),
        "pre_score": round(score, 1),
        "best_zip_intent": best_intent,
        "last_zip_intent": last_intent,
        "buying_signals": buying,
        "negative_signals": negative,
        "crosscheck_flags": flags,
        "dials": sum(1 for c in cs if c["direction"] == "outbound"),
        "connected_calls": sum(1 for c in cs if c["answered"]),
        "longest_call_min": round(longest / 60, 1),
        "total_talk_min": round(total_talk / 60, 1),
        "inbound_calls": len(inbound),
        "inbound_missed_unreturned": len(missed_inbound_open),
        "last_conversation_ist": last_conv.astimezone(IST).strftime("%Y-%m-%d %H:%M") if last_conv else None,
        "days_since_last_conversation": days_since,
        "dials_since_last_conversation": len(dials_after),
        "unanswered_since_last_conversation": len(unanswered_after),
        "next_follow_up_ist": fu.astimezone(IST).strftime("%Y-%m-%d %H:%M") if fu else None,
        "zip_notes": [
            {"at_ist": z["t"].astimezone(IST).strftime("%m-%d %H:%M"), "intent": z["intent"],
             "reason": z["reason"][:400], "summary": z["summary"][:1200]}
            for z in zs[-3:]
        ],
        "call_log": [
            f"{c['t'].astimezone(IST):%m-%d %H:%M} {'IN ' if c['direction'] == 'inbound' else 'OUT'} "
            f"{c['status']}{' ' + str(c['duration']) + 's' if c['answered'] else ''} ({c.get('caller') or '?'})"
            for c in cs[-15:]
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", nargs="+")
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--per-owner", type=int, default=20, help="shortlist size per owner")
    ap.add_argument("--out", default="exports/priority")
    a = ap.parse_args()
    snap = merge_snapshots([json.load(open(p)) for p in a.snapshots])
    rows = build_timelines(snap, a.days)
    team = {f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip() for u in snap["users"]}
    rows = [r for r in rows if r["owner"] in team and r["stage"] not in CLOSED_STAGES]
    os.makedirs(a.out, exist_ok=True)
    short = []
    for owner in sorted({r["owner"] for r in rows}):
        mine = sorted((r for r in rows if r["owner"] == owner), key=lambda r: -r["pre_score"])
        short.extend(mine[: a.per_owner])
        print(f"{owner}: {len(mine)} candidates")
    json.dump(rows, open(os.path.join(a.out, "all_candidates.json"), "w"), indent=1)
    json.dump(short, open(os.path.join(a.out, "shortlist.json"), "w"), indent=1)
    print(f"{len(rows)} candidates, {len(short)} shortlisted -> {a.out}")


if __name__ == "__main__":
    main()
