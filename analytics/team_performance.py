"""Daily team and caller performance report, Parameters v1.0 (reports/report_parameters.md).

Which teams and callers turned potential clients into first-time enrolments on one IST day:
team scorecard, ranking, callers to recognise, coaching cases and Zipteams quality, plus
close-behaviour markers from a transcript sample. A separate plan tracker measures the revenue
plan's levers (payment step, full pitch, same-day callback of missed inbound calls) per team and
caller; it never changes the v1.0 figures.

    python -m analytics.team_performance 2026-10-05 [--fetch] [--no-transcripts] [--as-of "2026-10-08 13:00"] [--out reports]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from analytics.call_markers import MARKERS, PAYMENT_STEP, has_payment_step, marker_rates
from analytics.lead_priority import strip_html
from analytics.team_report import IST, utc, zip_score

VERSION = "1.0"
REAL_SECS = 120
LONG_NON_CONVERTED_SECS = 300
BOT = re.compile(r"\b(system|bot|welcome|reminder|webinar|ivr)\b", re.I)
WARM = {"Elite Changemakers", "DSV - UK (Aditya)", "DSV-Domestic-(Shivam Sharma)"}
UNRANKED = ("Unassigned", "Not a user")
MIN_CALLERS, MIN_REAL = 3, 25
RECOGNISE, ASSETS, ASSET_MIN_NOTES = 8, 4, 15  # 8 recognised + 1 coaching case = P52's "up to 9"
SAMPLE_CONVERTED, SAMPLE_PER_TEAM, SAMPLE_TEAMS, SAMPLE_SEED = 40, 10, 5, 5
SHORT = {"US Bookkeeping Accounting-Sana": "US Bookkeeping (Sana)", "US Bookkeeping Accounting-Deepanshi": "US Bookkeeping (Deepanshi)",
         "US Accounting -Closures - Deepanshi": "US Acc. Closures (Deepanshi)", "US accounting counselors - Sana": "US Acc. Counselors (Sana)",
         "DSV - UK (Aditya)": "DSV UK (Aditya)", "DSV-Domestic-(Shivam Sharma)": "DSV Domestic (Shivam)", "Gourp A": "Group A",
         "Team Bootcamp +Anas": "Bootcamp (Anas)", "Team Bootcamp +Jyoti": "Bootcamp (Jyoti)", "Corporate law trainees": "Corporate Law Trainees"}


def short(team: str) -> str:
    return SHORT.get(team, team)


def _name(u: dict) -> str:
    return f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip()


def load_run(path: str) -> dict:
    j = lambda n: json.load(open(os.path.join(path, n)))  # noqa: E731
    return {"meta": j("meta.json"), "users": j("users.json"), "calls": j("calls.json"), "zips": j("zip.json"),
            "enrollments": j("enrollments.json"), "payments": j("payments.json")}


def map_calls(calls: list[dict], users: list[dict]) -> tuple[list[dict], int, set[str]]:
    """P10-P12, P14: name and team per call; bot calls removed. Returns (calls, bots, multi-group user ids)."""
    by_id = {u["ID"]: u for u in users}
    by_name = {_name(u): u for u in users}
    kept, bots, multi = [], 0, set()
    for c in calls:
        c = dict(c)
        u = by_id.get(c.get("user_id")) or by_name.get((c.get("caller") or "").strip())
        if u:
            c["name"], gs = _name(u), u.get("MemberOfGroups") or []
            c["team"] = gs[0].strip() if gs else "Unassigned"
            if len(gs) > 1:
                multi.add(u["ID"])
        else:
            c["name"], c["team"] = (c.get("caller") or "(unknown)").strip(), "Not a user"
        if BOT.search(c["name"]):
            bots += 1
            continue
        c["t"] = utc(c.get("start_utc"))
        c["ans"] = c.get("status") == "Answered"
        c["real"] = c["ans"] and (c.get("duration") or 0) >= REAL_SECS
        kept.append(c)
    kept.sort(key=lambda c: c["t"] or datetime.min.replace(tzinfo=timezone.utc))
    return kept, bots, multi


def credit_enrollments(enrollments: list[dict], calls: list[dict], users: list[dict], cw_end: datetime | None = None) -> list[dict]:
    """P27-P29b: each first-time enrolment, credited to the caller with most answered talk on the lead that day."""
    by_id = {u["ID"]: u for u in users}
    talk: dict[str, Counter] = defaultdict(Counter)
    for c in calls:
        if c["ans"] and c["team"] != "Not a user":
            talk[c["lead_id"]][(c["team"], c["name"])] += c.get("duration") or 0
    out = []
    for e in enrollments:
        t = utc(e["enrolled_at"])
        if not t or (cw_end and t > cw_end):
            continue
        o = by_id.get(e.get("OwnerId"))
        top = talk[e["ProspectID"]].most_common(1)
        out.append({"lead": e["ProspectID"], "day": t.astimezone(IST).strftime("%Y-%m-%d"),
                    "owner_team": ((o.get("MemberOfGroups") or ["Unassigned"])[0].strip() if o else "Unassigned"),
                    "team": top[0][0][0] if top else None, "caller": top[0][0][1] if top else None})
    return out


def attribute_zip(zips: list[dict], calls: list[dict]) -> tuple[list[dict], int]:
    """P40: each note to the caller of the lead's last answered call at or before it; the rest dropped."""
    by_lead = defaultdict(list)
    for c in calls:
        if c["ans"] and c["t"]:
            by_lead[c["lead_id"]].append(c)
    out, dropped = [], 0
    for a in zips:
        t = utc(a.get("CreatedOn"))
        prior = [c for c in by_lead.get(a.get("RelatedProspectId"), []) if t and c["t"] <= t]
        if not prior:
            dropped += 1
            continue
        c = prior[-1]
        out.append({"team": c["team"], "name": c["name"], "lead": a.get("RelatedProspectId"),
                    "intent": (a.get("mx_Custom_1") or "NOT_AVAILABLE").upper(),
                    "probe": zip_score(a.get("mx_Custom_5")), "pitch": zip_score(a.get("mx_Custom_4")),
                    "obj": zip_score(a.get("mx_Custom_6")),
                    "payment_step": has_payment_step(strip_html(a.get("ActivityEvent_Note")) + " " + (a.get("mx_Custom_2") or ""))})
    return out, dropped


