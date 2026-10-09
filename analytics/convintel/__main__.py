"""Command line for conversation intelligence (docs/conversation_intelligence.md). Read-only against LeadSquared and
the transcript API; the model is called only by ``analyze --layer semantic --limit N`` (or ``run --semantic-limit
N``) once a key is configured, and D1 is written only by ``analytics.convintel.d1push --push --yes``.

    python -m analytics.convintel status [FROM TO]
    python -m analytics.convintel inventory FROM TO [--window 14:00-14:30]
    python -m analytics.convintel fetch-transcripts [--requests 9] [--pace 7]
    python -m analytics.convintel analyze [--layer keyword|semantic|all] [--limit N] [--mode sync|batch] [--workers 4]
    python -m analytics.convintel poll-batches
    python -m analytics.convintel retry [FROM TO]
    python -m analytics.convintel reconcile FROM TO
    python -m analytics.convintel estimate [FROM TO] [--batch]
    python -m analytics.convintel report FROM TO [--key 7d] [--excerpts] [--accountability DIR] [--out DIR]
    python -m analytics.convintel run FROM TO [--fetch-runs 1] [--semantic-limit 0] [--report] [--loop --every 300]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta

from analytics.convintel import schema as S
from analytics.convintel.store import DEFAULT_PATH, Registry
from integrations.timeutil import EDIT_MARGIN, IST, ist_day, ist_day_start, now_utc, utc

REREAD_TODAY = timedelta(minutes=5)
REREAD_RECENT = timedelta(hours=6)


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def _ist(s: str | None) -> str:
    t = utc(s)
    return t.astimezone(IST).strftime("%d %b %Y %H:%M IST") if t else "never"


def print_status(reg: Registry, day_from: str | None, day_to: str | None) -> dict:
    cov = reg.coverage(day_from, day_to)
    blocked = reg.get_meta("blocked_layers", {})
    print(f"Calls: {cov['total_calls']}  ({', '.join(f'{k} {v}' for k, v in cov['by_class'].items())})")
    print(f"Expected transcripts: {cov['expected_transcripts']}   found: {cov['transcripts_found']}   "
          f"analysed: {cov['analyzed']}   coverage: {cov['coverage_pct'] if cov['coverage_pct'] is not None else '-'}%")
    for s in S.STATUSES:
        print(f"  {s:<24} {cov['by_status'][s]}")
    print("Transcript search: " + ", ".join(f"{k} {v}" for k, v in cov["by_transcript_state"].items()))
    for layer, why in blocked.items():
        print(f"{layer} layer waiting: {why}")
    last = reg.q("SELECT kind, finished_utc, status FROM processing_runs ORDER BY run_id DESC LIMIT 5")
    for r in last:
        print(f"  last {r['kind']}: {r['status']} at {_ist(r['finished_utc'])}")
    return cov


def days_due(reg: Registry, day_from: str, day_to: str, now) -> list[str]:
    """Days to (re)read: never read, or still inside LeadSquared's edit margin and last read a while ago."""
    from analytics.convintel.sources import days
    out = []
    for d in days(day_from, day_to):
        src = reg.get_meta(f"source:{d}")
        if not src or src.get("window"):
            out.append(d)
            continue
        read = utc(src["read_utc"])
        final_after = ist_day_start(d) + timedelta(days=1) + EDIT_MARGIN
        every = REREAD_TODAY if d == ist_day(now) else REREAD_RECENT
        if read < final_after and now - read >= every:
            out.append(d)
    return out


def cycle(reg: Registry, a, lsq=None) -> dict:
    """One pass of the continuous pipeline: inventory what is due, search transcripts, analyse, reconcile."""
    from analytics.convintel.analyze import keyword_pass, poll_batches, semantic_pass
    from analytics.convintel.fetch import run_fetch
    from analytics.convintel.inventory import inventory
    from analytics.convintel.reconcile import reconcile
    now = now_utc()
    out = {}
    due = days_due(reg, a.start, a.end, now)
    if due:
        from integrations.leadsquared import LeadSquaredClient
        lsq = lsq or LeadSquaredClient()
        users = lsq.get_users()
        for d in due:
            out[f"inventory {d}"] = dict(inventory(reg, lsq, d, d, now_utc(), users=users, log=log))
    for i in range(a.fetch_runs):
        if i:
            time.sleep(60)          # the transcript API counts requests per minute across runs
        out[f"fetch {i + 1}"] = dict(run_fetch(reg, now_utc(), requests=a.requests, pace_s=a.pace, log=log))
    out["keyword"] = dict(keyword_pass(reg, now_utc(), log=log))
    out["semantic"] = dict(semantic_pass(reg, now_utc(), a.semantic_limit, mode=a.mode, workers=a.workers, log=log))
    if reg.open_batches(S.SEMANTIC):
        out["batches"] = dict(poll_batches(reg, now_utc(), log=log))
    checks = reconcile(reg, a.start, a.end, now_utc(), verify_files=False)
    out["reconciliation"] = {c["check"]: c["ok"] for c in checks}
    if a.report:
        from analytics.convintel.export import run_report
        out["report"] = run_report(reg, a.start, a.end, now_utc(), key=a.key, excerpts=a.excerpts, out_dir=a.out,
                                   accountability_dir=a.accountability, log=log)
    return out


