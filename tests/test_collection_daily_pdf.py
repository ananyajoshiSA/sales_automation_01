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


def test_team_leader_money_from_the_callerwise_mis():
    rows = [{"bootcamp": "Bootcamp | US Accounting & Bookkeeping - Nov 14, Nov 15, Nov 16 , 2026", "caller": "Yamini Hitesh Billore",
             "all_teams": 10, "recovered": 6, "pending": 3, "dropped": 1, "recovered_amount": 100.0, "pending_amount": 50.0},
            {"bootcamp": "Bootcamp | US Accounting & Bookkeeping - Nov 14, Nov 15, Nov 16 , 2026", "caller": "Rahul Kumar",
             "all_teams": 5, "recovered": 1, "pending": 4, "dropped": 0, "recovered_amount": 10.0, "pending_amount": 40.0}]
    got = pdf.tl_mis(rows, {"Yamini Billore"})
    assert [(r["all_teams"], r["recovered"], r["day"]) for r in got] == [(10, 6, "2026-11-14")]
    assert pdf.same_person("Yamini Billore", "Yamini Hitesh Billore") and not pdf.same_person("Rahul Kumar", "Rahul Sharma")


def test_management_recovery_compares_settled_bootcamps_only():
    from analytics.collection_daily.management import recovery, settled
    rows = [{"bootcamp": "Bootcamp | Law - October 24, October 25, October 26, 2026", "caller": "Yamini Hitesh Billore",
             "all_teams": 10, "recovered": 6},
            {"bootcamp": "Bootcamp | Law - Nov 14, Nov 15, Nov 16 , 2026", "caller": "Yamini Billore", "all_teams": 10, "recovered": 1},
            {"bootcamp": "Bootcamp | Law - October 24, October 25, October 26, 2026", "caller": "Someone Else", "all_teams": 5, "recovered": 0}]
    old = settled(rows, date(2026, 11, 25))
    assert [r["recovered"] for r in old] == [6, 0]  # the 14 Nov bootcamp is still being collected
    assert recovery(old, ["Yamini Billore"]) == 60
    assert recovery(old, ["Nobody Here"]) is None


def test_management_untouched_skips_fresh_cold_and_waiting_leads():
    from analytics.collection_daily.management import untouched
    t = lambda d, h=10: datetime(2026, 11, d, h, tzinfo=IST)
    leads = [{"lead_id": "fresh", "allocated": t(24), "last_dial": None},      # given yesterday: a fresh lead, not untouched
             {"lead_id": "never", "allocated": t(18), "last_dial": None},      # given a week ago, never dialled
             {"lead_id": "old", "allocated": t(10), "last_dial": t(21, 19)},   # nothing on 22, 23 or 24 Nov
             {"lead_id": "recent", "allocated": t(10), "last_dial": t(22, 9)},
             {"lead_id": "cold", "allocated": t(10), "last_dial": None},
             {"lead_id": "wait", "allocated": t(10), "last_dial": None}]
    js = {"cold": {"status": "cold"}, "wait": {"status": "counsel", "waiting_until": "2026-12-01"}}
    rows = [(l, js.get(l["lead_id"], {"status": "hot"}), {}) for l in leads]
    assert untouched(leads, rows, date(2026, 11, 25)) == 2


def test_management_reached_payment_never_counts_cold():
    from analytics.collection_daily.management import reached_payment
    assert reached_payment({"status": "hot", "payment": None})
    assert reached_payment({"status": "counsel", "payment": "Bajaj loan, docs on Friday"})
    assert not reached_payment({"status": "connected", "payment": "undecided"})
    assert not reached_payment({"status": "cold", "payment": "Bajaj loan"})


def test_management_leads_dialled_counts_each_lead_once():
    from analytics.collection_daily.management import leads_dialled
    t = lambda d, h: datetime(2026, 11, d, h, tzinfo=IST)
    call = lambda by, when, out=True: {"by": by, "t": when, "out": out}
    leads = [{"calls": [call("Asha", t(24, 10)), call("Asha", t(24, 12)), call("Asha", t(24, 15))]},
             {"calls": [call("Asha", t(23, 10)), call("Ravi", t(24, 11))]},
             {"calls": [call("Asha", t(24, 9), out=False)]},
             {"calls": [call("Asha", t(24, 18))]}]
    assert leads_dialled(leads, "Asha", date(2026, 11, 24)) == 2


