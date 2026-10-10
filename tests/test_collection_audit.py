from analytics.collection_audit import (age_bucket, audit, chance, contact_bucket, history_table, pipeline, position,
                                        simulate, stage_group, wilson)
from integrations.timeutil import utc

NOW = utc("2026-10-10 09:00:00")


def stage(t, cur, prev="New Lead", comment=""):
    return {"EventCode": 3002, "CreatedOn": t,
            "Data": [{"Key": "PreviousStage", "Value": prev}, {"Key": "CurrentStage", "Value": cur},
                     {"Key": "Comment", "Value": comment}]}


def call(t, status="Answered", dur=300, by="Asha Rao", out=True):
    note = f"Caller{{=}}{by}{{next}}Duration{{=}}{dur}{{next}}Status{{=}}{status}"
    return {"EventCode": 22 if out else 21, "CreatedOn": t, "ActivityFields": {"ActivityEvent_Note": note}}


def lead(lid, stage_now, tag="Community Webinar Collections"):
    return {"ProspectID": lid, "OwnerId": "u1", "OwnerIdName": "Asha Rao", "ProspectStage": stage_now,
            "mx_Bootcamp_collections": tag}


def test_buckets_and_groups():
    assert age_bucket(2.5) == "0-3 days" and age_bucket(30) == "22-30 days" and age_bucket(40) == "31+ days"
    assert contact_bucket(None) == "never spoke since booking" and contact_bucket(1) == "spoke in last 2 days"
    assert stage_group("Loan pending") == "Loan pending" and stage_group("Booking Fees Received").startswith("Untouched")
    assert stage_group("Roadmap Done").startswith("Other")


def test_position_reads_only_up_to_the_snapshot():
    acts = [stage("2026-08-01 05:00:00", "Booking fees received"), call("2026-08-02 05:00:00"),
            stage("2026-08-02 05:10:00", "Loan pending", "Booking fees received"), call("2026-08-09 05:00:00")]
    p = position(acts, utc("2026-08-01 05:00:00"), utc("2026-08-06 05:00:00"))
    assert p["stage"] == "Loan pending" and p["days_open"] == 5 and p["days_since_spoke"] == 4


def test_history_table_counts_open_snapshots_and_later_collections():
    # booked 1 Aug; collected on day 8: open at the day-2 and day-5 snapshots, collected within 14 days of both
    hist = {"a": [stage("2026-08-01 05:00:00", "Booking fees received"),
                  stage("2026-08-09 05:00:00", "Collections done", "Booking fees received")],
            # booked 1 Aug, lost on day 3: only the day-2 snapshot, never collected
            "b": [stage("2026-08-01 05:00:00", "Booking fees received"),
                  stage("2026-08-04 05:00:00", "Not Interested", "Booking fees received")],
            # booked too recently for a 45-day horizon
            "c": [stage("2026-10-01 05:00:00", "Booking fees received")]}
    t = history_table([lead("a", "Collections done"), lead("b", "Not Interested"), lead("c", "Booking fees received")], hist, NOW)
    assert t[("Community", "0-3 days")] == [2, 0, 1, 1, 1]      # day 2: collected on day 8, so not within 3 days
    assert t[("Community", "4-7 days")] == [1, 1, 1, 1, 1]      # day 5: collected 3 days later


def test_chance_falls_back_to_a_coarser_position():
    pos = {"stage": "Loan pending", "days_open": 2, "days_since_spoke": 1}
    table = {("Community", "0-3 days", "Loan pending", "spoke in last 2 days"): [5, 5, 5, 5, 5],
             ("Community", "0-3 days", "Loan pending"): [40, 4, 10, 16, 20], ("Community", "0-3 days"): [100, 10, 30, 35, 40]}
    c = chance(table, "Community", pos)
    assert c["chance_from"] == "age and stage" and c["chance_3d"] == 0.1 and c["chance_14d"] == 0.25
    assert c["chance_21d"] == 0.4 and c["chance_45d"] == 0.5
    assert c["chance_14d_low"] < 0.25 < c["chance_14d_high"]
    lo, hi = wilson(0, 50)
    assert lo == 0 and 0 < hi < 0.06


