"""Report export: the analysis dataset, the dashboard snapshot, and the files team leaders open.

For a period it reads the registry, then LeadSquared users, lead details, first enrolments, Zipteams notes and
payment records (read-only; cached under data/convintel/sources/), puts each call's validated layer results and
its Zipteams analysis on it, and assembles the snapshot the dashboard section shows. Files, under
data/convintel/reports/<key>/ (git-ignored): snapshot.json, flagged_calls.csv (the calls that may not be real
conversations, with reasons) and index.html (the dashboard section as one offline page). Verbatim transcript
excerpts are left out unless ``--excerpts`` is given.

    python -m analytics.convintel report FROM TO [--key 7d] [--excerpts] [--accountability DIR] [--out DIR]
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from analytics.convintel import schema as S
from analytics.convintel import sources
from analytics.convintel.attribution import safe_name
from analytics.convintel.classify import REAL_CALL_SECS
from analytics.convintel.store import LAYER_VERSIONS, Registry, ts
from analytics.definitions import WORKING_DAY_DIALS
from analytics.zip_calls import attach
from integrations.timeutil import IST, ist_day_start, utc

REPORTS_DIR = os.path.join("data", "convintel", "reports")
CAPS = {"calls": 3000, "leads": 2000, "gaps": 500, "opportunities": 1000}
SOURCE_MAX_AGE = timedelta(hours=1)
SNAPSHOT_VERSION = "ci-snapshot-1"
ZIP_LAYER, ZIP_VERSION = "zipteams", "zip-compare-1"
ORG_ROW = "(organisation)"
# What each compared rate is out of, stored next to it in team_conversation_aggregates.
DENOMINATORS = {"connectPct": "outbound dials", "realRatePct": "connected calls", "avgRealMin": "real calls (3+ min)",
                "realPerWorkingDay": "working days (20+ dials)", "convPerRealPct": "leads with a real call (3+ min)",
                "convPerContactedPct": "leads contacted", "qualityAvg": "calls with a model reading",
                "coveragePct": "calls expected to have a transcript", "integrityPct": "connected calls"}


def _ist(t: datetime | None) -> str | None:
    return t.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t else None


def layer_results(reg: Registry, day_from: str, day_to: str) -> dict[str, dict[str, dict]]:
    """call_id -> {layer: output} for every valid result at the current version of its layer."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for layer, version in LAYER_VERSIONS.items():
        for r in reg.q("SELECT x.call_id, x.output FROM conversation_analysis_results x JOIN transcript_coverage_registry r "
                       "USING (call_id) WHERE r.ist_day >= ? AND r.ist_day <= ? AND x.layer = ? AND x.version = ? AND x.valid = 1",
                       (day_from, day_to, layer, version)):
            out[r["call_id"]][layer] = json.loads(r["output"])
    return out


def dataset(reg: Registry, day_from: str, day_to: str, users: list[dict], leads: dict, enrolments: list[dict],
            payments: list[dict], zips: list[dict], now: datetime) -> dict:
    """The analytics input (see the build contract): registry rows for the period with kw/sem/zip/t on each."""
    calls = reg.q("SELECT * FROM transcript_coverage_registry WHERE ist_day >= ? AND ist_day <= ? ORDER BY start_utc",
                  (day_from, day_to))
    res = layer_results(reg, day_from, day_to)
    for c in calls:
        c["t"] = utc(c["start_utc"])
        c["caller_name"] = safe_name(c.get("caller_name"))     # rows inventoried before names were checked
        c["missing_layers"] = json.loads(c["missing_layers"] or "[]")
        got = res.get(c["call_id"], {})
        c["kw"], c["sem"], c["zip"] = got.get(S.KEYWORD), got.get(S.SEMANTIC), None
        c["ans"], c["duration"] = bool(c["answered"]), c["duration_s"] or 0      # what analytics.zip_calls reads
    day_end = ist_day_start(day_to) + timedelta(days=1)
    zip_unmatched, zip_other_day = attach(zips, calls, day_end) if zips else (0, 0)
    for c in calls:
        c.pop("ans", None)
        c.pop("duration", None)
    return {"calls": calls, "users": users, "leads": leads, "enrolments": enrolments, "payments": payments,
            "day_from": day_from, "day_to": day_to, "now": now,
            "zip_notes": {"total": len(zips), "unmatched": zip_unmatched, "other_day": zip_other_day}}


