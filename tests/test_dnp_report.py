import json

from analytics.dnp_report import dialer_days, main, team_of_user


def c(name, day, status, sec="00"):
    return {"name": name, "team": "T", "day": day, "status": status, "start_utc": f"{day} 05:00:{sec}"}


def test_dialer_days_need_a_working_day_and_half_failures():
    calls = [c("A", "2026-10-05", "CallFailure", "01") for _ in range(12)] + [c("A", "2026-10-05", "Answered") for _ in range(8)]
    calls += [c("B", "2026-10-05", "CallFailure") for _ in range(10)]          # only 10 dials: not a working day
    calls += [c("A", "2026-10-06", "CallFailure", f"{i:02d}") for i in range(9)] + [c("A", "2026-10-06", "Answered") for _ in range(11)]
    d = dialer_days(calls)
    assert [(x["caller"], x["day"], x["failure_%"], x["same_second_failures"]) for x in d] == [("A", "2026-10-05", 60.0, 12)]


def test_dialer_day_is_left_out_of_caller_rates(tmp_path):
    users = [{"ID": "u1", "FirstName": "A", "LastName": "", "MemberOfGroups": ["T"]}]
    rows = [{"lead_id": f"L{i}", "direction": "outbound", "start_utc": "2026-10-05 05:00:00", "user_id": "u1", "caller": "A",
             "status": "CallFailure", "duration": 0} for i in range(20)]
    rows += [{"lead_id": f"M{i}", "direction": "outbound", "start_utc": "2026-10-06 05:00:00", "user_id": "u1", "caller": "A",
              "status": "Answered", "duration": 200} for i in range(20)]
    (tmp_path / "calls.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (tmp_path / "users.json").write_text(json.dumps(users))
    main(str(tmp_path / "calls.jsonl"), str(tmp_path / "users.json"), str(tmp_path / "out"))
    res = json.load(open(tmp_path / "out" / "dnp.json"))
    a = res["callers"][0]
    assert a["dialer_failure_days"] == 1 and a["answered_%"] == 100.0 and a["active_days"] == 1
    assert res["overall"]["call_failure_%"] == 50.0                            # account totals still show the outage


def test_team_of_user_skips_calling_software_groups():
    assert team_of_user({"MemberOfGroups": ["Mcube Users", "T"]}) == "T"
    assert team_of_user({"MemberOfGroups": ["Acefone Users"]}) == team_of_user({}) == "(no group)"
