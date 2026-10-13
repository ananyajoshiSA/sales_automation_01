"""Daily plan pipeline. Runbook (what Claude does between the steps): docs/daily_plan.md.

    python -m analytics.daily_plan fetch   2026-10-12 [--full]   # snapshot (4 days back) + enrolled leads, reusing the last pull
    python -m analytics.daily_plan prepare 2026-10-12            # transcripts, dossiers, reading batches
    #   ... Claude reads every batch -> data/daily/<date>/reads/out_*.jsonl, writes narrative.json ...
    python -m analytics.daily_plan build   2026-10-12 [--mail]   # rows, review, workbook, PDF, state
    python -m analytics.daily_plan status  2026-10-12 [--mail]   # 14:00 / 17:00 status check (fetches today)
    python -m analytics.daily_plan revival-prepare 2026-10-13 --start 2026-07-01 --end 2026-09-20   # older leads to revive
    python -m analytics.daily_plan revival-build 2026-10-13 [--publish-state]
    python -m analytics.daily_plan share exports/daily/<date>/<report>.html   # copy without phone numbers for email/Drive
    python -m analytics.daily_plan fetch-month 2026-10-12        # month-to-date snapshot for the month lead flow (pipeline-prepare runs it)
    python -m analytics.daily_plan fetch 2026-10-12 --days-back 3 && python -m analytics.daily_plan pipeline-prepare 2026-10-12
    #   ... Claude reads every batch -> data/daily/<date>/pipeline/reads/out_*.jsonl ...
    python -m analytics.daily_plan pipeline-build 2026-10-12 [--mail] [--publish-state]   # nightly pipeline to month end
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from analytics.daily_plan import content, dossiers, leadflow, mailer, pdf, pipeline, recalc, review, rows, state, status, transcripts, workbook
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
    since = datetime.fromordinal(datetime.strptime(a.date, "%Y-%m-%d").toordinal() - a.days_back).strftime("%Y-%m-%d")
    prev = None if a.full else fetch.latest_snapshot(start=since)
    cache = {} if a.full else fetch.cached_enrolled()
    fetch.snapshot(a.date, a.days_back, p["snap"], a.team, prev)
    fetch.enrolled(since, p["enrolled"], cache=cache)


def flow_sections(snap: Snap, day: str, today: str, day_label: str) -> str:
    """Lead flow for ``day`` (from the daily snapshot) and month to date (from the month snapshot, when one exists)."""
    H = [leadflow.section(f"Lead flow: leads that came in on {day_label}", leadflow.rows(snap, day, day), by_caller=True)]
    M = load_month(today)
    if M is None:
        H.append("<p class='small'>Month-to-date lead flow: no month pull yet (it is refreshed by the nightly run).</p>")
        return "".join(H)
    month = leadflow.rows(M, today[:8] + "01", today)
    H.append(leadflow.section(f"Lead flow: leads received this month (1 to {int(today[8:])} {datetime.strptime(today, '%Y-%m-%d'):%b}, "
                              f"data to {M.fetched:%a %H:%M} IST)", month, leads=False, by_caller=True))
    cold = [r for r in month if not r["reached"]]
    if cold:
        H.append(f"<h3>This month's leads never reached ({len(cold)}): never dialled first, then oldest</h3>" + leadflow.lead_table(cold, limit=80))
    return "".join(H)


def cmd_share(a):
    from analytics.daily_plan import share

    for h in a.html:
        out = share.write(h, a.doc)
        print(f"{out} ({os.path.getsize(out) // 1024} KB)")


def month_path(date: str) -> str:
    return f"data/daily/month/{date[:7]}.json"


def cmd_fetch_month(a):
    """Team snapshot from the 1st of the month to ``date``, refreshed from the last month pull: feeds the month lead flow."""
    from analytics.daily_plan import fetch

    out = month_path(a.date)
    days = int(a.date[8:]) - 1
    prev = out if os.path.exists(out) and not a.full else None
    tmp = out + ".tmp"
    fetch.snapshot(a.date, days, tmp, a.team, prev)
    os.replace(tmp, out)


def load_month(date: str) -> Snap | None:
    """The month snapshot if one exists for ``date``'s month (the leads that came in this month, with all their calls)."""
    p = month_path(date)
    return Snap(load_snapshots([p])) if os.path.exists(p) else None


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
    C["leadflow_html"] = flow_sections(snap, prev, a.date, f"{datetime.strptime(prev, '%Y-%m-%d'):%A %-d %b}")
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
        summary = "<h3>Scorecard</h3><table border=1 cellpadding=4 style='border-collapse:collapse'>" + "".join(
            f"<tr><td>{r[0]}</td><td>{r[2]}</td></tr>" for r in RV["scorecard"]) + "</table>"
        top = "".join(f"<li>{p['name']} ({p['owner'].split()[0]}, {p['chance']}%, check by {p['check_by']})</li>" for p in C["priority"][:5])
        body = (f"<p>{RV['headline']}</p>{summary}<h3>{a.leader.split()[0]}'s priority list: {len(C['priority'])} leads, top 5</h3><ol>{top}</ol>"
                f"<p>The full plan (PDF) and the call sheets (workbook) are attached.</p>")
        mailer.send(f"{a.team}: plan for {C['day_label']} + review of {RV['day_label']}", body,
                    f"{a.team}: plan for {C['day_label']} and review of {RV['day_label']} attached.", mailer.recipients("REPORT_TO"),
                    [stem + ".pdf", stem + ".xlsx"])
        if mailer.recipients("REPORT_TO_SUMMARY"):  # leadership copy without lead details, only when a list is set
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
        fetch.snapshot(a.date, 0, snap_path, a.team, None if a.full else fetch.latest_snapshot(start=a.date))
        fetch.enrolled(a.date, enr_path, cache={} if a.full else fetch.cached_enrolled())
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


