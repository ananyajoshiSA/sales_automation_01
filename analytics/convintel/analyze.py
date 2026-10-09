"""Analysis runner: every found transcript through every required layer, resumable, with retries.

Work is claimed from the registry's job table, so an interrupted run loses nothing (its claims are taken back
after 30 minutes) and a re-run never analyses a call twice at the same version. A layer that fails or returns an
incomplete result is retried with a growing wait (5 min to 24 h); a transcript file that went missing is searched
for again. The keyword layer is free and runs on every transcript. The semantic layer needs model access: without
it every transcript stays ANALYSIS_INCOMPLETE with the reason shown, and nothing is spent. With access it runs
only up to the ``--limit`` the user gives, either now (``--mode sync``) or through the Batches API at half the
price (``--mode batch``, results collected by ``poll-batches``).

    python -m analytics.convintel analyze [--layer keyword|semantic|all] [--limit N] [--mode sync|batch] [--workers 4]
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from analytics.convintel import schema as S
from analytics.convintel.fetch import read_text
from analytics.convintel.store import LAYER_VERSIONS, Registry, ts

KEYWORD_BATCH = 2000
BATCH_CALLS = 5000        # the Batches API takes 256 MB per batch: about 8,000 of these requests
REFUSAL_RETRIES = 1       # a transcript the model declined twice is not retried automatically


def save_findings(reg: Registry, call: dict, layer: str, findings: list[dict], now: datetime) -> None:
    """The layer's evidence-backed findings for one call, replacing any earlier ones for that call and layer."""
    rows = [{"finding_id": f"{call['call_id']}:{layer}:{i}", "call_id": call["call_id"], "lead_id": call.get("lead_id"),
             "caller_id": call.get("caller_id"), "caller_name": call.get("caller_name"), "team": call.get("team"),
             "ist_day": call.get("ist_day"), "layer": layer, "version": LAYER_VERSIONS[layer], "category": f.get("category"),
             "excerpt": f.get("excerpt") or None, "offset": f.get("offset"), "speaker": f.get("speaker") or "not labelled",
             "confidence": f.get("confidence"), "reasoning": f.get("reasoning"),
             "recommended_action": f.get("recommended_action"), "created_utc": ts(now)}
            for i, f in enumerate(findings)]
    reg.replace_rows("conversation_quality_findings", rows, "call_id = ? AND layer = ?", (call["call_id"], layer))


def _text_or_requeue(reg: Registry, call: dict, now: datetime) -> str | None:
    text = read_text(call)
    if text is None:
        reg.lost_transcript(call["call_id"], now)
    return text


def keyword_pass(reg: Registry, now: datetime, limit: int | None = None, call_ids=None, log=lambda *a: None) -> Counter:
    from analytics.convintel import rules
    out = Counter()
    while limit is None or out["claimed"] < limit:
        rows = reg.claim(S.KEYWORD, min(KEYWORD_BATCH, (limit - out["claimed"]) if limit else KEYWORD_BATCH), now, call_ids)
        if not rows:
            break
        out["claimed"] += len(rows)
        for c in rows:
            text = _text_or_requeue(reg, c, now)
            if text is None:
                out["transcript_missing"] += 1
                continue
            try:
                result = rules.analyze_keywords(text, c.get("duration_s"))
                missing = rules.missing_components(result)
            except Exception as e:  # one bad transcript must not stop the run; it is retried later
                reg.fail(c["call_id"], S.KEYWORD, f"{type(e).__name__}: {e}", now, rules.ENGINE)
                out["failed"] += 1
                continue
            out[reg.finish(c["call_id"], S.KEYWORD, rules.ENGINE, result, missing, 0, now)] += 1
            save_findings(reg, c, S.KEYWORD, result.get("findings") or [], now)
        log(f"keyword layer: {dict(out)}")
    return out


def _block(reg: Registry, layer: str, why: str | None, now: datetime) -> None:
    """Record (or clear) why a layer can't run, so every waiting call's status says so."""
    blocked = reg.get_meta("blocked_layers", {})
    if blocked.get(layer) == why or (why is None and layer not in blocked):
        return
    if why is None:
        blocked.pop(layer, None)
    else:
        blocked[layer] = why
    reg.set_meta("blocked_layers", blocked)
    reg.refresh(None, now)