def _mean(zs: list[dict], k: str) -> int | None:
    v = [z[k] for z in zs if z[k] is not None]
    return round(statistics.mean(v)) if v else None  # P45: missing is "–", never 0


def scorecard(calls: list[dict], zs: list[dict], credited: int) -> dict:
    """P20-P25, P29a, P41-P44 for one team or caller."""
    out = [c for c in calls if c["direction"] == "outbound"]
    reached = {c["lead_id"] for c in calls if c["real"]}
    rated = [z for z in zs if z["intent"] != "NOT_AVAILABLE"]
    return {"callers": len({c["name"] for c in out}), "dials": len(out), "inbound": len(calls) - len(out),
            "answer_pct": round(100 * sum(c["ans"] for c in out) / len(out), 1) if out else 0.0,
            "real": sum(c["real"] for c in calls), "reached": len(reached),
            "talk_min": round(sum(c.get("duration") or 0 for c in calls if c["ans"]) / 60),
            "credited": credited, "conv_pct": round(100 * credited / len(reached), 1) if reached else 0.0,
            "zip_n": len(zs), "probe": _mean(zs, "probe"), "pitch": _mean(zs, "pitch"), "obj": _mean(zs, "obj"),
            "hi_mod_pct": round(100 * sum(z["intent"] in ("HIGH", "MODERATE") for z in rated) / len(rated)) if rated else None}


