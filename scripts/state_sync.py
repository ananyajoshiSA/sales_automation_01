"""Save a team chat's working files to a private Drive folder, and load them back (docs/state_sync.md).

A chat's cloud machine is replaced when the chat sits idle, and everything on it that is not in GitHub is lost:
the scratchpad, the repo's data/ and exports/ folders, scripts never pushed. So a chat saves its working folders
at the end of every run (and after any one-off work) and loads them back before any work. Files go through the
report mailer (email_report/Code.gs) into its private Drive folder "Team chat saves (private)", where every save is
kept forever. Saves never email anyone.

A save uploads only the files that are new or changed since the team's last save, plus an index of every file the
chat has (path, size, time, checksum, and which save holds its bytes). So Drive keeps every version of every file
ever saved, and a load can rebuild the folders exactly as they were at any save. Files already in Drive are never
uploaded again: a restore reads each earlier save that holds a file it needs, so data a run paid to fetch is fetched
from Drive, not from the APIs, after a wipe. Only the first save, and a save after an old-style save (one bundle of
everything, the first format), upload everything.

Each folder is saved under its full path and comes back to the same path, which is the same on a new machine
(the session keeps its scratchpad path). .git, virtual environments, caches and .env files are never saved.
A load never deletes or overwrites anything: a saved folder that is still here with its files (no wipe) is left
exactly as it is, and only folders that are missing or empty are brought back. So the same load command is safe to
run before any work, wiped or not. --replace writes saved files over files of the same name (other files are left
alone); --into DIR loads under a spare folder, for a test or to look at an older day (--as-of DATE, --only PATH).

Standalone on purpose (standard library plus requests, nothing from this repo), so a chat on any branch can run
main's copy without switching or merging:

    git fetch -q origin main && git show origin/main:scripts/state_sync.py | python3 - load deepanshi

Needs REPORT_EMAIL_URL and REPORT_EMAIL_TOKEN (the cloud environment's settings or the repo's .env), and the
mailer's Script Property STATE_SAVES set to on.

Exit status: 0 done; 2 no finished save for this team (stop the run and say so); 1 anything else went wrong.

    python3 scripts/state_sync.py save TEAM PATH [PATH ...] [--label TEXT]   # send new/changed files to Drive
    python3 scripts/state_sync.py load TEAM [--version V | --as-of DATE] [--into DIR] [--only PATH] [--replace]
    python3 scripts/state_sync.py list TEAM [--limit N]                      # the team's saves; changes nothing
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sys
import tarfile
import tempfile
import time
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone

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
INDEX = ".state_sync.json"  # inside an old-style bundle: which paths it holds
# --as-of dates are IST days. This script can't import integrations/timeutil.py (it runs alone from main on any
# branch), so it keeps its own copy of the offset under another name; it is used only for --as-of.
AS_OF_ZONE = timezone(timedelta(hours=5, minutes=30))
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
    seen = set()
    for top in paths:
        if os.path.isfile(top):
            walk = [(os.path.dirname(top), [], [os.path.basename(top)])]
        else:
            walk = os.walk(top)
        for root, dirs, files in walk:
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))
                             and not os.path.isfile(os.path.join(root, d, "pyvenv.cfg")))  # any virtual environment
            for name in sorted(files):
                full = os.path.join(root, name)
                if full not in seen and not SKIP_FILE.search(name) and not os.path.islink(full):
                    seen.add(full)
                    yield full


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _new_version() -> str:
    now = datetime.now(timezone.utc)
    # sorts by time, down to the microsecond, so the newest save is always the one loaded
    return now.strftime("%Y%m%dT%H%M%SZ") + f"-{now.microsecond:05x}" + secrets.token_hex(2)[:3]


def _upload(url: str, token: str, team: str, version: str, tmp, size: int, session: requests.Session) -> tuple[list[str], str]:
    parts = max(1, -(-size // PART_BYTES))
    if parts > MAX_PARTS:
        raise SyncError(f"this save is {mb(size)}, over {MAX_PARTS} parts of {mb(PART_BYTES)}: leave out what can be fetched again")
    tmp.seek(0)
    whole, part_sha = hashlib.sha256(), []
    for i in range(parts):
        chunk = tmp.read(PART_BYTES)
        whole.update(chunk)
        part_sha.append(hashlib.sha256(chunk).hexdigest())
        need(call(url, {"token": token, "action": "state_put", "team": team, "version": version, "part": i, "parts": parts,
                        "content_b64": b64encode(chunk).decode("ascii"), "sha256": part_sha[-1]},
                  f"part {i + 1} of {parts}", session), f"part {i + 1} of {parts}", ("stored",))
    return part_sha, whole.hexdigest()


def _commit(url: str, token: str, team: str, version: str, body: dict, sha: str, session: requests.Session) -> dict:
    answer = call(url, {"token": token, "action": "state_commit", "team": team, "version": version, **body}, "finishing the save", session)
    if answer.get("status") != "saved":  # a lost answer to a commit that did finish shows up when asked for by name
        got = call(url, {"token": token, "action": "state_get", "team": team, "version": version}, "checking the save", session)
        if got.get("status") != "found":
            need(answer, "finishing the save", ("saved",))
    got = need(call(url, {"token": token, "action": "state_get", "team": team, "version": version}, "checking the save", session),
               "checking the save", ("found",))
    if got["manifest"].get("sha256") != sha:
        raise SyncError("the save did not show up in Drive; run save again")
    return got["manifest"]


def _previous(url: str, token: str, team: str, session: requests.Session) -> tuple[dict | None, dict | None]:
    """The team's newest save and its index of files, or (None, None) when there is none or it is an old-style bundle."""
    listed = need(call(url, {"token": token, "action": "state_list", "team": team, "limit": 1}, "finding the last save", session),
                  "finding the last save", ("listed",))
    if "versions" not in listed:  # an older mailer would keep only 7 saves and not know this format
        raise SyncError("the mailer is out of date: paste email_report/Code.gs from main into Apps Script and deploy a new version")
    if not listed["saves"]:
        return None, None
    manifest = listed["saves"][0]
    if manifest.get("format") != 2:
        return manifest, None
    cached = _cached_index(team)
    if cached.get("version") == manifest["version"]:
        return manifest, cached
    return manifest, _fetch_index(url, token, team, manifest, session)


