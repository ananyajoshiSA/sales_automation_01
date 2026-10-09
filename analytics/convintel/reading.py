"""Claude reading: Claude itself reads every transcript inside a Claude Code session working in this repository.
There is no API key and no API call to any model (user, 9 Oct 2026: "make use of the claude models only for
transcripts, no api is to be used from any llm. Transcripts are to be processed, analysed from the llm's models
only").

A reading round has three steps:

1. ``read prepare`` takes the calls whose transcript is found and still needs a reading and writes a round folder
   under data/convintel/reading/<round>/ (git-ignored): INSTRUCTIONS.md (how to read a call and the exact JSON
   shape, schema.SEMANTIC_SCHEMA) and packs of a few calls each. A pack holds, per call, its id and what Claude
   reads: the IST start, direction, talk time, call class and the transcript (prompt.user_message), never the lead
   number, lead id or anyone's name. The calls are marked as handed out, so no other round takes them; a call not
   read within 25 hours is handed out again by a later round.
2. A Claude Code session reads each pack and writes one file per call, results/<call_id>.json, and checks it with
   ``read check`` until every excerpt is verbatim and every part is present. A passing check writes
   results/<call_id>.ok (the file's SHA-256), which marks the reading as finished.
3. ``read collect`` stores every finished reading (the file still matches its .ok mark) of a call that is still
   handed out, from whichever round's folder holds it, after validating it again against the transcript. A draft,
   a half-written file or a file changed since its check is left alone until it passes. ``read prepare`` collects
   first, so a finished reading is never handed out again.

    python -m analytics.convintel read prepare [--limit N] [--per-pack 6]
    python -m analytics.convintel read check data/convintel/reading/ROUND/results/CALL_ID.json
    python -m analytics.convintel read collect [ROUND]
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime

from analytics.convintel import schema as S
from analytics.convintel.analyze import store_semantic
from analytics.convintel.fetch import read_text
from analytics.convintel.prompt import SYSTEM, user_message
from analytics.convintel.store import Registry, ts
from integrations.timeutil import IST

READING_DIR = os.path.join("data", "convintel", "reading")
PER_PACK = 6
ENGINE = "claude-code"           # Claude reading in a Claude Code session, no API

INSTRUCTIONS = """# Reading round {round}

You are Claude, reading recorded sales-call transcripts for the conversation-intelligence module of this
repository. No API is involved: you read each transcript yourself and write your reading as a JSON file.

Privacy: the packs hold real customer conversations. Keep them in this folder. Don't copy transcript text
anywhere else, and don't quote it in chat replies, logs or commit messages.

For each call in your pack file (pack_NNN.json, a list of calls with "call_id" and "message"):

1. Read the message: the dialer facts and the transcript between the <transcript> tags.
2. Write your reading of that call to `results/<call_id>.json` in this folder: one JSON object that follows the
   JSON Schema at the end of this file exactly. Every property must be present, with no extra properties, and
   every label must come from its list.
3. From the repository root, run
   `.venv/bin/python -m analytics.convintel read check <path to that results file>`.
   Fix whatever it reports (an excerpt that is not in the transcript word for word, a missing or malformed part,
   a value out of range) and run the check again until it prints OK. Only a reading whose last check printed OK
   is stored, so check again after every edit.

Every excerpt must be copied character for character from the transcript (a short span, up to about 200
characters). Never translate, paraphrase or tidy an excerpt; if no exact span supports a point, use "" for its
excerpt and confidence "low". The transcripts are machine transcriptions without speaker labels, so infer who is
speaking and say so (speaker "caller", "customer" or "unclear"; speaker_turns.inferred true).

## How to read a call

{system}

## The JSON shape (JSON Schema)

```json
{schema}
```
"""


def round_id(now: datetime) -> str:
    return now.astimezone(IST).strftime("r%Y%m%d-%H%M%S")


def instructions(rid: str) -> str:
    return INSTRUCTIONS.format(round=rid, system=SYSTEM.strip(), schema=json.dumps(S.SEMANTIC_SCHEMA, indent=1))


def _write_json(path: str, data) -> None:
    """Written to a temporary file and renamed, so a reader never sees half a file."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _sha(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _mark(path: str) -> str:
    return os.path.splitext(path)[0] + ".ok"


def finished(path: str) -> bool:
    """The reading passed ``read check`` and has not changed since."""
    try:
        with open(_mark(path), encoding="utf-8") as fh:
            return fh.read().strip() == _sha(path)
    except OSError:
        return False


