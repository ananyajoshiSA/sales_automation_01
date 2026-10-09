"""The module's persistent registry: one SQLite file under data/ (git-ignored; it holds lead numbers).

Tables follow requirement 11's entities:

* ``transcript_coverage_registry``: one row per LeadSquared call (the inventory), with its call class,
  transcript lookup state and analysis status. A row is never deleted, so a call can't drop out unnoticed.
* ``conversation_analysis_jobs``: one work item per call and analysis layer (state, attempts, retry time,
  claim), which makes runs resumable and idempotent.
* ``conversation_analysis_results``: each layer's validated output, kept per version.
* ``conversation_quality_findings``, ``caller_coaching_insights``, ``team_conversation_aggregates``,
  ``lead_accountability_findings``, ``revenue_opportunity_findings``, ``lead_cross_call``: generated findings,
  kept apart from source data and keyed so a re-run replaces instead of duplicating.
* ``number_lookups`` (the transcript search queue, one row per lead number), ``orphan_recordings``
  (transcript API recordings no LeadSquared call matches) and ``processing_runs`` (run log with checkpoints).

Statuses are derived from the transcript state and the jobs (``derive_status``) and stored for fast counts.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable

from analytics.convintel import schema as S

DEFAULT_PATH = os.path.join("data", "convintel", "registry.sqlite")
LOOKUP_LAG = timedelta(minutes=20)           # give the dialer and the transcriber time before the first search
RECHECK_AFTER = (timedelta(hours=2), timedelta(hours=24), timedelta(hours=72), timedelta(days=7))
RETRY_AFTER = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=12), timedelta(hours=24))
STALE_CLAIM = timedelta(minutes=30)          # an in-progress claim older than this was interrupted
BATCH_STALE = timedelta(hours=25)            # the Batches API finishes or expires within 24 h
LAYER_VERSIONS = {S.KEYWORD: S.KEYWORD_VERSION, S.SEMANTIC: S.SEMANTIC_VERSION}

CALL_COLUMNS = ("call_id", "lead_id", "lead_number", "number", "caller_number", "direction", "call_status", "answered",
                "start_utc", "ist_day", "duration_s", "duration_raw", "call_class", "class_reason", "caller_id",
                "caller_name", "caller_kind", "team", "team_source", "source")

DDL = """
CREATE TABLE IF NOT EXISTS transcript_coverage_registry (
  call_id TEXT PRIMARY KEY, lead_id TEXT, lead_number TEXT, number TEXT, caller_number TEXT, direction TEXT,
  call_status TEXT,
  answered INTEGER NOT NULL DEFAULT 0, start_utc TEXT, ist_day TEXT, duration_s INTEGER, duration_raw TEXT,
  call_class TEXT NOT NULL, class_reason TEXT, caller_id TEXT, caller_name TEXT, caller_kind TEXT, team TEXT,
  team_source TEXT, source TEXT,
  transcript_expected INTEGER NOT NULL DEFAULT 0,
  transcript_state TEXT NOT NULL DEFAULT 'NOT_LOOKED_UP', transcript_ref TEXT, transcript_sha256 TEXT,
  transcript_words INTEGER, transcript_api_duration INTEGER, transcript_shift_min INTEGER, transcript_gap_min REAL,
  transcript_source_id TEXT, transcript_found_utc TEXT,
  lookup_attempts INTEGER NOT NULL DEFAULT 0, last_lookup_utc TEXT, next_lookup_utc TEXT, lookup_note TEXT,
  analysis_status TEXT NOT NULL DEFAULT 'PENDING_ANALYSIS', analysis_version TEXT, missing_layers TEXT,
  status_reason TEXT, status_utc TEXT, inventoried_utc TEXT, updated_utc TEXT, seen_utc TEXT
);
CREATE INDEX IF NOT EXISTS reg_status ON transcript_coverage_registry (analysis_status);
CREATE INDEX IF NOT EXISTS reg_number ON transcript_coverage_registry (number);
CREATE INDEX IF NOT EXISTS reg_day ON transcript_coverage_registry (ist_day);
CREATE INDEX IF NOT EXISTS reg_lead ON transcript_coverage_registry (lead_id);
CREATE INDEX IF NOT EXISTS reg_tstate ON transcript_coverage_registry (transcript_state);
CREATE TABLE IF NOT EXISTS conversation_analysis_jobs (
  call_id TEXT NOT NULL, layer TEXT NOT NULL, version TEXT NOT NULL, state TEXT NOT NULL, engine TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, claimed_utc TEXT, batch_id TEXT, next_utc TEXT, missing TEXT, error TEXT,
  updated_utc TEXT, PRIMARY KEY (call_id, layer)
);
CREATE INDEX IF NOT EXISTS jobs_state ON conversation_analysis_jobs (layer, state);
CREATE TABLE IF NOT EXISTS conversation_analysis_results (
  call_id TEXT NOT NULL, layer TEXT NOT NULL, version TEXT NOT NULL, engine TEXT, created_utc TEXT,
  valid INTEGER NOT NULL, missing TEXT, dropped_excerpts INTEGER NOT NULL DEFAULT 0, output TEXT NOT NULL,
  PRIMARY KEY (call_id, layer, version)
);
CREATE TABLE IF NOT EXISTS conversation_quality_findings (
  finding_id TEXT PRIMARY KEY, call_id TEXT, lead_id TEXT, caller_id TEXT, caller_name TEXT, team TEXT,
  ist_day TEXT, layer TEXT, version TEXT, category TEXT, excerpt TEXT, offset INTEGER, speaker TEXT,
  confidence TEXT, reasoning TEXT, recommended_action TEXT, created_utc TEXT
);
CREATE INDEX IF NOT EXISTS findings_call ON conversation_quality_findings (call_id);
CREATE TABLE IF NOT EXISTS caller_coaching_insights (
  period TEXT NOT NULL, caller_id TEXT NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL, caller_name TEXT, team TEXT,
  n INTEGER, evidence TEXT, version TEXT, created_utc TEXT, PRIMARY KEY (period, caller_id, kind, item)
);
CREATE TABLE IF NOT EXISTS team_conversation_aggregates (
  period TEXT NOT NULL, team TEXT NOT NULL, metric TEXT NOT NULL, value REAL, denominator TEXT, version TEXT,
  created_utc TEXT, PRIMARY KEY (period, team, metric)
);
CREATE TABLE IF NOT EXISTS lead_accountability_findings (
  finding_id TEXT PRIMARY KEY, lead_id TEXT, action TEXT, action_timestamp_utc TEXT, account_owner TEXT,
  assigned_by TEXT, assigned_to TEXT, transferred_by TEXT, transferred_to TEXT, action_performed_by TEXT,
  responsible_person TEXT, followup_owner TEXT, team_at_action_time TEXT, owner_at_enrolment TEXT,
  last_answered_caller TEXT, status TEXT, evidence TEXT, source_attribution TEXT, corrected_attribution TEXT,
  created_utc TEXT
);
CREATE TABLE IF NOT EXISTS revenue_opportunity_findings (
  finding_id TEXT PRIMARY KEY, lead_id TEXT, call_id TEXT, kind TEXT, ist_day TEXT, caller_id TEXT, caller_name TEXT,
  team TEXT, owner_at_enrolment TEXT, evidence TEXT, next_action TEXT, status TEXT, created_utc TEXT
);
CREATE TABLE IF NOT EXISTS lead_cross_call (
  lead_id TEXT PRIMARY KEY, version TEXT, calls INTEGER, output TEXT NOT NULL, updated_utc TEXT
);
CREATE TABLE IF NOT EXISTS number_lookups (
  number TEXT PRIMARY KEY, last_lookup_utc TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_ok INTEGER,
  recordings INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS orphan_recordings (
  source_id TEXT PRIMARY KEY, number TEXT, kind TEXT, start_utc TEXT, duration INTEGER, agent_name TEXT,
  has_text INTEGER, seen_utc TEXT
);
CREATE TABLE IF NOT EXISTS processing_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, started_utc TEXT, finished_utc TEXT, params TEXT,
  status TEXT, counts TEXT, checkpoint TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def ts(t: datetime | None) -> str | None:
    """UTC 'YYYY-MM-DD HH:MM:SS' (the format LeadSquared and the dashboard use) for an aware datetime."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if t else None


def parse_ts(s: str | None) -> datetime | None:
    from integrations.timeutil import utc
    return utc(s)


def _backoff(attempts: int, table=RETRY_AFTER) -> timedelta:
    return table[min(max(attempts, 1), len(table)) - 1]


def derive_status(call: dict, jobs: dict[str, dict], now: datetime, required: Iterable[str] = S.REQUIRED_LAYERS,
                  blocked: dict[str, str] | None = None) -> tuple[str, list[str], str]:
    """(analysis status, missing layers, reason) for one call. ``jobs`` maps layer -> job row; ``blocked`` maps a
    layer that can't run at all right now (e.g. no model access) to the reason, which is shown instead of
    "not run yet"."""
    required, blocked = list(required), blocked or {}
    t = call.get("transcript_state") or S.T_NOT_LOOKED_UP
    if t == S.T_FOUND:
        current = {l: j for l, j in jobs.items() if j and j.get("version") == LAYER_VERSIONS.get(l)}
        done = [l for l in required if (current.get(l) or {}).get("state") == "done"]
        missing = [l for l in required if l not in done]
        if not missing:
            return S.ANALYZED, [], "every required layer validated"
        claimed = [l for l in missing if (current.get(l) or {}).get("state") == "in_progress"
                   and (c := parse_ts(current[l].get("claimed_utc"))) and now - c < STALE_CLAIM]
        if claimed:
            return S.ANALYSIS_IN_PROGRESS, missing, f"{', '.join(claimed)} layer running"
        batched = [l for l in missing if (current.get(l) or {}).get("state") == "batched"
                   and (c := parse_ts(current[l].get("claimed_utc"))) and now - c < BATCH_STALE]
        if batched:
            return S.ANALYSIS_IN_PROGRESS, missing, f"{', '.join(batched)} layer sent in a batch, waiting for results"
        failed = [l for l in missing if (current.get(l) or {}).get("state") == "failed"]
        if failed:
            errs = "; ".join(f"{l}: {(current[l].get('error') or '')[:120]}" for l in failed)
            when = "retry scheduled" if all(current[l].get("next_utc") for l in failed) else "not retried automatically"
            return S.ANALYSIS_FAILED, missing, f"failed, {when} ({errs})"
        partial = [l for l in missing if (current.get(l) or {}).get("state") == "incomplete"]
        if done or partial:
            why = [f"{l} missing {', '.join(json.loads(current[l].get('missing') or '[]'))}" for l in partial]
            why += [f"{l} layer waiting: {blocked[l]}" if l in blocked else f"{l} not run yet"
                    for l in missing if l not in partial]
            return S.ANALYSIS_INCOMPLETE, missing, "; ".join(why)
        return S.PENDING_ANALYSIS, missing, "transcript found, waiting for analysis"
    if not call.get("transcript_expected"):
        return S.NO_TRANSCRIPT_EXPECTED, [], "not connected, so nothing was recorded"
    if t == S.T_NOT_LOOKED_UP:
        return S.PENDING_ANALYSIS, list(required), "transcript not searched yet"
    if t == S.T_LOOKUP_FAILED:
        return S.PENDING_ANALYSIS, list(required), f"transcript search failed, retrying ({call.get('lookup_note') or ''})"
    note = call.get("lookup_note") or ""
    return S.TRANSCRIPT_NOT_FOUND, list(required), {
        S.T_NOT_FOUND: "no recording matches this call", S.T_NOT_TRANSCRIBED: "recording exists but has no transcript",
        S.T_NO_NUMBER: "the call record has no lead number to search"}.get(t, t) + (f" ({note})" if note else "")


class Registry:
    def __init__(self, path: str = DEFAULT_PATH):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
        self.db.executescript(DDL)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(transcript_coverage_registry)")}
        for col in ("caller_number", "seen_utc"):            # registries created before these columns existed
            if col not in have:
                self.db.execute(f"ALTER TABLE transcript_coverage_registry ADD COLUMN {col} TEXT")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def q(self, sql: str, params: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, tuple(params))]

    # ------------------------------------------------------------------ inventory

    def upsert_calls(self, records: list[dict], now: datetime) -> Counter:
        """Add new calls and refresh the source fields of known ones (status, duration, number), never their
        stamped team or analysis state. A call that turns out to have connected becomes expected to have a
        transcript. Every call read gets ``seen_utc``, so reconciliation can find calls the source no longer
        returns. Returns counts of new, changed and unchanged calls."""
        out = Counter()
        n = ts(now)
        for r in records:
            old = self.db.execute("SELECT * FROM transcript_coverage_registry WHERE call_id = ?", (r["call_id"],)).fetchone()
            expected = 1 if r.get("answered") or r.get("call_class") != S.NOT_CONNECTED else 0
            if old is None:
                cols = list(CALL_COLUMNS) + ["transcript_expected", "transcript_state", "inventoried_utc", "updated_utc",
                                             "seen_utc"]
                vals = [r.get(c) for c in CALL_COLUMNS] + [
                    expected, S.T_NOT_LOOKED_UP if expected else S.T_NOT_EXPECTED, n, n, n]
                if expected and not r.get("number"):
                    vals[cols.index("transcript_state")] = S.T_NO_NUMBER
                self.db.execute(f"INSERT INTO transcript_coverage_registry ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
                out["new"] += 1
                continue
            old = dict(old)
            changes = {c: r.get(c) for c in ("call_status", "answered", "duration_s", "duration_raw", "call_class",
                                            "class_reason", "lead_number", "number", "caller_number", "lead_id")
                       if r.get(c) is not None and r.get(c) != old.get(c)}
            for c in ("caller_id", "caller_name", "caller_kind", "team", "team_source"):  # fill gaps only
                if not old.get(c) and r.get(c):
                    changes[c] = r[c]
            if expected and not old["transcript_expected"]:
                changes["transcript_expected"] = 1
                changes["transcript_state"] = S.T_NOT_LOOKED_UP if (r.get("number") or old.get("number")) else S.T_NO_NUMBER
            if old["transcript_state"] == S.T_NO_NUMBER and changes.get("number"):
                changes["transcript_state"] = S.T_NOT_LOOKED_UP
            if not changes:
                self.db.execute("UPDATE transcript_coverage_registry SET seen_utc = ? WHERE call_id = ?", (n, r["call_id"]))
                out["unchanged"] += 1
                continue
            changes["updated_utc"] = changes["seen_utc"] = n
            self.db.execute(f"UPDATE transcript_coverage_registry SET {', '.join(f'{c} = ?' for c in changes)} WHERE call_id = ?",
                            [*changes.values(), r["call_id"]])
            out["changed"] += 1
        self.db.commit()
        return out

    def call(self, call_id: str) -> dict | None:
        rows = self.q("SELECT * FROM transcript_coverage_registry WHERE call_id = ?", (call_id,))
        return rows[0] if rows else None

    # ------------------------------------------------------------------ transcript search queue

    def due_numbers(self, now: datetime, limit: int) -> list[str]:
        """Lead numbers to search next. First, numbers with calls never searched since they happened (newest
        call first, so today's calls are not stuck behind the backlog); then scheduled rechecks and failed
        searches, oldest due first."""
        cutoff = ts(now - LOOKUP_LAG)
        fresh = self.q(
            "SELECT number, MAX(start_utc) AS last FROM transcript_coverage_registry "
            "WHERE transcript_expected = 1 AND number IS NOT NULL AND number <> '' AND start_utc <= ? "
            "AND transcript_state = ? GROUP BY number ORDER BY last DESC LIMIT ?", (cutoff, S.T_NOT_LOOKED_UP, limit))
        out = [r["number"] for r in fresh]
        if len(out) < limit:
            again = self.q(
                "SELECT number, MIN(next_lookup_utc) AS due FROM transcript_coverage_registry "
                "WHERE transcript_expected = 1 AND number IS NOT NULL AND number <> '' "
                "AND transcript_state IN (?, ?, ?) AND next_lookup_utc IS NOT NULL AND next_lookup_utc <= ? "
                "GROUP BY number ORDER BY due LIMIT ?",
                (S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED, S.T_LOOKUP_FAILED, ts(now), limit))
            out += [r["number"] for r in again if r["number"] not in out][:limit - len(out)]
        return out

    def calls_for_number(self, number: str) -> list[dict]:
        return self.q("SELECT * FROM transcript_coverage_registry WHERE number = ? ORDER BY start_utc", (number,))

    def record_lookup(self, number: str, now: datetime, ok: bool, recordings: int = 0, error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO number_lookups (number, last_lookup_utc, attempts, last_ok, recordings, error) VALUES (?, ?, 1, ?, ?, ?) "
            "ON CONFLICT(number) DO UPDATE SET last_lookup_utc = excluded.last_lookup_utc, attempts = attempts + 1, "
            "last_ok = excluded.last_ok, recordings = excluded.recordings, error = excluded.error",
            (number, ts(now), int(ok), recordings, error))
        self.db.commit()

    def lookup_failed(self, number: str, now: datetime, error: str) -> None:
        """A failed search leaves the number's unresolved calls queued, with a retry time."""
        for c in self.calls_for_number(number):
            if c["transcript_state"] in (S.T_NOT_LOOKED_UP, S.T_LOOKUP_FAILED, S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED) and c["transcript_expected"]:
                attempts = c["lookup_attempts"] + 1
                self.db.execute(
                    "UPDATE transcript_coverage_registry SET transcript_state = ?, lookup_attempts = ?, last_lookup_utc = ?, "
                    "next_lookup_utc = ?, lookup_note = ?, updated_utc = ? WHERE call_id = ?",
                    (S.T_LOOKUP_FAILED, attempts, ts(now), ts(now + _backoff(attempts)), error[:300], ts(now), c["call_id"]))
        self.record_lookup(number, now, False, 0, error[:300])

    def set_transcript(self, call_id: str, state: str, now: datetime, **f) -> None:
        """Record a search result for one call. NOT_FOUND / NOT_TRANSCRIBED get the next recheck time from the
        call's own start (2 h, 24 h, 72 h, 7 days); after the last one the call stays TRANSCRIPT_NOT_FOUND."""
        c = self.call(call_id)
        attempts = (c["lookup_attempts"] if c else 0) + 1
        nxt = None
        if state in (S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED) and c and (start := parse_ts(c["start_utc"])):
            nxt = next((start + d for d in RECHECK_AFTER if start + d > now), None)
        fields = {"transcript_state": state, "lookup_attempts": attempts, "last_lookup_utc": ts(now),
                  "next_lookup_utc": ts(nxt) if nxt else None, "updated_utc": ts(now), **f}
        if state in (S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED) and nxt is None:
            fields["lookup_note"] = "; ".join(x for x in (fields.get("lookup_note"), (
                f"searched {attempts} times, last {ts(now)} UTC; no more automatic rechecks")) if x)
        if state == S.T_FOUND:
            fields.setdefault("transcript_found_utc", ts(now))
            fields["lookup_note"] = None
        self.db.execute(f"UPDATE transcript_coverage_registry SET {', '.join(f'{k} = ?' for k in fields)} WHERE call_id = ?",
                        [*fields.values(), call_id])
        self.db.commit()

    def lost_transcript(self, call_id: str, now: datetime) -> None:
        """The saved transcript is missing or changed on disk (e.g. a fresh machine): search for it again."""
        self.db.execute(
            "UPDATE transcript_coverage_registry SET transcript_state = ?, transcript_ref = NULL, transcript_sha256 = NULL, "
            "transcript_source_id = NULL, next_lookup_utc = ?, lookup_note = ?, updated_utc = ? WHERE call_id = ?",
            (S.T_NOT_LOOKED_UP, ts(now), "the saved transcript was missing, so it is searched for again", ts(now), call_id))
        self.db.execute("UPDATE conversation_analysis_jobs SET state = 'queued', claimed_utc = NULL, batch_id = NULL, "
                        "updated_utc = ? WHERE call_id = ? AND state IN ('in_progress', 'batched')", (ts(now), call_id))
        self.db.commit()
        self.refresh([call_id], now)

    def assigned_sources(self, number: str) -> set[str]:
        return {r["transcript_source_id"] for r in self.q(
            "SELECT transcript_source_id FROM transcript_coverage_registry WHERE number = ? AND transcript_source_id IS NOT NULL",
            (number,))}

    def requeue_not_found(self, now: datetime, day_from: str | None = None, day_to: str | None = None) -> int:
        """Manual retry: search again for every call whose transcript was not found (or failed)."""
        sql = ("UPDATE transcript_coverage_registry SET next_lookup_utc = ?, updated_utc = ? WHERE transcript_state IN (?, ?, ?, ?)")
        params = [ts(now), ts(now), S.T_NOT_FOUND, S.T_NOT_TRANSCRIBED, S.T_LOOKUP_FAILED, S.T_NOT_LOOKED_UP]
        if day_from:
            sql += " AND ist_day >= ?"
            params.append(day_from)
        if day_to:
            sql += " AND ist_day <= ?"
            params.append(day_to)
        n = self.db.execute(sql, params).rowcount
        self.db.commit()
        return n

    def save_orphans(self, rows: list[dict], now: datetime) -> None:
        self.db.executemany(
            "INSERT INTO orphan_recordings (source_id, number, kind, start_utc, duration, agent_name, has_text, seen_utc) "
            "VALUES (:source_id, :number, :kind, :start_utc, :duration, :agent_name, :has_text, :seen_utc) "
            "ON CONFLICT(source_id) DO UPDATE SET has_text = excluded.has_text, seen_utc = excluded.seen_utc",
            [{**r, "seen_utc": ts(now)} for r in rows])
        self.db.execute("DELETE FROM orphan_recordings WHERE source_id IN (SELECT transcript_source_id FROM "
                        "transcript_coverage_registry WHERE transcript_source_id IS NOT NULL)")
        self.db.commit()

    # ------------------------------------------------------------------ analysis jobs

    def jobs(self, call_id: str) -> dict[str, dict]:
        return {j["layer"]: j for j in self.q("SELECT * FROM conversation_analysis_jobs WHERE call_id = ?", (call_id,))}

    def claim(self, layer: str, limit: int, now: datetime, call_ids: Iterable[str] | None = None) -> list[dict]:
        """Calls with a transcript whose ``layer`` result at the current version is missing and due: marked
        in progress and returned (oldest call first). Stale claims from an interrupted run are taken back."""
        version = LAYER_VERSIONS[layer]
        stale = ts(now - STALE_CLAIM)
        sql = ("SELECT r.* FROM transcript_coverage_registry r LEFT JOIN conversation_analysis_jobs j "
               "ON j.call_id = r.call_id AND j.layer = ? WHERE r.transcript_state = ? AND ("
               "j.call_id IS NULL OR j.version <> ? OR (j.state IN ('queued', 'failed', 'incomplete') "
               "AND (j.next_utc IS NULL OR j.next_utc <= ?)) OR (j.state = 'in_progress' AND j.claimed_utc < ?) "
               "OR (j.state = 'batched' AND j.claimed_utc < ?))")
        params: list = [layer, S.T_FOUND, version, ts(now), stale, ts(now - BATCH_STALE)]
        if call_ids is not None:
            ids = list(call_ids)
            if not ids:
                return []
            sql += f" AND r.call_id IN ({','.join('?' * len(ids))})"
            params += ids
        sql += " ORDER BY r.start_utc LIMIT ?"
        rows = self.q(sql, params + [limit])
        for r in rows:
            self.db.execute(
                "INSERT INTO conversation_analysis_jobs (call_id, layer, version, state, claimed_utc, updated_utc) "
                "VALUES (?, ?, ?, 'in_progress', ?, ?) ON CONFLICT(call_id, layer) DO UPDATE SET "
                "attempts = CASE WHEN version <> excluded.version THEN 0 ELSE attempts END, version = excluded.version, "
                "state = 'in_progress', claimed_utc = excluded.claimed_utc, updated_utc = excluded.updated_utc",
                (r["call_id"], layer, version, ts(now), ts(now)))
        self.db.commit()
        self.refresh([r["call_id"] for r in rows], now)
        return rows

    def finish(self, call_id: str, layer: str, engine: str, output: dict, missing: list[str], dropped: int,
               now: datetime) -> str:
        """Store a layer's result. Complete and valid -> job done; otherwise incomplete with a retry time."""
        version = LAYER_VERSIONS[layer]
        valid = not missing
        self.db.execute(
            "INSERT INTO conversation_analysis_results (call_id, layer, version, engine, created_utc, valid, missing, "
            "dropped_excerpts, output) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(call_id, layer, version) DO UPDATE SET "
            "engine = excluded.engine, created_utc = excluded.created_utc, valid = excluded.valid, missing = excluded.missing, "
            "dropped_excerpts = excluded.dropped_excerpts, output = excluded.output",
            (call_id, layer, version, engine, ts(now), int(valid), json.dumps(missing), dropped,
             json.dumps(output, ensure_ascii=False)))
        job = self.jobs(call_id).get(layer) or {}
        attempts = (job.get("attempts") or 0) + 1
        state = "done" if valid else "incomplete"
        self.db.execute(
            "INSERT INTO conversation_analysis_jobs (call_id, layer, version, state, engine, attempts, next_utc, missing, "
            "error, updated_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?) ON CONFLICT(call_id, layer) DO UPDATE SET "
            "version = excluded.version, state = excluded.state, engine = excluded.engine, attempts = excluded.attempts, "
            "next_utc = excluded.next_utc, missing = excluded.missing, error = NULL, claimed_utc = NULL, "
            "batch_id = NULL, updated_utc = excluded.updated_utc",
            (call_id, layer, version, state, engine, attempts, None if valid else ts(now + _backoff(attempts)),
             json.dumps(missing), ts(now)))
        self.db.commit()
        self.refresh([call_id], now)
        return state

    def mark_batched(self, call_ids: Iterable[str], layer: str, batch_id: str, now: datetime) -> None:
        """Claimed calls sent to the Batches API: kept out of other runs until results come back (or 25 h pass)."""
        ids = list(call_ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            self.db.execute(f"UPDATE conversation_analysis_jobs SET state = 'batched', batch_id = ?, claimed_utc = ?, "
                            f"updated_utc = ? WHERE layer = ? AND call_id IN ({','.join('?' * len(chunk))})",
                            [batch_id, ts(now), ts(now), layer, *chunk])
        self.db.commit()
        self.refresh(ids, now)

    def open_batches(self, layer: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.q("SELECT batch_id, call_id FROM conversation_analysis_jobs WHERE layer = ? AND state = 'batched'", (layer,)):
            out.setdefault(r["batch_id"], []).append(r["call_id"])
        return out

    def fail(self, call_id: str, layer: str, error: str, now: datetime, engine: str | None = None,
             retry: bool = True) -> None:
        """A failed layer, retried after a growing wait, or not automatically when ``retry`` is False (e.g. the
        model declined the same transcript twice); ``retry_failed`` queues those again."""
        job = self.jobs(call_id).get(layer) or {}
        attempts = (job.get("attempts") or 0) + 1
        self.db.execute(
            "INSERT INTO conversation_analysis_jobs (call_id, layer, version, state, engine, attempts, next_utc, error, "
            "updated_utc) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?) ON CONFLICT(call_id, layer) DO UPDATE SET "
            "version = excluded.version, state = 'failed', engine = excluded.engine, attempts = excluded.attempts, "
            "next_utc = excluded.next_utc, error = excluded.error, claimed_utc = NULL, batch_id = NULL, "
            "updated_utc = excluded.updated_utc",
            (call_id, layer, LAYER_VERSIONS[layer], engine, attempts, ts(now + _backoff(attempts)) if retry else None,
             error[:500], ts(now)))
        self.db.commit()
        self.refresh([call_id], now)

    def retry_failed(self, layer: str, now: datetime) -> int:
        """Manual retry: every failed or incomplete job of ``layer`` is due now."""
        n = self.db.execute("UPDATE conversation_analysis_jobs SET next_utc = ?, updated_utc = ? WHERE layer = ? AND "
                            "state IN ('failed', 'incomplete')", (ts(now), ts(now), layer)).rowcount
        self.db.commit()
        return n

    def release(self, call_ids: Iterable[str], layer: str, now: datetime) -> None:
        """Hand claimed calls back untouched (e.g. the engine is not configured): queued, due now."""
        ids = list(call_ids)
        if ids:
            self.db.execute(f"UPDATE conversation_analysis_jobs SET state = 'queued', claimed_utc = NULL, updated_utc = ? "
                            f"WHERE layer = ? AND state = 'in_progress' AND call_id IN ({','.join('?' * len(ids))})",
                            [ts(now), layer, *ids])
            self.db.commit()
            self.refresh(ids, now)

    def result(self, call_id: str, layer: str, version: str | None = None) -> dict | None:
        rows = self.q("SELECT * FROM conversation_analysis_results WHERE call_id = ? AND layer = ? AND version = ?",
                      (call_id, layer, version or LAYER_VERSIONS[layer]))
        if not rows:
            return None
        r = rows[0]
        return {**r, "output": json.loads(r["output"]), "missing": json.loads(r["missing"] or "[]")}

    # ------------------------------------------------------------------ statuses

    def refresh(self, call_ids: Iterable[str] | None, now: datetime) -> Counter:
        """Re-derive and store the analysis status of the given calls (all calls when None)."""
        if call_ids is None:
            calls = self.q("SELECT * FROM transcript_coverage_registry")
            jobs: dict[str, dict] = {}
            for j in self.q("SELECT * FROM conversation_analysis_jobs"):
                jobs.setdefault(j["call_id"], {})[j["layer"]] = j
        else:
            ids = list(call_ids)
            calls, jobs = [], {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                calls += self.q(f"SELECT * FROM transcript_coverage_registry WHERE call_id IN ({marks})", chunk)
                for j in self.q(f"SELECT * FROM conversation_analysis_jobs WHERE call_id IN ({marks})", chunk):
                    jobs.setdefault(j["call_id"], {})[j["layer"]] = j
        out, blocked = Counter(), self.get_meta("blocked_layers", {})
        for c in calls:
            status, missing, reason = derive_status(c, jobs.get(c["call_id"], {}), now, blocked=blocked)
            out[status] += 1
            if (status, json.dumps(missing), reason) != (c["analysis_status"], c["missing_layers"], c["status_reason"]):
                self.db.execute(
                    "UPDATE transcript_coverage_registry SET analysis_status = ?, missing_layers = ?, status_reason = ?, "
                    "analysis_version = ?, status_utc = ? WHERE call_id = ?",
                    (status, json.dumps(missing), reason, S.ANALYSIS_VERSION if status == S.ANALYZED else None, ts(now),
                     c["call_id"]))
        self.db.commit()
        return out

    # ------------------------------------------------------------------ findings and derived tables

    def replace_rows(self, table: str, rows: list[dict], where: str = "", params: Iterable = ()) -> None:
        """Replace a derived table's rows (optionally only those matching ``where``) in one transaction."""
        with self.db:
            if where:
                self.db.execute(f"DELETE FROM {table} WHERE {where}", tuple(params))
            for r in rows:
                cols = list(r)
                self.db.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in r.values()])

    # ------------------------------------------------------------------ runs

    def start_run(self, kind: str, params: dict, now: datetime) -> int:
        cur = self.db.execute("INSERT INTO processing_runs (kind, started_utc, params, status) VALUES (?, ?, ?, 'running')",
                              (kind, ts(now), json.dumps(params)))
        self.db.commit()
        return int(cur.lastrowid)

    def checkpoint(self, run_id: int, data: dict) -> None:
        self.db.execute("UPDATE processing_runs SET checkpoint = ? WHERE run_id = ?", (json.dumps(data), run_id))
        self.db.commit()

    def finish_run(self, run_id: int, status: str, counts: dict, now: datetime, error: str | None = None) -> None:
        self.db.execute("UPDATE processing_runs SET finished_utc = ?, status = ?, counts = ?, error = ? WHERE run_id = ?",
                        (ts(now), status, json.dumps(counts), error, run_id))
        self.db.commit()

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def get_meta(self, key: str, default=None):
        rows = self.q("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    # ------------------------------------------------------------------ coverage

    def coverage(self, day_from: str | None = None, day_to: str | None = None) -> dict:
        """Counts for the coverage view. The denominator is every call expected to have a transcript plus any
        not-connected call whose transcript turned up anyway; never only the calls that were fetched."""
        where, params = ["1 = 1"], []
        if day_from:
            where.append("ist_day >= ?")
            params.append(day_from)
        if day_to:
            where.append("ist_day <= ?")
            params.append(day_to)
        w = " AND ".join(where)
        status = {r["analysis_status"]: r["n"] for r in self.q(
            f"SELECT analysis_status, COUNT(*) AS n FROM transcript_coverage_registry WHERE {w} GROUP BY analysis_status", params)}
        tstate = {r["transcript_state"]: r["n"] for r in self.q(
            f"SELECT transcript_state, COUNT(*) AS n FROM transcript_coverage_registry WHERE {w} GROUP BY transcript_state", params)}
        classes = {r["call_class"]: r["n"] for r in self.q(
            f"SELECT call_class, COUNT(*) AS n FROM transcript_coverage_registry WHERE {w} GROUP BY call_class", params)}
        total = sum(status.values())
        expected = total - status.get(S.NO_TRANSCRIPT_EXPECTED, 0)
        analyzed = status.get(S.ANALYZED, 0)
        retries = self.q(f"SELECT COUNT(*) AS n FROM conversation_analysis_jobs j JOIN transcript_coverage_registry r "
                         f"USING (call_id) WHERE {w.replace('ist_day', 'r.ist_day')} AND j.state IN ('failed', 'incomplete') "
                         f"AND j.next_utc IS NOT NULL", params)[0]["n"]
        return {
            "total_calls": total, "by_class": {c: classes.get(c, 0) for c in S.CALL_CLASSES},
            "expected_transcripts": expected,
            "transcripts_found": tstate.get(S.T_FOUND, 0),
            "by_status": {s: status.get(s, 0) for s in S.STATUSES},
            "by_transcript_state": {s: tstate.get(s, 0) for s in S.TRANSCRIPT_STATES},
            "analyzed": analyzed, "unanalyzed": expected - analyzed,
            "missing_transcripts": status.get(S.TRANSCRIPT_NOT_FOUND, 0),
            "pending_retries": retries,
            "coverage_pct": round(100 * analyzed / expected, 1) if expected else None,
            "reconciles": sum(status.values()) == total and analyzed + sum(status.get(s, 0) for s in S.GAP_STATUSES) == expected,
        }
