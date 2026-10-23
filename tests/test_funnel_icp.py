from __future__ import annotations

import json
import os

import openpyxl

from analytics import funnel_icp as fi

MAP = {"icps": ["Retired", "Teachers / educators", "General"],
       "pages": {"ret-a": "Retired", "edu-a": "Teachers / educators"}}


def lead(phone, slug, at, email=""):
    return {"whatsapp": phone, "email": email, "pageUrl": f"https://x/f/{slug}?utm=1", "funnel": slug,
            "captured_ist": at}


def test_first_funnel_wins_and_sales_match_by_phone_then_email():
    leads = [lead("+91 98765 43210", "edu-a", "2026-10-01T10:00"),
             lead("9876543210", "ret-a", "2026-10-02T10:00"),
             lead("9000000001", "ret-a", "2026-10-03T10:00", "A@B.in"),
             lead("9000000002", "new-page", "2026-10-03T11:00")]
    sales = [{"phone": "9876543210", "email": "", "paid": 100.0},
             {"phone": "1111111111", "email": "a@b.in", "paid": 50.0},
             {"phone": "2222222222", "email": "", "paid": 10.0}]
    r = fi.build(leads, sales, MAP)
    assert r["people"] == 3
    assert r["icp"]["Teachers / educators"] == {"leads": 1, "sales": 1, "paid": 100.0}
    assert r["icp"]["Retired"] == {"leads": 1, "sales": 1, "paid": 50.0}
    assert r["icp"][fi.UNMAPPED]["leads"] == 1
    assert r["matched"] == {"phone": 1, "email": 1, "not in GrowthX": 1}


def test_read_sales_keeps_sale_statuses_and_float_phones(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bootcamp | X"
    ws.append(["Bootcamp Lead", "Date", "Student name", "Phone No.", "Email Id", "Money received ",
               "Status of the lead"])
    ws.append(["", "", "A", 919876543210.0, "A@x.in", 5000, "Disbursed"])
    ws.append(["", "", "B", 919000000000.0, "", 0, "Not interested"])
    path = os.path.join(tmp_path, "s.xlsx")
    wb.save(path)
    assert fi.read_sales(path, "Bootcamp | X") == [{"phone": "9876543210", "email": "a@x.in", "paid": 5000.0}]


def test_shipped_maps_use_only_their_own_icps():
    for name in os.listdir(fi.MAPS_DIR):
        m = json.load(open(os.path.join(fi.MAPS_DIR, name), encoding="utf-8"))
        assert set(m["pages"].values()) <= set(m["icps"]), name
