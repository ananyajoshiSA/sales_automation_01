"""Second reading of every compliance decision: a separate Claude reader re-checks each lead's compliance flags
against the full day's transcripts and the prompt's fixed bar, then ``apply`` writes the outcome into the results
(re-checked, so a changed flag still needs a verbatim quote)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from analytics.discovery_audit_module import reading

REVIEW = """# Compliance re-check

For each item in in.json, read the whole `transcript` and decide whether `compliance_flags` follows the audit
prompt's PROHIBITED LANGUAGE bar exactly (in INSTRUCTIONS.md one folder up): CRITICAL only for a specific earnings
figure or range presented as what the lead will or can (easily) earn, a job, client or paid work stated as certain,
a 100% guarantee of an outcome, fake scarcity, or a visa guarantee, spoken by the counsellor. Vague claims with no
figure, figures the lead says, and support language are NOT flagged.

Write out_NN.json (same NN as your in_NN.json): a list with one object per item:
{"lead_key": "...", "verdict": "keep" or "change", "compliance_flags": "the corrected value (same as before if keep): NONE, or CRITICAL: <type> (<quote>) items joined by ' | '",
 "pitch_discipline": "the corrected Pitch Discipline cell (same as before if keep)", "reason": "one sentence"}
Quotes must be word for word from the transcript, in straight double quotes. Missed a flag? Add it. Flag not met?
Remove it and say so in pitch_discipline as risky framing if relevant.
"""


PER_PART = 14  # leads per reviewer: a reviewer re-reads every transcript in its part


def prepare(entries: list[dict], folder: str) -> list[str]:
    """Writes review/in_NN.json parts (leads not yet re-checked). Each reviewer writes the matching out_NN.json."""
    os.makedirs(f"{folder}/review", exist_ok=True)
    items = []
    for e in entries:
        p = f"{folder}/results/{reading.lead_key(e)}.json"
        if reading.finished(p):
            r = json.load(open(p))
            if "review" not in r:
                items.append({"lead_key": r["lead_key"], "compliance_flags": r["compliance_flags"],
                              "pitch_discipline": r["sheet"]["Pitch Discipline"], "transcript": reading.day_text(e)})
    Path(f"{folder}/review/REVIEW.md").write_text(REVIEW)
    # Later rounds (late transcripts, carried-over leads) number on, so an earlier round's out file is never overwritten.
    start = max((int(f.stem[3:]) for f in Path(f"{folder}/review").glob("in_*.json")), default=0)
    paths = []
    for i in range(0, len(items), PER_PART):
        paths.append(f"{folder}/review/in_{start + i // PER_PART + 1:02d}.json")
        json.dump(items[i:i + PER_PART], open(paths[-1], "w"), indent=1, ensure_ascii=False)
    return paths


def apply(entries: list[dict], folder: str) -> dict:
    """Writes each verdict into its result. Returns {"kept", "changed", "failed": [(lead_key, problems)]}."""
    out = [v for f in sorted(Path(f"{folder}/review").glob("out_*.json")) for v in json.load(open(f))]
    by_key = {reading.lead_key(e): e for e in entries}
    done = {"kept": 0, "changed": 0, "failed": []}
    for v in out:
        p = f"{folder}/results/{v['lead_key']}.json"
        r = json.load(open(p))
        if "review" in r:
            continue
        old = r["compliance_flags"]
        if v["verdict"] == "change":
            flags = v["compliance_flags"].strip()
            r["compliance_flags"] = "NONE" if flags.upper() in ("NONE", "") else flags
            r["sheet"]["Pitch Discipline"] = v["pitch_discipline"]
        r["review"] = {"verdict": v["verdict"], "reason": v.get("reason", ""), "before": old}
        json.dump(r, open(p, "w"), indent=1, ensure_ascii=False)
        bad = reading.check(p, reading.day_text(by_key[v["lead_key"]]))
        if bad:
            done["failed"].append((v["lead_key"], bad))
        else:
            done["changed" if v["verdict"] == "change" else "kept"] += 1
    return done
