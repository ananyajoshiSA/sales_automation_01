"""Keyword layer (engine "rules", version schema.KEYWORD_VERSION): word- and phrase-level signals in one transcript.

It is deterministic, free and fast, so it runs on every transcript, but it counts words and does not read context.
Transcripts carry no speaker labels, so nothing here says who said a word: findings say so and stay at "low" or
"medium" confidence. Terms live in analytics/convintel/lexicon.py (English, Hinglish, Devanagari). Every excerpt is
copied verbatim from the transcript with its character offset (``text[offset:offset + len(excerpt)] == excerpt``) and
is one sentence or a window of at most EXCERPT_CHARS characters. A finding's reasoning names the kind of words heard
and how often, never the words: it reaches the dashboard even when excerpts are off.

Integrity reuses analytics.call_integrity.transcript_flags (no_content, thin, machine, loop). Its word split breaks
Devanagari words at their vowel signs, so it is given the transcript with each word written as one plain token;
the word count then matches this layer's, and a recorded message is also looked for in the original opening words.

Readiness is READINESS_BASE plus points for each kind of buying signal and close step heard, minus points for each
kind of objection, negative signal and hesitation, clamped to 0-100; the bands match the semantic prompt (hot 75+,
warm 50+, cool 25+, cold below). With almost no words or a recorded message the band is "unclear" and the score 0,
so read the band before using the score.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections import Counter

from analytics.call_integrity import MACHINE, THIN_WPM, transcript_flags
from analytics.call_markers import NEXT_STEP, has_payment_step, markers_in
from analytics.convintel import lexicon as X
from analytics.convintel import schema as S
from analytics.definitions import NEGATIVE_SIGNALS
from analytics.definitions import _NEGATION as NEGATED_BEFORE  # the rule analytics.definitions.signals() applies

ENGINE = "rules"
VERSION = S.KEYWORD_VERSION

READINESS_BASE = 25
SIGNAL_POINTS = {"payment_intent": 20, "enrol_intent": 15, "emi_interest": 10, "start_date": 10, "fee_question": 8,
                 "documents_or_details": 8, "urgency": 6, "decision_maker_ready": 4, "other": 2}
STEP_POINTS = {"commitment": 10, "payment_step": 12, "dated_next_step": 8, "amounts": 4}
OBJECTION_POINTS, MAX_OBJECTIONS = -8, 4        # per distinct objection category, at most 4 counted
NEGATIVE_POINTS, MAX_NEGATIVE = -20, 2          # per distinct negative signal, at most 2 counted
HESITATION_POINTS = -8
BANDS = ((75, "hot"), (50, "warm"), (25, "cool"), (0, "cold"))
UNCLEAR_FLAGS = frozenset({"no_content", "machine"})

EXCERPT_CHARS = 160
MAX_SENTENCE = 240          # an unpunctuated stretch is cut into pieces this long so labels stay local
MAX_LABELLED, MAX_EXAMPLES, MAX_PHRASES, MAX_TERMS = 40, 5, 10, 20
PHRASE_WORDS, PHRASE_REPEATS = (4, 5, 6), 3
HEAD_WORDS = 40             # call_integrity looks for a recorded message in the opening 40 words
INTEGRITY_FLAGS = ("no_content", "thin", "machine", "loop")
LANGUAGES = ("english", "hindi", "hinglish", "other", "unclear")
CATEGORIES = tuple(X.CATEGORIES)
# Sentences with these labels are kept first when the labelled list is capped.
KEY_LABELS = frozenset(CATEGORIES) - {"persuasion", "ineffective_wording", "uncertainty"}
LABELS = CATEGORIES + ("question",)
KW, KW_CHECK = "Keyword match:", "Keyword check:"   # every finding says it comes from word matching, not a reading
NO_SPEAKERS = "The transcript has no speaker labels, so check who said it."
REVIEW = "This needs review; it is not proof that the call was not real."

_L, _R = rf"(?<![{X.WORD_CHARS}])", rf"(?![{X.WORD_CHARS}])"
_WORD = re.compile(rf"[{X.WORD_CHARS}]+(?:['’][{X.WORD_CHARS}]+)*")
_PLAIN = re.compile(r"\w+")
_DEV = re.compile(r"[\u0900-\u097f]")


def _alternation(terms: list[tuple[str, str | None]], flags: int = 0) -> re.Pattern:
    # One boundary around the whole alternation, no group per term and (normally) no IGNORECASE: sre then skips
    # non-starting positions and non-matching branches in C, about ten times faster than a group per term.
    return re.compile(f"{_L}(?:{'|'.join(rx for rx, _ in terms)}){_R}", flags)


# category -> (pattern for lower-cased text, case-blind pattern for the rare text lower() would resize,
#              one pattern per term to tell which term matched, the terms' tags)
_CATS = {cat: (_alternation(terms), _alternation(terms, re.I), [re.compile(rx, re.I) for rx, _ in terms],
               [tag for _, tag in terms]) for cat, terms in X.CATEGORIES.items()}
_NEG_AFTER = re.compile(rf"\s*(?:nahi|nahin|nhi|mat|नहीं|नही|मत){_R}", re.I)
# A term that is itself a negation ("can't afford", "not now") is not cancelled by a "no" before it: that "no" is
# emphasis ("No, I can't afford it"). Nor do these set phrases negate what follows ("no problem, I will pay").
_SELF_NEG = re.compile(rf"{_L}(?:not|no|never|cannot|without|nahi|nahin|nhi|mat|नहीं|नही|मत){_R}|n't{_R}", re.I)
_NOT_NEGATION = re.compile(r"\b(?:no (?:problem|issues?|worries|tension|doubt)|not only)\b", re.I)
# A negation reaches back only within its clause: "not working and the payment failed" still counts the failure.
_CLAUSE = re.compile(r"[,.;:?!।॥\n]|\b(?:and|but|or|because|aur|lekin|magar|kyunki)\b", re.I)
_END = re.compile(r"[?!।॥]+|(?<!\brs)(?<!\bmr)(?<!\bdr)(?<!\bmrs)(?<!\bms)\.+(?=\s|$)", re.I)
_QSTART = re.compile(rf"\W*{X.QUESTION_START}{_R}", re.I)
_QEND = re.compile(rf"{_L}{X.QUESTION_END}\W*$", re.I)
_TIME = re.compile(rf"{_L}(?:{X.TIME_WORDS}){_R}", re.I)
_PAY_STEP = re.compile(rf"{_L}(?:{X.PAYMENT_STEP_HINGLISH}){_R}", re.I)
_UNITS = {unicodedata.normalize(f, k): v for k, v in X.AMOUNT_UNITS.items() for f in ("NFC", "NFD")}
_AMOUNT = re.compile(
    rf"(?:(?P<cur>₹|{_L}(?:rs|inr)\.?(?=\s*\d)|{_L}rupees?{_R})\s*|(?<![\w,.]))"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)"
    rf"(?:\s*(?P<unit>{'|'.join(sorted(map(re.escape, _UNITS), key=len, reverse=True))}){_R})?"
    rf"(?:\s*(?P<suf>(?:rupees?|rupaye|rupay|rs|inr){_R}|/-|रुपये|रुपए))?", re.I)
_DIGIT_BEFORE, _DIGIT_AFTER = re.compile(r"\d[\s-]$"), re.compile(r"[\s-]\d")
_NOT_MONEY = re.compile(rf"{_L}(?:otp|pin|pin ?code|code|roll no|id|number|ext(?:ension)?|ref(?:erence)?)\.?(?:\s+(?:is|was))?"
                        r"\s*[:#-]?\s*$", re.I)
_COURSE_IN = re.compile(
    rf"{_L}(?P<lead>{X.COURSE_LEADS})\s+(?P<prep>in|on)\s+(?P<topic>[a-z][\w&/-]*(?:\s+[a-z&][\w&/-]*){{0,5}})", re.I)
_COURSE_NAMED = re.compile(
    rf"{_L}(?:(?P<kind>{X.COURSE_KINDS})|(?:ka|ki|wala|wali|vala)\s+(?P<noun>course|diploma|program)){_R}", re.I)
_WORDS_BEFORE = re.compile(r"(?<![\w-])(?:[a-z][\w-]*\s+){1,3}$", re.I)


# ------------------------------------------------------------------ text structure

def _tokens(text: str) -> tuple[list[str], list[str]]:
    """The words, and the same words lower-cased."""
    toks, low_text = _WORD.findall(text), text.lower()
    low = _WORD.findall(low_text) if len(low_text) == len(text) else []
    return toks, low if len(low) == len(toks) else [t.lower() for t in toks]


def _word_spans(text: str, n: int) -> list[tuple[int, int]]:
    """Character spans of the first ``n`` words (only a few excerpts need them, so they aren't kept for every word)."""
    return [m.span() for m, _ in zip(_WORD.finditer(text), range(n))]


def _strip(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def _sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence: split on . ? ! and the danda, long unpunctuated runs cut at a space."""
    spans, start = [], 0
    for m in _END.finditer(text):
        _add_sentence(text, start, m.end(), spans)
        start = m.end()
    _add_sentence(text, start, len(text), spans)
    return spans


def _add_sentence(text: str, s: int, e: int, spans: list) -> None:
    s, e = _strip(text, s, e)
    while e - s > MAX_SENTENCE:
        cut = text.rfind(" ", s + 1, s + MAX_SENTENCE)
        cut = cut if cut > s else s + MAX_SENTENCE
        a, b = _strip(text, s, cut)
        if _WORD.search(text, a, b):
            spans.append((a, b))
        s, e = _strip(text, cut, e)
    if e > s and _WORD.search(text, s, e):
        spans.append((s, e))


def _excerpt(text: str, sent: tuple[int, int], s: int, e: int) -> tuple[str, int]:
    """The sentence holding text[s:e] when it is short, else a window of EXCERPT_CHARS around the match."""
    a, b = sent[0], max(sent[1], e)
    if b - a > EXCERPT_CHARS:
        if e - s >= EXCERPT_CHARS:
            a, b = s, s + EXCERPT_CHARS
        else:
            lo = max(a, s - (EXCERPT_CHARS - (e - s)) // 2)
            hi = min(b, lo + EXCERPT_CHARS)
            lo = max(a, hi - EXCERPT_CHARS)
            if lo > a and (sp := text.find(" ", lo, s)) != -1:     # don't start or end inside a word
                lo = sp + 1
            if hi < b and (sp := text.rfind(" ", e, hi)) != -1:
                hi = sp
            a, b = lo, hi
    a, b = _strip(text, a, b)
    return text[a:b], a


# ------------------------------------------------------------------ term matching

def _negated(text: str, s: int, e: int) -> bool:
    """Negated just before in the same clause ("not interested in EMI"), or by a Hindi negation after ("EMI nahi")."""
    if _NEG_AFTER.match(text, e):
        return True
    if _SELF_NEG.search(text, s, e):
        return False
    before = _NOT_NEGATION.sub(" ", _CLAUSE.split(text[max(0, s - 30):s])[-1])
    return bool(NEGATED_BEFORE.search(before))


def _matches(text: str) -> dict[str, list[tuple[int, int, str, str | None]]]:
    """category -> (start, end, term, tag) for every match that is not negated. The alternation takes the first
    listed term that matches at a position, so the tag is that of the first term matching the whole span."""
    low = text.lower()
    # Offsets carry over unless lower() resized a character (e.g. "İ"); a curly apostrophe matches as a straight one.
    same = len(low) == len(text)
    src = low.replace("’", "'") if same else text
    out = {}
    for cat, (rx, rx_any, each, tags) in _CATS.items():
        negatable, tagged = cat in X.NEGATABLE, any(tags)
        hits = []
        for m in (rx if same else rx_any).finditer(src):
            s, e = m.span()
            if negatable and _negated(src, s, e):
                continue
            g = m.group()
            tag = next((tags[i] for i, r in enumerate(each) if r.fullmatch(g)), None) if tagged else None
            hits.append((s, e, " ".join(g.lower().split())[:40], tag))
        out[cat] = hits
    return out


def _amounts(text: str) -> list[int]:
    """Rupee amounts in order of first mention. A bare number counts only with 4-6 digits, not a year, not part
    of a spoken phone number and not an OTP, PIN code, ID or other "number"; a currency sign or word, a unit (k,
    thousand, hazaar, lakh) or commas make it money."""
    out = []
    for m in _AMOUNT.finditer(text):
        num, unit = m["num"], m["unit"]
        if not (m["cur"] or m["suf"] or unit or "," in num):
            if ("." in num or not 4 <= len(num) <= 6 or (len(num) == 4 and 1900 <= int(num) <= 2099)
                    or _DIGIT_BEFORE.search(text, max(0, m.start() - 2), m.start()) or _DIGIT_AFTER.match(text, m.end())
                    or _NOT_MONEY.search(text, max(0, m.start() - 24), m.start())):
                continue
        value = round(float(num.replace(",", "")) * (_UNITS.get(unit.lower(), 1) if unit else 1))
        if 0 < value < 10 ** 9 and value not in out:
            out.append(value)
    return out


def _course_words(words: list[str], leading: bool) -> list[str]:
    """Trim a course name: cut at a stop word (keep the words after it when ``leading``), drop joins at both ends."""
    low = [w.lower() for w in words]
    stops = [i for i, w in enumerate(low) if w in X.COURSE_STOP]
    if stops:
        words, low = (words[stops[-1] + 1:], low[stops[-1] + 1:]) if leading else (words[:stops[0]], low[:stops[0]])
    while low and low[0] in X.COURSE_JOIN:
        words, low = words[1:], low[1:]
    while low and low[-1] in X.COURSE_JOIN:
        words, low = words[:-1], low[:-1]
    return words


def _courses(text: str) -> list[str]:
    """Course names in order of first mention, lower-cased: "diploma in X", "X bootcamp", "X ka course"."""
    found = []
    for m in _COURSE_IN.finditer(text):
        topic = _course_words(m["topic"].split(), False)
        if topic:
            found.append((m.start(), f"{m['lead']} {m['prep']} {' '.join(topic)}"))
    for m in _COURSE_NAMED.finditer(text):
        before = _WORDS_BEFORE.search(text, max(0, m.start() - 60), m.start())
        words = _course_words(before.group().split()[-(2 if m["kind"] else 3):] if before else [], True)
        if m["kind"]:
            found.append((m.start(), " ".join(words + [m["kind"]])))
        elif words:
            found.append((m.start(), f"{' '.join(words)} {m['noun']}"))
    out = []
    for _, name in sorted(found):
        name = " ".join(name.lower().split())
        if name not in out:
            out.append(name)
    return out


def _repeats(low: list[str]) -> list[tuple[int, int, tuple, int]]:
    """(words, first index, phrase, count) for 4-6 word phrases said PHRASE_REPEATS+ times, most repeated first.
    A phrase inside one that repeats at least as often is left out, and so are the shifted copies of a loop
    ("can you hear me hello" next to "hello can you hear me")."""
    found, prev = {}, None
    for n in PHRASE_WORDS:
        grams = list(zip(*(low[i:] for i in range(n))))
        counts = Counter(grams if prev is None else [g for g in grams if g[:-1] in prev])
        rep = {g: k for g, k in counts.items() if k >= PHRASE_REPEATS}
        if not rep:
            break
        for g, k in rep.items():            # the shorter phrases this one extends are not worth listing apart
            for sub in (g[:-1], g[1:]):
                if 0 < found.get(sub, (0, 0))[1] <= k:
                    del found[sub]
        first = {}
        for i, g in enumerate(grams):
            if g in rep and g not in first:
                first[g] = i
        found.update((g, (first[g], k)) for g, k in rep.items())
        prev = rep
    kept = []
    for g, (i, k) in sorted(found.items(), key=lambda x: (-x[1][1], -len(x[0]), x[1][0])):
        n, phrase = len(g), f" {' '.join(g)} "
        if not any(i < j + m and j < i + n or phrase in f"{ks}{ks[1:]}" for m, j, ks, _ in kept):
            kept.append((n, i, phrase, k))
    return [(n, i, tuple(ks.split()), k) for n, i, ks, k in kept]


# ------------------------------------------------------------------ layer parts

def _language(text: str, toks: list[str], low: list[str]) -> dict:
    n, counts = len(toks), Counter(low)
    dev = 0 if text.isascii() else sum(1 for t in toks if not t.isascii() and _DEV.search(t))
    hinglish = sum(counts[w] for w in X.HINGLISH_WORDS)
    english = sum(counts[w] for w in X.ENGLISH_WORDS)
    hindi = dev + hinglish
    switching = n > 0 and min(hindi, english) >= 3 and min(hindi, english) / n >= 0.05
    if n < 5:
        primary = "unclear"
    elif switching:
        primary = "hinglish"
    elif hindi / n >= 0.05 and hindi >= english:
        primary = "hindi"
    else:
        primary = "english" if english / n >= 0.05 else "other"
    return {"primary": primary, "devanagari_share": round(dev / n, 3) if n else 0.0, "hinglish_markers": hinglish,
            "code_switching": switching}


def _categories(text: str, sents: list, sent_of, hits: dict) -> tuple[dict, dict[int, set], dict[int, tuple]]:
    """word_level.categories, the labels of each sentence and each labelled sentence's first match."""
    categories, labels, first_hit = {}, {}, {}
    for cat in CATEGORIES:
        examples, seen = [], set()
        for s, e, _, _ in hits[cat]:
            i = sent_of(s)
            labels.setdefault(i, set()).add(cat)
            if i not in first_hit or (s, e) < first_hit[i]:
                first_hit[i] = (s, e)
            if len(examples) < MAX_EXAMPLES:
                ex, off = _excerpt(text, sents[i], s, e)
                if off not in seen:
                    seen.add(off)
                    examples.append({"excerpt": ex, "offset": off})
        terms = Counter(t for _, _, t, _ in hits[cat])
        categories[cat] = {"count": len(hits[cat]), "terms": dict(terms.most_common(MAX_TERMS)), "examples": examples}
    return categories, labels, first_hit


def _sentence_level(text: str, sents: list, labels: dict[int, set], first_hit: dict[int, tuple]) -> dict:
    """Sentence and question counts, and the labelled sentences (key labels first when capped)."""
    questions = 0
    for i, (a, b) in enumerate(sents):
        if text[b - 1] == "?" or _QSTART.match(text, a, b) or _QEND.search(text, max(a, b - 12), b):
            questions += 1
            labels.setdefault(i, set()).add("question")
    keyed = sorted(i for i, ls in labels.items() if ls & KEY_LABELS)
    rest = sorted(i for i, ls in labels.items() if not ls & KEY_LABELS)
    labelled = []
    for i in sorted((keyed + rest)[:MAX_LABELLED]):
        s, e = first_hit.get(i, sents[i])
        ex, off = _excerpt(text, sents[i], s, e)
        labelled.append({"index": i, "offset": off, "excerpt": ex, "labels": [lb for lb in LABELS if lb in labels[i]]})
    return {"sentences": len(sents), "questions": questions, "labelled": labelled, "labelled_total": len(labels)}


def _integrity(text: str, toks: list[str], duration: int | None) -> tuple[dict, re.Match | None]:
    if text.isascii():
        plain = " ".join(toks).replace("'", "")
    else:
        ids: dict[str, int] = {}
        plain = " ".join(t.replace("'", "") if t.isascii() else t if _PLAIN.fullmatch(t)
                         else f"u{ids.setdefault(t.lower(), len(ids))}" for t in toks)
    # Without a duration "thin" can't be judged: one second makes the words-a-minute rate too high to trip it.
    flags, ev = transcript_flags(plain, duration or 1)
    head_words = _word_spans(text, HEAD_WORDS)
    head = MACHINE.search(text, 0, head_words[-1][1]) if head_words else None
    if head and "machine" not in flags:
        flags.append("machine")
    return {"words": ev["words"], "wpm": ev["wpm"] if duration else None, "loop_share": ev["loop_share"],
            "machine_text": ev["machine_text"] or (head.group(0) if head else ""),
            "flags": [f for f in INTEGRITY_FLAGS if f in flags]}, head


def _readiness(buying: list[str], objections: list[str], negative: list[str], steps: dict[str, bool],
               hesitation: bool, flags: list[str]) -> tuple[int, str]:
    if UNCLEAR_FLAGS & set(flags):
        return 0, "unclear"
    score = (READINESS_BASE + sum(SIGNAL_POINTS[t] for t in buying) + sum(p for k, p in STEP_POINTS.items() if steps[k])
             + OBJECTION_POINTS * min(len(objections), MAX_OBJECTIONS) + NEGATIVE_POINTS * min(len(negative), MAX_NEGATIVE)
             + (HESITATION_POINTS if hesitation else 0))
    score = max(0, min(100, score))
    return score, next(band for limit, band in BANDS if score >= limit)


def _signals(text: str, hits: dict, sent_of, flags: list[str]) -> dict:
    tags = {cat: {t for _, _, _, t in hits[cat] if t} for cat in CATEGORIES}
    buying = [t for t in S.SIGNAL_TYPES if any(t in tags[c] for c in X.SIGNAL_CATEGORIES)]
    objections = [o for o in S.OBJECTION_CATEGORIES if any(o in tags[c] for c in X.OBJECTION_SOURCES)]
    negative = [k for k in NEGATIVE_SIGNALS if k in tags["negative"]]
    markers = sorted(markers_in(text))
    # A day or time counts as a dated next step when it shares a sentence with a callback, promise or payment term.
    action = {sent_of(s) for c in ("callback", "commitment", "payment_intent") for s, _, _, _ in hits[c]}
    dated = NEXT_STEP in markers or bool(action) and any(sent_of(m.start()) in action for m in _TIME.finditer(text))
    amounts = _amounts(text)
    payment_step = has_payment_step(text) or bool(_PAY_STEP.search(text))
    steps = {"commitment": bool(hits["commitment"]), "payment_step": payment_step, "dated_next_step": dated,
             "amounts": bool(amounts)}
    score, band = _readiness(buying, objections, negative, steps, bool(hits["hesitation"]), flags)
    return {"buying": buying, "objections": objections, "negative": negative, "payment_step": payment_step,
            "dated_next_step": dated, "callback_requested": bool(hits["callback"]), "amounts": amounts,
            "course_mentions": _courses(text), "readiness_score": score, "readiness_band": band, "markers": markers}


def _finding(category: str, ex: tuple[str, int] | None, confidence: str, reasoning: str, action: str) -> dict:
    excerpt, offset = ex or ("", -1)
    return {"category": category, "excerpt": excerpt, "offset": offset, "confidence": confidence,
            "reasoning": reasoning, "recommended_action": action}


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _findings(cats: dict, sig: dict, integ: dict, duration: int | None, loop_ex, head_ex) -> list[dict]:
    """Reasoning names what kind of words came up and how often, never the words themselves: it reaches the
    dashboard even when excerpts are switched off, so transcript text stays in the excerpt field only."""
    def first(cat: str) -> tuple[str, int] | None:
        exs = cats[cat]["examples"]
        return (exs[0]["excerpt"], exs[0]["offset"]) if exs else None

    def level(strong: bool) -> str:
        return "medium" if strong else "low"

    out = []
    pay, commit = cats["payment_intent"]["count"], cats["commitment"]["count"]
    if pay or (sig["payment_step"] and commit):
        backing = sum((pay > 0, sig["payment_step"], bool(sig["amounts"]), commit > 0, sig["dated_next_step"]))
        also = [w for w, on in (("a payment step such as a payment link", sig["payment_step"]),
                                ("an amount", bool(sig["amounts"])), ("a day or time", sig["dated_next_step"])) if on]
        also = ", ".join(also[:-1]) + " and " + also[-1] if len(also) > 1 else "".join(also)
        out.append(_finding(
            "payment_ready", first("payment_intent") or first("commitment"), level(backing >= 2),
            f"{KW} {f'words about paying came up {_times(pay)}' if pay else f'promise words came up {_times(commit)}'}"
            f"{f', with {also} mentioned' if also else ''}. {NO_SPEAKERS}",
            "Check in LeadSquared whether the payment came through; if not, call today and help the lead finish it."
            if sig["payment_step"] else
            "Call back today: confirm the amount, send the payment link while on the call and agree the date to pay."))
    if n := cats["payment_friction"]["count"]:
        out.append(_finding("payment_friction", first("payment_friction"), level(n >= 2),
                            f"{KW} words about a payment problem (a failed payment, a link that does not open, a card "
                            f"or EMI refused) came up {_times(n)}. {NO_SPEAKERS}",
                            "Call back today to fix it: resend the link, offer another way to pay (UPI, card, NEFT) "
                            "or check EMI eligibility."))
    if n := cats["course_availability"]["count"]:
        out.append(_finding("course_unavailable", first("course_availability"), level(n >= 2),
                            f"{KW} words saying a course or batch is full, closed or not offered came up {_times(n)}. "
                            f"{NO_SPEAKERS}",
                            "Tell your team leader which course or batch the lead wanted, then offer the next batch "
                            "date or the closest course that is open now."))
    if n := cats["callback"]["count"]:
        dated = sig["dated_next_step"]
        out.append(_finding("callback_promised", first("callback"), level(dated),
                            f"{KW} a call back was asked for or promised ({_times(n)}), "
                            f"{'with a day or time named' if dated else 'with no day or time found'}. {NO_SPEAKERS}",
                            "Put the callback in LeadSquared for the day and time named and call on time." if dated else
                            "Put a callback in LeadSquared and agree a specific day and time with the lead."))
    if commit:
        out.append(_finding("commitment_made", first("commitment"), level(commit >= 2 or sig["dated_next_step"]),
                            f"{KW} words making a promise came up {_times(commit)}. {NO_SPEAKERS}",
                            "Write the promise and its date in LeadSquared and follow up on the day it is due."))
    if flags := integ["flags"]:
        why = []
        if "no_content" in flags:
            words = f"Only {integ['words']} words were" if integ["words"] else "No words were"
            why.append(f"{words} transcribed" + (f" for a {duration}-second call." if duration else "."))
        if "thin" in flags:
            why.append(f"{integ['wpm']} words a minute were transcribed; under {THIN_WPM} is unusually little talk.")
        if "machine" in flags:
            why.append("The opening words look like a recorded message, IVR or voicemail.")
        if "loop" in flags:
            why.append(f"One phrase repeats: about {round(100 * (integ['loop_share'] or 0))}% of the words.")
        out.append(_finding("possible_not_real", head_ex if "machine" in flags else loop_ex if "loop" in flags else None,
                            level(flags != ["thin"]), " ".join([KW_CHECK] + why + [REVIEW]),
                            "Team leader: listen to the recording before drawing any conclusion; if it was not a real "
                            "conversation, check the dialer and the caller's call log."))
    return out


# ------------------------------------------------------------------ the layer

def analyze_keywords(text: str | None, duration_s: int | None) -> dict:
    """The keyword layer's output for one transcript (format in the build contract); empty text gives a valid result."""
    text = text if isinstance(text, str) else ""
    ok = isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool) and duration_s > 0
    duration = int(duration_s) if ok else None
    toks, low = _tokens(text)
    sents = _sentences(text)
    starts = [a for a, _ in sents]

    def sent_of(pos: int) -> int:
        return max(bisect_right(starts, pos) - 1, 0)

    hits = _matches(text)
    categories, labels, first_hit = _categories(text, sents, sent_of, hits)
    sentence_level = _sentence_level(text, sents, labels, first_hit)
    phrases = _repeats(low)
    integ, head = _integrity(text, toks, duration)
    signals = _signals(text, hits, sent_of, integ["flags"])
    loop_ex = head_ex = None
    if phrases:
        n, i, _, _ = phrases[0]
        spans = _word_spans(text, i + n)
        loop_ex = _excerpt(text, sents[sent_of(spans[i][0])], spans[i][0], spans[-1][1])
    if head:
        head_ex = _excerpt(text, sents[sent_of(head.start())], head.start(), head.end())
    return {
        "language": _language(text, toks, low),
        "word_level": {"words": len(toks), "wpm": round(len(toks) * 60 / duration) if duration else None,
                       "categories": categories,
                       "repeated_phrases": [{"phrase": " ".join(g), "count": k} for _, _, g, k in phrases[:MAX_PHRASES]],
                       "repeated_phrases_total": len(phrases)},
        "sentence_level": sentence_level,
        "signals": signals,
        "integrity": integ,
        "findings": _findings(categories, signals, integ, duration, loop_ex, head_ex),
    }


# ------------------------------------------------------------------ completeness check

def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _excerpts_ok(items) -> bool:
    return isinstance(items, list) and all(isinstance(x, dict) and isinstance(x.get("excerpt"), str)
                                           and isinstance(x.get("offset"), int) for x in items)


def _language_ok(v: dict) -> bool:
    share = v["devanagari_share"]
    return (v["primary"] in LANGUAGES and isinstance(share, (int, float)) and not isinstance(share, bool)
            and 0 <= share <= 1 and _count(v["hinglish_markers"]) and isinstance(v["code_switching"], bool))


def _word_level_ok(v: dict) -> bool:
    cats = v["categories"]
    return (_count(v["words"]) and (v["wpm"] is None or _count(v["wpm"])) and isinstance(cats, dict)
            and all(isinstance(cats[c], dict) and _count(cats[c]["count"]) and isinstance(cats[c]["terms"], dict)
                    and _excerpts_ok(cats[c]["examples"]) for c in CATEGORIES)
            and isinstance(v["repeated_phrases"], list)
            and all(isinstance(p.get("phrase"), str) and _count(p.get("count")) for p in v["repeated_phrases"]))


def _sentence_level_ok(v: dict) -> bool:
    return (_count(v["sentences"]) and _count(v["questions"]) and v["questions"] <= v["sentences"]
            and _excerpts_ok(v["labelled"]) and all(isinstance(x.get("labels"), list) for x in v["labelled"]))


def _signals_ok(v: dict) -> bool:
    return (set(v["buying"]) <= set(S.SIGNAL_TYPES) and set(v["objections"]) <= set(S.OBJECTION_CATEGORIES)
            and isinstance(v["negative"], list) and isinstance(v["markers"], list)
            and all(isinstance(v[k], bool) for k in ("payment_step", "dated_next_step", "callback_requested"))
            and isinstance(v["amounts"], list) and all(_count(a) for a in v["amounts"])
            and isinstance(v["course_mentions"], list) and all(isinstance(c, str) for c in v["course_mentions"])
            and _count(v["readiness_score"]) and v["readiness_score"] <= 100 and v["readiness_band"] in S.READINESS_BANDS)


def _integrity_ok(v: dict) -> bool:
    return (_count(v["words"]) and (v["wpm"] is None or _count(v["wpm"])) and isinstance(v["flags"], list)
            and set(v["flags"]) <= set(INTEGRITY_FLAGS))


_CHECKS = {"language": _language_ok, "word_level": _word_level_ok, "sentence_level": _sentence_level_ok,
           "signals": _signals_ok, "integrity": _integrity_ok}


def missing_components(out: dict) -> list[str]:
    """Names from schema.KEYWORD_COMPONENTS that are absent or malformed in ``out``."""
    missing = []
    for name in S.KEYWORD_COMPONENTS:
        part = out.get(name) if isinstance(out, dict) else None
        try:
            ok = isinstance(part, dict) and _CHECKS[name](part)
        except (KeyError, TypeError, AttributeError, ValueError):
            ok = False
        if not ok:
            missing.append(name)
    return missing
