import base64
import hashlib
import io
import json
import os
import re
import shutil
import tarfile

import pytest
import responses

from scripts import state_sync as ss

URL = "https://script.google.com/macros/s/AKfy-abc_123/exec"
TOKEN = "t" * 40


class FakeMailer:
    """The mailer's save actions (email_report/Code.gs) kept in memory: parts, then a manifest once all arrived."""

    def __init__(self, on=True):
        self.on, self.parts, self.manifests, self.calls = on, {}, {}, []
        self.old = False  # a mailer from before 13 Nov 2026: no versions in its list, no format 2 fields
        self.fail_next = []  # tries answered with an HTML error page instead, in order
        self.lose = ""  # an action whose work is done but whose answer never arrives, once

    def __call__(self, request):
        body = json.loads(request.body)
        self.calls.append(body["action"])
        if self.fail_next:
            self.fail_next.pop(0)
            return 404, {}, "<html>Sorry, unable to open the file at this time.</html>"
        answer = self.handle(body)
        if body["action"] == self.lose:
            self.lose = ""
            return 500, {}, "<html>error</html>"
        return 200, {}, json.dumps(answer)

    def handle(self, b):
        if b["token"] != TOKEN:
            return {"ok": False, "status": "unauthorized"}
        if not self.on:
            return {"ok": False, "status": "error", "detail": "team chat saves are off: set the Script Property STATE_SAVES to on"}
        team, version, act = b["team"], b.get("version", ""), b["action"]
        mine = sorted((m for m in self.manifests.values() if m["team"] == team), key=lambda m: m["version"], reverse=True)
        if act == "state_list":
            if self.old:
                return {"ok": True, "status": "listed", "saves": mine}
            limit = b.get("limit") or 30
            return {"ok": True, "status": "listed", "saves": mine[:limit], "total": len(mine), "versions": [m["version"] for m in mine]}
        if act == "state_get":
            m = next((x for x in mine if x["version"] == version), None) if version else (mine[0] if mine else None)
            if not m:
                return {"ok": False, "status": "not_found", "detail": "no finished save"}
            if "part" not in b:
                return {"ok": True, "status": "found", "manifest": m}
            data = self.parts[(team, m["version"], b["part"])]
            return {"ok": True, "status": "found", "sha256": hashlib.sha256(data).hexdigest(),
                    "content_b64": base64.b64encode(data).decode()}
        if act == "state_put":
            data = base64.b64decode(b["content_b64"])
            assert hashlib.sha256(data).hexdigest() == b["sha256"]
            self.parts[(team, version, b["part"])] = data
            return {"ok": True, "status": "stored", "part": b["part"]}
        if act == "state_commit":
            if (team, version) in {(m["team"], m["version"]) for m in self.manifests.values()}:
                return {"ok": False, "status": "bad_request", "detail": f"save {version} is already finished"}
            for i in range(b["parts"]):
                if (team, version, i) not in self.parts:
                    return {"ok": False, "status": "incomplete", "detail": f"part {i} never arrived"}
            m = {"version": version, "team": team, "parts": b["parts"], "sha256": b["sha256"], "part_sha256": b["part_sha256"],
                 "size": sum(len(self.parts[(team, version, i)]) for i in range(b["parts"])), "saved_at": "2026-11-12 14:05 IST",
                 "label": b.get("label", ""), "files": b.get("files", 0)}
            if b.get("format") is not None:
                m.update(format=2, index_bytes=b["index_bytes"], changed=b["changed"], full=b.get("full") is True)
            self.manifests[(team, version)] = m
            return {"ok": True, "status": "saved", "manifest": m}
        return {"ok": False, "status": "bad_request"}


@pytest.fixture
def mailer(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORT_EMAIL_URL", URL)
    monkeypatch.setenv("REPORT_EMAIL_TOKEN", TOKEN)
    monkeypatch.setattr(ss, "WAITS", (0, 0, 0))
    monkeypatch.setattr(ss, "MARKERS", str(tmp_path / "home" / ".cache" / "state_sync"))
    monkeypatch.chdir(tmp_path)
    fake = FakeMailer()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.POST, URL, callback=fake)
        yield fake


