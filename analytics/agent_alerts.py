"""Alerts that say which TimePay discovery agent needs work, and what kind of work.

For each campaign in ``analytics/agent_alerts.json`` it joins every GrowthX lead (first funnel page) to
its best TimePay outcome and raises an alert when:

- ``BELOW_TARGET``  positive leads ÷ leads called is under the target (30% unless the config says otherwise).
- ``LOW_CONNECT``   share of called leads ever connected is under 70% of the median across the campaigns
                    checked: a dialling problem (attempts, hours), not the agent.
- ``OPENING``       "Hangup Early" share of connected leads is over 130% of the median: people hang up in the
                    first seconds, so change the opening line.
- ``ICP_FIT``       "Irrelevant" + "Not Interested" share of connected leads is over 130% of the median: the
                    people reached are not who the agent is built for, so add or fix an ICP (or the ads).
- ``DISCOVERY``     under 25% of positive leads reach Full Conversion while the median campaign is over
                    50%: the agent confirms attendance but stops before discovery.
- ``WRONG_AGENT``   more than 5% of the campaign's calls were made by an agent other than the expected one.
- ``MISSING_ICP``   a funnel page the map marks as targeting an audience the agent has no segment for.
- ``PARTIAL_ICP``   a funnel page whose audience fits an agent segment only in part (the map says what the
                    agent lacks: pain line, story, offer).
- ``NEW_FUNNEL``    a funnel page not in the map yet: read it and map it (``python -m analytics.funnel_icp pages``).
- ``WEAK_FUNNEL``   a page with 40+ connected leads whose positive rate (of connected) is under 70% of the
                    campaign's: that audience is not landing with the agent.

The maps in ``analytics/agent_alerts_maps/<campaign>.json`` hold each funnel page's agent segment, and
the pages whose audience the agent misses (``missing_pages``) or covers only in part (``partial_pages``).
They are read by a person (page headline vs the agent prompt) and hold no lead data.

Thresholds are relative to the campaigns checked in the same run, so they move with the account; they live
in ``RULES`` and can be overridden per run in the config. Positive = the four registration dispositions
(``analytics.funnel_icp.POSITIVE``); a lead is connected when any call has status Contacted.

Reads only (GrowthX, TimePay). Writes ``agent_alerts_<date>.csv`` (one row per alert),
``agent_alerts_<date>_campaigns.csv`` (the numbers behind them) and ``agent_alerts_<date>.md`` (the message).

    python -m analytics.agent_alerts [--config analytics/agent_alerts.json] [--only women,usacc] <out_dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import Iterable

from analytics.funnel_icp import POSITIVE, first_touch, page_slug, phone10
from integrations.timeutil import now_ist

CONFIG = os.path.join(os.path.dirname(__file__), "agent_alerts.json")
MAPS_DIR = os.path.join(os.path.dirname(__file__), "agent_alerts_maps")
RULES = {"target": 0.30, "low_connect": 0.70, "opening": 1.30, "icp_fit": 1.30, "full_share": 0.25,
         "full_share_median": 0.50, "wrong_agent": 0.05, "page_min_connected": 40, "weak_page": 0.70,
         "missing_min_leads": 20, "partial_min_leads": 100, "new_min_leads": 20}
ACTION = {
    "BELOW_TARGET": "See the other alerts for this campaign for the cause.",
    "LOW_CONNECT": "Dialling, not the agent: add attempts, call in business hours, check numbers.",
    "OPENING": "Change the opening: say why you are calling in the first line, tie it to the ad they filled.",
    "ICP_FIT": "Add or fix an ICP in the agent (or stop the ads bringing these people).",
    "DISCOVERY": "Agent stops at attendance: make it finish discovery before closing.",
    "WRONG_AGENT": "Point the campaign at its own agent.",
    "MISSING_ICP": "Add a segment for this audience to the agent: pain line, story, day to highlight.",
    "PARTIAL_ICP": "Add this audience's pain line and a matching story to its segment in the agent.",
    "NEW_FUNNEL": "New ad page: read it and add it to the ICP map, then check the agent covers it.",
    "WEAK_FUNNEL": "This page's audience is not landing: check its segment's pain line and story in the agent.",
}


def _contacted(r: dict) -> bool:
    return r.get("status") == "Contacted"


def _disp(r: dict) -> str:
    return str(r.get("disposition") or "").replace(" NEW", "").strip()


def outcomes(logs: Iterable[dict]) -> tuple[dict[str, dict], Counter]:
    """Per phone: connected, best positive rank (len(POSITIVE) = none), and its connected dispositions.
    Also the calls per agent. Each call_id once."""
    seen: set = set()
    out: dict[str, dict] = {}
    agents: Counter = Counter()
    for r in logs:
        if r.get("call_id") in seen:
            continue
        seen.add(r.get("call_id"))
        agents[r.get("agent_id") or ""] += 1
        p = phone10(r.get("to"))
        if not p:
            continue
        s = out.setdefault(p, {"connected": False, "best": len(POSITIVE), "disps": set()})
        if _contacted(r):
            s["connected"] = True
            d = _disp(r)
            s["disps"].add(d)
            s["best"] = min(s["best"], next((i for i, x in enumerate(POSITIVE) if d.startswith(x)), len(POSITIVE)))
    return out, agents


def campaign_stats(leads: list[dict], tp: dict[str, dict], agents: Counter, expected_agent: str | None) -> dict:
    """Numbers for one campaign: per lead called (in TimePay), and per first-funnel page."""
    people = first_touch(leads)
    page_of = {k: page_slug(r.get("pageUrl")) for k, r in people.items()}
    pages: dict[str, Counter] = defaultdict(Counter)
    for k, slug in page_of.items():
        pages[slug]["leads"] += 1
    called = connected = positive = full = hang = unfit = 0
    for phone, s in tp.items():
        slug = page_of.get(phone, "(not in GrowthX)")
        pc = pages[slug]
        called += 1
        pc["called"] += 1
        if not s["connected"]:
            continue
        connected += 1
        pc["connected"] += 1
        pos = s["best"] < len(POSITIVE)
        positive += pos
        pc["positive"] += pos
        full += s["best"] == 0
        if not pos:
            hang += any(d.startswith("Hangup Early") for d in s["disps"])
            unfit += any(d.startswith(("Irrelevant", "Not Interested")) for d in s["disps"])
    total_calls = sum(agents.values())
    other = total_calls - agents.get(expected_agent or "", 0) if expected_agent else 0
    return {"leads": len(people), "called": called, "connected": connected, "positive": positive, "full": full,
            "hangup_early": hang, "unfit": unfit, "calls": total_calls, "other_agent_calls": other,
            "agents": dict(agents), "pages": {k: dict(v) for k, v in pages.items()}}


def _r(a: float, b: float) -> float:
    return a / b if b else 0.0


def rates(s: dict) -> dict:
    return {"positive_rate": _r(s["positive"], s["called"]), "connect_rate": _r(s["connected"], s["called"]),
            "positive_of_connected": _r(s["positive"], s["connected"]),
            "hangup_early_rate": _r(s["hangup_early"], s["connected"]), "unfit_rate": _r(s["unfit"], s["connected"]),
            "full_share": _r(s["full"], s["positive"]), "other_agent_share": _r(s["other_agent_calls"], s["calls"])}


def load_map(name: str) -> dict:
    with open(os.path.join(MAPS_DIR, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def alerts(stats: dict[str, dict], maps: dict[str, dict], rules: dict | None = None) -> list[dict]:
    """One dict per alert: campaign, code, detail (numbers), action. Campaigns with no connected leads are skipped."""
    rule = {**RULES, **(rules or {})}
    live = {k: s for k, s in stats.items() if s["connected"]}
    rt = {k: rates(s) for k, s in live.items()}
    med = {m: statistics.median(r[m] for r in rt.values()) if rt else 0.0
           for m in ("connect_rate", "hangup_early_rate", "unfit_rate", "full_share")}
    out: list[dict] = []

    def add(camp: str, code: str, detail: str) -> None:
        out.append({"campaign": camp, "code": code, "detail": detail, "action": ACTION[code]})

    for k, s in live.items():
        r = rt[k]
        if r["positive_rate"] < rule["target"]:
            add(k, "BELOW_TARGET", f"{s['positive']:,} of {s['called']:,} leads called positive "
                f"({r['positive_rate']:.1%}), target {rule['target']:.0%}: "
                f"{int(rule['target'] * s['called']) - s['positive']:,} short")
        if r["connect_rate"] < rule["low_connect"] * med["connect_rate"]:
            add(k, "LOW_CONNECT", f"{s['connected']:,} of {s['called']:,} connected ({r['connect_rate']:.1%}); "
                f"median campaign {med['connect_rate']:.1%}")
        if r["hangup_early_rate"] > rule["opening"] * med["hangup_early_rate"]:
            add(k, "OPENING", f"{s['hangup_early']:,} of {s['connected']:,} connected hung up early and never turned "
                f"positive ({r['hangup_early_rate']:.1%}); median {med['hangup_early_rate']:.1%}")
        if r["unfit_rate"] > rule["icp_fit"] * med["unfit_rate"]:
            add(k, "ICP_FIT", f"{s['unfit']:,} of {s['connected']:,} connected said irrelevant / not interested "
                f"({r['unfit_rate']:.1%}); median {med['unfit_rate']:.1%}")
        if s["positive"] and r["full_share"] < rule["full_share"] and med["full_share"] > rule["full_share_median"]:
            add(k, "DISCOVERY", f"only {s['full']:,} of {s['positive']:,} positive reached Full Conversion "
                f"({r['full_share']:.1%}); median {med['full_share']:.1%}")
        if r["other_agent_share"] > rule["wrong_agent"]:
            add(k, "WRONG_AGENT", f"{s['other_agent_calls']:,} of {s['calls']:,} calls by another agent "
                f"({r['other_agent_share']:.1%})")
        m = maps.get(k, {})
        known = set(m.get("pages", {})) | set(m.get("missing_pages", {}))
        missing: dict[str, int] = defaultdict(int)
        partial: dict[str, int] = defaultdict(int)
        for slug, pc in s["pages"].items():
            if slug in m.get("missing_pages", {}):
                missing[m["missing_pages"][slug]] += pc.get("leads", 0)
            elif slug in m.get("partial_pages", {}):
                partial[m["partial_pages"][slug]] += pc.get("leads", 0)
            elif m and slug and slug != "(not in GrowthX)" and slug not in known and pc.get("leads", 0) >= rule["new_min_leads"]:
                add(k, "NEW_FUNNEL", f"page '{slug}' has {pc['leads']:,} leads and is not in the ICP map")
            if pc.get("connected", 0) >= rule["page_min_connected"] and r["positive_of_connected"]:
                pr = _r(pc.get("positive", 0), pc["connected"])
                if pr < rule["weak_page"] * r["positive_of_connected"]:
                    add(k, "WEAK_FUNNEL", f"page '{slug}': {pc.get('positive', 0):,} of {pc['connected']:,} connected "
                        f"positive ({pr:.1%}) vs campaign {r['positive_of_connected']:.1%}")
        for who, n in sorted(missing.items(), key=lambda x: -x[1]):
            if n >= rule["missing_min_leads"]:
                add(k, "MISSING_ICP", f"{n:,} leads from ads for '{who}', no agent segment for them")
        for what, n in sorted(partial.items(), key=lambda x: -x[1]):
            if n >= rule["partial_min_leads"]:
                add(k, "PARTIAL_ICP", f"{n:,} leads: {what}")
    order = list(ACTION)
    return sorted(out, key=lambda a: (a["campaign"], order.index(a["code"])))


def message(stats: dict[str, dict], found: list[dict], when: str, names: dict[str, str]) -> str:
    lines = [f"# Agent alerts, {when} IST", ""]
    by = defaultdict(list)
    for a in found:
        by[a["campaign"]].append(a)
    for k, s in stats.items():
        r = rates(s)
        head = f"**{names.get(k, k)}**: {s['called']:,} leads called · {s['positive']:,} positive ({r['positive_rate']:.1%})"
        lines.append(head + (" — no alerts" if not by[k] else ""))
        for a in by[k]:
            lines.append(f"- {a['code']}: {a['detail']}. **Do:** {a['action']}")
        lines.append("")
    return "\n".join(lines)


def run(config: dict, only: set[str] | None, fetch_leads, fetch_logs) -> tuple[dict, list[dict]]:
    stats, maps = {}, {}
    for k, c in config["campaigns"].items():
        if only and k not in only:
            continue
        logs = fetch_logs(c["timepay"], c["from"])
        tp, agents = outcomes(logs)
        stats[k] = campaign_stats(fetch_leads(c["leadtype"]), tp, agents, c.get("agent"))
        maps[k] = load_map(c["map"]) if c.get("map") else {}
    return stats, alerts(stats, maps, config.get("rules"))


def _write(out_dir: str, stats: dict, found: list[dict], names: dict[str, str]) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    now = now_ist()
    day, when = now.strftime("%Y-%m-%d"), now.strftime("%d %b %Y %H:%M")
    paths = [os.path.join(out_dir, f"agent_alerts_{day}{x}") for x in (".csv", "_campaigns.csv", ".md")]
    with open(paths[0], "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Campaign", "Alert", "Detail", "What to do", "As of (IST)"])
        for a in found:
            w.writerow([names.get(a["campaign"], a["campaign"]), a["code"], a["detail"], a["action"], when])
    with open(paths[1], "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Campaign", "Leads (GrowthX)", "Leads called", "Connected", "Positive", "Positive %",
                    "Full Conversion", "Hung up early (not positive)", "Irrelevant / not interested", "Calls",
                    "Calls by another agent", "As of (IST)"])
        for k, s in stats.items():
            w.writerow([names.get(k, k), s["leads"], s["called"], s["connected"], s["positive"],
                        f"{rates(s)['positive_rate']:.1%}", s["full"], s["hangup_early"], s["unfit"], s["calls"],
                        s["other_agent_calls"], when])
    with open(paths[2], "w", encoding="utf-8") as f:
        f.write(message(stats, found, when, names))
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out_dir")
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    with open(a.config, encoding="utf-8") as f:
        config = json.load(f)
    from analytics.funnel_icp import fetch_leads, fetch_timepay

    end = now_ist().strftime("%Y-%m-%dT%H:%M:%S")
    stats, found = run(config, set(filter(None, a.only.split(","))) or None,
                       lambda lt: fetch_leads(lt, None, None),
                       lambda cid, start: fetch_timepay([cid], start, end)[0])
    names = {k: c.get("name", k) for k, c in config["campaigns"].items()}
    for p in _write(a.out_dir, stats, found, names):
        print("report:", p)
    print(message(stats, found, end, names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
