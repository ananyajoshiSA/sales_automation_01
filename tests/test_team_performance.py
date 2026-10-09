from analytics.call_markers import has_payment_step, marker_rates
from analytics.report_validation import data_checks, spot_check, verdict_numbers_check
from analytics.team_performance import analyse, plan_tracker
from analytics.team_performance_html import report_html, scorecard_cells, tracker_html

USERS = [
    {"ID": "u1", "FirstName": "Asha", "LastName": "K", "MemberOfGroups": ["Team A"]},
    {"ID": "u2", "FirstName": "Ravi", "LastName": "S", "MemberOfGroups": ["Team A", "Other"]},
    {"ID": "u3", "FirstName": "Meena", "LastName": "P", "MemberOfGroups": ["Team A"]},
    {"ID": "u4", "FirstName": "Neha", "LastName": "T", "MemberOfGroups": []},
    {"ID": "bot", "FirstName": "Webinar", "LastName": "Bot", "MemberOfGroups": ["Team A"]},
]


def call(user, lead, start, status="Answered", dur=200, direction="outbound", caller=""):
    return {"user_id": user, "caller": caller, "lead_id": lead, "start_utc": start, "status": status,
            "duration": dur, "direction": direction, "lead_number": "98765" + lead[-5:].rjust(5, "0")}


def run():
    calls = [call("u1", f"L{i}", "2026-10-05 05:00:00") for i in range(10)]
    calls += [call("u2", f"M{i}", "2026-10-05 06:00:00", dur=150) for i in range(10)]
    calls += [call("u3", f"N{i}", "2026-10-05 07:00:00", dur=130) for i in range(10)]
    calls += [call("u3", "L1", "2026-10-05 08:00:00", dur=30),                  # shorter talk on L1: u1 keeps the credit
              call("u1", "X1", "2026-10-05 09:00:00", status="NotAnswered", dur=0),
              call("u4", "Y1", "2026-10-05 09:00:00"),                           # no group -> Unassigned
              call("", "Z1", "2026-10-05 09:00:00", caller="Front Desk"),          # not a user
              call("bot", "B1", "2026-10-05 09:00:00"),                          # bot: excluded everywhere
              call("u1", "L2", "2026-10-05 10:00:00", status="NotAnswered", dur=0, direction="inbound"),
              call("u1", "L2", "2026-10-05 10:30:00", status="NotAnswered", dur=0)]
    zips = [{"CreatedOn": "2026-10-05 05:10:00", "RelatedProspectId": "L1", "mx_Custom_1": "HIGH",
             "mx_Custom_4": '{"mx_CustomObject_1":"100"}', "mx_Custom_5": '{"mx_CustomObject_1":"0"}',
             "ActivityEvent_Note": "<p>Shared the payment link on WhatsApp</p>"},
            {"CreatedOn": "2026-10-05 05:10:00", "RelatedProspectId": "Q9", "mx_Custom_1": "LOW"}]  # no call -> dropped
    enr = [{"ProspectID": "L1", "OwnerId": "u2", "enrolled_at": "2026-10-06 06:00:00"},
           {"ProspectID": "M3", "OwnerId": "u2", "enrolled_at": "2026-10-05 06:00:00"},
           {"ProspectID": "Q1", "OwnerId": "u1", "enrolled_at": "2026-10-05 06:00:00"}]  # never called that day
    return {"meta": {"date": "2026-10-05", "d0": "2026-10-05T00:00:00+05:30", "cw_end": "2026-10-08T23:59:59+05:30"},
            "users": USERS, "calls": calls, "zips": zips, "enrollments": enr, "payments": []}