def test_audit_and_pipeline():
    booked = "2026-09-20 05:00:00"
    hist = {"open": [stage(booked, "Booking fees received"), {"EventCode": 21, "CreatedOn": "2026-09-21 06:00:00",
                     "ActivityFields": {"ActivityEvent_Note": "Status{=}Missed"}},
                     stage("2026-09-22 05:00:00", "Loan pending", "Booking fees received", "loan process done, utr pending")],
            "won": [stage(booked, "Booking fees received"), call("2026-09-20 06:00:00"),
                    stage("2026-09-21 05:00:00", "Collections done", "Booking fees received")],
            "old": [stage("2026-07-01 05:00:00", "Booking fees received")]}
    leads = [lead("open", "Loan pending"), lead("won", "Collections done"), lead("old", "Booking fees received")]
    rows, pool, _, _ = audit(leads, hist, {"u1": "Elite Changemakers"}, frozenset({"Asha Rao"}), NOW, "2026-09-01")
    assert [r["lead_id"] for r in rows] == ["open"] and len(pool) == 2
    (r,) = rows
    assert r["stage_group"] == "Loan pending" and r["days_since_spoke"] is None
    assert any("paid or loan done" in f for f in r["flags"])
    assert any("not called back" in f for f in r["flags"]) and any("Never dialled" in f for f in r["flags"])
    assert "LEAD CALLED IN: missed" in r["dossier"] and "utr pending" in r["dossier"]
    r.update({"chance_3d": 0.0, "chance_3d_low": 0.0, "chance_3d_high": 0.0, "chance_14d": 0.5, "chance_14d_low": 0.5, "chance_14d_high": 0.5,
              "chance_21d": 1.0, "chance_21d_low": 1.0, "chance_21d_high": 1.0,
              "chance_45d": 1.0, "chance_45d_low": 1.0, "chance_45d_high": 1.0})
    assert simulate([r], "chance_45d") == (1.0, 1, 1)
    (p,) = pipeline(rows, pool, "kind")
    assert (p["pool"], p["collected"], p["open"], p["projected_%_45d"]) == (2, 1, 1, 100.0)
    assert p["needed_for_75%"] == 1 and p["needed_for_75%_share_of_open"] == "100%"


def test_reading_round_check_and_merge(tmp_path):
    import json
    from analytics.collection_audit import check_reading, load_readings, priority, write_reading_round
    rows = [{"lead_id": "a", "flags": [], "dossier": "x"}, {"lead_id": "b", "flags": ["No dial for 4 days"], "dossier": "y"}]
    folder = write_reading_round(rows, str(tmp_path), "2026-10-10 17:05")
    assert folder.endswith("20261010-1705")
    good = {"lead_id": "a", "situation": "Loan KYC left.", "blocker": "Loan or EMI paperwork in progress",
            "outlook": "Ready to close", "next_action": "Call today.", "what_to_say": "Ask if KYC is done.", "when": "today",
            "process_issues": ["Loan stalled"]}
    read = tmp_path / "20261010-1705" / "read_01.jsonl"
    read.write_text(json.dumps(good) + "\n" + json.dumps({**good, "lead_id": "b", "blocker": "Money"}) + "\n")
    problems = check_reading(str(read), f"{folder}/batch_01.jsonl")
    assert problems == ["line 2: blocker 'Money' not in the list"]
    assert load_readings(str(tmp_path)) == {}           # a failing file is not merged
    read.write_text(json.dumps(good) + "\n" + json.dumps({**good, "lead_id": "b", "outlook": "Check payment first"}) + "\n")
    assert check_reading(str(read), f"{folder}/batch_01.jsonl") == []
    got = load_readings(str(tmp_path))
    assert set(got) == {"a", "b"}
    a = {**got["a"], "chance_14d": 0.9, "days_open": 3}
    b = {**got["b"], "chance_14d": 0.1, "days_open": 9}
    assert sorted([a, b], key=priority)[0]["lead_id"] == "b"   # payment checks come first


