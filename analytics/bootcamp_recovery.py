"""Bootcamp leads that can still be won: under-worked open leads and leads dropped too early.

Covers every lead from one month's bootcamps that the collection teams work: leads who paid a booking fee
("Booking fees received"), and the chat and failed-payment leads from day 2 and 3 of the bootcamp (the
"Enrollment Opted" field ends in "- Chat" or "- Failed"). Effort is counted from the booking fee, or for
chat and failed-payment leads from their hand-over to a caller during the bootcamp.

An open lead is under-worked when it was never dialled, was dialled on fewer than 3 days without being
reached, has had no dial for 3+ days, went 5+ days after its last real conversation without a dial, missed a
logged callback, or called in and was not called back that day. A dropped lead (Not Interested, Invalid,
May buy later) may have been dropped too early when it had no 2-minute conversation, was dialled on fewer
than 3 days, came back after the drop (called in, filled a form, started a payment), or was dropped for a
reason that is temporary or unrecorded. Explicit refusals (joined elsewhere, wrong number, do-not-call)
are left out. CRM notes understate wrong drops: in September 2026, 34 of 38 drops with no rule flag turned out,
on reading the calls, to be temporary or still recoverable, so "C" means "check the calls", not "justified".

    python -m analytics.bootcamp_recovery data/bc_sep exports/bootcamp_recovery_sep
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

from analytics.bootcamp_collections import (BOOKED, BOT, COLLECTED, DEFERRED, LOST, SYSTEM_COMMENT, _call, _data,
                                            _fields, _note_text, callbacks, course_family, loss_reason, parse_tag)
from analytics.definitions import BUYING_SIGNALS, REAL_CONVERSATION_SECS, signals
from integrations.timeutil import IST, ist_day, utc

MIN_DIAL_DAYS = 3
STALE_DAYS = 3
NO_FOLLOW_UP_DAYS = 5
CLOSURE_STAGES = frozenset({"Booking fees received", "Booking Fees Received", "Follow Up For Closure", "Loan pending",
                            "Counselled lead", "Discovery Call Done", "Roadmap\xa0Done", "Roadmap Done", "Opportunity Created"})
HARD_NO = re.compile(r"joined (another|other)|already (joined|enrolled) (in |with )?(another|other)|wrong number|not the (right|same) person"
                     r"|do not call|don'?t call|stop calling|invalid number|duplicate|fraud|scam|abus", re.I)
REFUSAL = re.compile(r"(don'?t|do not|doesn'?t|does not) want (any|it|this|that|to (do|join|continue|proceed|go ahead|pursue|enrol))"
                     r"|bluntly|not looking for any|knows? (all|everything)"
                     r"|want(s|ed)? to drop|drop(ping)? out|by mistake|not interested in (it|this|that|the (course|program))", re.I)
LATER = re.compile(r"later|next (week|month|year|batch)|after|month|jan|feb|march|fund|financ|money|salary|emi|loan|instal"
                   r"|job|exam|busy|time|health|pregnan|emergenc|travel|think", re.I)
WELL_WORKED_DAYS = 7
TEMPORARY = {"Will decide later", "Deferred to a later batch", "Stopped responding", "No reason recorded",
             "Loan, EMI or payment process failed", "No time / personal or health reasons"}
# Things only the lead does; the caller's own disposition form (97) and outbound messages don't count.
COMEBACK_EVENTS = {21: "called in", 208: "payment started", 214: "payment attempt failed", 213: "payment made",
                   222: "SMS reply", 234: "chat", 23: "enquired again", 229: "enquired again", 3: "website form",
                   21502: "landing-page form"}


def opted_kind(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    if v.endswith("chat"):
        return "Chat"
    if v.endswith("failed"):
        return "Failed payment"
    return None


def opted_course(value: str | None) -> tuple[str, str]:
    """("Independent Director", "2026-09-12") from "Bootcamp | Independent Director - September 12, ... 2026 - Chat"."""
    v = value or ""
    m = re.search(r"\|\s*(.+?)\s+-\s+([A-Z][a-z]+) (\d{1,2}),.*?(20\d\d)", v)
    if not m:
        return "", ""
    course = re.sub(r"\s*\((re|RE|Re)\)", "", m.group(1)).replace(" | ", " ")
    try:
        day = datetime.strptime(f"{m.group(3)} {m.group(2)} {m.group(4)}", "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        day = ""
    return course_family(course), day


def lead_view(lead: dict, acts: list[dict], team: str, team_callers: frozenset, now: datetime) -> dict | None:
    """One September lead: how hard it was worked since it reached the collection team, and what is left to win."""
    acts = sorted((a for a in acts if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
    stages = [(utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventCode") == 3002]
    booked = next((t for t, d in stages if (d.get("CurrentStage") or "").strip().lower() == BOOKED), None)
    kind = opted_kind(lead.get("mx_Enrollment_Opted"))
    if kind:
        course, camp = opted_course(lead.get("mx_Enrollment_Opted"))
    else:
        course, camp = parse_tag(lead.get("mx_Bootcamp_collections"))
        course = course_family(course)
    if booked and not kind:
        kind, start = "Booked", booked
    elif kind and camp:
        camp_start = datetime.fromisoformat(camp).replace(tzinfo=IST)
        handed = [utc(a["CreatedOn"]) for a in acts if a.get("EventCode") == 3001 and utc(a["CreatedOn"]) >= camp_start
                  and (_data(a).get("CurrentOwner") or "").strip() in team_callers]
        start = handed[0] if handed else camp_start + timedelta(days=1, hours=12)
    else:
        return None
    calls = [c for c in (_call(a) for a in acts if a.get("EventCode") in (21, 22))
             if c["t"] and c["t"] >= start and not BOT.search(c["by"])]
    dials = [c for c in calls if c["out"]]
    answered = [c for c in calls if c["answered"]]
    real = [c for c in answered if c["dur"] >= REAL_CONVERSATION_SECS]
    after = [(t, d) for t, d in stages if t >= start]
    stage = lead.get("ProspectStage") or ""
    won_stages = COLLECTED | ({"Booking fees received", "Booking Fees Received"} if kind != "Booked" else set())
    won = next((t for t, d in after if d.get("CurrentStage") in won_stages), None)
    if won:
        outcome, end = "won", won
    elif stage in LOST or stage in DEFERRED:
        outcome = "dropped"
        end = next((t for t, d in reversed(after) if d.get("CurrentStage") == stage), None) or start
    else:
        outcome, end = "open", None
    forms = [a for a in acts if a.get("EventCode") == 103]
    notes = [_note_text(a) for a in forms if utc(a["CreatedOn"]) >= start]
    zips = [a for a in acts if a.get("EventCode") == 237 and utc(a["CreatedOn"]) >= start]
    intents = [(_fields(a).get("mx_Custom_1") or "").upper() for a in zips]
    buying = signals(" ".join(notes), BUYING_SIGNALS)
    cbs = callbacks(forms, [c["t"] for c in dials], start, end, now)
    missed_in = [c for c in calls if not c["out"] and not c["answered"]]
    unreturned = len({ist_day(m["t"]) for m in missed_in if ist_day(m["t"]) < ist_day(now)  # today isn't over
                      and not any(d["t"] > m["t"] and ist_day(d["t"]) == ist_day(m["t"]) for d in dials)})
    last_dial = dials[-1]["t"] if dials else None
    last_real = real[-1]["t"] if real else None
    dial_days = len({ist_day(c["t"]) for c in dials})
    due = utc(lead.get("mx_Next_follow_up_date"))
    v = {
        "lead_id": lead["ProspectID"], "kind": kind, "team": team, "owner": lead.get("OwnerIdName") or "",
        "course": course, "bootcamp_day": camp, "stage": stage, "outcome": outcome,
        "start_ist": start.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
        "dials": len(dials), "dial_days": dial_days, "connected": bool(answered), "real_convs": len(real),
        "talk_min": round(sum(c["dur"] for c in answered) / 60, 1),
        "days_since_last_dial": round((now - last_dial).total_seconds() / 86400, 1) if last_dial else None,
        "days_since_real_conv": round((now - last_real).total_seconds() / 86400, 1) if last_real else None,
        "callbacks_missed": sum(1 for c in cbs if not c["kept"]), "inbound_unreturned_days": unreturned,
        "zip_intent": next((i for i in reversed(intents) if i in ("HIGH", "MODERATE", "LOW", "NEUTRAL")), ""),
        "buying_words": ", ".join(buying), "last_note": (notes[-1] if notes else "")[:300],
        "gaps": [], "signals": [], "priority": "",
    }
    sig = v["signals"]
    if kind == "Failed payment":
        sig.append("tried to pay at the bootcamp")
    if real:
        sig.append(f"{len(real)} real conversation(s)")
    if stage in CLOSURE_STAGES:
        sig.append(f"stage: {stage.replace(chr(160), ' ')}")
    if buying:
        sig.append("buying words in notes: " + ", ".join(buying))
    if v["zip_intent"] in ("HIGH", "MODERATE"):
        sig.append(f"Zipteams intent {v['zip_intent'].lower()}")
    gaps = v["gaps"]
    if outcome == "open":
        if not dials:
            gaps.append("never called")
        elif not answered and dial_days < MIN_DIAL_DAYS:
            gaps.append(f"not reached, called on only {dial_days} day(s)")
        if last_dial and now - last_dial > timedelta(days=STALE_DAYS):
            gaps.append(f"no call for {v['days_since_last_dial']:g} days")
        if last_real and now - last_real > timedelta(days=NO_FOLLOW_UP_DAYS) and not any(d["t"] > last_real for d in dials):
            gaps.append("no call since the last conversation")
        if v["callbacks_missed"]:
            gaps.append(f"{v['callbacks_missed']} logged callback(s) missed")
        if due and due < now - timedelta(hours=2) and not any(d["t"] >= due - timedelta(hours=2) for d in dials):
            gaps.append("follow-up date passed, not called")
        if missed_in and not any(d["t"] > missed_in[-1]["t"] for d in dials) and ist_day(missed_in[-1]["t"]) < ist_day(now):
            gaps.append("lead's last call-in never returned")
        strong = kind == "Failed payment" or real or stage in CLOSURE_STAGES or buying or v["zip_intent"] in ("HIGH", "MODERATE")
        v["priority"] = ("A: under-worked, strong signal" if gaps and strong else "B: under-worked" if gaps
                         else "C: being worked")
    elif outcome == "dropped":
        comment = next((d.get("Comment") or "" for t, d in reversed(after) if d.get("CurrentStage") == stage), "")
        text = " | ".join(x for x in ([""] if SYSTEM_COMMENT.match(comment) else [comment]) + notes[-2:] if x)
        v["reason"] = "Deferred to a later batch" if stage in DEFERRED and loss_reason(text) == "No reason recorded" \
            else loss_reason(text)
        v["drop_text"] = text[:300]
        v["days_to_drop"] = round((end - start).total_seconds() / 86400, 1)
        before = [c for c in dials if c["t"] <= end]
        if not [c for c in real if c["t"] <= end]:
            gaps.append("dropped without a 2-minute conversation")
        if len({ist_day(c["t"]) for c in before}) < MIN_DIAL_DAYS:
            gaps.append(f"dropped after calls on {len({ist_day(c['t']) for c in before})} day(s)")
        back = sorted({COMEBACK_EVENTS[a["EventCode"]] for a in acts
                       if a.get("EventCode") in COMEBACK_EVENTS and utc(a["CreatedOn"]) > end + timedelta(days=1)})
        if back:
            gaps.append("came back after the drop: " + ", ".join(back))
        if v["reason"] in TEMPORARY:
            gaps.append(f"reason is temporary or unrecorded: {v['reason']}")
        hard = bool(HARD_NO.search(text)) or (bool(REFUSAL.search(text)) and not LATER.search(text)) or stage in {"Invalid", "Invalid lead", "Duplicate", "Invalid Number"} \
            or v["reason"] == "Joined elsewhere / already a student"
        thin = any(g.startswith(("dropped without", "dropped after")) for g in gaps)
        came_back = any(g.startswith("came back") for g in gaps)
        # A past conversation is not a reason to reopen a drop: almost every booked lead had one.
        keen = kind == "Failed payment" or bool(buying) or v["zip_intent"] in ("HIGH", "MODERATE")
        v["priority"] = ("Explicit refusal: leave" if hard else
                         "A: dropped too early, strong signal" if came_back or (thin and keen) else
                         "B: dropped too early" if thin or (v["reason"] in TEMPORARY and keen
                                                            and dial_days < WELL_WORKED_DAYS) else
                         "C: no rule flag, check the calls")
    else:
        v["priority"] = "won"
    return v


def build(leads: list[dict], hist: dict[str, list], team_of_owner: dict[str, str], team_callers: frozenset,
          now: datetime) -> tuple[list[dict], Counter]:
    views, skipped = [], Counter()
    for l in leads:
        acts = hist.get(l["ProspectID"])
        if acts is None:
            skipped["no history"] += 1
            continue
        v = lead_view(l, acts, team_of_owner.get(l.get("OwnerId") or "", "Outside the two teams"), team_callers, now)
        if v is None:
            skipped["not booked, chat or failed payment"] += 1
            continue
        views.append(v)
    return views, skipped


def main(data_dir: str, out_dir: str) -> None:
    meta = json.load(open(os.path.join(data_dir, "meta.json")))
    tagged = json.load(open(os.path.join(data_dir, "tagged_leads.json")))
    opted = json.load(open(os.path.join(data_dir, "opted_leads.json")))
    by_id = {l["ProspectID"]: l for l in tagged}
    for l in opted["leads"]:
        by_id[l["ProspectID"]] = {**by_id.get(l["ProspectID"], {}), **{k: v for k, v in l.items() if k not in ("type", "team")}}
    hist = {}
    for line in open(os.path.join(data_dir, "hist.jsonl")):
        d = json.loads(line)
        if d.get("activities") is not None and not d.get("error"):
            hist[d["lead_id"]] = d["activities"]
    now = utc(max(meta["fetched_at_utc"], opted["fetched_at_utc"]))
    views, skipped = build(list(by_id.values()), hist, meta["team_of_owner"], frozenset(meta["team_callers"]), now)
    os.makedirs(out_dir, exist_ok=True)
    rows = [{**v, "gaps": "; ".join(v["gaps"]), "signals": "; ".join(v["signals"])} for v in views]
    with open(os.path.join(out_dir, "leads.csv"), "w", newline="", encoding="utf-8") as fh:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    summary = {
        "as_of_ist": now.astimezone(IST).strftime("%Y-%m-%d %H:%M"), "leads": len(views), "skipped": dict(skipped),
        "by_kind_outcome": Counter(f"{v['kind']} | {v['outcome']}" for v in views),
        "priority": Counter(f"{v['kind']} | {v['team']} | {v['priority']}" for v in views if v["outcome"] != "won"),
    }
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1, default=str)
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:3])
