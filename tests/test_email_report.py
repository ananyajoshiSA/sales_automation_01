import base64
import hashlib
import json
import os
import time

import pytest
import responses

from scripts import email_report as er

URL = "https://script.google.com/macros/s/AKfy-abc_123/exec"
ECHO = "https://script.googleusercontent.com/macros/echo?user_content_key=k"
TOKEN = "t" * 40
PDF, XLSX, CSV = b"%PDF-1.7 fake report", b"PK\x03\x04 fake workbook", b"caller,dials\nA,40\n"
NOW = time.time()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo folder with exports/ and a project-files reports folder."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "exports").mkdir()
    (tmp_path / "project_reports").mkdir()
    monkeypatch.setattr(er, "ROOTS", (str(tmp_path / "project_reports"), "exports"))
    monkeypatch.setenv("REPORT_EMAIL_URL", URL)
    monkeypatch.setenv("REPORT_EMAIL_TOKEN", TOKEN)
    return tmp_path


def put(root, rel, data, age_secs):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (NOW - age_secs, NOW - age_secs))
    return str(p)


def mailer(*answers, mode="live", recipients=3):
    """Apps Script answers each POST with a 302 to an echo URL that serves the JSON."""
    for a in ({"ok": True, "status": "ready", "mode": mode, "recipients": recipients},) + answers:
        responses.add(responses.POST, URL, status=302, headers={"Location": ECHO})
        responses.add(responses.GET, ECHO, json=a)


def bodies():
    return [json.loads(c.request.body) for c in responses.calls if c.request.method == "POST"]


def test_finished_reports_skip_drafts_fresh_writes_fakes_and_other_folders(repo):
    good = [put(repo, "project_reports/team_calling_report_2026-10-11.pdf", PDF, 3600),
            put(repo, "exports/plans/call_plan_Elite_2026-10-12.xlsx", XLSX, 7200),
            put(repo, "exports/coaching/attempts.csv", CSV, 9000)]
    put(repo, "exports/report_draft.pdf", PDF, 60)
    put(repo, "exports/tmp_calls.csv", CSV, 60)
    put(repo, "exports/still_writing.pdf", PDF, 2)
    put(repo, "exports/fake.pdf", b"<html>", 60)
    put(repo, "exports/binary.csv", b"a,b\n\0\0", 60)
    put(repo, "exports/notes.json", b"{}", 60)
    put(repo, "exports/empty.csv", b"", 60)
    put(repo, "data/report_2026-10-11/zip_calls.csv", CSV, 60)
    assert [os.path.realpath(p) for p in er.finished(NOW)] == [os.path.realpath(p) for p in good]


def test_named_files_outside_report_folders_are_refused(repo):
    raw = put(repo, "data/report_2026-10-11/zip_calls.csv", CSV, 600)
    assert "only finished reports" in er.problem(raw, NOW)
    os.symlink(raw, repo / "exports" / "link.csv")
    assert "only finished reports" in er.problem(str(repo / "exports" / "link.csv"), NOW)
    assert "PDF, Excel" in er.problem(put(repo, "exports/x.docx", b"x", 600), NOW)
    assert "not found" in er.problem(str(repo / "exports" / "missing.pdf"), NOW)


def test_latest_is_the_newest_single_report(repo):
    put(repo, "exports/old.csv", CSV, 20000)
    newest = put(repo, "project_reports/team_calling_report_2026-10-11.pdf", PDF, 3600)
    assert er.latest(NOW) == newest


def test_latest_stops_when_one_run_wrote_several_reports(repo):
    put(repo, "project_reports/team_calling_report_2026-10-11.pdf", PDF, 700)
    put(repo, "project_reports/plan_tracker_2026-10-11.pdf", PDF, 640)
    with pytest.raises(er.Unclear, match="team_calling_report.*plan_tracker|plan_tracker.*team_calling_report"):
        er.latest(NOW)


def test_latest_stops_when_nothing_is_recent(repo):
    with pytest.raises(er.Unclear, match="no finished"):
        er.latest(NOW)
    put(repo, "exports/team_calling_report_2026-10-01.pdf", PDF, 3 * 86400)
    with pytest.raises(er.Unclear, match="finished 3 days ago; name it"):
        er.latest(NOW)


@pytest.mark.parametrize("url,token,err", [
    ("https://evil.example/macros/s/x/exec", TOKEN, "REPORT_EMAIL_URL"),
    ("http://script.google.com/macros/s/x/exec", TOKEN, "REPORT_EMAIL_URL"),
    (URL, "short", "REPORT_EMAIL_TOKEN"),
])
def test_settings_refuse_anything_but_the_google_web_app(monkeypatch, url, token, err):
    monkeypatch.setenv("REPORT_EMAIL_URL", url)
    monkeypatch.setenv("REPORT_EMAIL_TOKEN", token)
    with pytest.raises(er.ApiError, match=err):
        er.settings()


