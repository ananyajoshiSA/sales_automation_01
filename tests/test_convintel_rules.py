"""Keyword layer (analytics/convintel/rules.py and lexicon.py). Every transcript here is invented."""

from __future__ import annotations

import copy
import random
import re
import time

import pytest

from analytics.convintel import lexicon as X
from analytics.convintel import rules as R
from analytics.convintel import schema as S
from analytics.definitions import BUYING_SIGNALS, NEGATIVE_SIGNALS

FILLER = "The classes are live on weekends and the recordings stay in the portal for revision. "
EN = ("Hello Asha, this is Ravi from the academy about the diploma in corporate law. "
      "What is the fee for this course? The fee is Rs 45,000 and EMI is available at 5000 per month. "
      "Okay, please send me the payment link, I will pay tomorrow. "
      "Sure, I will call you back tomorrow at 5 pm to confirm. " + FILLER)
HI = ("Haan ji sir, fees kitni hai? EMI ho jayega kya? Course achha hai but abhi nahi, "
      "paise nahi hai abhi. Ghar pe puchna padega, soch ke batata hoon. Kal call karna sir. " + FILLER)
DEV = "नमस्ते सर। मुझे कोर्स की फीस कितनी है? फीस 25 हजार है। घर पे पूछना पड़ेगा। कल शाम पांच बजे कॉल करना। " * 2
IVR = "The number you have dialled is currently switched off. Please try again later. " * 4
LOOP = "hello can you hear me " * 25


def excerpts(out: dict):
    for cat in out["word_level"]["categories"].values():
        yield from ((x["excerpt"], x["offset"]) for x in cat["examples"])
    yield from ((x["excerpt"], x["offset"]) for x in out["sentence_level"]["labelled"])
    yield from ((f["excerpt"], f["offset"]) for f in out["findings"])


def assert_verbatim(text: str, out: dict) -> None:
    for ex, off in excerpts(out):
        if ex == "":
            assert off == -1
        else:
            assert text[off:off + len(ex)] == ex and len(ex) <= R.EXCERPT_CHARS


def terms(out: dict, cat: str) -> dict:
    return out["word_level"]["categories"][cat]["terms"]


# ------------------------------------------------------------------ structure and vocabularies

def test_every_component_present_and_complete():
    out = R.analyze_keywords(EN, 30)
    assert set(S.KEYWORD_COMPONENTS) <= set(out) and R.missing_components(out) == []
    assert R.ENGINE == "rules" and R.VERSION == S.KEYWORD_VERSION
    assert set(out["word_level"]["categories"]) == set(X.CATEGORIES)
    assert set(out["integrity"]) == {"words", "wpm", "loop_share", "machine_text", "flags"}
    assert set(out["signals"]) == {"buying", "objections", "negative", "payment_step", "dated_next_step",
                                   "callback_requested", "amounts", "course_mentions", "readiness_score",
                                   "readiness_band", "markers"}
    for f in out["findings"]:
        assert set(f) == {"category", "excerpt", "offset", "confidence", "reasoning", "recommended_action"}


def test_lexicon_tags_stay_inside_the_schema_vocabularies():
    for cat, entries in X.CATEGORIES.items():
        for rx, tag in entries:
            re.compile(rx)
            # matched against lower-cased text without IGNORECASE, so a capital letter would never match
            assert not re.search(r"[A-Z]", re.sub(r"\\u[0-9a-fA-F]{4}|\\.", "", rx)), rx
            if cat in X.SIGNAL_CATEGORIES:
                assert tag is None or tag in S.SIGNAL_TYPES
            elif cat in X.OBJECTION_SOURCES:
                assert tag in S.OBJECTION_CATEGORIES
            elif cat == "negative":
                assert tag in NEGATIVE_SIGNALS
            else:
                assert tag is None
    # the shared patterns are imported, not copied
    assert (BUYING_SIGNALS["fee"], "fee_question") in X.CATEGORIES["buying"]
    assert (BUYING_SIGNALS["payment"], "payment_intent") in X.CATEGORIES["payment_intent"]
    assert all((rx, key) in X.CATEGORIES["negative"] for key, rx in NEGATIVE_SIGNALS.items())


@pytest.mark.parametrize("text", [EN, HI, DEV, IVR, LOOP, "", "Not interested. " * 50],
                         ids=["english", "hinglish", "devanagari", "ivr", "loop", "empty", "repeated"])
