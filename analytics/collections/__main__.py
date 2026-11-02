"""Build the community and bootcamp collection reports from one fetch (``scripts/fetch_collections.py``).

Each report is a PDF (who to call first, then money by month, team, collector and community or bootcamp, then the
data checks) and a workbook with every student. Runs with no manual step: students are matched to LeadSquared by
phone then email, bootcamps paired to their LeadSquared tag, communities typed from Funnel name; whatever cannot be
placed is labelled and kept in the totals. Reads files only.

    python -m analytics.collections data/collections/2026-11-03 exports/collections/2026-11-03 [community|bootcamp]
"""

from __future__ import annotations

import json
import os
import sys

from analytics.collections import render
from analytics.collections.match import LeadIndex, assign_community, match_students, pair_bootcamps
from analytics.collections.report import build
from analytics.collections.sheets import read_bootcamp, read_community


def load_tagged(data_dir: str) -> list[tuple[str, dict]]:
    out = []
    for line in open(os.path.join(data_dir, "lsq_tags.jsonl")):
        if line.strip():
            d = json.loads(line)
            out += [(d["tag"], l) for l in d["leads"]]
    return out


def reports(data_dir: str, which: tuple[str, ...] = ("community", "bootcamp")) -> dict[str, dict]:
    meta = json.load(open(os.path.join(data_dir, "meta.json")))
    tagged = load_tagged(data_dir)
    index = LeadIndex(tagged)
    out = {}
    if "community" in which:
        rows, note = read_community(os.path.join(data_dir, "community.xlsx"))
        match_students(rows, index, "community")
        assign_community(rows)
        out["community"] = build(rows, tagged, "community", note, meta)
    if "bootcamp" in which:
        rows, note = read_bootcamp(os.path.join(data_dir, "bootcamp.xlsx"))
        match_students(rows, index, "bootcamp")
        pairs = pair_bootcamps(rows, meta.get("tags") or [t for t, _ in tagged])
        out["bootcamp"] = build(rows, tagged, "bootcamp", note, meta, pairs)
    return out


def main(data_dir: str, out_dir: str, which: str = "") -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    day = os.path.basename(os.path.normpath(data_dir))
    files = []
    for kind, R in reports(data_dir, (which,) if which else ("community", "bootcamp")).items():
        base = os.path.join(out_dir, f"{kind.title()}_collections_{day}")
        files.append(render.workbook(R, base + ".xlsx"))
        files.append(render.pdf(R, base + ".html", base + ".pdf"))
        T, C = R["total"], R["checks"]
        print(f"{kind}: {T['students']:,} students, received Rs {T['received']:,}, still to collect Rs {T['balance_open']:,}, "
              f"matched {C['matched']:,}/{C['students']:,}, disagreements {sum(C['flags'].values()):,}", file=sys.stderr)
    for f in files:
        print(f)
    return files


if __name__ == "__main__":
    main(*sys.argv[1:4])
