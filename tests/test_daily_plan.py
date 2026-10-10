import json

from analytics.daily_plan import content, mailer, rows, state, status, transcripts, workbook
from analytics.daily_plan.common import Snap, previous_working_day
from analytics.daily_plan.stats import day_stats

USERS = [{"ID": "u1", "FirstName": "Asha", "LastName": "K"}, {"ID": "u2", "FirstName": "Ravi", "LastName": "S"},
         {"ID": "u0", "FirstName": "Tara", "LastName": "L"}]
LEADS = [{"ProspectID": f"L{i}", "FirstName": f"Lead{i}", "Phone": f"+91-90000000{i:02d}", "OwnerIdName": "Asha K" if i % 2 else "Ravi S",
          "ProspectStage": "Follow Up For Closure", "CreatedOn": "2026-09-01 05:00:00"} for i in range(1, 9)]


def call(user, lead, utc, status="NotAnswered", dur=0, direction="outbound"):
    return {"activity_id": f"{user}{lead}{utc}{direction}", "user_id": user, "lead_id": lead, "start_utc": utc, "status": status,
            "duration": dur, "direction": direction, "lead_number": "90000000" + lead[1:].rjust(2, "0"), "display_number": "918065990718"}


def snap(calls, fetched="2026-10-10T08:30:00+00:00", leads=LEADS):
    return Snap({"users": USERS, "fetched_at": fetched, "leads": leads, "calls": calls, "zip_activities": []}, leader="Tara L")


def test_state_keeps_no_lead_ids_and_resolves_back():
    by_owner = {"Asha K": [{"lead_id": "L1", "tier": "A", "chance": 30}], "Ravi S": [{"lead_id": "L2", "tier": "B", "chance": 10, "verify": True}]}
    st = state.to_state("2026-10-10", by_owner, [{"lead_id": "L1", "group": 1, "check_by": "11:30"}])
    assert "L1" not in json.dumps(st) and "Lead1" not in json.dumps(st)
    got = state.resolve(st, ["L1", "L2", "L3"])
    assert got["L1"]["group"] == 1 and got["L2"]["verify"] and "L3" not in got


def test_day_stats_measures_the_a_block_and_missed_calls():
    sheet = {"L1": {"owner": "Asha K", "tier": "A"}, "L3": {"owner": "Asha K", "tier": "A"}, "L2": {"owner": "Ravi S", "tier": "B"}}
    cs = [call("u1", "L1", "2026-10-09 05:00:00"), call("u1", "L1", "2026-10-09 05:10:00", "Answered", 300),  # redial in 10 min, reached
          call("u1", "L5", "2026-10-09 06:00:00"),                                                               # off-sheet dial
          call("u0", "L2", "2026-10-09 07:00:00", "Missed", 0, "inbound")]                                        # Ravi's lead rang, unreturned
    st = day_stats(snap(cs), "2026-10-09", sheet)
    a, r, t = st["callers"]["Asha K"], st["callers"]["Ravi S"], st["team"]
    assert (a["dials"], a["real"], a["on_sheet_pct"], a["a_tried"], a["a_twice"], a["a_reached"]) == (3, 1, 67, 1, 1, 1)
    assert (t["a_total"], t["a_tried"], t["a_first_unanswered"], t["a_redial15"]) == (2, 1, 1, 1)
    assert t["a_not_tried"] == ["L3"] and t["absent"] == ["Ravi S"]
    assert r["missed_in"] == 1 and r["missed_unreturned"] == 1


def test_previous_working_day_skips_a_quiet_sunday():
    cs = [call("u1", "L1", f"2026-10-10 05:{m:02d}:00") for m in range(55)]
    s = snap(cs)
    assert previous_working_day(s, "2026-10-12") == "2026-10-10"