def cmd_revival_prepare(a):
    """Full history of the period's leads, shortlist of those with a real conversation, transcripts, dossiers, batches."""
    from analytics.daily_plan import revival

    p = paths(a.date)
    d = os.path.join(p["dir"], "revival")
    os.makedirs(os.path.join(d, "reads"), exist_ok=True)
    base = json.load(open(p["snap"]))
    ids = revival.pool(Snap(base), a.start, a.end)
    print(f"pool: {len(ids)} leads created {a.start} to {a.end}", file=sys.stderr)
    hist = revival.history(ids, os.path.join(d, "history.jsonl"))
    raw = revival.snapshot(base, {k: hist[k] for k in ids})
    json.dump(raw, open(os.path.join(d, "snap.json"), "w"))
    snap = Snap(raw)
    short, skipped = revival.focus(snap, ids, a.since)
    nums = revival.numbers(snap, short)
    tx = transcripts.fetch(nums, os.path.join(d, "tx"), pause=a.pause)
    have, missing = transcripts.coverage(tx, nums)
    index = dossiers.build(snap, a.date, set(short), {}, tx, os.path.join(d, "dossiers"))
    files = []
    for k, b in enumerate(revival.batches(index)):
        f = os.path.join(d, "reads", f"batch_{k}.txt")
        open(f, "w").write("\n".join(os.path.join(d, "dossiers", f"{x['lead_id']}.txt") for x in b) + "\n")
        files.append(f)
    info = {"date": a.date, "period": f"{a.start} to {a.end}", "pool": len(ids), "talked": len(short), "skipped": skipped, "dossiers": len(index),
            "batches": files, "transcript_numbers": len(nums), "with_transcript": have, "without_transcript": missing}
    json.dump(info, open(os.path.join(d, "prepare.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "batches"} | {"batches": len(files)}, indent=1))


def cmd_revival_build(a):
    from analytics.daily_plan import revival

    p = paths(a.date)
    d = os.path.join(p["dir"], "revival")
    info = json.load(open(os.path.join(d, "prepare.json")))
    rows = revival.load(os.path.join(d, "reads"))
    s, e = info["period"].split(" to ")
    period = f"{datetime.strptime(s, '%Y-%m-%d'):%-d %b} to {datetime.strptime(e, '%Y-%m-%d'):%-d %b %Y}"
    meta = {"period": period, "built": now_ist().strftime("%a %-d %b %H:%M"), "pool": info["pool"], "talked": info["talked"],
            "skipped": info.get("skipped", {})}
    os.makedirs(p["out"], exist_ok=True)
    stem = os.path.join(p["out"], "Elite_revival_list")
    pdf.render(revival.html_doc(rows, meta, a.team), stem + ".html", stem + ".pdf")
    revival.workbook(rows, stem + ".xlsx")
    keep = revival.ranked(rows)
    print(f"revival: {len(rows)} read, {len(keep)} to revive -> {stem}.pdf")
    path = state.save(revival.to_state(a.date, rows), a.state_dir, "revival_")
    print(f"state -> {path}")
    if a.publish_state:
        state.publish(a.state_dir, f"revival {a.date}")


def cmd_pipeline_prepare(a):
    p = paths(a.date)
    d = os.path.join(p["dir"], "pipeline")
    a.full = False
    cmd_fetch_month(a)  # month lead flow for tonight's pipeline and tomorrow's morning report (about a minute once seeded)
    snap = _snap(p)
    last = state.latest_before(a.state_dir, a.date, "pipeline_")
    last_night = state.resolve(last, snap.leads)
    plan = state.resolve(state.load(a.state_dir, a.date), snap.leads)
    allocated = pipeline.fresh(snap, a.date)
    cand = pipeline.candidates(snap, a.date, last_night, plan, allocated)
    nums = pipeline.numbers(snap, cand)
    tx = transcripts.fetch(nums, os.path.join(d, "tx"), pause=a.pause)
    have, missing = transcripts.coverage(tx, nums)
    index = dossiers.build(snap, a.date, cand, plan, tx, os.path.join(d, "dossiers"))
    for i in index:
        e = last_night.get(i["lead_id"])
        if e:
            open(os.path.join(d, "dossiers", f"{i['lead_id']}.txt"), "a").write(
                f"\n\nLAST NIGHT'S PIPELINE ({last['date']}): chance {e['month_chance']}%, stage {e['stage']}")
    end = pipeline.horizon(a.date)
    open(os.path.join(d, "brief.md"), "w").write(pipeline.brief(open("docs/pipeline_brief.md").read(), a.date, end))
    os.makedirs(os.path.join(d, "reads"), exist_ok=True)
    n = max(1, min(8, -(-sum(i["size"] for i in index) // 220_000)))
    files = []
    for k, b in enumerate(dossiers.batches(index, n)):
        f = os.path.join(d, "reads", f"batch_{k}.txt")
        open(f, "w").write("\n".join(os.path.join(d, "dossiers", f"{x['lead_id']}.txt") for x in b))
        files.append(f)
    info = {"date": a.date, "horizon": end, "candidates": len(cand), "fresh_allocated": len(allocated), "last_night": last["date"] if last else None,
            "brief": os.path.join(d, "brief.md"), "batches": files, "with_transcript": have, "without_transcript": missing}
    json.dump(info, open(os.path.join(d, "prepare.json"), "w"), indent=1)
    print(json.dumps(info, indent=1))


def cmd_pipeline_build(a):
    p = paths(a.date)
    d = os.path.join(p["dir"], "pipeline")
    rws = pipeline.load(os.path.join(d, "reads"))
    last = state.latest_before(a.state_dir, a.date, "pipeline_")
    snap = _snap(p)
    F = pipeline.fresh_outcomes(snap, a.date, pipeline.fresh(snap, a.date), rws)
    res = pipeline.build(rws, a.date, now_ist().strftime("%a %-d %b %H:%M"), (last or {}).get("leads", {}), p["out"], a.team, F,
                         flow_sections(snap, a.date, a.date, datetime.strptime(a.date, "%Y-%m-%d").strftime("%a %-d %b") + " (today)"))
    inc = pipeline.included(rws)
    print(f"pipeline -> {state.save(pipeline.to_state(a.date, inc), a.state_dir, 'pipeline_')}; {res['pdf']}")
    S = res["summary"]
    FT = {k: sum(v[k] for v in F["callers"].values()) for k in ("allocated", "new", "real", "followup", "pipeline")}
    text = (f"{a.team}: pipeline to {res['horizon']} — {S['leads']} leads, expected {S['expected_low']:.0f}–{S['expected_high']:.0f} enrollments (estimate).\n"
            f"Fresh leads allocated today: {FT['allocated']} ({FT['new']} new); {FT['real']} real conversations, {FT['followup']} follow-ups fixed, "
            f"{FT['pipeline']} into the pipeline.\n"
            + "\n".join(f"{i}. {r.get('name')} ({r.get('owner')}) {r['month_chance']}% · {r.get('expected_window')} · {r.get('next_step')}"
                        for i, r in enumerate(res["top"], 1)))
    print(text)
    if a.publish_state:
        state.publish(a.state_dir, f"pipeline {a.date}")
    if a.mail:
        mailer.send(f"{a.team}: enrollment pipeline to {res['horizon']}", "<pre style='font-family:Arial'>" + html_escape(text) + "</pre>", text,
                    mailer.recipients("REPORT_TO"), [res["pdf"], res["xlsx"]])


def html_escape(s: str) -> str:
    import html

    return html.escape(s)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m analytics.daily_plan")
    ap.add_argument("--team", default=TEAM)
    ap.add_argument("--leader", default=LEADER)
    ap.add_argument("--state-dir", default="plan_state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("date"); f.add_argument("--days-back", type=int, default=4)
    f.add_argument("--full", action="store_true", help="ignore earlier pulls and read everything again")
    pr = sub.add_parser("prepare"); pr.add_argument("date"); pr.add_argument("--pause", type=int, default=60)
    pr.add_argument("--batches", type=int, default=8); pr.add_argument("--no-transcripts", action="store_true")
    b = sub.add_parser("build"); b.add_argument("date"); b.add_argument("--mail", action="store_true"); b.add_argument("--publish-state", action="store_true")
    s = sub.add_parser("status"); s.add_argument("date"); s.add_argument("--mail", action="store_true"); s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--snapshot"); s.add_argument("--enrolled"); s.add_argument("--full", action="store_true")
    rp = sub.add_parser("revival-prepare"); rp.add_argument("date"); rp.add_argument("--start", required=True); rp.add_argument("--end", required=True)
    rp.add_argument("--since", default="2026-09-21", help="leads with a real conversation from this day on are in the daily reports")
    rp.add_argument("--pause", type=int, default=60)
    rb = sub.add_parser("revival-build"); rb.add_argument("date"); rb.add_argument("--publish-state", action="store_true")
    sh = sub.add_parser("share"); sh.add_argument("html", nargs="+"); sh.add_argument("--doc", action="store_true")
    fm = sub.add_parser("fetch-month"); fm.add_argument("date"); fm.add_argument("--full", action="store_true")
    pp = sub.add_parser("pipeline-prepare"); pp.add_argument("date"); pp.add_argument("--pause", type=int, default=60)
    pb = sub.add_parser("pipeline-build"); pb.add_argument("date"); pb.add_argument("--mail", action="store_true"); pb.add_argument("--publish-state", action="store_true")
    a = ap.parse_args(argv)
    {"revival-prepare": cmd_revival_prepare, "revival-build": cmd_revival_build, "share": cmd_share, "fetch": cmd_fetch, "fetch-month": cmd_fetch_month, "prepare": cmd_prepare, "build": cmd_build, "status": cmd_status,
     "pipeline-prepare": cmd_pipeline_prepare, "pipeline-build": cmd_pipeline_build}[a.cmd](a)


if __name__ == "__main__":
    main()
