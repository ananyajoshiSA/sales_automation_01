from scripts.fetch_bot_calls import phone10, real_bot_calls


def test_phone_is_matched_on_its_last_ten_digits():
    assert phone10("+91-98920 15765") == "9892015765" and phone10(None) == ""


def test_only_real_bot_conversations_are_kept_newest_first():
    logs = [{"start_time": "2026-09-13T10:00:00", "status": "Contacted", "duration": "300", "disposition": "Full Conversion",
             "campaign_name": "U.SAcc_12thSep", "summary": " B.Com, 3 years. "},
            {"start_time": "2026-09-20T10:00:00", "status": "Contacted", "duration": "40", "disposition": "Hangup", "summary": "x"},
            {"start_time": "2026-09-21T10:00:00", "status": "No Contact", "duration": "0", "disposition": "NO_USER_RESPONSE"},
            {"start_time": "2026-09-25T10:00:00", "status": "Contacted", "duration": "150", "disposition": "Call Back NEW",
             "campaign_name": "Re_USAcc", "summary": "Busy, call later."}]
    got = real_bot_calls(logs)
    assert [(c["start_ist"][:10], c["secs"], c["outcome"]) for c in got] == [("2026-09-25", 150, "Call Back NEW"),
                                                                              ("2026-09-13", 300, "Full Conversion")]
    assert got[1]["summary"] == "B.Com, 3 years."
