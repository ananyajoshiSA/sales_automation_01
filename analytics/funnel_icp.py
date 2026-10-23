"""Leads and sales per ICP for a bootcamp campaign, read from the GrowthX landing-page funnels.

Each person (last 10 phone digits, else email) is credited to the first funnel they filled, and that
funnel's landing page is mapped to one ICP in ``analytics/funnel_icp_maps/<bootcamp>.json``. The ICPs
there are the ones the TimePay discovery agent uses, plus "Teachers / educators" and "General".
A sale is a row in the enrolment sheet's bootcamp tab whose status is in ``SALE_STATUSES``, matched to a
GrowthX lead by phone, then email.

New campaigns bring new landing pages. ``pages`` lists every page not yet in the map, with its headline
text, so whoever runs it (Claude, reading each page) can add it to the map. ``report`` refuses to price
a campaign while pages are unmapped, unless ``--allow-unmapped``.

    python -m analytics.funnel_icp pages  <bootcamp> <leadtype> [--from YYYY-MM-DD --to YYYY-MM-DD] <out_dir>
    python -m analytics.funnel_icp report <bootcamp> <leadtype> <sales.xlsx> <tab> <out_dir> [--from .. --to ..]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Iterable

import requests

MAPS_DIR = os.path.join(os.path.dirname(__file__), "funnel_icp_maps")
SALE_STATUSES = {"Disbursed", "One shot", "Installments", "Booking fees received"}
UNMAPPED = "UNMAPPED"


def phone10(value: Any) -> str:
    if isinstance(value, float):
        value = int(value)
    return re.sub(r"\D", "", str(value or ""))[-10:]


def email_key(value: Any) -> str:
    return str(value or "").strip().lower()


def page_slug(url: str | None) -> str:
    path = (url or "").split("?")[0].split("#")[0]
    return path.split("/f/", 1)[1].strip("/") if "/f/" in path else ""


def load_map(bootcamp: str) -> dict:
    with open(os.path.join(MAPS_DIR, f"{bootcamp}.json"), encoding="utf-8") as f:
        return json.load(f)


def person_key(row: dict) -> str:
    return phone10(row.get("whatsapp") or row.get("phone")) or email_key(row.get("email"))


def first_touch(rows: Iterable[dict]) -> dict[str, dict]:
    """Each person's earliest lead row (by ``captured_ist``, then ``capturedAt`` order as given)."""
    best: dict[str, dict] = {}
    for row in rows:
        key = person_key(row)
        if not key:
            continue
        if key not in best or (row.get("captured_ist") or "") < (best[key].get("captured_ist") or ""):
            best[key] = row
    return best


def read_sales(xlsx_path: str, tab: str) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    rows = wb[tab].iter_rows(values_only=True)
    head = [str(h or "").strip().lower() for h in next(rows)]

    def col(prefix: str) -> int:
        return next(i for i, h in enumerate(head) if h.startswith(prefix))

    c_name, c_phone, c_email = col("student name"), col("phone"), col("email")
    c_paid, c_status = col("money received"), col("status of the lead")
    out = []
    for r in rows:
        if not r or not r[c_name] or str(r[c_status] or "").strip() not in SALE_STATUSES:
            continue
        paid = r[c_paid]
        out.append({"phone": phone10(r[c_phone]), "email": email_key(r[c_email]),
                    "paid": float(paid) if isinstance(paid, (int, float)) else 0.0})
    return out


def build(leads: list[dict], sales: list[dict], icp_map: dict) -> dict:
    """Aggregate first-touch leads and matched sales per ICP and per funnel."""
    pages = icp_map["pages"]
    people = first_touch(leads)
    by_email = {email_key(r.get("email")): k for k, r in people.items() if r.get("email")}

    def icp_of(row: dict) -> str:
        return pages.get(page_slug(row.get("pageUrl")), UNMAPPED)

    icp = {name: {"leads": 0, "sales": 0, "paid": 0.0} for name in icp_map["icps"]}
    funnels: dict[tuple, dict] = defaultdict(lambda: {"leads": 0, "sales": 0, "paid": 0.0})
    unmapped: Counter = Counter()
    for row in people.values():
        name = icp_of(row)
        icp.setdefault(name, {"leads": 0, "sales": 0, "paid": 0.0})["leads"] += 1
        funnels[(row.get("funnel") or "", page_slug(row.get("pageUrl")), name)]["leads"] += 1
        if name == UNMAPPED:
            unmapped[(row.get("pageUrl") or "").split("?")[0]] += 1
    matched = Counter()
    for sale in sales:
        key = sale["phone"] if sale["phone"] in people else by_email.get(sale["email"]) if sale["email"] else None
        matched["phone" if key == sale["phone"] and key else "email" if key else "not in GrowthX"] += 1
        if not key:
            continue
        row = people[key]
        name = icp_of(row)
        for bucket in (icp[name], funnels[(row.get("funnel") or "", page_slug(row.get("pageUrl")), name)]):
            bucket["sales"] += 1
            bucket["paid"] += sale["paid"]
    return {"icp": icp, "funnels": dict(funnels), "unmapped": dict(unmapped), "matched": dict(matched),
            "people": len(people), "sales_total": len(sales)}


