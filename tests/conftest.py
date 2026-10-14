from __future__ import annotations

import threading

import pytest

from integrations.leadsquared import client as lsq_client


class FakeClock:
    """A monotonic clock that moves only when something sleeps on it (thread-safe)."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.t

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.t += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def lsq_pacer(monkeypatch, fake_clock):
    """Each test gets its own LeadSquared pacer (no cooldown, fake clock), so none waits for real or leaks state."""
    pacer = lsq_client.RequestPacer(0.0, clock=fake_clock.now, sleep=fake_clock.sleep)
    monkeypatch.setattr(lsq_client, "_pacer", pacer)
    return pacer
