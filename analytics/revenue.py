"""Revenue from payment activities (LeadSquared event 213, "Payment Successful") per caller and course.

Plan action 6 (repo step 2). The amount and course are read from whichever activity field carries
them (mx_Custom_* or note keys named like amount / course), and each payment is credited to the
caller of the lead's last answered call before it, else the lead owner. No payment activity was
logged for 5-8 Oct 2026, so the report says plainly when there is nothing to read.

The average fee per enrolment is a setting with no default (--avg-fee or REVENUE_AVG_FEE_INR). When
set, enrolments x fee is shown as an estimate, labelled as such, next to any measured revenue.

    python -m analytics.revenue data/snapshot.json|data/report_YYYY-MM-DD exports/revenue [--avg-fee N] [--amount-field mx_Custom_2]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict

from analytics.team_report import utc

AMOUNT_KEY = re.compile(r"amount|amt|paid|price|fee", re.I)
COURSE_KEY = re.compile(r"course|product|program", re.I)


def _num(v) -> float | None:
    try:
        x = float(re.sub(r"[^\d.]", "", str(v))) if v not in (None, "") else None
    except ValueError:
        return None
    return x if x and x > 0 else None


def payment_fields(a: dict, amount_field: str | None = None, course_field: str | None = None) -> tuple[float | None, str]:
    """Amount and course of one payment activity: the named fields, else any field named like them."""
    fields = {**(a.get("note") or {}), **{k: v for k, v in a.items() if isinstance(v, (str, int, float))}}
    if amount_field or course_field:
        return _num(fields.get(amount_field)) if amount_field else None, str(fields.get(course_field) or "").strip()
    amount = next((x for k, v in fields.items() if AMOUNT_KEY.search(k) and (x := _num(v))), None)
    course = next((str(v).strip() for k, v in fields.items() if COURSE_KEY.search(k) and str(v or "").strip()), "")
    return amount, course


def load(path: str) -> dict:
    if os.path.isdir(path):
        j = lambda n: json.load(open(os.path.join(path, n))) if os.path.exists(os.path.join(path, n)) else []  # noqa: E731
        return {"payments": j("payments.json"), "calls": j("calls.json"), "users": j("users.json"),
                "leads": j("enrollments.json"), "enrolments": len(j("enrollments.json"))}
    snap = json.load(open(path))
    return {**snap, "enrolments": sum(1 for l in snap.get("leads", []) if l.get("ProspectStage") == "Course Enrolled")}


def revenue(data: dict, avg_fee: float | None = None, amount_field: str | None = None, course_field: str | None = None) -> dict:
    users = {u["ID"]: f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip() for u in data.get("users", [])}
    owners = {l["ProspectID"]: l.get("OwnerIdName") or users.get(l.get("OwnerId"), "") for l in data.get("leads", [])}
    answered = defaultdict(list)
    for c in data.get("calls", []):
        if c.get("status") == "Answered" and (t := utc(c.get("start_utc"))):
            answered[c["lead_id"]].append((t, users.get(c.get("user_id")) or (c.get("caller") or "").strip()))
    by_caller, by_course, rows, no_amount = defaultdict(float), defaultdict(float), [], 0
    for a in data.get("payments", []):
        lead, t = a.get("RelatedProspectId"), utc(a.get("CreatedOn"))
        amount, course = payment_fields(a, amount_field, course_field)
        prior = sorted(x for x in answered.get(lead, []) if t and x[0] <= t)
        caller = prior[-1][1] if prior else owners.get(lead) or "(unknown)"
        rows.append({"lead_id": lead, "paid_utc": a.get("CreatedOn"), "amount": amount, "course": course or "(unknown)",
                     "credited_to": caller})
        if amount is None:
            no_amount += 1
            continue
        by_caller[caller] += amount
        by_course[course or "(unknown)"] += amount
    out = {
        "payments": len(rows), "payments_without_amount": no_amount, "revenue_inr": round(sum(by_caller.values())),
        "by_caller": sorted(({"caller": k, "revenue_inr": round(v)} for k, v in by_caller.items()), key=lambda r: -r["revenue_inr"]),
        "by_course": sorted(({"course": k, "revenue_inr": round(v)} for k, v in by_course.items()), key=lambda r: -r["revenue_inr"]),
        "rows": rows, "enrolments": data.get("enrolments", 0), "avg_fee_inr": avg_fee,
        "estimate_inr": round(avg_fee * data.get("enrolments", 0)) if avg_fee else None,
    }
    if not rows:
        out["flag"] = ("No payment activities (event 213) in this data, so revenue cannot be measured. "
                       "Confirm which system records payments, with amount and course.")
    elif no_amount == len(rows):
        out["flag"] = "Payment activities exist but none carries an amount field; check the event 213 fields."
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("out")
    ap.add_argument("--avg-fee", type=float, default=float(os.environ["REVENUE_AVG_FEE_INR"]) if os.environ.get("REVENUE_AVG_FEE_INR") else None,
                    help="average fee collected per enrolment, in rupees (no default)")
    ap.add_argument("--amount-field", help="activity field holding the amount, e.g. mx_Custom_2, once event 213 is inspected")
    ap.add_argument("--course-field", help="activity field holding the course")
    a = ap.parse_args()
    r = revenue(load(a.data), a.avg_fee, a.amount_field, a.course_field)
    os.makedirs(a.out, exist_ok=True)
    json.dump({k: v for k, v in r.items() if k != "rows"}, open(os.path.join(a.out, "revenue.json"), "w"), indent=1)
    if r["rows"]:
        with open(os.path.join(a.out, "payments.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(r["rows"][0]))
            w.writeheader()
            w.writerows(r["rows"])
    print(r.get("flag") or f"{r['payments']} payments, ₹{r['revenue_inr']:,} measured ({r['payments_without_amount']} without an amount)")
    if r["estimate_inr"] is not None:
        print(f"Estimate (not measured): {r['enrolments']} enrolments × ₹{r['avg_fee_inr']:,.0f} average fee = ₹{r['estimate_inr']:,}")
    else:
        print("No average fee set (--avg-fee or REVENUE_AVG_FEE_INR), so no rupee estimate.")


if __name__ == "__main__":
    main()
