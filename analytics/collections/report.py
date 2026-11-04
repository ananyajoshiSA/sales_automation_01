"""The measures in both collection reports: money and students by month, team, collector and community or bootcamp,
who to call first, and the data checks (matching, pairing, disagreements, leads missing from the sheet)."""

from __future__ import annotations

from collections import Counter, defaultdict

from analytics.collections.match import NOT_PAIRED, OTHER, disagreement
from analytics.collections.tags import tag_date, tag_kind

OPEN = ("Pending", "Instalments", "Not stated", "Other")
# LeadSquared stages that mean a collection is still being worked; a tagged lead in one but missing from the sheet
# is worth a look.
WORKING_STAGES = {"booking fees received", "loan pending", "follow up for closure", "call back later",
                  "call not picking up", "wants to go on loan"}


def pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


def summarise(rows: list[dict]) -> dict:
    out = Counter(r["outcome"] for r in rows)
    value = sum(r["price"] for r in rows)
    received = sum(r["received"] for r in rows)
    dropped_value = sum(r["balance"] for r in rows if r["outcome"] == "Dropped")
    open_balance = sum(r["balance"] for r in rows if r["outcome"] in OPEN and r["balance"] > 0)
    return {
        "students": len(rows), "collected": out["Collected"], "instalments": out["Instalments"],
        "pending": out["Pending"], "dropped": out["Dropped"], "not_stated": out["Not stated"] + out["Other"],
        "course_value": round(value), "received": round(received),
        "subvention": round(sum(r["subvention"] for r in rows)), "discount": round(sum(r["discount"] for r in rows)),
        "balance_open": round(open_balance), "balance_dropped": round(dropped_value),
        "recovered_%_amount": pct(received, value), "collected_%_students": pct(out["Collected"], len(rows)),
        "dropped_%_students": pct(out["Dropped"], len(rows)),
    }


def group(rows: list[dict], *keys: str) -> list[dict]:
    g: dict[tuple, list] = defaultdict(list)
    for r in rows:
        g[tuple(r.get(k) or "(blank)" for k in keys)].append(r)
    out = [{**dict(zip(keys, k)), **summarise(rs)} for k, rs in g.items()]
    return sorted(out, key=lambda x: (-x["balance_open"], *(str(x[k]) for k in keys)))


def next_step(r: dict) -> str:
    bal = f"Rs {r['balance']:,.0f}"
    if r["outcome"] == "Instalments":
        return f"Confirm the next instalment ({bal} left)"
    if "loan" in (r["sheet_stage"] + " " + r["lsq_stage"]).lower():
        return f"Chase the loan file; {bal} due"
    return f"Call to collect the {bal} balance"


def call_list(rows: list[dict]) -> list[dict]:
    """Open balances, largest first: who to call and what to say."""
    todo = [r for r in rows if r["outcome"] in OPEN and r["balance"] > 0]
    todo.sort(key=lambda r: (-r["balance"], r["name"]))
    return [{"name": r["name"], "phone": r["phone"], "month": r["month"],
             "group": r.get("community_type") or r.get("bootcamp_raw") or "",
             "balance": round(r["balance"]), "status": r["status"], "sheet_stage": r["sheet_stage"],
             "collector": r["owner"], "lsq_owner": r["lsq_owner"], "lsq_stage": r["lsq_stage"],
             "expected_payment": r.get("expected_payment", ""), "next_step": next_step(r),
             "flag": disagreement(r)} for r in todo]


def missing_from_sheet(rows: list[dict], tagged: list[tuple[str, dict]], kind: str, months: set[str]) -> list[dict]:
    """Tagged LeadSquared leads of this kind, still being worked, that no sheet row matched."""
    seen = {r["lsq_id"] for r in rows if r["lsq_id"]}
    out = []
    for tag, l in tagged:
        if tag_kind(tag) != kind or l["ProspectID"] in seen:
            continue
        if (l.get("ProspectStage") or "").strip().lower() not in WORKING_STAGES:
            continue
        if kind == "bootcamp" and (tag_date(tag) or "")[:7] not in months:
            continue
        out.append({"lsq_id": l["ProspectID"], "name": " ".join(x for x in (l.get("FirstName"), l.get("LastName")) if x),
                    "tag": tag, "lsq_stage": l.get("ProspectStage") or "", "lsq_owner": l.get("OwnerIdName") or "",
                    "funnel": l.get("mx_Funnel_name") or ""})
    return sorted(out, key=lambda x: (x["tag"], x["lsq_owner"], x["name"]))


def build(rows: list[dict], tagged: list[tuple[str, dict]], kind: str, sheet_note: dict, meta: dict,
          pairs: dict | None = None, sheet_read_ist: str = "") -> dict:
    """The report for one kind. ``sheet_read_ist`` is when the sheet was downloaded (the LeadSquared read time if
    unknown, as the fetch downloads both together)."""
    for r in rows:
        r["flag"] = disagreement(r)
        r["lsq_team"] = meta.get("team_of_owner", {}).get(r.get("lsq_owner_id") or "", "")
    by_what = "community_type" if kind == "community" else "bootcamp_raw"
    months = {r["month"] for r in rows}
    unmatched = [r for r in rows if not r["lsq_id"]]
    R = {
        "kind": kind, "fetched_at_ist": meta.get("fetched_at_ist", ""),
        "sheet_read_ist": sheet_read_ist or meta.get("fetched_at_ist", ""), "sheet": sheet_note,
        "total": summarise(rows),
        "by_month": group(rows, "month"),
        "by_team": group(rows, "team"),
        "by_collector": group(rows, "team", "owner"),
        "by_group": group(rows, by_what) if kind == "community" else group(rows, "bootcamp_raw", "start_date", "bootcamp_tag"),
        "call_list": call_list(rows),
        "checks": {
            "students": len(rows),
            "matched": len(rows) - len(unmatched),
            "matched_%": pct(len(rows) - len(unmatched), len(rows)),
            "matched_by": dict(Counter(r["matched_by"] for r in rows if r["matched_by"])),
            "not_matched_reasons": dict(Counter(r["match_note"] for r in unmatched)),
            "not_matched_amount_received": round(sum(r["received"] for r in unmatched)),
            "flags": dict(Counter(r["flag"].split(";")[0] for r in rows if r["flag"])),
            "missing_from_sheet": missing_from_sheet(rows, tagged, kind, months),
        },
        "unmatched": [{k: r[k] for k in ("name", "phone", "email", "month", "match_note", "status", "balance", "owner")}
                      for r in unmatched],
        "flagged": [{k: r.get(k, "") for k in ("name", "phone", "month", "status", "balance", "owner", "lsq_stage",
                                                 "lsq_owner", "flag")} for r in rows if r["flag"]],
    }
    if kind == "community":
        R["checks"]["type_from"] = dict(Counter(r["community_from"] or OTHER for r in rows))
        R["checks"]["type_other"] = sum(1 for r in rows if r["community_type"] == OTHER)
        R["checks"]["type_differs"] = sum(1 for r in rows if r["community_differs"])
    else:
        pairs = pairs or {}
        R["checks"]["bootcamps"] = len(pairs)
        R["checks"]["bootcamps_not_paired"] = sum(1 for p in pairs.values() if p["tag"] == NOT_PAIRED)
        R["checks"]["paired_by"] = dict(Counter("students" if "carry" in p["how"] else "date and name"
                                                for p in pairs.values() if p["tag"] != NOT_PAIRED))
    R["rows"] = rows
    return R


