"""Check a semantic-layer reply before it is stored: excerpts must really be in the transcript, numbers in range,
labels from the schema's lists, and every component present and well-formed.

* Excerpts are matched on whole words ignoring case, spacing and punctuation (machine transcripts are loosely
  punctuated) but never meaning: an excerpt that is not in the transcript is replaced by "" and counted, so a
  paraphrase or a translation is never kept as evidence. A found excerpt is replaced by the transcript's own words
  for that span, so ``transcript[offset:offset + len(excerpt)] == excerpt`` always holds. A finding that loses its
  excerpt drops to confidence "low"; every finding gets ``offset`` (-1 when it has no excerpt).
* Numbers outside their range are clamped (readiness 0-100, quality 0-10, caller share 0-100, counts >= 0) and
  listed in ``_validation.range_fixes``. A label outside the schema's list becomes its "unclear"/"other"-style
  value (or is removed from a list of flags) and is listed in ``_validation.value_fixes``; so is a readiness band
  that contradicts its score (prompt.BAND_FLOORS).
* A component that is absent, of the wrong type, or missing a required field is left out of the cleaned output
  and named in ``missing``, so the job stays incomplete and is retried.

Notes in ``_validation`` name fields and labels, never transcript text.
"""

from __future__ import annotations

import math
import re
import unicodedata

from analytics.convintel.prompt import band_for
from analytics.convintel.schema import QUALITY_DIMENSIONS, SEMANTIC_COMPONENTS, SEMANTIC_SCHEMA

APOSTROPHES = frozenset("'`‘’´")
RANGES = {("intent", "readiness_score"): (0, 100), ("speaker_turns", "caller_share_pct"): (0, 100),
          ("quality", "overall"): (0, 10)}
QUALITY_SCORE = (0, 10)
# Replacement for a label the schema doesn't allow, in order of preference; each is the "don't know" or weakest
# value of its list (strength -> weak, finding confidence -> low, real_conversation -> doubtful).
FALLBACKS = ("unclear", "other", "doubtful", "low", "weak")
_LABEL = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _normalize(text: str) -> tuple[str, list[int]]:
    """Lower-cased text with punctuation and whitespace runs folded to one space, plus each character's index in
    ``text``. Apostrophes, invisible format characters (e.g. the zero-width joiner in Devanagari) and commas inside
    numbers are dropped, so "don't" matches "dont" and "25,000" matches "25000"; combining marks are kept, so
    Devanagari words stay whole."""
    out: list[str] = []
    idx: list[int] = []
    gap = True
    last = len(text) - 1
    for i, ch in enumerate(text):
        cat = unicodedata.category(ch)
        if (ch in APOSTROPHES or cat == "Cf"
                or (ch == "," and 0 < i < last and text[i - 1].isdigit() and text[i + 1].isdigit())):
            continue
        if ch.isspace() or cat[0] in "PSZ" or cat == "Cc":
            if not gap:
                out.append(" ")
                idx.append(i)
                gap = True
            continue
        for c in ch.casefold():
            out.append(c)
            idx.append(i)
        gap = False
    return "".join(out), idx


class _Finder:
    def __init__(self, transcript: str | None):
        self.text = transcript or ""
        norm, self.idx = _normalize(self.text)
        self.hay = f" {norm} "

    def span(self, excerpt: str) -> tuple[int, int] | None:
        """(start, end) of the excerpt in the original transcript, matched on whole words; None if absent."""
        needle = _normalize(excerpt or "")[0].strip()
        if not needle:
            return None
        p = self.hay.find(f" {needle} ")
        if p < 0:
            return None
        return self.idx[p], self.idx[p + len(needle) - 1] + 1


    def count(self, excerpt: str) -> int:
        """Whole-word occurrences of the excerpt in the transcript, matched the same way as ``span``."""
        needle = _normalize(excerpt or "")[0].strip()
        if not needle:
            return 0
        n, p = 0, self.hay.find(f" {needle} ")
        while p >= 0:
            n += 1
            p = self.hay.find(f" {needle} ", p + len(needle) + 1)
        return n


def find_excerpt(excerpt: str, transcript: str) -> int:
    """Char offset of ``excerpt`` in ``transcript`` ignoring case, spacing and punctuation; -1 if absent or empty."""
    s = _Finder(transcript).span(excerpt)
    return s[0] if s else -1


class _Malformed(Exception):
    pass


