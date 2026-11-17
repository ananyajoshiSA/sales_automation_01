"""followup-audit-module. Follow-up call audit: every lead moved to "Follow Up For Closure" on a day, its follow-up calls up
to the move read together by Claude against Final_Prompt_Oct_2026 (unchanged), as an audit workbook and one feedback PDF
per lead. Guide: docs/followup_audit_module.md.

Steps (data/followup_audit/<run>/ holds everything with lead details; git-ignored; <run> is the day, or numbers_<date>):
  fetch          find the day's leads, their calls (LeadSquared, read-only) and transcripts; write the reading packs;
                 also the 7-day enrolment check of the leads audited a week earlier
  fetch-numbers  on demand: the same audit for pasted numbers (each lead's latest move to the stage)
  check          validate one result file written by a Claude reader (prints OK or the problems)
  status         how many leads are found, read, checked, re-checked
  review         prepare | apply the second-reading compliance check
  build          the workbook, PDFs, ZIP and CSV; copied to the project's reports folder

    python -m analytics.followup_audit_module fetch 2026-11-14
    python -m analytics.followup_audit_module fetch-numbers numbers.txt
    python -m analytics.followup_audit_module check 2026-11-14 data/followup_audit/2026-11-14/reading/results/KEY.json
    python -m analytics.followup_audit_module review prepare 2026-11-14
    python -m analytics.followup_audit_module build 2026-11-14 [--out /mnt/project-files/reports/followup_audit] [--discovery data/discovery_audit]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import timedelta

from analytics.discovery_audit_module import find as dfind
from analytics.discovery_audit_module import transcripts
from analytics.followup_audit_module import find, reading, report, review
from integrations.leadsquared import LeadSquaredClient
from integrations.timeutil import now_ist, utc

BASE = "data/followup_audit"
REPORTS = "/mnt/project-files/reports/followup_audit"
ENROLLED = "Course Enrolled"


def folder(run: str) -> str:
    return f"{BASE}/{run}"


def load(run: str) -> list[dict]:
    return json.load(open(f"{folder(run)}/leads.json"))


def days_before(day: str, n: int) -> str:
    for _ in range(n):
        day = find.prev_day(day)
    return day


def retry_earlier(day: str, days_back: int = 2) -> list[dict]:
    """Leads from the last two runs still waiting for a transcript, carried into this run until their third try."""
    last: dict[str, dict] = {}
    for k in range(days_back, 0, -1):  # oldest first, so the latest run's copy (and its try count) wins
        d = days_before(day, k)
        p = f"{folder(d)}/leads.json"
        if os.path.exists(p):
            for e in json.load(open(p)):
                if report.status(e, f"{folder(d)}/reading")[0] == "TRANSCRIPT_NOT_FOUND":
                    last[reading.lead_key(e)] = {**e, "_run": d}
    out = []
    for e in last.values():
        if e.get("tries", 1) < report.MAX_TRIES:
            run = e.pop("_run")
            out.append({**e, "tries": e.get("tries", 1) + 1,
                        "note": (e.get("note") + ". " if e.get("note") else "") + f"Carried over from the {run} run (transcript was late)"})
    return out


def attach(entries: list[dict], f: str) -> dict:
    """Transcripts for the scored and background calls (the call dicts are filled in place)."""
    return transcripts.attach([{"lead": e["lead"], "calls": e["calls"] + (e.get("background") or [])} for e in entries], f)


def outcomes(day: str, client: LeadSquaredClient | None = None) -> list[dict] | None:
    """The leads audited 7 days before ``day``: has each had a first-time move to Course Enrolled since its move?"""
    week = days_before(day, 7)
    p = f"{folder(week)}/leads.json"
    if not os.path.exists(p):
        return None
    out_path = f"{folder(day)}/outcomes.json"
    if os.path.exists(out_path):
        return json.load(open(out_path))
    c = client or LeadSquaredClient()
    out = []
    for e in json.load(open(p)):
        if report.status(e, f"{folder(week)}/reading")[0] != "ANALYZED":
            continue
        r = report.result_of(e, f"{folder(week)}/reading")
        h = (c.get_lead_activities(e["lead"]["ProspectID"], activity_event=dfind.STAGE_CHANGE, row_count=50) or {}).get("ProspectActivities") or []
        moves = dfind.stage_changes_to(h, ENROLLED)
        moved = utc(e["moved_utc"])
        enrolled = "Before the move (not first-time)" if any(t < moved for t in moves) else "Yes" if moves else "No"
        out.append({"lead": report.lead_name(e), "mobile": report.mobile(e), "caller": report.callers(e), "label": reading.relaxed_label(reading.totals(r["subscores"])[0]),
                    "moved": reading.ist(e["moved_utc"]), "enrolled": enrolled})
    json.dump(out, open(out_path, "w"), indent=1)
    return out


def finish_fetch(run: str, entries: list[dict], extra: dict) -> None:
    f = folder(run)
    counts = attach(entries, f)
    json.dump(entries, open(f"{f}/leads.json", "w"), indent=1)
    to_read = [e for e in entries if report.status(e, f"{f}/reading")[0] in ("PENDING_ANALYSIS", "ANALYSIS_FAILED")]
    packs = reading.prepare(to_read, f"{f}/reading")
    st = Counter(report.status(e, f"{f}/reading")[0] for e in entries)
    print(json.dumps({"leads": len(entries), **extra, **counts, **st, "to_read": len(to_read), "packs": packs}, indent=1))


def cmd_fetch(day: str) -> None:
    f = folder(day)
    entries = find.find(day, f)
    late = retry_earlier(day)
    keys = {reading.lead_key(y) for y in entries}
    entries += [x for x in late if reading.lead_key(x) not in keys]
    oc = outcomes(day)
    finish_fetch(day, entries, {"carried_over": len(late), "outcome_leads": None if oc is None else len(oc)})


def cmd_fetch_numbers(path: str) -> str:
    run = f"numbers_{now_ist().strftime('%Y-%m-%d')}"
    nums = [x.strip() for x in open(path).read().split() if x.strip()]
    entries = find.find_numbers(nums, folder(run))
    finish_fetch(run, entries, {"numbers": len(nums), "run": run})
    return run


def cmd_status(run: str) -> None:
    entries = load(run)
    st = Counter(report.status(e, f"{folder(run)}/reading")[0] for e in entries)
    reviewed = sum(1 for e in entries if (r := report.result_of(e, f"{folder(run)}/reading")) and "review" in r)
    print(json.dumps({"leads": len(entries), **st, "compliance_rechecked": reviewed}, indent=1))


def cmd_build(run: str, out: str, ddir: str) -> None:
    f = folder(run)
    entries = load(run)
    oc = json.load(open(f"{f}/outcomes.json")) if os.path.exists(f"{f}/outcomes.json") else None
    day = run if not run.startswith("numbers_") else run[len("numbers_"):]
    res = report.build(day, entries, f"{f}/reading", f"{f}/out", f"{BASE}/history.jsonl", ddir, oc,
                       None if day == run else f"numbers_{report.ddmmyyyy(day).replace('-', '')}")
    dest = f"{out}/{run}"
    os.makedirs(dest, exist_ok=True)
    for k in ("xlsx", "zip", "summary_pdf"):
        shutil.copy2(res[k], dest)
    print(json.dumps({**res, "copied_to": dest}, indent=1, default=str))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "fetch-numbers", "check", "status", "review", "build"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--out", default=REPORTS)
    ap.add_argument("--discovery", default="data/discovery_audit", help="the discovery audit's data folder, for its H/M/L")
    a = ap.parse_args(argv)
    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    if a.cmd == "fetch":
        cmd_fetch(a.args[0] if a.args else yesterday)
    elif a.cmd == "fetch-numbers":
        cmd_fetch_numbers(a.args[0])
    elif a.cmd == "check":
        run, path = a.args
        key = os.path.basename(path)[:-5]
        entry = next(x for x in load(run) if reading.lead_key(x) == key)
        bad = reading.check(path, entry)
        print("OK" if not bad else "\n".join(f"- {b}" for b in bad))
        sys.exit(1 if bad else 0)
    elif a.cmd == "status":
        cmd_status(a.args[0] if a.args else yesterday)
    elif a.cmd == "review":
        step, run = a.args[0], (a.args[1] if len(a.args) > 1 else yesterday)
        entries = load(run)
        if step == "prepare":
            print(review.prepare(entries, f"{folder(run)}/reading"))
        else:
            print(json.dumps(review.apply(entries, f"{folder(run)}/reading"), indent=1))
    elif a.cmd == "build":
        cmd_build(a.args[0] if a.args else yesterday, a.out, a.discovery)


if __name__ == "__main__":
    main()