def page_text(url: str, timeout: float = 20) -> str:
    try:
        body = requests.get(url, timeout=timeout).text
    except requests.RequestException as exc:
        return f"(could not open: {exc.__class__.__name__})"
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())[:700]


def fetch_leads(leadtype: str, start: str | None, end: str | None) -> list[dict]:
    import integrations.env  # noqa: F401  loads .env
    from integrations.growthx.client import GrowthXClient, captured_ist

    client = GrowthXClient()
    if start and end:
        return client.leads_for_ist_days(start, end, leadtype=leadtype)[0]
    rows = []
    for row in client.leads(leadtype=leadtype):
        at = captured_ist(row.get("capturedAt"))
        rows.append({**row, "captured_ist": at.strftime("%Y-%m-%dT%H:%M") if at else ""})
    return rows


def write_xlsx(result: dict, path: str, title: str) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "By ICP"
    ws.append([title])
    ws.append([f"{result['people']} unique leads (each credited to their first funnel); "
               f"{result['sales_total']} sales in the sheet, matched: {result['matched']}"])
    ws.append([])
    ws.append(["ICP", "Leads", "Share of leads", "Sales", "Conversion", "Money received (Rs)"])
    for name, a in sorted(result["icp"].items(), key=lambda x: -x[1]["leads"]):
        if not a["leads"] and not a["sales"]:
            continue
        ws.append([name, a["leads"], round(a["leads"] / max(result["people"], 1), 3), a["sales"],
                   round(a["sales"] / max(a["leads"], 1), 4), round(a["paid"])])
    fs = wb.create_sheet("By funnel")
    fs.append(["Funnel", "Landing page", "ICP", "Leads", "Sales", "Conversion", "Money received (Rs)"])
    for (funnel, slug, name), a in sorted(result["funnels"].items(), key=lambda x: -x[1]["leads"]):
        fs.append([funnel, slug, name, a["leads"], a["sales"], round(a["sales"] / max(a["leads"], 1), 4),
                   round(a["paid"])])
    if result["unmapped"]:
        us = wb.create_sheet("Unmapped pages")
        us.append(["Landing page", "Leads"])
        for url, n in sorted(result["unmapped"].items(), key=lambda x: -x[1]):
            us.append([url, n])
    wb.save(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pages", "report"):
        p = sub.add_parser(name)
        p.add_argument("bootcamp")
        p.add_argument("leadtype")
        if name == "report":
            p.add_argument("sales_xlsx")
            p.add_argument("tab")
            p.add_argument("--allow-unmapped", action="store_true")
        p.add_argument("out_dir")
        p.add_argument("--from", dest="start")
        p.add_argument("--to", dest="end")
    a = ap.parse_args(argv)
    icp_map = load_map(a.bootcamp)
    os.makedirs(a.out_dir, exist_ok=True)
    leads = fetch_leads(a.leadtype, a.start, a.end)

    if a.cmd == "pages":
        seen: dict[str, dict] = {}
        for row in leads:
            slug = page_slug(row.get("pageUrl"))
            if slug and slug not in icp_map["pages"]:
                entry = seen.setdefault(slug, {"url": row["pageUrl"].split("?")[0], "funnels": Counter(), "leads": 0})
                entry["leads"] += 1
                entry["funnels"][row.get("funnel") or ""] += 1
        out = [{"slug": s, "leads": e["leads"], "funnels": dict(e["funnels"]), "url": e["url"],
                "page_text": page_text(e["url"])} for s, e in sorted(seen.items(), key=lambda x: -x[1]["leads"])]
        path = os.path.join(a.out_dir, f"unmapped_pages_{a.leadtype}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        print(f"{len(leads)} leads, {len(out)} landing pages not in {a.bootcamp}.json -> {path}")
        return 0

    result = build(leads, read_sales(a.sales_xlsx, a.tab), icp_map)
    if result["unmapped"] and not a.allow_unmapped:
        print(f"{sum(result['unmapped'].values())} leads land on {len(result['unmapped'])} unmapped pages. "
              f"Run `pages` and add them to {a.bootcamp}.json first.", file=sys.stderr)
        return 2
    path = os.path.join(a.out_dir, f"ICP_funnel_report_{a.leadtype}.xlsx")
    write_xlsx(result, path, f"Leads and sales per ICP · {a.bootcamp} · GrowthX leadtype {a.leadtype}")
    for name, v in sorted(result["icp"].items(), key=lambda x: -x[1]["leads"]):
        if v["leads"] or v["sales"]:
            print(f"{name:40} {v['leads']:6} leads {v['sales']:4} sales "
                  f"{100 * v['sales'] / max(v['leads'], 1):5.1f}%  Rs {v['paid']:,.0f}")
    print(f"sales matched: {result['matched']} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
