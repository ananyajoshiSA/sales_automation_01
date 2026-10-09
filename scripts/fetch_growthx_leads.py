"""GrowthX funnel leads and bootcamp checkouts captured on IST days, one JSON line each (read-only).

Output holds lead PII (name, WhatsApp, email), so write it under data/ (git-ignored). Each row
keeps GrowthX's own fields plus ``captured_ist``. ``amount`` on a Paid row is a bootcamp ticket
(Rs 10-200), not a course fee.

    PYTHONPATH=. python scripts/fetch_growthx_leads.py 2026-10-08 2026-10-08 data/growthx_leads_2026-10-08.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

import integrations  # noqa: F401  (loads .env)
from integrations.growthx import GrowthXClient
from integrations.http import ApiError


def summary(rows: list[dict]) -> str:
    kinds = Counter(r.get("funnelType") or "?" for r in rows)
    status = Counter(r.get("status") or "?" for r in rows if r.get("funnelType") == "paid")
    paid = sum(float(r["amount"]) for r in rows
               if r.get("status") == "Paid" and str(r.get("amount") or "").replace(".", "", 1).isdigit())
    return (f"{len(rows)} leads: {kinds.get('lead', 0)} form fills, {kinds.get('paid', 0)} bootcamp checkouts "
            f"({', '.join(f'{k} {v}' for k, v in status.most_common())}); Rs {paid:,.0f} in paid tickets")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    start, end, path = argv
    try:
        rows, bad = GrowthXClient().leads_for_ist_days(start, end)
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{summary(rows)} -> {path}")
    if bad:
        print(f"Warning: {bad} rows had an unreadable capturedAt and were left out", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
