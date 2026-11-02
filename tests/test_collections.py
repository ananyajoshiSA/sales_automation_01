from __future__ import annotations

import json

import openpyxl
import pytest
import responses

from analytics.collections import render
from analytics.collections.__main__ import reports
from analytics.collections.match import (NOT_PAIRED, OTHER, LeadIndex, assign_community, community_type, disagreement,
                                         match_students, pair_bootcamps)
from analytics.collections.sheets import money, month_of, outcome, phone10, read_bootcamp, read_community
from analytics.collections.tags import is_collection_tag, tag_date, tag_kind
from integrations.gsheets import SheetError, download_xlsx
from scripts.fetch_collections import wanted_tags

COMMUNITY_HEAD = ["Name", "Email Id", "Contact No", "Date", "Month", "Community Name", "Course Paid For",
                  "Enrollment Stage", "Course price R.s", "Total Amount Paid", "Amount Paid", "Subvention", "Discount",
                  "Pending Revenue", "Pending Status", "Owner", "Team", "Verticals", "Revised Team", "Lead Stage"]
BOOT_HEAD = ["Month", "Start Date", "End Date", "Bootcamp Name", "Bootcamp Lead", "Manual/Online Enrollments",
             "TLs Name", "Date", "Student name ", "Phone No.", "Email Id",
             "Financial status (LawSikho/Bajaj/Credence/Eduvanz)", "Course price ", "Money received ", "Discount",
             "Subvention cost", "Balance due ", "Total Month for Enach", "Status of the lead",
             "Counselor Name For Claim Leads", "Elite Changemakers", "Counselor Comments level 1",
             "Status of the lead level 1"]


def crow(name, phone, date, community, price, paid, balance, status, owner="Asha", email=""):
    return [name, email, phone, date, "", community, "Course", "Booking fees", price, paid, paid, 0, 0, balance,
            status, owner, "Changemakers", "Team Mayur", "Team Anjali", "Call Back Later"]


def brow(name, phone, month, start, camp, price, received, balance, status, collector="Ravi"):
    return [month, start, "", camp, "Bootcamp Lead", "Online", "Sonal Tripathi", "", name, phone, "", "LawSikho",
            price, received, 0, 0, balance, "", status, "", collector, "", "Follow Up For Closure"]


def community_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Combined_Sheet_Aquib"
    ws.append(COMMUNITY_HEAD)
    ws.append(crow("A One", "9000000001.0", "2026-05-03", "4/SQE/community", 60000, 2000, 58000, "Pending"))
    ws.append(crow("B Two", "9000000002", "2026-08-04", "57/CONTRACT/DRAFTING", 58000, 58000, 0, "Disbursed"))
    aug = wb.create_sheet("August 2026")
    aug.append(COMMUNITY_HEAD)
    aug.append(crow("B Two", "9000000002", "2026-08-04", "57/CONTRACT/DRAFTING", 58000, 58000, 0, "Disbursed"))
    aug.append(crow("C Three", "+91 90000 00003", "2026-08-09", "88/Independent Director Community", 63000, 2000,
                    61000, "Drop"))
    nov = wb.create_sheet("November 2025")
    nov.sheet_state = "hidden"
    nov.append(COMMUNITY_HEAD)
    nov.append(crow("D Four", "9000000004", "#N/A", "Legal AI", 50000, 20000, 30000, "Installments"))
    mis = wb.create_sheet("Snap")
    mis.append(["Community Pool MIS"])
    mis.append(["Community Name", "Total enrollments", "Pool Size"])
    wb.save(path)


def bootcamp_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pivot"
    ws.append(["Total", "Drop"])
    m = wb.create_sheet("Mastersheet")
    m.append(BOOT_HEAD)
    camp = "Bootcamp | US Accounting & Bookkeeping - October 24, October 25, 2026"
    m.append(brow("E Five", "919000000005", "October '2026", "24th October'26", camp, 57000, 2000, 55000, "Booking fees received"))
    m.append(brow("F Six", "9000000006", "October '2026", "24th October'26", camp, 57000, 57000, 0, "One shot"))
    m.append(brow("G Seven", "9000000007", "October '2026", "31st October'26", "Strategic HR Bootcamp (Oct 31)", 50000,
                  5000, 45000, "Installments"))
    m.append(["November '2026", "31st October'26"] + [None] * (len(BOOT_HEAD) - 2))
    wb.save(path)


