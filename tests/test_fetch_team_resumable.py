import os
from datetime import datetime, timedelta, timezone

import pytest

from integrations.leadsquared import AdmissionDeferred, LeadSquaredError
from scripts import fetch_team_resumable as fr

T0 = datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc)  # 21 Oct 00:00 IST


def test_day_windows_cover_the_span_and_cut_the_last_day():
    w = fr.day_windows(T0, T0 + timedelta(days=2, hours=5))
    assert len(w) == 3 and w[0] == (T0, T0 + timedelta(days=1)) and w[-1][1] == T0 + timedelta(days=2, hours=5)
    assert fr.day_windows(T0, T0) == []


def test_cached_reads_once(tmp_path):
    calls = []
    path = str(tmp_path / "x.json")
    assert fr.cached(path, lambda: calls.append(1) or [1]) == [1]
    assert fr.cached(path, lambda: calls.append(1) or [2]) == [1]
    assert calls == [1] and not os.path.exists(path + ".tmp")


def test_patient_retries_a_refusal_then_stops_on_a_deferral(monkeypatch):
    waits = []
    monkeypatch.setattr(fr, "sleep", waits.append)
    tries = iter([LeadSquaredError("blocked", status_code=403), ["ok"]])

    def read():
        x = next(tries)
        if isinstance(x, Exception):
            raise x
        return x

    assert fr.patient(read, "w") == ["ok"] and waits == [fr.BLOCK_WAITS[0]]

    def refused():
        raise LeadSquaredError("blocked", status_code=403)

    with pytest.raises(LeadSquaredError):
        fr.patient(refused, "w")
    assert len(waits) == 1 + len(fr.BLOCK_WAITS)

    def other():
        raise LeadSquaredError("bad request", status_code=400)

    with pytest.raises(LeadSquaredError):
        fr.patient(other, "w")  # not a refusal: raised at once, no wait
    assert len(waits) == 1 + len(fr.BLOCK_WAITS)

    def deferred():
        raise AdmissionDeferred("budget used up")

    with pytest.raises(fr.Deferred):
        fr.patient(deferred, "w")


class FakeClient:
    def __init__(self, rows):
        self.rows, self.reads = rows, []

    def clone(self):
        return self

    def _iter_unpaged(self, ev, a, b):
        self.reads.append(a)
        return [r for r in self.rows if a <= datetime.fromisoformat(r["ModifiedOn"]) < b]


def row(i, created, modified):
    return {"ProspectActivityId": i, "CreatedOn": created.strftime("%Y-%m-%d %H:%M:%S"), "ModifiedOn": modified.isoformat()}


def test_started_caches_closed_days_only_and_keeps_by_creation(tmp_path):
    now = T0 + timedelta(days=1, hours=3)
    rows = [row("a", T0 + timedelta(hours=1), T0 + timedelta(hours=1)),
            row("b", T0 + timedelta(hours=2), T0 + timedelta(days=1, hours=1)),  # edited the next day: read once
            row("old", T0 - timedelta(days=2), T0 + timedelta(hours=3))]          # created before the span: dropped
    c = FakeClient(rows)
    got = fr.started(c, str(tmp_path), 22, T0, now, lambda a: True, now)
    assert sorted(r["ProspectActivityId"] for r in got) == ["a", "b"]
    assert len(os.listdir(tmp_path)) == 1          # the first day is cached; the open day (ends at now) is not
    c.reads.clear()
    fr.started(c, str(tmp_path), 22, T0, now, lambda a: True, now)
    assert c.reads == [T0 + timedelta(days=1)]     # a rerun reads only the open day again


def test_main_exits_75_on_a_deferral_and_keeps_the_cache(monkeypatch, tmp_path):
    def fetch(*a, **k):
        raise fr.Deferred("budget used up")

    monkeypatch.setattr(fr, "fetch", fetch)
    assert fr.main(["Team A", "2026-10-12", "2026-10-23", str(tmp_path / "s.json"), "--cache", str(tmp_path)]) == 75
    assert tmp_path.exists()  # the cache folder is kept for the rerun
