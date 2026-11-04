import json
from datetime import datetime

import pytest

from analytics.collection_reports import Report, mode_of, row
from integrations.timeutil import IST
from tests.test_collection_cycle import META, T, call, lead, owner, stage

POOL = {"as_of": "29 Oct 2026", "bootcamps": {"Technology Law 24 Oct": {"price": [59600, 60400], "all_teams": 3, "recovered": 1,
                                                                         "recovered_amount": 56600, "pending": 2, "pending_amount": 113200}}}
J = {"status": "hot", "why": "Card EMI agreed", "intent": "Card EMI · Thu 29 Oct", "cb_kept": 1, "cb_promised": 2, "next": "Thu 29 Oct 15:00 · close"}


def cycle(tmp_path):
    hist = {"a": [owner("2026-10-26 11:00", "Seller", "Vedahi Rana"), call("2026-10-26 12:00"), stage("2026-10-29 12:00", "Course Enrolled")],
            "b": [owner("2026-10-26 11:00", "Seller", "Vedahi Rana"), call("2026-10-26 13:00")],
            "c": [owner("2026-10-27 11:00", "Seller", "Vedahi Rana")]}
    leads = [lead("a"), {**lead("b"), "Mobile": "+91-0000000002"}, {**lead("c"), "Mobile": "+91-0000000003"}]
    d = tmp_path / "w1"
    d.mkdir()
    (d / "tagged_leads.json").write_text(json.dumps(leads))
    (d / "hist.jsonl").write_text("".join(json.dumps({"lead_id": k, "activities": v}) + "\n" for k, v in hist.items()))
    (d / "meta.json").write_text(json.dumps(META))
    judged = {"b": {"2026-10-28": J, "2026-10-30": {**J, "status": "warm", "wed_hot_followup": "no", "wed_hot_outcome": "Call missed"}},
              "a": {"2026-10-28": {**J, "status": "warm"}}}
    (d / "j.json").write_text(json.dumps(judged))
    return {"side": "Mayur", "team": "Test team", "month": "November 2026", "fee": 3000, "target": 85, "wed_cut": "2026-10-28 20:00",
            "weekends": [{"n": 1, "label": "bootcamps 24–26 Oct", "data_dir": str(d), "bootcamp_day": "2026-10-24", "judgements": str(d / "j.json")}],
            "pool": POOL}


def test_reports_list_numbers_not_ids_and_follow_wednesday_hot_leads(tmp_path):
    cfg = cycle(tmp_path)
    with pytest.raises(ValueError, match="no judgement"):       # lead c is open and was never judged
        Report(cfg, "wed", T("2026-10-28 20:00"))
    j = json.loads((tmp_path / "w1" / "j.json").read_text())
    j["c"] = {d: {"status": "notreached", "why": "Not called", "next": "Call now"} for d in ("2026-10-28", "2026-10-30")}
    (tmp_path / "w1" / "j.json").write_text(json.dumps(j))
    wed = Report(cfg, "wed", T("2026-10-28 20:00"))
    st = {l["lead_id"]: s["status"] for l, s in wed.rows}
    assert st == {"a": "warm", "b": "hot", "c": "notreached"}
    doc = wed.html()
    assert "+91-0000000003" in doc and "Technology Law 24 Oct · D2" in doc and '"a"' not in doc
    assert "1 lead not called yet" in doc
    # columns removed at the user's request (Nov 2026); "not reached" renamed
    for gone in ("Median 1st call", ">1st call<", "Booking fees, all teams", "Median days to pay", "Not reached"):
        assert gone not in doc
    assert "DNP/No real conversation" in doc
    fri = Report(cfg, "fri", T("2026-10-30 12:00"))
    st = {l["lead_id"]: s for l, s in fri.rows}
    assert st["a"]["status"] == "enrolled" and st["a"]["value"] == 56600 and st["b"]["value"] == 56600
    doc = fri.html()
    assert "Wednesday’s 1 hot leads" in doc and "Call missed" in doc and "1 were not called at the time promised" in doc
    assert "Median days to pay" not in doc and "Booking fees, all teams" not in doc and ">1st call<" not in doc
    assert "agree on every bootcamp" in doc and "Thursday review" not in doc and "Friday review" in doc
    assert "<span>Called within 12 h</span>" not in doc and "Lead by lead check not done" in doc
    cfg["pool"]["bootcamps"]["Technology Law 24 Oct"]["recovered"] = 2
    assert "1 enrolled in LSQ, 2 recovered in the MIS" in Report(cfg, "fri", T("2026-10-30 12:00")).html()
    # the sheet's enrolled list: b paid per the sheet (still open in LSQ), a enrolled in LSQ but listed under a wrong number
    cfg["pool"]["lists_as_of"] = "2026-10-30"
    cfg["pool"]["bootcamps"]["Technology Law 24 Oct"]["enrolled_list"] = [["Lead b", "+91 00000 00002"], ["Lead a", "9999999999"]]
    rep = Report(cfg, "fri", T("2026-10-30 12:00"))
    sheet_only, lsq_only, by_name, checked = rep.gaps
    assert [l["lead_id"] for l, _ in sheet_only] == ["b"] and not lsq_only and [l["lead_id"] for l, _, _ in by_name] == ["a"]
    doc = rep.html()
    assert "Pool sheet shows enrolled" in doc and "disagree on 1 leads" in doc and "correct the sheet" in doc
    assert Report(cfg, "wed", T("2026-10-28 20:00")).gaps == ([], [], [], [])   # a newer list is not used for an older cut-off


def test_judgement_must_agree_with_calls():
    l = {"calls": [], "assigned": T("2026-10-26 11:00"), "due": T("2026-10-27 13:00"), "enrolled_at": None, "lost_at": None, "stages": [],
         "bootcamp": "Technology Law 24 Oct", "day": 1, "phone": "x", "caller": "y"}
    with pytest.raises(ValueError, match="real talks"):
        row(l, T("2026-10-28 20:00"), {**J}, POOL, 3000)       # hot without a single real talk


def test_mode_groups():
    assert mode_of("Loan (finance partner) · documents shared") == "Loan"
    assert mode_of("Card EMI or loan · undecided") == "Undecided"
    assert mode_of("3 instalments offered; 4 asked") == "Instalments"
    assert mode_of("—") == "No mode yet"


def test_header_shows_when_leadsquared_was_read_not_a_later_cut_off(tmp_path):
    cfg = cycle(tmp_path)
    j = json.loads((tmp_path / "w1" / "j.json").read_text())
    j["c"] = {"2026-10-28": {"status": "notreached", "why": "Not called", "next": "Call now"}}
    (tmp_path / "w1" / "j.json").write_text(json.dumps(j))
    meta = tmp_path / "w1" / "meta.json"
    meta.write_text(json.dumps({**json.loads(meta.read_text()), "fetched_at_utc": "2026-10-28 13:40:00"}))
    doc = Report(cfg, "wed", T("2026-10-28 20:00")).html()
    assert "stages up to Wed 28 Oct 2026, 19:10 IST" in doc
