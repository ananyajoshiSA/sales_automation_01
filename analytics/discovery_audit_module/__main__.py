"""discovery-audit-module. Discovery call audit: every lead moved to "Discovery Call Done" on a day, all of that day's calls read together
by Claude against Final_Prompt_Oct_2026, as an audit workbook and one feedback PDF per lead.

Steps (data/discovery_audit/<day>/ holds everything with lead details; git-ignored):
  fetch   find the leads and their calls (LeadSquared, read-only), get transcripts, write the reading packs
  check   validate one result file written by a Claude reader (prints OK or the problems)
  status  how many leads are found, read, checked, re-checked
  review  prepare | apply the second-reading compliance check
  hold    mark leads "under review" when only the owner can decide about them (the email still goes on time; hold.py)
  build   the workbook, PDFs, ZIP and CSV; copied to the project's reports folder. Writes attention.txt for the email
          (empty on a normal day): pass it to scripts/email_report.py --attention-file

    python -m analytics.discovery_audit_module fetch 2026-11-13
    python -m analytics.discovery_audit_module check 2026-11-13 data/discovery_audit/2026-11-13/reading/results/KEY.json
    python -m analytics.discovery_audit_module review prepare 2026-11-13
    python -m analytics.discovery_audit_module hold 2026-11-17 --reason "moved in one batch by X, 13:04-13:41" --moved-between 13:04-13:41
    python -m analytics.discovery_audit_module hold 2026-11-17 [KEY ...] --clear | --list
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

from analytics.discovery_audit_module import find, hold, reading, report, review, transcripts
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
    """Leads from the last two runs still waiting for a transcript, carried into this run until their third try."""
    last: dict[str, dict] = {}
    for k in range(days_back, 0, -1):  # oldest first, so the latest run's copy (and its try count) wins
        d = day
        for _ in range(k):
            d = find.prev_day(d)
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
                        "note": (e.get("note") + " " if e.get("note") else "") + f"Carried over from the {run} run (transcript was late)."})
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
                   and reading.finished(p, reading.day_text(e)) and "review" in json.load(open(p)))
    print(json.dumps({"leads": len(entries), **st, "compliance_rechecked": reviewed}, indent=1))


def cmd_hold(day: str, keys: list[str], reason: str, window: str, clear: bool, show: bool) -> None:
    f = folder(day)
    entries = load(day)
    if show:
        print(json.dumps(hold.load(f), indent=1))
        return
    if clear:
        print(json.dumps({"under_review": len(hold.clear(f, keys or None))}, indent=1))
        return
    try:
        if window:  # a batch of stage changes: only the leads moved without a call are held
            moved = [x for x in entries if report.status(x, f"{f}/reading")[0] == "MOVED_WITHOUT_CALL"]
            keys = keys + [reading.lead_key(x) for x in hold.moved_between(moved, window, "stage_changed_utc")]
        held = hold.add(f, reason, list(dict.fromkeys(keys)), {reading.lead_key(x) for x in entries})
    except ValueError as exc:
        sys.exit(f"hold: {exc}")
    print(json.dumps({"under_review": len(held), "reasons": dict(hold.groups(held, list(held)))}, indent=1))


def cmd_build(day: str, out: str) -> None:
    f = folder(day)
    res = report.build(day, load(day), f"{f}/reading", f"{f}/out", f"{BASE}/history.jsonl", hold.load(f), hold.late_note(day))
    dest = f"{out}/{day}"
    os.makedirs(dest, exist_ok=True)
    for k in ("xlsx", "zip", "summary_pdf"):
        shutil.copy2(res[k], dest)
    note = hold.write_note(f, res["attention"])
    print(json.dumps({**res, "copied_to": dest, "attention_file": note}, indent=1, default=str))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "check", "status", "review", "hold", "build"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--out", default=REPORTS)
    ap.add_argument("--reason", default="", help="hold: why these leads are under review (shown to readers)")
    ap.add_argument("--moved-between", default="", metavar="HH:MM-HH:MM", help="hold: the leads moved without a call in this IST window")
    ap.add_argument("--clear", action="store_true", help="hold: release these leads (all, if none named)")
    ap.add_argument("--list", action="store_true", help="hold: show the leads under review")
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
    elif a.cmd == "hold":
        cmd_hold(a.args[0], a.args[1:], a.reason, a.moved_between, a.clear, a.list)
    elif a.cmd == "build":
        cmd_build(a.args[0] if a.args else yesterday, a.out)


if __name__ == "__main__":
    main()