def test_enrolled_or_paid_leads_never_go_back_on_a_sheet():
    reads = {"L1": {"lead_id": "L1", "name": "Lead1", "status": "paid_new", "tier": "D", "chance": 0, "payment_evidence": "Rs 3,000 paid on the call"},
             "L3": {"lead_id": "L3", "name": "Lead3", "status": "21day", "tier": "D", "chance": 0},
             "L4": {"lead_id": "L4", "name": "Lead4", "status": "active", "tier": "B", "chance": 12},
             "L5": {"lead_id": "L5", "name": "Lead5", "status": "active", "tier": "A", "chance": 40}}
    leads = [dict(l, ProspectStage="Course Enrolled") if l["ProspectID"] in ("L5", "L6") else l for l in LEADS]
    cs = [call("u0", "L4", "2026-10-09 10:00:00", "Missed", 0, "inbound")]
    prev = {"L7": {"owner": "Asha K", "tier": "C", "chance": 3}, "L6": {"owner": "Ravi S", "tier": "A", "chance": 30},
            "L8": {"owner": "Ravi S", "tier": "P", "chance": 0}}
    R = rows.build_rows(snap(cs, leads=leads), "2026-10-10", reads, prev, [])
    flat = {r["lead_id"]: r for rs in R["by_owner"].values() for r in rs}
    assert not {"L1", "L5", "L6", "L8"} & set(flat)                       # paid on a call, Course Enrolled, or an old paid row
    assert sorted(p["lead_id"] for p in R["paid"]) == ["L1", "L5"]
    assert "L3" not in flat and R["dropped"][0]["status"] == "21day"
    assert flat["L4"]["tier"] == "M" and flat["L4"]["missed"] == 1
    assert flat["L7"]["source"] == "carried" and flat["L7"]["tier"] == "C"


def test_rs10_bootcamp_registration_is_a_prospect_not_an_enrollment():
    from analytics.daily_plan import review
    leads = [dict(l, ProspectStage="Course Enrolled") if l["ProspectID"] in ("L1", "L3") else l for l in LEADS]
    reads = {"L1": {"lead_id": "L1", "name": "Lead1", "status": "bootcamp", "tier": "B", "chance": 8, "payment_evidence": "Rs 10 bootcamp registration"},
             "L3": {"lead_id": "L3", "name": "Lead3", "status": "paid_new", "tier": "D", "chance": 0}}
    enrolled = [{"ProspectID": lid, "FirstName": f"Lead{lid[1:]}", "OwnerIdName": "Asha K", "first_enrolled": "2026-10-09 06:00:00", "history": []}
                for lid in ("L1", "L3")]
    s = snap([], leads=leads)
    R = rows.build_rows(s, "2026-10-10", reads, {}, enrolled)
    flat = {r["lead_id"]: r for rs in R["by_owner"].values() for r in rs}
    assert flat["L1"]["tier"] == "B" and "Rs 10 bootcamp" in flat["L1"]["why"] and "L3" not in flat
    assert [e["lead_id"] for e in review.enrollments(s, "2026-10-09", enrolled, reads)] == ["L3"]
    assert [b["name"] for b in review.bootcamp_registrations(s, "2026-10-09", enrolled, reads)] == ["Lead1"]
    st = state.to_state("2026-10-10", R["by_owner"], [{"lead_id": "L1", "group": 3, "check_by": "15:30"}])
    S = status.build(s, "2026-10-10", state.resolve(st, s.leads), [], "Tara L")
    assert [p["lead_id"] for p in S["priority"]] == ["L1"]


def test_priority_groups_respect_the_day_the_lead_asked_for():
    R = {"today": "2026-10-10", "by_owner": {"Asha K": [
        {"lead_id": "a", "name": "A", "tier": "A", "chance": 30, "callback_requested": ""},
        {"lead_id": "b", "name": "B", "tier": "B", "chance": 8, "callback_requested": "Sat 10 Oct 18:00"},
        {"lead_id": "c", "name": "C", "tier": "B", "chance": 12, "callback_requested": "Monday evening"},
        {"lead_id": "d", "name": "D", "tier": "A", "chance": 40, "callback_requested": "", "exact_ask": "Send the payment link today"},
        {"lead_id": "e", "name": "E", "tier": "C", "chance": 2, "callback_requested": ""}]}}
    g = {p["lead_id"]: (p["group"], p["check_by"]) for p in content.priority(R, "2026-10-10")}
    assert g == {"d": (0, "12:00"), "a": (1, "11:30"), "b": (2, "18:00"), "c": (5, "later")}