def test_outputs_use_only_schema_values(text):
    out = R.analyze_keywords(text, 120)
    sig = out["signals"]
    assert set(sig["buying"]) <= set(S.SIGNAL_TYPES) and set(sig["objections"]) <= set(S.OBJECTION_CATEGORIES)
    assert set(sig["negative"]) <= set(NEGATIVE_SIGNALS) and sig["readiness_band"] in S.READINESS_BANDS
    assert 0 <= sig["readiness_score"] <= 100
    assert out["language"]["primary"] in S.SEMANTIC_SCHEMA["properties"]["language"]["properties"]["primary"]["enum"]
    assert set(out["integrity"]["flags"]) <= {"no_content", "thin", "machine", "loop"}
    assert all(f["category"] in S.FINDING_CATEGORIES and f["confidence"] in ("low", "medium") for f in out["findings"])
    assert all(lb in set(X.CATEGORIES) | {"question"} for x in out["sentence_level"]["labelled"] for lb in x["labels"])
    assert R.missing_components(out) == []
    assert_verbatim(text, out)


# ------------------------------------------------------------------ languages

def test_english_call_with_a_full_close():
    out = R.analyze_keywords(EN, 30)
    sig = out["signals"]
    assert sig["buying"] == ["fee_question", "payment_intent", "emi_interest"]
    assert sig["payment_step"] and sig["dated_next_step"] and sig["callback_requested"]
    assert sig["amounts"] == [45000, 5000] and sig["course_mentions"] == ["diploma in corporate law"]
    assert sig["readiness_band"] == "hot" and sig["readiness_score"] >= 75
    assert out["language"]["primary"] == "english" and not out["language"]["code_switching"]
    found = {f["category"]: f for f in out["findings"]}
    assert found["payment_ready"]["confidence"] == "medium" and found["callback_promised"]["confidence"] == "medium"
    assert "no speaker labels" in found["payment_ready"]["reasoning"] and found["payment_ready"]["recommended_action"]
    assert_verbatim(EN, out)


def test_hinglish_terms_objections_and_language():
    out = R.analyze_keywords(HI, 30)
    assert {"fees kitni hai", "emi ho jayega"} <= set(terms(out, "buying"))
    assert {"abhi nahi", "soch ke batata"} <= set(terms(out, "hesitation"))
    assert {"paise nahi", "ghar pe puchna"} <= set(terms(out, "objection"))
    assert "kal call" in terms(out, "callback")
    sig = out["signals"]
    assert sig["objections"] == ["emi_or_finance", "family_approval"] and sig["negative"] == ["no_budget"]
    assert sig["callback_requested"] and sig["dated_next_step"]
    assert out["language"]["primary"] == "hinglish" and out["language"]["code_switching"]
    assert out["language"]["hinglish_markers"] >= 10
    assert_verbatim(HI, out)


def test_devanagari_words_terms_amounts_and_boundaries():
    out = R.analyze_keywords(DEV, 30)
    assert out["word_level"]["words"] == out["integrity"]["words"] == len(DEV.split())
    assert out["language"]["primary"] == "hindi" and out["language"]["devanagari_share"] > 0.9
    sig = out["signals"]
    assert "fee_question" in sig["buying"] and sig["objections"] == ["family_approval"]
    assert sig["amounts"] == [25000] and sig["callback_requested"] and sig["dated_next_step"]
    assert out["sentence_level"]["questions"] == 2
    assert_verbatim(DEV, out)
    # a vowel sign is part of the word, so "फीसदी" (per cent) is not "फीस" (fee)
    assert R.analyze_keywords("ब्याज दस फीसदी है", None)["word_level"]["categories"]["buying"]["count"] == 0


def test_word_boundaries_in_english():
    out = R.analyze_keywords("The premium seminar on coffee feedback is free", None)
    assert out["word_level"]["categories"]["buying"]["count"] == 0


def test_text_that_lower_case_would_resize_keeps_offsets():
    text = "İstanbul office, fees kitni hai? " + FILLER
    out = R.analyze_keywords(text, None)
    assert "fees kitni hai" in terms(out, "buying")
    assert_verbatim(text, out)


# ------------------------------------------------------------------ negation

