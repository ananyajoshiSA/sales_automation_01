"""Daily bootcamp collection reports. A run folder holds one weekend folder per November bootcamp (w1, w2, ...), each
fetched by scripts/fetch_bootcamp_collections.py and scripts/fetch_lead_histories.py, plus notes.jsonl from
scripts/fetch_lead_notes.py. Steps:

  prepare  — fetch the lead comment fields and one run of transcripts (read-only), then write a timeline per open lead
             to <run_dir>/tl/ for the graders (only leads with activity after --since, if given)
  merge    — fold the readers' files (<run_dir>/grade/out*.json) into <run_dir>/judgements.json under <date>, checking
             each grade against the lead's calls
  morning  — build the call list for <day> from the facts and <run_dir>/judgements.json (lead_id → {date: grade})

    python -m analytics.collection_daily prepare <run_dir> [--since YYYY-MM-DDTHH:MM] [--no-fetch]
    python -m analytics.collection_daily merge <run_dir> <date YYYY-MM-DD>
    python -m analytics.collection_daily morning <run_dir> <day YYYY-MM-DD> <out_dir>
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import date, datetime

from analytics.collection_daily import facts, morning, timelines
from integrations.timeutil import IST


def judged_on(path: str, upto: date) -> dict:
    """Each lead's latest grade made on or before ``upto`` (the day the data was read)."""
    out = {}
    for lead_id, by_date in json.load(open(path)).items():
        dates = sorted(d for d in by_date if d <= upto.isoformat())
        if dates:
            out[lead_id] = by_date[dates[-1]]
    return out


def merge(run_dir: str, day: str, leads: list[dict]) -> tuple[int, list[str]]:
    """Add the readers' grades under ``day``; returns (grades added, open leads still without a grade)."""
    path = os.path.join(run_dir, "judgements.json")
    store = json.load(open(path)) if os.path.exists(path) else {}
    by_id = {l["lead_id"]: l for l in leads}
    added = 0
    for f in sorted(glob.glob(os.path.join(run_dir, "grade", "out*.json"))):
        for lead_id, g in json.load(open(f)).items():
            if lead_id in by_id:
                morning.check(by_id[lead_id], g)
                store.setdefault(lead_id, {})[day] = g
                added += 1
    json.dump(store, open(path, "w"), indent=1, ensure_ascii=False)
    missing = [l["lead_id"] for l in leads if not l["enrolled"] and not l["dropped"] and l["lead_id"] not in store]
    return added, missing


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m analytics.collection_daily")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("run_dir")
    p.add_argument("--since")
    p.add_argument("--no-fetch", action="store_true")
    g = sub.add_parser("merge")
    g.add_argument("run_dir")
    g.add_argument("day")
    m = sub.add_parser("morning")
    m.add_argument("run_dir")
    m.add_argument("day")
    m.add_argument("out_dir")
    a = ap.parse_args(argv)
    cut = facts.read_at(a.run_dir)  # the data's own time, not the clock: a dial after the fetch is not "missed"
    leads = facts.collect(a.run_dir, cut)
    if a.cmd == "prepare":
        tx = os.path.join(a.run_dir, "transcripts")
        older = sorted(glob.glob("data/**/transcripts", recursive=True), key=os.path.getmtime, reverse=True)
        tx_dirs = [tx] + [d for d in older if os.path.abspath(d) != os.path.abspath(tx)]
        if not a.no_fetch:
            timelines.fetch_lead_fields(a.run_dir, [d for d, _ in facts.weekends(a.run_dir)])
            # every number with a real talk that no saved transcript covers, read again unless already read after the data
            todo = timelines.without_transcript(leads, tx_dirs, cut)
            print("transcripts fetched %d, still to fetch %d" % timelines.fetch_transcripts(tx, todo, fresh_after=cut))
        since = datetime.fromisoformat(a.since).replace(tzinfo=IST) if a.since else None
        index = timelines.write(a.run_dir, leads, cut, tx_dirs, since)
        print(f"{len(index)} timelines in {a.run_dir}/tl")
    elif a.cmd == "merge":
        added, missing = merge(a.run_dir, a.day, leads)
        print(f"{added} grades added; {len(missing)} open leads without a grade")
    else:
        day = date.fromisoformat(a.day)
        books, saved = morning.build(leads, judged_on(os.path.join(a.run_dir, "judgements.json"), cut.astimezone(IST).date()),
                                     day, cut, a.out_dir)
        print("\n".join(books + [saved]))


if __name__ == "__main__":
    main()
