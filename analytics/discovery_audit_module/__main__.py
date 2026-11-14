"""discovery-audit-module. Discovery call audit: every lead moved to "Discovery Call Done" on a day, all of that day's calls read together
by Claude against Final_Prompt_Oct_2026, as an audit workbook and one feedback PDF per lead.

Steps (data/discovery_audit/<day>/ holds everything with lead details; git-ignored):
  fetch   find the leads and their calls (LeadSquared, read-only), get transcripts, write the reading packs
  check   validate one result file written by a Claude reader (prints OK or the problems)
  status  how many leads are found, read, checked, re-checked
  review  prepare | apply the second-reading compliance check
  build   the workbook, PDFs, ZIP and CSV; copied to the project's reports folder

    python -m analytics.discovery_audit_module fetch 2026-11-13
    python -m analytics.discovery_audit_module check 2026-11-13 data/discovery_audit/2026-11-13/reading/results/KEY.json
    python -m analytics.discovery_audit_module review prepare 2026-11-13
    python -m analytics.discovery_audit_module build 2026-11-13 [--out /mnt/project-files/reports/discovery_audit]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import timedelta

from analytics.discovery_audit_module import find, reading, report, review, transcripts
from integrations.timeutil import now_ist

BASE = "data/discovery_audit"
REPORTS = "/mnt/project-files/reports/discovery_audit"


def folder(day: str) -> str:
    return f"{BASE}/{day}"


def load(day: str) -> list[dict]:
    return json.load(open(f"{folder(day)}/leads.json"))


def transcript_of(day: str, key: str) -> str:
    e = next(x for x in load(day) if reading.lead_key(x) == key)
    return reading.day_text(e)


def retry_earlier(day: str, days_back: int = 2) -> list[dict]:
    """Leads from the last two runs still waiting for a transcript: tried again and carried into this run."""
    out = []
    for k in range(1, days_back + 1):
        d = find.prev_day(day) if k == 1 else find.prev_day(find.prev_day(day))
        p = f"{folder(d)}/leads.json"
        if os.path.exists(p):
            for e in json.load(open(p)):
                if report.status(e, f"{folder(d)}/reading")[0] == "TRANSCRIPT_NOT_FOUND":
                    out.append({**e, "note": (e.get("note") + " " if e.get("note") else "") + f"Carried over from the {d} run (transcript was late)."})
    return out


def cmd_fetch(day: str) -> None:
    f = folder(day)
    entries = find.find(day, f)
    late = retry_earlier(day)
    entries += [x for x in late if reading.lead_key(x) not in {reading.lead_key(y) for y in entries}]
    counts = transcripts.attach(entries, f)
    json.dump(entries, open(f"{f}/leads.json", "w"), indent=1)
    to_read = [e for e in entries if report.status(e, f"{f}/reading")[0] in ("PENDING_ANALYSIS", "ANALYSIS_FAILED")]
    packs = reading.prepare(to_read, f"{f}/reading")
    print(json.dumps({"leads": len(entries), "carried_over": len(late), **counts, "to_read": len(to_read), "packs": packs}, indent=1))


def cmd_status(day: str) -> None:
    entries = load(day)
    st = Counter(report.status(e, f"{folder(day)}/reading")[0] for e in entries)
    reviewed = sum(1 for e in entries if (p := f"{folder(day)}/reading/results/{reading.lead_key(e)}.json")
                   and reading.finished(p) and "review" in json.load(open(p)))
    print(json.dumps({"leads": len(entries), **st, "compliance_rechecked": reviewed}, indent=1))


def cmd_build(day: str, out: str) -> None:
    f = folder(day)
    res = report.build(day, load(day), f"{f}/reading", f"{f}/out", f"{BASE}/history.jsonl")
    dest = f"{out}/{day}"
    os.makedirs(dest, exist_ok=True)
    for k in ("xlsx", "zip", "summary_pdf"):
        shutil.copy2(res[k], dest)
    print(json.dumps({**res, "copied_to": dest}, indent=1, default=str))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "check", "status", "review", "build"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--out", default=REPORTS)
    a = ap.parse_args(argv)
    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    if a.cmd == "fetch":
        cmd_fetch(a.args[0] if a.args else yesterday)
    elif a.cmd == "check":
        day, path = a.args
        bad = reading.check(path, transcript_of(day, os.path.basename(path)[:-5]))
        print("OK" if not bad else "\n".join(f"- {b}" for b in bad))
        sys.exit(1 if bad else 0)
    elif a.cmd == "status":
        cmd_status(a.args[0] if a.args else yesterday)
    elif a.cmd == "review":
        step, day = a.args[0], (a.args[1] if len(a.args) > 1 else yesterday)
        entries = load(day)
        if step == "prepare":
            print(review.prepare(entries, f"{folder(day)}/reading"))
        else:
            print(json.dumps(review.apply(entries, f"{folder(day)}/reading"), indent=1))
    elif a.cmd == "build":
        cmd_build(a.args[0] if a.args else yesterday, a.out)


if __name__ == "__main__":
    main()
