"""Learner-needs report: read 300–700 TimePay calls per bootcamp so trainers know who is coming.

Steps (guide: docs/learner_needs.md, skill: .claude/skills/learner-needs-report):
  sample     pull the campaign's call logs, pick the calls, fetch transcripts, write reading chunks
  aggregate  combine the readers' TSVs (after they finish) into counts; lists unmapped segment labels
  pdf        build the trainer PDF from summary.json + findings.json
  status     what exists for a bootcamp run

Run data (lead PII) stays in data/learner_needs/<KEY>/; the PDF and lead CSV go to exports/.

Usage: python -m analytics.learner_needs {sample|aggregate|pdf|status} <KEY> [--data data/learner_needs] [--out exports]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from analytics.learner_needs import aggregate as agg
from analytics.learner_needs import sample as smp

HERE = os.path.dirname(__file__)


def load_cfg(key: str) -> dict:
    path = os.path.join(HERE, "bootcamps", f"{key}.json")
    if not os.path.exists(path):
        sys.exit(f"No config {path}. Copy another bootcamp's json and fill it in.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def growthx_slugs(cfg: dict) -> dict[str, str]:
    """phone -> landing-page slug for the bootcamp's GrowthX leadtype (first capture wins)."""
    gx = cfg.get("growthx") or {}
    if not gx.get("leadtype"):
        return {}
    from integrations.growthx.client import GrowthXClient
    rows, _ = GrowthXClient().leads_for_ist_days(gx["from"], gx["to"], leadtype=gx["leadtype"])
    out: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r.get("captured_ist") or ""):
        p = smp.phone10(r.get("whatsapp") or r.get("phone"))
        if p and p not in out:
            out[p] = r.get("slug") or ""
    return out


def cmd_sample(cfg: dict, run: str) -> None:
    from integrations.timepay.client import TimePayClient
    c = TimePayClient()
    logs = []
    for cid in cfg["campaign_ids"]:
        for r in c.iter_logs(cfg.get("calls_from"), cfg.get("calls_to"), max_pages=None, type="call", campaign_id=cid):
            logs.append(r)
    calls = smp.longest_per_lead(logs, cfg.get("min_seconds", 60))
    slugs = growthx_slugs(cfg)
    for x in calls:
        x["slug"] = slugs.get(x["p"], "")
    picked = smp.stratified(calls, cfg.get("sample_size", 400), cfg.get("min_per_funnel", 8))
    print(f"{len(logs)} call logs, {len(calls)} leads with a {cfg.get('min_seconds', 60)}s+ call, {len(picked)} picked")

    def tx(x: dict) -> list[dict] | None:
        for _ in range(3):
            try:
                return (c.get(f"transcript/{x['tid']}") or {}).get("data")
            except Exception:  # noqa: BLE001 - retry, then read the call as empty
                continue
        return None

    with ThreadPoolExecutor(8) as ex:
        turns = list(ex.map(tx, picked))
    os.makedirs(run, exist_ok=True)
    idmap = {}
    for j, part in enumerate(smp.chunks(list(zip(picked, turns)), cfg.get("chunk_size", 130)), 1):
        name = f"chunk_{j:02d}"
        texts = []
        for i, (x, t) in enumerate(part, 1):
            texts.append(smp.format_call(i, x, t))
            idmap[f"{name}-{i}"] = x
        with open(os.path.join(run, f"{name}.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(texts) + "\n")
    with open(os.path.join(run, "idmap.json"), "w", encoding="utf-8") as f:
        json.dump(idmap, f, ensure_ascii=False)
    print(f"wrote {len(glob.glob(os.path.join(run, 'chunk_*.txt')))} chunks to {run}; "
          f"missing transcripts: {sum(1 for t in turns if not t)}")


def cmd_aggregate(cfg: dict, run: str, out: str, key: str) -> int:
    rows = agg.load(run)
    missing = [p for p in glob.glob(os.path.join(run, "chunk_*.txt"))
               if not os.path.exists(p[:-4] + ".tsv")]
    groups = {k.lower(): v for k, v in (cfg.get("groups") or {}).items()}
    um = agg.unmapped(rows, groups)
    themes = [t for p in sorted(glob.glob(os.path.join(run, "themes*.tsv"))) for t in agg.read_themes(p)]
    summary = agg.summarise(rows, groups, themes)
    agg.save_summary(summary, os.path.join(run, "summary.json"))
    os.makedirs(out, exist_ok=True)
    agg.write_lead_csv(rows, groups, os.path.join(out, f"Learner_needs_{key}_leads.csv"))
    print(f"{len(rows)} lead lines; chunks without a TSV: {len(missing)}")
    print("groups:", json.dumps(summary["by_group"], ensure_ascii=False))
    print("themes:", {k: len(v) for k, v in summary["themes"].items()} or "none yet (write themes.tsv)")
    if um:
        print(f"UNMAPPED segment labels ({sum(um.values())} leads) - add them to the config's groups:")
        for label, n in um.most_common():
            print(f"  {n:4d}  {label}")
        return 1
    return 0


def cmd_pdf(cfg: dict, run: str, out: str, key: str) -> None:
    from analytics.learner_needs.pdf import write_pdf
    with open(os.path.join(run, "summary.json"), encoding="utf-8") as f:
        summary = json.load(f)
    with open(os.path.join(run, "findings.json"), encoding="utf-8") as f:
        findings = json.load(f)
    groups = {k.lower(): v for k, v in (cfg.get("groups") or {}).items()}
    leads = [{**r, "group": agg.group_of(r["segment"], groups) or "Unmapped"} for r in agg.load(run)]
    pdf = os.path.join(out, f"Learner_needs_{key}.pdf")
    write_pdf(cfg, summary, findings, leads if cfg.get("appendix_leads", True) else None, pdf)
    print(pdf)


def cmd_status(run: str) -> None:
    txt = sorted(glob.glob(os.path.join(run, "chunk_*.txt")))
    tsv = [p for p in txt if os.path.exists(p[:-4] + ".tsv")]
    print(f"{run}: {len(txt)} chunks, {len(tsv)} read; themes files "
          f"{len(glob.glob(os.path.join(run, 'themes*.tsv')))}; findings.json "
          f"{'yes' if os.path.exists(os.path.join(run, 'findings.json')) else 'no'}")
    for p in txt:
        if p not in tsv:
            print("  to read:", p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sample", "aggregate", "pdf", "status"])
    ap.add_argument("key")
    ap.add_argument("--data", default="data/learner_needs")
    ap.add_argument("--out", default="exports")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.key)
    run = os.path.join(a.data, a.key)
    if a.cmd == "sample":
        from integrations import env  # noqa: F401 - loads .env
        cmd_sample(cfg, run)
    elif a.cmd == "aggregate":
        return cmd_aggregate(cfg, run, a.out, a.key)
    elif a.cmd == "pdf":
        cmd_pdf(cfg, run, a.out, a.key)
    else:
        cmd_status(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