def _path(path: tuple) -> str:
    return ".".join(str(p) for p in path)


def _shown(v) -> str:
    """A model value quoted in a note only when it looks like a label, so notes never carry transcript text."""
    return repr(v) if isinstance(v, str) and _LABEL.fullmatch(v) else "a value"


def _is_excerpt(sch: dict) -> bool:
    return sch.get("type") == "string" and "verbatim" in sch.get("description", "")


def _fallback(enum: list) -> str | None:
    if len(enum) == 1:
        return enum[0]
    return next((f for f in FALLBACKS if f in enum), None)


class _Checker:
    def __init__(self, transcript: str | None):
        self.finder = _Finder(transcript)
        self.checked = self.dropped = 0
        self.range_fixes: list[str] = []
        self.value_fixes: list[str] = []

    def walk(self, v, sch: dict, path: tuple):
        if "anyOf" in sch:
            if v is None and any(o.get("type") == "null" for o in sch["anyOf"]):
                return None
            sch = next(o for o in sch["anyOf"] if o.get("type") != "null")
        t = sch.get("type")
        if t == "object":
            return self._object(v, sch, path)
        if t == "array":
            return self._array(v, sch["items"], path)
        if _is_excerpt(sch):
            return self._excerpt(v, path)
        if t == "string":
            if not isinstance(v, str):
                raise _Malformed(f"{_path(path)} is not text")
            return self._enum(v, sch["enum"], path) if "enum" in sch else v
        if t == "integer":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise _Malformed(f"{_path(path)} is not a number")
            return self._range(v, path)
        if t == "boolean":
            if not isinstance(v, bool):
                raise _Malformed(f"{_path(path)} is not true/false")
            return v
        return v

    def _object(self, v, sch: dict, path: tuple) -> dict:
        if not isinstance(v, dict):
            raise _Malformed(f"{_path(path)} is not an object")
        props = sch["properties"]
        absent = [k for k in props if k not in v]
        if absent:
            raise _Malformed(f"{_path(path)} lacks {', '.join(absent)}")
        extra = sorted(str(k) for k in v if k not in props)
        if extra:
            self.value_fixes.append(f"{_path(path)}: removed {len(extra)} unexpected field(s) "
                                    f"{', '.join(_shown(k) for k in extra[:5])}")
        return {k: self.walk(v[k], ps, path + (k,)) for k, ps in props.items()}

    def _array(self, v, items: dict, path: tuple) -> list:
        if not isinstance(v, list):
            raise _Malformed(f"{_path(path)} is not a list")
        out = []
        for i, x in enumerate(v):
            if _is_excerpt(items):
                x = self._excerpt(x, path + (i,))
                if x:   # a bare evidence list keeps only excerpts found in the transcript
                    out.append(x)
            elif "enum" in items and _fallback(items["enum"]) is None:   # a list of flags: drop unknown or repeated
                y = x.strip().lower() if isinstance(x, str) else x
                if y not in items["enum"]:
                    self.value_fixes.append(f"{_path(path)}: removed {_shown(x)}, not an allowed value")
                elif y not in out:
                    out.append(y)
            else:
                out.append(self.walk(x, items, path + (i,)))
        return out

    def _excerpt(self, v, path: tuple) -> str:
        if v is None:   # "no excerpt" said with null instead of ""
            self.value_fixes.append(f"{_path(path)}: null read as no excerpt")
            return ""
        if not isinstance(v, str):
            raise _Malformed(f"{_path(path)} is not text")
        if not v.strip():
            return ""
        self.checked += 1
        s = self.finder.span(v)
        if s is None:
            self.dropped += 1
            return ""
        return self.finder.text[s[0]:s[1]]

    def _enum(self, v: str, enum: list, path: tuple) -> str:
        if v in enum:
            return v
        low = v.strip().lower()
        if low in enum:
            return low
        fb = _fallback(enum)
        if fb is None:
            raise _Malformed(f"{_path(path)} has a value outside its list")
        self.value_fixes.append(f"{_path(path)}: {_shown(v)} is not an allowed value, set to {fb!r}")
        return fb

    def _range(self, v, path: tuple) -> int:
        if not math.isfinite(v):
            raise _Malformed(f"{_path(path)} is not a finite number")
        key = tuple(p for p in path if not isinstance(p, int))
        lo, hi = QUALITY_SCORE if key[0] == "quality" and key[-1] == "score" else RANGES.get(key, (0, None))
        n = max(lo, round(v)) if hi is None else min(hi, max(lo, round(v)))
        if n != v:
            rng = f"{lo} or more" if hi is None else f"{lo}-{hi}"
            why = f"outside {rng}" if not lo <= v <= (math.inf if hi is None else hi) else "not a whole number"
            self.range_fixes.append(f"{_path(path)}: {v} {why}, set to {n}")
        return n