LEADS = [
    ("Community Webinar Collections", {"ProspectID": "L1", "Phone": "+91-9000000001", "FirstName": "A",
                                       "OwnerIdName": "Asha", "OwnerId": "U1", "ProspectStage": "Booking fees received",
                                       "mx_Funnel_name": "4/SQE/community"}),
    ("Community Webinar Collections", {"ProspectID": "L2", "Phone": "9000000002", "OwnerIdName": "Asha", "OwnerId": "U1",
                                       "ProspectStage": "Not Interested", "mx_Funnel_name": "31/CONTRACT/DRAFTING"}),
    ("Community Webinar Collections", {"ProspectID": "L9", "Phone": "9000000099", "FirstName": "Z",
                                       "OwnerIdName": "Asha", "ProspectStage": "Loan pending"}),
    ("US Accounting Bootcamp Collection - 24th October'26", {"ProspectID": "L5", "Phone": "9000000005",
                                                              "ProspectStage": "Booking fees received", "OwnerIdName": "Ravi"}),
    ("US Accounting Bootcamp Collection - 24th October'26", {"ProspectID": "L6", "Mobile": "9000000006",
                                                              "ProspectStage": "Collections done", "OwnerIdName": "Ravi"}),
    ("Strategic HR Bootcamp Collection - 31st October'26", {"ProspectID": "L8", "Phone": "9111111111",
                                                             "ProspectStage": "Booking fees received"}),
]


def test_tags():
    assert tag_date("Global Patent Analyst Bootcamp Collection - 13Th December'25") == "2025-12-13"
    assert tag_date("AI for Business Growth | Women (Re) - Oct 17, Oct 18, Oct 19, 2026)") == "2026-10-17"
    assert tag_date("23th May'26") == "2026-05-23"
    assert tag_date("Community Webinar Collections") == ""
    assert tag_kind("Community Webinar Collections-31stmay") == "community"
    assert tag_kind("AI for Business Growth | Women (Re) - Oct 17, 2026") == "bootcamp"
    assert not is_collection_tag("Funnel Direct Payments")
    tags = ["Funnel Direct Payments", "Community Webinar Collections", "X Bootcamp Collection - 4th October'25",
            "Y Bootcamp Collection - 5th November'25"]
    assert wanted_tags(tags, "2025-11") == ["Community Webinar Collections", "Y Bootcamp Collection - 5th November'25"]


def test_cell_cleaning():
    assert phone10("9030369779.0") == "9030369779"
    assert phone10("918105863313") == "8105863313"
    assert phone10("12345") == ""
    assert money("61,000.00") == 61000.0 and money("#N/A") == 0.0
    assert month_of("April '2026") == "2026-04" and month_of("9/26/2026 12:02:08 PM") == "2026-09"
    assert [outcome(s) for s in ("Disbursed", "One shot", "Installments", "Booking fees received", "Drop", "")] == \
        ["Collected", "Collected", "Instalments", "Pending", "Dropped", "Not stated"]


def test_community_type():
    assert community_type("57/CONTRACT/DRAFTING") == "Contract Drafting"
    assert community_type("22 – Independent Director Community") == "Independent Director"
    assert community_type("Criminal Lit(31St Oct 8Th Dec 2025) Week 1") == "Criminal Litigation"
    assert community_type(": AI FOR LEGAL - Group 25 & 26, Week 1") == "Legal AI"
    assert community_type("") == OTHER and community_type("12/") == OTHER


def test_read_community_uses_each_month_once(tmp_path):
    p = tmp_path / "c.xlsx"
    community_book(p)
    rows, note = read_community(str(p))
    by = {r["name"]: r for r in rows}
    assert sorted(by) == ["A One", "B Two", "C Three", "D Four"]          # B Two listed twice, counted once
    assert by["B Two"]["tab"] == "August 2026"                             # the month's own tab wins
    assert by["A One"]["tab"] == "Combined_Sheet_Aquib"                    # a month with no tab of its own
    assert by["D Four"]["month"] == "2025-11" and by["D Four"]["hidden"]   # hidden tab, month from its name
    assert by["C Three"]["phone"] == "9000000003" and by["C Three"]["outcome"] == "Dropped"
    assert note["month_sources"]["2026-08"] == "August 2026"
    assert note["skipped"]["repeated in Combined_Sheet_Aquib"] == 1


def test_read_bootcamp(tmp_path):
    p = tmp_path / "b.xlsx"
    bootcamp_book(p)
    rows, note = read_bootcamp(str(p))
    assert [r["name"] for r in rows] == ["E Five", "F Six", "G Seven"]
    assert note["skipped"] == {"rows with no student (bootcamp and dates only)": 1}
    assert rows[0]["phone"] == "9000000005" and rows[0]["start_date"] == "2026-10-24" and rows[0]["month"] == "2026-10"
    assert rows[0]["owner"] == "Ravi" and rows[0]["team"] == "Sonal Tripathi"


