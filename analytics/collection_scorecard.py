"""Weekly collection scorecard for bootcamp collection leads: a workbook for ADs and team leaders.

Built only from LeadSquared (``scripts/fetch_bootcamp_collections.py``), so nobody types into it. Sheets:
Summary (recovery against the bar per team, AD and TL), Recovery pipeline (every pending lead, most recoverable
first, why it is listed and the next step), Second chance (recent drops whose reason can still be worked),
Callers (recovery next to effort, with a box and the TL's action), Weekly cohorts (recovered by day 7, 14 and
30 of each booking week) and Bootcamps (bootcamp × team).

Recovered: the lead reached Collections done or Course Enrolled after paying its booking fee (loan disbursed,
one shot or first instalment). Each lead counts for the last collection-team caller who owned it, so a lead moved
out after a drop still counts against the caller who dropped it. Recovery rates use leads booked 30+ days ago,
by when 98% of collections have happened. A pending lead's chance is the share of past leads still pending at the
same age that later paid: a measured rate, not a promise.

    python -m analytics.collection_scorecard data/bc exports/collection_scorecard [--bar 85]
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from analytics.bootcamp_collections import BOT, COLLECTED, _call, _data, _fields, build, median, pct
from analytics.daily_plan.workbook import F, hdr, put, title
from integrations.timeutil import IST, ist_day, utc

SIDES = {"Elite Changemakers": "Mayur", "Team Puja Malik (DSV+Women AI)": "Puja / Anmol"}
NO_TEAM = "Not with either team"
# Mayur's TLs by caller (the enrolment sheet's caller list, Oct 2026); Puja's side by LeadSquared sub-group.
MAYUR_TLS = {
    "Sonal Tripathi": ("Sonal Tripathi", "Dipak Kumar Verma", "Vedahi Rana", "Shikha Chahar", "Yamini Billore"),
    "Anjali Srivastava": ("Anjali Srivastava", "Devika Singh", "Kanika Verma", "Srashti Dwivedi", "Shivangi Vyas"),
    "Ayush Srivastava": ("Ayush Srivastava", "Aisha Sami", "Himanshi"),
    "Prawal Singh": ("Prawal Singh",),
}
PUJA_GROUP_TL = {"Team Women AI": "Nancy Bajaj", "DSV - UK (Aditya)": "Aditya Chugh",
                 "DSV-Domestic-(Shivam Sharma)": "Shivam Sharma"}
AD_OF_TL = {"Sonal Tripathi": "Sahil Arora", "Anjali Srivastava": "Raunak Mandilwar", "Ayush Srivastava": "Raunak Mandilwar",
            "Prawal Singh": "Raunak Mandilwar", "Nancy Bajaj": "Anmol Gakhar", "Aditya Chugh": "Anmol Gakhar",
            "Shivam Sharma": "Anmol Gakhar"}
ADS = {"Sahil Arora": "Mayur", "Raunak Mandilwar": "Mayur", "Anmol Gakhar": "Puja / Anmol"}
UNKNOWN = "Not known"

BAR = 85.0
MATURE_DAYS = 30
MIN_CALLER_LEADS = 20
EARLY_BOOKING_DAYS = 10      # a booking this long before its bootcamp belongs to an earlier bootcamp
STALE_DAYS = 7
SECOND_CHANCE_DAYS = 45
AGE_BUCKETS = ((0, "0–7 days"), (8, "8–14 days"), (15, "15–30 days"), (31, "31–60 days"), (61, "61+ days"))
WORKABLE_REASONS = {"Loan, EMI or payment process failed", "Will decide later", "Deferred to a later batch",
                    "Stopped responding", "No reason recorded"}
LOAN_WORDS = ("loan", "emi", "finance", "nbfc")


def hierarchy(member_groups: dict[str, list[str]]) -> dict[str, tuple[str, str, str]]:
    """Caller name → (side, AD, TL) for every member of the two collection teams."""
    out = {}
    for name, groups in member_groups.items():
        side = next((SIDES[g] for g in groups if g in SIDES), None)
        if not side:
            continue
        if name in ADS:
            out[name] = (side, name, "(AD)")
            continue
        tl = next((t for t, cs in MAYUR_TLS.items() if name in cs), None) if side == "Mayur" else \
            (name if name in PUJA_GROUP_TL.values() else next((PUJA_GROUP_TL[g] for g in groups if g in PUJA_GROUP_TL), None))
        out[name] = (side, AD_OF_TL.get(tl, UNKNOWN), tl or UNKNOWN)
    return out


def credit(view: dict, acts: list[dict], team: dict[str, tuple]) -> dict:
    """The lead counted for the last collection-team caller who owned it (its owner if still in a team)."""
    owner = view["owner"]
    if owner not in team:
        prev = [(_data(a).get("PreviousOwner") or "").strip()
                for a in sorted(acts, key=lambda a: a.get("CreatedOn") or "") if a.get("EventCode") == 3001]
        owner = next((p for p in reversed(prev) if p in team), owner)
    side, ad, tl = team.get(owner, (NO_TEAM, NO_TEAM, NO_TEAM))
    return {**view, "caller": owner, "current_owner": view["owner"], "moved_out": owner != view["owner"],
            "side": side, "ad": ad, "tl": tl}


def lead_extras(acts: list[dict], booked: datetime, now: datetime) -> dict:
    """What the pipeline needs beyond the lead view: attempt-days, last talk, last disposition, unreturned call."""
    acts = sorted((a for a in acts if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
    calls = [c for c in (_call(a) for a in acts if a.get("EventCode") in (21, 22))
             if c["t"] and c["t"] >= booked and not BOT.search(c["by"])]
    dials = [c for c in calls if c["out"]]
    real = [c["t"] for c in calls if c["answered"] and c["dur"] >= 120]
    last_dial = dials[-1]["t"] if dials else None
    missed = [c["t"] for c in calls if not c["out"] and not c["answered"]]
    forms = [a for a in acts if a.get("EventCode") == 103 and utc(a["CreatedOn"]) >= booked]
    collected = next((utc(a["CreatedOn"]) for a in acts if a.get("EventCode") == 3002 and utc(a["CreatedOn"]) >= booked
                      and _data(a).get("CurrentStage") in COLLECTED), None)
    return {
        "attempt_days": len({ist_day(d["t"]) for d in dials}),
        "days_to_collect": round((collected - booked).total_seconds() / 86400, 1) if collected else None,
        "last_dial": last_dial,
        "days_since_real": round((now - real[-1]).total_seconds() / 86400, 1) if real else None,
        "last_disposition": (_fields(forms[-1]).get("Status") or "").replace("\xa0", " ") if forms else "",
        "call_not_returned": bool(missed and (not last_dial or missed[-1] > last_dial)),
    }


def rows_for(leads: list[dict], hist: dict[str, list], meta: dict) -> tuple[list[dict], dict]:
    """One row per collection lead, credited and enriched, plus the reconciliation of what was left out."""
    now = utc(meta["fetched_at_utc"])
    team = hierarchy(meta.get("member_groups") or {})
    views, recon = build(leads, hist, meta["team_of_owner"], now, frozenset(meta.get("team_callers") or ()))
    names = {l["ProspectID"]: l for l in leads}
    rows = []
    for v in views:
        if v["bootcamp_day"] and (date.fromisoformat(v["bootcamp_day"])
                                  - date.fromisoformat(v["booked_ist"][:10])).days > EARLY_BOOKING_DAYS:
            recon["booking fee paid for an earlier bootcamp (left out)"] = \
                recon.get("booking fee paid for an earlier bootcamp (left out)", 0) + 1
            continue
        r = credit(v, hist[v["lead_id"]], team)
        r.update(lead_extras(hist[v["lead_id"]], v["booked"], now))
        l = names.get(v["lead_id"]) or {}
        r["lead_name"] = f"{l.get('FirstName') or ''} {l.get('LastName') or ''}".strip()
        r["next_follow_up"] = utc(l.get("mx_Next_follow_up_date"))
        r["mature"] = v["days_since_booking"] >= MATURE_DAYS
        rows.append(r)
    return rows, recon


def chances(rows: list[dict]) -> dict[str, tuple[float | None, int]]:
    """For each age bucket, of past leads still pending at that age, the share that later paid (rate, leads)."""
    out = {}
    for start, label in AGE_BUCKETS:
        settled = [r for r in rows if r["days_since_booking"] >= start + MATURE_DAYS]
        pending = [r for r in settled
                   if not (r["days_to_collect"] is not None and r["days_to_collect"] < start)
                   and not (r["outcome"] in ("lost", "deferred") and (r["days_to_loss"] or 0) < start)]
        later = sum(1 for r in pending if r["days_to_collect"] is not None)
        out[label] = (pct(later, len(pending)), len(pending))
    return out


def age_bucket(days: float) -> str:
    return next(label for start, label in reversed(AGE_BUCKETS) if days >= start)


def priority(r: dict) -> str:
    if r["days_since_booking"] <= 14 or r["call_not_returned"] or (r["days_since_real"] is not None and r["days_since_real"] <= 7):
        return "1 · Today"
    return "2 · This week" if r["days_since_booking"] <= 60 else "3 · TL decides"


def why(r: dict, now: datetime) -> str:
    out = []
    if r["call_not_returned"]:
        out.append("Lead called, not called back")
    if r["dials"] == 0:
        out.append("Never dialled after booking")
    elif r["days_since_last_dial"] is not None and r["days_since_last_dial"] >= STALE_DAYS:
        out.append(f"No dial for {int(r['days_since_last_dial'])} days")
    if r["callbacks_missed"]:
        out.append("Callback missed")
    if r["followup_overdue"]:
        out.append("Follow-up date passed")
    elif not r["next_follow_up"] or r["next_follow_up"] < now:
        out.append("No dated next step")
    if not r["connected"]:
        out.append("Never answered")
    return "; ".join(out) or "Being worked"


def next_step(r: dict) -> str:
    if r["call_not_returned"]:
        return "Call back first: the lead tried to reach you."
    if r["dials"] == 0:
        return "First call not made yet: call today."
    if priority(r) == "3 · TL decides":
        return "TL: final call this week: collect, move to a named batch, or drop with a reason."
    if not r["connected"]:
        return "Try another time (after 6 pm) and send a WhatsApp."
    if any(w in (r["last_disposition"] + " " + r["stage"]).lower() for w in LOAN_WORDS):
        return "Chase the loan: documents and partner decision; offer another partner if rejected."
    return "Call today: agree the payment date and mode, and save it as the next follow-up."


def second_chance_step(reason: str, real: bool) -> str:
    if not real:
        return "Dropped without a real conversation: TL calls once before it is closed."
    return {"Loan, EMI or payment process failed": "Offer another finance partner or instalments.",
            "Will decide later": "Call on the date they gave; offer the next batch with the booking fee kept.",
            "Deferred to a later batch": "Confirm the batch and date; move them into it.",
            "Stopped responding": "Try another time and number; send a WhatsApp with the payment options.",
            }.get(reason, "Find out why it was dropped and record the reason.")


def agg(rs: list[dict], bar: float) -> dict:
    n, col = len(rs), sum(r["outcome"] == "collected" for r in rs)
    return {"leads": n, "recovered": col, "recovery_%": pct(col, n),
            "gap_to_bar": max(0, math.ceil(bar / 100 * n - col)) if n else 0,
            "dialled_24h_%": pct(sum(r["dialled_24h"] for r in rs), n),
            "real_conv_%": pct(sum(r["real_convs"] > 0 for r in rs), n),
            "attempt_days": median([r["attempt_days"] for r in rs if r["outcome"] != "collected"]),
            "callbacks_kept": f"{sum(r['callbacks_due'] - r['callbacks_missed'] for r in rs)} of {sum(r['callbacks_due'] for r in rs)}",
            "abandoned": sum(r["outcome"] == "open" and r["stalled"] for r in rs),
            "moved_out": sum(r["moved_out"] and r["outcome"] != "collected" for r in rs),
            "no_reason": sum(r["reason"] == "No reason recorded" for r in rs),
            "lost_%": pct(sum(r["outcome"] in ("lost", "deferred") for r in rs), n)}


def callers(rows: list[dict], bar: float) -> list[dict]:
    """Recovery next to effort per caller (leads booked 30+ days ago), with a box and the TL's action."""
    g = defaultdict(list)
    for r in rows:
        if r["mature"] and r["side"] != NO_TEAM:
            g[(r["side"], r["ad"], r["tl"], r["caller"])].append(r)
    out = [{"side": k[0], "ad": k[1], "tl": k[2], "caller": k[3], **agg(v, bar)} for k, v in g.items() if len(v) >= MIN_CALLER_LEADS]
    for r in out:
        r["abandoned_%"] = pct(r["abandoned"], r["leads"])
    med = {(s, k): median([r[k] for r in out if r["side"] == s]) for s in {r["side"] for r in out}
           for k in ("dialled_24h_%", "attempt_days", "abandoned_%")}
    for r in out:
        m = lambda k: med[(r["side"], k)] or 0
        weak = sum([(r["dialled_24h_%"] or 0) < m("dialled_24h_%"), (r["attempt_days"] or 0) < m("attempt_days"),
                    (r["abandoned_%"] or 0) > m("abandoned_%")])
        good, busy = (r["recovery_%"] or 0) >= bar, weak < 2
        r["effort"] = "High" if busy else "Low"
        r["box"], r["action"] = {
            (True, True): ("Performing well", "Share what works with the team."),
            (True, False): ("Results fine, follow-up thin", "Watch: check the pipeline tab weekly."),
            (False, True): ("Effort there, not converting", "TL listens to 3 lost calls this week (conversation quality)."),
            (False, False): ("Effort first", "TL checks the caller's pipeline tab daily until the gaps close."),
        }[(good, busy)]
    return sorted(out, key=lambda r: (r["side"], r["ad"], r["tl"], -(r["recovery_%"] or 0)))


