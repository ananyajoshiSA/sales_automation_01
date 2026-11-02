from datetime import date

from analytics.collections.weekly import in_week, last_week


def test_last_week_is_previous_monday_to_sunday():
    assert last_week(date(2026, 11, 2)) == date(2026, 10, 26)
    assert last_week(date(2026, 11, 8)) == date(2026, 10, 26)


def test_in_week_keeps_only_that_week():
    rows = [{"date": "2026-10-25"}, {"date": "2026-10-26"}, {"date": "2026-11-01"}, {"date": "2026-11-02"}, {}]
    assert [r["date"] for r in in_week(rows, date(2026, 10, 26))] == ["2026-10-26", "2026-11-01"]


def test_sheet_id_accepts_a_full_url(monkeypatch):
    from integrations.gsheets import sheet_id
    monkeypatch.setenv("X_SHEET_ID", "https://docs.google.com/spreadsheets/d/abc_123-XY/edit#gid=0")
    assert sheet_id("X_SHEET_ID") == "abc_123-XY"
