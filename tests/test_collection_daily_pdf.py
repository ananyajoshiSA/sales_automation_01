from datetime import date, datetime

from analytics.collection_daily import pdf
from analytics.collection_daily.review import day_stats, stats_line
from integrations.timeutil import IST


def ist(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)


def test_route_from_the_readers_payment_text():
    assert pdf.route("Loan (Bajaj) · docs by Thu 19 Nov") == "Loan / finance"
    assert pdf.route("Card EMI 18 months · Wed 18 Nov") == "Card EMI"
    assert pdf.route("One shot ~48k · by Wed 18 Nov") == "One shot"
    assert pdf.route("3 instalments, first 20 Nov") == "Instalments"
    assert pdf.route("—") == pdf.route("Undecided (finance, card explained)") == pdf.route(None) == "Not decided"


def test_promised_date_skips_since_dates_and_impossible_ones():
    assert pdf.promised("Loan · bank statement by Wed 18 Nov", 2026) == date(2026, 11, 18)
    assert pdf.promised("Loan · documents pending since 17 Nov", 2026) is None
    assert pdf.promised("One shot · salary 1 Jan", 2026) == date(2027, 1, 1)
    assert pdf.promised("pay by 31 Nov", 2026) is None


def test_objection_and_iit_groups():
    assert pdf.objection_kind("Asked about a refund of the booking fee") == "Refund or cancel"
    assert pdf.objection_kind("No credit card; age blocks loan EMI") == "No card or loan route"
    assert pdf.objection_kind("Wants seat on hold six months") == "Wants later or no time"
    assert pdf.iit_kind("Not pitched") == "Not pitched"
    assert pdf.iit_kind("Pitched 17 Nov: declined, budget") == "Declined"
    assert pdf.iit_kind("Pitched 16 Nov: interested, will add to card EMI") == "Interested"
    assert pdf.iit_kind("Pitched 18 Nov: will think about it") == "Pitched, undecided"
    assert pdf.iit_kind(None) == "Not read yet"


def call(t, answered=True, dur=200, out=True, by="Ankita"):
    return {"t": ist(t), "answered": answered, "dur": dur, "out": out, "by": by}


def lead(**kw):
    base = {"caller": "Ankita", "calls": [], "follow_ups": [], "deadline": ist("2026-11-18 20:00"), "met48": True, "enrolled_at": None}
    return base | kw


def test_day_result_and_day_stats():
    day = date(2026, 11, 18)
    talked = lead(calls=[call("2026-11-18 11:00", dur=300), call("2026-11-18 12:00", answered=False)])
    short = lead(calls=[call("2026-11-18 11:00", dur=40)], met48=False)
    rang = lead(calls=[call("2026-11-18 10:00", answered=False, out=False)], deadline=ist("2026-11-19 20:00"))
    assert [pdf.day_result(l, day) for l in (talked, short, rang)] == ["Real talk", "Short pick-up", "Not called"]
    s = day_stats([talked, short, rang], day)["Ankita"]
    assert (s["dials"], s["answered"], s["real"], s["talk_secs"]) == (3, 2, 1, 340)
    assert (s["d48_due"], s["d48_met"], s["in_missed"], s["in_unreturned"]) == (2, 1, 1, 1)
    assert "3 dials · 67% answered · 1 real talks" in stats_line(s) and "11:00–12:00" in stats_line(s)