def cohorts(rows: list[dict], now: datetime) -> list[dict]:
    """Booking week × side: share recovered within 7, 14 and 30 days (blank until the week is that old)."""
    g = defaultdict(list)
    for r in rows:
        d = r["booked"].astimezone(IST).date()
        g[(d - timedelta(days=d.weekday()), r["side"])].append(r)
    out = []
    today = now.astimezone(IST).date()
    for (wk, side), rs in sorted(g.items()):
        n = len(rs)
        row = {"week": wk, "side": side, "leads": n}
        for h in (7, 14, 30):
            old = (today - (wk + timedelta(days=6))).days >= h
            row[f"d{h}"] = pct(sum(r["days_to_collect"] is not None and r["days_to_collect"] <= h for r in rs), n) if old else None
        row["now"] = pct(sum(r["outcome"] == "collected" for r in rs), n)
        row["pending"] = sum(r["outcome"] == "open" for r in rs)
        out.append(row)
    return out


def pipeline(rows: list[dict], now: datetime, rates: dict) -> list[dict]:
    out = []
    for r in rows:
        if r["outcome"] != "open":
            continue
        rate, _ = rates[age_bucket(r["days_since_booking"])]
        out.append({**r, "priority": priority(r), "chance": rate, "why": why(r, now), "step": next_step(r)})
    return sorted(out, key=lambda r: (r["priority"], -(r["chance"] or 0), -(r["days_since_last_dial"] or 999)))


