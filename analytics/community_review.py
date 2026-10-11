"""Community collections review: what happened to every community lead each collection team was given, why payments did
not come in, whose problem it was, and where payments can still come from.

Community leads join the community for Rs 10, book a seat in one of its sales webinars with a booking amount, and the
community team (one allocator) hands them to a collection caller, who collects the balance. This review looks at both
collection teams separately and gives the community team its own view.

* **Numbers, for every community lead allocated since ``stats_from``:** outcome, speed to the first collection-team
  dial, reached within 72 hours, a real conversation (120 s+), a closure stage reached (Follow Up For Closure or Loan
  pending), collected; by team, allocation month and caller. Collection rates are compared on leads allocated 30+
  days ago, so young leads do not drag a team down.
* **A reading of every unpaid lead allocated since ``read_from``:** its history and call transcripts read in full by
  Claude in a Claude Code session (no model API), naming what happened, the primary reason it has not paid, who owns
  that reason (lead, caller, process, community team), whether better handling could have saved it, when payment can
  still be expected, how well it was counselled, and anything the community team should know.

    python -m analytics.community_review data/coll_d11 exports/community_review 2026-05-01 2026-08-01 transcripts.jsonl
    python -m analytics.community_review check <round folder>/read_01.jsonl <round folder>/batch_01.jsonl
    python -m analytics.community_review report exports/community_review data/coll_d11/contacts.json recommendations.json

``report`` writes three PDFs: one per collection team and one for the community team. ``recommendations.json`` holds the
written conclusions per report ({team: {"verdict", "why", "actions"}, "community": {"summary", "community_actions",
"teams"}}, each a list of {"title", "text"}), drawn from the numbers and the readings.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

from analytics.bootcamp_collections import _data, build, median, pct
from analytics.collection_audit import _load, dossier, flags, transcript_excerpts
from analytics.collection_plan import EXTRA_CSS, TEAM_LEADERS, TEAM_TITLE, clean_name, esc, render_pdf
from integrations.timeutil import IST, utc
from scripts.build_plan_pdf import CSS

MATURE = timedelta(days=30)
CLOSURE_STAGES = {"follow up for closure", "loan pending"}
BATCH = 25

CAUSES = {
    "Lead: no way to pay (no card, income, credit, or over age)": "Lead",
    "Lead: cannot afford it now": "Lead",
    "Lead: timing, wants a later batch": "Lead",
    "Lead: family or spouse did not agree": "Lead",
    "Lead: doubts the value or own ability": "Lead",
    "Lead: health or personal emergency": "Lead",
    "Lead: unreachable despite real attempts": "Lead",
    "Caller: not called or counselled in time": "Caller",
    "Caller: follow-up dropped (callbacks or the lead's calls missed)": "Caller",
    "Caller: pressure, add-on or confusing payment terms": "Caller",
    "Caller: over-dialled, never a real conversation": "Caller",
    "Process: loan or payment route failed, no fallback offered": "Process",
    "Process: paid or loan done, stage not updated": "Process",
    "Community: booked without understanding the balance or terms": "Community team",
    "Community: not a fit for the programme (eligibility, language, profile)": "Community team",
    "Too early: still in progress, no blocker yet": "Too early",
}
OWNERS = ("Lead", "Caller", "Process", "Community team", "Too early")
SAVABLE = ("Yes, with better handling", "Maybe", "No")
EXPECT = ("Paid already - check", "By 31 Oct", "In Nov or Dec", "Later or unclear", "Not expected")
COUNSELLING = ("Good", "Partial", "None")
TEXT = {"what_happened": 340, "why_not_paid": 240, "community_note": 220}

INSTRUCTIONS = """# Community collections review: reading round