def test_scorecard_credit_and_ranking():
    A = analyse(run())
    t = A["teams"]["Team A"]
    assert A["totals"]["bots_excluded"] == 1
    assert (t["callers"], t["dials"], t["inbound"], t["real"], t["reached"]) == (3, 33, 1, 30, 30)
    assert t["credited"] == 2 and t["conv_pct"] == round(100 * 2 / 30, 1)
    assert t["same_day_owner"] == 2 and A["teams"]["Unassigned"]["callers"] == 1
    assert A["rank"] == ["Team A"]                        # Unassigned / Not a user are never ranked
    assert A["totals"]["enroll_window"] == 3 and A["totals"]["enroll_credited"] == 2
    assert A["totals"]["multi_group_callers"] == 1
    asha = next(p for p in A["people"] if p["name"] == "Asha K")
    assert asha["credited"] == 1 and asha["pitch"] == 100 and asha["probe"] == 0 and asha["obj"] is None
    assert A["totals"]["zip_dropped"] == 1
    checks = {c["check"]: c["ok"] for c in data_checks(run(), A)}
    failed = [k for k, ok in checks.items() if not ok]
    assert failed == ["Zipteams included, under 5% of notes dropped"]  # 1 of 2 notes dropped


def test_as_of_caps_the_conversion_window():
    from datetime import datetime
    from analytics.team_report import IST
    A = analyse(run(), datetime(2026, 10, 5, 23, 0, tzinfo=IST))
    assert A["totals"]["enroll_window"] == 2


def test_plan_tracker_and_html():
    A = analyse(run())
    P = plan_tracker(A)
    team = next(t for t in P["teams"] if t["team"] == "Team A")
    assert team["payment_step_zip_pct"] == 100 and team["missed_inbound_leads"] == 1
    assert team["called_back_same_day_pct"] == 100 and team["median_callback_min"] == 30
    html, nums = report_html(A, None, "Validated")
    assert "Monday 5 October 2026" in html and "Parameters v1.5." in html
    assert verdict_numbers_check(nums, scorecard_cells(A))["ok"]
    assert "Parameters v1.5" in tracker_html(P)


def test_markers():
    assert has_payment_step("please complete the payment today")
    assert not has_payment_step("we discussed the fee")
    rates = marker_rates(["the fee is 40,000 and the payment link is sent", "tell me about yourself"])
    assert rates["Price / fee / EMI"] == 50 and rates["Discovery questions"] == 50


def test_gate_catches_bad_data():
    r = run()
    r["calls"].append(dict(r["calls"][0]))                                  # duplicate activity
    r["calls"][0]["activity_id"] = r["calls"][-1]["activity_id"] = "dup"
    r["calls"].append(call("u1", "W1", "2026-10-06 09:00:00"))              # next IST day
    A = analyse(r)
    failed = {c["check"] for c in data_checks(r, A) if not c["ok"]}
    assert failed == {"No duplicate activity IDs", "Zipteams included, under 5% of notes dropped"}
    assert A["totals"]["outside_window_excluded"] == 1                    # the next-day call is dropped, not counted


def test_spot_check_against_stage_history():
    A = analyse(run())
    hist = {"L1": [{"CreatedOn": "2026-10-06 06:00:00", "Data": [{"Key": "CurrentStage", "Value": "Course Enrolled"}]}],
            "M3": [{"CreatedOn": "2026-10-05 06:00:00", "Data": [{"Key": "CurrentStage", "Value": "Course Enrolled"}]}]}
    assert spot_check(A, lambda l: hist[l])["ok"]
    hist["M3"].insert(0, {"CreatedOn": "2026-09-01 06:00:00", "Data": [{"Key": "CurrentStage", "Value": "Course Enrolled"}]})
    assert not spot_check(A, lambda l: hist[l])["ok"]                       # enrolled before: not first-time
    assert not verdict_numbers_check(["99.9"], scorecard_cells(A))["ok"]


def test_conversion_window_is_shown_in_ist():
    r = run()
    r["meta"]["cw_end"] = "2026-10-08T18:06:40+00:00"
    html, _ = report_html(analyse(r), None, "Validated")
    assert "8 Oct 23:36 IST" in html