@responses.activate
def test_send_checks_the_mailer_then_uploads_file_and_checksum(repo, capsys):
    path = put(repo, "exports/plans/call_plan_Elite_2026-10-12.xlsx", XLSX, 600)
    mailer({"ok": True, "status": "sent", "detail": "emailed to 3 recipient(s) in live mode"})
    assert er.main([]) == 0
    check, sent = bodies()
    assert check == {"token": TOKEN, "action": "check"}
    assert sent == {"token": TOKEN, "action": "send", "file_name": "call_plan_Elite_2026-10-12.xlsx", "resend": False,
                    "sha256": hashlib.sha256(XLSX).hexdigest(), "content_b64": base64.b64encode(XLSX).decode()}
    out = capsys.readouterr().out
    assert f"file: {path} (Excel, 0.0 MB, finished " in out and "IST, 10 min ago)" in out
    assert "mailer: live mode, 3 approved recipient(s)\nresult: sent - emailed to 3" in out
    assert TOKEN not in out
    assert all(c.request.method == "GET" and c.request.body is None for c in responses.calls if "echo" in c.request.url)


@responses.activate
def test_check_sends_nothing(repo, capsys):
    put(repo, "exports/a.csv", CSV, 600)
    mailer(mode="test", recipients=1)
    assert er.main(["--check"]) == 0
    assert [b["action"] for b in bodies()] == ["check"]
    assert "mailer: test mode, 1 approved recipient(s) (only the test address)" in capsys.readouterr().out


@responses.activate
def test_duplicate_and_resend(repo, capsys):
    path = put(repo, "exports/a.csv", CSV, 600)
    mailer({"ok": True, "status": "duplicate", "detail": "this exact file was already emailed at 2026-10-12 09:10 IST"})
    assert er.main([path]) == 0
    assert "result: duplicate - this exact file was already emailed" in capsys.readouterr().out
    responses.reset()
    mailer({"ok": True, "status": "sent", "detail": "emailed to 3 recipient(s) in live mode"})
    assert er.main([path, "--resend"]) == 0
    assert bodies()[1]["resend"] is True


@responses.activate
def test_off_mode_stops_before_upload(repo, capsys):
    put(repo, "exports/a.csv", CSV, 600)
    responses.add(responses.POST, URL, json={"ok": True, "status": "off", "mode": "off", "recipients": 0})
    assert er.main([]) == 2
    assert len(responses.calls) == 1
    assert "mailer: off - DELIVERY_MODE is off" in capsys.readouterr().out


@responses.activate
def test_refusals_from_the_mailer_fail(repo, capsys):
    put(repo, "exports/a.csv", CSV, 600)
    mailer({"ok": False, "status": "limit", "detail": "already 20 emails today"})
    assert er.main([]) == 2
    assert "result: limit - already 20 emails today" in capsys.readouterr().out


@responses.activate
def test_wrong_token_or_login_page(repo, capsys):
    put(repo, "exports/a.csv", CSV, 600)
    responses.add(responses.POST, URL, json={"ok": False, "status": "unauthorized"})
    assert er.main([]) == 2
    assert "mailer: unauthorized\n" in capsys.readouterr().out
    responses.reset()
    responses.add(responses.POST, URL, body="<html>Sign in</html>")
    assert er.main([]) == 2
    assert "access 'Anyone'" in capsys.readouterr().out


def test_unclear_refused_too_large_and_missing_settings_contact_nothing(repo, monkeypatch, capsys):
    with responses.RequestsMock():  # any HTTP call would raise
        put(repo, "project_reports/team_calling_report_2026-10-11.pdf", PDF, 700)
        put(repo, "project_reports/plan_tracker_2026-10-11.pdf", PDF, 640)
        assert er.main([]) == 1
        assert capsys.readouterr().out.startswith("unclear: these finished together")
        assert er.main([str(repo / "exports" / "report_draft.pdf")]) == 2
        assert "refused:" in capsys.readouterr().out
        monkeypatch.setattr(er, "MAX_BYTES", 10)
        assert er.main([str(repo / "project_reports" / "plan_tracker_2026-10-11.pdf")]) == 2
        assert "too_large: over 20 MB" in capsys.readouterr().out
        monkeypatch.setattr(er, "MAX_BYTES", 20 * 1024 * 1024)
        monkeypatch.delenv("REPORT_EMAIL_TOKEN")
        assert er.main([str(repo / "project_reports" / "plan_tracker_2026-10-11.pdf")]) == 2
        assert "error: REPORT_EMAIL_TOKEN" in capsys.readouterr().out


def test_list_shows_newest_first_and_sends_nothing(repo, capsys):
    put(repo, "exports/older.csv", CSV, 7200)
    put(repo, "exports/newer.pdf", PDF, 600)
    with responses.RequestsMock():
        assert er.main(["--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [l.split(" (")[0].rsplit("/", 1)[1] for l in lines] == ["newer.pdf", "older.csv"]
