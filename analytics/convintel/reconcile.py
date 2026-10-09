"""Reconciliation: proves the registry accounts for every call before any coverage figure is trusted.

Checks, per period: every day was read from LeadSquared and the registry holds exactly the calls the latest read
returned; every call has exactly one status and the statuses add up; every found transcript is still on disk
and unchanged; no recording is matched to two calls; every ANALYZED call has a valid result for each required
layer at the current version; no claim is stuck; how much of the search backlog is overdue. Coverage may be
called 100% only when every check passes and nothing is left unanalysed.

    python -m analytics.convintel reconcile FROM TO
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta

from analytics.convintel import schema as S
from analytics.convintel.sources import days
from analytics.convintel.store import LAYER_VERSIONS, STALE_CLAIM, Registry, ts

BACKLOG_HOURS = 24


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def reconcile(reg: Registry, day_from: str, day_to: str, now: datetime, verify_files: bool = True) -> list[dict]:
    out, w, p = [], "ist_day >= ? AND ist_day <= ?", (day_from, day_to)
    per_day = {r["ist_day"]: r for r in reg.q(
        f"SELECT ist_day, COUNT(*) AS n, MAX(seen_utc) AS seen FROM transcript_coverage_registry WHERE {w} GROUP BY ist_day", p)}
    missing_days, partial, mismatched, stale = [], [], [], 0
    for d in days(day_from, day_to):
        src = reg.get_meta(f"source:{d}")
        if not src:
            missing_days.append(d)
            continue
        if src.get("window"):
            partial.append(f"{d} ({src['window']} IST only)")
        have = per_day.get(d, {}).get("n", 0)
        if have != src["calls"] - src.get("unreadable_start", 0):
            mismatched.append(f"{d}: LeadSquared {src['calls']}, registry {have}")
        stale += reg.q("SELECT COUNT(*) AS n FROM transcript_coverage_registry WHERE ist_day = ? AND seen_utc < ?",
                       (d, src["read_utc"]))[0]["n"]
    out.append(_check("every day read from LeadSquared", not missing_days,
                      "all days read" if not missing_days else f"not read yet: {', '.join(missing_days)}"))
    out.append(_check("whole days read", not partial,
                      "whole IST days" if not partial else f"sample windows only: {', '.join(partial)}"))
    out.append(_check("registry matches LeadSquared's call count", not mismatched and not stale,
                      "every call LeadSquared returned is in the registry, and nothing else" if not mismatched and not stale
                      else "; ".join(mismatched + ([f"{stale} calls in the registry were not in the latest read "
                                                    "(deleted or moved in LeadSquared)"] if stale else []))))
    unread = reg.q("SELECT COUNT(*) AS n FROM transcript_coverage_registry WHERE ist_day IS NULL")[0]["n"]
    if unread:
        out.append(_check("call start times readable", False, f"{unread} calls have an unreadable start time and no day"))

    cov = reg.coverage(day_from, day_to)
    out.append(_check("statuses add up", cov["reconciles"],
                      f"{cov['total_calls']} calls = {cov['analyzed']} analysed + {cov['unanalyzed']} not yet + "
                      f"{cov['by_status'][S.NO_TRANSCRIPT_EXPECTED]} not connected (no recording expected)"))

    dup = reg.q(f"SELECT transcript_source_id, COUNT(*) AS n FROM transcript_coverage_registry WHERE {w} AND "
                f"transcript_source_id IS NOT NULL GROUP BY transcript_source_id HAVING n > 1", p)
    out.append(_check("one recording per call", not dup,
                      "no recording is matched to two calls" if not dup else f"{len(dup)} recordings matched to 2+ calls"))

    if verify_files:
        bad = 0
        found = reg.q(f"SELECT transcript_ref, transcript_sha256 FROM transcript_coverage_registry WHERE {w} AND transcript_state = ?",
                      (*p, S.T_FOUND))
        for r in found:
            ok = r["transcript_ref"] and os.path.exists(r["transcript_ref"]) and hashlib.sha256(
                open(r["transcript_ref"], "rb").read()).hexdigest() == r["transcript_sha256"]
            bad += not ok
        out.append(_check("found transcripts on disk", not bad,
                          f"all {len(found)} found transcripts are saved and unchanged" if not bad
                          else f"{bad} of {len(found)} transcripts are missing or changed; the next analysis run searches again"))

    broken = 0
    for layer in S.REQUIRED_LAYERS:
        broken += reg.q(
            f"SELECT COUNT(*) AS n FROM transcript_coverage_registry r LEFT JOIN conversation_analysis_results x "
            f"ON x.call_id = r.call_id AND x.layer = ? AND x.version = ? AND x.valid = 1 "
            f"WHERE {w.replace('ist_day', 'r.ist_day')} AND r.analysis_status = ? AND x.call_id IS NULL",
            (layer, LAYER_VERSIONS[layer], *p, S.ANALYZED))[0]["n"]
    out.append(_check("analysed calls have every layer", not broken,
                      f"every ANALYZED call has a valid result for each of {', '.join(S.REQUIRED_LAYERS)}" if not broken
                      else f"{broken} layer results missing on calls marked ANALYZED"))

    stuck = reg.q("SELECT COUNT(*) AS n FROM conversation_analysis_jobs WHERE state = 'in_progress' AND claimed_utc < ?",
                  (ts(now - STALE_CLAIM),))[0]["n"]
    out.append(_check("no stuck analysis", not stuck,
                      "no interrupted claims" if not stuck else f"{stuck} interrupted claims (taken back on the next run)"))

    overdue = reg.q(f"SELECT COUNT(*) AS n FROM transcript_coverage_registry WHERE {w} AND transcript_expected = 1 AND "
                    f"transcript_state = ? AND start_utc < ?", (*p, S.T_NOT_LOOKED_UP, ts(now - timedelta(hours=BACKLOG_HOURS))))[0]["n"]
    out.append(_check("search backlog", not overdue,
                      f"no connected call has waited over {BACKLOG_HOURS} h for its first transcript search" if not overdue
                      else f"{overdue} connected calls waited over {BACKLOG_HOURS} h for their first search (queued, not dropped)"))

    complete = all(c["ok"] for c in out) and cov["unanalyzed"] == 0 and cov["expected_transcripts"] > 0
    out.append(_check("100% coverage confirmed", complete,
                      "every expected transcript is analysed and every check passed" if complete else
                      f"coverage is {cov['coverage_pct'] if cov['coverage_pct'] is not None else 'not measurable'}% "
                      f"({cov['analyzed']} of {cov['expected_transcripts']}); not claimed as complete"))
    return out
