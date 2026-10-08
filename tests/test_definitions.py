from analytics.definitions import BUYING_SIGNALS, NEGATIVE_SIGNALS, signals, speed_bucket


def test_course_names_are_not_buying_signals():
    assert signals("Enquired about the family law course, shared feedback form", BUYING_SIGNALS) == []


def test_buying_signals_match_on_word_boundaries():
    assert signals("Asked about the fee and EMI options", BUYING_SIGNALS) == ["emi_or_loan", "fee"]
    assert signals("Coffee chat, feedback call", BUYING_SIGNALS) == []


def test_negated_signal_is_ignored():
    assert signals("Lead is not interested in EMI", BUYING_SIGNALS) == []
    assert signals("Lead said not interested", NEGATIVE_SIGNALS, negatable=False) == ["not_interested"]


def test_speed_bucket():
    assert speed_bucket(None) == "never"
    assert speed_bucket(3) == "≤5 min"
    assert speed_bucket(30) == "15–60 min"
    assert speed_bucket(2000) == ">24 h"
