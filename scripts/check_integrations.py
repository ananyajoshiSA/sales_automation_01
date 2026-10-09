"""Read-only connection check for Google Ads, Meta Ads, Zoom, TimePay and GrowthX. Prints no secrets.

    PYTHONPATH=. python scripts/check_integrations.py [google meta zoom timepay growthx]
"""

from __future__ import annotations

import sys
from datetime import timedelta

import integrations  # noqa: F401  (loads .env)
from integrations.google_ads import GoogleAdsClient
from integrations.growthx import GrowthXClient
from integrations.http import ApiError
from integrations.meta_ads import MetaAdsClient
from integrations.timepay import TimePayClient
from integrations.timeutil import now_ist
from integrations.zoom import ACCOUNTS, ZoomClient


def google() -> str:
    c = GoogleAdsClient()
    end = now_ist().date() - timedelta(days=1)
    rows = c.campaign_daily(end - timedelta(days=6), end)
    spend = sum(r["cost_inr"] for r in rows)
    return f"{len(c.list_accessible_customers())} accessible manager account(s); last 7 days across " \
           f"{len(c.customer_ids)} ad accounts: {len(rows)} campaign-days, Rs {spend:,.0f} spend"


def meta() -> str:
    accounts = MetaAdsClient().ad_accounts()
    return f"{len(accounts)} ad accounts readable"


def zoom() -> str:
    out, failed = [], False
    for name in ACCOUNTS:
        try:
            out.append(f"{name}: {len(ZoomClient(name).users())} users")
        except ApiError as exc:
            failed = True
            out.append(f"{name}: failed ({exc})")
    if failed:
        raise ApiError("; ".join(out))
    return "; ".join(out)


def timepay() -> str:
    c = TimePayClient()
    d = (now_ist().date() - timedelta(days=1)).isoformat()
    n = c.count_logs(f"{d}T00:00:00", f"{d}T23:59:59", type="call")
    first = next(iter(c.paged("campaigns", max_pages=1)), None)
    return f"{n:,} call logs yesterday ({d}); campaigns readable: {'yes' if first else 'none found'}"


def growthx() -> str:
    c = GrowthXClient()
    d = (now_ist().date() - timedelta(days=1)).isoformat()
    return f"{c.total():,} leads in all; {c.total(d, d):,} captured on {d} (UTC day)"


CHECKS = {"google": google, "meta": meta, "zoom": zoom, "timepay": timepay, "growthx": growthx}


def main(argv: list[str]) -> int:
    names = argv or list(CHECKS)
    bad = 0
    for name in names:
        try:
            print(f"{name:8} OK   {CHECKS[name]()}")
        except (ApiError, KeyError) as exc:
            bad += 1
            print(f"{name:8} FAIL {exc}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
