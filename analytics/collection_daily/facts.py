"""What LeadSquared alone says about each November bootcamp collection lead, as of a cut-off: the facts behind the
morning list and the evening update. Judgement (status, objection, payment mode and timeline) comes from the
graders' file, not from here.

Rules agreed with the user (17 Nov 2026):
- **Allocation** is when the lead reaches the caller's bucket: the last LeadSquared owner change to that caller (so the
  clock restarts if a lead is ever moved), or the cycle's assignment when there is none.
- **48 hours**: a lead allocated on day D needs one substantial conversation by the end of the calling day (20:00 IST)
  on D+2. A pick-up of any length is tracked too.
- **Substantial conversation**: an answered call of 2 minutes or more.
- **Enrolled** also when a Note on the Notes tab says the loan is done (the user's rule, 19 Nov 2026), from when it was
  written; stage comments and activity notes are read by ``analytics.collection_cycle``.
- **Follow-up**: a call-back time saved in a disposition form within 30 minutes after an answered call (a "call back
  now" default, due within 15 minutes of the form, is not one); graders add times agreed in transcripts and Notes.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from analytics.bootcamp_collections import _data, parse_tag
from analytics.collection_cycle import cycle_leads, says_loan_done, state
from analytics.collection_scorecard import hierarchy, load
from integrations.timeutil import IST, utc

SIDES = ("Mayur", "Puja / Anmol")
REAL_SECS = 120
DAY_END = 20  # end of the calling day, IST
FORM_AFTER_CALL = timedelta(minutes=30)
NOT_A_SCHEDULE = timedelta(minutes=15)
KEPT_WITHIN = timedelta(hours=2)


def weekends(run_dir: str) -> list[tuple[str, str]]:
    """(data dir, bootcamp day) for each weekend folder of a run (w1, w2, ...), from each folder's meta."""
    out = []
    for name in sorted(os.listdir(run_dir)):
        d = os.path.join(run_dir, name)
        if name.startswith("w") and os.path.exists(os.path.join(d, "meta.json")):
            meta = json.load(open(os.path.join(d, "meta.json")))
            days = {parse_tag(t)[1] for t in meta.get("bootcamps") or []}  # a weekend folder is fetched for one bootcamp day
            if len(days) == 1:
                out.append((d, days.pop()))
    return out


def read_at(run_dir: str) -> datetime:
    """When the run's LeadSquared data was read: the earliest weekend fetch, so nothing later is taken as missing."""
    times = [utc(json.load(open(os.path.join(d, "meta.json"))).get("fetched_at_utc")) for d, _ in weekends(run_dir)]
    return min(t for t in times if t)


def deadline_of(allocated: datetime) -> datetime:
    """20:00 IST two calendar days after the allocation day."""
    d = allocated.astimezone(IST).date() + timedelta(days=2)
    return datetime(d.year, d.month, d.day, DAY_END, tzinfo=IST)


def allocation(lead: dict, acts: list[dict], start: datetime) -> datetime:
    to_caller = [utc(a["CreatedOn"]) for a in acts if a.get("EventCode") == 3001 and utc(a.get("CreatedOn"))
                 and utc(a["CreatedOn"]) >= start and (_data(a).get("CurrentOwner") or "").strip() == lead["caller"]]
    return max(to_caller) if to_caller else lead["assigned"]


def follow_ups(lead: dict, cut: datetime) -> list[dict]:
    """Call-backs saved after an answered call, each with whether it was kept (a dial within 2 h of its time)."""
    answered = [c["t"] for c in lead["calls"] if c["answered"] and c["t"] <= cut]
    dials = [c["t"] for c in lead["calls"] if c["out"] and c["t"] <= cut]
    out = []
    for f in lead["forms"]:
        if not f["due"] or f["t"] > cut or f["due"] - f["t"] <= NOT_A_SCHEDULE:
            continue
        if not any(timedelta(0) <= f["t"] - t <= FORM_AFTER_CALL for t in answered):
            continue
        kept = any(timedelta(minutes=-15) <= t - f["due"] <= KEPT_WITHIN for t in dials)
        out.append({"set_at": f["t"], "due": f["due"], "kept": kept if f["due"] + KEPT_WITHIN <= cut else None})
    return out


def lead_facts(lead: dict, acts: list[dict], start: datetime, cut: datetime) -> dict:
    s = state(lead, cut)
    alloc = allocation(lead, acts, start)
    since = [c for c in lead["calls"] if c["t"] <= cut and (c["t"] >= alloc or c.get("team"))]
    picked = [c["t"] for c in since if c["answered"]]
    real = [c["t"] for c in since if c["answered"] and c["dur"] >= REAL_SECS]
    dl = deadline_of(alloc)
    dials = [c for c in lead["calls"] if c["out"] and c["t"] <= cut]
    last_dial = dials[-1]["t"] if dials else None
    missed_in = [c["t"] for c in lead["calls"] if not c["out"] and not c["answered"] and c["t"] <= cut and (not last_dial or c["t"] > last_dial)]
    fu = follow_ups(lead, cut)
    return {"allocated": alloc, "deadline": dl, "first_pickup": picked[0] if picked else None,
            "first_real": real[0] if real else None, "met48": bool(real and real[0] <= dl),
            "due48": not real,  # still needs its substantial conversation (overdue when cut > deadline)
            "follow_ups": fu, "next_follow_up": max(fu, key=lambda x: x["set_at"])["due"] if fu else None,
            "unreturned_calls": missed_in, "dials": s["dials"], "dial_days": s["dial_days"], "real_talks": s["real_talks"],
            "last_real": s["last_real"], "last_dial": last_dial, "enrolled": s["enrolled"], "dropped": s["dropped"], "stage": s["stage"]}


def note_loan_done(notes: list[dict], cut: datetime) -> datetime | None:
    times = [utc(n["t"]) for n in notes if utc(n["t"]) and utc(n["t"]) <= cut and says_loan_done(n.get("text"))]
    return min(times) if times else None


def collect(run_dir: str, cut: datetime) -> list[dict]:
    """Every collection lead of the run's weekends (both teams) assigned by ``cut``, with its facts."""
    from analytics.collection_daily.timelines import load_notes  # timelines reads facts' output, not the other way
    notes = load_notes(run_dir)
    out = []
    for d, day in weekends(run_dir):
        leads, hist, meta = load(d)
        team = hierarchy(meta.get("member_groups") or {})
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
        for side in SIDES:
            for l in cycle_leads(leads, hist, meta, day, side):
                if l["assigned"] > cut:
                    continue
                if (t := note_loan_done(notes.get(l["lead_id"], []), cut)) and not (l["enrolled_at"] and l["enrolled_at"] <= t):
                    l = {**l, "enrolled_at": t}
                acts = sorted((a for a in hist.get(l["lead_id"]) or [] if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
                out.append({**l, "side": side, "bootcamp_day": day, "data_dir": d, "team_of": team.get(l["caller"]),
                            **lead_facts(l, acts, start, cut)})
    return out
