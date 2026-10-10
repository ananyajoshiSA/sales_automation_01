"""Validation gate for the daily team performance report. Runs before any PDF is written.

Every check is pass/fail with a one-line detail; one failure blocks the report. Covers the
Parameters checklist (section 10) plus data checks: duplicate activities, calls outside the IST
window, unmapped callers, enrolment double-counting and credit, impossible values, totals that
must reconcile, and a spot-check of credited enrolments against the raw LeadSquared stage history.
"""

from __future__ import annotations

import json
import os
import random
from collections import Counter
from datetime import datetime, timedelta

from analytics.definitions import is_calling_software
from analytics.team_report import utc
from analytics.zip_calls import ZIP_LATE_WINDOW

NOT_A_USER_MAX_PCT = 5      # calls whose caller is not a LeadSquared user (IVR etc.) after bots are removed
ZIP_DROPPED_MAX_PCT = 5     # P40 / checklist 3
SPOT_CHECK_N, SPOT_CHECK_SEED = 10, 5


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def data_checks(run: dict, A: dict) -> list[dict]:
    calls, T, tot = A["_calls"], A["teams"], A["totals"]
    out = []

    ids = Counter(c.get("activity_id") for c in run["calls"] if c.get("activity_id"))
    zids = Counter(z.get("ProspectActivityId") for z in run["zips"] if z.get("ProspectActivityId"))
    dup = sum(v - 1 for v in ids.values() if v > 1) + sum(v - 1 for v in zids.values() if v > 1)
    out.append(_check("No duplicate activity IDs", dup == 0, f"{dup} duplicate call/Zipteams activity IDs"))

    d0 = datetime.fromisoformat(A["window"]["d0"])
    d1 = d0 + timedelta(days=1)
    outside = sum(1 for c in calls if not c["t"] or not d0 <= c["t"] < d1)
    zout = sum(1 for z in run["zips"] if not (t := utc(z.get("CreatedOn"))) or not d0 <= t < d1 + ZIP_LATE_WINDOW)
    zout += sum(1 for c in calls if c.get("zip") and not d0 <= c["t"] < d1)  # an analysis on a call of another day
    bad = tot.get("unreadable_time_excluded", 0)
    out.append(_check("Every counted call and note is inside the IST day", outside == 0 and zout == 0 and bad == 0,
                      f"{outside} counted calls and {zout} Zipteams notes outside {d0:%d %b} 00:00–23:59 IST "
                      f"(notes up to {ZIP_LATE_WINDOW.seconds // 3600} h later count only for a call that ended then: "
                      f"{tot.get('zip_after_day_kept', 0)} kept, {tot.get('zip_other_day_excluded', 0)} excluded); "
                      f"{tot['outside_window_excluded']} fetched calls started on another day and were excluded; "
                      f"{bad} calls had a start time that couldn't be read; "
                      f"{tot.get('late_edits_recovered', 0)} calls edited after the day were recovered "
                      f"(edit margin {tot.get('edit_margin_days', 0)} day{'' if tot.get('edit_margin_days', 0) == 1 else 's'})"))

    nu = T.get("Not a user", {})
    nu_calls = nu.get("dials", 0) + nu.get("inbound", 0)
    pct = 100 * nu_calls / len(calls) if calls else 0
    out.append(_check("Callers map to a team", pct < NOT_A_USER_MAX_PCT and all(c.get("team") for c in calls),
                      f"{nu_calls} calls ({pct:.1f}%) from callers who are not LeadSquared users; limit {NOT_A_USER_MAX_PCT}%"))

    people, G = A["people"], A.get("groups") or {}
    users = {(p["team"], p["name"]): p["groups"] for p in people}
    wrong = [k for k, gs in users.items() if is_calling_software(k[0]) or k[0] not in gs
             and not (k[0] == "Unassigned" and all(is_calling_software(g) for g in gs))]
    shared = {g for p in people if p["dials"] for g in p["groups"] if g != p["team"]}
    off = [g for g in shared | set(G) if g not in G or g not in shared or any(
        G[g][k] != sum(p[k] for p in people if g in p["groups"]) for k in ("dials", "inbound", "real", "credited"))]
    multi = sum(1 for gs in users.values() if len(gs) > 1)
    out.append(_check("Callers in several groups: one team each, every shared group shown", not wrong and not off,
                      f"{len(wrong)} callers in a team that is a phone system or not one of their groups; {multi} callers in more "
                      f"than one group; {len(G)} groups share callers with another team, {len(off)} missing or not matching "
                      "their callers' figures"))

    E = A["enrollments"]
    leads = Counter(e["lead"] for e in E)
    doubled = [l for l, n in leads.items() if n > 1]
    out.append(_check("Each enrolment counted once", not doubled, f"{len(doubled)} leads counted more than once"))

    spoke = {(c["lead_id"], c["name"]) for c in calls if c["ans"]}
    bad = [e for e in E if e["caller"] and (e["lead"], e["caller"]) not in spoke]
    out.append(_check("Credited callers spoke to the lead that day", not bad,
                      f"{len(bad)} of {sum(1 for e in E if e['caller'])} credited enrolments have no answered call by that caller"))

    rates = [(t, k, s[k]) for t, s in T.items() for k in ("answer_pct", "conv_pct", "probe", "pitch", "obj", "hi_mod_pct")
             if s[k] is not None and not 0 <= s[k] <= 100]
    neg = sum(1 for c in run["calls"] if (c.get("duration") or 0) < 0)
    out.append(_check("Rates within 0–100 and no negative durations", not rates and neg == 0,
                      f"{len(rates)} rates out of range, {neg} negative durations"))

    recon = {
        "calls = team dials + inbound": tot["calls"] == sum(s["dials"] + s["inbound"] for s in T.values()),
        "fetched = counted + other-day + unreadable-time + bot calls":
            tot["calls_raw"] == tot["calls"] + tot["outside_window_excluded"] + tot.get("unreadable_time_excluded", 0) + tot["bots_excluded"],
        "credited = sum of team credited": tot["enroll_credited"] == sum(s["credited"] for s in T.values()),
        "team credited = sum of its callers": all(s["credited"] == sum(p["credited"] for p in people if p["team"] == t)
                                                  for t, s in T.items() if t != "Not a user"),
        "team real convs = sum of its callers": all(s["real"] == sum(p["real"] for p in people if p["team"] == t)
                                                    for t, s in T.items() if t != "Not a user"),
        "Zipteams attributed + dropped = total": tot["zip_attr"] + tot["zip_dropped"] == tot["zip_total"],
    }
    failed = [k for k, ok in recon.items() if not ok]
    out.append(_check("Totals reconcile across teams and callers", not failed,
                      "; ".join(failed) or f"team and caller totals add up to the day's totals ({len(recon)} of {len(recon)} sums match)"))

    zp = 100 * tot["zip_dropped"] / tot["zip_total"] if tot["zip_total"] else 100
    out.append(_check("Zipteams included, under 5% of notes dropped", tot["zip_total"] > 0 and zp < ZIP_DROPPED_MAX_PCT,
                      f"{tot['zip_attr']:,} of {tot['zip_total']:,} notes attributed, {tot['zip_dropped']} dropped ({zp:.1f}%)"))
    out.append(_check("At least one team eligible for ranking", bool(A["rank"]), f"{len(A['rank'])} ranked teams"))
    return out


