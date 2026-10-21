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


SCRATCH = "tmp/claude-0/-home-user-repo/abc/scratchpad"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo folder, a chat's scratch folder and a project-files reports folder."""
    monkeypatch.chdir(tmp_path)
    for d in ("repo", "project_reports", SCRATCH):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setattr(er, "REPO", str(tmp_path / "repo"))
    monkeypatch.setattr(er, "BLOCKED", str(tmp_path / "repo" / "data"))
    monkeypatch.setattr(er, "LAST_SENT", str(tmp_path / "repo" / "data" / "email_report" / "last_sent.json"))
    monkeypatch.setattr(er, "PROJECT_REPORTS", str(tmp_path / "project_reports"))
    monkeypatch.setattr(er, "SCRATCH", [str(tmp_path / "tmp" / "claude-*" / "*" / "*" / "scratchpad")])
    monkeypatch.setattr(er, "RETRY_SECS", 0)
    monkeypatch.setenv("REPORT_EMAIL_URL", URL)
    monkeypatch.setenv("REPORT_EMAIL_TOKEN", TOKEN)
    return tmp_path


def put(root, rel, data, age_secs):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (NOW - age_secs, NOW - age_secs))
    return str(p)


def real(paths):
    return [os.path.realpath(p) for p in paths]


def mailer(*answers, mode="live", recipients=3, multi=True):
    """Apps Script answers each POST with a 302 to an echo URL that serves the JSON."""
    for a in ({"ok": True, "status": "ready", "mode": mode, "recipients": recipients, **({"multi": True} if multi else {})},) + answers:
        responses.add(responses.POST, URL, status=302, headers={"Location": ECHO})
        responses.add(responses.GET, ECHO, json=a)


def bodies():
    return [json.loads(c.request.body) for c in responses.calls if c.request.method == "POST"]


def test_finished_reports_anywhere_the_chat_saved_them_but_not_drafts_fakes_or_raw_data(repo):
    good = [put(repo, "project_reports/team_calling_report_2026-10-11.pdf", PDF, 3600),
            put(repo, SCRATCH + "/plan/exports/plans/Deepanshi_consolidated_plan_Wed_21_Oct.pdf", PDF, 5000),
            put(repo, "repo/exports/plans/call_plan_Elite_2026-10-12.xlsx", XLSX, 7200),
            put(repo, "repo/exports/coaching/attempts.csv", CSV, 9000)]
    put(repo, "repo/exports/report_draft.pdf", PDF, 60)
    put(repo, SCRATCH + "/tmp_calls.csv", CSV, 60)
    put(repo, "repo/exports/still_writing.pdf", PDF, 2)
    put(repo, "repo/exports/fake.pdf", b"<html>", 60)
    put(repo, "repo/exports/binary.csv", b"a,b\n\0\0", 60)
    put(repo, "repo/exports/notes.json", b"{}", 60)
    put(repo, "repo/exports/empty.csv", b"", 60)
    put(repo, "repo/data/report_2026-10-11/zip_calls.csv", CSV, 60)
    put(repo, SCRATCH + "/plan/data/snapshot.csv", CSV, 60)
    put(repo, "repo/.venv/lib/report.html", b"<p>", 60)
    put(repo, "elsewhere/other_chat.pdf", PDF, 60)
    assert real(er.finished(NOW)) == real(good)


def test_named_files_from_any_folder_but_never_raw_data_or_hidden_files(repo):
    assert er.problem(put(repo, "elsewhere/plan.pdf", PDF, 600), NOW) is None
    assert er.problem(put(repo, "elsewhere/chart.png", b"\x89PNG\r\n", 600), NOW) is None
    assert er.problem(put(repo, "elsewhere/notes.docx", XLSX, 600), NOW) is None
    raw = put(repo, "repo/data/report_2026-10-11/zip_calls.csv", CSV, 600)
    assert "raw lead data" in er.problem(raw, NOW)
    os.symlink(raw, repo / "elsewhere" / "link.csv")
    assert "raw lead data" in er.problem(str(repo / "elsewhere" / "link.csv"), NOW)
    assert "characters the mailer refuses" in er.problem(put(repo, "repo/.env.csv", CSV, 600), NOW)
    assert "PDF, Excel" in er.problem(put(repo, "repo/exports/x.json", b"{}", 600), NOW)
    assert "not a real PNG image" in er.problem(put(repo, "elsewhere/x.png", PDF, 600), NOW)
    assert "not found" in er.problem(str(repo / "elsewhere" / "missing.pdf"), NOW)


def test_latest_set_is_the_newest_run_of_reports(repo):
    put(repo, "repo/exports/old.csv", CSV, 20000)
    pdf = put(repo, SCRATCH + "/plan/team_plan.pdf", PDF, 600)
    xlsx = put(repo, SCRATCH + "/plan/team_plan.xlsx", XLSX, 900)
    chart = put(repo, "repo/exports/chart.png", b"\x89PNG\r\n", 2400)
    assert real(er.latest_set(NOW)) == real([pdf, xlsx, chart])