def main(argv: list[str] | None = None) -> None:
    from integrations.env import load_dotenv
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m analytics.convintel", description=__doc__.splitlines()[0])
    p.add_argument("--db", default=DEFAULT_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)

    def period(sp, required=True):
        sp.add_argument("start", nargs=None if required else "?")
        sp.add_argument("end", nargs=None if required else "?")

    sp = sub.add_parser("status")
    period(sp, False)
    sp = sub.add_parser("inventory")
    period(sp)
    sp.add_argument("--window", help="IST 'HH:MM-HH:MM' part of each day, to validate on a small sample")
    for name in ("fetch-transcripts", "run"):
        sp = sub.add_parser(name)
        if name == "run":
            period(sp)
            sp.add_argument("--fetch-runs", type=int, default=1, help="transcript runs per cycle (each at most 9 requests)")
            sp.add_argument("--semantic-limit", type=int, default=0, help="calls sent to the model per cycle (0 = none)")
            sp.add_argument("--mode", choices=("sync", "batch"), default="batch")
            sp.add_argument("--workers", type=int, default=4)
            sp.add_argument("--report", action="store_true")
            sp.add_argument("--key")
            sp.add_argument("--excerpts", action="store_true")
            sp.add_argument("--accountability")
            sp.add_argument("--out")
            sp.add_argument("--loop", action="store_true")
            sp.add_argument("--every", type=int, default=300, help="seconds between cycles with --loop")
        sp.add_argument("--requests", type=int, default=None, help="at most 9 (the client's per-run ceiling)")
        sp.add_argument("--pace", type=float, default=7.0, help="seconds between requests (API: ~10 a minute)")
    sp = sub.add_parser("analyze")
    sp.add_argument("--layer", choices=("keyword", "semantic", "all"), default="keyword")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--mode", choices=("sync", "batch"), default="sync")
    sp.add_argument("--workers", type=int, default=4)
    sp.add_argument("--call-ids", nargs="*")
    sub.add_parser("poll-batches")
    sp = sub.add_parser("retry")
    period(sp, False)
    sp = sub.add_parser("reconcile")
    period(sp)
    sp = sub.add_parser("estimate")
    period(sp, False)
    sp.add_argument("--batch", action="store_true")
    sp.add_argument("--model")
    sp = sub.add_parser("report")
    period(sp)
    sp.add_argument("--key")
    sp.add_argument("--excerpts", action="store_true", help="include verbatim evidence excerpts (stays local)")
    sp.add_argument("--accountability", help="an analytics.accountability output folder (actions.csv) for the period")
    sp.add_argument("--out")
    sp.add_argument("--refresh-sources", action="store_true")
    a = p.parse_args(argv)

    reg = Registry(a.db)
    now = now_utc()
    if a.cmd == "status":
        print_status(reg, a.start, a.end)
    elif a.cmd == "inventory":
        from analytics.convintel.inventory import inventory
        from integrations.leadsquared import LeadSquaredClient
        print(json.dumps(dict(inventory(reg, LeadSquaredClient(), a.start, a.end, now, window=a.window, log=log))))
    elif a.cmd == "fetch-transcripts":
        from analytics.convintel.fetch import run_fetch
        print(json.dumps(dict(run_fetch(reg, now, requests=a.requests, pace_s=a.pace, log=log))))
    elif a.cmd == "analyze":
        from analytics.convintel.analyze import keyword_pass, semantic_pass
        if a.layer in ("keyword", "all"):
            print(json.dumps({"keyword": dict(keyword_pass(reg, now, a.limit, a.call_ids, log=log))}))
        if a.layer in ("semantic", "all"):
            if not a.limit:
                sys.exit("The semantic layer calls a paid model: give --limit N (calls to send). See `estimate` first.")
            print(json.dumps({"semantic": dict(semantic_pass(reg, now, a.limit, mode=a.mode, workers=a.workers,
                                                             call_ids=a.call_ids, log=log))}))
    elif a.cmd == "poll-batches":
        from analytics.convintel.analyze import poll_batches
        print(json.dumps(dict(poll_batches(reg, now, log=log))))
    elif a.cmd == "retry":
        n = reg.requeue_not_found(now, a.start, a.end)
        m = sum(reg.retry_failed(layer, now) for layer in S.LAYERS)
        reg.refresh(None, now)
        print(f"{n} calls queued for another transcript search and {m} failed analyses queued again, from now")
    elif a.cmd == "reconcile":
        from analytics.convintel.reconcile import reconcile
        for c in reconcile(reg, a.start, a.end, now):
            print(f"{'OK  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}")
    elif a.cmd == "estimate":
        from analytics.convintel.llm import estimate_cost
        where, params = "transcript_state = ? AND analysis_status <> ?", [S.T_FOUND, S.ANALYZED]
        if a.start:
            where += " AND ist_day >= ? AND ist_day <= ?"
            params += [a.start, a.end or a.start]
        r = reg.q(f"SELECT COUNT(*) AS n, AVG(transcript_words) AS w FROM transcript_coverage_registry WHERE {where}", params)[0]
        print(json.dumps(estimate_cost(r["n"] or 0, r["w"] or 0, a.model, a.batch), indent=1))
    elif a.cmd == "report":
        from analytics.convintel.export import run_report
        print(json.dumps(run_report(reg, a.start, a.end, now, key=a.key, excerpts=a.excerpts, out_dir=a.out,
                                    accountability_dir=a.accountability, refresh=a.refresh_sources, log=log), indent=1))
    elif a.cmd == "run":
        while True:
            log(json.dumps(cycle(reg, a)))
            if not a.loop:
                break
            log(f"next cycle at {(now_utc() + timedelta(seconds=a.every)).astimezone(IST):%H:%M} IST")
            time.sleep(a.every)
    reg.close()


if __name__ == "__main__":
    main()