def spot_check(A: dict, history) -> dict:
    """Re-read the stage history of a sample of credited enrolments and confirm the first 'Course Enrolled'.

    ``history(lead_id)`` returns the lead's stage-change activities (event 3002).
    """
    from analytics.team_performance import first_enrollment

    credited = sorted(e["lead"] for e in A["enrollments"] if e["team"])
    random.seed(SPOT_CHECK_SEED)
    sample = random.sample(credited, min(SPOT_CHECK_N, len(credited)))
    d0, end = datetime.fromisoformat(A["window"]["d0"]), datetime.fromisoformat(A["window"]["cw_end"])
    bad = []
    for lead in sample:
        first = first_enrollment(history(lead))
        if not first or not d0 <= first <= end:
            bad.append(lead)
    return _check("Spot-check of credited enrolments against stage history", sample and not bad,
                  f"{len(sample) - len(bad)} of {len(sample)} sampled enrolments confirmed as first-ever 'Course Enrolled' in the window")


def verdict_numbers_check(verdict_numbers: list[str], scorecard_cells: set[str]) -> dict:
    missing = [n for n in verdict_numbers if n not in scorecard_cells]
    return _check("Every team figure quoted on page 1 appears in the team tables", not missing,
                  f"missing: {', '.join(missing)}" if missing else f"{len(verdict_numbers)} numbers matched")


def sections_check(missing: list[str], total: int) -> dict:
    """P62: every section of the report is there, in order."""
    return _check("Every report section is present, in order", not missing,
                  f"missing or out of order: {', '.join(missing)}" if missing else f"{total} sections in order")


def write_log(checks: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(checks, open(path, "w"), indent=1)
    with open(path.replace(".json", ".txt"), "w", encoding="utf-8") as fh:
        for c in checks:
            fh.write(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']}: {c['detail']}\n")


def summary_line(n_checks: int, log_path: str) -> str:
    """Printed in section 11; a report is only written when every check passed."""
    return f"Validated: all {n_checks} checks passed before this report was built (log: {log_path.replace('.json', '.txt')})"


def integrity_check(I: dict) -> dict:
    """P74: the call-integrity counts reconcile and carry no lead details."""
    problems = []
    if I["flagged_calls"] > I["long_calls"]:
        problems.append("more flagged calls than long calls")
    if sum(r["long_calls"] for r in I["callers"]) != I["long_calls"]:
        problems.append("caller long calls don't add up to the total")
    if any(r["flagged"] > r["long_calls"] or r["checked"] > r["long_calls"] for r in I["callers"]):
        problems.append("a caller has more flagged or checked calls than long calls")
    if I.get("transcripts_matched", 0) > I.get("sampled", 0):
        problems.append("more transcripts matched than calls sampled")
    if any(k in json.dumps(I) for k in ("lead_number", "lead_id", "display_number")):
        problems.append("lead details in the summary")
    return {"check": "Call-integrity counts reconcile, no lead details", "ok": not problems,
            "detail": "; ".join(problems) or f"{I['flagged_calls']} of {I['long_calls']} long calls flagged; "
                      + (f"{I.get('transcripts_matched', 0)} of {I['sampled']} sampled calls matched a transcript"
                         if I.get("sampled") else "no transcripts were read for this check")}
