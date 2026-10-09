"""TimePay AI voice-agent call logs for an IST time window, one JSON line per call (read-only).

The API gives 10 logs a page, so a whole day (about 43,000 calls on 8 Oct 2026) is over 4,000
requests; try a short window or ``--max-pages`` first. The window filters on TimePay's own queue
time, not the call's ``start_time`` (calls can start minutes later). Output holds phone numbers,
summaries and LeadSquared lead ids, so write it under data/ (git-ignored).

    PYTHONPATH=. python scripts/fetch_timepay_logs.py 2026-10-08T10:00:00 2026-10-08T10:05:00 data/timepay_2026-10-08_1000.jsonl [--max-pages N]
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

import integrations  # noqa: F401  (loads .env)
from integrations.http import ApiError
from integrations.timepay import TimePayClient


def main(argv: list[str]) -> int:
    max_pages = None
    if "--max-pages" in argv:
        i = argv.index("--max-pages")
        max_pages = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 3:
        print(__doc__)
        return 1
    start, end, path = argv
    try:
        tp = TimePayClient()
        total = tp.count_logs(start, end, type="call")
        print(f"{total:,} calls in the window ({-(-total // 10):,} pages)")
        seen, rows = set(), []
        for r in tp.iter_logs(start, end, max_pages=max_pages, type="call"):
            key = r.get("call_id") or json.dumps(r, sort_keys=True)
            if key not in seen:
                seen.add(key)
                rows.append(r)
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    top = Counter(r.get("disposition") or "?" for r in rows).most_common(5)
    print(f"{len(rows)} calls -> {path}; top outcomes: {', '.join(f'{k} {v}' for k, v in top)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