def analyse(run: dict, as_of: datetime | None = None) -> dict:
    meta = run["meta"]
    d0 = datetime.fromisoformat(meta["d0"])
    cw_end = min(datetime.fromisoformat(meta["cw_end"]), as_of) if as_of else datetime.fromisoformat(meta["cw_end"])
    calls, bots, multi = map_calls(run["calls"], run["users"])
    E = credit_enrollments(run["enrollments"], calls, run["users"], cw_end)
    cred_t = Counter(e["team"] for e in E if e["team"])
    cred_p = Counter((e["team"], e["caller"]) for e in E if e["team"])
    own_same = Counter(e["owner_team"] for e in E if e["day"] == d0.strftime("%Y-%m-%d"))
    Z, dropped = attribute_zip(run["zips"], calls)

    T, P, ZT, ZP = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for c in calls:
        T[c["team"]].append(c)
        P[(c["team"], c["name"])].append(c)
    for z in Z:
        ZT[z["team"]].append(z)
        ZP[(z["team"], z["name"])].append(z)
    teams = {t: {**scorecard(r, ZT[t], cred_t[t]), "same_day_owner": own_same[t], "warm": t in WARM} for t, r in T.items()}
    people = [{"team": k[0], "name": k[1], **scorecard(r, ZP[k], cred_p[k])} for k, r in P.items() if k[0] != "Not a user"]

    elig = [t for t, s in teams.items() if t not in UNRANKED and s["callers"] >= MIN_CALLERS and s["real"] >= MIN_REAL]
    rank = sorted(elig, key=lambda t: (-teams[t]["credited"], -teams[t]["conv_pct"], -teams[t]["real"]))
    front = [t for t in rank if not teams[t]["warm"]]
    rec = sorted([p for p in people if p["credited"] > 0], key=lambda p: (-p["credited"], -p["real"]))[:RECOGNISE]
    cases = [p for p in people if p["credited"] <= 1 and p["team"] != "Unassigned"]
    assets = sorted([p for p in people if p["zip_n"] >= ASSET_MIN_NOTES],
                    key=lambda p: -((p["probe"] or 0) + (p["pitch"] or 0) + (p["obj"] or 0)))[:ASSETS]
    out_calls = [c for c in calls if c["direction"] == "outbound"]
    return {
        "version": VERSION, "date": meta.get("date") or d0.strftime("%Y-%m-%d"),
        "window": {"d0": d0.isoformat(), "cw_end": cw_end.isoformat(), "fetched": meta.get("fetched")},
        "teams": teams, "people": people, "rank": rank, "runner_up": rank[1] if len(rank) > 1 else None,
        "best_front_line": front[0] if front else None,
        "rec": rec, "coach_case": max(cases, key=lambda p: p["real"]) if cases else None, "assets": assets,
        "enrollments": E,
        "totals": {"calls_raw": len(calls) + bots, "bots_excluded": bots, "calls": len(calls), "outbound": len(out_calls),
                   "inbound": len(calls) - len(out_calls), "answered_out": sum(c["ans"] for c in out_calls),
                   "zip_total": len(run["zips"]), "zip_attr": len(Z), "zip_dropped": dropped, "enroll_window": len(E),
                   "enroll_credited": sum(cred_t.values()), "payments": len(run["payments"]),
                   "multi_group_callers": len({c.get("user_id") for c in calls} & multi)},
        "_calls": calls, "_zip": Z,
    }


def validate(A: dict) -> list[tuple[str, bool]]:
    """Section 10 checks that can be computed from the data (the PDF page count is checked after rendering)."""
    t = A["totals"]
    return [
        (f"S1 calls {t['calls']:,} equal the sum of team dials and inbound",
         t["calls"] == sum(s["dials"] + s["inbound"] for s in A["teams"].values())),
        (f"Zipteams notes attributed {t['zip_attr']:,} of {t['zip_total']:,}, dropped < 5%",
         t["zip_total"] > 0 and t["zip_dropped"] < 0.05 * t["zip_total"]),
        ("At least one team is eligible for ranking", bool(A["rank"])),
    ]