# One reading per call everywhere (team.py's): the semantic layer when it ran, else the keyword layer; an
# "unclear" readiness is None; unknown objection categories are "other".
def readiness(c: dict) -> tuple[int | float | None, str | None, str | None]:
    from analytics.convintel.team import readiness as read
    return read(c)


def objections_of(c: dict) -> list[str]:
    from analytics.convintel.team import objections
    return sorted(objections(c))


def signals_of(c: dict) -> list[str]:
    from analytics.convintel.team import buying_types
    return buying_types(c)


def team_of(c: dict) -> str:
    """The team label a call is shown under: the stamped team for a person, else the pseudo-team of a shared
    login, automation account or non-user (the labels the teams list uses)."""
    from analytics.convintel.team import entity
    return entity(c)[0]


def call_row(c: dict, excerpts: bool) -> dict:
    from analytics.convintel.zipcompare import zip_agrees
    score, band, engine = readiness(c)
    layer = c.get("sem") or c.get("kw") or {}
    findings = [{"category": f.get("category"), "confidence": f.get("confidence"), "reasoning": f.get("reasoning"),
                 "action": f.get("recommended_action"), **({"excerpt": f.get("excerpt")} if excerpts else {})}
                for f in layer.get("findings") or []]
    return {"callId": c["call_id"], "leadId": c.get("lead_id"), "callerId": c.get("caller_id"), "caller": c.get("caller_name"),
            "team": team_of(c), "kind": c.get("caller_kind"), "day": c.get("ist_day"), "startIst": _ist(c.get("t")),
            "direction": c.get("direction"), "durationS": c.get("duration_s"), "class": c.get("call_class"),
            "status": c.get("analysis_status"), "statusReason": c.get("status_reason"),
            "transcriptState": c.get("transcript_state"), "engine": engine, "readiness": score, "readinessBand": band,
            "quality": ((c.get("sem") or {}).get("quality") or {}).get("overall"), "objections": objections_of(c),
            "signals": signals_of(c), "integrityFlags": [f["flag"] for f in c.get("flags") or []],
            "integrityReasons": [f["reason"] for f in c.get("flags") or []],
            "zipIntent": (c.get("zip") or {}).get("intent"), "zipAgrees": zip_agrees(c),
            "summary": (c.get("sem") or {}).get("summary"), "findings": findings}


def _call_priority(c: dict) -> tuple:
    tiers = {f["tier"] for f in c.get("flags") or []}
    score = readiness(c)[0]
    return (0 if tiers & {"suspect", "pattern"} else 1, 0 if c.get("kw") or c.get("sem") else 1,
            -(score or -1), -(c["t"].timestamp() if c.get("t") else 0))


def lead_teams(leads: dict, users: list[dict]) -> dict:
    """The lead details with each lead's team: its owner's current LeadSquared group when the owner is a person
    (LeadSquared keeps no team on the lead itself)."""
    from analytics.convintel.attribution import PERSON, Directory, user_name, user_team
    d, out = Directory(users), {}
    for lid, x in (leads or {}).items():
        x = dict(x or {})
        u = d.find(x.get("owner_id"), x.get("owner_name"))
        if x.get("team") is None and u and d.kind(x.get("owner_name") or user_name(u), u) == PERSON:
            x["team"] = user_team(u)
        out[lid] = x
    return out


def coverage_section(reg: Registry, calls: list[dict], day_from: str, day_to: str, now: datetime) -> dict:
    cov = reg.coverage(day_from, day_to)

    def counts(rows):
        st = Counter(r["analysis_status"] for r in rows)
        expected = len(rows) - st[S.NO_TRANSCRIPT_EXPECTED]
        return {"total_calls": len(rows), "expected_transcripts": expected, "analyzed": st[S.ANALYZED],
                "transcripts_found": sum(1 for r in rows if r["transcript_state"] == S.T_FOUND),
                "by_status": {s: st[s] for s in S.STATUSES},
                "coverage_pct": round(100 * st[S.ANALYZED] / expected, 1) if expected else None}
    by_team, by_day = defaultdict(list), defaultdict(list)
    for c in calls:
        by_team[team_of(c)].append(c)
        by_day[c.get("ist_day")].append(c)
    gaps = [c for c in calls if c["analysis_status"] in S.GAP_STATUSES]
    gaps.sort(key=lambda c: (c["analysis_status"], c["start_utc"] or ""))
    return {**cov, "byTeam": [{"team": t, **counts(v)} for t, v in sorted(by_team.items())],
            "byDay": [{"day": d, **counts(v)} for d, v in sorted(by_day.items())],
            "gaps": [{"callId": c["call_id"], "leadId": c.get("lead_id"), "callerId": c.get("caller_id"),
                      "caller": c.get("caller_name"), "team": team_of(c), "day": c.get("ist_day"),
                      "startIst": _ist(c.get("t")), "class": c.get("call_class"), "status": c["analysis_status"],
                      "reason": c.get("status_reason"), "attempts": c.get("lookup_attempts"),
                      "nextRetryIst": _ist(utc(c.get("next_lookup_utc")))} for c in gaps[:CAPS["gaps"]]],
            "gapsTotal": len(gaps)}


