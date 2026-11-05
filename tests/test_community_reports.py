import json

import pytest

import analytics.community_reports as cr
from analytics.community_cycle import batch_of, community_leads
from tests.test_collection_cycle import META, T, call, lead, owner, stage

GROUPS = {**META["member_groups"], "Kanika Verma": ["Elite Changemakers"]}


def sheet(lid, *, owner_name="Vedahi Rana", outcome="Pending", received=2000, balance=53000, phone="9000000001", d="2026-10-18"):
    return {"lsq_id": lid, "match_note": "", "vertical": "Team Mayur", "date": d, "month": d[:7], "owner": owner_name,
            "course": "Community: Independent Director - Group 88 & 89, Week_1 , (18th October, 2026)", "name": f"Lead {lid}",
            "phone": phone, "price": 55000, "received": received, "balance": balance, "outcome": outcome, "sheet_stage": "",
            "status": outcome}


def test_batch_joins_weeks_of_one_group_and_reads_its_start():
    assert batch_of("Community: Independent Director - Group 88 & 89, Week_2, (5th November, 2026)") == ("Independent Director G88–89", "5 Nov")
    assert batch_of("Community: Independent Director - Group 84-85 Week 1, (4th October, 2026)") == ("Independent Director G84–85", "4 Oct")
    assert batch_of("Community: AI for Business Growth | Women - Group 41, 42, Week_1 , (30th October, 2026)")[0] == "AI for Women G41–42"
    assert batch_of("Community: AI FOR LEGAL (Diwali Special Program) - Diwali Special Program (1st November 2026.)") == ("Legal AI Diwali Special", "1 Nov")


def test_lead_is_credited_from_leadsquared_with_the_sheets_money():
    hist = {"a": [owner("2026-10-18 11:00", "Seller", "Vedahi Rana"), call("2026-10-18 12:00"), stage("2026-10-20 12:00", "Collections done")],
            "b": [call("2026-10-10 12:00")],  # a sales call before booking is not a collection call
            "c": []}
    leads = {"a": lead("a"), "b": lead("b", owner_name="Seller"), "c": lead("c", owner_name="Seller")}
    rows = [sheet("a", outcome="Collected", received=52000, balance=0), sheet("b"), {**sheet("c"), "lsq_id": "", "match_note": "no lead"}]
    got, left = community_leads(rows, leads, hist, GROUPS, "Mayur", ("2026-10",))
    a, b = got
    assert a["caller"] == "Vedahi Rana" and a["credited_by"] == "lsq" and a["enrolled_at"] and a["received"] == 52000
    assert a["assigned"] == T("2026-10-18 11:00") and a["label"] == "Independent Director G88–89 · booked 18 Oct"
    # LeadSquared's owner is outside the team: the sheet's owner is credited, and the gaps page says so
    assert b["caller"] == "Vedahi Rana" and b["credited_by"] == "sheet" and b["calls"] == []
    assert [r["why_left"] for r in left] == ["no lead"]


def test_community_check_lists_numbers_batches_and_sheet_gaps(tmp_path, monkeypatch):
    hist = {"a": [owner("2026-10-18 11:00", "Seller", "Vedahi Rana"), call("2026-10-18 12:00"), stage("2026-10-20 12:00", "Collections done")],
            "b": [owner("2026-11-02 11:00", "Seller", "Vedahi Rana"), call("2026-11-02 12:00")],
            "c": [owner("2026-10-19 11:00", "Seller", "Vedahi Rana"), call("2026-10-19 12:00", dur=30)]}
    leads = {k: lead(k) for k in hist}
    rows = [sheet("a", outcome="Collected", received=52000, balance=0), sheet("b", phone="9000000002", d="2026-11-02"),
            sheet("c", phone="9000000003", outcome="Collected", received=40000, balance=13000)]
    monkeypatch.setattr(cr, "sheet_rows", lambda d, m: (rows, leads))
    monkeypatch.setattr(cr, "load_hist", lambda p: hist)
    (tmp_path / "groups.json").write_text(json.dumps({"member_groups": GROUPS}))
    j = {"b": {"2026-11-05": {"status": "hot", "why": "3 Nov call: card EMI agreed", "intent": "Card EMI · Fri 6 Nov", "next": "Fri 06 Nov 11:00 · send link",
                              "cb_kept": 1, "cb_promised": 1, "risk": ""}}}
    (tmp_path / "j.json").write_text(json.dumps(j))
    cfg = {"side": "Mayur", "team": "Test team", "months": ["2026-10", "2026-11"], "target": 85, "data_dir": str(tmp_path),
           "groups_meta": str(tmp_path / "groups.json"), "judgements": str(tmp_path / "j.json"), "lsq_read_at": "2026-11-05 14:00",
           "sheet_read_at": "2026-11-05 13:30"}
    with pytest.raises(ValueError, match="no judgement"):  # lead c is open and was never graded
        cr.CommunityReport(cfg, T("2026-11-05 15:00"))
    j["c"] = {"2026-11-05": {"status": "notreached", "why": "Only a 30-second call", "next": "Call today"}}
    (tmp_path / "j.json").write_text(json.dumps(j))
    rep = cr.CommunityReport(cfg, T("2026-11-05 15:00"))
    st = {l["lead_id"]: s for l, s in rep.rows}
    assert st["a"]["status"] == "enrolled" and st["a"]["value"] == 52000 and st["b"]["value"] == 53000
    doc = rep.html()
    assert "Thursday check" in doc and "+91-9000000002" in doc and "Independent Director G88–89 · booked 2 Nov" in doc
    assert "Weekend" not in doc and "Bootcamp · day" not in doc and "(est.)" not in doc and "pool sheet" not in doc.lower()
    assert "Latest — November bookings" in doc and "October performance" in doc and "Collected (sheet)" in doc
    # c is paid per the sheet but open in LeadSquared: flagged on its row and on the gaps page
    assert "Community sheet shows enrolled" in doc and "Paid per the community sheet, still open in LeadSquared (1)" in doc
    assert "stages up to Thu 05 Nov 2026, 14:00 IST" in doc
