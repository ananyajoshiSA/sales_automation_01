"""Compare two sales teams working the same lead type over one month.

Inputs
  leads.json     - team-owned leads (lead fields incl. bootcamp attendance), each tagged with "team"
  teams.json     - {"Team": [{"id","name"}, ...]}
  hist.jsonl     - full activity history per lead ({"lead_id","activities"})
  calls.json     - every call made/received by team members in the month (parse_phone_call rows + "team")

    python -m analytics.team_compare data/us_team_leads.json data/us_teams.json data/us_hist.jsonl \
        data/us_calls_sep.json 2026-09-01 2026-09-30 exports/us_compare
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.definitions import ENROLLED, REAL_CONVERSATION_SECS, WORKING_DAY_DIALS
from analytics.team_report import IST, enrolment_credit, utc, zip_score
from integrations.leadsquared import parse_activity_note

REAL = REAL_CONVERSATION_SECS
WON = ENROLLED
PIPE = ["Discovery Call Done", "Roadmap\xa0Done", "Counselled lead", "Follow Up For Closure", WON]


def _data(a):
    return {d.get("Key"): d.get("Value") for d in a.get("Data") or []}


def _call(a):
    note = parse_activity_note((a.get("ActivityFields") or {}).get("ActivityEvent_Note"))
    try:
        dur = int(float(note.get("Duration") or 0))
    except ValueError:
        dur = 0
    return {"t": utc(a["CreatedOn"]), "dir": "in" if a.get("EventCode") == 21 else "out",
            "status": note.get("Status") or (a.get("ActivityFields") or {}).get("Status"),
            "by": (note.get("Caller") or note.get("Reciever") or "").strip(), "dur": dur}


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def is_us_acc(l):
    s = " ".join(str(l.get(k) or "") for k in ("mx_Enquired_Course", "Source", "mx_Campaign_Name")).lower()
    return "account" in s or "usacc" in s


def lead_view(lead, acts, team_names, d0, d1):
    acts = sorted((a for a in acts if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
    created = utc(lead.get("CreatedOn"))
    calls = [_call(a) for a in acts if a.get("EventCode") in (21, 22)]
    out = [c for c in calls if c["dir"] == "out"]
    ans = [c for c in calls if c["status"] == "Answered"]
    real = [c for c in ans if c["dur"] >= REAL]
    assigns = [(utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventName") == "LeadAssigned"]
    first_team = next(((t, d) for t, d in assigns if d.get("CurrentOwner") in team_names), None)
    owners_in_team = [d.get("CurrentOwner") for t, d in assigns if d.get("CurrentOwner") in team_names]
    stages = [(utc(a["CreatedOn"]), _data(a)) for a in acts if a.get("EventName") == "StageChange"]
    reached = {d.get("CurrentStage") for _, d in stages} | {lead.get("ProspectStage")}
    enrolled_at = next((t for t, d in stages if d.get("CurrentStage") == WON), None)
    iec = [a for a in acts if a.get("EventCode") == 103]
    ni_reasons = [((a.get("ActivityFields") or {}).get("ActivityEvent_Note") or "").strip() for a in iec
                  if ((a.get("ActivityFields") or {}).get("Status") or "") == "Not Interested"]
    ni_comments = [d.get("Comment") or "" for _, d in stages if d.get("CurrentStage") == "Not Interested"]
    zips = [a for a in acts if a.get("EventCode") == 237]
    att = (lead.get("mx_Bootcamp_attended") or "").lower() == "yes"
    days = lead.get("mx_Bootcamp_Attendance")
    first_dial = out[0]["t"] if out else None
    last_real = real[-1]["t"] if real else None
    after_real = [c for c in out if last_real and c["t"] > last_real]
    gaps = []
    closure_calls = sorted(c["t"] for c in out)
    for a, b in zip(closure_calls, closure_calls[1:]):
        gaps.append((b - a).total_seconds() / 3600)
    return {
        "lead_id": lead["ProspectID"], "team": lead["team"], "owner": lead.get("OwnerIdName"),
        "first_team_owner": first_team[1].get("CurrentOwner") if first_team else lead.get("OwnerIdName"),
        "owners_in_team": len(set(owners_in_team)) or 1,
        "source": lead.get("Source") or "(blank)", "course": lead.get("mx_Enquired_Course") or "",
        "created": created, "created_day": created.astimezone(IST).strftime("%Y-%m-%d") if created else None,
        "bootcamp_attended": att, "bootcamp_days": days,
        "mins_to_team": round((first_team[0] - created).total_seconds() / 60) if first_team and created else None,
        "mins_to_first_dial": round((first_dial - created).total_seconds() / 60) if first_dial and created else None,
        "dials": len(out), "dial_days": len({c["t"].astimezone(IST).date() for c in out}),
        "answered": len(ans), "real": len(real), "talk_min": round(sum(c["dur"] for c in ans) / 60, 1),
        "longest_min": round(max((c["dur"] for c in ans), default=0) / 60, 1),
        "inbound": sum(1 for c in calls if c["dir"] == "in"),
        "inbound_missed": sum(1 for c in calls if c["dir"] == "in" and c["status"] != "Answered"),
        "inbound_missed_unreturned": sum(1 for c in calls if c["dir"] == "in" and c["status"] != "Answered"
                                         and not any(o["t"] > c["t"] for o in out)),
        "dials_after_last_real": len(after_real),
        "median_gap_h": med(gaps),
        "stage": lead.get("ProspectStage") or "", "reached": sorted(s for s in reached if s),
        "enrolled_at": enrolled_at,
        "zip_n": len(zips),
        "zip_pitch": [zip_score((a.get("ActivityFields") or {}).get("mx_Custom_4")) for a in zips],
        "zip_probe": [zip_score((a.get("ActivityFields") or {}).get("mx_Custom_5")) for a in zips],
        "zip_obj": [zip_score((a.get("ActivityFields") or {}).get("mx_Custom_6")) for a in zips],
        "zip_intent": [((a.get("ActivityFields") or {}).get("mx_Custom_1") or "").upper() for a in zips],
        "ni_reasons": [r for r in ni_reasons if r], "ni_comments": [c for c in ni_comments if c],
        "first_dial_in_month": bool(first_dial and d0 <= first_dial < d1),
        "dispositions": [((a.get("ActivityFields") or {}).get("Status") or "") for a in iec],
        "followups_logged": sum(1 for a in iec if (a.get("ActivityFields") or {}).get("mx_Custom_1")),
    }


def load_hist(path, ids):
    out = {}
    for line in open(path):
        d = json.loads(line)
        if d["lead_id"] in ids and d.get("activities") is not None and not d.get("error"):
            out[d["lead_id"]] = d["activities"]
    return out


def enrollment_credit(lead, acts, team_names):
    """Owner at the moment of enrollment (from assignment history), else current owner."""
    e = enrolment_credit(lead, acts, [])
    if not e or e["date_source"] != "stage history":
        return None, None
    return e["t"], e["owner_at_enrolment"]


def caller_stats(calls, team_of, d0, d1):
    per = defaultdict(lambda: defaultdict(Counter))
    times = defaultdict(lambda: defaultdict(list))
    for c in calls:
        t = utc(c["start_utc"])
        if not t or not (d0 <= t < d1):
            continue
        name = (c.get("caller") or "").strip()
        if name not in team_of:
            continue
        day = t.astimezone(IST).strftime("%Y-%m-%d")
        k = per[name][day]
        if c["direction"] == "outbound":
            k["dials"] += 1
        else:
            k["inbound"] += 1
            if c["status"] != "Answered":
                k["inbound_missed"] += 1
        if c["status"] == "Answered":
            k["connected"] += 1
            k["talk"] += c["duration"]
            if c["duration"] >= REAL:
                k["real"] += 1
        times[name][day].append(t.astimezone(IST))
    rows = []
    for name, days in per.items():
        active = [d for d, k in days.items() if k["dials"] >= WORKING_DAY_DIALS]
        tot = Counter()
        for d in active:
            tot.update(days[d])
        n = len(active) or 1
        starts = sorted(min(times[name][d]).hour * 60 + min(times[name][d]).minute for d in active)
        ends = sorted(max(times[name][d]).hour * 60 + max(times[name][d]).minute for d in active)
        fm = lambda m: f"{m // 60:02d}:{m % 60:02d}" if m is not None else ""  # noqa: E731
        rows.append({
            "team": team_of[name], "caller": name, "active_days": len(active),
            "dials_per_day": round(tot["dials"] / n), "connected_per_day": round(tot["connected"] / n),
            "connect_rate_%": pct(tot["connected"], tot["dials"] + tot["inbound"]),
            "real_calls_per_day": round(tot["real"] / n, 1), "talk_hrs_per_day": round(tot["talk"] / 3600 / n, 2),
            "inbound_per_day": round(tot["inbound"] / n, 1), "inbound_missed_%": pct(tot["inbound_missed"], tot["inbound"]),
            "median_start": fm(starts[len(starts) // 2] if starts else None),
            "median_end": fm(ends[len(ends) // 2] if ends else None),
        })
    return sorted(rows, key=lambda r: (r["team"], -r["talk_hrs_per_day"]))


def summarise(views, team):
    V = [v for v in views if v["team"] == team]
    n = len(V)
    att = [v for v in V if v["bootcamp_attended"]]
    s = {
        "team": team, "leads": n,
        "bootcamp_attended_%": pct(len(att), n),
        "attended_2plus_days_%": pct(sum(1 for v in V if str(v["bootcamp_days"]) in ("2", "3")), n),
        "median_hrs_to_team": round(med(v["mins_to_team"] for v in V) / 60, 1) if med(v["mins_to_team"] for v in V) is not None else None,
        "median_hrs_to_first_dial": round(med(v["mins_to_first_dial"] for v in V) / 60, 1) if med(v["mins_to_first_dial"] for v in V) is not None else None,
        "dialled_within_1h_%": pct(sum(1 for v in V if v["mins_to_first_dial"] is not None and v["mins_to_first_dial"] <= 60), n),
        "never_dialled_%": pct(sum(1 for v in V if not v["dials"]), n),
        "avg_dials": round(sum(v["dials"] for v in V) / n, 1) if n else 0,
        "reached_%": pct(sum(1 for v in V if v["answered"]), n),
        "real_conversation_%": pct(sum(1 for v in V if v["real"]), n),
        "avg_talk_min": round(sum(v["talk_min"] for v in V) / n, 1) if n else 0,
        "unreached_with_<=2_dials_%": pct(sum(1 for v in V if not v["answered"] and v["dials"] <= 2), sum(1 for v in V if not v["answered"])),
        "inbound_missed_unreturned": sum(v["inbound_missed_unreturned"] for v in V),
        "multi_owner_%": pct(sum(1 for v in V if v["owners_in_team"] > 1), n),
    }
    for st in PIPE:
        s[f"reached_{st.replace(chr(160), ' ')}_%"] = pct(sum(1 for v in V if st in v["reached"]), n)
    s["enrolled"] = sum(1 for v in V if WON in v["reached"])
    s["enrolled_%"] = pct(s["enrolled"], n)
    s["attendees"] = len(att)
    s["enrolled_of_attendees_%"] = pct(sum(1 for v in att if WON in v["reached"]), len(att))
    s["real_conv_of_attendees_%"] = pct(sum(1 for v in att if v["real"]), len(att))
    s["attendees_never_real_conv"] = sum(1 for v in att if not v["real"])
    closure = [v for v in V if v["stage"] in ("Follow Up For Closure", "Counselled lead", "Roadmap\xa0Done")]
    s["closure_stage_now"] = len(closure)
    s["closure_median_gap_h"] = med(v["median_gap_h"] for v in closure)
    s["closure_going_dark_%"] = pct(sum(1 for v in closure if v["dials_after_last_real"] >= 4), len(closure))
    z = [x for v in V for x in v["zip_pitch"] if x is not None]
    s["zip_pitch_%"] = round(statistics.mean(z)) if z else None
    z = [x for v in V for x in v["zip_probe"] if x is not None]
    s["zip_probing_%"] = round(statistics.mean(z)) if z else None
    z = [x for v in V for x in v["zip_obj"] if x is not None]
    s["zip_objection_%"] = round(statistics.mean(z)) if z else None
    intents = Counter(i for v in V for i in v["zip_intent"] if i and i != "NOT_AVAILABLE")
    s["zip_high_mod_%"] = pct(intents["HIGH"] + intents["MODERATE"], sum(intents.values()))
    s["ni_notes_sample"] = [r for v in V for r in (v["ni_reasons"] + v["ni_comments"])][:400]
    s["dispositions"] = Counter(d for v in V for d in v["dispositions"]).most_common(12)
    s["leads_with_followup_date_logged_%"] = pct(sum(1 for v in V if v["followups_logged"]), n)
    return s


def main(leads_p, teams_p, hist_p, calls_p, start, end, out_dir):
    teams = json.load(open(teams_p))
    team_names = {m["name"] for ms in teams.values() for m in ms}
    team_of = {m["name"]: t for t, ms in teams.items() for m in ms}
    d0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    d1 = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)
    leads = {l["ProspectID"]: l for l in json.load(open(leads_p))}
    month_leads = {k: l for k, l in leads.items() if is_us_acc(l) and utc(l.get("CreatedOn"))
                   and d0 <= utc(l["CreatedOn"]) < d1}
    hist = load_hist(hist_p, set(leads))
    views = [lead_view(l, hist[k], team_names, d0, d1) for k, l in month_leads.items() if k in hist]

    # enrollments that happened in the month (any lead age), credited to owner at the time
    enrol = []
    for k, acts in hist.items():
        t, owner = enrollment_credit(leads[k], acts, team_names)
        if t and d0 <= t < d1:
            l = leads[k]
            created = utc(l.get("CreatedOn"))
            calls = [_call(a) for a in acts if a.get("EventCode") in (21, 22) and utc(a["CreatedOn"]) <= t]
            enrol.append({"lead_id": k, "team": team_of.get(owner, l["team"]), "credited_to": owner,
                          "enrolled_ist": t.astimezone(IST).strftime("%Y-%m-%d"),
                          "lead_created": created.astimezone(IST).strftime("%Y-%m-%d") if created else "",
                          "days_to_enrol": round((t - created).total_seconds() / 86400, 1) if created else None,
                          "calls_before": sum(1 for c in calls if c["dir"] == "out"),
                          "talk_min_before": round(sum(c["dur"] for c in calls if c["status"] == "Answered") / 60, 1),
                          "bootcamp_attended": (l.get("mx_Bootcamp_attended") or "") == "Yes",
                          "source": l.get("Source") or ""})
    calls = json.load(open(calls_p)) if calls_p and os.path.exists(calls_p) else []
    callers = caller_stats(calls, team_of, d0, d1)
    summary = [summarise(views, t) for t in teams]
    os.makedirs(out_dir, exist_ok=True)

    def wcsv(name, rows):
        if rows:
            with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)

    wcsv("callers.csv", callers)
    wcsv("enrollments.csv", enrol)
    json.dump({"summary": summary, "callers": callers, "enrollments": enrol}, open(os.path.join(out_dir, "compare.json"), "w"),
              indent=1, default=str)
    json.dump(views, open(os.path.join(out_dir, "lead_views.json"), "w"), default=str)
    for s in summary:
        print(json.dumps(s, indent=1, default=str, ensure_ascii=False))
    print("enrollments in month by team:", Counter(e["team"] for e in enrol))


if __name__ == "__main__":
    main(*sys.argv[1:8])
