"""Daily plan pipeline. Runbook (what Claude does between the steps): docs/daily_plan.md.

    python -m analytics.daily_plan fetch   2026-10-12            # snapshot (4 days back) + enrolled leads
    python -m analytics.daily_plan prepare 2026-10-12            # transcripts, dossiers, reading batches
    #   ... Claude reads every batch -> data/daily/<date>/reads/out_*.jsonl, writes narrative.json ...
    python -m analytics.daily_plan build   2026-10-12 [--mail]   # rows, review, workbook, PDF, state
    python -m analytics.daily_plan status  2026-10-12 [--mail]   # 14:00 / 17:00 status check (fetches today)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from analytics.daily_plan import content, dossiers, mailer, pdf, recalc, review, rows, state, status, transcripts, workbook
from analytics.daily_plan.common import LEADER, TEAM, Snap, load_snapshots, previous_working_day
from integrations.timeutil import now_ist


def paths(date: str) -> dict:
    d = f"data/daily/{date}"
    return {"dir": d, "snap": f"{d}/snap.json", "enrolled": f"{d}/enrolled.json", "tx": f"{d}/tx", "dossiers": f"{d}/dossiers",
            "reads": f"{d}/reads", "rows": f"{d}/rows.json", "facts": f"{d}/facts.json", "narrative": f"{d}/narrative.json",
            "content": f"{d}/content.json", "prepare": f"{d}/prepare.json", "out": f"exports/daily/{date}"}


def _snap(p: dict) -> Snap:
    return Snap(load_snapshots([p["snap"]]))


def cmd_fetch(a):
    from analytics.daily_plan import fetch

    p = paths(a.date)
    os.makedirs(p["dir"], exist_ok=True)
    fetch.snapshot(a.date, a.days_back, p["snap"], a.team)
    since = (datetime.strptime(a.date, "%Y-%m-%d").toordinal() - a.days_back)
    fetch.enrolled(datetime.fromordinal(since).strftime("%Y-%m-%d"), p["enrolled"])


def cmd_prepare(a):
    p = paths(a.date)
    snap = _snap(p)
    prev = previous_working_day(snap, a.date)
    st = state.load(a.state_dir, prev)
    prev_plan = state.resolve(st, snap.leads)
    sel = dossiers.select(snap, prev, prev_plan)
    nums = transcripts.numbers_for(snap, prev, extra_lead_ids=sel)
    tx = transcripts.fetch(nums, p["tx"], pause=a.pause) if not a.no_transcripts else transcripts.load(p["tx"])
    have, missing = transcripts.coverage(tx, nums)
    index = dossiers.build(snap, prev, sel, prev_plan, tx, p["dossiers"])
    os.makedirs(p["reads"], exist_ok=True)
    files = []
    for k, b in enumerate(dossiers.batches(index, a.batches)):
        f = os.path.join(p["reads"], f"batch_{k}.txt")
        open(f, "w").write("\n".join(os.path.join(p["dossiers"], f"{i['lead_id']}.txt") for i in b))
        files.append(f)
    info = {"date": a.date, "previous_day": prev, "plan_found": bool(st), "dossiers": len(index), "batches": files,
            "transcript_numbers": len(nums), "with_transcript": have, "without_transcript": missing}
    json.dump(info, open(p["prepare"], "w"), indent=1)
    print(json.dumps(info, indent=1))


def cmd_build(a):
    p = paths(a.date)
    snap = _snap(p)
    info = json.load(open(p["prepare"])) if os.path.exists(p["prepare"]) else {}
    prev = info.get("previous_day") or previous_working_day(snap, a.date)
    before = previous_working_day(snap, prev)
    prev_plan = state.resolve(state.load(a.state_dir, prev), snap.leads)
    reads = rows.load_reads(p["reads"])
    enrolled = json.load(open(p["enrolled"])) if os.path.exists(p["enrolled"]) else []
    R = rows.build_rows(snap, a.date, reads, prev_plan, enrolled)
    F = review.facts(snap, prev, before, prev_plan, reads, enrolled)
    N = json.load(open(p["narrative"])) if os.path.exists(p["narrative"]) else None
    C = content.build(R, F, N, a.team, a.leader)
    if info:
        C["data_note"] = (f"Data: LeadSquared pulled {R['built_at']} IST. Salesa transcripts: {info.get('transcript_numbers')} numbers searched "
                          f"(≤10 numbers per request, ≤9 requests per run); {info.get('with_transcript')} had a transcript, {info.get('without_transcript')} did not. "
                          "Zipteams notes cross-checked, never used alone. An enrollment is a lead in Course Enrolled or one who said on a call that they paid. Chances are estimates.")
    for k, v in (("rows", R), ("facts", F), ("content", C)):
        json.dump(v, open(p[k], "w"), indent=1, default=str)
    os.makedirs(p["out"], exist_ok=True)
    stem = os.path.join(p["out"], f"Elite_call_plan_{datetime.strptime(a.date, '%Y-%m-%d'):%a_%-d_%b}")
    workbook.write(R, C, stem + ".xlsx")
    n, errors = recalc.check(stem + ".xlsx")
    if errors:
        sys.exit(f"workbook has {errors} formula errors out of {n}; not sending")
    pdf.render(pdf.html_doc(R, C), stem + ".html", stem + ".pdf")
    st = state.to_state(a.date, R["by_owner"], C["priority"])
    print(f"built {stem}.pdf/.xlsx ({n} formulas, 0 errors); state -> {state.save(st, a.state_dir)}")
    if a.publish_state:
        state.publish(a.state_dir, a.date)
    if a.mail:
        RV = C["review"]
        body = (f"<p>{RV['headline']}</p><p>Today's plan and the review of {RV['day_label']} are attached. "
                f"{len(C['priority'])} leads on {a.leader.split()[0]}'s priority list.</p>")
        mailer.send(f"{a.team}: plan for {C['day_label']} + review of {RV['day_label']}", body,
                    f"{a.team}: plan for {C['day_label']} and review of {RV['day_label']} attached.", mailer.recipients("REPORT_TO"),
                    [stem + ".pdf", stem + ".xlsx"])
        summary = "<h3>Scorecard</h3><table border=1 cellpadding=4>" + "".join(
            f"<tr><td>{r[0]}</td><td>{r[2]}</td></tr>" for r in RV["scorecard"]) + "</table>"
        mailer.send(f"{a.team}: {RV['day_label']} summary", f"<p>{RV['headline']}</p>{summary}", "See the HTML version.",
                    mailer.recipients("REPORT_TO_SUMMARY"))


def cmd_status(a):
    p = paths(a.date)
    at = now_ist().strftime("%H%M")
    snap_path = f"{p['dir']}/status_{at}.json"
    enr_path = f"{p['dir']}/status_{at}_enrolled.json"
    if not a.no_fetch:
        from analytics.daily_plan import fetch

        os.makedirs(p["dir"], exist_ok=True)
        fetch.snapshot(a.date, 0, snap_path, a.team)
        fetch.enrolled(a.date, enr_path)
    else:
        snap_path, enr_path = a.snapshot or p["snap"], a.enrolled or p["enrolled"]
    snap = Snap(load_snapshots([snap_path]))
    plan = state.resolve(state.load(a.state_dir, a.date), snap.leads)
    enrolled = json.load(open(enr_path)) if os.path.exists(enr_path) else []
    S = status.build(snap, a.date, plan, enrolled, a.leader)
    os.makedirs(p["out"], exist_ok=True)
    stem = os.path.join(p["out"], f"Elite_status_{S['at'].replace(':', '')}")
    pdf.render(status.html_doc(S, a.team), stem + ".html", stem + ".pdf")
    text = status.text_summary(S, a.team)
    print(text)
    print(f"status -> {stem}.pdf")
    if a.mail:
        mailer.send(f"{a.team}: status at {S['at']}", "<pre style='font-family:Arial'>" + text + "</pre>", text,
                    mailer.recipients("REPORT_TO"), [stem + ".pdf"])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m analytics.daily_plan")
    ap.add_argument("--team", default=TEAM)
    ap.add_argument("--leader", default=LEADER)
    ap.add_argument("--state-dir", default="plan_state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("date"); f.add_argument("--days-back", type=int, default=4)
    pr = sub.add_parser("prepare"); pr.add_argument("date"); pr.add_argument("--pause", type=int, default=60)
    pr.add_argument("--batches", type=int, default=8); pr.add_argument("--no-transcripts", action="store_true")
    b = sub.add_parser("build"); b.add_argument("date"); b.add_argument("--mail", action="store_true"); b.add_argument("--publish-state", action="store_true")
    s = sub.add_parser("status"); s.add_argument("date"); s.add_argument("--mail", action="store_true"); s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--snapshot"); s.add_argument("--enrolled")
    a = ap.parse_args(argv)
    {"fetch": cmd_fetch, "prepare": cmd_prepare, "build": cmd_build, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