def work(tmp_path):
    """A chat's scratchpad and the repo's data/ folder, with things that must never be saved."""
    scratch, data = tmp_path / "scratchpad", tmp_path / "repo" / "data"
    files = {scratch / "plan" / "data" / "build.py": b"print('build')\n",
             scratch / "plan" / "data" / "not_on_team.json": b'["Someone"]',
             scratch / "plan" / "exports" / "top50.xlsx": b"PK\x03\x04 workbook",
             data / "snap_merged_1112.json": json.dumps({"leads": list(range(2000))}).encode()}
    never = [scratch / "plan" / ".env", scratch / "plan" / ".git" / "config", scratch / ".venv" / "lib" / "x.py",
             scratch / "plan" / "__pycache__" / "build.cpython-313.pyc", scratch / "pv" / "pyvenv.cfg", scratch / "pv" / "lib" / "fitz.py",
             data / ".env.local"]
    for p, b in list(files.items()) + [(n, b"SECRET=1") for n in never]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    (scratch / "link").symlink_to("/etc/hostname")
    return [str(scratch), str(data)], files, never


def rel(p):
    return str(p).lstrip("/")


def test_save_then_test_load_brings_back_every_working_file_and_no_secrets(mailer, tmp_path):
    paths, files, never = work(tmp_path)
    r = ss.save("deepanshi", paths, label="12 Nov run")
    assert (r["files"], r["parts"], r["changed"], r["full"]) == (4, 1, 4, True)
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", r["version"])  # the mailer's own rule for save names
    assert mailer.calls == ["state_list", "state_put", "state_commit", "state_get"]
    out = ss.load("deepanshi", into=str(tmp_path / "spare"))
    assert out["version"] == r["version"] and out["files"] == 4
    for p, b in files.items():
        assert (tmp_path / "spare" / rel(p)).read_bytes() == b
    for p in never:
        assert not (tmp_path / "spare" / rel(p)).exists()
    assert not (tmp_path / "spare" / rel(tmp_path / "scratchpad" / "link")).exists()


def wipe(tmp_path, paths):
    """The machine is replaced: the scratchpad comes back empty, data/ and this machine's notes not at all."""
    for p in paths:
        shutil.rmtree(p)
    os.makedirs(paths[0])
    shutil.rmtree(tmp_path / "home", ignore_errors=True)


def test_after_a_reset_load_puts_files_back_where_they_were(mailer, tmp_path):
    paths, files, _ = work(tmp_path)
    ss.save("sana", paths)
    wipe(tmp_path, paths)
    out = ss.load("sana")
    assert out["paths"] == paths and out["kept_as_is"] == []
    for p, b in files.items():
        assert p.read_bytes() == b


def test_with_no_wipe_the_daily_load_leaves_everything_as_it_is(mailer, tmp_path, capsys):
    paths, files, _ = work(tmp_path)
    ss.save("darshan", paths)
    build = next(p for p in files if p.name == "build.py")
    build.write_bytes(b"newer\n")
    (build.parent / "extra.py").write_bytes(b"made after the save")
    before = len(mailer.calls)
    assert ss.main(["load", "darshan"]) == 0
    assert "already here, left as is (no wipe)" in capsys.readouterr().out
    assert mailer.calls[before:] == []  # nothing even downloaded
    assert build.read_bytes() == b"newer\n" and (build.parent / "extra.py").exists()


def test_only_missing_folders_come_back_and_nothing_here_is_overwritten(mailer, tmp_path):
    paths, files, _ = work(tmp_path)
    ss.save("inayat", paths)
    shutil.rmtree(paths[1])  # data/ gone, the scratchpad still here with a newer file
    build = next(p for p in files if p.name == "build.py")
    build.write_bytes(b"newer\n")
    out = ss.load("inayat")
    assert out["paths"] == [paths[1]] and out["kept_as_is"] == [paths[0]]
    assert build.read_bytes() == b"newer\n"
    snap = next(p for p in files if p.name == "snap_merged_1112.json")
    assert snap.read_bytes() == files[snap]


