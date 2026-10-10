from analytics import collection_forecast as cf


def row(lid, month_end="Likely", stuck="Choosing how to pay", chance=0.3, kind="Bootcamp", caller="Asha Rao"):
    return {"lead_id": lid, "kind": kind, "team": "Elite Changemakers", "caller": caller, "course": "Independent Director",
            "days_open": "6", "stage": "Call Back Later", "name": lid.title(), "month_end": month_end, "stuck_at": stuck,
            "situation": "s", "objection": "Wants 6-month EMI", "month_end_reason": "r", "next_action": "n",
            "blocker": "Wants a loan or EMI option", "chance_21d": chance, "chance_21d_low": chance, "chance_21d_high": chance}


def test_order_puts_the_closest_to_paying_first():
    rows = [row("c", "Possible"), row("b", "Likely", "Not yet counselled", 0.9), row("a", "Likely", "Payment link sent, not paid", 0.1)]
    assert [r["lead_id"] for r in sorted(rows, key=cf.order_key)] == ["a", "b", "c"]


def test_forecast_keeps_past_rates_and_reading_apart():
    f = cf.forecast([row("a", "Very likely", chance=1.0), row("b", "Likely", chance=0.0), row("c", "Unlikely", chance=0.0)])
    assert (f["open"], f["reading"], f["expected"], f["low"], f["high"]) == (3, 2, 1.0, 1, 1)


def test_html_has_forecast_targets_and_every_lead():
    rows = [row("a", "Very likely", chance=0.5), row("b", "Unlikely", chance=0.1, kind="Community", caller="Ravi Das")]
    report = {"by_kind_team": [{"kind": "Bootcamp", "team": "Elite Changemakers", "pool": 10, "collected": 6},
                               {"kind": "Community", "team": "Elite Changemakers", "pool": 5, "collected": 1}],
              "new_bookings": [{"kind": "Bootcamp", "team": "Elite Changemakers", "weekly_bookings": [8, 10], "expected": 4.2,
                                "range": "2-7", "weeks": ["10 Oct: ~9 bookings x 50% within 19 days"]}]}
    page = cf.build_html("Elite Changemakers", rows, report, "2026-10-10 18:30", 0)
    assert "for Mayur Sachdeva" in page and "Very likely to pay by 31 Oct · 1 leads" in page
    assert "Unlikely to pay by 31 Oct · 1 leads" in page and "Wants 6-month EMI" in page
    assert "6 of 10" in page and "10 Oct: ~9 bookings" in page and "Ravi Das" in page


def test_target_range_runs_from_the_lower_to_the_higher_view_and_patterns_show():
    assert cf.target_range({"expected": 21.6, "reading": 15}) == "15–22"
    assert cf.target_range({"expected": 4.2, "reading": 4}) == "4"
    page = cf.build_html("Elite Changemakers", [row("a")], {"by_kind_team": []}, "2026-10-10 18:30", 0,
                         [{"title": "Loan files stall.", "text": "Review them daily."}])
    assert "What the calls show" in page and "Loan files stall." in page
