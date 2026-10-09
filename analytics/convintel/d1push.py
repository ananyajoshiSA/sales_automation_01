"""Load conversation-intelligence snapshots into the dashboard's D1 database for /api/ci (dashboard/src/ci.ts).

Each snapshot's compact JSON is split into parts so every SQL statement stays under 90 KB (D1 caps one at 100 KB):
the raw text is split first and each part escaped after, so a cut never falls inside an escape. One load deletes
the range's old parts, inserts the new ones with one generated_at, then updates the range's index row. The Worker
joins the parts back as text and answers "being updated" while they don't belong to one load.

Free tier: about two rows written per part (old parts deleted, new ones inserted) plus one index row; a push above
MAX_PUSH_ROWS is refused. statements() refuses a snapshot over MAX_SNAPSHOT_BYTES; the command line first trims its
capped lists (calls, leads, opportunities, coverage gaps) from the end with fit(), and their totals keep the full
counts, so the page still says "showing N of M".

Writing to the remote database changes what team leaders see, so it needs the user's go-ahead for each run
(--push --yes). Snapshots holding transcript excerpts or phone numbers stay local and are refused; the command line
first replaces any phone number quoted in free text (a summary or reasoning from Claude's reading) with "[number removed]". The SQL
file holds lead ids and caller names: write it under data/ (git-ignored) and delete it after loading.

    python -m analytics.convintel.d1push SNAPSHOT.json... [--sql OUT.sql] [--push --yes]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

MAX_STATEMENT_BYTES = 90_000
MAX_PUSH_ROWS = 5_000
# The Worker returns the parts without parsing them, but D1 still hands them over inside the 10 ms CPU budget.
# Not measured on Cloudflare yet (wrangler tail shows CPU per request): raise it only after checking.
MAX_SNAPSHOT_BYTES = 1_500_000
# Lists the export caps (each with its total elsewhere in the snapshot), ordered most important first.
TRIMMABLE = (("calls",), ("leads",), ("opportunities",), ("coverage", "gaps"))
VERSION = "ci-snapshot-1"
RANGE_KEY = re.compile(r"^(today|yesterday|7d|30d|\d{4}-\d{2}-\d{2})$")
UTC_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
API = "https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"
WRANGLER = Path(__file__).resolve().parents[2] / "dashboard" / "wrangler.jsonc"
INSERT_PART = "INSERT INTO ci_snapshot (range_key, part, parts, generated_at, body) VALUES "
GO_AHEAD = ("Writing to the dashboard's remote D1 database changes what team leaders see and needs the user's "
            "go-ahead for this run: re-run with --push --yes once they have agreed.")
# A phone number in text: an Indian mobile (with or without +91 / 0, maybe split 5-5 by a space or dash) or any run
# of 10+ digits. Never inside an id: lead, call and user ids are GUIDs, whose digit groups follow a dash or a letter.
PHONE = re.compile(r"(?<![\w-])(?:(?:(?:\+|00)91[\s-]?|91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}|\+?\d{10,})(?![\w-])")
NUMBER_KEYS = frozenset({"number", "leadNumber", "lead_number", "callerNumber", "caller_number", "phone", "mobile"})
TEXT_KEYS = frozenset({"excerpt", "excerpts", "transcript"})
REDACTED = "[number removed]"


def compact(snapshot: dict) -> str:
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def q(v) -> str:
    return "'" + str(v if v is not None else "").replace("'", "''") + "'"


def _size(s: str) -> int:
    """UTF-8 bytes of ``s`` once quoted for SQL (each ' doubles)."""
    return len(s.encode("utf-8")) + s.count("'")


def _escape_start(s: str, k: int) -> bool:
    """A backslash at ``k`` that starts a JSON escape (not the second half of ``\\\\``)."""
    n = 0
    while k - n - 1 >= 0 and s[k - n - 1] == "\\":
        n += 1
    return s[k] == "\\" and n % 2 == 0


def _cut(s: str, lo: int, j: int) -> int:
    """Move a cut at ``j`` back to the start of a JSON escape (\\" or \\u00e9) that would straddle it."""
    for k in range(max(lo + 1, j - 5), j):
        if _escape_start(s, k) and k + (6 if s[k + 1:k + 2] == "u" else 2) > j:
            return k
    return j


def split(body: str, limit: int) -> list[str]:
    """``body`` in consecutive pieces whose SQL-quoted UTF-8 size is at most ``limit`` bytes."""
    parts, i, n = [], 0, len(body)
    while i < n:
        j = min(n, i + limit)
        while (size := _size(body[i:j])) > limit:
            j = i + max(1, (j - i) * limit // size - 1)
        if j < n:
            j = _cut(body, i, j)
        parts.append(body[i:j])
        i = j
    return parts or [""]


def _check(snapshot: dict) -> tuple[str, str, str]:
    """(range key, generated_at, compact JSON) after refusing anything the dashboard must not get."""
    if not isinstance(snapshot, dict) or snapshot.get("version") != VERSION:
        raise ValueError(f"not a conversation-intelligence snapshot (version must be {VERSION})")
    key = (snapshot.get("range") or {}).get("key") or ""
    if not RANGE_KEY.match(key):
        raise ValueError(f"range key {key!r} must be today, yesterday, 7d, 30d or a day YYYY-MM-DD")
    privacy = snapshot.get("privacy") or {}
    if privacy.get("excerpts") or privacy.get("leadNumbers"):
        raise ValueError(f"the {key} snapshot holds transcript excerpts or lead numbers, which stay local: "
                         "build it without --excerpts before loading it into the dashboard")
    if bad := leaks(snapshot):
        more = f" and {len(bad) - 5:,} more" if len(bad) > 5 else ""
        raise ValueError(f"the {key} snapshot holds a phone number or transcript text at {', '.join(bad[:5])}{more}, "
                         "which stays local: load it with the command line (it removes numbers from free text) or "
                         "rebuild it without --excerpts")
    gen = str(snapshot.get("generatedAt") or "").removesuffix(" UTC")
    if not UTC_TS.match(gen):
        raise ValueError(f"the {key} snapshot's generatedAt must be 'YYYY-MM-DD HH:MM:SS UTC'")
    body = compact(snapshot)
    if (size := len(body.encode("utf-8"))) > MAX_SNAPSHOT_BYTES:
        raise ValueError(f"the {key} snapshot is {size:,} bytes, over {MAX_SNAPSHOT_BYTES:,}: cap its lists "
                         "(leads, calls, gaps, opportunities) before loading it, e.g. with fit()")
    return key, gen, body


def leaks(snapshot, path: str = "") -> list[str]:
    """JSON paths (never the values) where the snapshot holds a phone number, a phone-number field or transcript
    text (an excerpt or a transcript field that is not empty)."""
    out = []
    if isinstance(snapshot, dict):
        for k, v in snapshot.items():
            p = f"{path}.{k}" if path else str(k)
            if PHONE.search(str(k)) or (v and not isinstance(v, bool) and (k in NUMBER_KEYS or k in TEXT_KEYS)):
                out.append(p)
            else:
                out += leaks(v, p)
    elif isinstance(snapshot, list):
        for i, v in enumerate(snapshot):
            out += leaks(v, f"{path}[{i}]")
    elif isinstance(snapshot, str) and PHONE.search(snapshot):
        out.append(path)
    return out


def scrub(snapshot):
    """(a copy with every phone number in a text replaced by REDACTED, the JSON paths changed)."""
    changed: list[str] = []

    def walk(o, path):
        if isinstance(o, dict):
            return {k: walk(v, f"{path}.{k}" if path else str(k)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(o)]
        if isinstance(o, str) and PHONE.search(o):
            changed.append(path)
            return PHONE.sub(REDACTED, o)
        return o
    return walk(snapshot, ""), changed


def _at(s: dict, path: tuple[str, ...]) -> tuple[dict, str]:
    for k in path[:-1]:
        s = s.get(k) or {}
    return s, path[-1]


def fit(snapshot: dict, max_bytes: int | None = None) -> tuple[dict, dict[str, tuple[int, int]]]:
    """(a copy that fits in ``max_bytes``, {list: (listed, kept)}): the biggest capped list loses its last quarter
    until the JSON fits. The export sorts these lists most important first and their totals stay as they are."""
    limit = MAX_SNAPSHOT_BYTES if max_bytes is None else max_bytes
    s = {**snapshot, **({"coverage": dict(snapshot["coverage"])} if isinstance(snapshot.get("coverage"), dict) else {})}

    def rows(path):
        parent, key = _at(s, path)
        return parent.get(key) or []

    before = {p: len(rows(p)) for p in TRIMMABLE}
    while len(compact(s).encode("utf-8")) > limit:
        parent, key = _at(s, max(TRIMMABLE, key=lambda p: len(compact(rows(p)))))
        if not parent.get(key):
            break
        parent[key] = parent[key][:len(parent[key]) * 3 // 4]
    return s, {"/".join(p): (n, len(rows(p))) for p, n in before.items()}


def statements(snapshots: list[dict]) -> list[str]:
    """SQL for D1: per snapshot, delete the range's parts, insert the new parts, upsert its index row."""
    out, seen = [], set()
    for snap in snapshots:
        key, gen, body = _check(snap)
        if key in seen:
            raise ValueError(f"two snapshots for the range {key}")
        seen.add(key)
        overhead = len(INSERT_PART) + _size(key) + _size(gen) + 64      # quotes, commas, part numbers
        parts = split(body, MAX_STATEMENT_BYTES - overhead)
        out.append(f"DELETE FROM ci_snapshot WHERE range_key = {q(key)}")
        out += [f"{INSERT_PART}({q(key)}, {i}, {len(parts)}, {q(gen)}, {q(p)})" for i, p in enumerate(parts, 1)]
        r = snap.get("range") or {}
        out.append("INSERT INTO ci_snapshot_index (range_key, label, day_from, day_to, generated_at, parts, bytes) "
                   f"VALUES ({q(key)}, {q(r.get('label') or key)}, {q(r.get('from'))}, {q(r.get('to'))}, {q(gen)}, "
                   f"{len(parts)}, {len(body.encode('utf-8'))}) ON CONFLICT(range_key) DO UPDATE SET "
                   "label = excluded.label, day_from = excluded.day_from, day_to = excluded.day_to, "
                   "generated_at = excluded.generated_at, parts = excluded.parts, bytes = excluded.bytes")
    return out


def rows_estimate(stmts: list[str]) -> int:
    """D1 rows a push writes: each new part, about as many old parts deleted, and the index row."""
    inserts = sum(s.startswith(INSERT_PART) for s in stmts)
    return 2 * inserts + sum(s.startswith("INSERT INTO ci_snapshot_index") for s in stmts)


def write_sql(path: str | os.PathLike, snapshots: list[dict]) -> int:
    """Write the statements as a file for ``wrangler d1 execute --file``; returns how many."""
    stmts = statements(snapshots)
    Path(path).write_text("".join(s + ";\n" for s in stmts), encoding="utf-8")
    return len(stmts)


def d1_ids(path: str | os.PathLike = WRANGLER) -> tuple[str, str]:
    """(account_id, database_id) pinned in dashboard/wrangler.jsonc."""
    text = Path(path).read_text(encoding="utf-8")
    acc = re.search(r'"account_id"\s*:\s*"([^"]+)"', text)
    db = re.search(r'"database_id"\s*:\s*"([^"]+)"', text)
    if not (acc and db):
        raise ValueError(f"{path} has no account_id / database_id")
    return acc.group(1), db.group(1)


def push(snapshots: list[dict], account_id: str | None = None, database_id: str | None = None,
         token: str | None = None, yes: bool = False, session: requests.Session | None = None) -> dict:
    """Run the statements against the remote D1 database through Cloudflare's HTTP API, one per request.

    Refuses unless ``yes`` (the user's go-ahead for this run) and when the push would write over MAX_PUSH_ROWS
    rows. The token comes from CLOUDFLARE_API_TOKEN and is never printed."""
    if not yes:
        raise PermissionError(GO_AHEAD)
    stmts = statements(snapshots)
    estimate = rows_estimate(stmts)
    if estimate > MAX_PUSH_ROWS:
        raise ValueError(f"this push would write about {estimate:,} D1 rows, over the {MAX_PUSH_ROWS:,} cap "
                         "(free tier: 100,000 a day for the whole dashboard)")
    if not (account_id and database_id):
        account_id, database_id = d1_ids()
    token = token or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise RuntimeError("CLOUDFLARE_API_TOKEN is not set (root .env or the environment)")
    url = API.format(account=account_id, database=database_id)
    s = session or requests.Session()
    written = 0
    for i, sql in enumerate(stmts, 1):
        r = s.post(url, headers={"Authorization": f"Bearer {token}"}, json={"sql": sql}, timeout=60)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not body.get("success"):
            why = "; ".join(str(e.get("message", ""))[:200] for e in body.get("errors") or [] if isinstance(e, dict))
            raise RuntimeError(f"D1 statement {i} of {len(stmts)} failed (HTTP {r.status_code}{': ' + why if why else ''}). "
                               "The dashboard shows 'being updated' for that range until a push completes.")
        written += sum(int((x.get("meta") or {}).get("rows_written") or 0) for x in body.get("result") or [])
    return {"statements": len(stmts), "rowsEstimate": estimate, "rowsWritten": written,
            "ranges": [(snap.get("range") or {}).get("key") for snap in snapshots]}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m analytics.convintel.d1push", description=__doc__.splitlines()[0])
    p.add_argument("snapshots", nargs="+", help="snapshot JSON files from `python -m analytics.convintel report`")
    p.add_argument("--sql", help="write the statements to this file (for wrangler d1 execute --file)")
    p.add_argument("--push", action="store_true", help="run them against the remote D1 database")
    p.add_argument("--yes", action="store_true", help="the user has given the go-ahead for this remote write")
    a = p.parse_args(argv)
    snaps = []
    for f in a.snapshots:
        with open(f, encoding="utf-8") as fh:
            clean, scrubbed = scrub(json.load(fh))
        snap, kept = fit(clean)
        snaps.append(snap)
        if scrubbed:
            print(f"{(snap.get('range') or {}).get('key')}: removed what looked like a phone number from "
                  f"{len(scrubbed):,} text{'s' if len(scrubbed) > 1 else ''} ({', '.join(scrubbed[:3])}"
                  f"{' ...' if len(scrubbed) > 3 else ''})",
                  file=sys.stderr)
        cut = [f"{k} {n:,} -> {m:,}" for k, (n, m) in kept.items() if m < n]
        if cut:
            print(f"{(snap.get('range') or {}).get('key')}: trimmed to fit the dashboard ({', '.join(cut)}); "
                  "totals unchanged", file=sys.stderr)
    try:
        stmts = statements(snaps)
    except ValueError as e:
        sys.exit(f"Refused: {e}")
    for snap in snaps:
        key = snap["range"]["key"]
        n = sum(s.startswith(f"{INSERT_PART}({q(key)}, ") for s in stmts)
        print(f"{key}: {len(compact(snap).encode('utf-8')):,} bytes in {n} parts", file=sys.stderr)
    print(f"{len(stmts)} statements, about {rows_estimate(stmts):,} D1 rows written", file=sys.stderr)
    if a.sql:
        write_sql(a.sql, snaps)
        print(f"wrote {a.sql}; load it with: cd dashboard && npx wrangler d1 execute sales_dashboard --remote "
              f"--file {os.path.relpath(a.sql, 'dashboard')}  (a remote write: needs the user's go-ahead)", file=sys.stderr)
    if a.push:
        if not a.yes:
            sys.exit(GO_AHEAD)
        from integrations.env import load_dotenv
        load_dotenv()
        print(json.dumps(push(snaps, yes=True)))


if __name__ == "__main__":
    main()
