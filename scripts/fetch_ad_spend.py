"""Daily campaign spend, clicks and leads from Google Ads and Meta into one CSV (read-only).

Google ``conversions`` and Meta ``leads`` are each platform's own count, not LeadSquared
leads or enrolments; join to LeadSquared by campaign/source before judging a campaign.

    PYTHONPATH=. python scripts/fetch_ad_spend.py 2026-10-01 2026-10-07 exports/ad_spend_2026-10-01_2026-10-07.csv
"""

from __future__ import annotations

import csv
import os
import sys

import integrations  # noqa: F401  (loads .env)
from integrations.google_ads import GoogleAdsClient
from integrations.http import ApiError
from integrations.meta_ads import MetaAdsClient

COLUMNS = ["platform", "account_id", "date", "campaign_id", "campaign", "spend_inr", "impressions", "clicks",
           "platform_leads"]


def rows(start: str, end: str) -> list[dict]:
    out = []
    for r in GoogleAdsClient().campaign_daily(start, end):
        out.append({"platform": "google", "account_id": r["customer_id"], "date": r["date"],
                    "campaign_id": r["campaign_id"], "campaign": r["campaign"], "spend_inr": r["cost_inr"],
                    "impressions": r["impressions"], "clicks": r["clicks"], "platform_leads": r["conversions"]})
    meta = MetaAdsClient()
    for acc in meta.ad_accounts():
        for r in meta.campaign_daily(acc["id"], start, end):
            out.append({"platform": "meta", **{k: r[k] for k in ("account_id", "date", "campaign_id", "campaign")},
                        "spend_inr": r["spend_inr"], "impressions": r["impressions"], "clicks": r["clicks"],
                        "platform_leads": r["leads"]})
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    start, end, path = argv
    try:
        data = rows(start, end)
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(data)
    print(f"{len(data)} campaign-days -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
