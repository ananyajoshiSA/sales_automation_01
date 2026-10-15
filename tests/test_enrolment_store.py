from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from analytics import enrolment_store
from analytics.convintel import sources
from analytics.definitions import ENROLLED
from analytics.enrolment_store import HISTORY_ROWS, ScanStats, first_enrolment, scan_since
from analytics.team_performance import first_enrollment
from integrations.leadsquared import AdmissionDeferred

D0 = datetime(2026, 10, 4, 18, 30, tzinfo=timezone.utc)   # 5 Oct 00:00 IST


def stage(at: str, current: str, by: str = "Asha") -> dict:
    return {"CreatedOn": at, "Data": [{"Key": "CurrentStage", "Value": current}, {"Key": "CreatedBy", "Value": by}]}


class FakeClient:
    """Leads now in Course Enrolled (newest edit first) and their stage histories; counts history reads."""

    def __init__(self, leads, histories, account="acct-a"):
        self.leads, self.histories, self.reads = leads, histories, []
        self.admission = type("A", (), {"account": account})()

    def iter_leads(self, lookup, value, columns=None, sort_by=None, page_size=None):
        assert (lookup, value, sort_by) == ("ProspectStage", ENROLLED, "ModifiedOn")
        yield from self.leads

    def get_lead_activities(self, lead_id, activity_event=None, row_count=100):
        assert activity_event == 3002 and row_count == HISTORY_ROWS
        self.reads.append(lead_id)
        return {"ProspectActivities": self.histories.get(lead_id, [])}


def leads():
    return [{"ProspectID": "L1", "ModifiedOn": "2026-10-06 10:00:00", "OwnerId": "u1", "OwnerIdName": "Ravi "},
            {"ProspectID": "L2", "ModifiedOn": "2026-10-05 09:00:00", "OwnerId": "u2"},
            {"ProspectID": "L3", "ModifiedOn": "2026-10-04 20:00:00", "OwnerId": "u3"},
            {"ProspectID": "L4", "ModifiedOn": "2026-10-01 10:00:00", "OwnerId": "u4"}]   # edited before d0: stop


def histories():
    return {"L1": [stage("2026-10-05 08:00:00", ENROLLED, "Ravi"), stage("2026-10-02 08:00:00", "Interested")],
            "L2": [stage("2026-10-05 07:00:00", ENROLLED), stage("2026-09-01 07:00:00", ENROLLED, "Old")],
            "L3": [stage("2026-10-04 19:00:00", "Interested")]}                 # no enrolment in history


def test_first_enrolment_matches_the_report_rule():
    for hist in histories().values():
        got = first_enrolment(hist)
        assert (got[0] if got else None) == first_enrollment(hist)
    assert first_enrolment(histories()["L2"])[1] == "Old"


def test_scan_reads_each_history_once_then_uses_the_store():
    c = FakeClient(leads(), histories())
    stats = ScanStats()
    first = [(l["ProspectID"], at, by) for l, at, by in scan_since(c, D0, ["ProspectID"], stats)]
    assert [x[0] for x in first] == ["L1", "L2", "L3"]          # stopped at L4
    assert first[1][2] == "Old" and first[2][1] is None
    assert (stats.scanned, stats.known, stats.read) == (3, 0, 3)

    c.reads, stats = [], ScanStats()
    again = [(l["ProspectID"], at, by) for l, at, by in scan_since(c, D0, ["ProspectID"], stats)]
    assert again == first                                        # same answer
    assert c.reads == ["L3"]                                     # only the lead with no enrolment is read again
    assert (stats.known, stats.read) == (2, 1)
    assert str(stats) == "scanned 3 enrolled leads (2 from the store, 1 histories read)"


def test_a_full_page_of_history_is_not_stored():
    full = [stage("2026-10-05 08:00:00", ENROLLED)] + [stage("2026-10-05 09:00:00", "Interested")] * (HISTORY_ROWS - 1)
    c = FakeClient(leads()[:1], {"L1": full})
    list(scan_since(c, D0, ["ProspectID"]))
    list(scan_since(c, D0, ["ProspectID"]))
    assert c.reads == ["L1", "L1"]


