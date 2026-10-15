"""Shared admission control for LeadSquared requests: one gate per LeadSquared account, shared by every
Python process on this machine through a SQLite file in the git-ignored ``data/`` folder.

``LeadSquaredClient.request`` takes a slot here before every HTTP attempt, retries included:

- at most ``LEADSQUARED_MAX_IN_FLIGHT`` attempts (default 1) run at a time across all processes using the file;
- each starts no sooner than ``LEADSQUARED_COOLDOWN_SECONDS`` (default 6) after the previous one finished;
- at most ``LEADSQUARED_MAX_REQUESTS_PER_WINDOW`` attempts (default 10) start in any ``LEADSQUARED_WINDOW_SECONDS``
  (default 5), a ceiling that still holds when the cooldown is lowered or more attempts may run at once;
- optional rolling budgets (operator-set, off unless set): attempts per hour and per day, requested records
  per day, and attempts per hour for one endpoint category;
- a 429's ``Retry-After`` holds every caller (the client calls ``hold_until`` before freeing its slot).

A wait longer than ``LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS`` or an exhausted budget raises ``AdmissionDeferred``
(with the next eligible time) instead of sleeping; a store that can't be used raises ``CoordinatorUnavailable``
and nothing is sent. Every admitted attempt is counted when it is admitted, so a crash between admission and
sending is never refunded. In-flight slots are leases owned by a random token, renewed while the request runs;
a crashed process's slot frees itself when its lease runs out, and a caller whose lease was taken over can't
free the new holder's slot.

Deadlines in the file are wall-clock seconds (``time.time()``), which every process on one machine shares;
waits are measured locally with ``time.monotonic()``. Not covered: other machines (keep the file on local disk,
never on a network mount such as /mnt/project-files, where SQLite locking is unreliable). Details:
docs/leadsquared_load_control.md.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from integrations.env import REPO_ROOT

log = logging.getLogger("integrations.leadsquared")

DEFAULT_DB = os.path.join(REPO_ROOT, "data", "leadsquared", "admission.sqlite3")
HOUR, DAY = 3600.0, 86400.0
BUSY_POLL = 0.25          # seconds between checks while every slot is taken
KEEP_HISTORY = 2 * DAY    # attempt rows older than this are pruned

# Endpoint categories, by path (never by HTTP verb: LeadSquared reads often use POST).
CATEGORIES = ("lead_history", "activity_bulk", "lead_bulk_ids", "lead_read", "metadata", "write", "other")
_CATEGORY_PATHS = (
    ("ProspectActivity.svc/Retrieve", "lead_history"),
    ("ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent", "activity_bulk"),
    ("LeadManagement.svc/Leads/Retrieve/ByIds", "lead_bulk_ids"),
    ("LeadManagement.svc/Leads.Get", "lead_read"),
    ("LeadManagement.svc/Leads.GetById", "lead_read"),
    ("LeadManagement.svc/Leads.GetByEmailaddress", "lead_read"),
    ("LeadManagement.svc/RetrieveLeadByPhoneNumber", "lead_read"),
    ("LeadManagement.svc/LeadsMetaData.Get", "metadata"),
    ("ProspectActivity.svc/ActivityTypes.Get", "metadata"),
    ("UserManagement.svc/Users.Get", "metadata"),
    ("LeadManagement.svc/Lead.Create", "write"),
    ("LeadManagement.svc/Lead.Update", "write"),
    ("LeadManagement.svc/Lead.CreateOrUpdate", "write"),
    ("ProspectActivity.svc/Create", "write"),
)

# Clocks, looked up on each call so tests can swap them for a fake one.
wall_clock: Callable[[], float] = time.time
monotonic: Callable[[], float] = time.monotonic
sleep: Callable[[float], None] = time.sleep


def endpoint_category(path: str) -> str:
    p = path.lstrip("/").split("?")[0]
    return next((c for prefix, c in _CATEGORY_PATHS if p == prefix), "other")


def requested_records(json_body: Any) -> int:
    """A conservative record count for the records budget: the number of lead IDs sent to a known-ID bulk read,
    else the page size asked for (the most a page can return), else 1."""
    if isinstance(json_body, dict):
        ids = (json_body.get("SearchParameters") or {}).get("LeadIds") if isinstance(
            json_body.get("SearchParameters"), dict) else None
        if isinstance(ids, list):
            return max(1, len(ids))
        paging = json_body.get("Paging")
        if isinstance(paging, dict):
            for key in ("PageSize", "RowCount"):
                try:
                    n = int(paging.get(key) or 0)
                except (TypeError, ValueError):
                    n = 0
                if n > 0:
                    return n
    return 1


class AdmissionError(Exception):
    """Base for admission problems (the client re-raises these as LeadSquaredError subclasses)."""


class AdmissionDeferred(AdmissionError):
    """No request was sent: a budget is used up or the wait is too long. Retry at ``next_eligible`` (UTC)."""

    def __init__(self, reason: str, next_eligible: datetime | None):
        when = next_eligible.strftime("%Y-%m-%d %H:%M:%S UTC") if next_eligible else "unknown"
        super().__init__(f"LeadSquared request deferred: {reason}; next eligible at {when}")
        self.reason = reason
        self.next_eligible = next_eligible


class CoordinatorUnavailable(AdmissionError):
    """The shared admission store can't be used, so no request is sent."""