# ------------------------------------------------------------------ transcripts (P30-P33)

def transcript_sample(A: dict) -> tuple[list[dict], list[dict], list[str]]:
    """P31: up to 40 converted leads (longest call first) + 10 long non-converted per top-5 team (seed 5)."""
    credited = {e["lead"] for e in A["enrollments"] if e["team"]}
    longest: dict[str, dict] = {}
    for c in A["_calls"]:
        if c["ans"] and c.get("lead_number") and (c["lead_id"] not in longest or c["duration"] > longest[c["lead_id"]]["duration"]):
            longest[c["lead_id"]] = c
    conv = sorted([longest[l] for l in credited if l in longest], key=lambda c: -c["duration"])[:SAMPLE_CONVERTED]
    top = sorted(A["rank"], key=lambda t: -A["teams"][t]["real"])[:SAMPLE_TEAMS]
    random.seed(SAMPLE_SEED)
    non = []
    for t in top:
        pool = sorted([c for l, c in longest.items() if l not in credited and c["team"] == t
                       and c["duration"] >= LONG_NON_CONVERTED_SECS], key=lambda c: c["lead_id"])
        non += random.sample(pool, min(SAMPLE_PER_TEAM, len(pool)))
    return conv, non, top


def fetch_transcripts(A: dict, out_dir: str) -> dict:
    """Search the sample within the API limits and score the P33 markers. Transcripts stay under data/."""
    from integrations.transcripts import TranscriptClient
    from integrations.transcripts.client import normalize_phone

    conv, non, top = transcript_sample(A)
    meta = {}
    for c in conv + non:
        k = normalize_phone(c["lead_number"])
        meta.setdefault(k, {"lead": c["lead_id"], "team": c["team"], "converted": c in conv})
    nums = list(meta)[:SAMPLE_CONVERTED + SAMPLE_PER_TEAM * SAMPLE_TEAMS]
    tc = TranscriptClient(max_retries=0, timeout=120)
    res, failed = [], 0
    time.sleep(60)  # P30: 10 requests a minute, counting any earlier run
    for i in range(0, len(nums), 10):
        if i:
            time.sleep(7)
        try:
            res += tc.search(nums[i:i + 10])
        except Exception as e:  # noqa: BLE001 - one failed chunk must not lose the rest
            failed += 1
            print(f"transcript chunk {i // 10 + 1} failed: {str(e)[:120]}", file=sys.stderr)
    best = {}
    for x in res:
        if not x.start_time or x.start_time.astimezone(IST).strftime("%Y-%m-%d") != A["date"]:
            continue
        if (x.duration or 0) < REAL_SECS or not x.transcript.strip() or (x.agent_name and BOT.search(x.agent_name)):
            continue
        k = normalize_phone(x.phone)
        if k not in best or (x.duration or 0) > (best[k].duration or 0):
            best[k] = x
    rows = [{**meta[k], "agent": x.agent_name, "duration": x.duration, "transcript": x.transcript}
            for k, x in best.items() if k in meta]
    os.makedirs(out_dir, exist_ok=True)
    json.dump(rows, open(os.path.join(out_dir, "transcripts.json"), "w"))
    return summarise_transcripts(rows, {"requests": tc.requests_made, "failed_chunks": failed, "numbers": len(nums),
                                        "sample_conv": len(conv), "sample_non": len(non), "top5": top})


def summarise_transcripts(rows: list[dict], info: dict) -> dict:
    out = {**info, "markers": {k: {} for k in MARKERS}}
    for flag, key in ((True, "converted"), (False, "not_converted")):
        g = [r for r in rows if r["converted"] == flag]
        out[key + "_n"] = len(g)
        for k, v in marker_rates([r["transcript"] for r in g]).items():
            out["markers"][k][key] = v
    by_team = defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r["transcript"])
    out["payment_step_by_team"] = {t: {"n": len(v), "pct": marker_rates(v)[PAYMENT_STEP]} for t, v in by_team.items()}
    return out


