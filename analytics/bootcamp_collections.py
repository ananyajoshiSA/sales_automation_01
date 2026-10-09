"""How the bootcamp collection teams work the leads who paid a booking fee.

A collection lead paid a booking fee during a bootcamp (stage change to "Booking fees received") and
carries a "Bootcamp collections" tag; the collection teams then collect the balance. For each lead this
traces booking → first dial → first answered call → scheduled callbacks kept or missed → outcome
(balance collected, lost, deferred or still open), and sums it per team, bootcamp, caller and loss reason.

Scheduled callbacks are the follow-up time saved on each disposition form (event 103, ``mx_Custom_1``,
UTC). A callback is kept when the lead is dialled within 2 hours either side of it (GOAL.md L6); one
replaced by a newer form more than 2 hours before it fell due is not counted.

Inputs come from ``scripts/fetch_bootcamp_collections.py``.

    python -m analytics.bootcamp_collections data/bc exports/bootcamp_collections
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.definitions import ENROLLED, REAL_CONVERSATION_SECS, speed_bucket
from integrations.leadsquared import parse_activity_note
from integrations.timeutil import IST, ist_day, utc

TEAMS = ("Elite Changemakers", "Team Puja Malik (DSV+Women AI)")
OUTSIDE = "Outside the two teams"
BOOKED = "booking fees received"
COLLECTED = frozenset({"Collections done", ENROLLED})
LOST = frozenset({"Not Interested", "Invalid", "Invalid lead", "Irrelevant lead", "Invalid Number", "Duplicate"})
DEFERRED = frozenset({"May buy later"})
CALLBACK_WINDOW = timedelta(hours=2)
NOT_A_SCHEDULE = timedelta(minutes=15)   # a callback set for "now" is a form default, not a plan
STALLED_DAYS = 7
BOT = re.compile(r"\b(system|bot|welcome|reminder|webinar|ivr)\b", re.I)
SYSTEM_COMMENT = re.compile(r'^\s*\{"ActionType"')

# First match wins: the reason a lead gave comes before how they left (refund, going silent).
LOSS_REASONS = (
    ("Loan, EMI or payment process failed", r"loan|\bemi\b|nbfc|cibil|credit card|paper finance|financ(e|ing) (team|option|partner)"
                                        r"|shopse|kyc|aadha+r|co.?applicant|documents?\b|csc\b"),
    ("Cannot afford the balance", r"afford|financial|money|budget|salary|\bfunds?\b|expensive|costly|no job|jobless|earner|earning"
                                  r"|pay manually"),
    ("Doubts about course value or trust", r"review|negative|doubt|trust|fake|scam|fraud|value|expect|relevan|not (useful|satisf)"
                                           r"|useless|content|quality|fulfil|requirement|placement|anchor"),
    ("No time / personal or health reasons", r"(no|not have|don.?t have|enough|sufficient|lack of) (\w+ )?time|busy|occupied"
                                             r"|exam|health|hospital|operation|surgery|medical|personal|travel"
                                             r"|marriage|wedding|pregnan|baby|husband|family|\bjob\b|shift"),
    ("Joined elsewhere / already a student", r"other (course|institute|certification)|another|elsewhere|already (enrolled"
                                             r"|doing|joined|a student|involved)|existing student|diff(erent)? course"),
    ("Asked for refund (no reason given)", r"refund|cancel|withdraw|quit|drop(ped)? out|not able to continue|discontinue"
                                           r"|(do not|don'?t) want to continue"),
    ("Will decide later", r"later|next (year|month)|after \d|months?\b|2027"),
    ("Stopped responding", r"not (responding|reachable|picking|revert)|no (response|revert)|dnp|disconnect|switch(ed)? off"
                           r"|ringing|hung up|blocked|unreachable"),
)


def parse_tag(tag: str | None) -> tuple[str, str]:
    """("Independent Director", "18 Jul 2026") from "Independent Director Bootcamp Collection - 18th July'26"."""
    tag = (tag or "").strip()
    course = re.split(r"\s+bootcamp\b|\s+collections?\b", tag, maxsplit=1, flags=re.I)[0].strip(" -") or tag
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]{3})[A-Za-z]*\s*'\s*(\d\d)", tag)
    if not m:
        return course, ""
    try:
        d = datetime.strptime(f"{m.group(1)} {m.group(2).title()} 20{m.group(3)}", "%d %b %Y")
    except ValueError:
        return course, ""
    return course, d.strftime("%Y-%m-%d")


def course_family(course: str) -> str:
    """Bootcamp names drift ("Independent Directors", "Remote Work for Women AI"); group the obvious variants."""
    c = course.lower()
    for key, name in (("independent director", "Independent Director"), ("us accounting", "US Accounting"),
                      ("patent", "Patent Law & Analysis"), ("women", "Remote Work for Women AI"),
                      ("academic", "Global Academic Writers"), ("ip law", "IP Law Career"),
                      ("corporate finance", "Corporate Finance"), ("strategic hr", "Strategic HR"),
                      ("senior ai", "Senior AI"), ("us corporate", "US Corporate Law"), ("us technology", "US Technology Law"),
                      ("corporate litigation", "Corporate Litigation"), ("data protection", "Data Protection"),
                      ("content writ", "Content Writing"), ("ai for business", "AI for Business Growth")):
        if key in c:
            return name
    return course


def loss_reason(text: str) -> str:
    t = (text or "").lower()
    for label, rx in LOSS_REASONS:
        if re.search(rx, t):
            return label
    return "No reason recorded"


def _data(a: dict) -> dict:
    return {d.get("Key"): d.get("Value") for d in a.get("Data") or []}


def _fields(a: dict) -> dict:
    return a.get("ActivityFields") or {}


def _call(a: dict) -> dict:
    note = parse_activity_note(_fields(a).get("ActivityEvent_Note"))
    d = _data(a)
    try:
        dur = int(float(note.get("Duration") or d.get("Duration") or 0))
    except ValueError:
        dur = 0
    status = note.get("Status") or _fields(a).get("Status") or ""
    return {"t": utc(a.get("CreatedOn")), "out": a.get("EventCode") == 22, "status": status,
            "answered": status == "Answered", "dur": dur,
            "by": (note.get("Caller") or d.get("Caller") or note.get("Reciever") or "").strip()}


def _note_text(a: dict) -> str:
    return (_fields(a).get("ActivityEvent_Note") or _data(a).get("NotableEventDescription") or "").strip()


def callbacks(forms: list[dict], dials: list[datetime], start: datetime, end: datetime | None, now: datetime) -> list[dict]:
    """Each callback scheduled after ``start`` that fell due before the lead closed (``end``) and 2 h before now.

    ``forms`` are disposition forms (event 103) sorted by time; ``dials`` are outbound call times.
    """
    out = []
    times = [utc(f.get("CreatedOn")) for f in forms]
    for i, f in enumerate(forms):
        set_at, due = times[i], utc(_fields(f).get("mx_Custom_1"))
        if not set_at or not due or set_at < start or due - set_at < NOT_A_SCHEDULE:
            continue
        if due > now - CALLBACK_WINDOW or (end and due > end):
            continue
        if any(t and set_at < t < due - CALLBACK_WINDOW for t in times[i + 1:]):
            continue  # replaced by a newer plan before it fell due
        kept = any(abs((d - due).total_seconds()) <= CALLBACK_WINDOW.total_seconds() for d in dials)
        same_day = any(ist_day(d) == ist_day(due) for d in dials)
        out.append({"due": due, "kept": kept, "same_day": same_day})
    return out


def lead_view(lead: dict, acts: list[dict], team: str, now: datetime, team_callers: frozenset = frozenset()) -> dict | None:
    """One collection lead traced from booking to outcome; None if it never reached "Booking fees received".

    ``team_callers`` are the collection teams' callers by name: their first dial and the lead's hand-over to
    them are measured apart from dials by anyone (often the bootcamp seller who took the booking fee).
    """
    acts = sorted((a for a in acts if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
    stages = [(utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventCode") == 3002]
    booked = next((t for t, d in stages if (d.get("CurrentStage") or "").strip().lower() == BOOKED), None)
    if not booked:
        return None
    course, camp_day = parse_tag(lead.get("mx_Bootcamp_collections"))
    calls = [c for c in (_call(a) for a in acts if a.get("EventCode") in (21, 22))
             if c["t"] and c["t"] >= booked and not BOT.search(c["by"])]
    dials = [c for c in calls if c["out"]]
    answered = [c for c in calls if c["answered"]]
    real = [c for c in answered if c["dur"] >= REAL_CONVERSATION_SECS]
    first_dial = dials[0]["t"] if dials else None
    team_dial = next((c["t"] for c in dials if c["by"] in team_callers), None)
    handed = [t for t, d in ((utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventCode") == 3001)
              if (d.get("CurrentOwner") or "").strip() in team_callers]
    handover = booked if any(t <= booked for t in handed) else next((t for t in handed if t > booked), None)
    first_ans = answered[0]["t"] if answered else None
    after = [(t, d) for t, d in stages if t >= booked]
    collected = next((t for t, d in after if d.get("CurrentStage") in COLLECTED), None)
    enrolled_before = any(d.get("CurrentStage") == ENROLLED for t, d in stages if t < booked)
    stage = lead.get("ProspectStage") or ""
    if collected:
        outcome, end = "collected", collected
    elif stage in LOST:
        outcome = "lost"
        end = next((t for t, d in reversed(after) if d.get("CurrentStage") == stage), None) or now
    elif stage in DEFERRED:
        outcome = "deferred"
        end = next((t for t, d in reversed(after) if d.get("CurrentStage") == stage), None) or now
    else:
        outcome, end = "open", None
    forms = [a for a in acts if a.get("EventCode") == 103]
    cbs = callbacks(forms, [c["t"] for c in dials], booked, end, now)
    missed_in = [c for c in calls if not c["out"] and not c["answered"]]
    unreturned = len({ist_day(m["t"]) for m in missed_in
                      if not any(d["t"] > m["t"] and ist_day(d["t"]) == ist_day(m["t"]) for d in dials)})
    due = utc(lead.get("mx_Next_follow_up_date"))
    overdue = bool(outcome == "open" and due and due < now - CALLBACK_WINDOW
                   and not any(d["t"] >= due - CALLBACK_WINDOW for d in dials))
    reason = text = ""
    if outcome in ("lost", "deferred"):
        comment = next((d.get("Comment") or "" for t, d in reversed(after) if d.get("CurrentStage") == stage), "")
        notes = [_note_text(a) for a in forms if utc(a["CreatedOn"]) >= booked and utc(a["CreatedOn"]) <= end]
        text = " | ".join(x for x in ([""] if SYSTEM_COMMENT.match(comment) else [comment]) + notes[-2:] if x)
        reason = "Deferred to a later batch" if outcome == "deferred" and loss_reason(text) == "No reason recorded" \
            else loss_reason(text)
    last_dial = dials[-1]["t"] if dials else None
    mins = (first_dial - booked).total_seconds() / 60 if first_dial else None
    team_mins = (team_dial - booked).total_seconds() / 60 if team_dial else None
    return {
        "lead_id": lead["ProspectID"], "team": team, "owner": lead.get("OwnerIdName") or "",
        "tag": lead.get("mx_Bootcamp_collections") or "", "course": course_family(course), "bootcamp_day": camp_day,
        "booked_ist": booked.astimezone(IST).strftime("%Y-%m-%d %H:%M"), "booked": booked,
        "stage": stage, "outcome": outcome, "enrolled_before_booking": enrolled_before,
        "mins_to_first_dial": round(mins) if mins is not None else None, "speed": speed_bucket(mins),
        "dialled_24h": mins is not None and mins <= 1440,
        "connected_24h": bool(first_ans and first_ans - booked <= timedelta(hours=24)),
        "connected_72h": bool(first_ans and first_ans - booked <= timedelta(hours=72)),
        "team_mins_to_first_dial": round(team_mins) if team_mins is not None else None,
        "team_dialled_24h": team_mins is not None and team_mins <= 1440,
        "hrs_to_handover": round((handover - booked).total_seconds() / 3600, 1) if handover else None,
        "dials": len(dials), "answered_dials": sum(1 for c in dials if c["answered"]),
        "connected": bool(answered), "real_convs": len(real), "talk_min": round(sum(c["dur"] for c in answered) / 60, 1),
        "callbacks_set": sum(1 for f in forms if (t := utc(f.get("CreatedOn"))) and t >= booked
                             and (d := utc(_fields(f).get("mx_Custom_1"))) and d - t >= NOT_A_SCHEDULE),
        "days_to_loss": round((end - booked).total_seconds() / 86400, 1) if outcome in ("lost", "deferred") else None,
        "callbacks_due": len(cbs), "callbacks_missed": sum(1 for c in cbs if not c["kept"]),
        "callbacks_missed_whole_day": sum(1 for c in cbs if not c["same_day"]),
        "inbound_missed": len(missed_in), "inbound_answered": sum(1 for c in calls if not c["out"] and c["answered"]),
        "inbound_unreturned_days": unreturned, "followup_overdue": overdue,
        "days_since_last_dial": round((now - last_dial).total_seconds() / 86400, 1) if last_dial else None,
        "days_since_booking": round((now - booked).total_seconds() / 86400, 1),
        "stalled": outcome == "open" and (not last_dial or now - last_dial > timedelta(days=STALLED_DAYS)),
        "lost_without_real_conv": outcome in ("lost", "deferred") and not real,
        "reason": reason, "reason_text": text[:400],
    }


def pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def median(xs: list) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return float(xs[m]) if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def summarise(views: list[dict]) -> dict:
    """The headline measures for any group of lead views."""
    n = len(views)
    out = Counter(v["outcome"] for v in views)
    closed = out["collected"] + out["lost"] + out["deferred"]
    dials = sum(v["dials"] for v in views)
    due = sum(v["callbacks_due"] for v in views)
    return {
        "leads": n,
        "dialled_24h_%": pct(sum(v["dialled_24h"] for v in views), n),
        "never_dialled": sum(1 for v in views if v["mins_to_first_dial"] is None),
        "median_hrs_to_first_dial": round(m / 60, 1) if (m := median([v["mins_to_first_dial"] for v in views])) is not None else None,
        "connected_24h_%": pct(sum(v["connected_24h"] for v in views), n),
        "connected_72h_%": pct(sum(v["connected_72h"] for v in views), n),
        "ever_connected_%": pct(sum(v["connected"] for v in views), n),
        "dial_answer_%": pct(sum(v["answered_dials"] for v in views), dials),
        "dials_per_lead": round(dials / n, 1) if n else None,
        "real_conv_%": pct(sum(1 for v in views if v["real_convs"]), n),
        "leads_with_callback_set_%": pct(sum(1 for v in views if v["callbacks_set"]), n),
        "median_days_to_loss": median([v["days_to_loss"] for v in views]),
        "lost_no_reason": sum(1 for v in views if v["reason"] == "No reason recorded"),
        "callbacks_due": due, "callbacks_missed": sum(v["callbacks_missed"] for v in views),
        "callbacks_missed_%": pct(sum(v["callbacks_missed"] for v in views), due),
        "callbacks_missed_whole_day_%": pct(sum(v["callbacks_missed_whole_day"] for v in views), due),
        "team_dialled_24h_%": pct(sum(v["team_dialled_24h"] for v in views), n),
        "median_hrs_to_handover": median([v["hrs_to_handover"] for v in views]),
        "handed_over_after_24h": sum(1 for v in views if v["hrs_to_handover"] is not None and v["hrs_to_handover"] > 24),
        "inbound_answer_%": pct(sum(v["inbound_answered"] for v in views),
                                sum(v["inbound_answered"] + v["inbound_missed"] for v in views)),
        "leads_with_unreturned_missed_call": sum(1 for v in views if v["inbound_unreturned_days"]),
        "open_followup_overdue": sum(v["followup_overdue"] for v in views),
        "collected": out["collected"], "lost": out["lost"], "deferred": out["deferred"], "open": out["open"],
        "collected_%_of_closed": pct(out["collected"], closed),
        "lost_%": pct(out["lost"] + out["deferred"], n),
        "open_stalled": sum(v["stalled"] for v in views),
        "lost_without_real_conv": sum(v["lost_without_real_conv"] for v in views),
    }


def group(views: list[dict], *keys: str, min_leads: int = 1) -> list[dict]:
    g: dict[tuple, list] = defaultdict(list)
    for v in views:
        g[tuple(v[k] for k in keys)].append(v)
    rows = [{**dict(zip(keys, k)), **summarise(vs)} for k, vs in g.items() if len(vs) >= min_leads]
    return sorted(rows, key=lambda r: tuple(str(r[k]) for k in keys))


def build(leads: list[dict], hist: dict[str, list], team_of_owner: dict[str, str], now: datetime,
          team_callers: frozenset = frozenset()) -> tuple[list[dict], dict]:
    """Lead views for every tagged lead with history, plus a reconciliation of what was left out."""
    views, skipped = [], Counter()
    for l in leads:
        acts = hist.get(l["ProspectID"])
        if acts is None:
            skipped["no history fetched"] += 1
            continue
        v = lead_view(l, acts, team_of_owner.get(l.get("OwnerId") or "", OUTSIDE), now, team_callers)
        if v is None:
            skipped["never reached Booking fees received"] += 1
            continue
        views.append(v)
    return views, {"tagged": len(leads), "analysed": len(views), **skipped}


def _write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(data_dir: str, out_dir: str) -> None:
    meta = json.load(open(os.path.join(data_dir, "meta.json")))
    leads = json.load(open(os.path.join(data_dir, "tagged_leads.json")))
    team_of_owner = meta["team_of_owner"]
    hist = {}
    for line in open(os.path.join(data_dir, "hist.jsonl")):
        d = json.loads(line)
        if d.get("activities") is not None and not d.get("error"):
            hist[d["lead_id"]] = d["activities"]
    now = utc(meta["fetched_at_utc"])
    views, recon = build(leads, hist, team_of_owner, now, frozenset(meta.get("team_callers") or ()))
    os.makedirs(out_dir, exist_ok=True)
    report = {
        "fetched_at_ist": now.astimezone(IST).strftime("%Y-%m-%d %H:%M"), "bootcamps": meta["bootcamps"],
        "reconciliation": recon,
        "by_team": group(views, "team"),
        "by_team_course": group(views, "team", "course"),
        "by_team_bootcamp": group(views, "team", "course", "bootcamp_day"),
        "by_caller": group(views, "team", "owner"),
        "speed": {t: Counter(v["speed"] for v in views if v["team"] == t) for t in {v["team"] for v in views}},
        "loss_reasons": {t: Counter(v["reason"] for v in views if v["team"] == t and v["reason"])
                         for t in {v["team"] for v in views}},
    }
    json.dump(report, open(os.path.join(out_dir, "report.json"), "w"), indent=1, default=str)
    _write_csv(os.path.join(out_dir, "leads.csv"), [{k: v for k, v in x.items() if k != "booked"} for x in views])
    for name in ("by_team_course", "by_team_bootcamp", "by_caller"):
        _write_csv(os.path.join(out_dir, f"{name}.csv"), report[name])
    print(json.dumps({k: report[k] for k in ("fetched_at_ist", "reconciliation", "by_team")}, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:3])