# ------------------------------------------------------------------ configuration

def _env_number(name: str, default: float | None, *, minimum: float, integer: bool = False,
                allow_off: bool = False, strict_min: bool = False) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if allow_off and raw.lower() == "off":
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    bad = not math.isfinite(value) or value < minimum or (strict_min and value <= minimum) \
        or (integer and value != int(value))
    if bad:
        kind = "a whole number" if integer else "a finite number"
        lower = f"above {minimum:g}" if strict_min else f"{minimum:g} or more"
        off = ', or "off"' if allow_off else ""
        raise ValueError(f"{name} must be {kind}, {lower}{off} (got {raw!r})")
    return int(value) if integer else value


def _endpoint_budgets(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or name not in CATEGORIES or not value.isdigit() or int(value) < 1:
            raise ValueError(f"LEADSQUARED_ENDPOINT_HOURLY_BUDGETS must look like 'lead_history=600,activity_bulk=200' "
                             f"with categories from {', '.join(CATEGORIES)} (got {part!r})")
        out[name] = int(value)
    return out


@dataclass(frozen=True)
class AdmissionConfig:
    cooldown: float = 6.0
    max_in_flight: int = 1
    per_window: int | None = 10          # attempts started in any `window` seconds; None = off
    window: float = 5.0
    per_hour: int | None = None          # None = off
    per_day: int | None = None
    records_per_day: int | None = None
    endpoint_per_hour: dict[str, int] = field(default_factory=dict)
    max_wait: float = 300.0
    lease: float = 120.0
    max_ids_per_bulk: int = 250

    @classmethod
    def from_env(cls) -> "AdmissionConfig":
        return cls(
            cooldown=_env_number("LEADSQUARED_COOLDOWN_SECONDS", 6.0, minimum=0),
            max_in_flight=_env_number("LEADSQUARED_MAX_IN_FLIGHT", 1, minimum=1, integer=True),
            per_window=_env_number("LEADSQUARED_MAX_REQUESTS_PER_WINDOW", 10, minimum=1, integer=True,
                                   allow_off=True),
            window=_env_number("LEADSQUARED_WINDOW_SECONDS", 5.0, minimum=0, strict_min=True),
            per_hour=_env_number("LEADSQUARED_MAX_REQUESTS_PER_HOUR", None, minimum=1, integer=True, allow_off=True),
            per_day=_env_number("LEADSQUARED_MAX_REQUESTS_PER_DAY", None, minimum=1, integer=True, allow_off=True),
            records_per_day=_env_number("LEADSQUARED_MAX_RECORDS_PER_DAY", None, minimum=1, integer=True,
                                        allow_off=True),
            endpoint_per_hour=_endpoint_budgets(os.environ.get("LEADSQUARED_ENDPOINT_HOURLY_BUDGETS", "")),
            max_wait=_env_number("LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS", 300.0, minimum=0),
            lease=_env_number("LEADSQUARED_LEASE_SECONDS", 120.0, minimum=0, strict_min=True),
            max_ids_per_bulk=_env_number("LEADSQUARED_MAX_IDS_PER_BULK_REQUEST", 250, minimum=1, integer=True),
        )


def account_label(host: str, access_key: str) -> str:
    """``LEADSQUARED_ACCOUNT_LABEL`` if set (set it to one value everywhere when several keys reach one account),
    else a non-secret fingerprint of the host and access key."""
    label = os.environ.get("LEADSQUARED_ACCOUNT_LABEL", "").strip()
    if label:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", label):
            raise ValueError("LEADSQUARED_ACCOUNT_LABEL must be 1-64 letters, digits, '.', '_' or '-'")
        return label
    return "key-" + hashlib.sha256(f"{host}|{access_key}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------ the gate

DDL = """
CREATE TABLE IF NOT EXISTS gate (account TEXT PRIMARY KEY, next_start REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS slots (account TEXT NOT NULL, slot INTEGER NOT NULL, holder TEXT, lease_until REAL,
                                  PRIMARY KEY (account, slot));
CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, account TEXT NOT NULL, at REAL NOT NULL,
                                     category TEXT NOT NULL, records INTEGER NOT NULL, status TEXT NOT NULL,
                                     duration REAL, waited REAL);
CREATE INDEX IF NOT EXISTS attempts_by_time ON attempts (account, at);
CREATE TABLE IF NOT EXISTS deferrals (account TEXT NOT NULL, at REAL NOT NULL, category TEXT NOT NULL,
                                      reason TEXT NOT NULL, next_eligible REAL);
"""


@dataclass
class Ticket:
    token: str
    slot: int
    attempt_id: int
    category: str
    started: float          # monotonic
    waited: float


def _utc(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, timezone.utc) if ts is not None and math.isfinite(ts) else None


class Admission:
    """The gate for one LeadSquared account in one SQLite file. Safe to share between threads."""

    def __init__(self, path: str, account: str, config: AdmissionConfig):
        self.path, self.account, self.config = path, account, config
        self._local = threading.local()
        try:
            if path != ":memory:":
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._conn().executescript(DDL)
        except (OSError, sqlite3.Error) as exc:
            raise self._unavailable(exc) from exc

    # -- storage
    def _unavailable(self, exc: Exception) -> CoordinatorUnavailable:
        return CoordinatorUnavailable(
            f"LeadSquared admission store {self.path} can't be used ({exc}); no request was sent. Check the file "
            "and folder are writable, or point LEADSQUARED_ADMISSION_DB at a writable local file.")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA busy_timeout = 30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """A short write transaction; never held across an HTTP call or a wait."""
        try:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise self._unavailable(exc) from exc
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            try:
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                raise self._unavailable(exc) from exc

    # -- admission
    def _budget_block(self, db: sqlite3.Connection, now: float, category: str, records: int) -> tuple[str, float] | None:
        """(reason, next eligible wall time) for the first budget this attempt would break, else None."""
        c = self.config
        checks: list[tuple[str, float, int, str, tuple]] = []
        if c.per_hour is not None:
            checks.append(("hourly request budget", HOUR, c.per_hour, "", ()))
        if c.per_day is not None:
            checks.append(("daily request budget", DAY, c.per_day, "", ()))
        if category in c.endpoint_per_hour:
            checks.append((f"hourly {category} budget", HOUR, c.endpoint_per_hour[category], " AND category = ?",
                           (category,)))
        for reason, window, limit, extra, args in checks:
            n = db.execute(f"SELECT COUNT(*) FROM attempts WHERE account = ? AND at > ?{extra}",
                           (self.account, now - window, *args)).fetchone()[0]
            if n >= limit:   # rolling window: free again when the (n - limit + 1)th oldest attempt ages out
                at = db.execute(f"SELECT at FROM attempts WHERE account = ? AND at > ?{extra} ORDER BY at "
                                "LIMIT 1 OFFSET ?", (self.account, now - window, *args, n - limit)).fetchone()[0]
                return reason, at + window
        if c.records_per_day is not None:
            if records > c.records_per_day:
                return f"one request asks for {records} records, above the daily record budget", math.inf
            rows = db.execute("SELECT at, records FROM attempts WHERE account = ? AND at > ? ORDER BY at",
                              (self.account, now - DAY)).fetchall()
            used = sum(r for _, r in rows)
            if used + records > c.records_per_day:
                for at, r in rows:
                    used -= r
                    if used + records <= c.records_per_day:
                        return "daily record budget", at + DAY
        return None

    def _window_free(self, db: sqlite3.Connection, now: float) -> float | None:
        """When the next attempt may start under the per-window limit, if that is later than now, else None."""
        c = self.config
        if c.per_window is None:
            return None
        n = db.execute("SELECT COUNT(*) FROM attempts WHERE account = ? AND at > ?",
                       (self.account, now - c.window)).fetchone()[0]
        if n < c.per_window:
            return None
        at = db.execute("SELECT at FROM attempts WHERE account = ? AND at > ? ORDER BY at LIMIT 1 OFFSET ?",
                        (self.account, now - c.window, n - c.per_window)).fetchone()[0]
        return at + c.window

    def _try_admit(self, category: str, records: int, not_before: float):
        c = self.config
        with self._tx() as db:
            now = wall_clock()
            db.execute("INSERT OR IGNORE INTO gate (account, next_start) VALUES (?, 0)", (self.account,))
            next_start = db.execute("SELECT next_start FROM gate WHERE account = ?", (self.account,)).fetchone()[0]
            start_at = max(next_start, not_before)
            if start_at > now:
                reason = "retry backoff" if not_before > next_start else "cooldown or a LeadSquared Retry-After hold"
                return ("wait", start_at, reason)
            window_free = self._window_free(db, now)
            if window_free is not None:
                return ("wait", window_free, f"{c.per_window} requests per {c.window:g} s limit")
            db.executemany("INSERT OR IGNORE INTO slots (account, slot) VALUES (?, ?)",
                           [(self.account, i) for i in range(c.max_in_flight)])
            free = db.execute("SELECT slot FROM slots WHERE account = ? AND slot < ? AND (holder IS NULL OR "
                              "lease_until <= ?) ORDER BY slot LIMIT 1", (self.account, c.max_in_flight, now)).fetchone()
            if free is None:
                return ("busy", None, "every in-flight slot is taken")
            block = self._budget_block(db, now, category, records)
            if block:
                db.execute("INSERT INTO deferrals VALUES (?, ?, ?, ?, ?)",
                           (self.account, now, category, block[0], block[1] if math.isfinite(block[1]) else None))
                return ("defer", block[1], block[0])
            token = uuid.uuid4().hex
            db.execute("UPDATE slots SET holder = ?, lease_until = ? WHERE account = ? AND slot = ?",
                       (token, now + c.lease, self.account, free[0]))
            attempt = db.execute("INSERT INTO attempts (account, at, category, records, status) "
                                 "VALUES (?, ?, ?, ?, 'reserved')", (self.account, now, category, records)).lastrowid
            db.execute("DELETE FROM attempts WHERE account = ? AND at < ?", (self.account, now - KEEP_HISTORY))
            db.execute("DELETE FROM deferrals WHERE account = ? AND at < ?", (self.account, now - KEEP_HISTORY))
            return ("go", (token, free[0], attempt), "")

    def acquire(self, category: str = "other", records: int = 1, not_before: float = -math.inf) -> Ticket:
        """Wait (briefly) for a slot and reserve the attempt; raise AdmissionDeferred instead of a long wait."""
        t0 = monotonic()
        while True:
            kind, value, reason = self._try_admit(category, records, not_before)
            if kind == "go":
                token, slot, attempt = value
                waited = monotonic() - t0
                return Ticket(token, slot, attempt, category, monotonic(), waited)
            if kind == "defer":
                self._deferred(category, reason, value)
            now = wall_clock()
            if kind == "wait":
                if value - now > self.config.max_wait:
                    self._deferred(category, f"{reason} lasts longer than the {self.config.max_wait:g} s wait limit",
                                   value, record=True)
                pause = value - now
            else:
                if monotonic() - t0 > self.config.max_wait:
                    self._deferred(category, f"{reason} for over {self.config.max_wait:g} s", now + BUSY_POLL,
                                   record=True)
                pause = BUSY_POLL
            if pause > 0:
                sleep(pause)

    def _deferred(self, category: str, reason: str, when: float, record: bool = False):
        if record:
            try:
                with self._tx() as db:
                    db.execute("INSERT INTO deferrals VALUES (?, ?, ?, ?, ?)",
                               (self.account, wall_clock(), category, reason, when if math.isfinite(when) else None))
            except CoordinatorUnavailable:
                pass
        log.warning("LeadSquared %s request deferred: %s", category, reason)
        raise AdmissionDeferred(reason, _utc(when))

    def renew(self, ticket: Ticket) -> bool:
        """Extend the lease while the request runs; False if another caller now owns the slot."""
        with self._tx() as db:
            return db.execute("UPDATE slots SET lease_until = ? WHERE account = ? AND slot = ? AND holder = ?",
                              (wall_clock() + self.config.lease, self.account, ticket.slot, ticket.token)).rowcount == 1

    def release(self, ticket: Ticket, status: str) -> None:
        """Free the slot (only if this ticket still owns it), start the cooldown and record the outcome.
        Never raises: if the store fails here, the lease runs out and frees the slot."""
        duration = monotonic() - ticket.started
        try:
            with self._tx() as db:
                now = wall_clock()
                owned = db.execute("UPDATE slots SET holder = NULL, lease_until = NULL WHERE account = ? AND slot = ? "
                                   "AND holder = ?", (self.account, ticket.slot, ticket.token)).rowcount == 1
                db.execute("UPDATE gate SET next_start = MAX(next_start, ?) WHERE account = ?",
                           (now + self.config.cooldown, self.account))
                db.execute("UPDATE attempts SET status = ?, duration = ?, waited = ? WHERE id = ?",
                           (status if owned else f"{status} (lease lost)", duration, ticket.waited, ticket.attempt_id))
            if not owned:
                log.warning("LeadSquared %s request outlived its lease; another request may have overlapped it",
                            ticket.category)
            log.debug("LeadSquared %s attempt: %s in %.2f s after waiting %.2f s", ticket.category, status, duration,
                      ticket.waited)
        except CoordinatorUnavailable as exc:
            log.error("Could not free the LeadSquared slot (it frees itself when its lease ends): %s", exc)

    def hold_until(self, deadline: float) -> None:
        """No attempt starts before ``deadline`` (wall seconds). Never moves the deadline earlier."""
        with self._tx() as db:
            db.execute("INSERT OR IGNORE INTO gate (account, next_start) VALUES (?, 0)", (self.account,))
            db.execute("UPDATE gate SET next_start = MAX(next_start, ?) WHERE account = ?", (deadline, self.account))

    @contextmanager
    def slot(self, category: str = "other", records: int = 1, not_before: float = -math.inf) -> Iterator[dict]:
        """Hold a slot for one HTTP attempt. Set ``outcome["status"]`` inside; the lease is renewed meanwhile."""
        ticket = self.acquire(category, records, not_before)
        outcome = {"status": "interrupted"}
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(ticket, stop), daemon=True)
        try:
            beat.start()
            yield outcome
        finally:
            stop.set()
            beat.join(timeout=5)
            self.release(ticket, outcome["status"])

    def _heartbeat(self, ticket: Ticket, stop: threading.Event) -> None:
        while not stop.wait(self.config.lease / 3):
            try:
                if not self.renew(ticket):
                    log.warning("LeadSquared %s request lost its lease while running", ticket.category)
                    return
            except CoordinatorUnavailable as exc:
                log.error("Could not renew the LeadSquared lease: %s", exc)
                return

    # -- reporting
    def status(self) -> dict:
        """Counts only (no URLs, keys or lead data): what is running, what was used, what is left."""
        c, now = self.config, wall_clock()
        db = self._conn()
        try:
            def count(window: float, extra: str = "", args: tuple = ()) -> int:
                return db.execute(f"SELECT COUNT(*) FROM attempts WHERE account = ? AND at > ?{extra}",
                                  (self.account, now - window, *args)).fetchone()[0]
            row = db.execute("SELECT next_start FROM gate WHERE account = ?", (self.account,)).fetchone()
            busy = db.execute("SELECT COUNT(*) FROM slots WHERE account = ? AND holder IS NOT NULL AND lease_until > ?",
                              (self.account, now)).fetchone()[0]
            records = db.execute("SELECT COALESCE(SUM(records), 0) FROM attempts WHERE account = ? AND at > ?",
                                 (self.account, now - DAY)).fetchone()[0]
            by_status = dict(db.execute("SELECT status, COUNT(*) FROM attempts WHERE account = ? AND at > ? "
                                        "GROUP BY status", (self.account, now - DAY)).fetchall())
            by_category = dict(db.execute("SELECT category, COUNT(*) FROM attempts WHERE account = ? AND at > ? "
                                          "GROUP BY category", (self.account, now - DAY)).fetchall())
            avg = db.execute("SELECT AVG(duration), AVG(waited) FROM attempts WHERE account = ? AND at > ? AND "
                             "duration IS NOT NULL", (self.account, now - DAY)).fetchone()
            last = db.execute("SELECT at, category, reason, next_eligible FROM deferrals WHERE account = ? "
                              "ORDER BY at DESC LIMIT 1", (self.account,)).fetchone()
        except sqlite3.Error as exc:
            raise self._unavailable(exc) from exc
        hour, day = count(HOUR), count(DAY)

        def left(limit: int | None, used: int) -> int | str:
            return "off" if limit is None else max(0, limit - used)

        fmt = lambda t: _utc(t).strftime("%Y-%m-%d %H:%M:%S UTC") if _utc(t) else None  # noqa: E731
        return {
            "account": self.account, "store": self.path,
            "settings": {"cooldown_s": c.cooldown, "max_in_flight": c.max_in_flight,
                         "max_requests_per_window": c.per_window if c.per_window is not None else "off",
                         "window_s": c.window, "max_wait_s": c.max_wait,
                         "lease_s": c.lease, "max_ids_per_bulk_request": c.max_ids_per_bulk},
            "in_flight": busy, "next_start": fmt(row[0]) if row and row[0] > now else "now",
            "attempts_last_hour": hour, "attempts_last_24h": day, "records_last_24h": records,
            "budget_left": {"hourly": left(c.per_hour, hour), "daily": left(c.per_day, day),
                            "records_daily": left(c.records_per_day, records),
                            **{f"hourly_{k}": max(0, v - count(HOUR, " AND category = ?", (k,)))
                               for k, v in c.endpoint_per_hour.items()}},
            "by_status_24h": by_status, "by_category_24h": by_category,
            "avg_duration_s": round(avg[0], 2) if avg and avg[0] is not None else None,
            "avg_wait_s": round(avg[1], 2) if avg and avg[1] is not None else None,
            "last_deferral": {"at": fmt(last[0]), "category": last[1], "reason": last[2],
                              "next_eligible": fmt(last[3])} if last else None,
        }


_registry: dict[tuple[str, str], Admission] = {}
_registry_lock = threading.Lock()


def shared_admission(host: str, access_key: str) -> Admission:
    """The process's gate for this account, made once (settings read from the environment then) and kept."""
    path = os.environ.get("LEADSQUARED_ADMISSION_DB", "").strip() or DEFAULT_DB
    account = account_label(host, access_key)
    with _registry_lock:
        key = (path, account)
        if key not in _registry:
            _registry[key] = Admission(path, account, AdmissionConfig.from_env())
        return _registry[key]
