"""LeadSquared request pacing: one HTTP attempt at a time per process, a cooldown after each, retries and
Retry-After through the same gate. Fake clock and stub sessions only; nothing waits for real."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime as http_date

import pytest
import requests
import responses

from integrations.leadsquared import LeadSquaredClient, LeadSquaredError
from integrations.leadsquared import client as lsq_client
from integrations.leadsquared.client import COOLDOWN_ENV, RequestPacer, retry_after_seconds

HOST = "https://api-test.leadsquared.com/v2/"


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
            self.starts.append(self.clock.now())
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


def use_pacer(monkeypatch, fake_clock, cooldown):
    pacer = RequestPacer(cooldown, clock=fake_clock.now, sleep=fake_clock.sleep)
    monkeypatch.setattr(lsq_client, "_pacer", pacer)
    return pacer


def mk(session, **kw):
    return LeadSquaredClient("ak", "sk", host=HOST, session=session, **kw)


def test_first_request_goes_at_once_and_the_cooldown_runs_from_completion(monkeypatch, fake_clock):
    use_pacer(monkeypatch, fake_clock, 4)
    s = StubSession(fake_clock, during=lambda: fake_clock.sleep(10))     # a slow request: 10 s
    c = mk(s)
    c.request("GET", "x")
    c.request("GET", "x")
    assert s.starts == [0, 14]                       # no wait first; then 4 s after the first one ended (t=10)
    assert fake_clock.sleeps == [10, 4, 10]          # first request, one 4 s cooldown, second request


def test_zero_cooldown_still_one_at_a_time(monkeypatch, fake_clock):
    use_pacer(monkeypatch, fake_clock, 0)
    s = StubSession(fake_clock)
    c = mk(s)
    for _ in range(3):
        c.request("GET", "x")
    assert s.starts == [0, 0, 0] and fake_clock.sleeps == []


@pytest.mark.parametrize("cooldown", [0, 4])
def test_threads_and_separate_clients_never_overlap(monkeypatch, fake_clock, cooldown):
    use_pacer(monkeypatch, fake_clock, cooldown)
    entered = threading.Event()
    s = StubSession(fake_clock, during=lambda: (entered.set(), threading.Event().wait(0.002)))
    clients = [mk(s), mk(s)]                                              # two clients, one shared pacer
    threads = [threading.Thread(target=clients[i % 2].request, args=("POST", "x")) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    assert entered.is_set() and len(s.starts) == 8 and s.max_in_flight == 1
    gaps = [b - a for a, b in zip(s.starts, s.starts[1:])]
    assert all(g >= cooldown for g in gaps)


def test_a_retry_waits_for_the_later_of_its_backoff_and_the_cooldown(monkeypatch, fake_clock):
    use_pacer(monkeypatch, fake_clock, 4)
    s = StubSession(fake_clock, replies=[(502, {}), (200, {})])
    assert mk(s, max_retries=1).request("GET", "x") == {"ok": 1}
    assert s.starts == [0, 4]                        # backoff 1 s overlaps the 4 s cooldown: 4 s, not 5


def test_connection_errors_retry_through_the_gate_with_the_same_limits(monkeypatch, fake_clock):
    use_pacer(monkeypatch, fake_clock, 0)
    err = requests.ConnectionError("down")
    s = StubSession(fake_clock, replies=[(err, {}), (err, {}), (err, {})])
    with pytest.raises(LeadSquaredError, match="failed: down"):
        mk(s, max_retries=2).request("GET", "x")
    assert s.starts == [0, 1, 3]                     # 3 attempts, backoff 1 s then 2 s as before


def test_retry_after_on_429_holds_every_caller(monkeypatch, fake_clock):
    use_pacer(monkeypatch, fake_clock, 4)
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": "30"}), (200, {})])
    with pytest.raises(LeadSquaredError) as exc:
        mk(s, max_retries=0).request("GET", "x")      # no retries left: the error is raised as before
    assert exc.value.status_code == 429
    mk(s).request("GET", "y")                         # another client still waits out the 30 s
    assert s.starts == [0, 30]


def test_retry_after_as_an_http_date(monkeypatch, fake_clock):
    use_pacer(monkeypatch, fake_clock, 0)
    when = http_date(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": when}), (200, {})])
    mk(s, max_retries=1).request("GET", "x")
    assert 110 <= s.starts[1] <= 120


def test_malformed_retry_after_falls_back_and_deadlines_never_shrink(monkeypatch, fake_clock):
    pacer = use_pacer(monkeypatch, fake_clock, 0)
    s = StubSession(fake_clock, replies=[(429, {"Retry-After": "soon"}), (200, {})])
    mk(s, max_retries=1).request("GET", "x")
    assert s.starts == [0, 1]                         # the old 2**attempt fallback
    pacer.hold_until(100)
    pacer.hold_until(50)                              # an earlier deadline doesn't shorten the wait
    s.replies = [(429, {"Retry-After": "5"}), (200, {})]
    mk(s, max_retries=1).request("GET", "x")
    assert s.starts[2] == 100 and s.starts[3] == 105


def test_retry_after_parsing():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp()
    assert retry_after_seconds("7", lambda: now) == 7
    assert retry_after_seconds("Wed, 14 Oct 2026 12:00:30 GMT", lambda: now) == 30
    assert retry_after_seconds("Wed, 14 Oct 2026 11:00:00 GMT", lambda: now) == 0      # already past
    for bad in (None, "", "-3", "1.5", "²", "soon", "Wed, 99 Foo 2026"):
        assert retry_after_seconds(bad, lambda: now) is None


def test_an_exception_mid_request_frees_the_gate(monkeypatch, fake_clock):
    pacer = use_pacer(monkeypatch, fake_clock, 4)
    s = StubSession(fake_clock, replies=[(KeyboardInterrupt(), {}), (200, {})])
    with pytest.raises(KeyboardInterrupt):
        mk(s).request("GET", "x")
    assert not pacer._busy
    assert mk(s).request("GET", "x") == {"ok": 1} and s.starts == [0, 4]


def test_leaving_while_waiting_never_takes_the_gate(monkeypatch, fake_clock):
    def interrupted(_):
        raise KeyboardInterrupt
    pacer = RequestPacer(4, clock=fake_clock.now, sleep=interrupted)
    pacer.hold_until(10)
    with pytest.raises(KeyboardInterrupt):
        with pacer.slot():
            pass
    assert not pacer._busy


@pytest.mark.parametrize("raw,expected", [(None, 4.0), ("", 4.0), ("0", 0.0), ("2.5", 2.5)])
def test_cooldown_setting(monkeypatch, raw, expected):
    monkeypatch.setattr(lsq_client, "_pacer", None)
    if raw is None:
        monkeypatch.delenv(COOLDOWN_ENV, raising=False)
    else:
        monkeypatch.setenv(COOLDOWN_ENV, raw)
    assert LeadSquaredClient("ak", "sk").pacer.cooldown == expected


@pytest.mark.parametrize("raw", ["-1", "nan", "inf", "four"])
def test_invalid_cooldown_is_refused(monkeypatch, raw):
    monkeypatch.setattr(lsq_client, "_pacer", None)
    monkeypatch.setenv(COOLDOWN_ENV, raw)
    with pytest.raises(ValueError, match=COOLDOWN_ENV):
        LeadSquaredClient("ak", "sk")


def test_a_new_client_keeps_the_shared_pacer_and_its_state(monkeypatch):
    monkeypatch.setattr(lsq_client, "_pacer", None)
    monkeypatch.setenv(COOLDOWN_ENV, "4")
    first = LeadSquaredClient("ak", "sk").pacer
    first.hold_until(first.now() + 1000)
    monkeypatch.setenv(COOLDOWN_ENV, "0")             # read once per process
    second = LeadSquaredClient("ak", "sk").pacer
    assert second is first and second.cooldown == 4 and second._next_start >= first.now() + 999


@responses.activate
def test_retry_limit_and_request_unchanged():
    url = HOST + "LeadManagement.svc/Leads.Get"
    for _ in range(3):
        responses.post(url, status=429)
    with pytest.raises(LeadSquaredError) as exc:
        LeadSquaredClient("ak", "sk", host=HOST, max_retries=1).search_leads("mx_City", "Delhi")
    assert exc.value.status_code == 429 and len(responses.calls) == 2
    assert all(c.request.body == responses.calls[0].request.body for c in responses.calls)
