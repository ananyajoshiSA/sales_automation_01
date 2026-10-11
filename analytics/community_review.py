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
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

from analytics.bootcamp_collections import _data, build, median, pct
from analytics.collection_audit import _load, dossier, flags, transcript_excerpts
from integrations.timeutil import IST, utc

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


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        found = check_reading(sys.argv[2], sys.argv[3])
        print("\n".join(found) or "ok")
        sys.exit(1 if found else 0)
    main(*sys.argv[1:6])
