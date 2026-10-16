"""LeadSquared admission gate: one HTTP attempt at a time per account across threads, clients and processes,
a cooldown after each, rolling budgets, deferral, leases and Retry-After. Fake clock and stub sessions only;
nothing calls LeadSquared."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
from datetime import datetime, timezone
from email.utils import format_datetime as http_date

import pytest
import requests
import responses

from integrations.leadsquared import AdmissionDeferred, CoordinatorUnavailable, LeadSquaredClient, LeadSquaredError
from integrations.leadsquared import admission
from integrations.leadsquared.client import retry_after_seconds

HOST = "https://api-test.leadsquared.com/v2/"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StubSession:
    """Answers each request from ``replies`` (status, headers) or raises; records start times and overlap."""

    def __init__(self, clock, replies=None, during=None):
        self.clock, self.replies, self.during = clock, list(replies or []), during
        self.starts: list[float] = []
        self.in_flight = self.max_in_flight = 0
        self.lock = threading.Lock()

    def request(self, method, url, params=None, json=None, timeout=None):
        with self.lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.starts.append(self.clock.now() - self.clock.start)
        try:
            if self.during:
                self.during()
            status, headers = self.replies.pop(0) if self.replies else (200, {})
            if isinstance(status, BaseException):
                raise status
            r = requests.Response()
            r.status_code, r._content = status, b'{"ok": 1}' if status == 200 else b""
            r.headers.update(headers)
            return r
        finally:
            with self.lock:
                self.in_flight -= 1


def configure(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setattr(admission, "_registry", {})


def mk(session, key="ak", **kw):
    return LeadSquaredClient(key, "sk", host=HOST, session=session, **kw)


# ------------------------------------------------------------------ pacing

def test_first_request_goes_at_once_and_the_cooldown_runs_from_completion(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=4)
    s = StubSession(fake_clock, during=lambda: fake_clock.sleep(10))     # a slow request: 10 s
    c = mk(s)
    c.request("GET", "x")
    c.request("GET", "x")
    assert s.starts == [0, 14]                       # no wait first; then 4 s after the first one ended (t=10)


def test_zero_cooldown_still_one_at_a_time(fake_clock):
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(3):
        c.request("GET", "x")
    assert s.starts == [0, 0, 0] and fake_clock.sleeps == []


def test_at_most_10_requests_start_in_any_5_seconds(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_REQUESTS_PER_WINDOW=10, LEADSQUARED_MAX_IN_FLIGHT=4)
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(25):
        c.request("GET", "x")
    assert s.starts == [0] * 10 + [5] * 10 + [10] * 5           # each window of 5 s holds at most 10 starts
    assert c.admission.status()["settings"]["max_requests_per_window"] == 10


def test_the_window_limit_counts_retries_and_rolls(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_REQUESTS_PER_WINDOW=3, LEADSQUARED_WINDOW_SECONDS=5)
    s = StubSession(fake_clock, replies=[(503, {}), (200, {})])
    c = mk(s)
    c.request("GET", "x")                                         # a 503 and its retry: two attempts
    fake_clock.sleep(2)
    c.request("GET", "x")
    c.request("GET", "x")                                         # the 4th attempt waits for the first to age out
    assert s.starts == [0, 1, 3, 5]


def test_window_limit_off(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_REQUESTS_PER_WINDOW="off")
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(15):
        c.request("GET", "x")
    assert s.starts == [0] * 15 and c.admission.status()["settings"]["max_requests_per_window"] == "off"


BULK = "ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent"


def test_the_company_wide_activity_read_starts_at_most_twice_per_5_seconds(monkeypatch, fake_clock):
    monkeypatch.delenv("LEADSQUARED_ENDPOINT_MAX_PER_WINDOW")             # the default: activity_bulk=2
    configure(monkeypatch, LEADSQUARED_MAX_REQUESTS_PER_WINDOW=10, LEADSQUARED_MAX_IN_FLIGHT=2)
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(6):
        c.request("POST", BULK, json={})
    assert s.starts == [0, 2.5, 5, 7.5, 10, 12.5]                  # one every 2.5 s: never 3 in any 5 s
    assert c.admission.status()["settings"]["endpoint_max_per_window"] == {"activity_bulk": 2}


def test_the_endpoint_rate_leaves_other_reads_alone_and_counts_retries(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_ENDPOINT_MAX_PER_WINDOW="activity_bulk=2.5")
    s = StubSession(fake_clock, replies=[(503, {}), (200, {})])
    c = mk(s)
    c.request("POST", BULK, json={})                               # a 503 at 0 s, its retry waits for 2 s
    c.request("GET", "UserManagement.svc/Users.Get")               # another endpoint: no extra wait
    c.request("POST", BULK, json={})
    assert s.starts == [0, 2, 2, 4]


def test_endpoint_rate_off(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_ENDPOINT_MAX_PER_WINDOW="off")
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(3):
        c.request("POST", BULK, json={})
    assert s.starts == [0, 0, 0] and c.admission.status()["settings"]["endpoint_max_per_window"] == {}


@pytest.mark.parametrize("cooldown", [0, 4])
def test_threads_and_separate_clients_never_overlap(monkeypatch, fake_clock, cooldown):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=cooldown)
    fake_clock.real_pause = 0.001
    s = StubSession(fake_clock, during=lambda: threading.Event().wait(0.003))
    clients = [mk(s), mk(s)]                                              # two clients, one shared gate
    assert clients[0].admission is clients[1].admission
    threads = [threading.Thread(target=clients[i % 2].request, args=("POST", "x")) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert len(s.starts) == 8 and s.max_in_flight == 1
    assert all(b - a >= cooldown for a, b in zip(s.starts, s.starts[1:]))


def defaults(monkeypatch):
    for name in ("LEADSQUARED_COOLDOWN_SECONDS", "LEADSQUARED_MAX_IN_FLIGHT", "LEADSQUARED_MAX_REQUESTS_PER_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(admission, "_registry", {})


def at_most_per_window(starts, limit=10, window=5.0):
    return all(sum(a <= t < a + window for t in starts) <= limit for a in starts)


def test_default_pace_spreads_one_reader_half_a_second_apart(monkeypatch, fake_clock):
    defaults(monkeypatch)
    s = StubSession(fake_clock)                                      # instant replies: the fastest case
    c = mk(s)
    for _ in range(25):
        c.request("GET", "x")
    assert s.starts == [i * 0.5 for i in range(25)]                 # evenly spread, never back to back
    assert at_most_per_window(s.starts)


def test_default_pace_lets_two_readers_overlap_and_holds_10_per_5_seconds(monkeypatch, fake_clock):
    defaults(monkeypatch)
    fake_clock.real_pause = 0.001
    s = StubSession(fake_clock, during=lambda: threading.Event().wait(0.003))
    c = mk(s)
    threads = [threading.Thread(target=c.request, args=("POST", "x")) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert len(s.starts) == 30 and s.max_in_flight <= 2
    assert at_most_per_window(sorted(s.starts))


def test_separate_processes_share_one_gate(tmp_path):
    """Real processes, real clocks: three processes, three requests each, never overlapping, 0.2 s apart."""
    log = tmp_path / "log.jsonl"
    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent(f"""
        import json, sys, time
        import requests
        sys.path.insert(0, {REPO!r})
        from integrations.leadsquared import LeadSquaredClient

        class Session:
            def request(self, *a, **k):
                start = time.time()
                time.sleep(0.05)
                with open({str(log)!r}, "a") as fh:
                    fh.write(json.dumps([start, time.time()]) + "\\n")
                r = requests.Response(); r.status_code = 200; r._content = b"{{}}"
                return r

        c = LeadSquaredClient("ak", "sk", host="https://h.example/v2/", session=Session())
        for _ in range(3):
            c.request("GET", "x")
    """))
    env = {**os.environ, "LEADSQUARED_ADMISSION_DB": str(tmp_path / "shared.sqlite3"),
           "LEADSQUARED_COOLDOWN_SECONDS": "0.2", "PYTHONPATH": REPO}
    procs = [subprocess.Popen([sys.executable, str(worker)], env=env) for _ in range(3)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    spans = sorted(json.loads(line) for line in log.read_text().splitlines())
    assert len(spans) == 9
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert s2 >= e1 + 0.18                       # no overlap, and the cooldown held across processes


# ------------------------------------------------------------------ retries and Retry-After

def test_a_retry_waits_for_the_later_of_its_backoff_and_the_cooldown(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=4)
    s = StubSession(fake_clock, replies=[(502, {}), (200, {})])
    c = mk(s, max_retries=1)
    assert c.request("GET", "x") == {"ok": 1}
    assert s.starts == [0, 4]                        # backoff 1 s overlaps the 4 s cooldown: 4 s, not 5
    st = c.admission.status()
    assert st["attempts_last_hour"] == 2 and st["by_status_24h"] == {"5xx": 1, "2xx": 1}   # retries count


def test_connection_errors_retry_through_the_gate_with_the_same_limits(fake_clock):
    err = requests.ConnectionError("down")
    s = StubSession(fake_clock, replies=[(err, {}), (err, {}), (err, {})])
    with pytest.raises(LeadSquaredError, match="failed: down"):
        mk(s, max_retries=2).request("GET", "x")
    assert s.starts == [0, 1, 3]                     # 3 attempts, backoff 1 s then 2 s as before


def test_network_errors_never_carry_the_keys(fake_clock):
    c = mk(StubSession(fake_clock), max_retries=0)
    err = requests.ConnectionError(f"Max retries exceeded with url: /v2/x?accessKey={c.access_key}&secretKey={c.secret_key}")
    c.session = StubSession(fake_clock, replies=[(err, {})])
    with pytest.raises(LeadSquaredError) as exc:
        c.request("GET", "x")
    text = str(exc.value)
    assert "accessKey=***&secretKey=***" in text and c.access_key not in text and c.secret_key not in text
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_retry_after_on_429_holds_every_caller(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=4)
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": "30"}), (200, {})])
    with pytest.raises(LeadSquaredError) as exc:
        mk(s, max_retries=0).request("GET", "x")      # no retries left: the error is raised as before
    assert exc.value.status_code == 429 and not isinstance(exc.value, AdmissionDeferred)
    mk(s).request("GET", "y")                         # another client still waits out the 30 s
    assert s.starts == [0, 30]


def test_retry_after_as_an_http_date(fake_clock):
    when = http_date(datetime.fromtimestamp(fake_clock.now() + 120, timezone.utc), usegmt=True)
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": when}), (200, {})])
    mk(s, max_retries=1).request("GET", "x")
    assert s.starts == [0, 120]


def test_malformed_retry_after_falls_back_and_deadlines_never_shrink(fake_clock):
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": "soon"}), (200, {})])
    c = mk(s, max_retries=1)
    c.request("GET", "x")
    assert s.starts == [0, 1]                         # the old 2**attempt fallback
    c.admission.hold_until(fake_clock.start + 100)
    c.admission.hold_until(fake_clock.start + 50)     # an earlier deadline doesn't shorten the wait
    s.replies = [(429, {"Retry-After": "5"}), (200, {})]
    c.request("GET", "x")
    assert s.starts[2:] == [100, 105]


def test_a_long_hold_defers_instead_of_sleeping(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS=300)
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": "3600"})])
    with pytest.raises(AdmissionDeferred) as exc:
        mk(s, max_retries=1).request("GET", "x")      # its own retry would wait an hour: deferred, not slept
    assert exc.value.next_eligible.timestamp() == fake_clock.start + 3600
    assert len(s.starts) == 1 and fake_clock.sleeps == []
    with pytest.raises(AdmissionDeferred):
        mk(s).request("GET", "y")                     # and so is every other caller, without sending
    assert len(s.starts) == 1


def test_retry_after_parsing():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp()
    assert retry_after_seconds("7", lambda: now) == 7
    assert retry_after_seconds("Wed, 14 Oct 2026 12:00:30 GMT", lambda: now) == 30
    assert retry_after_seconds("Wed, 14 Oct 2026 11:00:00 GMT", lambda: now) == 0      # already past
    for bad in (None, "", "-3", "1.5", "²", "soon", "Wed, 99 Foo 2026"):
        assert retry_after_seconds(bad, lambda: now) is None


# ------------------------------------------------------------------ budgets and deferral

def test_hourly_budget_defers_without_sending_then_frees_on_a_rolling_window(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_REQUESTS_PER_HOUR=3)
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(3):
        c.request("GET", "x")
        fake_clock.sleep(600)
    with pytest.raises(AdmissionDeferred) as exc:
        c.request("GET", "x")
    assert "hourly request budget" in exc.value.reason and len(s.starts) == 3
    assert exc.value.next_eligible.timestamp() == fake_clock.start + 3600       # when the oldest ages out
    fake_clock.sleep(fake_clock.start + 3600 - fake_clock.now() + 1)
    c.request("GET", "x")
    assert len(s.starts) == 4
    assert c.admission.status()["last_deferral"]["reason"] == "hourly request budget"


def test_daily_budget(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_REQUESTS_PER_DAY=2)
    c = mk(StubSession(fake_clock))
    c.request("GET", "x")
    c.request("GET", "x")
    with pytest.raises(AdmissionDeferred, match="daily request budget"):
        c.request("GET", "x")
    assert c.admission.status()["budget_left"]["daily"] == 0


def test_record_budget_counts_ids_sent_and_page_sizes(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_MAX_RECORDS_PER_DAY=250)
    s = StubSession(fake_clock)
    c = mk(s)
    by_ids = {"SearchParameters": {"LeadIds": [f"l{i}" for i in range(100)]}, "Paging": {"PageIndex": 1, "PageSize": 200}}
    c.request("POST", "LeadManagement.svc/Leads/Retrieve/ByIds", json=by_ids)     # 100 (the IDs, not the page)
    c.request("POST", "ProspectActivity.svc/Retrieve", json={"Paging": {"Offset": 0, "RowCount": 100}})   # 100
    with pytest.raises(AdmissionDeferred, match="daily record budget"):
        c.request("POST", "LeadManagement.svc/Leads/Retrieve/ByIds", json=by_ids)
    c.request("GET", "LeadManagement.svc/LeadsMetaData.Get")                       # 1 record still fits
    with pytest.raises(AdmissionDeferred) as exc:
        c.request("POST", "LeadManagement.svc/Leads.Get", json={"Paging": {"PageIndex": 1, "PageSize": 1000}})
    assert exc.value.next_eligible is None                                         # can never fit: say so
    assert len(s.starts) == 3


def test_endpoint_budget_only_limits_its_category(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_ENDPOINT_HOURLY_BUDGETS="lead_history=2")
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(2):
        c.request("POST", "ProspectActivity.svc/Retrieve", params={"leadId": "x"}, json={"Paging": {}})
    with pytest.raises(AdmissionDeferred, match="hourly lead_history budget"):
        c.request("POST", "ProspectActivity.svc/Retrieve", params={"leadId": "x"}, json={"Paging": {}})
    c.request("GET", "UserManagement.svc/Users.Get")
    assert len(s.starts) == 3 and c.admission.status()["budget_left"]["hourly_lead_history"] == 0


def test_accounts_are_isolated(fake_clock):
    s = StubSession(fake_clock)
    a, b = mk(s, key="key-a"), mk(s, key="key-b")
    assert a.admission is not b.admission
    a.admission.hold_until(fake_clock.start + 500)
    b.request("GET", "x")
    assert s.starts == [0]


# ------------------------------------------------------------------ store outages, crashes, leases

def test_an_unusable_store_refuses_to_send(monkeypatch, tmp_path, fake_clock):
    (tmp_path / "file").write_text("not a folder")
    configure(monkeypatch, LEADSQUARED_ADMISSION_DB=tmp_path / "file" / "admission.sqlite3")
    with pytest.raises(CoordinatorUnavailable, match="no request was sent"):
        mk(StubSession(fake_clock))
    configure(monkeypatch, LEADSQUARED_ADMISSION_DB=tmp_path / "ok.sqlite3")
    s = StubSession(fake_clock)
    c = mk(s)

    def broken(self):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(admission.Admission, "_conn", broken)
    with pytest.raises(CoordinatorUnavailable):
        c.request("GET", "x")
    assert s.starts == []                            # never sent without the gate


def test_an_exception_mid_request_frees_the_gate(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=4)
    s = StubSession(fake_clock, replies=[(KeyboardInterrupt(), {}), (200, {})])
    c = mk(s)
    with pytest.raises(KeyboardInterrupt):
        c.request("GET", "x")
    assert c.admission.status()["in_flight"] == 0
    assert c.request("GET", "x") == {"ok": 1} and s.starts == [0, 4]
    assert c.admission.status()["by_status_24h"] == {"interrupted": 1, "2xx": 1}


def test_leaving_while_waiting_never_takes_the_gate(monkeypatch, fake_clock):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=4)
    c = mk(StubSession(fake_clock))
    c.request("GET", "x")

    def interrupted(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(admission, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        c.request("GET", "x")                        # interrupted during the cooldown wait
    st = c.admission.status()
    assert st["in_flight"] == 0 and st["attempts_last_hour"] == 1


def test_a_crashed_holder_is_counted_and_freed_by_its_lease_and_cannot_free_its_successor(monkeypatch, lsq_gate,
                                                                                          fake_clock):
    configure(monkeypatch, LEADSQUARED_LEASE_SECONDS=60)
    cfg = admission.AdmissionConfig.from_env()
    crashed = admission.Admission(str(lsq_gate), "acct", cfg).acquire()           # "process" 1 never finishes
    fresh = admission.Admission(str(lsq_gate), "acct", cfg)                        # "process" 2, same file
    t0 = fake_clock.now()
    ticket = fresh.acquire()                                                       # waits out the lease
    assert fake_clock.now() - t0 >= 60
    crashed_late = admission.Admission(str(lsq_gate), "acct", cfg)
    crashed_late.release(crashed, "2xx")                                           # a stale caller comes back
    st = fresh.status()
    assert st["in_flight"] == 1 and st["attempts_last_hour"] == 2                   # successor kept; both counted
    assert st["by_status_24h"]["2xx (lease lost)"] == 1
    assert not crashed_late.renew(crashed) and fresh.renew(ticket)
    fresh.release(ticket, "2xx")
    assert fresh.status()["in_flight"] == 0


def test_cooldown_and_holds_survive_a_restart(monkeypatch, lsq_gate, fake_clock):
    configure(monkeypatch, LEADSQUARED_COOLDOWN_SECONDS=4)
    cfg = admission.AdmissionConfig.from_env()
    first = admission.Admission(str(lsq_gate), "acct", cfg)
    first.release(first.acquire(), "2xx")
    first.hold_until(fake_clock.now() + 20)
    t0 = fake_clock.now()
    admission.Admission(str(lsq_gate), "acct", cfg).acquire()                     # a new process
    assert fake_clock.now() - t0 == 20


# ------------------------------------------------------------------ configuration

@pytest.mark.parametrize("raw,expected", [(None, 0.5), ("", 0.5), ("0", 0.0), ("2.5", 2.5)])
def test_cooldown_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("LEADSQUARED_COOLDOWN_SECONDS", raising=False)
    else:
        monkeypatch.setenv("LEADSQUARED_COOLDOWN_SECONDS", raw)
    assert LeadSquaredClient("ak", "sk").admission.config.cooldown == expected


def test_defaults_leave_budgets_off_explicitly(monkeypatch):
    for name in ("LEADSQUARED_COOLDOWN_SECONDS", "LEADSQUARED_MAX_IN_FLIGHT", "LEADSQUARED_MAX_REQUESTS_PER_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    c = admission.AdmissionConfig.from_env()
    assert (c.cooldown, c.max_in_flight, c.max_ids_per_bulk) == (0.5, 2, 250)
    assert (c.per_window, c.window) == (10, 5.0)
    assert (c.per_hour, c.per_day, c.records_per_day, c.endpoint_per_hour) == (None, None, None, {})
    monkeypatch.delenv("LEADSQUARED_ENDPOINT_MAX_PER_WINDOW")
    assert admission.AdmissionConfig.from_env().endpoint_per_window == {"activity_bulk": 2}
    monkeypatch.setenv("LEADSQUARED_MAX_REQUESTS_PER_HOUR", "off")
    monkeypatch.setenv("LEADSQUARED_ENDPOINT_HOURLY_BUDGETS", "lead_history=600, activity_bulk=200")
    c = admission.AdmissionConfig.from_env()
    assert c.per_hour is None and c.endpoint_per_hour == {"lead_history": 600, "activity_bulk": 200}


@pytest.mark.parametrize("name,raw", [
    ("LEADSQUARED_COOLDOWN_SECONDS", "-1"), ("LEADSQUARED_COOLDOWN_SECONDS", "nan"),
    ("LEADSQUARED_COOLDOWN_SECONDS", "inf"), ("LEADSQUARED_COOLDOWN_SECONDS", "four"),
    ("LEADSQUARED_MAX_IN_FLIGHT", "0"), ("LEADSQUARED_MAX_IN_FLIGHT", "1.5"),
    ("LEADSQUARED_MAX_REQUESTS_PER_HOUR", "0"), ("LEADSQUARED_MAX_REQUESTS_PER_DAY", "lots"),
    ("LEADSQUARED_MAX_RECORDS_PER_DAY", "-5"), ("LEADSQUARED_LEASE_SECONDS", "0"),
    ("LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS", "inf"), ("LEADSQUARED_MAX_IDS_PER_BULK_REQUEST", "0"),
    ("LEADSQUARED_ENDPOINT_HOURLY_BUDGETS", "history=5"), ("LEADSQUARED_ENDPOINT_HOURLY_BUDGETS", "lead_history"),
    ("LEADSQUARED_ACCOUNT_LABEL", "has spaces"),
    ("LEADSQUARED_MAX_REQUESTS_PER_WINDOW", "0"), ("LEADSQUARED_MAX_REQUESTS_PER_WINDOW", "2.5"),
    ("LEADSQUARED_WINDOW_SECONDS", "0"), ("LEADSQUARED_WINDOW_SECONDS", "nan"),
    ("LEADSQUARED_ENDPOINT_MAX_PER_WINDOW", "activity_bulk=0"), ("LEADSQUARED_ENDPOINT_MAX_PER_WINDOW", "bulk=2"),
    ("LEADSQUARED_ENDPOINT_MAX_PER_WINDOW", "activity_bulk"), ("LEADSQUARED_ENDPOINT_MAX_PER_WINDOW", "activity_bulk=inf"),
])
def test_invalid_settings_are_refused(monkeypatch, name, raw):
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match=name):
        LeadSquaredClient("ak", "sk")


def test_a_new_client_keeps_the_shared_gate_and_its_settings(monkeypatch):
    monkeypatch.setenv("LEADSQUARED_COOLDOWN_SECONDS", "4")
    first = LeadSquaredClient("ak", "sk").admission
    monkeypatch.setenv("LEADSQUARED_COOLDOWN_SECONDS", "0")             # read once per process
    second = LeadSquaredClient("ak", "sk").admission
    assert second is first and second.config.cooldown == 4


def test_account_label_is_stable_and_not_the_key(monkeypatch):
    label = admission.account_label("https://h/v2/", "the-access-key")
    assert label == admission.account_label("https://h/v2/", "the-access-key") and "the-access-key" not in label
    monkeypatch.setenv("LEADSQUARED_ACCOUNT_LABEL", "lawsikho-prod")
    assert admission.account_label("https://h/v2/", "other") == "lawsikho-prod"


def test_endpoint_categories_ignore_the_http_verb():
    assert admission.endpoint_category("ProspectActivity.svc/Retrieve") == "lead_history"
    assert admission.endpoint_category("/ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent") == "activity_bulk"
    assert admission.endpoint_category("LeadManagement.svc/Leads.Get") == "lead_read"
    assert admission.endpoint_category("LeadManagement.svc/Lead.Create") == "write"
    assert admission.endpoint_category("Something.svc/New") == "other"


# ------------------------------------------------------------------ request shape and reporting

def test_too_many_ids_in_one_bulk_read_is_refused_not_split(fake_clock):
    s = StubSession(fake_clock)
    with pytest.raises(ValueError, match="at most 250"):
        mk(s).request("POST", "LeadManagement.svc/Leads/Retrieve/ByIds",
                      json={"SearchParameters": {"LeadIds": [str(i) for i in range(251)]}})
    assert s.starts == []


@responses.activate
def test_retry_limit_and_request_unchanged():
    url = HOST + "LeadManagement.svc/Leads.Get"
    for _ in range(3):
        responses.post(url, status=429)
    with pytest.raises(LeadSquaredError) as exc:
        LeadSquaredClient("ak", "sk", host=HOST, max_retries=1).search_leads("mx_City", "Delhi")
    assert exc.value.status_code == 429 and len(responses.calls) == 2
    assert all(c.request.body == responses.calls[0].request.body for c in responses.calls)


def test_status_holds_counts_not_secrets_or_urls(fake_clock):
    c = mk(StubSession(fake_clock), key="secret-access-key")
    c.request("GET", "LeadManagement.svc/Leads.GetById", params={"id": "lead-123"})
    text = json.dumps(c.admission.status())
    assert "secret-access-key" not in text and "lead-123" not in text and HOST not in text
    assert json.loads(text)["by_category_24h"] == {"lead_read": 1}