def test_each_call_carries_its_zipteams_analysis():
    from analytics.team_performance import zip_call_rows

    r = run()
    r["zips"].append({"CreatedOn": "2026-10-05 18:33:00", "RelatedProspectId": "E1", "mx_Custom_1": "HIGH"})  # after midnight
    r["zips"].append({"CreatedOn": "2026-10-05 20:00:00", "RelatedProspectId": "L1", "mx_Custom_1": "LOW"})   # next day's call
    r["calls"].append(call("u1", "E1", "2026-10-05 18:25:00", dur=480))                                     # 23:55 IST
    A = analyse(r)
    rows = {x["start_ist"]: x for x in zip_call_rows(A)}
    assert rows["2026-10-05 10:30"]["zip_intent"] == "HIGH" and rows["2026-10-05 10:30"]["zip_payment_step"] is True
    assert rows["2026-10-05 23:55"]["zip_intent"] == "HIGH"
    t = A["totals"]
    assert (t["zip_after_day_kept"], t["zip_other_day_excluded"], t["zip_attr"] + t["zip_dropped"]) == (1, 1, t["zip_total"])
    inside = next(c for c in data_checks(r, A) if c["check"] == "Every counted call and note is inside the IST day")
    assert inside["ok"], inside["detail"]


def test_late_edits_and_unreadable_times_are_counted():
    r = run()
    r["meta"]["edit_margin_days"] = 3
    r["calls"][0]["modified_utc"] = "2026-10-06 04:00:00"                     # edited the next morning, still counted
    r["calls"].append(call("u1", "W2", "10/5/2026 2:29:02 PM"))              # a start time we can't read
    A = analyse(r)
    t = A["totals"]
    assert (t["late_edits_recovered"], t["unreadable_time_excluded"], t["calls"]) == (1, 1, analyse(run())["totals"]["calls"])
    inside = next(c for c in data_checks(r, A) if c["check"] == "Every counted call and note is inside the IST day")
    assert not inside["ok"] and "1 calls had a start time that couldn't be read" in inside["detail"]
    recon = next(c for c in data_checks(r, A) if c["check"] == "Totals reconcile across teams and callers")
    assert recon["ok"]


def test_transcripts_are_placed_by_their_leadsquared_call_not_their_own_clock():
    from datetime import timedelta
    from types import SimpleNamespace

    from analytics.team_performance import pick_transcripts
    from integrations.transcripts import normalize_phone

    r = run()
    r["calls"].append(call("u1", "E1", "2026-10-05 14:30:00", dur=900))      # 20:00 IST
    A = analyse(r)
    evening = next(c for c in A["_calls"] if c["lead_id"] == "E1")
    k = normalize_phone(evening["lead_number"])
    api = [SimpleNamespace(phone=k, start_time=evening["t"] + timedelta(minutes=330), duration=905, transcript="evening call",
                           agent_name="Asha K"),                                # IST read as UTC: 01:30 IST next day
           SimpleNamespace(phone=k, start_time=evening["t"] - timedelta(days=2), duration=1500, transcript="older call",
                           agent_name="Asha K")]                                # longer, but another day's call
    rows, shifts = pick_transcripts(api, A["_calls"], {k: {"lead": "E1", "team": "Team A", "converted": False}})
    assert [r["transcript"] for r in rows] == ["evening call"]
    assert rows[0]["zip"] is None                                            # no Zipteams note on that call
    assert shifts == {"on_time": 0, "api_5h30_early": 0, "api_5h30_late": 1}


def test_funnel_hours_and_enrolment_days():
    A = analyse(run())
    F = A["funnel"]
    assert (F["calls"], F["answered"], F["real"], F["reached"], F["credited"]) == (36, 33, 32, 32, 2)  # the bot call is out
    assert F["conv_pct"] == round(100 * 2 / 32, 1)
    hours = {h["hour"]: h for h in A["hours"]}
    assert list(hours) == list(range(10, 17))                          # 05:00-10:30 UTC = 10:30-16:00 IST
    assert hours[10]["dials"] == 10 and hours[10]["answer_pct"] == 100.0 and hours[10]["real"] == 10
    assert (hours[14]["dials"], hours[14]["answered"], hours[14]["answer_pct"]) == (3, 2, 66.7)
    assert hours[15]["inbound"] == 1 and hours[15]["answer_pct"] is None   # no dials: no answer rate, not 0
    assert hours[16]["dials"] == 1 and hours[16]["answer_pct"] == 0.0
    assert sum(h["dials"] for h in A["hours"]) == A["totals"]["outbound"]
    days = {d["day"]: d for d in A["enrol_days"]}
    assert list(days) == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    assert (days["2026-10-05"]["credited"], days["2026-10-05"]["not_credited"], days["2026-10-06"]["credited"]) == (1, 1, 1)


