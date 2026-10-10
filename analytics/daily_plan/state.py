"""Plan state kept between runs without lead details: lead IDs are stored only as salted hashes.

The morning run saves today's tiers and priority groups; the status checks and the next morning's review
read them back and match them to the leads in a fresh LeadSquared snapshot by hashing those IDs again.
"""

from __future__ import annotations

import hashlib
import json
import os

SALT = "elite-plan-v1"


def h(lead_id: str) -> str:
    return hashlib.sha256((SALT + lead_id).encode()).hexdigest()[:20]


def to_state(date: str, by_owner: dict, priority: list[dict]) -> dict:
    """Only owner, tier, chance, group, check-by and flags per hashed lead ID: nothing that identifies a lead."""
    pri = {p["lead_id"]: p for p in priority if p.get("lead_id")}
    leads = {}
    for owner, rows in by_owner.items():
        for r in rows:
            p = pri.get(r["lead_id"], {})
            leads[h(r["lead_id"])] = {"owner": owner, "tier": r["tier"], "chance": r.get("chance", 0),
                                      "verify": bool(r.get("verify")), "group": p.get("group"), "check_by": p.get("check_by", ""),
                                      "bootcamp": r.get("status") == "bootcamp"}
    for lid, p in pri.items():  # priority leads owned outside the sheets still count
        leads.setdefault(h(lid), {"owner": p.get("owner"), "tier": "A", "chance": p.get("chance", 0), "verify": False,
                                  "group": p.get("group"), "check_by": p.get("check_by", "")})
    return {"date": date, "version": 1, "leads": leads}


def save(state: dict, directory: str) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{state['date']}.json")
    json.dump(state, open(path, "w"), separators=(",", ":"), sort_keys=True)
    return path


def load(directory: str, date: str) -> dict | None:
    path = os.path.join(directory, f"{date}.json")
    return json.load(open(path)) if os.path.exists(path) else None


def resolve(state: dict | None, lead_ids) -> dict:
    """{lead_id: entry} for the snapshot leads that appear in the state."""
    if not state:
        return {}
    by_hash = state["leads"]
    return {lid: by_hash[h(lid)] for lid in lead_ids if h(lid) in by_hash}



def _git(*args, check=True):
    import subprocess

    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def branch() -> str:
    return os.environ.get("PLAN_STATE_BRANCH") or _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def checkout(directory: str = "plan_state") -> str:
    """Bring the state folder up to date with the branch it lives on (this repository's working branch)."""
    _git("pull", "-q", "origin", branch(), check=False)
    os.makedirs(directory, exist_ok=True)
    return directory


def publish(directory: str, date: str) -> None:
    """Commit and push the state folder; retries the push on network errors (2, 4, 8, 16 s)."""
    import time

    _git("add", directory)
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    _git("commit", "-q", "-m", f"Plan state {date} (hashed lead IDs only)", "--", directory)
    for wait in (2, 4, 8, 16, 0):
        if _git("push", "-q", "origin", f"HEAD:{branch()}", check=False).returncode == 0:
            return
        if not wait:
            raise RuntimeError("could not push plan state")
        _git("pull", "-q", "--rebase", "origin", branch(), check=False)
        time.sleep(wait)
