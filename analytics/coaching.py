"""Weekly coaching pack: Zipteams objections and quality per caller and course, plus each caller's longest calls.

Plan action 4 (repo steps 3a and 3b). Step 3a needs no transcript budget: Zipteams objection fields
on the lead and its 237 notes are already in a fetch_team_data.py snapshot. Step 3b samples each
caller's 5 longest real conversations; with --transcripts the sample is searched within the API
limits (bottom converters first, 90 leads a run), saved under data/ and scored for the close
behaviours in analytics.call_markers.

    python -m analytics.coaching data/snapshot.json exports/coaching [--per-caller 5] [--transcripts]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict

from analytics.call_markers import MARKERS, NEXT_STEP, PAYMENT_STEP, markers_in, summary_has_payment_step
from analytics.lead_priority import strip_html
from analytics.team_report import IST, utc, zip_score

REAL_SECS = 120
ENROLLED = "Course Enrolled"


def _name(u: dict) -> str:
    return f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip()


def _split(v) -> list[str]:
    return [x.strip() for x in re.split(r"[,;|]", str(v or "")) if x.strip()]


def last_answered_caller(snap: dict) -> dict[str, list[tuple]]:
    users = {u["ID"]: _name(u) for u in snap["users"]}
    out = defaultdict(list)
    for c in snap["calls"]:
        t = utc(c.get("start_utc"))
        if t and c.get("status") == "Answered" and c.get("user_id") in users:
            out[c["lead_id"]].append((t, users[c["user_id"]]))
    for v in out.values():
        v.sort()
    return out


def objections(snap: dict) -> list[dict]:
    """Step 3a: leads per (caller, course, objection category), and how many of them enrolled anyway."""
    callers = last_answered_caller(snap)
    rows = Counter()
    won = Counter()
    for l in snap["leads"]:
        cats = _split(l.get("mx_Zip_Objection_Category"))
        if not cats:
            continue
        lid = l["ProspectID"]
        caller = callers[lid][-1][1] if callers.get(lid) else (l.get("OwnerIdName") or "(unknown)")
        course = l.get("mx_Enquired_Course") or "(blank)"
        for cat in cats:
            rows[(caller, course, cat)] += 1
            won[(caller, course, cat)] += l.get("ProspectStage") == ENROLLED
    return [{"caller": k[0], "course": k[1], "objection": k[2], "leads": n, "enrolled": won[k],
             "enrolled_%": round(100 * won[k] / n)} for k, n in sorted(rows.items(), key=lambda x: (-x[1], x[0]))]


def quality(snap: dict) -> list[dict]:
    """Step 3a: Zipteams pass rates per caller (237 notes credited to the lead's last answered call before them)."""
    callers = last_answered_caller(snap)
    per = defaultdict(lambda: defaultdict(list))
    for a in snap.get("zip_activities", []):
        t = utc(a.get("CreatedOn"))
        if str(a.get("ActivityEvent")) != "237" or not t:
            continue
        prior = [n for at, n in callers.get(a.get("RelatedProspectId"), []) if at <= t]
        if not prior:
            continue
        q = per[prior[-1]]
        q["n"].append(1)
        for key, field in (("probing_%", "mx_Custom_5"), ("pitch_%", "mx_Custom_4"), ("objection_handling_%", "mx_Custom_6")):
            if (s := zip_score(a.get(field))) is not None:
                q[key].append(s)
        q["payment_step_%"].append(100 * summary_has_payment_step(strip_html(a.get("ActivityEvent_Note"))))
    for l in snap["leads"]:
        lid = l["ProspectID"]
        if callers.get(lid) and (s := zip_score(l.get("mx_Zip_Quality_Score"))) is not None:
            per[callers[lid][-1][1]]["lead_quality_score"].append(s)
    out = []
    for caller, q in sorted(per.items()):
        row = {"caller": caller, "zip_notes": len(q["n"])}
        for k in ("probing_%", "pitch_%", "objection_handling_%", "payment_step_%", "lead_quality_score"):
            row[k] = round(statistics.mean(q[k])) if q[k] else None
        out.append(row)
    return out


def longest_calls(snap: dict, per_caller: int = 5) -> list[dict]:
    """Step 3b: each caller's longest real conversations in the snapshot, one per lead."""
    users = {u["ID"]: _name(u) for u in snap["users"]}
    leads = {l["ProspectID"]: l for l in snap["leads"]}
    best: dict[tuple, dict] = {}
    for c in snap["calls"]:
        if c.get("status") != "Answered" or (c.get("duration") or 0) < REAL_SECS or c.get("user_id") not in users:
            continue
        k = (users[c["user_id"]], c["lead_id"])
        if k not in best or c["duration"] > best[k]["duration"]:
            best[k] = c
    by_caller = defaultdict(list)
    for (caller, lid), c in best.items():
        by_caller[caller].append(c)
    out = []
    for caller in sorted(by_caller):
        for c in sorted(by_caller[caller], key=lambda c: -c["duration"])[:per_caller]:
            l = leads.get(c["lead_id"], {})
            out.append({"caller": caller, "lead_id": c["lead_id"], "lead_number": c.get("lead_number"),
                        "at_ist": utc(c["start_utc"]).astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                        "minutes": round(c["duration"] / 60, 1), "course": l.get("mx_Enquired_Course") or "",
                        "stage": l.get("ProspectStage") or "", "enrolled": l.get("ProspectStage") == ENROLLED})
    return out


def score_sample(sample: list[dict], transcripts: dict[str, str]) -> list[dict]:
    """Per caller: share of sampled calls with each close behaviour. ``transcripts`` maps lead_id -> text."""
    per = defaultdict(list)
    for s in sample:
        if s["lead_id"] in transcripts:
            per[s["caller"]].append(markers_in(transcripts[s["lead_id"]]))
    return [{"caller": c, "calls_read": len(m),
             **{k: round(100 * sum(k in x for x in m) / len(m)) for k in MARKERS}} for c, m in sorted(per.items())]


def fetch_sample_transcripts(sample: list[dict], conversion: dict[str, float], out_dir: str) -> dict[str, str]:
    """Search the sample's numbers (bottom converters first) within 9 requests of 10 numbers; keep the longest per lead."""
    from integrations.transcripts import TranscriptClient
    from integrations.transcripts.client import normalize_phone

    order = sorted(sample, key=lambda s: (conversion.get(s["caller"], 0), s["caller"], -s["minutes"]))
    lead_of = {}
    for s in order:
        if (k := normalize_phone(s.get("lead_number"))) and k not in lead_of:
            lead_of[k] = s["lead_id"]
    nums = list(lead_of)[:90]
    tc = TranscriptClient(max_retries=0, timeout=120)
    texts: dict[str, tuple[int, str]] = {}
    time.sleep(60)  # the API allows 10 requests a minute, counting earlier runs
    for i in range(0, len(nums), 10):
        if i:
            time.sleep(7)
        try:
            for x in tc.search(nums[i:i + 10]):
                lid = lead_of.get(normalize_phone(x.phone))
                if lid and x.transcript.strip() and (x.duration or 0) >= REAL_SECS and (x.duration or 0) > texts.get(lid, (0, ""))[0]:
                    texts[lid] = (x.duration or 0, x.transcript)
        except Exception as e:  # noqa: BLE001 - one failed chunk must not lose the rest
            print(f"transcript chunk {i // 10 + 1} failed: {str(e)[:120]}", file=sys.stderr)
    out = {k: v[1] for k, v in texts.items()}
    os.makedirs(out_dir, exist_ok=True)
    json.dump(out, open(os.path.join(out_dir, "coaching_transcripts.json"), "w"))  # PII: data/ only
    return out


def write_csv(path: str, rows: list[dict]) -> None:
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("out")
    ap.add_argument("--per-caller", type=int, default=5)
    ap.add_argument("--transcripts", action="store_true", help="search the sample's transcripts (saved under data/)")
    a = ap.parse_args()
    snap = json.load(open(a.snapshot))
    os.makedirs(a.out, exist_ok=True)
    obj, qual, sample = objections(snap), quality(snap), longest_calls(snap, a.per_caller)
    write_csv(os.path.join(a.out, "objections.csv"), obj)
    write_csv(os.path.join(a.out, "quality.csv"), qual)
    write_csv(os.path.join(a.out, "coaching_sample.csv"), [{k: v for k, v in s.items() if k != "lead_number"} for s in sample])
    print(f"{len(obj)} caller/course/objection rows, {len(qual)} callers scored, {len(sample)} calls in the coaching sample")
    if a.transcripts:
        conv = {}
        for s in sample:
            conv.setdefault(s["caller"], []).append(s["enrolled"])
        texts = fetch_sample_transcripts(sample, {c: sum(v) / len(v) for c, v in conv.items()}, "data")
        scored = score_sample(sample, texts)
        write_csv(os.path.join(a.out, "close_behaviour.csv"), scored)
        for r in sorted(scored, key=lambda r: r[PAYMENT_STEP]):
            print(f"{r['caller'][:24]:24} read {r['calls_read']}: payment step {r[PAYMENT_STEP]}%, dated next step {r[NEXT_STEP]}%")


if __name__ == "__main__":
    main()