def test_dialer_issue_and_callers_who_need_support():
    r = run()
    r["calls"] += [call("u3", f"F{i}", "2026-10-05 11:00:00", status="CallFailure", dur=0) for i in range(20)]
    A = analyse(r)
    meena = next(p for p in A["people"] if p["name"] == "Meena P")
    assert meena["dials"] == 31 and meena["failed_pct"] == round(100 * 20 / 31, 1) and meena["dialer_issue"]
    assert A["teams"]["Team A"]["dialer_issue_callers"] == 1
    assert not next(p for p in A["people"] if p["name"] == "Asha K")["dialer_issue"]
    assert A["support"] == [] and A["coach_case"] is None  # Asha and Ravi are recognised; Meena has a dialer issue
    r["users"] = USERS + [{"ID": "u5", "FirstName": "Dev", "LastName": "R", "MemberOfGroups": ["Team A"]},
                          {"ID": "u6", "FirstName": "Gita", "LastName": "M", "MemberOfGroups": ["Team A"]}]
    r["calls"] += [call("u5", f"D{i}", "2026-10-05 07:30:00", dur=600) for i in range(5)]
    r["calls"] += [call("u6", f"G{i}", "2026-10-05 07:40:00", dur=150) for i in range(5)]  # same real conversations, less talk
    A = analyse(r)
    assert [p["name"] for p in A["support"]] == ["Dev R", "Gita M"] and A["support"][0] is A["coach_case"]
    assert A["ties_left_out"] == {"rec": 0, "support": 0}


def test_report_has_every_section_and_plain_language():
    from analytics.team_performance_html import SECTIONS, sections_present

    r = run()
    r["meta"]["sample"] = "Built from a few calls only."
    A = analyse(r)
    html, _ = report_html(A, None, "Validated", [{"check": "No duplicate activity IDs", "ok": True, "detail": "0 duplicates"}])
    assert sections_present(html) == []
    assert "What this means" in html and "Words used in this report" in html and "Built from a few calls only." in html
    assert "No duplicate activity IDs" in html and "0 duplicates" in html            # the checks are printed in section 11
    assert "SAMPLE, partial data" in html                                          # in every page's footer
    summary, _ = report_html(A, None, summary_only=True)
    for doc in (html, summary):                                                    # no customer numbers or lead IDs
        assert not any(c["lead_number"] in doc or f">{c['lead_id']}<" in doc or f" {c['lead_id']} " in doc for c in r["calls"])
    assert SECTIONS[3] not in summary and sections_present(summary) == SECTIONS[3:]
    out_of_order = html.replace(f">{SECTIONS[5]}</h", ">moved</h") + f"<h2>{SECTIONS[5]}</h2>"
    assert sections_present(out_of_order) == SECTIONS[6:]


def test_team_story_suggests_the_dialer_first():
    from analytics.team_performance_html import _account, _team_story

    r = run()
    r["calls"] += [call("u3", f"F{i}", "2026-10-05 11:00:00", status="CallFailure", dur=0) for i in range(20)]
    A = analyse(r)
    lines, step = _team_story(A, "Team A", _account(A))
    assert step.startswith("First fix the dialer for Meena P (half or more of their dials failed). Then: ")
    assert any("1 caller had a dialer issue" in x for x in lines)
    A = analyse(run())
    A["teams"]["Team A"]["credited"] = 0
    assert _team_story(A, "Team A", _account(A))[1].startswith("Listen to three of the longest calls")


def test_report_without_ranked_teams_still_renders():
    from analytics.team_performance_html import sections_present

    r = run()
    r["calls"] = [c for c in r["calls"] if c["user_id"] != "u3"]   # 2 callers: Team A can't be ranked
    A = analyse(r)
    assert A["rank"] == []
    html, nums = report_html(A, None, "Validated")
    assert nums == [] and sections_present(html) == [] and "No team met the ranking rule" in html