def second_chance(rows: list[dict], now: datetime) -> list[dict]:
    out = []
    for r in rows:
        if r["outcome"] not in ("lost", "deferred") or r["days_to_loss"] is None:
            continue
        dropped = r["booked"] + timedelta(days=r["days_to_loss"])
        if now - dropped > timedelta(days=SECOND_CHANCE_DAYS):
            continue
        if r["reason"] in WORKABLE_REASONS or r["lost_without_real_conv"]:
            out.append({**r, "dropped": dropped, "step": second_chance_step(r["reason"], bool(r["real_convs"]))})
    return sorted(out, key=lambda r: r["dropped"], reverse=True)


# ---------- workbook ----------

GOOD, NEAR, BAD, GREY = "DCEFE6", "FFF0C2", "F8D7D3", "F2F2F2"
PRIORITY_FILL = {"1 · Today": BAD, "2 · This week": NEAR, "3 · TL decides": GREY}


def band(v: float | None, bar: float) -> str | None:
    if v is None:
        return None
    return GOOD if v >= bar else NEAR if v >= bar - 10 else BAD


def _ist(dt: datetime | None, fmt: str = "%d %b") -> str:
    return dt.astimezone(IST).strftime(fmt) if dt else ""


def _table(ws, r0: int, heads: list[str], widths: list[int], rows: list[list], fills: list[list] | None = None) -> int:
    hdr(ws, r0, heads, widths)
    for i, vals in enumerate(rows):
        for j, v in enumerate(vals):
            put(ws, r0 + 1 + i, j + 1, v, fill=(fills[i][j] if fills else None))
    ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    return r0 + 1 + len(rows)