def prepare(reg: Registry, now: datetime, limit: int | None = None, per_pack: int = PER_PACK,
            base: str = READING_DIR, call_ids=None, log=lambda *a: None) -> dict:
    """Store the readings finished so far, then hand the calls that still need a reading to a new round: writes its
    folder and marks the calls as handed out. ``limit`` 0 hands out nothing."""
    collected = collect(reg, now, base=base, log=log)
    rows = reg.claim(S.SEMANTIC, 10 ** 9 if limit is None else limit, now, call_ids) if limit != 0 else []
    items = []
    for c in rows:
        text = read_text(c)
        if text is None:
            reg.lost_transcript(c["call_id"], now)     # searched for again, then read in a later round
        else:
            items.append((c, text))
    out = {"collected": dict(collected), "round": None, "folder": None, "calls": len(items), "packs": 0,
           "transcript_missing": len(rows) - len(items)}
    if not items:
        return out
    rid, n = round_id(now), 1
    while os.path.exists(os.path.join(base, rid if n == 1 else f"{rid}-{n}")):    # two rounds in one second
        n += 1
    rid = rid if n == 1 else f"{rid}-{n}"
    folder = os.path.join(base, rid)
    os.makedirs(os.path.join(folder, "results"))
    with open(os.path.join(folder, "INSTRUCTIONS.md"), "w", encoding="utf-8") as fh:
        fh.write(instructions(rid))
    size = max(1, per_pack)
    packs = [items[i:i + size] for i in range(0, len(items), size)]
    for k, pack in enumerate(packs, 1):
        _write_json(os.path.join(folder, f"pack_{k:03d}.json"),
                    {"round": rid, "pack": k, "calls": [{"call_id": c["call_id"], "message": user_message(c, text)}
                                                        for c, text in pack]})
    ids = [c["call_id"] for c, _ in items]
    _write_json(os.path.join(folder, "manifest.json"), {"round": rid, "created_utc": ts(now), "calls": ids,
                                                        "packs": len(packs)})
    reg.mark_batched(ids, S.SEMANTIC, rid, now)
    out.update(round=rid, folder=folder, packs=len(packs))
    log(f"reading round {rid}: {len(ids)} calls in {len(packs)} packs under {folder}")
    return out


def _load(path: str) -> tuple[dict | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return None, f"the reading is not readable JSON ({type(e).__name__})"
    return (data, None) if isinstance(data, dict) else (None, "the reading is not a JSON object")


def check(reg: Registry, path: str) -> dict:
    """Validate one results file against its call's transcript, without storing anything. A pass writes the file's
    .ok mark (the reading is finished); a failure removes it."""
    res = _check(reg, path)
    try:
        if res["ok"]:
            with open(_mark(path), "w", encoding="utf-8") as fh:
                fh.write(_sha(path) or "")
        elif os.path.exists(_mark(path)):
            os.remove(_mark(path))
    except OSError as e:
        res["ok"] = False
        res["problems"].append(f"could not write the finished mark ({type(e).__name__})")
    return res


def _check(reg: Registry, path: str) -> dict:
    from analytics.convintel.validate import validate_semantic
    cid = os.path.splitext(os.path.basename(path))[0]
    c = reg.call(cid)
    if c is None:
        return {"ok": False, "callId": cid, "problems": ["no call with this id in the registry (name the file <call_id>.json)"]}
    text = read_text(c)
    if text is None:
        return {"ok": False, "callId": cid, "problems": ["this call's transcript file is missing or changed"]}
    data, err = _load(path)
    if err:
        return {"ok": False, "callId": cid, "problems": [err]}
    clean, missing, dropped = validate_semantic(data, text)
    v = clean["_validation"]
    problems = ([f"missing or malformed part: {m}" for m in missing] + [f"malformed: {m}" for m in v["malformed"]]
                + ([f"{dropped} excerpt(s) are not in the transcript word for word"] if dropped else [])
                + [f"fixed on storing: {x}" for x in v["range_fixes"] + v["value_fixes"]])
    return {"ok": not missing and not dropped, "callId": cid, "excerptsChecked": v["excerpts_checked"],
            "excerptsDropped": dropped, "problems": problems}


def rounds(base: str = READING_DIR) -> list[str]:
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, d) for d in os.listdir(base) if os.path.exists(os.path.join(base, d, "manifest.json")))


def collect(reg: Registry, now: datetime, folder: str | None = None, base: str = READING_DIR,
            engine: str = ENGINE, log=lambda *a: None) -> Counter:
    """Store every finished reading of a call that is still handed out, looking in one round's folder or in all of
    them (the call's own round first). A reading that is missing, unfinished or changed since its check stays
    waiting; the call is handed out again only once its round is 25 hours old."""
    out = Counter()
    found: dict[str, list[str]] = {}
    for f in [folder] if folder else rounds(base):
        res = os.path.join(f, "results")
        for name in sorted(os.listdir(res)) if os.path.isdir(res) else []:
            if name.endswith(".json"):
                found.setdefault(name[:-5], []).append(os.path.join(res, name))
    waiting = {r["call_id"]: r["batch_id"] for r in reg.q(
        "SELECT call_id, batch_id FROM conversation_analysis_jobs WHERE layer = ? AND state = 'batched'", (S.SEMANTIC,))}
    for cid in sorted(waiting):
        paths = sorted(found.get(cid, []), key=lambda p: os.path.basename(os.path.dirname(os.path.dirname(p))) != waiting[cid])
        if not paths:
            out["not_read_yet"] += 1
            continue
        path = next((p for p in paths if finished(p)), None)
        if path is None:
            out["not_checked_yet"] += 1
            continue
        c = reg.call(cid)
        text = read_text(c) if c else None
        if text is None:
            reg.lost_transcript(cid, now)
            out["transcript_missing"] += 1
            continue
        data, err = _load(path)
        if err:
            reg.fail(cid, S.SEMANTIC, err, now, engine)
            out["failed"] += 1
            continue
        out[store_semantic(reg, c, text, {"output": data}, engine, now)] += 1
    log(f"collected readings: {dict(out)}")
    return out