You are Claude, reviewing community collection leads of LawSikho's two collection teams (Elite Changemakers under
Mayur Sachdeva; Team Puja Malik). A community lead joined the community for Rs 10, booked a seat in one of its sales
webinars with a booking amount, and was handed by the community team to a collection caller to collect the balance
(often around Rs 50,000-60,000, by card EMI, a finance partner's loan, or instalments). None of these leads has paid
the balance yet. Data as of {as_of} IST.

Privacy: the files hold real leads and their calls. Keep them in this folder; never quote them in chat, logs or commits.
Keep any helper scripts in a folder of your own named after your batch (for example scratch_b07/ inside this round
folder), never in a shared place.

For each line of your batch file ("lead_id", "flags", "dossier", "transcripts": recorded calls since around the
booking, oldest first), read the dossier and every transcript in full, then write one JSON object per lead, one per
line, to read_NN.jsonl in this folder, with exactly these fields:

- "lead_id": copied exactly.
- "what_happened": plain story from allocation to now: who called, when, what was discussed, where it stopped
  (max {what_happened} characters). No names, phone numbers or emails: "the lead", "the caller", "the spouse".
- "why_not_paid": the real reason the balance has not come in, in plain words (max {why_not_paid} characters).
- "primary_cause": the one root cause, exactly one of: {causes}.
- "contributing": a list (may be empty) of other causes from the same list that also played a part.
- "savable": could better handling have collected it, exactly one of: {savable}.
- "expect_payment": exactly one of: {expect}.
- "counselling": how well the lead was counselled on goal, course and payment options, exactly one of: {counselling}.
- "community_note": what the community team should know about how this lead came in (what they expected at booking,
  whether they understood the balance, whether they fit the programme), or "none" (max {community_note} characters).

How to judge the cause fairly:
- It is the lead's when the lead's own situation stops payment and the team did its part: they were reached, counselled,
  offered the routes that exist, and followed up as agreed.
- It is the caller's when the team's handling stopped it: no proper first call within a day or two, no counselling,
  callbacks or the lead's own calls missed, follow-up stopped after a good conversation, pressure, the add-on pushed
  before the balance, wrong or changing payment terms, many dials but no real conversation and no other approach.
  "Unreachable despite real attempts" is the lead's only if calls were spread over days and times and a message was sent.
- It is process when the lead was willing but the loan or payment route failed and no other route was offered, or the
  lead paid or the loan went through and the stage was never updated.
- It is the community team's when the lead booked without understanding there is a balance or what it costs (for
  example expected about Rs 3,000 a month, or thought the booking was the fee), or is not a fit for the programme
  (not eligible, cannot follow English, a minor, no use for it).
- "Too early" only for leads allocated in the last week or so where nothing has gone wrong yet.

When you finish, run from the repository root:

    .venv/bin/python -m analytics.community_review check {folder}/read_NN.jsonl {folder}/batch_NN.jsonl

and fix every problem it lists until it prints "ok".
"""


def allocated_by(acts: list[dict], booked: datetime) -> str:
    """Who set the stage to Booking fees received at the start of the lead's latest booking episode."""
    for a in acts:
        if a.get("EventCode") == 3002 and utc(a.get("CreatedOn")) == booked:
            return (_data(a).get("CreatedBy") or a.get("CreatedByName") or "").strip() or "unknown"
    return "unknown"


def reached_closure(acts: list[dict], booked: datetime) -> bool:
    return any(a.get("EventCode") == 3002 and utc(a.get("CreatedOn")) and utc(a["CreatedOn"]) >= booked
               and (_data(a).get("CurrentStage") or "").strip().lower() in CLOSURE_STAGES for a in acts)