def test_matching_and_pairing(tmp_path):
    index = LeadIndex(LEADS)
    rows = [{"phone": "9000000005", "email": ""}, {"phone": "", "email": "nobody@x.in"},
            {"phone": "9000000006", "email": ""}]
    match_students(rows, index, "bootcamp")
    assert rows[0]["lsq_id"] == "L5" and rows[0]["matched_by"] == "phone"
    assert rows[1]["lsq_id"] == "" and rows[1]["match_note"] == "no lead with this phone or email"
    assert rows[2]["lsq_id"] == "L6"                                       # alternate number
    shared = LeadIndex([("Community Webinar Collections", {"ProspectID": "A", "Phone": "9222222222"}),
                        ("Community Webinar Collections", {"ProspectID": "B", "Phone": "9222222222"})])
    assert shared.find("9222222222", "", "community") == (None, "phone shared by 2 leads")

    p = tmp_path / "b.xlsx"
    bootcamp_book(p)
    boot, _ = read_bootcamp(str(p))
    match_students(boot, index, "bootcamp")
    pairs = pair_bootcamps(boot, [t for t, _ in LEADS])
    us = next(v for k, v in pairs.items() if k[0].startswith("Bootcamp | US"))
    assert us["tag"] == "US Accounting Bootcamp Collection - 24th October'26" and "2 of 2" in us["how"]
    hr = next(v for k, v in pairs.items() if k[0].startswith("Strategic HR"))
    assert hr["tag"] == "Strategic HR Bootcamp Collection - 31st October'26" and hr["how"].startswith("same bootcamp date")
    nothing = pair_bootcamps([{"bootcamp_raw": "Odd camp", "start_date": "2026-01-01", "lsq_tag": ""}], [t for t, _ in LEADS])
    assert list(nothing.values())[0]["tag"] == NOT_PAIRED


def test_community_type_prefers_funnel_name_and_flags_disagreement():
    rows = [{"lsq_funnel": "31/CONTRACT/DRAFTING", "community_raw": "57/CONTRACT/DRAFTING"},
            {"lsq_funnel": "", "community_raw": "4/SQE/community"},
            {"lsq_funnel": "88/Independent Director Community", "community_raw": "Legal AI"},
            {"lsq_funnel": "", "community_raw": ""}]
    assign_community(rows)
    assert [r["community_type"] for r in rows] == ["Contract Drafting", "SQE", "Independent Director", OTHER]
    assert [r["community_differs"] for r in rows] == [False, False, True, False]


def test_disagreements():
    base = {"lsq_id": "L", "balance": 0}
    assert "LeadSquared stage is Not Interested" in disagreement({**base, "outcome": "Collected", "lsq_stage": "Not Interested"})
    assert disagreement({**base, "outcome": "Dropped", "lsq_stage": "Course Enrolled"}).startswith("Sheet says dropped")
    assert "Rs 5,000 still due" in disagreement({**base, "outcome": "Pending", "balance": 5000, "lsq_stage": "Collections done"})
    assert disagreement({**base, "outcome": "Collected", "lsq_stage": "Collections done"}) == ""
    assert disagreement({"lsq_id": "", "outcome": "Collected", "lsq_stage": "", "balance": 0}) == ""


def test_reports_end_to_end(tmp_path):
    community_book(tmp_path / "community.xlsx")
    bootcamp_book(tmp_path / "bootcamp.xlsx")
    with open(tmp_path / "lsq_tags.jsonl", "w") as fh:
        for tag in dict.fromkeys(t for t, _ in LEADS):
            fh.write(json.dumps({"tag": tag, "leads": [l for t, l in LEADS if t == tag]}) + "\n")
    json.dump({"fetched_at_ist": "2026-11-03 09:00", "tags": [t for t, _ in LEADS], "team_of_owner": {"U1": "Team X"}},
              open(tmp_path / "meta.json", "w"))
    R = reports(str(tmp_path))
    c, b = R["community"], R["bootcamp"]
    assert c["total"]["students"] == 4 and c["total"]["received"] == 2000 + 58000 + 2000 + 20000
    assert c["total"]["balance_open"] == 58000 + 30000 and c["total"]["balance_dropped"] == 61000
    assert [x["name"] for x in c["call_list"]] == ["A One", "D Four"]
    assert c["checks"]["matched"] == 2 and c["checks"]["not_matched_amount_received"] == 22000
    assert c["checks"]["flags"] == {"Sheet says collected": 1}                 # B Two: paid, LSQ Not Interested
    assert [m["name"] for m in c["checks"]["missing_from_sheet"]] == ["Z"]     # L9 loan pending, no sheet row
    assert b["total"]["students"] == 3 and b["checks"]["bootcamps_not_paired"] == 0
    assert b["checks"]["missing_from_sheet"][0]["lsq_id"] == "L8"
    path = render.workbook(c, str(tmp_path / "c.xlsx"))
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[:2] == ["Summary", "Who to call"] and wb["Who to call"].max_row == 3
    doc = render.html_doc(b)
    assert "Bootcamp collections" in doc and "Who to call first" in doc and "E Five" in doc


@responses.activate
def test_download_xlsx(tmp_path):
    url = "https://docs.google.com/spreadsheets/d/abc/export?format=xlsx"
    responses.add(responses.GET, url, body=b"PK\x03\x04data", status=200)
    assert download_xlsx("abc", str(tmp_path / "s.xlsx")) == 8
    responses.replace(responses.GET, url, body=b"<html>Sign in</html>", status=200)
    with pytest.raises(SheetError, match="shared"):
        download_xlsx("abc", str(tmp_path / "s.xlsx"))