def test_workbook_counts_paid_without_asking_for_proof(tmp_path):
    R = {"by_owner": {"Asha K": [{"lead_id": "L1", "name": "Lead1", "tier": "A", "chance": 30, "phone": "9000000001"}]}}
    C = {"leader": "Tara L", "review": {"day_label": "Fri 9 Oct"}, "slots": content.SLOTS, "shivangi_title": "t", "shivangi_intro": "i",
         "priority": [], "priority_groups": content.GROUPS, "checks": [], "feedback": [], "changes": [], "caller_note": "n",
         "owner_order": ["Asha K"], "targets": {}, "how_to": [], "day_label": "Sat"}
    out = workbook.write(R, C, str(tmp_path / "p.xlsx"))
    from openpyxl import load_workbook
    ws = load_workbook(out)["Asha K"]
    formulas = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=IF(")]
    assert formulas and all('"Enrolled"' in f and "proof" not in f and '"Already a student"' in f for f in formulas)


def test_status_lists_untouched_tried_and_unreturned():
    plan = {"L1": {"owner": "Asha K", "tier": "A", "group": 1, "check_by": "11:30"},
            "L3": {"owner": "Asha K", "tier": "A", "group": 1, "check_by": "11:30"},
            "L5": {"owner": "Asha K", "tier": "B", "group": 2, "check_by": "18:00"},
            "L7": {"owner": "Asha K", "tier": "A", "group": 1, "check_by": "11:30"}}
    cs = [call("u1", "L3", "2026-10-10 05:00:00"), call("u0", "L2", "2026-10-10 06:00:00", "Missed", 0, "inbound")]
    leads = [dict(l, ProspectStage="Course Enrolled") if l["ProspectID"] == "L7" else l for l in LEADS]
    S = status.build(snap(cs, leads=leads), "2026-10-10", plan, [], "Tara L")
    by = {p["lead_id"]: p["status"] for p in S["priority"]}
    assert by == {"L1": "untouched", "L3": "tried", "L5": "untouched"}
    text = " ".join(S["todo"])
    assert "Ravi S" in text and "Lead1" in text and "Lead2" in text and "Lead5 18:00" in text
    assert "9000000002" not in status.text_summary(S, "Team")


def test_mailer_needs_settings_and_sends_attachments(tmp_path, monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert mailer.send("s", "<p>h</p>", "t", ["a@x.in"]) is False
    monkeypatch.setenv("SMTP_HOST", "smtp.x.in"); monkeypatch.setenv("SMTP_USER", "r@x.in"); monkeypatch.setenv("SMTP_PASSWORD", "p")
    f = tmp_path / "plan.pdf"; f.write_bytes(b"%PDF")
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout): sent.append((host, port))
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, m): sent.append(m)

    assert mailer.send("Plan", "<p>h</p>", "t", ["a@x.in", "b@x.in"], [str(f)], smtp_factory=FakeSMTP)
    msg = sent[1]
    assert sent[0] == ("smtp.x.in", 587) and msg["To"] == "a@x.in, b@x.in"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["plan.pdf"]


def test_transcript_runs_stay_within_limits_and_resume(tmp_path):
    made = []

    class FakeClient:
        requests_made = 0
        def search_raw(self, nums):
            assert len(nums) <= 90
            made.append(len(nums)); self.requests_made = -(-len(nums) // 10)
            return {n: {"sales_call": []} for n in nums}

    pauses = []
    nums = [f"9190000{i:05d}" for i in range(200)]
    transcripts.fetch(nums, str(tmp_path), pause=5, client_factory=FakeClient, sleep=pauses.append)
    assert made == [90, 90, 20] and pauses == [5, 5]
    transcripts.fetch(nums, str(tmp_path), pause=5, client_factory=FakeClient, sleep=pauses.append)
    assert made == [90, 90, 20]


def test_pipeline_runs_on_python_311():
    """Scheduled cloud sessions start on python3 = 3.11, which rejects 3.12-only f-string quoting."""
    import pathlib
    import shutil
    import subprocess

    import pytest

    py = shutil.which("python3.11")
    if not py:
        pytest.skip("python3.11 not installed")
    files = [str(p) for p in pathlib.Path("analytics/daily_plan").glob("*.py")]
    r = subprocess.run([py, "-m", "py_compile", *files], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