def community_views(leads: list[dict], hist: dict[str, list], meta: dict, now: datetime, since: str) -> list[dict]:
    """Every community lead allocated since ``since``, with allocator, closure stage and maturity added."""
    views, _ = build(leads, hist, meta["team_of_owner"], now, meta.get("team_of_caller") or frozenset(meta.get("team_callers") or ()))
    out = []
    for v in views:
        if v["kind"] != "Community" or v["booked_ist"][:10] < since:
            continue
        acts = sorted((a for a in hist[v["lead_id"]] if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
        out.append({**v, "allocated_by": allocated_by(acts, v["booked"]), "closure_stage": reached_closure(acts, v["booked"]),
                    "mature": now - v["booked"] >= MATURE, "month": v["booked_ist"][:7]})
    return out


def funnel(views: list[dict]) -> dict:
    n = len(views)
    mature = [v for v in views if v["mature"]]
    out = Counter(v["outcome"] for v in views)
    closure = [v for v in views if v["closure_stage"]]
    return {
        "allocated": n, "collected": out["collected"], "lost": out["lost"] + out["deferred"], "open": out["open"],
        "mature": len(mature), "collected_%_mature": pct(sum(v["outcome"] == "collected" for v in mature), len(mature)),
        "open_%_mature": pct(sum(v["outcome"] == "open" for v in mature), len(mature)),
        "lost_%_mature": pct(sum(v["outcome"] in ("lost", "deferred") for v in mature), len(mature)),
        "median_hrs_to_team_dial": median([v["hrs_handover_to_team_dial"] for v in views]),
        "team_dialled_24h_%": pct(sum(v["team_dialled_24h"] for v in views), n),
        "reached_72h_%": pct(sum(v["connected_72h"] for v in views), n),
        "real_conv_%": pct(sum(1 for v in views if v["real_convs"]), n),
        "closure_stage_%": pct(len(closure), n),
        "collected_%_of_closure_mature": pct(sum(v["outcome"] == "collected" for v in closure if v["mature"]),
                                             sum(1 for v in closure if v["mature"])),
        "median_days_to_collect": median([v["days_to_collect"] for v in views]),
        "unreturned_call_%": pct(sum(1 for v in views if v["inbound_unreturned_days"]), n),
        "dialled_5plus_a_day_%": pct(sum(1 for v in views if v["max_dials_one_day"] >= 5), n),
        "first_talk_under_2min_%": pct(sum(1 for v in views if v["first_talk_min"] is not None and v["first_talk_min"] < 2),
                                       sum(1 for v in views if v["first_talk_min"] is not None)),
    }


# ----------------------------------------------------------------------------------------------- reading round

def check_reading(read_path: str, batch_path: str) -> list[str]:
    want = [json.loads(line)["lead_id"] for line in open(batch_path, encoding="utf-8") if line.strip()]
    problems, seen = [], Counter()
    for n, line in enumerate(open(read_path, encoding="utf-8"), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            problems.append(f"line {n}: not JSON")
            continue
        seen[r.get("lead_id")] += 1
        if r.get("lead_id") not in want:
            problems.append(f"line {n}: lead_id {r.get('lead_id')} is not in the batch")
        for key, limit in TEXT.items():
            if not isinstance(r.get(key), str) or not r[key].strip():
                problems.append(f"line {n}: {key} missing")
            elif len(r[key]) > limit:
                problems.append(f"line {n}: {key} longer than {limit} characters")
        for key, allowed in (("primary_cause", CAUSES), ("savable", SAVABLE), ("expect_payment", EXPECT),
                             ("counselling", COUNSELLING)):
            if r.get(key) not in allowed:
                problems.append(f"line {n}: {key} {r.get(key)!r} not in the list")
        extra = r.get("contributing")
        if not isinstance(extra, list) or any(c not in CAUSES for c in extra):
            problems.append(f"line {n}: contributing must be a list of causes from the list")
    problems += [f"lead {lid} read {k} times" for lid, k in seen.items() if k > 1]
    problems += [f"lead {lid} not read" for lid in want if not seen[lid]]
    return problems


def write_round(items: list[dict], reading_dir: str, as_of: str) -> str:
    folder = os.path.join(reading_dir, as_of.replace("-", "").replace(" ", "-").replace(":", ""))
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if name.startswith("batch_"):
            os.remove(os.path.join(folder, name))
    for i in range(0, len(items), BATCH):
        with open(os.path.join(folder, f"batch_{i // BATCH + 1:02d}.jsonl"), "w", encoding="utf-8") as fh:
            for item in items[i:i + BATCH]:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    text = INSTRUCTIONS.format(as_of=as_of, folder=folder, causes="; ".join(CAUSES), savable="; ".join(SAVABLE),
                               expect="; ".join(EXPECT), counselling="; ".join(COUNSELLING), **TEXT)
    open(os.path.join(folder, "INSTRUCTIONS.md"), "w", encoding="utf-8").write(text)
    return folder


def load_readings(reading_dir: str) -> dict[str, dict]:
    """Every reading that passes its check; a later round wins."""
    out = {}
    if not os.path.isdir(reading_dir):
        return out
    for rnd in sorted(os.listdir(reading_dir)):
        folder = os.path.join(reading_dir, rnd)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not (name.startswith("read_") and name.endswith(".jsonl")):
                continue
            batch = os.path.join(folder, name.replace("read_", "batch_"))
            path = os.path.join(folder, name)
            if not os.path.exists(batch) or check_reading(path, batch):
                print(f"skipped {rnd}/{name}: run the check on it", file=sys.stderr)
                continue
            for line in open(path, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    out[r["lead_id"]] = {**r, "cause_owner": CAUSES[r["primary_cause"]]}
    return out


def reading_items(views: list[dict], leads: list[dict], hist: dict[str, list], now: datetime,
                  transcripts: dict[str, list]) -> list[dict]:
    by_id = {l["ProspectID"]: l for l in leads}
    items = []
    for v in views:
        acts = sorted((a for a in hist[v["lead_id"]] if utc(a.get("CreatedOn"))), key=lambda a: a["CreatedOn"])
        items.append({"lead_id": v["lead_id"], "flags": flags(v, acts, v["booked"], now),
                      "dossier": dossier(by_id[v["lead_id"]], v, acts, v["booked"], now),
                      "transcripts": transcript_excerpts(transcripts.get(v["lead_id"], []), v["booked"])})
    return items


def main(data_dir: str, out_dir: str, stats_from: str, read_from: str, transcripts_path: str) -> None:
    meta, leads, hist = _load(data_dir)
    now = utc(meta["fetched_at_utc"])
    as_of = now.astimezone(IST).strftime("%Y-%m-%d %H:%M")
    views = community_views(leads, hist, meta, now, stats_from)
    to_read = [v for v in views if v["booked_ist"][:10] >= read_from and v["outcome"] != "collected"]
    reading_dir = os.path.join(out_dir, "reading")
    readings = load_readings(reading_dir)
    unread = [v for v in to_read if v["lead_id"] not in readings]
    os.makedirs(out_dir, exist_ok=True)
    if unread:
        calls = {}
        for line in open(transcripts_path, encoding="utf-8"):
            d = json.loads(line)
            calls[d["lead_id"]] = d["calls"]
        folder = write_round(reading_items(unread, leads, hist, now, calls), reading_dir, as_of)
        missing = sum(1 for v in unread if v["lead_id"] not in calls)
        print(f"{len(unread)} unpaid leads to read ({missing} with no transcript search yet): {folder}", file=sys.stderr)
    data = {"as_of_ist": as_of, "stats_from": stats_from, "read_from": read_from,
            "views": [{k: x for k, x in v.items() if k != "booked"} for v in views], "readings": readings}
    json.dump(data, open(os.path.join(out_dir, "review_data.json"), "w"), default=str)
    for team in sorted({v["team"] for v in views}):
        f = funnel([v for v in views if v["team"] == team])
        print(team, json.dumps(f))
    print(f"to read: {len(to_read)}, read: {len(to_read) - len(unread)}")


# ----------------------------------------------------------------------------------------------- reports

TEAMS = ("Elite Changemakers", "Team Puja Malik (DSV+Women AI)")
SHORT = {"Elite Changemakers": "Mayur's team", "Team Puja Malik (DSV+Women AI)": "Pooja's team"}
OWNER_CLASS = {"Lead": "B", "Caller": "A", "Process": "F", "Community team": "R", "Too early": "C"}
REVIEW_CSS = """
.leads td { font-size: 8.2pt; } .leads td.num { width: 14pt; } .leads td.lead { width: 17%; }
.tier-A td { background: #fdeceb; } .tier-B td { background: #f4f7fc; } .tier-F td { background: #fbf8f0; }
.tier-R td { background: #f3eaf7; } .tier-C td { background: #f2f2f2; }
.band-h { margin-top: 14pt; padding: 5pt 9pt; border-radius: 4pt; color: #fff; background: #1f4e46; font-weight: bold; }
.num-r { text-align: right; }
"""
FUNNEL_ROWS = (
    ("Leads allocated", "allocated", ""), ("Allocated 30+ days ago", "mature", ""),
    ("Collected (of leads allocated 30+ days ago)", "collected_%_mature", "%"),
    ("Still open after 30+ days", "open_%_mature", "%"), ("Lost or deferred (30+ days)", "lost_%_mature", "%"),
    ("First dial by the collection team within 24 h", "team_dialled_24h_%", "%"),
    ("Median hours from hand-over to first dial", "median_hrs_to_team_dial", " h"),
    ("Reached within 72 h", "reached_72h_%", "%"), ("Had a real conversation (2 min+)", "real_conv_%", "%"),
    ("Median days from allocation to payment", "median_days_to_collect", " days"),
    ("A call from the lead not returned the same day", "unreturned_call_%", "%"),
    ("Dialled 5+ times in one day", "dialled_5plus_a_day_%", "%"),
    ("First answered call under 2 minutes", "first_talk_under_2min_%", "%"),
)


def _fmt(x, unit: str) -> str:
    return "–" if x is None else f"{x}{unit}"


def owner_table(readings: list[dict]) -> str:
    n = len(readings)
    owners = Counter(r["cause_owner"] for r in readings)
    savable = Counter((r["cause_owner"], r["savable"]) for r in readings)
    rows = "".join(f"<tr><td><b>{esc(o)}</b></td><td>{owners[o]}</td><td>{100 * owners[o] / n:.0f}%</td>"
                   f"<td>{savable[(o, SAVABLE[0])]}</td><td>{savable[(o, SAVABLE[1])]}</td></tr>" for o in OWNERS if owners[o])
    return (f"<table><thead><tr><th>Whose problem</th><th>Leads</th><th>Share</th><th>Savable with better handling</th>"
            f"<th>Maybe savable</th></tr></thead><tbody>{rows}<tr><td><b>All unpaid leads read</b></td><td>{n}</td><td>100%</td>"
            f"<td>{sum(r['savable'] == SAVABLE[0] for r in readings)}</td><td>{sum(r['savable'] == SAVABLE[1] for r in readings)}</td>"
            f"</tr></tbody></table>")


def cause_table(readings: list[dict]) -> str:
    c = Counter(r["primary_cause"] for r in readings)
    part = Counter(x for r in readings for x in r.get("contributing") or [])
    rows = "".join(f"<tr><td>{esc(k)}</td><td>{c[k]}</td><td>{part[k]}</td></tr>" for k in CAUSES if c[k] or part[k])
    return (f"<table><thead><tr><th>Cause</th><th>Main cause</th><th>Also played a part</th></tr></thead><tbody>{rows}</tbody></table>")


def funnel_table(fs: dict[str, dict]) -> str:
    heads = "".join(f"<th>{esc(k)}</th>" for k in fs)
    rows = "".join(f"<tr><td>{esc(label)}</td>" + "".join(f"<td>{_fmt(f[key], unit)}</td>" for f in fs.values()) + "</tr>"
                   for label, key, unit in FUNNEL_ROWS)
    return f"<table><thead><tr><th></th>{heads}</tr></thead><tbody>{rows}</tbody></table>"


def monthly_table(views: list[dict]) -> str:
    months = sorted({v["month"] for v in views})
    rows = []
    for m in months:
        cells = []
        for t in TEAMS:
            f = funnel([v for v in views if v["team"] == t and v["month"] == m])
            cells.append(f"<td>{f['allocated']}</td><td>{_fmt(f['collected_%_mature'], '%')}</td><td>{_fmt(f['open_%_mature'], '%')}</td>"
                         f"<td>{_fmt(f['team_dialled_24h_%'], '%')}</td>")
        rows.append(f"<tr><td><b>{m}</b></td>{''.join(cells)}</tr>")
    sub = "<th>Allocated</th><th>Collected*</th><th>Still open*</th><th>Dialled in 24 h</th>"
    return ("<table><thead><tr><th rowspan='2'>Month allocated</th>" + "".join(f"<th colspan='4'>{SHORT[t]}</th>" for t in TEAMS)
            + f"</tr><tr>{sub * len(TEAMS)}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            "<p class='note'>* Of leads allocated 30+ days ago; blank for the current month.</p>")


def caller_table(views: list[dict], readings: dict[str, dict]) -> str:
    by = {}
    for v in views:
        by.setdefault(v["caller"], []).append(v)
    rows = []
    for c in sorted(by, key=lambda c: -len(by[c])):
        vs = by[c]
        f = funnel(vs)
        rd = [readings[v["lead_id"]] for v in vs if v["lead_id"] in readings]
        caller_owned = sum(r["cause_owner"] == "Caller" for r in rd)
        no_counsel = sum(r["counselling"] == "None" for r in rd)
        rows.append(f"<tr><td><b>{esc(c)}</b></td><td>{f['allocated']}</td><td>{_fmt(f['collected_%_mature'], '%')}</td>"
                    f"<td>{_fmt(f['open_%_mature'], '%')}</td><td>{_fmt(f['median_hrs_to_team_dial'], '')}</td>"
                    f"<td>{_fmt(f['reached_72h_%'], '%')}</td><td>{_fmt(f['unreturned_call_%'], '%')}</td>"
                    f"<td>{len(rd)}</td><td>{caller_owned}</td><td>{no_counsel}</td>"
                    f"<td>{sum(r['savable'] == SAVABLE[0] for r in rd)}</td></tr>")
    return ("<table><thead><tr><th>Caller</th><th>Allocated since May</th><th>Collected*</th><th>Still open*</th>"
            "<th>Median hours to first dial</th><th>Reached in 72 h</th><th>Lead's call not returned</th>"
            "<th>Unpaid leads read (Aug–Oct)</th><th>Caller was the main cause</th><th>Never counselled</th>"
            f"<th>Savable with better handling</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            "<p class='note'>* Of leads allocated 30+ days ago. Callers with few leads swing a lot; read their leads, not the "
            "percentage. Unpaid leads are credited to the caller they settled with in the first 24 h.</p>")


def lead_rows(items: list[tuple[dict, dict]], names: dict[str, dict], with_team: bool = False) -> str:
    out = []
    for i, (v, r) in enumerate(items, 1):
        team = f" · {esc(SHORT.get(v['team'], v['team']))}" if with_team else ""
        out.append(f"""<tr class="tier-{OWNER_CLASS[r['cause_owner']]}"><td class="num">{i}</td>
<td class="lead"><b>{esc(clean_name(names.get(v['lead_id'], {}).get('name', ''))) or 'Unnamed lead'}</b>
<div class="sub">{esc(v['caller'])}{team}</div><div class="sub">allocated {esc(v['booked_ist'][:10])} · {esc(v['outcome'])} · {esc(v['stage'])}</div></td>
<td>{esc(r['what_happened'], 340)}</td><td>{esc(r['why_not_paid'], 240)}<div class="sub"><b>{esc(r['primary_cause'])}</b></div></td>
<td>{esc(r['savable'])}<div class="sub">Expect: {esc(r['expect_payment'])} · Counselling: {esc(r['counselling'])}</div></td></tr>""")
    return ("<table class='leads'><thead><tr><th>#</th><th>Lead</th><th>What happened</th><th>Why it has not paid</th>"
            f"<th>Savable / expected</th></tr></thead><tbody>{''.join(out)}</tbody></table>")


def _bullets(items: list[dict]) -> str:
    return "<ul>" + "".join(f"<li><b>{esc(x['title'])}</b> {esc(x['text'])}</li>" for x in items) + "</ul>" if items else ""


def team_html(team: str, data: dict, names: dict, recs: dict) -> str:
    views = [v for v in data["views"] if v["team"] in TEAMS]
    mine = [v for v in views if v["team"] == team]
    readings = data["readings"]
    read = [(v, readings[v["lead_id"]]) for v in mine if v["lead_id"] in readings]
    rd = [r for _, r in read]
    other = next(t for t in TEAMS if t != team)
    f_me, f_other = funnel(mine), funnel([v for v in views if v["team"] == other])
    owners = Counter(r["cause_owner"] for r in rd)
    expect = Counter(r["expect_payment"] for r in rd)
    n = len(rd) or 1
    leader = TEAM_LEADERS.get(team, "the team leader")
    title = TEAM_TITLE.get(team, team)
    expected = [(v, r) for v, r in read if r["expect_payment"] in EXPECT[:3]]
    expected.sort(key=lambda x: (EXPECT.index(x[1]["expect_payment"]), x[0]["caller"]))
    groups = "".join(
        f'<div class="band-h">{esc(o)}: {owners[o]} leads</div>' + lead_rows(
            sorted([(v, r) for v, r in read if r["cause_owner"] == o], key=lambda x: (x[1]["primary_cause"], x[0]["caller"])), names)
        for o in OWNERS if owners[o])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)} – community collections review</title>
<style>{CSS}{EXTRA_CSS}{REVIEW_CSS}</style></head><body>
<div class="cover"><div class="band"><div class="eyebrow">{esc(title)} · for {esc(leader)}</div>
<h1>Community collections review</h1>
<p>What happened to every community lead the team was given since {esc(data['stats_from'][:7])}, why payments did not come in,
whether it was a sales problem or a lead problem, and where payments can still come from.</p></div>
<div><div class="kpis">
<div><b>{f_me['allocated']}</b><span>community leads allocated since {esc(data['stats_from'])}</span></div>
<div><b>{_fmt(f_me['collected_%_mature'], '%')}</b><span>collected, of leads allocated 30+ days ago ({SHORT[other]}: {_fmt(f_other['collected_%_mature'], '%')})</span></div>
<div><b>{_fmt(f_me['open_%_mature'], '%')}</b><span>still open after 30+ days ({SHORT[other]}: {_fmt(f_other['open_%_mature'], '%')})</span></div>
<div><b>{len(rd)}</b><span>unpaid leads allocated since {esc(data['read_from'])}, each read in full with its calls</span></div>
<div><b>{100 * owners['Caller'] / n:.0f}%</b><span>of them did not pay mainly because of how they were handled</span></div>
<div><b>{expect['Paid already - check']} + {expect['By 31 Oct']}</b><span>show signs of having paid (finance to confirm) + expected to pay by 31 Oct</span></div>
</div>
<p class="note">Data: LeadSquared stages, calls, notes and owner changes as of {esc(data['as_of_ist'])} IST, and the recorded
calls of every unpaid lead read. Collected means the stage reached Collections done or Course Enrolled; LeadSquared holds no
payment amounts yet. A lead belongs to the collection caller it settled with in the first 24 hours after allocation.</p></div></div>

<section><h2>1. Sales problem or lead problem?</h2>
<p>Each unpaid lead allocated since {esc(data['read_from'])} was read in full (history, notes and every recorded call) and given
one main reason it has not paid, and who owns that reason.</p>
{owner_table(rd)}
{_bullets(recs.get('verdict', []))}</section>

<section><h2>2. How the team performs against {esc(SHORT[other])}</h2>
{funnel_table({SHORT[team]: f_me, SHORT[other]: f_other})}
<h3>Month by month</h3>{monthly_table(views)}
<p class="note">The two teams record stages differently ({SHORT[team]} moves {_fmt(f_me['closure_stage_%'], '%')} of leads to a closure
stage, {SHORT[other]} {_fmt(f_other['closure_stage_%'], '%')}), so stage-based measures are not compared.</p></section>

<section><h2>3. Why payments did not come in</h2>{cause_table(rd)}
<h3>Counselling on unpaid leads</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in Counter(r['counselling'] for r in rd).most_common())}</tbody></table>
{_bullets(recs.get('why', []))}</section>

<section><h2>4. Callers</h2>{caller_table(mine, readings)}</section>

<section><h2>5. Where payments can still come from</h2>
<table><thead><tr><th>Expected</th><th>Leads</th></tr></thead><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{expect[k]}</td></tr>" for k in EXPECT)}</tbody></table>
<p>The leads that may have paid already or are expected to pay by December:</p>{lead_rows(expected, names)}</section>

<section><h2>6. What to do differently</h2>{_bullets(recs.get('actions', []))}</section>

<section><h2>Appendix: every unpaid lead since {esc(data['read_from'])}, by whose problem it is</h2>{groups}</section>
</body></html>"""


def community_html(data: dict, names: dict, recs: dict) -> str:
    views = [v for v in data["views"] if v["team"] in TEAMS]
    readings = data["readings"]
    read = [(v, readings[v["lead_id"]]) for v in views if v["lead_id"] in readings]
    rd = [r for _, r in read]
    fs = {SHORT[t]: funnel([v for v in views if v["team"] == t]) for t in TEAMS}
    by_team = {t: [r for v, r in read if v["team"] == t] for t in TEAMS}
    alloc = Counter(v["allocated_by"] for v in data["views"]).most_common(6)
    days = Counter(v["booked_day"] for v in data["views"])
    months = sorted({v["month"] for v in views})
    vol = "".join(f"<tr><td>{m}</td>" + "".join(f"<td>{sum(1 for v in views if v['month'] == m and v['team'] == t)}</td>" for t in TEAMS)
                  + "</tr>" for m in months)
    owners_by_team = "".join(
        f"<tr><td><b>{esc(o)}</b></td>" + "".join(f"<td>{sum(r['cause_owner'] == o for r in by_team[t])} "
                                                  f"({100 * sum(r['cause_owner'] == o for r in by_team[t]) / max(1, len(by_team[t])):.0f}%)</td>"
                                                  for t in TEAMS) + "</tr>" for o in OWNERS)
    community = sorted([(v, r) for v, r in read if r["cause_owner"] == "Community team"
                        or (r["community_note"].strip().lower() not in ("none", "none.", ""))],
                       key=lambda x: (x[1]["cause_owner"] != "Community team", x[0]["booked_ist"]))
    lead_side = Counter(r["primary_cause"] for r in rd if r["cause_owner"] in ("Lead", "Community team"))
    note_rows = "".join(
        f"<tr class='tier-{OWNER_CLASS[r['cause_owner']]}'><td>{i}</td><td><b>{esc(clean_name(names.get(v['lead_id'], {}).get('name', '')))}</b>"
        f"<div class='sub'>{esc(SHORT[v['team']])} · allocated {esc(v['booked_ist'][:10])}</div></td>"
        f"<td>{esc(r['community_note'], 220)}</td><td>{esc(r['primary_cause'])}</td></tr>"
        for i, (v, r) in enumerate(community, 1))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Community leads after hand-over – review</title>
<style>{CSS}{EXTRA_CSS}{REVIEW_CSS}</style></head><body>
<div class="cover"><div class="band"><div class="eyebrow">For the community team</div>
<h1>Community leads<br>after hand-over</h1>
<p>What happened to the community leads handed to the two collection teams since {esc(data['stats_from'][:7])}, why many have not
paid the balance, which reasons sit with the lead or with how leads come in, and how the two teams compare.</p></div>
<div><div class="kpis">
<div><b>{len(views)}</b><span>community leads handed to the two collection teams since {esc(data['stats_from'])}</span></div>
<div><b>{_fmt(fs[SHORT[TEAMS[0]]]['collected_%_mature'], '%')} / {_fmt(fs[SHORT[TEAMS[1]]]['collected_%_mature'], '%')}</b><span>collected, of leads handed over 30+ days ago ({SHORT[TEAMS[0]]} / {SHORT[TEAMS[1]]})</span></div>
<div><b>{len(rd)}</b><span>unpaid leads handed over since {esc(data['read_from'])}, each read in full with its calls</span></div>
<div><b>{100 * sum(r['cause_owner'] == 'Lead' for r in rd) / max(1, len(rd)):.0f}%</b><span>did not pay mainly for the lead's own reasons</span></div>
<div><b>{100 * sum(r['cause_owner'] == 'Community team' for r in rd) / max(1, len(rd)):.0f}%</b><span>did not pay mainly because of how they came in (fit, what they understood at booking)</span></div>
<div><b>{100 * sum(r['cause_owner'] == 'Caller' for r in rd) / max(1, len(rd)):.0f}%</b><span>did not pay mainly because of the collection team's handling</span></div>
</div>
<p class="note">Data: LeadSquared as of {esc(data['as_of_ist'])} IST and the recorded calls of every unpaid lead. A lead is credited
to the collection caller it settled with in the first 24 hours after hand-over.</p></div></div>

<section><h2>1. Summary for the community team</h2>{_bullets(recs.get('summary', []))}</section>

<section><h2>2. Why community leads have not paid</h2>
<table><thead><tr><th>Whose problem</th>{''.join(f"<th>{SHORT[t]}</th>" for t in TEAMS)}</tr></thead><tbody>{owners_by_team}</tbody></table>
{cause_table(rd)}
<h3>The lead's side, in detail</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in lead_side.most_common())}</tbody></table></section>

<section><h2>3. What the community team can change</h2>{_bullets(recs.get('community_actions', []))}
<h3>Leads where how they came in mattered ({len(community)})</h3>
<table class="leads"><thead><tr><th>#</th><th>Lead</th><th>What the community team should know</th><th>Main cause</th></tr></thead>
<tbody>{note_rows}</tbody></table></section>

<section><h2>4. How the two collection teams perform</h2>{funnel_table(fs)}
<h3>Month by month</h3>{monthly_table(views)}{_bullets(recs.get('teams', []))}</section>

<section><h2>5. Hand-over</h2>
<table><thead><tr><th>Month handed over</th>{''.join(f"<th>{SHORT[t]}</th>" for t in TEAMS)}</tr></thead><tbody>{vol}</tbody></table>
<div class="two"><div><h3>Who handed leads over</h3><table><tbody>{''.join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in alloc)}</tbody></table></div>
<div><h3>Day of hand-over</h3><table><tbody>{''.join(f"<tr><td>{d}</td><td>{days[d]}</td></tr>" for d in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'))}</tbody></table></div></div>
<p class="note">The hand-over time is when the stage was set to Booking fees received and the lead assigned to a collection caller;
the time between a lead's payment and its hand-over is not visible until payment data is connected.</p></section>
</body></html>"""


def report(out_dir: str, contacts_path: str, recs_path: str) -> None:
    data = json.load(open(os.path.join(out_dir, "review_data.json")))
    data["readings"] = load_readings(os.path.join(out_dir, "reading"))
    names = json.load(open(contacts_path))["leads"]
    recs = json.load(open(recs_path)) if recs_path and os.path.exists(recs_path) else {}
    pages = [(f"{('Elite' if t.startswith('Elite') else 'Puja_Malik')}_community_review", team_html(t, data, names, recs.get(t, {})))
             for t in TEAMS]
    pages.append(("Community_team_review", community_html(data, names, recs.get("community", {}))))
    for slug, page in pages:
        path = os.path.join(out_dir, f"{slug}.html")
        open(path, "w", encoding="utf-8").write(page)
        made = render_pdf(path, path.replace(".html", ".pdf"))
        print(path.replace(".html", ".pdf") if made else path)


if __name__ == "__main__":
    if sys.argv[1:2] == ["report"]:
        report(*sys.argv[2:5])
        sys.exit(0)
    if sys.argv[1:2] == ["check"]:
        found = check_reading(sys.argv[2], sys.argv[3])
        print("\n".join(found) or "ok")
        sys.exit(1 if found else 0)
    main(*sys.argv[1:6])