def test_sections_check():
    from analytics.report_validation import sections_check

    assert sections_check([], 15)["ok"]
    assert not sections_check(["3. Teams compared"], 15)["ok"]


def test_ties_left_out_of_a_list():
    from analytics.team_performance import _ties

    ps = [{"credited": 0, "real": 5}, {"credited": 0, "real": 5}, {"credited": 0, "real": 5}, {"credited": 0, "real": 4}]
    assert (_ties(ps, 1), _ties(ps, 3), _ties(ps, 4), _ties(ps, 0)) == (2, 0, 0, 0)


def test_enrolments_after_a_short_call_are_not_among_the_leads_reached():
    r = run()
    r["calls"].append(call("u1", "S1", "2026-10-05 05:30:00", dur=60))     # answered, under 2 minutes
    r["enrollments"].append({"ProspectID": "S1", "OwnerId": "u1", "enrolled_at": "2026-10-06 06:00:00"})
    F = analyse(r)["funnel"]
    assert (F["reached"], F["credited"], F["credited_reached"]) == (32, 3, 2)
    html, _ = report_html(analyse(r), None, "Validated")
    assert "2 of these leads had a real conversation on 5 Oct; 1 enrolled after only a shorter call." in html


def test_callers_listed_under_a_phone_system_first_count_in_their_sales_team():
    phones = {"u1": ["Acefone Users", "Team A"], "u2": [" Mcube Users", "Team A", "Other"], "u4": ["New Joinees - Mcube"]}
    r = run()
    r["users"] = [{**u, "MemberOfGroups": phones.get(u["ID"], u["MemberOfGroups"])} for u in USERS]
    A, base = analyse(r), analyse(run())
    assert A["teams"]["Team A"] == base["teams"]["Team A"]                  # same team figures as without the phone groups
    assert A["rank"] == ["Team A"] and not {"Acefone Users", "Mcube Users", "New Joinees - Mcube"} & set(A["teams"])
    assert A["teams"]["Unassigned"]["callers"] == 1                         # only phone-system groups: no team
    assert A["totals"]["phone_first_callers"] == 3
    assert list(A["groups"]) == ["Acefone Users", "Mcube Users", "New Joinees - Mcube", "Other"]   # phone systems first
    ace, mc, other = A["groups"]["Acefone Users"], A["groups"]["Mcube Users"], A["groups"]["Other"]
    asha = next(p for p in A["people"] if p["name"] == "Asha K")
    assert (ace["callers"], ace["elsewhere"], ace["dials"], ace["real"], ace["credited"]) == (1, 1, asha["dials"], 10, 1)
    assert ace["phone"] and not other["phone"] and ace["also_in"] == {"Team A": 1}
    assert mc["also_in"] == {"Other": 1, "Team A": 1} and mc["teams_elsewhere"] == {"Team A": 1}
    assert {c["check"]: c["ok"] for c in data_checks(r, A)}["Callers in several groups: one team each, every shared group shown"]
    html, _ = report_html(A, None, "Validated")
    assert "3. Teams and groups compared" in html and "Groups that share callers with another team" in html
    assert "Acefone Users<br><span class='tag phone'>phone system</span>" in html
    assert "Phone systems: Acefone Users callers answered" in html and "; Mcube Users callers " in html
    assert "3 callers listed under a phone system first were counted in their sales team instead" in html
    assert "Calling-software group" not in html


def test_a_group_whose_callers_count_elsewhere_keeps_its_figures():
    A = analyse(run())                                         # Ravi S is in Team A and Other
    other = A["groups"]["Other"]
    ravi = next(p for p in A["people"] if p["name"] == "Ravi S")
    assert list(A["groups"]) == ["Other"] and ravi["team"] == "Team A" and ravi["groups"] == ["Team A", "Other"]
    assert (other["callers"], other["elsewhere"], other["real"], other["credited"]) == (1, 1, ravi["real"], ravi["credited"])
    html, _ = report_html(A, None, "Validated")
    assert "Other has the most callers counted in another team: its only caller counts in Team A (1)" in html
    assert "also in Other" in html                             # the appendix names a caller's other groups