def snapshot(ds: dict, reg: Registry, key: str, label: str, prior: dict | None = None, excerpts: bool = False,
             config: dict | None = None, accountability_rows: list[dict] | None = None,
             audit: list[dict] | None = None, derived: dict | None = None) -> dict:
    """The dashboard snapshot for one period (see the build contract's snapshot JSON). ``derived``, when given, is
    filled with the full, uncapped rows that ``persist`` stores in the registry."""
    from analytics.convintel import coaching, crosscall, integrity, responsibility, revenue, team, zipcompare
    from analytics.convintel.reconcile import reconcile
    calls, now, d0, d1 = ds["calls"], ds["now"], ds["day_from"], ds["day_to"]
    flags = integrity.call_flags(calls)
    for c in calls:
        c["flags"] = flags.get(c["call_id"], [])
    if prior:
        pflags = integrity.call_flags(prior["calls"])
        for c in prior["calls"]:
            c["flags"] = pflags.get(c["call_id"], [])
    views = team.build_views(calls, ds["users"], ds["leads"], ds["enrolments"], now,
                             prior={"calls": prior["calls"], "enrolments": prior["enrolments"]} if prior else None,
                             config=config)
    integ = integrity.integrity_summary(calls, flags)
    by_caller_integrity = {r["callerId"]: r for r in integ.get("byCaller") or []}
    per_caller = coaching.caller_coaching(calls)
    for c in views["callers"]:
        i = by_caller_integrity.get(c["callerId"]) or {}
        c["integrity"] = {"flagged": i.get("flagged", 0), "flaggedPct": i.get("flaggedPct"), "byFlag": i.get("byFlag", {}),
                          "shortCalls": i.get("shortCalls", 0), "shortPct": i.get("shortPct")}
        c["coaching"] = per_caller.get(c["callerId"]) or {"strengths": [], "weaknesses": [], "actions": [], "weeklySample": {}}

    data_end = min(now, ist_day_start(d1) + timedelta(days=1))
    journeys = crosscall.lead_journeys(calls, lead_teams(ds["leads"], ds["users"]), ds["enrolments"], now,
                                       data_end=data_end)
    for j in journeys.values():
        if j.get("priority") is None:
            j["priority"] = j.get("readinessBand") or (
                "unclear" if j.get("readiness") is None else
                "hot" if j["readiness"] >= 75 else "warm" if j["readiness"] >= 50 else "cool" if j["readiness"] >= 25 else "cold")
    leads = sorted(journeys.values(), key=lambda j: (bool(j.get("enrolled")), -(j.get("readiness") or -1),
                                                     -len(j.get("flags") or [])))
    eligible = [c for c in calls if c.get("answered") or c.get("flags") or c.get("kw") or c.get("sem")]
    eligible.sort(key=_call_priority)
    opps = revenue.opportunities(calls, ds["leads"], ds["enrolments"], now,
                                 calls_until=data_end)
    objections = {"org": Counter(), "byTeam": defaultdict(Counter)}
    for c in calls:
        for o in objections_of(c):
            objections["org"][o] += 1
            objections["byTeam"][team_of(c)][o] += 1
    acct_rows = responsibility.findings(accountability_rows, calls, ds["enrolments"], ds["users"], audit=audit,
                                        now=now) if accountability_rows else []
    acct = responsibility.summary(acct_rows) if accountability_rows else {
        "rows": 0, "byStatus": {}, "byPerson": [], "byAction": {}, "unverified": 0,
        "notes": ["No lead-accountability run was given for this period. Run python -m analytics.accountability for "
                  "the same dates and pass its folder with --accountability."]}
    blocked = reg.get_meta("blocked_layers", {})
    last_run = reg.q("SELECT MAX(finished_utc) AS t FROM processing_runs WHERE status = 'done'")[0]["t"]
    teams = views["teams"]
    team_coaching = coaching.team_coaching(calls, per_caller)
    notes = []
    if ist_day_start(d1) + timedelta(days=1) <= now:
        notes.append("Calls after this period's last day are not read, so a callback or follow-up made after it "
                     "is not seen: missed commitments, overdue follow-ups and missed callbacks near the end of the "
                     "period may be over-counted.")
    if derived is not None:
        derived.update(views=views, per_caller=per_caller, journeys=journeys, opportunities=opps,
                       accountability=acct_rows, zip=zipcompare.disagreements(calls))
    return {
        "version": SNAPSHOT_VERSION,
        "range": {"key": key, "from": d0, "to": d1, "label": label},
        "generatedAt": f"{ts(now)} UTC",
        "dataAsOf": {"callsUpTo": max((c["start_utc"] for c in calls if c.get("start_utc")), default=None),
                     "lastRun": last_run},
        "privacy": {"excerpts": excerpts, "leadNumbers": False},
        "definitions": {
            "realCallSecs": REAL_CALL_SECS, "workingDayDials": WORKING_DAY_DIALS,
            "analysisVersion": S.ANALYSIS_VERSION, "requiredLayers": list(S.REQUIRED_LAYERS),
            "semanticEngine": (f"not running: {blocked[S.SEMANTIC]}" if S.SEMANTIC in blocked else
                               "Claude (model layer)" if any(c.get("sem") for c in calls) else
                               "not run yet for this period: every reading shown is keyword-based"),
            "notes": ["A real call here is answered and at least 3 minutes. The main dashboard's 2-minute 'real "
                      "conversation' is unchanged.",
                      "A call counts as analysed only when both the keyword layer and the model layer have a validated "
                      "result.", *views.get("notes", []), *notes]},
        "coverage": coverage_section(reg, calls, d0, d1, now),
        "org": views["org"], "teams": teams, "callers": views["callers"],
        "leads": leads[:CAPS["leads"]], "leadsTotal": len(leads),
        "calls": [call_row(c, excerpts) for c in eligible[:CAPS["calls"]]], "callsTotal": len(eligible),
        "objections": {"org": dict(objections["org"]), "byTeam": {t: dict(v) for t, v in objections["byTeam"].items()}},
        "integrity": integ,
        "opportunities": opps[:CAPS["opportunities"]], "opportunitiesTotal": len(opps),
        "opportunitiesByKind": dict(Counter(o["kind"] for o in opps)),
        "zip": zipcompare.compare(calls, ds["enrolments"], ds["leads"]),
        "coachingTeams": team_coaching,
        "accountability": acct,
        "revenue": revenue.revenue_section(ds["enrolments"], calls, ds["users"], ds["payments"],
                                           amount_field=(config or {}).get("payment_amount_field")),
        "reconciliation": reconcile(reg, d0, d1, now, verify_files=False),
        "filters": {
            "teams": sorted({t["team"] for t in teams}),
            "teamLeaders": sorted({t.get("teamLeader") for t in teams if t.get("teamLeader")}),
            "callers": sorted({c["caller"] for c in views["callers"] if c.get("caller")}),
            "courses": sorted({j["course"] for j in journeys.values() if j.get("course")}),
            "stages": sorted({j["stage"] for j in journeys.values() if j.get("stage")}),
            "priorities": [p for p in S.READINESS_BANDS if any(j.get("priority") == p for j in journeys.values())],
            "categories": sorted(set(S.OBJECTION_CATEGORIES) | set(S.FINDING_CATEGORIES)),
            "statuses": list(S.STATUSES)},
    }


