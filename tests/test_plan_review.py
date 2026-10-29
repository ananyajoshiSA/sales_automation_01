import json

import pytest

from scripts import plan_review as pr
from integrations.timeutil import utc


def cand(lid, owner="Ravi K", **kw):
    return {"lead_id": lid, "owner": owner, "name": f"Lead {lid}", "stage": "Call Back Later", "course": "IP Law",
            "best_zip_intent": None, "buying_signals": [], "crosscheck_flags": [], "dials": 3, "longest_call_min": 4.0,
            "total_talk_min": 6.0, "inbound_calls": 0, "last_conversation_ist": "2026-10-20 18:00",
            "days_since_last_conversation": 3.0, "unanswered_since_last_conversation": 0, "next_follow_up_ist": None,
            "zip_notes": [{"summary": "Asked about EMI.\nLink to the conversation: https://x", "reason": "r"}],
            "call_log": [], "phone": "9", "pre_score": 10.0, **kw}


def tiered(lid, tier, owner="Ravi K", **kw):
    return {"lead_id": lid, "owner": owner, "name": f"Lead {lid}", "tier": tier, "prob_3d": 15, "why": "rule",
            "callback_missed": False, "best_time": "18:00–21:00", "course": "IP Law", "phone": "9", "rank": 1, **kw}


def review(lid, tier="B", prob=12, **kw):
    return {"lead_id": lid, "tier": tier, "prob_3d": prob, "why": "Said they will decide on 24 Oct.", "opening_line": "Hi",
            "next_action": "Ask for the decision", "objection_to_prepare": "", "best_time": "", "promise_due": True,
            "stage_fix": "", "changed": "new lead", **kw}


PLAN = {"candidates": [cand("L1"), cand("L2"), cand("L3"), cand("L4"), cand("L5")],
        "tiered": [tiered("L1", "A"), tiered("L2", "C"), tiered("L3", "C", callback_missed=True), tiered("L4", "B"),
                   tiered("L5", "C")],
        "discovery": {"L1": "Bot, 12 Oct: final-year student"}}


def test_choose_rereads_touched_leads_and_first_reads_hot_ones():
    reviews = {"L4": review("L4"), "L5": review("L5", "C", 3)}
    again, first = pr.choose(PLAN, reviews, touched={"L5", "L2"})
    assert again == ["L5"]                # read before, new call since; L4 read before with nothing new keeps its reading
    assert sorted(first) == ["L1", "L3"]  # A, and C with a missed booked callback; L2 (plain C) is not read


def test_touched_since_counts_answered_calls_and_zip_notes_only():
    snap = {"calls": [{"lead_id": "L1", "status": "Answered", "start_utc": "2026-10-20 10:00:00"},
                      {"lead_id": "L2", "status": "NotAnswered", "start_utc": "2026-10-20 10:00:00"},
                      {"lead_id": "L3", "status": "Answered", "start_utc": "2026-10-10 10:00:00"}],
            "zip_activities": [{"RelatedProspectId": "L4", "CreatedOn": "2026-10-21 10:00:00"}]}
    assert pr.touched_since(snap, utc("2026-10-14 08:40:00")) == {"L1", "L4"}


def test_pack_rows_carry_the_previous_review_and_trim_notes():
    rows = pr.pack_rows(PLAN, ["L5", "L1", "missing"], {"L5": review("L5", "C", 3)})
    assert [r["lead_id"] for r in rows] == ["L1", "L5"]
    assert rows[0]["discovery_so_far"].startswith("Bot")
    assert rows[0]["zip_notes"][0]["summary"] == "Asked about EMI."
    assert "previous_review" not in rows[0]
    assert rows[1]["previous_review"]["tier"] == "C" and "changed" not in rows[1]["previous_review"]


def test_check_review_catches_order_tier_and_range():
    inp = [{"lead_id": "L1"}, {"lead_id": "L2"}]
    assert pr.check_review(inp, [review("L1"), review("L2", "A", 30)]) == []
    errs = pr.check_review(inp, [review("L2"), review("L1", "B", 40)])
    assert any("order" in e for e in errs) and any("outside 8-25" in e for e in errs)
    assert any("tier 'E'" in e for e in pr.check_review(inp[:1], [review("L1", "E")]))


def test_apply_reviews_overrides_rule_tiers_and_drops_d():
    plan = json.loads(json.dumps(PLAN))
    moved = pr.apply_reviews(plan, {
        "L1": review("L1", "B", 15, stage_fix="Follow Up For Closure: asked for the link", changed="paid token on 19 Oct"),
        "L2": review("L2", "D", 0),
        "L4": review("L4", "A", 30, changed="no answer, unchanged"),
        "gone": review("gone"),
    })
    t = {x["lead_id"]: x for x in plan["tiered"]}
    assert t["L1"]["tier"] == "B" and t["L1"]["prob_3d"] == 15 and t["L1"]["month_end_due"]
    assert t["L1"]["why"].endswith("Stage check: Follow Up For Closure: asked for the link.")
    assert t["L1"]["changed"] == "paid token on 19 Oct"
    assert t["L2"]["tier"] == "D"
    assert t["L4"]["changed"] == ""  # "no answer, unchanged" is not shown as an update
    assert moved[("A", "B")] == 1 and moved[("C", "D")] == 1 and "gone" not in t


def test_load_reviews_later_files_win(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "out_0.json").write_text(json.dumps([review("L1", "C", 3)]))
    (tmp_path / "b" / "out_0.json").write_text(json.dumps([review("L1", "A", 30)]))
    got = pr.load_reviews([str(tmp_path / "a" / "out_*.json"), str(tmp_path / "b" / "out_*.json")])
    assert got["L1"]["tier"] == "A"


def test_fill_brief_refuses_unfilled_placeholders():
    t = "Today {TODAY}. Input {DIR}/in_{K}.json"
    assert pr.fill_brief(t, TODAY="Fri", DIR="/d") == "Today Fri. Input /d/in_{K}.json"
    with pytest.raises(ValueError, match="DIR"):
        pr.fill_brief(t, TODAY="Fri")


def test_the_shipped_brief_fills_completely():
    out = pr.fill_brief(open(pr.BRIEF).read(), CONTEXT="Team.", TODAY="Friday", DATA_TO="23 Oct 12:15 IST",
                        PREVIOUS="Wed 14 Oct", PROMISE_BY="Mon 26 Oct", DIR="/d")
    assert "/d/in_{K}.json" in out and "/d/out_{K}.json" in out


def test_parse_utc_reads_the_usual_forms():
    want = utc("2026-10-14 08:40:00")
    assert pr.parse_utc("2026-10-14T08:40Z") == want
    assert pr.parse_utc("2026-10-14T14:10+05:30") == want
    assert pr.parse_utc("2026-10-14 08:40:00") == want
    with pytest.raises(SystemExit):
        pr.parse_utc("14 Oct")
    with pytest.raises(SystemExit):
        pr.parse_utc("2026-10-14T08:40")  # no zone: ambiguous