def test_group_check_fails_on_a_phone_team_or_a_missing_group():
    name = "Callers in several groups: one team each, every shared group shown"
    A = analyse(run())
    A["groups"]["Other"]["real"] += 1
    assert not {c["check"]: c["ok"] for c in data_checks(run(), A)}[name]
    A = analyse(run())
    del A["groups"]["Other"]
    assert not {c["check"]: c["ok"] for c in data_checks(run(), A)}[name]
    A = analyse(run())
    for c in A["_calls"]:
        if c["name"] == "Asha K":
            c["team"], c["groups"] = "Mcube Users", ["Mcube Users", "Team A"]
    assert not {c["check"]: c["ok"] for c in data_checks(run(), A)}[name]


def test_summary_falls_back_to_fewer_teams_until_it_fits(tmp_path):
    from analytics.team_performance import fit_summary
    from analytics.team_performance_html import PAGE1_TEAMS

    pages = iter([2, 2, 1])
    k, n = fit_summary(analyse(run()), None, str(tmp_path / "s.html"), str(tmp_path / "s.pdf"), render=lambda h, p: next(pages))
    assert (k, n) == (PAGE1_TEAMS[2], 1)
    k, n = fit_summary(analyse(run()), None, str(tmp_path / "s.html"), str(tmp_path / "s.pdf"), render=lambda h, p: 2)
    assert (k, n) == (PAGE1_TEAMS[-1], 2)                                # never fits: the gate's check then fails


def test_a_quiet_hour_is_not_named_best_or_worst():
    from analytics.team_performance_html import HOUR_MIN_SHARE, _hours

    A = analyse(run())
    A["hours"] = [{"hour": 10, "dials": 200, "answered": 100, "inbound": 0, "real": 40, "answer_pct": 50.0},
                  {"hour": 11, "dials": 2, "answered": 0, "inbound": 0, "real": 0, "answer_pct": 0.0},      # 1% of dials
                  {"hour": 12, "dials": 100, "answered": 30, "inbound": 0, "real": 10, "answer_pct": 30.0}]
    assert 100 * 2 / 302 < HOUR_MIN_SHARE
    out = _hours(A)
    assert "least often at 12:00–13:00 (30.0%)" in out and "11:00–12:00 (0.0%)" not in out


def test_hour_table_is_split_so_it_never_runs_off_the_page():
    from analytics.team_performance_html import HOURS_PER_TABLE, _hours

    A = analyse(run())
    A["hours"] = [{"hour": h, "dials": 10, "answered": 5, "inbound": 0, "real": 1, "answer_pct": 50.0} for h in range(24)]
    assert _hours(A).count("<table class='hours") == 24 // HOURS_PER_TABLE


def test_page_one_reads_well_on_a_day_without_enrolments():
    r = run()
    r["enrollments"] = []
    html, nums = report_html(analyse(r), None, "Validated")
    assert "No ranked team had a credited enrolment" in html and "No lead enrolled from 5 Oct to 8 Oct." in html
    assert "No enrolment was credited to a caller by 8 Oct 23:59 IST." in html and "1 in –" not in html


def test_a_warm_runner_up_after_a_warm_best_team_is_not_explained_twice():
    from analytics.team_performance_html import _verdict
    T = {t: {"conv_pct": c, "credited": k, "reached": 20, "callers": 4, "real": 30, "warm": w}
         for t, c, k, w in (("W1", 40.0, 8, True), ("W2", 30.0, 6, True), ("F1", 10.0, 2, False))}
    A = {"teams": T, "rank": ["W1", "W2", "F1"], "runner_up": "W2", "best_front_line": "F1",
         "window": {"cw_end": "2026-10-08T23:59:59+05:30"}}
    items, _ = _verdict(A, [], None)
    assert " ".join(items).count("It mainly calls warm leads") == 1 and items[1].endswith("It also mainly calls warm leads.")