def test_store_is_kept_per_account(tmp_path):
    list(scan_since(FakeClient(leads(), histories(), "acct-a"), D0, ["ProspectID"]))
    other = FakeClient(leads(), histories(), "acct-b")
    list(scan_since(other, D0, ["ProspectID"]))
    assert other.reads == ["L1", "L2", "L3"]


def test_store_off_reads_every_history(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADSQUARED_ENROLMENT_STORE", "off")
    c = FakeClient(leads(), histories())
    list(scan_since(c, D0, ["ProspectID"]))
    list(scan_since(c, D0, ["ProspectID"]))
    assert c.reads == ["L1", "L2", "L3"] * 2
    assert not list(tmp_path.glob("first_enrolments*"))


def test_convintel_first_enrolments_keeps_its_window_and_fields():
    got = sources.first_enrolments(FakeClient(leads(), histories()), D0, datetime(2026, 10, 5, 18, 29, tzinfo=timezone.utc))
    assert got == [{"lead_id": "L1", "at_utc": "2026-10-05 08:00:00", "ist_day": "2026-10-05", "owner_id": "u1",
                    "owner_name": "Ravi", "set_by": "Ravi"}]     # L2 first enrolled in September: not a first-timer


def test_deferral_mid_scan_keeps_what_was_found():
    class Deferring(FakeClient):
        def get_lead_activities(self, lead_id, **kw):
            if lead_id == "L2":
                raise AdmissionDeferred("deferred", "daily request budget used", None)
            return super().get_lead_activities(lead_id, **kw)

    with pytest.raises(AdmissionDeferred):
        list(scan_since(Deferring(leads(), histories()), D0, ["ProspectID"]))
    c = FakeClient(leads(), histories())
    list(scan_since(c, D0, ["ProspectID"]))
    assert c.reads == ["L2", "L3"]                               # L1 was stored before the stop


def test_lead_details_use_the_client_bulk_size():
    class Bulk:
        max_ids_per_bulk = 250
        calls: list = []

        def request(self, method, path, json=None):
            ids = json["SearchParameters"]["LeadIds"]
            self.calls.append((len(ids), json["Paging"]["PageSize"]))
            return {"Leads": [{"ProspectID": i} for i in ids], "RecordCount": len(ids)}

    c = Bulk()
    out = sources.leads(c, [f"L{i}" for i in range(600)] + ["L0", ""])
    assert len(out) == 600
    assert c.calls == [(250, 250), (250, 250), (100, 250)]


def test_fetch_lead_histories_stops_on_deferral_and_resumes(tmp_path, monkeypatch, capsys):
    from scripts import fetch_lead_histories as flh

    budget = {"left": 2}

    class Client:
        def request(self, method, path, params=None, json=None):
            if budget["left"] == 0:
                raise AdmissionDeferred("deferred", "hourly request budget used",
                                        datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
            budget["left"] -= 1
            return {"ProspectActivities": [{"ActivityId": params["leadId"]}]}

    monkeypatch.setattr(flh, "LeadSquaredClient", Client)
    ids, out = tmp_path / "ids.json", tmp_path / "out.jsonl"
    ids.write_text(json.dumps(["A", "B", "A", "C", "D"]))
    with pytest.raises(SystemExit) as stop:
        flh.main(str(ids), str(out), 1)
    assert stop.value.code == flh.DEFERRED_EXIT
    assert "rerun the same command after 14 Oct 17:30 IST" in capsys.readouterr().err
    assert [json.loads(x)["lead_id"] for x in out.read_text().splitlines()] == ["A", "B"]

    budget["left"] = 10
    assert flh.main(str(ids), str(out), 1) == 0
    assert [json.loads(x)["lead_id"] for x in out.read_text().splitlines()] == ["A", "B", "C", "D"]


def test_store_path_is_git_ignored():
    assert enrolment_store.DEFAULT_STORE.split("/")[-3:-1] == ["data", "leadsquared"]