@pytest.mark.parametrize("text,cat,tag,present", [
    ("I am not interested. Please don't call me again.", "negative", "not_interested", True),
    ("No, I am not interested in this course.", "negative", "not_interested", True),
    ("No problem sir, the classes are on weekends.", "objection", None, False),
    ("I am not interested in EMI right now.", "buying", "emi_interest", False),
    ("Mujhe EMI nahi chahiye, full payment karunga.", "buying", "emi_interest", False),
    ("It is not expensive at all for this course.", "objection", "price", False),
    ("No, it is too expensive for me.", "objection", "price", True),
    ("Busy nahi hoon, bataiye.", "objection", "time", False),
    ("Sir I am busy, time nahi hai abhi.", "objection", "time", True),
    # a "no" before a term that is itself a negation is emphasis, and "no problem" negates nothing after it
    ("No sir I can't afford it right now.", "objection", "price", True),
    ("No I am not interested.", "objection", "not_interested", True),
    ("No problem I will pay tomorrow.", "payment_intent", "payment_intent", True),
    ("No problem I will pay tomorrow.", "commitment", None, True),
    ("Bharosa nahi hai sir.", "objection", "trust", True),
    ("Bharosa rakhiye sir, sab clear hai.", "objection", "trust", False),
])
def test_negation(text, cat, tag, present):
    hits = R._matches(text)[cat]
    found = any(t == tag for *_, t in hits) if tag else bool(hits)
    assert found is present


@pytest.mark.parametrize("text,cat,tag", [
    ("Okay theek hai sir. Haan ji, accha. No problem.", None, None),
    ("Can you confirm your email id? Definitely sir, the course is good.", "commitment", None),
    ("Mujhe ek cheez puchna hai sir.", "objection", "family_approval"),
    ("I have already completed my LLB and already done an internship.", "objection", "joined_elsewhere"),
    ("The next batch starts next month.", "hesitation", None),
    ("Course ke baad mein internship bhi milti hai.", "hesitation", None),
    ("Let me check the batch dates for you.", "hesitation", None),
    ("इसका फायदा ये है कि नौकरी में मदद मिलेगी।", "objection", None),
    ("मुझे ईएमआई नहीं चाहिए।", "objection", "not_interested"),
])
def test_common_phrases_are_not_signals(text, cat, tag):
    hits = R._matches(text)
    if cat is None:
        assert not any(hits.values())
    else:
        assert not [h for h in hits[cat] if tag is None or h[3] == tag]


def test_putting_it_off_still_counts_as_hesitation():
    assert R._matches("Maybe I will join next month.")["hesitation"]
    assert R._matches("Abhi nahi, agle mahine dekhte hain.")["hesitation"]


def test_curly_apostrophes_match_like_straight_ones():
    hits = R._matches("I don’t want EMI. I’ll pay tomorrow, don’t worry.")
    assert not hits["buying"] and [t for _, _, t, _ in hits["commitment"]] == ["i'll pay"]


def test_negation_feeds_the_signals():
    out = R.analyze_keywords("No problem. I am not interested in EMI. " + FILLER * 2, None)
    assert out["signals"]["objections"] == ["not_interested"] and "emi_interest" not in out["signals"]["buying"]
    assert out["signals"]["negative"] == ["not_interested"]


# ------------------------------------------------------------------ amounts, courses, questions

@pytest.mark.parametrize("text,amounts", [
    ("The fee is 25000 only", [25000]),
    ("It costs 25,000 for the year", [25000]),
    ("Around 25k with the books", [25000]),
    ("Pay Rs 25,000 today", [25000]),
    ("Pay Rs.30000 today", [30000]),
    ("Just ₹25000 in all", [25000]),
    ("Sirf 25 hazaar lagega", [25000]),
    ("The LLM is 1.5 lakh", [150000]),
    ("Full fee 2,50,000 and EMI 4500 a month", [250000, 4500]),
    ("Only ₹ 999 for the workshop", [999]),
    ("25000 rupees or 25000/-", [25000]),
    ("Batch of 2026 starts at 5 pm", []),
    ("My number is 98765 43210", []),
    ("Version 1.5 of the notes", []),
    ("Your OTP is 452189 and the pincode is 110001", []),
    ("Roll number 23456, and the fee is 25000", [25000]),
])
def test_amounts(text, amounts):
    assert R.analyze_keywords(text, None)["signals"]["amounts"] == amounts