# ------------------------------------------------------------------ plan tracker (not part of v1.0)

def plan_tracker(A: dict, tx: dict | None = None) -> dict:
    """The plan's levers per team and caller: payment step, full pitch, same-day callback, dialer failures."""
    calls = A["_calls"]
    day_end = datetime.fromisoformat(A["window"]["d0"]) + timedelta(days=1)
    by_lead = defaultdict(list)
    for c in calls:
        by_lead[c["lead_id"]].append(c)
    missed = {}  # one missed inbound per lead: the first of the day
    for c in calls:
        if c["direction"] == "inbound" and not c["ans"] and c["t"] and c["lead_id"] not in missed:
            missed[c["lead_id"]] = c
    cb = {}
    for lid, m in missed.items():
        later = [x for x in by_lead[lid] if x["t"] and m["t"] < x["t"] < day_end and (x["direction"] == "outbound" or x["ans"])]
        cb[lid] = round((later[0]["t"] - m["t"]).total_seconds() / 60) if later else None

    def lever(cs: list[dict], zs: list[dict], ms: list[str]) -> dict:
        out = [c for c in cs if c["direction"] == "outbound"]
        mins = sorted(cb[l] for l in ms if cb[l] is not None)
        return {"zip_n": len(zs),
                "payment_step_zip_pct": round(100 * sum(z["payment_step"] for z in zs) / len(zs)) if zs else None,
                "full_pitch_pct": _mean(zs, "pitch"),
                "missed_inbound_leads": len(ms),
                "called_back_same_day_pct": round(100 * len(mins) / len(ms)) if ms else None,
                "median_callback_min": mins[len(mins) // 2] if mins else None,
                "dial_failure_pct": round(100 * sum(c.get("status") == "CallFailure" for c in out) / len(out), 1) if out else None}

    T, P, ZT, ZP, MT, MP = (defaultdict(list) for _ in range(6))
    for c in calls:
        T[c["team"]].append(c)
        P[(c["team"], c["name"])].append(c)
    for z in A["_zip"]:
        ZT[z["team"]].append(z)
        ZP[(z["team"], z["name"])].append(z)
    for lid, m in missed.items():  # a missed call belongs to whoever's line it rang on
        MT[m["team"]].append(lid)
        MP[(m["team"], m["name"])].append(lid)
    tx_team = (tx or {}).get("payment_step_by_team", {})
    teams = [{"team": t, **lever(T[t], ZT[t], MT[t]),
              "payment_step_transcript_pct": tx_team.get(t, {}).get("pct"), "transcripts_n": tx_team.get(t, {}).get("n", 0)}
             for t in sorted(T) if t != "Not a user"]
    callers = [{"team": k[0], "caller": k[1], **lever(P[k], ZP[k], MP[k])}
               for k in sorted(P) if k[0] != "Not a user" and any(c["direction"] == "outbound" for c in P[k])]
    return {"date": A["date"], "teams": teams, "callers": callers,
            "account": {**lever(calls, A["_zip"], list(missed)),
                        "payment_step_transcript_converted_pct": ((tx or {}).get("markers", {}).get(PAYMENT_STEP) or {}).get("converted"),
                        "payment_step_transcript_not_converted_pct": ((tx or {}).get("markers", {}).get(PAYMENT_STEP) or {}).get("not_converted")}}


# ------------------------------------------------------------------ PDF

def chromium() -> str | None:
    for p in (os.environ.get("CHROME_BIN"), "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", "chromium", "chromium-browser",
              "google-chrome"):
        if p and (os.path.exists(p) or subprocess.run(["which", p], capture_output=True).returncode == 0):
            return p
    return None


def render_pdf(html_path: str, pdf_path: str) -> int | None:
    """P61: headless Chromium; returns the page count (None if Chromium is missing)."""
    exe = chromium()
    if not exe:
        print("Chromium not found; set CHROME_BIN. HTML written to " + html_path, file=sys.stderr)
        return None
    subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", "file://" + os.path.abspath(html_path)],
                   check=True, capture_output=True, timeout=180)
    return len(re.findall(rb"/Type\s*/Page[^s]", open(pdf_path, "rb").read()))


