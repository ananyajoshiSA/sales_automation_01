import json

from scripts.build_plan_pdf import build


def row(tier, **kw):
    return {"lead_id": f"{tier}1", "name": "Asha Rao", "tier": tier, "prob_3d": 35 if tier == "A" else 5, "course": "US CPA",
            "stage": "Follow Up For Closure", "why": "Asked for the fee.", "opening_line": "Hi Asha", "next_action": "Send the link.",
            "best_time": "18:00–21:00", **kw}


def test_pdf_takes_team_day_and_window_from_meta(tmp_path):
    plan = {"Ravi K": [row("A", missed_unreturned=1, callback_missed=True), row("B"), row("F")], "Lead Er": [row("C")]}
    meta = {"team": "Team Test", "leader": "Lead Er", "date_label": "Monday 12 October 2026", "day": "Monday",
            "next_day": "Tuesday", "window": "1 Sep – 11 Oct 2026", "new_window": "1 Sep – 11 Oct", "target": 3,
            "subteams": {"Ravi K": "Sub-team One"}, "unblock": ["Fix the CPA fee sheet."],
            "yesterday": "Five enrolments, none from the focus list."}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    build(str(tmp_path / "plan.json"), str(tmp_path / "out.html"), str(tmp_path / "meta.json"))
    doc = (tmp_path / "out.html").read_text()
    assert "Team Test" in doc and "Monday 12 October 2026" in doc and "1 Sep – 11 Oct 2026" in doc
    assert "target 3)" in doc and "Sub-team One" in doc and "Fix the CPA fee sheet." in doc
    assert "How the last plan went.</b> Five enrolments" in doc
    assert "Booked callback missed" in doc and "Missed call from lead" in doc
    assert "<h2>Lead Er</h2>" not in doc  # the leader gets no caller section
    for old in ("Elite", "Shivangi", "Shaziya", "Wednesday", "Friday", "8 Oct"):
        assert old not in doc


def test_team_without_inbound_gets_no_missed_call_steps_and_sees_discovery(tmp_path):
    plan = {"Ravi K": [row("A", discovery="Bot, 5 Oct (Full Conversion, 5 min): CA Inter, wants US clients."), row("B")]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "meta.json").write_text(json.dumps({"team": "Team Test", "inbound": False}))
    build(str(tmp_path / "plan.json"), str(tmp_path / "out.html"), str(tmp_path / "meta.json"))
    doc = (tmp_path / "out.html").read_text()
    assert "missed call" not in doc.lower() and "inbound" not in doc.lower() and "<th>M</th>" not in doc
    assert "Discovery so far:</b> Bot, 5 Oct" in doc


def test_handed_over_leads_are_flagged_and_counted(tmp_path):
    plan = {"Ravi K": [row("A", handover_from="Gita C", bot_done=True), row("B")]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    build(str(tmp_path / "plan.json"), str(tmp_path / "out.html"))
    doc = (tmp_path / "out.html").read_text()
    assert "Leads handed over from the counsellors (1)" in doc and "From Gita (counsellor)" in doc
