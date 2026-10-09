"""Storing analysis results, resumable, with retries.

Work is claimed from the registry's job table, so an interrupted run loses nothing and a re-run never analyses a
call twice at the same version. A reading that fails validation or comes back incomplete is retried with a growing
wait (5 min to 24 h); a transcript file that went missing is searched for again.

The analysis is Claude's reading of each transcript (reading.py: no API, read inside a Claude Code session),
including the word and phrase analysis (user, 9 Oct 2026: transcripts, and keyword analysis too, are analysed by
Claude only). Nothing here analyses a transcript itself; it validates and stores what Claude wrote.
"""

from __future__ import annotations

from datetime import datetime

from analytics.convintel import schema as S
from analytics.convintel.store import LAYER_VERSIONS, Registry, ts


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


def store_semantic(reg: Registry, call: dict, text: str, res: dict, engine: str, now: datetime) -> str:
    from analytics.convintel.validate import validate_semantic
    if res.get("output") is None:
        reg.fail(call["call_id"], S.SEMANTIC, res.get("error") or "no reading was returned", now, engine)
        return "failed"
    try:
        clean, missing, dropped = validate_semantic(res["output"], text)
    except Exception as e:
        reg.fail(call["call_id"], S.SEMANTIC, f"validation failed: {type(e).__name__}: {e}", now, engine)
        return "failed"
    state = reg.finish(call["call_id"], S.SEMANTIC, engine, clean, missing, dropped, now)
    save_findings(reg, call, S.SEMANTIC, clean.get("findings") or [], now)
    return state
