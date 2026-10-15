from __future__ import annotations

import threading
import time

import pytest

from integrations.leadsquared import admission


class FakeClock:
    """A clock that moves only when something sleeps on it (thread-safe). Serves as both the wall clock and the
    monotonic clock of the LeadSquared gate. ``real_pause`` adds a tiny real sleep so busy-waiting threads
    don't race fake time ahead."""

    def __init__(self, start: float = 1_790_000_000.0) -> None:   # mid-September 2026
        self.t = start
        self.start = start
        self.sleeps: list[float] = []
        self.real_pause = 0.0
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.t

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.t += seconds
        if self.real_pause:
            time.sleep(self.real_pause)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def lsq_gate(monkeypatch, tmp_path, fake_clock):
    """Each test gets its own LeadSquared admission store (no cooldown, no per-window limit, no budgets, fake clock), so none waits
    for real, touches data/ or leaks state. The first-enrolment store is per test too."""
    for name in ("LEADSQUARED_MAX_IN_FLIGHT", "LEADSQUARED_MAX_REQUESTS_PER_HOUR", "LEADSQUARED_MAX_REQUESTS_PER_DAY",
                 "LEADSQUARED_MAX_RECORDS_PER_DAY", "LEADSQUARED_ENDPOINT_HOURLY_BUDGETS",
                 "LEADSQUARED_MAX_ADMISSION_WAIT_SECONDS", "LEADSQUARED_LEASE_SECONDS",
                 "LEADSQUARED_MAX_IDS_PER_BULK_REQUEST", "LEADSQUARED_ACCOUNT_LABEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LEADSQUARED_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("LEADSQUARED_MAX_REQUESTS_PER_WINDOW", "off")
    monkeypatch.delenv("LEADSQUARED_WINDOW_SECONDS", raising=False)
    monkeypatch.setenv("LEADSQUARED_ADMISSION_DB", str(tmp_path / "admission.sqlite3"))
    monkeypatch.setenv("LEADSQUARED_ENROLMENT_STORE", str(tmp_path / "first_enrolments.sqlite3"))
    monkeypatch.setattr(admission, "_registry", {})
    monkeypatch.setattr(admission, "wall_clock", fake_clock.now)
    monkeypatch.setattr(admission, "monotonic", fake_clock.now)
    monkeypatch.setattr(admission, "sleep", fake_clock.sleep)
    return tmp_path / "admission.sqlite3"
