"""Save a team chat's working files to a private Drive folder, and load them back (docs/state_sync.md).

A chat's cloud machine is replaced when the chat sits idle, and everything on it that is not in GitHub is lost:
the scratchpad, the repo's data/ and exports/ folders, scripts never pushed. So a chat saves its working folders
at the end of every run (and after any one-off work) and loads them back first thing on the next run. Files go
through the report mailer (email_report/Code.gs), which keeps the newest saves per team in its private Drive
folder "Team chat saves (private)". Saves never email anyone.

Each folder is saved under its full path and comes back to the same path, which is the same on a new machine
(the session keeps its scratchpad path). .git, virtual environments, caches and .env files are never saved.
A load never deletes or overwrites anything: a saved folder that is still here with its files (no wipe) is left
exactly as it is, and only folders that are missing or empty are brought back. So the same load command is safe to
run at the start of every run, wiped or not. --replace writes saved files over files of the same name (other files
are left alone); --into DIR loads everything under a spare folder, for a test.

Standalone on purpose (standard library plus requests, nothing from this repo), so a chat on any branch can run
main's copy without switching or merging:

    git fetch -q origin main && git show origin/main:scripts/state_sync.py | python3 - load deepanshi

Needs REPORT_EMAIL_URL and REPORT_EMAIL_TOKEN (the cloud environment's settings or the repo's .env), and the
mailer's Script Property STATE_SAVES set to on.

Exit status: 0 done; 2 no finished save for this team (stop the run and say so); 1 anything else went wrong.

    python3 scripts/state_sync.py save TEAM PATH [PATH ...] [--label TEXT]   # send these folders/files to Drive
    python3 scripts/state_sync.py load TEAM [--version V] [--into DIR] [--replace]  # bring the newest save back
    python3 scripts/state_sync.py list TEAM                                   # the team's saves; changes nothing
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import sys
import tarfile
import tempfile
import time
from base64 import b64decode, b64encode
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("state_sync needs the requests package: python3 -m pip install requests")

TEAM = re.compile(r"^[a-z0-9_]{1,40}$")
URL = re.compile(r"^https://script\.google\.com/macros/s/[\w-]+/exec$")
PART_BYTES = 8 * 1024 * 1024  # the mailer takes parts up to 10 MB; base64 adds a third on the way
MAX_PARTS = 100
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}
SKIP_FILE = re.compile(r"^\.env(\..+)?$|\.py[co]$")  # secrets never leave the machine
INDEX = ".state_sync.json"  # inside each bundle: which paths it holds
MARKERS = os.path.join(os.path.expanduser("~"), ".cache", "state_sync")  # which paths this machine saved or loaded; gone after a wipe
WAITS = (5, 15, 45)  # seconds between tries when Google is slow or answers with an error page
NO_SAVE = 2


class SyncError(Exception):
    pass


class NoSave(SyncError):
    pass


# ------------------------------------------------------------------ settings and the mailer


def _dotenv(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return out


def settings() -> tuple[str, str]:
    """The mailer's address and token: the environment first, then a .env in this folder or any folder above it."""
    url, token = os.environ.get("REPORT_EMAIL_URL", "").strip(), os.environ.get("REPORT_EMAIL_TOKEN", "").strip()
    here = os.getcwd()
    while not (url and token):
        found = _dotenv(os.path.join(here, ".env"))
        url, token = url or found.get("REPORT_EMAIL_URL", ""), token or found.get("REPORT_EMAIL_TOKEN", "")
        if os.path.dirname(here) == here:
            break
        here = os.path.dirname(here)
    if not URL.match(url):
        raise SyncError("REPORT_EMAIL_URL must be the mailer's web app address, https://script.google.com/macros/s/.../exec")
    if len(token) < 32:
        raise SyncError("REPORT_EMAIL_TOKEN is missing or too short")
    return url, token


def call(url: str, body: dict, label: str, session: requests.Session) -> dict:
    """POST to the mailer and return its JSON answer, trying again when Google is slow or answers with a page.

    Every action is safe to repeat: a part sent twice replaces itself, and lists and loads change nothing."""
    problem = ""
    for wait in (*WAITS, None):
        try:
            r = session.post(url, json=body, timeout=300)
            answer = r.json() if r.status_code == 200 else None
            if isinstance(answer, dict) and "status" in answer:
                return answer
            problem = f"HTTP {r.status_code}, not the mailer's answer"
        except (requests.RequestException, ValueError) as exc:
            problem = type(exc).__name__
        if wait is None:
            break
        time.sleep(wait)
    raise SyncError(f"{label}: the mailer did not answer after {len(WAITS) + 1} tries ({problem})")


def need(answer: dict, label: str, ok: tuple[str, ...]) -> dict:
    if answer.get("status") in ok:
        return answer
    raise SyncError(f"{label}: {answer.get('status')}: {answer.get('detail', '')}".rstrip(": "))


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


# ------------------------------------------------------------------ save