def write_workbook(path: str, rows: list[dict], recon: dict, meta: dict, bar: float) -> dict:
    now = utc(meta["fetched_at_utc"])
    asof = now.astimezone(IST).strftime("%a %d %b %Y, %H:%M IST")
    rates = chances(rows)
    team_rows = [r for r in rows if r["side"] != NO_TEAM]
    pipe, second, cal = pipeline(team_rows, now, rates), second_chance(team_rows, now), callers(rows, bar)
    expected = round(sum((p["chance"] or 0) / 100 for p in pipe))
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    title(ws, 1, f"Bootcamp collections scorecard — status as of {asof}", 14)
    put(ws, 2, 1, f"Bar: {bar:.0f}% of booked leads recovered. Recovered = Collections done or Course Enrolled after the "
                  f"booking fee (loan, one shot or first instalment). Rates use leads booked {MATURE_DAYS}+ days ago; each lead "
                  f"counts for the last collection caller who held it. Source: LeadSquared only, nothing typed.", wrap=True)
    ws.merge_cells("A2:J2")
    ws.row_dimensions[2].height = 42
    r = 4
    heads = ["Team", "AD", "TL", "Leads booked 30+ days ago", "Recovered", "Recovered %", f"More recoveries needed for {bar:.0f}%",
             "Pending now (all ages)", "Pipeline: today", "Pipeline: this week", "Pipeline: TL decides"]
    groups: dict[tuple, list] = defaultdict(list)
    for x in team_rows:
        for key in ((x["side"], "All", ""), (x["side"], x["ad"], "All"), (x["side"], x["ad"], x["tl"])):
            groups[key].append(x)
    pc = Counter((p["side"], p["ad"], p["tl"], p["priority"]) for p in pipe)
    body, fills = [], []
    for key in sorted(groups, key=lambda k: (k[0], k[1] != "All", k[1], k[2] != "All", k[2])):
        rs = groups[key]
        a = agg([x for x in rs if x["mature"]], bar)
        side, ad, tl = key
        match = lambda p: p[0] == side and (ad == "All" or p[1] == ad) and (tl in ("All", "") or p[2] == tl)
        counts = [sum(v for k, v in pc.items() if match(k) and k[3] == pr) for pr in PRIORITY_FILL]
        body.append([side, ad, tl, a["leads"], a["recovered"], a["recovery_%"], a["gap_to_bar"],
                     sum(x["outcome"] == "open" for x in rs), *counts])
        fills.append([None] * 5 + [band(a["recovery_%"], bar)] + [None] * 5)
    r = _table(ws, r, heads, [22, 20, 20, 12, 11, 11, 14, 12, 11, 11, 11], body, fills) + 1
    title(ws, r, "Pending leads: how often leads still pending at each age went on to pay (measured from past leads)")
    r = _table(ws, r + 1, ["Days since booking", "Past leads pending at that age", "Share that later paid"], [22, 20, 20],
               [[lab, n, f"{rate}%" if rate is not None else "—"] for lab, (rate, n) in rates.items()]) + 1
    put(ws, r, 1, f"{len(pipe)} leads are pending. Worked like past leads of the same age, about {expected} would pay "
                  f"(an estimate from history, not a target). Half of all collections happen within 3 days of booking and "
                  f"94% within 14, so the newest leads matter most.", wrap=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
    ws.row_dimensions[r].height = 30
    r += 2
    title(ws, r, "Reconciliation")
    for i, (k, v) in enumerate({**recon, "credited to a collection team": len(team_rows),
                                "never held by a collection-team caller": len(rows) - len(team_rows)}.items()):
        put(ws, r + 1 + i, 1, k)
        put(ws, r + 1 + i, 2, v)
    ws.freeze_panes = None

    ws = wb.create_sheet("Recovery pipeline")
    title(ws, 1, f"Pending leads, most recoverable first — {asof}. Work priority 1 today; chance = share of past leads of "
                 f"the same age that later paid.")
    body = [[p["priority"], f"{p['chance']}%" if p["chance"] is not None else "—", p["side"], p["ad"], p["tl"], p["caller"],
             p["lead_name"], p["lead_id"], p["course"], p["booked_ist"][:10], int(p["days_since_booking"]), p["stage"],
             p["last_disposition"] or "None logged", _ist(p["last_dial"]), p["dials"], p["real_convs"],
             int(p["days_since_real"]) if p["days_since_real"] is not None else "never", _ist(p["next_follow_up"], "%d %b %H:%M"),
             p["why"], p["step"]] for p in pipe]
    fills = [[PRIORITY_FILL[p["priority"]]] + [None] * 19 for p in pipe]
    _table(ws, 2, ["Priority", "Chance (history)", "Team", "AD", "TL", "Caller", "Lead", "Lead ID", "Bootcamp", "Booked",
                   "Days since booking", "Stage now", "Last disposition", "Last dial", "Dials", "Real conversations",
                   "Days since last real conversation", "Next follow-up (IST)", "Why it is listed", "Next step"],
           [13, 10, 13, 16, 16, 18, 20, 12, 20, 11, 9, 18, 16, 9, 7, 9, 10, 13, 34, 40], body, fills)
    ws.auto_filter.ref = f"A2:{get_column_letter(20)}{2 + len(body)}"

    ws = wb.create_sheet("Second chance")
    title(ws, 1, f"Dropped in the last {SECOND_CHANCE_DAYS} days with a reason that can still be worked, or with no real "
                 f"conversation — newest first")
    body = [[s["side"], s["ad"], s["tl"], s["caller"], s["current_owner"], s["lead_name"], s["lead_id"], s["course"],
             s["booked_ist"][:10], _ist(s["dropped"], "%Y-%m-%d"), s["stage"], s["reason"], s["real_convs"], s["step"]] for s in second]
    _table(ws, 2, ["Team", "AD", "TL", "Dropped by", "Owner now", "Lead", "Lead ID", "Bootcamp", "Booked", "Dropped on",
                   "Stage now", "Reason (from notes)", "Real conversations", "Next step"],
           [13, 16, 16, 18, 18, 20, 12, 20, 11, 11, 16, 26, 9, 44], body)
    ws.auto_filter.ref = f"A2:N{2 + len(body)}"

    ws = wb.create_sheet("Callers")
    title(ws, 1, f"Callers with {MIN_CALLER_LEADS}+ leads booked {MATURE_DAYS}+ days ago. Effort is High unless the caller is "
                 f"below their team's median on 2 of: dialled in 24 h, attempt-days, leads abandoned.")
    body = [[c["side"], c["ad"], c["tl"], c["caller"], c["leads"], c["recovery_%"], c["gap_to_bar"], c["dialled_24h_%"],
             c["real_conv_%"], c["attempt_days"], c["callbacks_kept"], c["abandoned"], c["moved_out"], c["no_reason"],
             c["effort"], c["box"], c["action"]] for c in cal]
    fills = [[None] * 5 + [band(c["recovery_%"], bar)] + [None] * 9 + [GOOD if c["box"] == "Performing well" else NEAR, None]
             for c in cal]
    _table(ws, 2, ["Team", "AD", "TL", "Caller", "Leads", "Recovered %", f"More needed for {bar:.0f}%", "Dialled in 24 h %",
                   "Real conversation %", "Attempt-days on unpaid leads (median)", "Dated callbacks kept",
                   "Abandoned (pending, no dial 7+ days)", "Dropped and moved out", "Drops with no reason", "Effort", "Box",
                   "What the TL does"], [12, 16, 16, 20, 7, 10, 10, 10, 10, 12, 10, 12, 10, 10, 8, 22, 40], body, fills)

    ws = wb.create_sheet("Weekly cohorts")
    title(ws, 1, f"Recovered by day 7, 14 and 30 of each booking week (blank until the week is that old). Bar {bar:.0f}%.")
    co = cohorts(team_rows, now)
    body = [[c["week"].strftime("%d %b %Y"), c["side"], c["leads"], c["d7"], c["d14"], c["d30"], c["now"], c["pending"]] for c in co]
    fills = [[None] * 3 + [None, None, band(c["d30"], bar), band(c["now"], bar), None] for c in co]
    _table(ws, 2, ["Booking week (Mon)", "Team", "Leads", "By day 7 %", "By day 14 %", "By day 30 %", "Recovered now %",
                   "Pending now"], [16, 14, 8, 10, 10, 10, 12, 10], body, fills)

    ws = wb.create_sheet("Bootcamps")
    title(ws, 1, "Recovered % by bootcamp and team (leads booked 30+ days ago). Low for both teams = lead quality; one "
                 "team lower on the same bootcamp = execution.")
    sides = sorted({r["side"] for r in team_rows})
    by = defaultdict(list)
    for x in team_rows:
        if x["mature"]:
            by[(x["course"], x["side"])].append(x)
    courses = sorted({c for c, _ in by}, key=lambda c: -sum(len(by.get((c, s), [])) for s in sides))
    body, fills = [], []
    for c in courses:
        row, fr = [c], [None]
        for s in sides:
            a = agg(by.get((c, s), []), bar)
            row += [a["leads"] or "", a["recovery_%"] if a["leads"] else ""]
            fr += [None, band(a["recovery_%"], bar) if a["leads"] >= 15 else None]
        body.append(row)
        fills.append(fr)
    _table(ws, 2, ["Bootcamp"] + [f"{s}: {k}" for s in sides for k in ("leads", "recovered %")],
           [26] + [12] * (2 * len(sides)), body, fills)
    put(ws, 3 + len(body), 1, "Colour only where a team has 15+ leads for the bootcamp.", italic=True)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)
    return {"as_of": asof, "pipeline": len(pipe), "pipeline_by_priority": dict(Counter(p["priority"] for p in pipe)),
            "expected_from_pipeline": expected, "second_chance": len(second), "callers": len(cal), "chances": rates,
            "by_side": {s: agg([x for x in team_rows if x["side"] == s and x["mature"]], bar) for s in sides}}


def load(data_dir: str) -> tuple[list[dict], dict, dict]:
    meta = json.load(open(os.path.join(data_dir, "meta.json")))
    leads = json.load(open(os.path.join(data_dir, "tagged_leads.json")))
    hist = {}
    for line in open(os.path.join(data_dir, "hist.jsonl")):
        d = json.loads(line)
        if d.get("activities") is not None and not d.get("error"):
            hist[d["lead_id"]] = d["activities"]
    return leads, hist, meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--bar", type=float, default=BAR)
    a = ap.parse_args()
    leads, hist, meta = load(a.data_dir)
    rows, recon = rows_for(leads, hist, meta)
    day = utc(meta["fetched_at_utc"]).astimezone(IST).strftime("%Y-%m-%d")
    path = os.path.join(a.out_dir, f"collection_scorecard_{day}.xlsx")
    out = write_workbook(path, rows, recon, meta, a.bar)
    print(json.dumps({"workbook": path, **out}, indent=1, default=str))


if __name__ == "__main__":
    main()