def _word_analysis(wa: dict, chk: _Checker) -> None:
    """A phrase is its excerpt, so one not found in the transcript is removed; a repetition count is replaced by the
    transcript's own count, and an entry that does not occur twice word for word is removed."""
    phrases = [p for p in wa["phrases"] if p["excerpt"]]
    if len(phrases) < len(wa["phrases"]):
        chk.value_fixes.append(f"word_analysis.phrases: removed {len(wa['phrases']) - len(phrases)} phrase(s) "
                               "not found in the transcript")
    wa["phrases"] = phrases
    kept = []
    for i, r in enumerate(wa["repeated"]):
        n = chk.finder.count(r["excerpt"]) if r["excerpt"] else 0
        if n < 2:
            chk.value_fixes.append(f"word_analysis.repeated.{i}: removed, it does not occur twice word for word")
            continue
        if r["times"] != n:
            chk.range_fixes.append(f"word_analysis.repeated.{i}.times: {r['times']} set to {n}, the transcript's count")
            r["times"] = n
        kept.append(r)
    wa["repeated"] = kept


def validate_semantic(output: dict, transcript: str) -> tuple[dict, list[str], int]:
    """(cleaned output with ``_validation`` and finding offsets, missing component names, dropped excerpt count)."""
    chk = _Checker(transcript)
    src = output if isinstance(output, dict) else {}
    props = SEMANTIC_SCHEMA["properties"]
    clean: dict = {}
    missing: list[str] = []
    malformed: list[str] = []
    for comp in SEMANTIC_COMPONENTS:
        if comp not in src:
            missing.append(comp)
            continue
        try:
            clean[comp] = chk.walk(src[comp], props[comp], (comp,))
        except _Malformed as e:
            missing.append(comp)
            malformed.append(str(e))
    extra = sorted(str(k) for k in src if k not in props and k != "_validation")
    if extra:
        chk.value_fixes.append(f"removed {len(extra)} unexpected top-level field(s) "
                               f"{', '.join(_shown(k) for k in extra[:5])}")
    # clean["findings"] exists only when the model's findings were a list of objects, one per raw item
    for i, (raw, f) in enumerate(zip(src["findings"] if "findings" in clean else [], clean.get("findings", []))):
        s = chk.finder.span(f["excerpt"]) if f["excerpt"] else None
        f["offset"] = s[0] if s else -1
        if not f["excerpt"] and str(raw.get("excerpt") or "").strip() and f["confidence"] != "low":
            chk.value_fixes.append(f"findings.{i}.confidence: set to 'low', its excerpt is not in the transcript")
            f["confidence"] = "low"
    wa = clean.get("word_analysis")
    if wa:
        _word_analysis(wa, chk)
    q = clean.get("quality")
    if q and q["overall"] is not None and all(q[d]["score"] is None for d in QUALITY_DIMENSIONS):
        chk.value_fixes.append("quality.overall: set to null, no skill was scored on this call")
        q["overall"] = None
    it = clean.get("intent")
    if it and it["readiness_band"] != "unclear" and band_for(it["readiness_score"]) != it["readiness_band"]:
        band = band_for(it["readiness_score"])
        chk.value_fixes.append(f"intent.readiness_band: {it['readiness_band']!r} does not fit score "
                               f"{it['readiness_score']}, set to {band!r}")
        it["readiness_band"] = band
    st = clean.get("speaker_turns")
    if (st and not st["labels_in_transcript"] and not st["inferred"]
            and any(st[k] is not None for k in ("caller_share_pct", "caller_questions", "customer_questions"))):
        chk.value_fixes.append("speaker_turns.inferred: set to true; the transcript has no speaker labels")
        st["inferred"] = True
    clean["_validation"] = {"excerpts_checked": chk.checked, "excerpts_dropped": chk.dropped,
                            "range_fixes": chk.range_fixes, "value_fixes": chk.value_fixes, "malformed": malformed}
    return clean, missing, chk.dropped