def test_course_mentions():
    text = ("We have a Diploma in US Corporate Law and Paralegal Studies which is great. Also the AI bootcamp, "
            "the corporate law ka course, and a certificate course on contract drafting for startups.")
    assert R.analyze_keywords(text, None)["signals"]["course_mentions"] == [
        "diploma in us corporate law and paralegal studies", "ai bootcamp", "corporate law course",
        "certificate course on contract drafting"]


def test_sentences_and_questions():
    out = R.analyze_keywords("Fees kitni hai? What is the duration. Aap join karoge kya. Okay theek hai.", None)
    assert out["sentence_level"]["sentences"] == 4 and out["sentence_level"]["questions"] == 3


def test_unpunctuated_text_is_cut_into_short_pieces_and_excerpts_stay_verbatim():
    words = ("so the fees kitni hai and the emi ho jayega and i will call you tomorrow and the batch "
             "is full and the payment failed twice ").split()
    text = " ".join(words * 12)
    out = R.analyze_keywords(text, 600)
    assert out["sentence_level"]["sentences"] > 1
    assert_verbatim(text, out)


# ------------------------------------------------------------------ caps and totals

def test_long_lists_are_capped_with_totals():
    text = " ".join(f"Question {i}: what is the fee for the batch number {i}?" for i in range(60))
    out = R.analyze_keywords(text, 600)
    sl = out["sentence_level"]
    assert len(sl["labelled"]) == R.MAX_LABELLED and sl["labelled_total"] == 60
    buying = out["word_level"]["categories"]["buying"]
    assert len(buying["examples"]) == R.MAX_EXAMPLES and buying["count"] == 60
    assert_verbatim(text, out)


# ------------------------------------------------------------------ integrity and findings

@pytest.mark.parametrize("text", ["", None, "   \n "])
def test_empty_text_gives_a_valid_result(text):
    out = R.analyze_keywords(text, None)
    assert R.missing_components(out) == []
    assert out["word_level"]["words"] == 0 and out["word_level"]["wpm"] is None
    assert out["signals"]["readiness_band"] == "unclear" and out["signals"]["readiness_score"] == 0
    assert out["integrity"]["flags"] == ["no_content"] and out["language"]["primary"] == "unclear"
    assert out["sentence_level"] == {"sentences": 0, "questions": 0, "labelled": [], "labelled_total": 0}
    (f,) = out["findings"]
    assert f["category"] == "possible_not_real" and f["excerpt"] == "" and f["offset"] == -1
    assert "not proof" in f["reasoning"]


def test_recorded_message_is_flagged_and_not_scored():
    out = R.analyze_keywords(IVR, 200)
    assert {"machine", "loop"} <= set(out["integrity"]["flags"]) and out["integrity"]["machine_text"]
    assert out["signals"]["readiness_band"] == "unclear"
    f = out["findings"][-1]
    assert f["category"] == "possible_not_real" and f["confidence"] == "medium"
    assert f["excerpt"] == "The number you have dialled is currently switched off." and f["offset"] == 0
    short = R.analyze_keywords("The number you are calling is switched off.", 40)
    assert short["integrity"]["flags"] == ["no_content", "machine"]


def test_looping_text_is_flagged_with_the_phrase():
    out = R.analyze_keywords(LOOP, 120)
    assert out["integrity"]["flags"] == ["loop"] and out["integrity"]["loop_share"] >= 0.15
    assert out["word_level"]["repeated_phrases"] == [{"phrase": "hello can you hear me", "count": 25}]
    assert out["word_level"]["repeated_phrases_total"] == 1
    f = out["findings"][-1]
    assert f["category"] == "possible_not_real" and f["excerpt"].startswith("hello can you hear me")
    assert_verbatim(LOOP, out)


def test_thin_needs_a_duration():
    text = " ".join(random.Random(3).choice(FILLER.split()) + str(i) for i in range(40))
    assert R.analyze_keywords(text, 600)["integrity"]["flags"] == ["thin"]
    out = R.analyze_keywords(text, None)
    assert out["integrity"]["flags"] == [] and out["integrity"]["wpm"] is None and out["word_level"]["wpm"] is None


