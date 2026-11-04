from __future__ import annotations

import json

import openpyxl
import responses

from analytics import collection_reports
from analytics.collections.pool_mis import load_mis, mis_tab, read_mis, refresh_pool

HEAD = [None, None, "Bootcamp Name", "Bootcamp Name 2", "Lawsikho / SkillArbitrage", "Total Enrollments", "Daywise Enrollments",
        "Price", "Pool Size", "Total Recovered enrollments", "Total Pending leads", "Drop leads", "Dropped leads %",
        "Recovered amount (Total)", "Total recovery % (Casewise)", "Total recovery % (Revenuewise)", "Pending amount (Total)",
        "Dropped amount"]


def mis_row(side, name, n, price, rec, pend, drop, rec_amt, pend_amt, drop_amt):
    return [None, side, name, name, "Lawsikho", n, "", price, 0, rec, pend, drop, 0, rec_amt, 0, 0, pend_amt, drop_amt]


TECH = "Bootcamp | Technology Law - October 24, October 25, October 26, 2026"
SENIOR = "Bootcamp | AI for Business Growth | Senior Professionals (RE) - Oct 31, Nov 1, Nov 2, 2026"
ROWS = [[None] * 5, [None, None, "Bootcamp Pool MIS - 03-Nov-2026"], HEAD,
        mis_row("Mayur", TECH, 57, "Day 1- 59600  Day 2- 60400", 38, 16, 3, 1947511.71, 906454.5, 171400),
        mis_row("Mayur", SENIOR, 6, "Day 1- 55000  Day 2- 50700", 2, 4, 0, 110578.46, 208000, 0),
        [None, None, None, "Changemakers Total", None, 63],
        mis_row("Anmol", SENIOR, 6, "Day 1- 49600  Day 2- 50700", 1, 5, 0, 64376.98, 260000, 0)]


def test_mis_tab_picks_the_month_from_cut_short_names():
    names = ["Main MIS Teamwise Split - Oct26", "Copy of Main MIS Teamwise Split", "Main MIS Teamwise Split - Nov26",
             "Main MIS Teamwise Split - June2", "Mastersheet"]
    assert mis_tab(names, "November 2026") == "Main MIS Teamwise Split - Nov26"
    assert mis_tab(names, "June 2026") == "Main MIS Teamwise Split - June2"
    assert mis_tab(names, "December 2026") is None


def test_read_mis_skips_totals_and_reads_prices():
    as_of, rows = read_mis(ROWS)
    assert as_of == "3 Nov 2026" and [r["side"] for r in rows] == ["Mayur", "Mayur", "Anmol"]
    assert rows[0]["price"] == [59600, 60400] and rows[0]["recovered"] == 38 and rows[0]["day"] == "2026-10-24"
    assert rows[1]["course"] == "AI for Business Growth | Senior Professionals (RE)"


def test_refresh_pool_updates_only_confident_matches_and_keeps_prices():
    _, rows = read_mis(ROWS)
    pool = {"as_of": "29 Oct 2026", "lists_as_of": "2026-10-30",
            "bootcamps": {"Technology Law 24 Oct": {"price": [1, 2], "recovered": 1, "enrolled_list": [["A", "1"]]},
                          "Senior AI 31 Oct": {"price": [55000, 50700], "recovered": 0},
                          "Patent Law & Analysis 24 Oct": {"price": [3, 4], "recovered": 7}}}
    notes = refresh_pool(pool, rows, "3 Nov 2026", "Mayur")
    b = pool["bootcamps"]
    assert b["Technology Law 24 Oct"]["recovered"] == 38 and b["Technology Law 24 Oct"]["price"] == [1, 2]
    assert b["Technology Law 24 Oct"]["enrolled_list"] == [["A", "1"]] and b["Technology Law 24 Oct"]["dropped_amount"] == 171400
    assert b["Senior AI 31 Oct"]["recovered"] == 2                     # Mayur's row, not Anmol's
    assert b["Patent Law & Analysis 24 Oct"]["recovered"] == 7 and "Patent Law & Analysis 24 Oct: kept" in notes[0]
    assert pool["as_of"] == "3 Nov 2026" and pool["lists_as_of"] == "2026-10-30"
    anmol = {"as_of": "", "bootcamps": {"Senior AI 31 Oct": {"price": [55000, 50700]}}}
    refresh_pool(anmol, rows, "3 Nov 2026", "Puja / Anmol")
    assert anmol["bootcamps"]["Senior AI 31 Oct"]["recovered"] == 1


@responses.activate
def test_wednesday_friday_build_refreshes_the_config_from_the_downloaded_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTCAMP_POOL_SHEET_ID", "boot")
    wb = openpyxl.Workbook()
    wb.active.title = "Mastersheet"
    ws = wb.create_sheet("Main MIS Teamwise Split - Nov26")
    for r in ROWS:
        ws.append(r)
    wb.save(tmp_path / "book.xlsx")
    responses.add(responses.GET, "https://docs.google.com/spreadsheets/d/boot/export?format=xlsx",
                  body=(tmp_path / "book.xlsx").read_bytes(), status=200)
    cfg = {"side": "Mayur", "month": "November 2026", "pool": {"as_of": "29 Oct 2026",
                                                               "bootcamps": {"Technology Law 24 Oct": {"price": [1, 2]}}}}
    path = tmp_path / "report_config_mayur.json"
    path.write_text(json.dumps(cfg))
    built = []
    monkeypatch.setattr(collection_reports, "Report", lambda c, k, t: built.append(c) or type("R", (), {"html": lambda s: "", "rows": []})())
    monkeypatch.setattr(collection_reports, "write_pdf", lambda doc, out: None)
    collection_reports.main("wed", str(path), "2026-11-04 20:00", str(tmp_path / "out.pdf"))
    saved = json.loads(path.read_text())
    assert built[0]["pool"]["bootcamps"]["Technology Law 24 Oct"]["recovered"] == 38
    assert saved["pool"]["as_of"] == "3 Nov 2026" and saved["pool"]["read_at_ist"]
    assert (tmp_path / "bootcamp_pool.xlsx").exists()
    collection_reports.main("wed", str(path), "2026-11-04 20:00", str(tmp_path / "out.pdf"), saved=True)
    assert len(responses.calls) == 1
    _, _, rows = load_mis(str(tmp_path / "bootcamp_pool.xlsx"), "November 2026")
    assert len(rows) == 3