def test_management_cash_moves_counts_only_leads_in_the_earlier_sheet():
    from analytics.collection_daily.management import cash_moves, short
    b = "Bootcamp | Independent Director - Nov 14, Nov 15, Nov 16 , 2026"
    row = lambda phone, got, owner="Rahul Kumar": {"phone": phone, "bootcamp_raw": b, "received": got, "owner": owner}
    old = [row("1", 5000), row("2", 5000), row("3", 5000)]
    new = [row("1", 25000), row("2", 5000), row("3", 4000), row("4", 5000, "Shweta Pal")]  # 4: added since, booking fee only
    assert sorted(cash_moves(new, old), key=lambda m: m[2]) == [("Rahul Kumar", b, -1000), ("Rahul Kumar", b, 20000)]
    assert short(b) == "Independent Director · 14 Nov"


def test_management_cash_moves_matches_a_lead_whose_month_was_mistyped_in_the_earlier_sheet():
    from analytics.collection_daily.management import cash_moves
    b = "Bootcamp | Global Academic Writers - November 21, November 22, November 23, 2026"
    old = [{"phone": "1", "bootcamp_raw": b, "received": 3000, "owner": "Aleena Khan", "month": "2071-11"}]  # 24 Nov sheet: year mistyped
    new = [{"phone": "1", "bootcamp_raw": b, "received": 43170, "owner": "Aleena Khan", "month": "2026-11"}]
    assert cash_moves(new, old) == [("Aleena Khan", b, 40170)]


MORNING_HTML = ('<div class="tile"><b>5</b><span>enrolled on Tuesday (LeadSquared stage)</span></div>'
                '<div class="tile"><b>19 of 127</b><span>real talks (2 min+) of dials on Tuesday</span></div>'
                '<div class="tile"><b>82%</b><span>48-hour score: real talk within 48 h, 154 of 187 leads past their deadline (target 90%)</span></div>'
                '<div class="tile"><b>68</b><span>open leads to call today · 7 follow-ups first</span></div>'
                '<h2>1 · Will pay today or already overdue (12) · ₹671,400 to collect</h2>'
                '<tr><td>Follow-ups kept (call-backs saved in LeadSquared or agreed on a call, due that day)</td><td>0 of 2</td><td><b>0 of 0</b></td></tr>'
                '<tr><td>Missed calls from leads, not called back</td><td>2 of 5</td><td><b>4 of 7</b></td></tr>'
                '<h2>Team performance by caller (Tue 24 Nov)</h2><table><tr><th>Caller</th></tr>'
                '<tr><td>Rahul Kumar</td><td class=n>46</td><td class=n>24%</td><td class=n>2</td></tr></table>')


def test_checks_read_the_morning_report_numbers():
    from analytics.collection_daily.checks import morning_numbers
    got = morning_numbers(MORNING_HTML)
    assert (got["enrolled"], got["real"], got["dials"], got["met48"], got["past48"], got["open"]) == (5, 19, 127, 154, 187, 68)
    assert (got["due_n"], got["due_rs"], got["paid"], got["fu"], got["in"]) == (12, 671400.0, 0, (0, 0), (4, 7))
    assert got["callers"] == {"Rahul Kumar": (46, 2)}


def test_a_team_leaders_section_counts_calls_on_any_team_lead_so_it_adds_up_to_the_team():
    day = date(2026, 11, 19)
    base = dict(side="Mayur", ad="AD", follow_ups=[], deadline=ist("2026-11-20 20:00"), met48=True, enrolled_at=None)
    a = lead(**base, lead_id="a", tl="T1", caller="Asha", calls=[call("2026-11-18 11:00", by="Asha"), call("2026-11-18 12:00", by="Ravi")])
    b = lead(**base, lead_id="b", tl="T2", caller="Ravi", calls=[call("2026-11-18 13:00", by="Ravi")])
    args = ([a, b], [], {}, ist("2026-11-19 08:30"), day, {}, ("", []), {}, {})
    team, t2 = pdf.TeamPDF("Mayur", *args), pdf.TeamPDF("Mayur", *args, tl="T2", callerwise=[])
    assert t2.stats["Ravi"]["dials"] == 2 and set(t2.stats) == {"Ravi"}
    assert sum(s["dials"] for s in team.stats.values()) == 3


def test_management_singular_forms():
    from analytics.collection_daily.management import singular
    assert singular("1 leads have paid; 11 leads; 21 leads are; the 1 callers who have had; after 1 real talks") == \
        "1 lead has paid; 11 leads; 21 leads are; the 1 caller who has had; after 1 real talk"