def test_payment_friction_and_unavailable_course_findings():
    text = ("The payment link is not working and the payment failed twice. Also the batch is full and "
            "admissions are closed for this course. " + FILLER)
    found = {f["category"]: f for f in R.analyze_keywords(text, 40)["findings"]}
    assert found["payment_friction"]["confidence"] == "medium" and "resend the link" in found["payment_friction"]["recommended_action"]
    assert found["course_unavailable"]["confidence"] == "medium"
    assert R.analyze_keywords(text, 40)["signals"]["objections"] == ["course_unavailable"]


@pytest.mark.parametrize("cat,a,b", [
    ("payment_friction", "The payment failed.", "Transaction declined hua."),
    ("course_unavailable", "The batch is full.", "Admissions are band now."),
    ("callback_promised", "Please call me back.", "Baad mein call karna."),
    ("commitment_made", "I will join the course.", "Main kar dunga sir."),
    ("payment_ready", "Please send the payment link.", "Payment link bhej do."),
])
def test_reasoning_names_the_kind_of_words_never_the_words(cat, a, b):
    # reasoning reaches the dashboard even with excerpts switched off, so transcript words may sit only in "excerpt"
    fa, fb = ({f["category"]: f for f in R.analyze_keywords(t, 60)["findings"]}[cat] for t in (a, b))
    assert fa["reasoning"] == fb["reasoning"] and fa["excerpt"] != fb["excerpt"]


def test_reasoning_is_labelled_and_quotes_nothing():
    for text in (EN, HI, DEV, IVR, LOOP, ""):
        out = R.analyze_keywords(text, 120)
        said = {t for c in out["word_level"]["categories"].values() for t in c["terms"] if len(t.split()) > 1}
        for f in out["findings"]:
            r = f["reasoning"]
            assert r.startswith(("Keyword match:", "Keyword check:")) and '"' not in r and f["confidence"] != "high"
            assert not [t for t in said if t in r.lower()] and (not f["excerpt"] or f["excerpt"] not in r)


def test_unclear_is_not_a_low_reading():
    out = R.analyze_keywords("Not interested. Don't call again.", 40)    # too few words to read
    assert out["signals"]["negative"] and out["signals"]["readiness_band"] == "unclear"
    cold = R.analyze_keywords("Not interested. Don't call again. Too expensive, paise nahi hai. " + FILLER * 3, 120)
    assert cold["signals"]["readiness_band"] == "cold"


# ------------------------------------------------------------------ completeness check

def test_missing_components_names_damaged_parts():
    out = copy.deepcopy(R.analyze_keywords(EN, 30))
    del out["language"]
    out["signals"]["readiness_score"] = 140
    out["integrity"]["flags"] = ["bogus"]
    del out["word_level"]["categories"]["callback"]
    assert R.missing_components(out) == ["language", "word_level", "signals", "integrity"]
    out = copy.deepcopy(R.analyze_keywords(EN, 30))
    out["sentence_level"] = "x"
    out["signals"]["buying"] = ["vibes"]
    assert R.missing_components(out) == ["sentence_level", "signals"]
    assert R.missing_components(None) == list(S.KEYWORD_COMPONENTS) == R.missing_components({})


# ------------------------------------------------------------------ speed

def test_200_transcripts_run_in_under_3_seconds():
    rng = random.Random(7)
    vocab = ("the a to and of you is it that in for this we have will can so on are what your with sir okay yes "
             "course law legal batch class live recording faculty practice drafting contract client career work "
             "time week month after before then because but if when how why which there here all some more about "
             "know think tell say ask talk explain understand learn study read write help need want like").split()
    cues = ["Sir fees kitni hai?", "EMI ho jayega kya?", "I will think and let you know.", "Kal shaam 5 baje call karna.",
            "Send me the payment link.", "It is too expensive for me.", "Ghar pe puchna padega.", "The fee is Rs 45,000."]
    texts = []
    for _ in range(200):
        parts, n, size = [], 0, rng.randint(300, 1100)
        while n < size:
            s = rng.choice(cues) if rng.random() < 0.08 else " ".join(rng.choices(vocab, k=rng.randint(6, 16))) + "."
            parts.append(s)
            n += len(s.split())
        texts.append(" ".join(parts))
    start = time.perf_counter()
    for t in texts:
        R.analyze_keywords(t, 300)
    assert time.perf_counter() - start < 3.0