def test_round_two_needs_transcript_fields_and_wins_over_round_one(tmp_path):
    import json
    from analytics.collection_audit import check_reading, load_readings, transcript_excerpts, write_reading_round
    booked = utc("2026-10-01 05:00:00")
    calls = [{"start_api": "2026-09-20T05:00:00+00:00", "transcript": "before the booking", "duration": 600},
             {"start_api": "2026-10-03T05:00:00+00:00", "transcript": "x" * 5000, "duration": 900, "agent": "Asha"},
             {"start_api": "2026-10-05T05:00:00+00:00", "transcript": "", "duration": 30}]
    (ex,) = transcript_excerpts(calls, booked)
    assert ex["date"] == "03 Oct" and ex["minutes"] == 15.0 and "[...]" in ex["text"] and len(ex["text"]) < 3100
    rows = [{"lead_id": "a", "flags": [], "dossier": "d"}]
    one = write_reading_round(rows, str(tmp_path), "2026-10-10 16:17")
    two = write_reading_round(rows, str(tmp_path), "2026-10-10 18:30", {"a": [ex]})
    assert json.loads(open(f"{two}/batch_01.jsonl").readline())["transcripts"][0]["date"] == "03 Oct"
    base = {"lead_id": "a", "situation": "s", "blocker": "Cannot afford now", "outlook": "Long shot", "next_action": "n",
            "what_to_say": "w", "when": "today", "process_issues": []}
    open(f"{one}/read_01.jsonl", "w").write(json.dumps(base) + "\n")
    open(f"{two}/read_01.jsonl", "w").write(json.dumps(base) + "\n")
    assert "line 1: stuck_at None not in the list" in check_reading(f"{two}/read_01.jsonl", f"{two}/batch_01.jsonl")
    assert load_readings(str(tmp_path))["a"]["version"] == 1
    v2 = {**base, "objection": "Wants 6-month EMI", "stuck_at": "Choosing how to pay", "month_end": "Likely",
          "month_end_reason": "Said she pays on the 25th"}
    open(f"{two}/read_01.jsonl", "w").write(json.dumps(v2) + "\n")
    assert check_reading(f"{two}/read_01.jsonl", f"{two}/batch_01.jsonl") == []
    got = load_readings(str(tmp_path))["a"]
    assert got["version"] == 2 and got["month_end"] == "Likely"


def test_new_bookings_outlook_counts_weeks_left_in_the_month():
    from datetime import timedelta
    from analytics.collection_audit import month_end, new_bookings_outlook
    now = utc("2026-10-10 10:00:00")                       # Saturday 10 Oct, 15:30 IST
    assert month_end(now).strftime("%Y-%m-%d %H:%M") == "2026-10-31 23:59"

    def v(booked, weekend, days_to_collect=None):
        return {"kind": "Bootcamp", "team": "T", "booked": utc(booked), "weekend": weekend, "days_to_collect": days_to_collect}
    views = [v("2026-07-06 05:00:00", "2026-07-04", 3), v("2026-07-06 05:00:00", "2026-07-04", 15),
             v("2026-07-06 05:00:00", "2026-07-04"), v("2026-07-06 05:00:00", "2026-07-04", 30)]
    views += [v("2026-09-28 05:00:00", "2026-09-26") for _ in range(4)] + [v("2026-10-05 05:00:00", "2026-10-03") for _ in range(2)]
    views += [v("2026-10-10 05:00:00", "2026-10-10")]       # one booking already in this weekend
    (o,) = new_bookings_outlook(views, now, weeks=2)
    assert o["weekly_bookings"] == [4, 2]
    # weekends 10, 17 and 24 Oct (31 Oct's Monday is past month end); Mondays leave 19, 12 and 5 days
    assert [w.split(":")[0] for w in o["weeks"]] == ["10 Oct", "17 Oct", "24 Oct"]
    # median volume 4 (sorted [2, 4] -> index 1); this weekend 4 - 1 = 3 x 50%, then 4 x 25%, 4 x 25%
    assert o["expected"] == round(3 * 0.5 + 4 * 0.25 + 4 * 0.25, 1)
    assert timedelta(days=45) < now - utc("2026-07-06 05:00:00") < timedelta(days=150)
