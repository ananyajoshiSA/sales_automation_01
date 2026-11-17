"""Daily bootcamp collection reports. A run folder holds one weekend folder per November bootcamp (w1, w2, ...), each
fetched by scripts/fetch_bootcamp_collections.py and scripts/fetch_lead_histories.py, plus notes.jsonl from
scripts/fetch_lead_notes.py. Steps:

  prepare  — fetch the lead comment fields and one run of transcripts (read-only), then write a timeline per open lead
             to <run_dir>/tl/ for the graders (only leads with activity after --since, if given)
  morning  — build the call list for <day> from the facts and <run_dir>/judgements.json (lead_id → {date: grade})

    python -m analytics.collection_daily prepare <run_dir> [--since YYYY-MM-DDTHH:MM] [--no-fetch]
    python -m analytics.collection_daily morning <run_dir> <day YYYY-MM-DD> <out_dir>
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import date, datetime

from analytics.collection_daily import facts, morning, timelines
from integrations.timeutil import IST, now_ist


def judged_on(path: str, day: date) -> dict:
    """Each lead's latest grade dated before ``day``."""
    out = {}
    for lead_id, by_date in json.load(open(path)).items():
        dates = sorted(d for d in by_date if d < day.isoformat())
        if dates:
            out[lead_id] = by_date[dates[-1]]
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m analytics.collection_daily")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("run_dir")
    p.add_argument("--since")
    p.add_argument("--no-fetch", action="store_true")
    m = sub.add_parser("morning")
    m.add_argument("run_dir")
    m.add_argument("day")
    m.add_argument("out_dir")
    a = ap.parse_args(argv)
    cut = now_ist()
    leads = facts.collect(a.run_dir, cut)
    if a.cmd == "prepare":
        tx = os.path.join(a.run_dir, "transcripts")
        if not a.no_fetch:
            timelines.fetch_lead_fields(a.run_dir, [d for d, _ in facts.weekends(a.run_dir)])
            open_phones = [l["phone"] for l in leads if not l["enrolled"] and not l["dropped"] and l["real_talks"]]
            print("transcripts fetched %d, still to fetch %d" % timelines.fetch_transcripts(tx, open_phones))
        older = sorted(glob.glob("data/**/transcripts", recursive=True), key=os.path.getmtime, reverse=True)
        since = datetime.fromisoformat(a.since).replace(tzinfo=IST) if a.since else None
        index = timelines.write(a.run_dir, leads, cut, [tx] + [d for d in older if os.path.abspath(d) != os.path.abspath(tx)], since)
        print(f"{len(index)} timelines in {a.run_dir}/tl")
    else:
        day = date.fromisoformat(a.day)
        xlsx, saved = morning.build(leads, judged_on(os.path.join(a.run_dir, "judgements.json"), day), day, cut, a.out_dir)
        print(xlsx)
        print(saved)


if __name__ == "__main__":
    main()