def test_replace_writes_saved_files_over_same_names_and_deletes_nothing(mailer, tmp_path):
    paths, files, _ = work(tmp_path)
    ss.save("women_ai", paths)
    build = next(p for p in files if p.name == "build.py")
    build.write_bytes(b"newer\n")
    (build.parent / "extra.py").write_bytes(b"made after the save")
    ss.load("women_ai", replace=True)
    assert build.read_bytes() == files[build] and (build.parent / "extra.py").exists()


def test_a_test_load_needs_an_empty_spare_folder(mailer, tmp_path):
    paths, _, _ = work(tmp_path)
    ss.save("sana", paths)
    ss.load("sana", into=str(tmp_path / "spare"))
    with pytest.raises(ss.SyncError, match="empty spare folder"):
        ss.load("sana", into=str(tmp_path / "spare"))


def test_big_saves_go_in_parts_and_a_damaged_part_loads_nothing(mailer, tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "PART_BYTES", 1024)
    paths, files, _ = work(tmp_path)
    big = tmp_path / "repo" / "data" / "noise.bin"
    big.write_bytes(os.urandom(5000))
    r = ss.save("inayat", paths)
    assert r["parts"] >= 5 and mailer.calls.count("state_put") == r["parts"]
    out = ss.load("inayat", into=str(tmp_path / "a"))
    assert (tmp_path / "a" / rel(big)).read_bytes() == big.read_bytes() and out["files"] == 5
    key = next(k for k in mailer.parts if k[2] == 1)
    mailer.parts[key] = b"damaged" + mailer.parts[key][7:]
    with pytest.raises(ss.SyncError, match="part 2 of .* arrived damaged"):
        ss.load("inayat", into=str(tmp_path / "b"))
    assert not (tmp_path / "b").exists()


def test_no_save_yet_stops_the_run_with_its_own_exit_status(mailer, capsys):
    assert ss.main(["load", "women_ai"]) == ss.NO_SAVE
    assert "STOP: no finished save for women_ai" in capsys.readouterr().err
    assert ss.main(["list", "women_ai"]) == 0
    assert "no finished save" in capsys.readouterr().out


def test_saves_switched_off_or_bad_input_fail_plainly(mailer, tmp_path, capsys):
    mailer.on = False
    paths, _, _ = work(tmp_path)
    assert ss.main(["save", "sana", *paths]) == 1
    assert "STATE_SAVES to on" in capsys.readouterr().err
    assert ss.main(["save", "Sana", *paths]) == 1
    assert ss.main(["save", "sana", str(tmp_path / "missing")]) == 1
    assert "does not exist" in capsys.readouterr().err
    with pytest.raises(ss.SyncError, match="too broad"):
        ss.save("sana", ["/"])


def test_google_error_pages_are_retried_and_a_lost_commit_answer_is_recovered(mailer, tmp_path, capsys):
    paths, _, _ = work(tmp_path)
    mailer.fail_next = ["page", "page"]  # the first part's first two tries
    assert ss.main(["save", "sana", *paths, "--label", "evening"]) == 0
    assert "saved sana: 4 files" in capsys.readouterr().out
    mailer.lose = "state_commit"  # the save finishes in Drive but its answer is lost; the retry hears "already finished"
    r = ss.save("sana", paths)
    rows, total = ss.saves("sana")
    assert rows[0]["version"] == r["version"] and total == 2


def test_the_mailer_never_answering_is_an_error_not_a_hang(mailer, tmp_path):
    paths, _, _ = work(tmp_path)
    mailer.fail_next = ["page"] * 4
    with pytest.raises(ss.SyncError, match="did not answer after 4 tries"):
        ss.save("sana", paths)