def _members(paths: list[str]):
    """Every file under the paths, skipping .git, virtual environments, caches and .env files."""
    for top in paths:
        if os.path.isfile(top):
            yield top
            continue
        for root, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))
                             and not os.path.isfile(os.path.join(root, d, "pyvenv.cfg")))  # any virtual environment
            for name in sorted(files):
                full = os.path.join(root, name)
                if not SKIP_FILE.search(name) and not os.path.islink(full):
                    yield full


def bundle(paths: list[str], out) -> int:
    """Writes a .tar.gz of the paths (stored under their full path) to the open file out; returns the file count."""
    index = json.dumps({"paths": paths, "made_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}).encode()
    count = 0
    with tarfile.open(fileobj=out, mode="w:gz", compresslevel=6) as tar:
        info = tarfile.TarInfo(INDEX)
        info.size = len(index)
        info.mtime = int(time.time())
        tar.addfile(info, fileobj=io.BytesIO(index))
        for full in _members(paths):
            tar.add(full, arcname=full.lstrip("/"), recursive=False)
            count += 1
    return count


def save(team: str, paths: list[str], label: str = "", session: requests.Session | None = None) -> dict:
    session = session or requests.Session()
    paths = [os.path.abspath(p) for p in paths]
    for p in paths:
        if not os.path.exists(p):
            raise SyncError(f"{p} does not exist")
        if p == "/" or p == os.path.expanduser("~"):
            raise SyncError(f"{p} is too broad: name the working folders")
    url, token = settings()
    start = time.time()
    now = datetime.now(timezone.utc)
    # name sorts by time, down to the microsecond, so the newest save is always the one loaded
    version = now.strftime("%Y%m%dT%H%M%SZ") + f"-{now.microsecond:05x}" + secrets.token_hex(2)[:3]
    with tempfile.TemporaryFile() as tmp:
        files = bundle(paths, tmp)
        size = tmp.tell()
        parts = max(1, -(-size // PART_BYTES))
        if parts > MAX_PARTS:
            raise SyncError(f"the bundle is {mb(size)}, over {MAX_PARTS} parts: leave out what can be fetched again")
        tmp.seek(0)
        whole, part_sha = hashlib.sha256(), []
        for i in range(parts):
            chunk = tmp.read(PART_BYTES)
            whole.update(chunk)
            part_sha.append(hashlib.sha256(chunk).hexdigest())
            need(call(url, {"token": token, "action": "state_put", "team": team, "version": version, "part": i, "parts": parts,
                            "content_b64": b64encode(chunk).decode("ascii"), "sha256": part_sha[-1]},
                      f"part {i + 1} of {parts}", session), f"part {i + 1} of {parts}", ("stored",))
    commit = {"token": token, "action": "state_commit", "team": team, "version": version, "parts": parts,
              "part_sha256": part_sha, "sha256": whole.hexdigest(), "label": label[:200], "files": files}
    answer = call(url, commit, "finishing the save", session)
    if answer.get("status") != "saved":  # a lost answer to a commit that did finish shows up in the list
        listed = call(url, {"token": token, "action": "state_list", "team": team}, "checking the save", session)
        if not any(s.get("version") == version for s in listed.get("saves", [])):
            need(answer, "finishing the save", ("saved",))
    listed = need(call(url, {"token": token, "action": "state_list", "team": team}, "checking the save", session),
                  "checking the save", ("listed",))
    newest = next((x for x in listed.get("saves", []) if x.get("version") == version), {})
    if newest.get("sha256") != whole.hexdigest():
        raise SyncError("the save did not show up in Drive; run save again")
    _remember(team, paths)
    return {"version": version, "files": files, "size": size, "parts": parts, "seconds": round(time.time() - start),
            "saved_at": newest.get("saved_at", ""), "kept": len(listed["saves"])}


def _remember(team: str, paths: list[str]) -> None:
    """Notes on this machine which folders the team's save holds, so a later load can tell nothing was wiped."""
    os.makedirs(MARKERS, exist_ok=True)
    with open(os.path.join(MARKERS, f"{team}.json"), "w", encoding="utf-8") as f:
        json.dump({"paths": paths}, f)


def _remembered(team: str) -> list[str]:
    try:
        return json.load(open(os.path.join(MARKERS, f"{team}.json"), encoding="utf-8"))["paths"]
    except (OSError, ValueError, KeyError):
        return []


# ------------------------------------------------------------------ load


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    out = []
    for m in tar.getmembers():
        parts = m.name.split("/")
        if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()):
            raise SyncError(f"the save holds an unsafe entry ({m.name!r}); nothing was loaded")
        out.append(m)
    return out


def _has_files(path: str) -> bool:
    if os.path.isfile(path):
        return True
    return os.path.isdir(path) and any(files for _, _, files in os.walk(path))


def load(team: str, version: str = "", into: str = "", replace: bool = False, session: requests.Session | None = None) -> dict:
    session = session or requests.Session()
    url, token = settings()
    start = time.time()
    known = _remembered(team)
    if known and not (into or replace or version) and all(_has_files(p) for p in known):
        return {"version": "", "saved_at": "", "size": 0, "files": 0, "paths": [], "kept_as_is": known, "seconds": 0}
    got = call(url, {"token": token, "action": "state_get", "team": team, **({"version": version} if version else {})},
               "finding the save", session)
    if got.get("status") == "not_found":
        raise NoSave(f"no finished save for {team}{' named ' + version if version else ''} in Drive: {got.get('detail', '')}")
    manifest = need(got, "finding the save", ("found",))["manifest"]
    with tempfile.TemporaryFile() as tmp:
        whole = hashlib.sha256()
        for i in range(manifest["parts"]):
            label = f"part {i + 1} of {manifest['parts']}"
            part = need(call(url, {"token": token, "action": "state_get", "team": team, "version": manifest["version"], "part": i},
                             label, session), label, ("found",))
            data = b64decode(part["content_b64"])
            if hashlib.sha256(data).hexdigest() != manifest["part_sha256"][i]:
                raise SyncError(f"{label} arrived damaged; nothing was loaded, run load again")
            whole.update(data)
            tmp.write(data)
        if whole.hexdigest() != manifest["sha256"]:
            raise SyncError("the save arrived incomplete; nothing was loaded, run load again")
        tmp.seek(0)
        with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
            members = _safe_members(tar)
            index = json.loads(tar.extractfile(INDEX).read()) if INDEX in tar.getnames() else {"paths": []}
            dest = os.path.abspath(into) if into else "/"
            targets = {p: os.path.join(dest, p.lstrip("/")) for p in index["paths"]}
            busy = [t for t in targets.values() if _has_files(t)]
            if into and busy and not replace:
                raise SyncError(f"{busy[0]} already has files; pick an empty spare folder for a test load")
            # A folder still here with its files was not wiped: leave it exactly as it is unless told --replace.
            wanted = [p for p, t in targets.items() if replace or not _has_files(t)]
            under = lambda name: any(name == w.lstrip("/") or name.startswith(w.lstrip("/").rstrip("/") + "/") for w in wanted)
            keep = [m for m in members if m.name != INDEX and under(m.name)]
            os.makedirs(dest, exist_ok=True)
            kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            tar.extractall(dest, members=keep, **kwargs)
    if not into:
        _remember(team, index["paths"])
    return {"version": manifest["version"], "saved_at": manifest.get("saved_at", ""), "size": manifest["size"],
            "files": sum(1 for m in keep if m.isfile()), "paths": [targets[p] for p in wanted],
            "kept_as_is": [t for p, t in targets.items() if p not in wanted], "seconds": round(time.time() - start)}


def saves(team: str, session: requests.Session | None = None) -> list[dict]:
    url, token = settings()
    answer = call(url, {"token": token, "action": "state_list", "team": team}, "listing saves", session or requests.Session())
    return need(answer, "listing saves", ("listed",))["saves"]


# ------------------------------------------------------------------ command line


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="state_sync", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save", help="send folders/files to the team's Drive folder")
    s.add_argument("team")
    s.add_argument("paths", nargs="+")
    s.add_argument("--label", default="")
    lo = sub.add_parser("load", help="bring back saved folders that are missing or empty; folders still here are left as they are")
    lo.add_argument("team")
    lo.add_argument("--version", default="")
    lo.add_argument("--into", default="", help="load under this folder instead, e.g. for a test load")
    lo.add_argument("--replace", action="store_true", help="also write saved files over files of the same name that are still here; nothing is deleted")
    li = sub.add_parser("list", help="the team's saves, newest first")
    li.add_argument("team")
    a = ap.parse_args(argv)
    if not TEAM.match(a.team):
        print("team must use only lower-case letters, digits and _, e.g. deepanshi", file=sys.stderr)
        return 1
    try:
        if a.cmd == "save":
            r = save(a.team, a.paths, a.label)
            print(f"saved {a.team}: {r['files']:,} files, {mb(r['size'])} in {r['parts']} part(s), {r['seconds']} s; "
                  f"save {r['version']} at {r['saved_at']}; {r['kept']} save(s) kept")
        elif a.cmd == "load":
            r = load(a.team, a.version, a.into, a.replace)
            if r["paths"]:
                print(f"loaded {a.team}: save {r['version']} from {r['saved_at']}, {r['files']:,} files, {mb(r['size'])}, "
                      f"{r['seconds']} s, into {', '.join(r['paths'])}")
            if r["kept_as_is"]:
                print(f"{a.team}: already here, left as is (no wipe): {', '.join(r['kept_as_is'])}")
        else:
            rows = saves(a.team)
            print("\n".join(f"{x['saved_at']}  {x['version']}  {mb(x['size'])}  {x.get('files', 0):,} files  {x.get('label', '')}"
                            for x in rows) or f"no finished save for {a.team}")
    except NoSave as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return NO_SAVE
    except (SyncError, OSError, tarfile.TarError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