def store_semantic(reg: Registry, call: dict, text: str, res: dict, engine: str, now: datetime) -> str:
    from analytics.convintel.validate import validate_semantic
    if res.get("output") is None:
        refused = res.get("stop_reason") == "refusal"
        tries = (reg.jobs(call["call_id"]).get(S.SEMANTIC) or {}).get("attempts") or 0
        reg.fail(call["call_id"], S.SEMANTIC, res.get("error") or "the model returned no result", now, engine,
                 retry=not (refused and tries >= REFUSAL_RETRIES))
        return "failed"
    try:
        clean, missing, dropped = validate_semantic(res["output"], text)
    except Exception as e:
        reg.fail(call["call_id"], S.SEMANTIC, f"validation failed: {type(e).__name__}: {e}", now, engine)
        return "failed"
    state = reg.finish(call["call_id"], S.SEMANTIC, engine, clean, missing, dropped, now)
    save_findings(reg, call, S.SEMANTIC, clean.get("findings") or [], now)
    return state


def semantic_pass(reg: Registry, now: datetime, limit: int, engine=None, mode: str = "sync", workers: int = 4,
                  call_ids=None, log=lambda *a: None) -> Counter:
    """Up to ``limit`` calls through the model. Never runs (and never spends) without access and a limit."""
    if engine is None:
        from analytics.convintel.llm import SemanticEngine
        engine = SemanticEngine()
    ok, why = engine.ready()
    _block(reg, S.SEMANTIC, None if ok else f"no model access yet ({why})", now)
    if not ok:
        log(f"semantic layer not run: {why}")
        return Counter(blocked=1)
    if not limit:
        return Counter()
    rows = reg.claim(S.SEMANTIC, limit, now, call_ids)
    items = []
    out = Counter(claimed=len(rows))
    for c in rows:
        text = _text_or_requeue(reg, c, now)
        if text is None:
            out["transcript_missing"] += 1
        else:
            items.append((c, text))
    if not items:
        return out
    if mode == "batch":
        for i in range(0, len(items), BATCH_CALLS):     # each request repeats the prompt and schema (~31 KB)
            chunk = items[i:i + BATCH_CALLS]
            try:
                batch_id = engine.submit_batch([(c["call_id"], c, text) for c, text in chunk])
            except Exception as e:
                reg.release([c["call_id"] for c, _ in items[i:]], S.SEMANTIC, now)
                log(f"batch not sent ({e}); {len(items) - i} calls stay queued")
                out["not_sent"] += len(items) - i
                break
            reg.mark_batched([c["call_id"] for c, _ in chunk], S.SEMANTIC, batch_id, now)
            out["batched"] += len(chunk)
            log(f"sent {len(chunk)} calls to the Batches API ({batch_id}); collect with poll-batches")
        return out
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:   # registry writes stay on this thread
        for (c, text), res in zip(items, pool.map(lambda it: engine.analyze(it[0], it[1]), items)):
            out[store_semantic(reg, c, text, res, engine.engine, now)] += 1
    log(f"semantic layer: {dict(out)}")
    return out


def poll_batches(reg: Registry, now: datetime, engine=None, log=lambda *a: None) -> Counter:
    """Store the results of every finished batch; calls a batch did not return are retried later."""
    if engine is None:
        from analytics.convintel.llm import SemanticEngine
        engine = SemanticEngine()
    out = Counter()
    for batch_id, ids in reg.open_batches(S.SEMANTIC).items():
        if engine.batch_state(batch_id) != "ended":
            out["batches_running"] += 1
            continue
        wanted, seen = set(ids), set()
        for cid, res in engine.batch_results(batch_id):
            if cid not in wanted or cid in seen:
                continue
            seen.add(cid)
            c = reg.call(cid)
            text = read_text(c) if c else None
            if text is None:
                reg.lost_transcript(cid, now)
                out["transcript_missing"] += 1
                continue
            out[store_semantic(reg, c, text, res, engine.engine, now)] += 1
        for cid in wanted - seen:
            reg.fail(cid, S.SEMANTIC, f"not in the results of batch {batch_id}", now, engine.engine)
            out["missing_from_batch"] += 1
        out["batches_done"] += 1
    log(f"batches: {dict(out)}")
    return out