def test_latest_set_starts_after_the_last_email(repo):
    put(repo, "repo/exports/sent_earlier.pdf", PDF, 1200)
    newer = put(repo, "repo/exports/after.pdf", PDF, 600)
    er.remember(["x"], NOW - 900)
    assert real(er.latest_set(NOW)) == real([newer])


def test_latest_set_stops_when_nothing_is_new(repo):
    with pytest.raises(er.Unclear, match="no finished report"):
        er.latest_set(NOW)
    put(repo, "repo/exports/team_calling_report_2026-10-01.pdf", PDF, 3 * 86400)
    with pytest.raises(er.Unclear, match="no new report since 24 hours ago.*finished 3 days ago; name it"):
        er.latest_set(NOW)
    put(repo, "repo/exports/today.pdf", PDF, 3600)
    er.remember(["x"], NOW - 60)
    with pytest.raises(er.Unclear, match="no new report since the last email"):
        er.latest_set(NOW)


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
def test_one_file_goes_in_the_original_request_shape(repo, capsys):
    path = put(repo, "repo/exports/plans/call_plan_Elite_2026-10-12.xlsx", XLSX, 600)
    mailer({"ok": True, "status": "sent", "detail": "emailed to 3 recipient(s) in live mode"}, multi=False)
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
    assert json.load(open(er.LAST_SENT))["files"] == [path]


@responses.activate
def test_the_latest_set_goes_together_in_one_email(repo, capsys):
    pdf = put(repo, SCRATCH + "/plan/exports/plans/Deepanshi_consolidated_plan_Wed_21_Oct.pdf", PDF, 600)
    xlsx = put(repo, SCRATCH + "/plan/exports/plans/Deepanshi_call_plan_Wed_21_Oct.xlsx", XLSX, 610)
    mailer({"ok": True, "status": "sent", "detail": "2 files in one email, emailed to 3 recipient(s) in live mode"})
    assert er.main([]) == 0
    sent = bodies()[1]
    assert sent["action"] == "send" and sent["resend"] is False and "file_name" not in sent
    assert sent["files"] == [{"file_name": os.path.basename(p), "sha256": hashlib.sha256(d).hexdigest(),
                              "content_b64": base64.b64encode(d).decode()} for p, d in ((pdf, PDF), (xlsx, XLSX))]
    out = capsys.readouterr().out
    assert out.count("file: ") == 2 and "result: sent - 2 files in one email" in out


@responses.activate
def test_named_files_go_together_and_an_old_mailer_stops_before_upload(repo, capsys):
    a = put(repo, "elsewhere/a.pdf", PDF, 600)
    b = put(repo, "elsewhere/b.csv", CSV, 600)
    copy = put(repo, "elsewhere/copy/a_copy.pdf", PDF, 600)
    mailer({"ok": True, "status": "sent", "detail": "2 files in one email"})
    assert er.main([a, b, copy]) == 0
    assert [f["file_name"] for f in bodies()[1]["files"]] == ["a.pdf", "b.csv"]  # same content attached once
    responses.reset()
    mailer(multi=False)
    assert er.main([a, b, "--check"]) == 2
    assert "mailer: outdated" in capsys.readouterr().out
    assert [x["action"] for x in bodies()] == ["check"]


def test_a_set_with_a_bad_file_a_shared_name_or_too_many_files_contacts_nothing(repo, monkeypatch, capsys):
    with responses.RequestsMock():
        good = put(repo, "elsewhere/a.pdf", PDF, 600)
        raw = put(repo, "repo/data/zip_calls.csv", CSV, 600)
        assert er.main([good, raw]) == 2
        assert "refused: " + raw + ": files in the repo's data/ folder" in capsys.readouterr().out
        twin = put(repo, "elsewhere/x/a.pdf", PDF + b"other", 600)
        assert er.main([good, twin]) == 2
        assert "both named a.pdf" in capsys.readouterr().out
        many = [put(repo, f"elsewhere/r{i}.csv", CSV + str(i).encode(), 600) for i in range(11)]
        assert er.main(many) == 1
        assert "more than 10 in one email" in capsys.readouterr().out
        monkeypatch.setattr(er, "MAX_BYTES", 30)
        assert er.main([good, many[0]]) == 2
        assert "too_large: over 20 MB together" in capsys.readouterr().out
        assert not os.path.exists(er.LAST_SENT)


@responses.activate
def test_check_sends_nothing(repo, capsys):
    put(repo, "repo/exports/a.csv", CSV, 600)
    mailer(mode="test", recipients=1)
    assert er.main(["--check"]) == 0
    assert [b["action"] for b in bodies()] == ["check"]
    assert "mailer: test mode, 1 approved recipient(s) (only the test address)" in capsys.readouterr().out
    assert not os.path.exists(er.LAST_SENT)