def test_unsafe_entries_in_a_save_are_refused():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../outside.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar, pytest.raises(ss.SyncError, match="unsafe entry"):
        ss._safe_members(tar)


def test_settings_come_from_the_environment_or_a_dotenv_above(monkeypatch, tmp_path):
    monkeypatch.delenv("REPORT_EMAIL_URL", raising=False)
    monkeypatch.delenv("REPORT_EMAIL_TOKEN", raising=False)
    (tmp_path / ".env").write_text(f"REPORT_EMAIL_URL={URL}\nexport REPORT_EMAIL_TOKEN='{TOKEN}'\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert ss.settings() == (URL, TOKEN)
    (tmp_path / ".env").write_text("REPORT_EMAIL_URL=https://example.com/exec\n")
    with pytest.raises(ss.SyncError, match="REPORT_EMAIL_URL"):
        ss.settings()


def test_runs_standalone_from_main_with_nothing_from_this_repo():
    """A chat on any branch runs main's copy through stdin, so the script may import nothing from the repo."""
    import ast

    tree = ast.parse(open(ss.__file__, encoding="utf-8").read())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"integrations", "analytics", "scripts"}


def _uploaded(mailer, version):
    """The file names inside a save's bundle (after its index)."""
    m = next(x for x in mailer.manifests.values() if x["version"] == version)
    data = b"".join(mailer.parts[(m["team"], version, i)] for i in range(m["parts"]))
    with tarfile.open(fileobj=io.BytesIO(data[m["index_bytes"]:]), mode="r:gz") as tar:
        return sorted("/" + n for n in tar.getnames())


def test_a_later_save_uploads_only_new_or_changed_files_and_a_wipe_brings_back_the_latest_of_all(mailer, tmp_path, capsys):
    paths, files, _ = work(tmp_path)
    first = ss.save("darshan", paths)
    build = next(p for p in files if p.name == "build.py")
    snap = next(p for p in files if p.name == "snap_merged_1112.json")
    xlsx = next(p for p in files if p.name == "top50.xlsx")
    build.write_bytes(b"print('build v2')\n")
    new = tmp_path / "repo" / "data" / "snap_merged_1113.json"
    new.write_bytes(b'{"leads": [1]}')
    os.utime(snap, (1, 1))  # touched, same bytes: not sent again
    xlsx.unlink()  # gone from the chat: the latest state leaves it out, Drive still has it
    assert ss.main(["save", "darshan", *paths, "--label", "13 Nov run"]) == 0
    assert "saved darshan: 4 files (2 new or changed" in capsys.readouterr().out
    second = mailer.manifests[("darshan", max(v for t, v in mailer.manifests if t == "darshan"))]
    assert (second["format"], second["changed"], second["full"], second["files"]) == (2, 2, False, 4)
    assert _uploaded(mailer, second["version"]) == sorted([str(build), str(new)])
    wipe(tmp_path, paths)
    out = ss.load("darshan")
    assert out["saves_read"] == 2 and out["files"] == 4
    assert build.read_bytes() == b"print('build v2')\n" and new.read_bytes() == b'{"leads": [1]}'
    assert snap.read_bytes() == files[snap] and not xlsx.exists()
    older = ss.load("darshan", version=first["version"], into=str(tmp_path / "old"))
    assert older["files"] == 4
    assert (tmp_path / "old" / rel(build)).read_bytes() == files[build]  # every version stays in Drive
    assert (tmp_path / "old" / rel(xlsx)).read_bytes() == files[xlsx]