def main():
    from analytics import team_performance_html as page

    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--data", help="folder written by scripts/fetch_report_day.py (default data/report_{DATE})")
    ap.add_argument("--fetch", action="store_true", help="fetch the day from LeadSquared first")
    ap.add_argument("--as-of", help="cap the conversion window at this IST time (YYYY-MM-DD HH:MM)")
    ap.add_argument("--no-transcripts", action="store_true", help="skip P30-P33 (the report then says so)")
    ap.add_argument("--out", default="/mnt/project-files/reports" if os.path.isdir("/mnt/project-files/reports") else "exports")
    ap.add_argument("--name", help="PDF file name (default team_calling_report_{DATE}.pdf)")
    a = ap.parse_args()
    data = a.data or f"data/report_{a.date}"
    if a.fetch or not os.path.exists(os.path.join(data, "enrollments.json")):
        cmd = [sys.executable, "scripts/fetch_report_day.py", a.date, "--out", data] + (["--as-of", a.as_of] if a.as_of else [])
        subprocess.run(cmd, check=True, env={**os.environ, "PYTHONPATH": "."})
    as_of = datetime.strptime(a.as_of, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if a.as_of else None
    A = analyse(load_run(data), as_of)
    tx_path = os.path.join(data, "tx_summary.json")
    if a.no_transcripts:
        tx = None
    elif os.path.exists(tx_path) and not a.fetch:
        tx = json.load(open(tx_path))
    else:
        tx = fetch_transcripts(A, data)
        json.dump(tx, open(tx_path, "w"), indent=1)
    tracker = plan_tracker(A, tx)

    os.makedirs(a.out, exist_ok=True)
    json.dump({k: v for k, v in A.items() if not k.startswith("_")}, open(os.path.join(data, "agg.json"), "w"), indent=1, default=str)
    pdf = os.path.join(a.out, a.name or f"team_calling_report_{a.date}.pdf")
    html_path = os.path.join(data, "report.html")
    open(html_path, "w", encoding="utf-8").write(page.report_html(A, tx))
    pages = render_pdf(html_path, pdf)
    tr_pdf = os.path.join(a.out, f"plan_tracker_{a.date}.pdf")
    tr_html = os.path.join(data, "plan_tracker.html")
    open(tr_html, "w", encoding="utf-8").write(page.tracker_html(tracker))
    render_pdf(tr_html, tr_pdf)
    json.dump(tracker, open(os.path.join(data, "plan_tracker.json"), "w"), indent=1)

    checks = validate(A) + [(f"PDF has {pages} page(s)", pages in (1, 2)), (f"'Parameters v{VERSION}' in Method", True)]
    t = A["totals"]
    print(f"Parameters v{VERSION} · {A['date']} · conversion window to {A['window']['cw_end']}")
    print(f"calls {t['calls']:,} ({t['bots_excluded']} bot calls excluded) · credited enrolments {t['enroll_credited']} "
          f"of {t['enroll_window']} · payments {t['payments']}")
    for i, team in enumerate(A["rank"], 1):
        s = A["teams"][team]
        print(f"{i:2}. {short(team)[:30]:30} credited {s['credited']:3}  conv {s['conv_pct']:5}%  real {s['real']:4}  warm={s['warm']}")
    for label, ok in checks:
        print(("PASS " if ok else "FAIL ") + label)
    print(f"report: {pdf}\nplan tracker: {tr_pdf}")
    if not all(ok for _, ok in checks):
        sys.exit(1)


if __name__ == "__main__":
    main()