@responses.activate
def test_duplicate_and_resend(repo, capsys):
    path = put(repo, "repo/exports/a.csv", CSV, 600)
    mailer({"ok": True, "status": "duplicate", "detail": "this exact file was already emailed at 2026-10-12 09:10 IST"})
    assert er.main([path]) == 0
    assert "result: duplicate - this exact file was already emailed" in capsys.readouterr().out
    responses.reset()
    mailer({"ok": True, "status": "sent", "detail": "emailed to 3 recipient(s) in live mode"})
    assert er.main([path, "--resend"]) == 0
    assert bodies()[1]["resend"] is True


@responses.activate
def test_off_mode_stops_before_upload(repo, capsys):
    put(repo, "repo/exports/a.csv", CSV, 600)
    responses.add(responses.POST, URL, json={"ok": True, "status": "off", "mode": "off", "recipients": 0})
    assert er.main([]) == 2
    assert len(responses.calls) == 1
    assert "mailer: off - DELIVERY_MODE is off" in capsys.readouterr().out


@responses.activate
def test_refusals_from_the_mailer_fail(repo, capsys):
    put(repo, "repo/exports/a.csv", CSV, 600)
    mailer({"ok": False, "status": "limit", "detail": "already 20 emails today"})
    assert er.main([]) == 2
    assert "result: limit - already 20 emails today" in capsys.readouterr().out


@responses.activate
def test_wrong_token_or_login_page(repo, capsys):
    put(repo, "repo/exports/a.csv", CSV, 600)
    responses.add(responses.POST, URL, json={"ok": False, "status": "unauthorized"})
    assert er.main([]) == 2
    assert "mailer: unauthorized\n" in capsys.readouterr().out
    responses.reset()
    responses.add(responses.POST, URL, body="<html>Sign in</html>")
    assert er.main([]) == 2
    assert "access 'Anyone'" in capsys.readouterr().out
    assert len(responses.calls) == 2  # one retry, then give up


@responses.activate
def test_one_google_404_or_error_page_is_retried(repo, capsys):
    path = put(repo, "repo/exports/a.csv", CSV, 600)
    responses.add(responses.POST, URL, status=302, headers={"Location": ECHO})
    responses.add(responses.GET, ECHO, status=404, body="<html>Not Found</html>")
    responses.add(responses.POST, URL, status=302, headers={"Location": ECHO + "2"})
    responses.add(responses.GET, ECHO + "2", json={"ok": True, "status": "ready", "mode": "live", "recipients": 3})
    responses.add(responses.POST, URL, status=302, headers={"Location": ECHO + "3"})
    responses.add(responses.GET, ECHO + "3", body="<html>Error</html>")
    responses.add(responses.POST, URL, status=302, headers={"Location": ECHO + "4"})
    responses.add(responses.GET, ECHO + "4", json={"ok": True, "status": "sent", "detail": "emailed to 3 recipient(s)"})
    assert er.main([]) == 0
    assert "result: sent" in capsys.readouterr().out
    assert [b["action"] for b in bodies()] == ["check", "check", "send", "send"]
    responses.reset()
    responses.add(responses.POST, URL, status=404, body="<html>Not Found</html>")
    assert er.main([path]) == 2
    assert "HTTP 404" in capsys.readouterr().out and len(responses.calls) == 2


def test_unclear_refused_too_large_and_missing_settings_contact_nothing(repo, monkeypatch, capsys):
    with responses.RequestsMock():  # any HTTP call would raise
        assert er.main([]) == 1
        assert capsys.readouterr().out.startswith("unclear: no finished report")
        assert er.main([str(repo / "repo" / "exports" / "report_draft.pdf")]) == 2
        assert "refused:" in capsys.readouterr().out
        path = put(repo, "project_reports/plan_tracker_2026-10-11.pdf", PDF, 640)
        monkeypatch.setattr(er, "MAX_BYTES", 10)
        assert er.main([path]) == 2
        assert "too_large: over 20 MB" in capsys.readouterr().out
        monkeypatch.setattr(er, "MAX_BYTES", 20 * 1024 * 1024)
        monkeypatch.delenv("REPORT_EMAIL_TOKEN")
        assert er.main([path]) == 2
        assert "error: REPORT_EMAIL_TOKEN" in capsys.readouterr().out


def test_list_shows_newest_first_and_sends_nothing(repo, capsys):
    put(repo, "repo/exports/older.csv", CSV, 7200)
    put(repo, SCRATCH + "/newer.pdf", PDF, 600)
    with responses.RequestsMock():
        assert er.main(["--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [l.split(" (")[0].rsplit("/", 1)[1] for l in lines] == ["newer.pdf", "older.csv"]