def test_an_older_day_or_one_file_loads_only_into_a_spare_folder(mailer, tmp_path, capsys, monkeypatch):
    paths, files, _ = work(tmp_path)
    build = next(p for p in files if p.name == "build.py")
    names = iter(["20261112T100000Z-00000aaa", "20261113T030000Z-00000bbb"])  # 15:30 IST on 12 Nov, 08:30 IST on 13 Nov
    monkeypatch.setattr(ss, "_new_version", lambda: next(names))
    first = ss.save("sana", paths)
    build.write_bytes(b"v2\n")
    ss.save("sana", paths)
    assert ss.main(["load", "sana", "--version", first["version"]]) == 1
    assert "only with --into" in capsys.readouterr().err
    assert ss.main(["load", "sana", "--as-of", "2026-11-12", "--only", str(build), "--into", str(tmp_path / "one")]) == 0
    got = [p for p in (tmp_path / "one").rglob("*") if p.is_file()]
    assert len(got) == 1 and got[0].read_bytes() == files[build]
    assert ss.main(["load", "sana", "--as-of", "2020-01-01", "--into", str(tmp_path / "none")]) == ss.NO_SAVE


def test_the_file_list_is_kept_on_the_machine_and_read_from_drive_after_a_wipe(mailer, tmp_path):
    paths, files, _ = work(tmp_path)
    ss.save("inayat", paths)
    before = len(mailer.calls)
    ss.save("inayat", paths)
    assert "state_get" not in mailer.calls[before:-1]  # the list on this machine matched the newest save
    shutil.rmtree(tmp_path / "home")
    before = len(mailer.calls)
    r = ss.save("inayat", paths)
    assert mailer.calls[before:].count("state_get") >= 2 and r["changed"] == 0  # list read from Drive; nothing new to send


def test_a_long_chain_of_saves_starts_over_with_everything(mailer, tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "MAX_CHAIN", 2)
    paths, files, _ = work(tmp_path)
    build = next(p for p in files if p.name == "build.py")
    assert ss.save("women_ai", paths)["full"]
    build.write_bytes(b"v2\n")
    assert not ss.save("women_ai", paths)["full"]
    team = next(p for p in files if p.name == "not_on_team.json")
    team.write_bytes(b"[]")
    r = ss.save("women_ai", paths)  # a restore would now read three saves
    assert r["full"] and r["changed"] == 4
    wipe(tmp_path, paths)
    assert ss.load("women_ai")["saves_read"] == 1 and build.read_bytes() == b"v2\n" and team.read_bytes() == b"[]"


def test_a_save_after_an_old_style_bundle_sends_everything_and_old_bundles_still_load(mailer, tmp_path):
    paths, files, _ = work(tmp_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        index = json.dumps({"paths": paths}).encode()
        info = tarfile.TarInfo(ss.INDEX)
        info.size = len(index)
        tar.addfile(info, io.BytesIO(index))
        for p in files:
            tar.add(str(p), arcname=rel(p))
    data = buf.getvalue()
    v = "20261112T101010Z-0000aaaa"
    mailer.parts[("deepanshi", v, 0)] = data
    mailer.manifests[("deepanshi", v)] = {"version": v, "team": "deepanshi", "parts": 1, "size": len(data), "saved_at": "x",
                                          "sha256": hashlib.sha256(data).hexdigest(), "part_sha256": [hashlib.sha256(data).hexdigest()]}
    out = ss.load("deepanshi", into=str(tmp_path / "old"))
    assert out["files"] == 4
    r = ss.save("deepanshi", paths)
    assert r["full"] and r["changed"] == 4


def test_an_out_of_date_mailer_stops_the_save_before_anything_is_sent(mailer, tmp_path):
    mailer.old = True
    paths, _, _ = work(tmp_path)
    with pytest.raises(ss.SyncError, match="mailer is out of date"):
        ss.save("sana", paths)
    assert "state_put" not in mailer.calls


def test_an_empty_saved_folder_comes_back_as_a_folder(mailer, tmp_path):
    paths, _, _ = work(tmp_path)
    empty = tmp_path / "repo" / "exports"
    empty.mkdir(parents=True)
    ss.save("darshan", paths + [str(empty)])
    wipe(tmp_path, paths)
    shutil.rmtree(empty)
    ss.load("darshan")
    assert empty.is_dir()
