"""The semantic layer's engine: Claude reads one whole call transcript and returns schema.SEMANTIC_SCHEMA.

Request shape from the Claude API skill docs (python/claude-api/README.md, batches.md, streaming.md; shared/models.md,
prompt-caching.md, tool-use-concepts.md "Structured Outputs", model-migration.md "Migrating to Claude Opus 5.5"):

* Model ``claude-opus-5-5`` unless ``CONVINTEL_MODEL`` says otherwise. Its thinking is adaptive and always on;
  ``output_config.effort`` sets how much it thinks, and ``max_tokens`` covers the thinking plus the reply.
* Structured outputs (``output_config.format``, type ``json_schema``) with SEMANTIC_SCHEMA, which uses only the
  documented constructs: closed objects (``additionalProperties: false``), enums, nullable integers via
  ``anyOf``, no numeric or length bounds. The reply is one JSON text block after the thinking blocks.
  validate.py still checks ranges, labels and excerpts.
* Prompt caching: the fixed system prompt carries the cache breakpoint (5-minute TTL; it is well over the
  512-token minimum), so every call after the first in a run reads it at the cache price.
* ``stop_reason`` "refusal": the partial reply is discarded and an error returned. "max_tokens": the reply is
  incomplete; ``analyze`` asks again with double the limit, up to 64,000 tokens (streamed, as the docs advise
  for long replies).
* The Message Batches API (half price, results within 24 h) for backlogs; custom_id is the call id. Batch
  requests are not streamed and can't be re-asked with a higher limit, so they get the 64,000-token ceiling
  from the start (max_tokens is a ceiling; only the tokens actually produced are billed).

``analyze`` never raises on API or parse problems: it returns ``{"output": None, "error": ...}`` and the runner
retries later. The SDK reads ANTHROPIC_API_KEY (loaded from the repo's .env) itself; the key is never printed,
logged or kept here. The ``anthropic`` package is imported only when a request is actually sent.
Cost before spending: ``python -m analytics.convintel estimate [FROM TO] [--batch]``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
from typing import Iterator

from analytics.convintel.prompt import SYSTEM, user_message
from analytics.convintel.schema import SEMANTIC_SCHEMA
from integrations.env import load_dotenv

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
STREAM_ABOVE = 16_000       # the docs' non-streaming examples stop here; longer replies are streamed
MAX_TOKENS_CAP = 64_000     # each retry after a cut-off reply doubles max_tokens, up to this; batches use it
MAX_RETRIES = 4             # SDK-level retries of connection errors, 429 and 5xx (default 2)
BATCH_MAX_REQUESTS, BATCH_MAX_BYTES = 100_000, 256_000_000   # per batch (python/claude-api/batches.md)

# USD per million tokens. Source: the Claude API skill docs bundled with Claude Code 2.1.295 (claude-api/shared/
# models.md and claude-api/shared/model-migration.md "Migrating to Claude Opus 5.5 > Pricing", read 9 Oct 2026).
# Check https://platform.claude.com/docs/en/about-claude/pricing before budgeting. A model not listed here has no
# documented price, so its cost is not estimated.
PRICE_SOURCE = ("Claude API skill docs bundled with Claude Code 2.1.295: claude-api/shared/models.md, "
                "claude-api/shared/model-migration.md, claude-api/shared/prompt-caching.md, "
                "claude-api/python/claude-api/batches.md (read 9 Oct 2026)")
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},      # prompts of 100K tokens or fewer (ours are)
    "claude-fable-5-1": {"input": 10.00, "output": 50.00, "cache_read": 0.25},
}
CACHE_WRITE_X = 1.25        # 5-minute cache write, x input price (shared/prompt-caching.md)
CACHE_READ_X = 0.10         # cache read, x input price, where a model's own rate isn't listed (same source)
BATCH_X = 0.50              # Batches API: 50% of standard prices on all token usage (batches.md)

# Token assumptions for estimate_cost, (low, high). These are NOT from the docs except where said: replace them
# with measured usage (analyze() returns it) after the first few calls.
TOKENS_PER_WORD = (1.3, 1.7)    # ~1.3 for English on older tokenizers; current models count ~30% more (docs)
CHARS_PER_TOKEN = 4             # rough size of the JSON schema if the API bills it as input (not documented)
REPLY_TOKENS = (2_000, 5_000)   # the JSON reply: 14 parts, nine scored dimensions, findings with excerpts
THINKING_TOKENS = (1_000, 6_000)  # adaptive thinking at effort "medium"; counted in max_tokens, priced as output

_USAGE = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _sdk_installed() -> bool:
    return importlib.util.find_spec("anthropic") is not None


def _model(model: str | None) -> str:
    load_dotenv()
    return model or os.environ.get("CONVINTEL_MODEL") or DEFAULT_MODEL


def _usage(msg) -> dict:
    u = getattr(msg, "usage", None)
    return {k: int(getattr(u, k, 0) or 0) for k in _USAGE}


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in _USAGE}


def _fail(error: str, stop_reason: str | None = None, usage: dict | None = None) -> dict:
    return {"output": None, "error": error, "stop_reason": stop_reason, "usage": usage or {}}


def parse_reply(msg, max_tokens: int) -> dict:
    """A Messages API response -> {"output", "error", "stop_reason", "usage"}; the stop reason is checked before
    the content is read, and reply text never goes into an error message."""
    stop, usage = getattr(msg, "stop_reason", None), _usage(msg)
    if stop == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        return _fail(f"the model declined to analyse this call (refusal{f', category {cat}' if cat else ''}); "
                     "its partial reply was discarded", stop, usage)
    if stop == "max_tokens":
        return _fail(f"the reply was cut off at the {max_tokens}-token limit; retry with a higher max_tokens",
                     stop, usage)
    text = "".join(b.text for b in getattr(msg, "content", None) or [] if getattr(b, "type", None) == "text")
    if not text.strip():
        return _fail(f"the model returned no text (stop reason {stop})", stop, usage)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return _fail(f"the reply was not valid JSON ({e.msg} at character {e.pos} of {len(text)})", stop, usage)
    if not isinstance(data, dict):
        return _fail("the reply was JSON but not an object", stop, usage)
    return {"output": data, "error": None, "stop_reason": stop, "usage": usage}


def _batch_error(res) -> str:
    err = getattr(res, "error", None)
    inner = getattr(err, "error", None) or err
    kind = str(getattr(inner, "type", None) or "unknown")
    what = getattr(inner, "message", None)
    advice = "the request needs fixing" if "invalid_request" in kind else "safe to retry"
    return f"the batch request failed ({kind}{f': {str(what)[:200]}' if what else ''}; {advice})"


class SemanticEngine:
    """Claude as the semantic layer. ``client`` is injected in tests; otherwise the SDK client is made on first use."""

    def __init__(self, client=None, model: str | None = None, effort: str = "medium", max_tokens: int = 16000):
        if effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        self.model = _model(model)
        self.engine = f"claude:{self.model}"
        self.effort, self.max_tokens = effort, max_tokens
        self.batch_max_tokens = max(max_tokens, MAX_TOKENS_CAP)
        self.last_error: str | None = None   # the latest error batch_state() turned into "in_progress"
        self._client_obj, self._injected, self._lock = client, client is not None, threading.Lock()

    def ready(self) -> tuple[bool, str]:
        """(True, "") when requests can be sent; else (False, why) in plain words."""
        if self._injected:
            return True, ""
        load_dotenv()
        why = []
        if not os.environ.get("ANTHROPIC_API_KEY"):
            why.append("ANTHROPIC_API_KEY is not set (add it to the .env file in the repo root)")
        if not _sdk_installed():
            why.append("the anthropic package is not installed (pip install anthropic)")
        return not why, "; ".join(why)

    def _client(self):
        with self._lock:
            if self._client_obj is None:
                import anthropic   # optional dependency, needed only once requests are sent
                self._client_obj = anthropic.Anthropic(max_retries=MAX_RETRIES)
        return self._client_obj

    def params(self, call: dict, transcript: str, max_tokens: int | None = None) -> dict:
        """One Messages API request. Everything before the user message is identical for every call, so it caches."""
        return {"model": self.model, "max_tokens": max_tokens or self.max_tokens,
                "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": self.effort,
                                  "format": {"type": "json_schema", "schema": SEMANTIC_SCHEMA}},
                "messages": [{"role": "user", "content": user_message(call, transcript)}]}

    def _send(self, params: dict):
        api = self._client().messages
        if params["max_tokens"] > STREAM_ABOVE:
            with api.stream(**params) as stream:
                return stream.get_final_message()
        return api.create(**params)

    def _error(self, e: Exception) -> str:
        msg = f"{type(e).__name__}: {e}"
        key = os.environ.get("ANTHROPIC_API_KEY")
        return f"model request failed ({(msg.replace(key, '[key]') if key else msg)[:300]})"

    def analyze(self, call: dict, transcript: str) -> dict:
        """{"output": raw dict | None, "error": str | None, "stop_reason": str | None, "usage": token counts}."""
        try:   # ready() reads .env, which can fail too; the runner relies on this never raising
            ok, why = self.ready()
        except Exception as e:
            return _fail(f"model not available: {type(e).__name__}")
        if not ok:
            return _fail(f"model not available: {why}")
        usage, limit = {}, self.max_tokens
        while True:
            try:
                res = parse_reply(self._send(self.params(call, transcript, limit)), limit)
            except Exception as e:   # API, network or SDK errors: reported, retried later by the runner
                return _fail(self._error(e), usage=usage)
            usage = _add(usage, res["usage"])
            if res["stop_reason"] != "max_tokens" or limit >= MAX_TOKENS_CAP:
                return {**res, "usage": usage}
            limit = min(limit * 2, MAX_TOKENS_CAP)

    def submit_batch(self, items: list[tuple[str, dict, str]]) -> str:
        """Send (call_id, call, transcript) items as one batch; returns the batch id. Raises before sending when the
        engine can't run or the batch breaks the API's limits, so nothing is half-sent."""
        ok, why = self.ready()
        if not ok:
            raise RuntimeError(f"model not available: {why}")
        ids = [cid for cid, _, _ in items]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("a batch needs at least one call and each call id once")
        requests = [{"custom_id": cid, "params": self.params(call, text, self.batch_max_tokens)}
                    for cid, call, text in items]
        size = sum(len(json.dumps(r, ensure_ascii=False).encode()) for r in requests)
        if len(requests) > BATCH_MAX_REQUESTS or size > BATCH_MAX_BYTES:
            fit = min(BATCH_MAX_REQUESTS, int(len(requests) * BATCH_MAX_BYTES / size))
            raise ValueError(f"{len(requests)} calls make a {size / 1e6:.0f} MB batch; the Batches API takes at most "
                             f"{BATCH_MAX_REQUESTS:,} requests or {BATCH_MAX_BYTES // 1_000_000} MB, so send about "
                             f"{fit:,} calls at a time")
        return self._client().messages.batches.create(requests=requests).id

    def batch_state(self, batch_id: str) -> str:
        """"in_progress", "canceling" or "ended". A failed status check reads as "in_progress" (the error is kept
        in ``last_error``), so the batch is simply checked again on the next poll."""
        try:
            return self._client().messages.batches.retrieve(batch_id).processing_status
        except Exception as e:
            self.last_error = self._error(e)
            return "in_progress"

    def batch_results(self, batch_id: str) -> Iterator[tuple[str, dict]]:
        """(call_id, result like analyze's) for every request in an ended batch. A network error while reading
        propagates: results stay available for 29 days, so the next poll reads them again rather than paying for
        the calls twice."""
        for r in self._client().messages.batches.results(batch_id):
            res = r.result
            kind = getattr(res, "type", None)
            if kind == "succeeded":
                yield r.custom_id, parse_reply(res.message, self.batch_max_tokens)
            elif kind == "errored":
                yield r.custom_id, _fail(_batch_error(res))
            elif kind == "canceled":
                yield r.custom_id, _fail("the batch was canceled before this call was analysed")
            elif kind == "expired":
                yield r.custom_id, _fail("the batch expired (24 h) before this call was analysed; send it again")
            else:
                yield r.custom_id, _fail(f"unexpected batch result type {kind!r}")


def _cost(tokens: dict, price: dict, batch: bool) -> float:
    """USD for {"input", "cache_write", "cache_read", "output"} token counts at ``price`` (per million tokens)."""
    inp = price["input"]
    read = price.get("cache_read", inp * CACHE_READ_X)
    usd = (tokens["input"] * inp + tokens["cache_write"] * inp * CACHE_WRITE_X + tokens["cache_read"] * read
           + tokens["output"] * price["output"]) / 1e6
    return usd * (BATCH_X if batch else 1)


def estimate_cost(calls: int, avg_words: float, model: str | None = None, batch: bool = False) -> dict:
    """An ESTIMATE (a low-high range, never a quote) of what sending ``calls`` transcripts of ``avg_words`` words
    to the model would cost. Prices come from PRICES (see PRICE_SOURCE); a model without a documented price gets
    cost None with the reason. Token figures are assumptions, listed in the result."""
    model = _model(model)
    calls, avg_words = max(0, int(calls or 0)), max(0.0, float(avg_words or 0))
    system_words = len(SYSTEM.split())
    header_words = len(user_message({}, "").split())
    schema_tokens = round(len(json.dumps(SEMANTIC_SCHEMA, separators=(",", ":"))) / CHARS_PER_TOKEN)
    lo_tpw, hi_tpw = TOKENS_PER_WORD
    system = (round(system_words * lo_tpw), round(system_words * hi_tpw))
    varying = (round((header_words + avg_words) * lo_tpw), round((header_words + avg_words) * hi_tpw))
    output = (REPLY_TOKENS[0] + THINKING_TOKENS[0], REPLY_TOKENS[1] + THINKING_TOKENS[1])
    # Low: the system prompt is written to the cache once and read by every later call; the schema costs nothing.
    # High: every call writes the cache (no hits, e.g. batch requests run far apart) and the schema is billed.
    low = {"input": calls * varying[0], "cache_write": system[0] if calls else 0,
           "cache_read": max(calls - 1, 0) * system[0], "output": calls * output[0]}
    high = {"input": calls * (varying[1] + schema_tokens), "cache_write": calls * system[1], "cache_read": 0,
            "output": calls * output[1]}
    price = PRICES.get(model)
    out = {
        "estimate": True,
        "label": "ESTIMATE ONLY, not a quote: token counts below are assumptions; real usage is returned with "
                 "every analysed call and should replace them",
        "model": model, "mode": "Batches API (half price, results within 24 h)" if batch else "one call at a time",
        "calls": calls, "avg_words": round(avg_words, 1),
        "assumptions": {
            "source": "rules of thumb, not measured: only the ~30% tokenizer increase, the prices and the cache and "
                      "batch multipliers come from the docs (price_source); reply and thinking sizes are guesses",
            "tokens_per_word": list(TOKENS_PER_WORD),
            "tokens_per_word_basis": "about 1.3 for English on older tokenizers; the docs say current models count "
                                     "about 30% more tokens for the same text, hence up to 1.7; romanized Hindi may "
                                     "be higher still",
            "system_prompt_words": system_words, "system_prompt_tokens": list(system),
            "schema_tokens_if_billed": schema_tokens, "header_words": header_words,
            "reply_tokens": list(REPLY_TOKENS), "thinking_tokens": list(THINKING_TOKENS),
            "caching": "low end: system prompt read from the cache after the first call; high end: written every call",
        },
        "tokens_per_call": {"input": [varying[0] + system[0], varying[1] + system[1] + schema_tokens],
                            "output": list(output)},
        "tokens_total": {"input": [sum(low[k] for k in ("input", "cache_write", "cache_read")),
                                   sum(high[k] for k in ("input", "cache_write", "cache_read"))],
                         "output": [low["output"], high["output"]]},
        "prices_usd_per_mtok": price, "price_source": PRICE_SOURCE,
        "cost_usd": None, "cost_per_call_usd": None, "reason": None,
    }
    if price is None:
        out["reason"] = f"no documented price for {model} in the skill docs, so no cost is estimated"
        return out
    lo, hi = _cost(low, price, batch), _cost(high, price, batch)
    out["cost_usd"] = {"low": round(lo, 2), "high": round(hi, 2)}
    if calls:
        out["cost_per_call_usd"] = {"low": round(lo / calls, 4), "high": round(hi / calls, 4)}
    return out