def persist(reg: Registry, ds: dict, derived: dict, now: datetime) -> dict[str, int]:
    """Store the period's derived rows in the registry tables (requirement 11), replacing the same period's
    earlier rows. Returns the rows written per table."""
    d0, d1 = ds["day_from"], ds["day_to"]
    period, created = f"{d0}_{d1}", ts(now)
    days = {c["call_id"]: c.get("ist_day") for c in ds["calls"]}
    opp = [{"finding_id": f"opp:{o['kind']}:{o['leadId']}:{o.get('callId') or ''}", "lead_id": o["leadId"],
            "call_id": o.get("callId"), "kind": o["kind"], "ist_day": o.get("day") or days.get(o.get("callId")),
            "caller_id": o.get("callerId"), "caller_name": o.get("caller"), "team": o.get("team"),
            "owner_at_enrolment": None, "evidence": o.get("evidence"), "next_action": o.get("nextAction"),
            "status": f"open ({o.get('confidence')} confidence)", "created_utc": created}
           for o in derived["opportunities"]]
    reg.replace_rows("revenue_opportunity_findings", opp, "ist_day >= ? AND ist_day <= ?", (d0, d1))
    zipf = [{"finding_id": f"{z['callId']}:{ZIP_LAYER}:0", "call_id": z["callId"], "lead_id": z.get("leadId"),
             "caller_id": z.get("callerId"), "caller_name": z.get("caller"), "team": z.get("team"), "ist_day": z.get("day"),
             "layer": ZIP_LAYER, "version": ZIP_VERSION, "category": z.get("category"), "excerpt": None, "offset": None,
             "speaker": None, "confidence": z.get("confidence"), "reasoning": z.get("reasoning"),
             "recommended_action": z.get("recommended_action"), "created_utc": created} for z in derived["zip"]]
    reg.replace_rows("conversation_quality_findings", zipf, "layer = ? AND ist_day >= ? AND ist_day <= ?",
                     (ZIP_LAYER, d0, d1))
    coach = []
    for cid, p in derived["per_caller"].items():
        base = {"period": period, "caller_id": cid, "caller_name": p.get("caller"), "team": p.get("team"),
                "version": S.ANALYSIS_VERSION, "created_utc": created}
        for kind, items in (("strength", p.get("strengths")), ("weakness", p.get("weaknesses"))):
            coach += [{**base, "kind": kind, "item": x["item"], "n": x.get("n"),
                       "evidence": {"avg": x["avg"]} if x.get("avg") is not None else None} for x in items or []]
        coach += [{**base, "kind": "action", "item": a, "n": i + 1, "evidence": None}
                  for i, a in enumerate(p.get("actions") or [])]
        coach += [{**base, "kind": "weekly_sample", "item": week, "n": len(ids), "evidence": ids}
                  for week, ids in (p.get("weeklySample") or {}).items()]
    reg.replace_rows("caller_coaching_insights", coach, "period = ?", (period,))
    views = derived["views"]
    agg = [{"period": period, "team": label, "metric": k, "value": v, "denominator": DENOMINATORS.get(k),
            "version": S.ANALYSIS_VERSION, "created_utc": created}
           for label, m in [(ORG_ROW, views["org"])] + [(t["team"], t) for t in views["teams"]]
           for k, v in m.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    reg.replace_rows("team_conversation_aggregates", agg, "period = ?", (period,))
    reg.replace_rows("lead_accountability_findings", derived["accountability"])
    cross = [{"lead_id": lid, "version": S.ANALYSIS_VERSION, "calls": j.get("calls"), "output": j, "updated_utc": created}
             for lid, j in derived["journeys"].items()]
    reg.replace_rows("lead_cross_call", cross)
    return {"revenue_opportunity_findings": len(opp), "conversation_quality_findings (Zipteams)": len(zipf),
            "caller_coaching_insights": len(coach), "team_conversation_aggregates": len(agg),
            "lead_accountability_findings": len(derived["accountability"]), "lead_cross_call": len(cross)}


def read_audit(folder: str) -> list[dict] | None:
    """analytics/accountability.py's audit.jsonl in ``folder`` (corrections with the original performer), if any."""
    path = os.path.join(folder, "audit.jsonl")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def private_dir(path: str) -> bool:
    """True when ``path`` is inside the repository's git-ignored data/ or exports/ folder."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    real = os.path.realpath(path)
    return any(os.path.commonpath([real, base]) == base
               for base in (os.path.realpath(os.path.join(root, d)) for d in ("data", "exports")))


def prior_period(day_from: str, day_to: str) -> tuple[str, str]:
    a, b = ist_day_start(day_from), ist_day_start(day_to)
    n = (b - a).days + 1
    return (a - timedelta(days=n)).strftime("%Y-%m-%d"), (a - timedelta(days=1)).strftime("%Y-%m-%d")


def run_report(reg: Registry, day_from: str, day_to: str, now: datetime, key: str | None = None, label: str | None = None,
               excerpts: bool = False, out_dir: str | None = None, accountability_dir: str | None = None,
               refresh: bool = False, client=None, config_path: str = os.path.join("data", "convintel", "config.json"),
               log=lambda *a: None) -> dict:
    """Fetch the period's sources (read-only), build the snapshot and write the files. Returns where they went."""
    from analytics.convintel import integrity
    from analytics.convintel.snapshot_html import write_html
    if client is None:
        from integrations.leadsquared import LeadSquaredClient
        client = LeadSquaredClient()
    key = key or (day_from if day_from == day_to else f"{day_from}_{day_to}")
    p_from, p_to = prior_period(day_from, day_to)
    d0, _ = sources.day_window(day_from, day_to)
    p0, _ = sources.day_window(p_from, p_to)
    _, d1 = sources.day_window(day_from, day_to)
    name = f"{p_from}_{day_to}"
    users = sources.cached("users", client.get_users, SOURCE_MAX_AGE, refresh)
    enrol_all = sources.cached(f"enrolments_{name}", lambda: sources.first_enrolments(client, p0, d1 + timedelta(
        days=sources.CONVERSION_DAYS)), SOURCE_MAX_AGE, refresh)
    zips = sources.cached(f"zip_{day_from}_{day_to}", lambda: sources.zip_notes(client, d0, d1), SOURCE_MAX_AGE, refresh)
    pays = sources.cached(f"payments_{day_from}_{day_to}", lambda: sources.payments(client, d0, d1), SOURCE_MAX_AGE, refresh)
    lead_ids = {r["lead_id"] for r in reg.q("SELECT DISTINCT lead_id FROM transcript_coverage_registry WHERE ist_day >= ? "
                                            "AND ist_day <= ? AND lead_id IS NOT NULL", (day_from, day_to))}
    lead_ids |= {e["lead_id"] for e in enrol_all}
    leads = sources.cached(f"leads_{day_from}_{day_to}", lambda: sources.leads(client, sorted(lead_ids)), SOURCE_MAX_AGE, refresh)
    in_period = lambda e, a, b: a <= (e.get("ist_day") or "") <= b  # noqa: E731
    cur_end = (ist_day_start(day_to) + timedelta(days=sources.CONVERSION_DAYS)).strftime("%Y-%m-%d")
    enrol = [e for e in enrol_all if in_period(e, day_from, cur_end)]
    ds = dataset(reg, day_from, day_to, users, leads, enrol, pays, zips, now)
    prior = None
    if all(reg.get_meta(f"source:{d}") for d in sources.days(p_from, p_to)):
        p_end = (ist_day_start(p_to) + timedelta(days=sources.CONVERSION_DAYS)).strftime("%Y-%m-%d")
        prior = dataset(reg, p_from, p_to, users, leads, [e for e in enrol_all if in_period(e, p_from, p_end)], [], [], now)
    rows = audit = None
    if accountability_dir:
        with open(os.path.join(accountability_dir, "actions.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        audit = read_audit(accountability_dir)
    config = json.load(open(config_path)) if os.path.exists(config_path) else None
    derived: dict = {}
    snap = snapshot(ds, reg, key, label or key, prior, excerpts, config, rows, audit, derived)
    stored = persist(reg, ds, derived, now)
    out = out_dir or os.path.join(REPORTS_DIR, key)
    if not private_dir(out):
        raise SystemExit(f"{out}: reports hold phone numbers (flagged_calls.csv), so they go under data/ or exports/ only")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))
    flagged = integrity.flag_rows(ds["calls"], {c["call_id"]: c["flags"] for c in ds["calls"] if c.get("flags")})
    integrity.write_flag_csv(os.path.join(out, "flagged_calls.csv"), flagged)
    write_html(os.path.join(out, "index.html"), snap)
    log(f"report {key}: {len(ds['calls'])} calls, {len(flagged)} flagged -> {out}; stored {stored}")
    return {"out": out, "calls": len(ds["calls"]), "flagged": len(flagged), "prior": bool(prior),
            "coveragePct": snap["coverage"]["coverage_pct"], "stored": stored}
