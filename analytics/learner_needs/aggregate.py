"""Combine the readers' one-line-per-lead TSVs into counts a trainer can trust.

* ``segment`` labels differ between readers, so each bootcamp config maps every label to one standard
  group (``groups``). Unmapped labels are listed, never guessed.
* Hopes, doubts, misconceptions and questions are free text. Claude reads them and writes
  ``themes.tsv`` (``lead<TAB>kind<TAB>theme``, one row per theme a lead raised); counts come from there.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
from collections import Counter, defaultdict

COLS = ["lead", "funnel", "who", "segment", "why_registered", "hopes", "time", "doubts", "misconceptions",
        "questions", "resonated", "their_words"]
KINDS = ("hope", "doubt", "misconception", "question", "resonated")
NOT_HEARD = "Not enough said"

csv.field_size_limit(10 ** 8)


def read_chunk_tsv(path: str) -> list[dict]:
    """Rows of one reader file; ``key`` is ``<chunk>-<lead number>``."""
    chunk = os.path.splitext(os.path.basename(path))[0]
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, row in enumerate(csv.reader(f, delimiter="\t")):
            if i == 0 and row and row[0].strip().lower() == "lead":
                continue
            if len(row) < len(COLS):
                continue
            m = re.match(r"\s*(\d+)", row[0])
            if not m:
                continue
            rec = {c: row[j].strip() for j, c in enumerate(COLS)}
            rec["key"] = f"{chunk}-{int(m.group(1))}"
            out.append(rec)
    return out


def load(run_dir: str) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(run_dir, "chunk_*.tsv"))):
        rows += read_chunk_tsv(path)
    seen, uniq = set(), []
    for r in rows:
        if r["key"] not in seen:
            seen.add(r["key"])
            uniq.append(r)
    return uniq


def group_of(label: str, groups: dict[str, str]) -> str | None:
    return groups.get(label.strip().lower())


def unmapped(rows: list[dict], groups: dict[str, str]) -> Counter:
    return Counter(r["segment"].strip().lower() for r in rows if group_of(r["segment"], groups) is None)


def read_themes(path: str) -> list[tuple[str, str, str]]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) >= 3 and row[1].strip().lower() in KINDS:
                out.append((row[0].strip(), row[1].strip().lower(), row[2].strip()))
    return out


def summarise(rows: list[dict], groups: dict[str, str], themes: list[tuple[str, str, str]]) -> dict:
    """Counts by group and funnel, and theme counts (each lead counted once per theme), overall and by group."""
    gby = {r["key"]: group_of(r["segment"], groups) or "Unmapped" for r in rows}
    total = len(rows)
    heard = sum(1 for g in gby.values() if g != NOT_HEARD)
    by_group = Counter(gby.values())
    by_funnel: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_funnel[r["funnel"] or "n/a"][gby[r["key"]]] += 1
    seen = set()
    theme_count: dict[str, Counter] = defaultdict(Counter)
    theme_group: dict[tuple[str, str], Counter] = defaultdict(Counter)
    unknown = 0
    for key, kind, theme in themes:
        if key not in gby:
            unknown += 1
            continue
        if (key, kind, theme) in seen:
            continue
        seen.add((key, kind, theme))
        theme_count[kind][theme] += 1
        theme_group[(kind, theme)][gby[key]] += 1
    return {
        "total": total, "heard": heard, "by_group": dict(by_group.most_common()),
        "by_funnel": {f: dict(c.most_common()) for f, c in sorted(by_funnel.items(), key=lambda x: -sum(x[1].values()))},
        "themes": {k: dict(c.most_common()) for k, c in theme_count.items()},
        "theme_groups": {f"{k}|{t}": dict(c) for (k, t), c in theme_group.items()},
        "themes_unknown_leads": unknown,
    }


def write_lead_csv(rows: list[dict], groups: dict[str, str], path: str) -> str:
    """Lead-by-lead table for the appendix: no phone numbers, only the reading key."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "group"] + COLS[1:])
        for r in rows:
            w.writerow([r["key"], group_of(r["segment"], groups) or "Unmapped"] + [r[c] for c in COLS[1:]])
    return path


def save_summary(summary: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return path