def save(team: str, paths: list[str], label: str = "", session: requests.Session | None = None) -> dict:
    session = session or requests.Session()
    paths = list(dict.fromkeys(os.path.abspath(p) for p in paths))
    for p in paths:
        if not os.path.exists(p):
            raise SyncError(f"{p} does not exist")
        if p == "/" or p == os.path.expanduser("~"):
            raise SyncError(f"{p} is too broad: name the working folders")
    url, token = settings()
    start = time.time()
    version = _new_version()
    last, prev = _previous(url, token, team, session)
    old = (prev or {}).get("files", {})
    files: dict[str, list] = {}
    changed: list[str] = []
    for full in _members(paths):
        st = os.stat(full)
        size, mtime = st.st_size, st.st_mtime_ns  # nanoseconds: a rewrite in the same second still counts as a change
        was = old.get(full)
        if was and was[0] == size and was[1] == mtime:
            files[full] = list(was)
            continue
        sha = _sha(full)
        if was and was[0] == size and was[2] == sha:  # touched but the same bytes: still held by the earlier save
            files[full] = [size, mtime, sha, was[3]]
            continue
        files[full] = [size, mtime, sha, version]
        changed.append(full)
    full_save = prev is None
    if full_save:  # the first save, or a save after an old-style bundle
        for f in files.values():
            f[3] = version
        changed = list(files)
    dirs = [p for p in paths if os.path.isdir(p)]
    index = gzip.compress(json.dumps({"version": version, "paths": paths, "dirs": dirs, "files": files},
                                     separators=(",", ":")).encode(), 6)
    with tempfile.TemporaryFile() as tmp:
        tmp.write(index)
        with tarfile.open(fileobj=tmp, mode="w:gz", compresslevel=6) as tar:
            for full in changed:
                tar.add(full, arcname=full.lstrip("/"), recursive=False)
        size = tmp.tell()
        part_sha, whole = _upload(url, token, team, version, tmp, size, session)
    body = {"parts": len(part_sha), "part_sha256": part_sha, "sha256": whole, "label": label[:200], "files": len(files),
            "format": 2, "index_bytes": len(index), "changed": len(changed), "full": full_save}
    manifest = _commit(url, token, team, version, body, whole, session)
    _keep_index(team, {"version": version, "paths": paths, "dirs": dirs, "files": files})
    _remember(team, paths)
    return {"version": version, "files": len(files), "changed": len(changed), "full": full_save, "size": size,
            "parts": len(part_sha), "seconds": round(time.time() - start), "saved_at": manifest.get("saved_at", ""),
            "after": (last or {}).get("version", "")}


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


def _keep_index(team: str, index: dict) -> None:
    os.makedirs(MARKERS, exist_ok=True)
    with gzip.open(os.path.join(MARKERS, f"{team}.index.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(index, f)


def _cached_index(team: str) -> dict:
    try:
        with gzip.open(os.path.join(MARKERS, f"{team}.index.json.gz"), "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, EOFError):
        return {}


# ------------------------------------------------------------------ load


def _download(url: str, token: str, team: str, manifest: dict, session: requests.Session, upto: int = 0):
    """A save's bytes, checked part by part, in a temporary file (only the first parts covering `upto` bytes if given)."""
    count = manifest["parts"] if not upto else min(manifest["parts"], -(-upto // PART_BYTES))
    tmp = tempfile.TemporaryFile()
    whole = hashlib.sha256()
    for i in range(count):
        label = f"{manifest['version']} part {i + 1} of {manifest['parts']}"
        part = need(call(url, {"token": token, "action": "state_get", "team": team, "version": manifest["version"], "part": i},
                         label, session), label, ("found",))
        data = b64decode(part["content_b64"])
        if hashlib.sha256(data).hexdigest() != manifest["part_sha256"][i]:
            tmp.close()
            raise SyncError(f"{label} arrived damaged; nothing was loaded, run load again")
        whole.update(data)
        tmp.write(data)
    if count == manifest["parts"] and whole.hexdigest() != manifest["sha256"]:
        tmp.close()
        raise SyncError(f"save {manifest['version']} arrived incomplete; nothing was loaded, run load again")
    tmp.seek(0)
    return tmp


def _fetch_index(url: str, token: str, team: str, manifest: dict, session: requests.Session) -> dict:
    with _download(url, token, team, manifest, session, upto=manifest["index_bytes"]) as tmp:
        try:
            return json.loads(gzip.decompress(tmp.read(manifest["index_bytes"])))
        except (OSError, ValueError, EOFError) as exc:
            raise SyncError(f"the file list of save {manifest['version']} could not be read ({exc}); nothing was loaded") from exc


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


def _under(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")


def _pick(url: str, token: str, team: str, version: str, as_of: str, session: requests.Session) -> dict:
    """The manifest to load: a named save, the newest save made on or before an IST day/time, or the newest save."""
    if as_of:
        try:
            when = datetime.fromisoformat(as_of.strip())
        except ValueError as exc:
            raise SyncError("--as-of must be a date like 2026-12-05 or 2026-12-05 14:30 (IST)") from exc
        if len(as_of.strip()) <= 10:
            when += timedelta(days=1)  # a whole day: every save made that day
        cutoff = when.replace(tzinfo=AS_OF_ZONE).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        listed = need(call(url, {"token": token, "action": "state_list", "team": team, "limit": 1}, "listing saves", session),
                      "listing saves", ("listed",))
        names = sorted((v for v in listed.get("versions") or [s["version"] for s in listed.get("saves", [])] if v[:16] < cutoff),
                       reverse=True)
        if not names:
            raise NoSave(f"no finished save for {team} on or before {as_of} IST")
        version = names[0]
    got = call(url, {"token": token, "action": "state_get", "team": team, **({"version": version} if version else {})},
               "finding the save", session)
    if got.get("status") == "not_found":
        raise NoSave(f"no finished save for {team}{' named ' + version if version else ''} in Drive: {got.get('detail', '')}")
    return need(got, "finding the save", ("found",))["manifest"]


def load(team: str, version: str = "", into: str = "", replace: bool = False, session: requests.Session | None = None,
         as_of: str = "", only: str = "") -> dict:
    session = session or requests.Session()
    url, token = settings()
    start = time.time()
    known = _remembered(team)
    if known and not (into or replace or version or as_of or only) and all(_has_files(p) for p in known):
        return {"version": "", "saved_at": "", "size": 0, "files": 0, "paths": [], "kept_as_is": known, "seconds": 0}
    manifest = _pick(url, token, team, version, as_of, session)
    if manifest.get("format") != 2:
        return _load_bundle(url, token, team, manifest, into, replace, only, session, start)
    index = _fetch_index(url, token, team, manifest, session)
    dest = os.path.abspath(into) if into else "/"
    targets = {p: os.path.join(dest, p.lstrip("/")) for p in index["paths"]}
    busy = [t for t in targets.values() if _has_files(t)]
    if into and busy and not replace:
        raise SyncError(f"{busy[0]} already has files; pick an empty spare folder for a test load")
    # A folder still here with its files was not wiped: leave it exactly as it is unless told --replace.
    wanted = [p for p, t in targets.items() if replace or into or not _has_files(t)]
    only = os.path.abspath(only) if only else ""
    pick = {f: e for f, e in index["files"].items()
            if any(_under(f, w) for w in wanted) and (not only or _under(f, only))}
    by_save: dict[str, set[str]] = {}
    for f, e in pick.items():
        by_save.setdefault(e[3], set()).add(f.lstrip("/"))
    # fetch every save that holds a needed file and check it all before writing a single file
    staged = tempfile.mkdtemp(prefix="state_sync_")
    size = 0
    try:
        kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        for v in sorted(by_save):
            m = manifest if v == manifest["version"] else _pick(url, token, team, v, "", session)
            size += m["size"]
            with _download(url, token, team, m, session) as tmp, tempfile.TemporaryFile() as part:
                tmp.seek(m["index_bytes"])  # gzip rewinds to the file's start, so the bundle gets a file of its own
                shutil.copyfileobj(tmp, part)
                part.seek(0)
                with tarfile.open(fileobj=part, mode="r:gz") as tar:
                    members = [x for x in _safe_members(tar) if x.name in by_save[v]]
                    missing = by_save[v] - {x.name for x in members}
                    if missing:
                        raise SyncError(f"save {v} lacks {len(missing)} file(s) its list names, e.g. /{sorted(missing)[0]}; nothing was loaded")
                    tar.extractall(staged, members=members, **kwargs)
        for f, e in pick.items():
            got = os.path.join(staged, f.lstrip("/"))
            if os.path.getsize(got) != e[0] or _sha(got) != e[2]:
                raise SyncError(f"/{f.lstrip('/')} came back different from what was saved; nothing was loaded")
        for f, e in pick.items():
            out = os.path.join(dest, f.lstrip("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.move(os.path.join(staged, f.lstrip("/")), out)
            os.utime(out, ns=(e[1], e[1]))
        for p in wanted:  # a saved folder that was empty comes back as a folder
            if p in index.get("dirs", []) and not only:
                os.makedirs(targets[p], exist_ok=True)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    if not into and not only:
        _remember(team, index["paths"])
        if manifest["version"] == _newest_version(url, token, team, session):
            _keep_index(team, index)
    return {"version": manifest["version"], "saved_at": manifest.get("saved_at", ""), "size": size, "files": len(pick),
            "saves_read": len(by_save), "paths": [targets[p] for p in wanted],
            "kept_as_is": [t for p, t in targets.items() if p not in wanted], "seconds": round(time.time() - start)}


def _newest_version(url: str, token: str, team: str, session: requests.Session) -> str:
    got = call(url, {"token": token, "action": "state_get", "team": team}, "finding the newest save", session)
    return got.get("manifest", {}).get("version", "") if got.get("status") == "found" else ""


def _load_bundle(url, token, team, manifest, into, replace, only, session, start) -> dict:
    """An old-style save: one bundle of every file with its own index inside."""
    with _download(url, token, team, manifest, session) as tmp:
        with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
            members = _safe_members(tar)
            index = json.loads(tar.extractfile(INDEX).read()) if INDEX in tar.getnames() else {"paths": []}
            dest = os.path.abspath(into) if into else "/"
            targets = {p: os.path.join(dest, p.lstrip("/")) for p in index["paths"]}
            busy = [t for t in targets.values() if _has_files(t)]
            if into and busy and not replace:
                raise SyncError(f"{busy[0]} already has files; pick an empty spare folder for a test load")
            wanted = [p for p, t in targets.items() if replace or into or not _has_files(t)]
            only = os.path.abspath(only).lstrip("/") if only else ""
            keep = [m for m in members if m.name != INDEX and any(_under(m.name, w.lstrip("/")) for w in wanted)
                    and (not only or _under(m.name, only))]
            os.makedirs(dest, exist_ok=True)
            kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            tar.extractall(dest, members=keep, **kwargs)
    if not into and not only:
        _remember(team, index["paths"])
    return {"version": manifest["version"], "saved_at": manifest.get("saved_at", ""), "size": manifest["size"],
            "files": sum(1 for m in keep if m.isfile()), "saves_read": 1, "paths": [targets[p] for p in wanted],
            "kept_as_is": [t for p, t in targets.items() if p not in wanted], "seconds": round(time.time() - start)}


def saves(team: str, limit: int = 30, session: requests.Session | None = None) -> tuple[list[dict], int]:
    url, token = settings()
    answer = call(url, {"token": token, "action": "state_list", "team": team, "limit": limit}, "listing saves",
                  session or requests.Session())
    listed = need(answer, "listing saves", ("listed",))
    return listed["saves"], int(listed.get("total", len(listed["saves"])))


# ------------------------------------------------------------------ command line


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="state_sync", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save", help="send new or changed files under these folders to the team's Drive folder")
    s.add_argument("team")
    s.add_argument("paths", nargs="+")
    s.add_argument("--label", default="")
    lo = sub.add_parser("load", help="bring back saved folders that are missing or empty; folders still here are left as they are")
    lo.add_argument("team")
    when = lo.add_mutually_exclusive_group()
    when.add_argument("--version", default="", help="a save's name, from list")
    when.add_argument("--as-of", default="", help="the newest save made on or before this IST day or time, e.g. 2026-12-05")
    lo.add_argument("--into", default="", help="load under this folder instead, e.g. for a test or an older day")
    lo.add_argument("--only", default="", help="only this file or folder (its full path as saved)")
    lo.add_argument("--replace", action="store_true", help="also write saved files over files of the same name that are still here; nothing is deleted")
    li = sub.add_parser("list", help="the team's saves, newest first")
    li.add_argument("team")
    li.add_argument("--limit", type=int, default=30)
    a = ap.parse_args(argv)
    if not TEAM.match(a.team):
        print("team must use only lower-case letters, digits and _, e.g. deepanshi", file=sys.stderr)
        return 1
    try:
        if a.cmd == "save":
            r = save(a.team, a.paths, a.label)
            what = "all uploaded" if r["full"] else f"{r['changed']:,} new or changed"
            print(f"saved {a.team}: {r['files']:,} files ({what}, {mb(r['size'])} in {r['parts']} part(s)), "
                  f"{r['seconds']} s; save {r['version']} at {r['saved_at']}; every save is kept")
        elif a.cmd == "load":
            if (a.version or a.as_of or a.only) and not a.into:
                print("an older save or a single path loads only with --into a spare folder, so nothing here is touched", file=sys.stderr)
                return 1
            r = load(a.team, a.version, a.into, a.replace, as_of=a.as_of, only=a.only)
            if r["paths"]:
                print(f"loaded {a.team}: save {r['version']} from {r['saved_at']}, {r['files']:,} files, {mb(r['size'])} read from "
                      f"{r['saves_read']} save(s), {r['seconds']} s, into {', '.join(r['paths'])}")
            if r["kept_as_is"]:
                print(f"{a.team}: already here, left as is (no wipe): {', '.join(r['kept_as_is'])}")
        else:
            rows, total = saves(a.team, a.limit)
            print("\n".join(f"{x['saved_at']}  {x['version']}  {mb(x['size'])}  {x.get('files', 0):,} files"
                            f"{'' if x.get('format') != 2 else ' (' + ('everything' if x.get('full') else str(x.get('changed', 0)) + ' changed') + ')'}"
                            f"  {x.get('label', '')}" for x in rows) or f"no finished save for {a.team}")
            if total > len(rows):
                print(f"... {total - len(rows)} older save(s); list {a.team} --limit {total} shows all")
    except NoSave as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return NO_SAVE
    except (SyncError, OSError, tarfile.TarError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
