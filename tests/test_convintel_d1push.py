import copy
import json
import re
import sqlite3
from pathlib import Path

import pytest
import responses

from analytics.convintel import d1push, snapshot_html
from analytics.convintel.d1push import (
    API, GO_AHEAD, MAX_STATEMENT_BYTES, compact, push, rows_estimate, split, statements, write_sql,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "convintel_snapshot.json"
MIGRATION = ROOT / "dashboard" / "migrations" / "0004_conversation_intelligence.sql"
CONTRACT_KEYS = {"version", "range", "generatedAt", "dataAsOf", "privacy", "definitions", "coverage", "org", "teams",
                 "callers", "leads", "leadsTotal", "calls", "callsTotal", "objections", "integrity", "opportunities",
                 "opportunitiesTotal", "opportunitiesByKind", "zip", "coachingTeams", "accountability", "revenue",
                 "reconciliation", "filters"}


def snap() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def big(key: str = "30d", n: int = 1000) -> dict:
    """The fixture padded with awkward text so it needs several parts: quotes, backslashes, control characters
    (JSON \\u escapes), Devanagari and ₹ (multi-byte), and '</script>'."""
    s = snap()
    s["range"] = {**s["range"], "key": key, "label": "Last 30 days"}
    base = s["calls"][0]
    s["calls"] = [{**base, "callId": f"c-{i:05d}", "caller": "Asha O'Neil \\ \"A\"",
                   "summary": f"फीस ₹25,000 EMI {i} \x01\x1f '' </script> \\u0041 " + "x" * (i % 17)} for i in range(n)]
    s["callsTotal"] = n
    return s


def load(stmts: list[str]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION.read_text(encoding="utf-8"))
    for st in stmts:
        db.execute(st)
    return db


def joined(db: sqlite3.Connection, key: str) -> str:
    rows = db.execute("SELECT part, parts, generated_at, body FROM ci_snapshot WHERE range_key = ? ORDER BY part", (key,)).fetchall()
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert {r[1] for r in rows} == {len(rows)} and len({r[2] for r in rows}) == 1
    return "".join(r[3] for r in rows)


def test_fixture_follows_the_snapshot_contract():
    s = snap()
    assert set(s) == CONTRACT_KEYS and s["version"] == "ci-snapshot-1"
    assert s["privacy"] == {"excerpts": False, "leadNumbers": False}
    assert len([t for t in s["teams"] if t.get("kind") == "team"]) == 2
    assert len(s["callers"]) == 4 and {c["kind"] for c in s["callers"]} == {"person", "shared"}
    assert s["revenue"]["measurable"] is False and s["coverage"]["gapsTotal"] >= len(s["coverage"]["gaps"])
    assert not re.search(r"\b91\d{10}\b", FIXTURE.read_text(encoding="utf-8"))     # no phone numbers


def test_statements_stay_under_90_kb_and_reassemble_exactly():
    snaps = [snap(), big()]
    stmts = statements(snaps)
    assert all(len(st.encode("utf-8")) < MAX_STATEMENT_BYTES for st in stmts)
    parts = [st for st in stmts if st.startswith("INSERT INTO ci_snapshot (") and "'30d'" in st[:120]]
    assert len(parts) >= 3
    db = load(stmts)
    for s in snaps:
        body = joined(db, s["range"]["key"])
        assert body == compact(s) and json.loads(body) == s
    idx = db.execute("SELECT range_key, label, day_from, day_to, generated_at, parts, bytes FROM ci_snapshot_index "
                     "ORDER BY range_key").fetchall()
    assert idx[0] == ("30d", "Last 30 days", "2026-10-02", "2026-10-08", "2026-10-09 06:30:00", len(parts),
                      len(compact(snaps[1]).encode("utf-8")))
    assert idx[1][:2] == ("7d", "Last 7 days")


def test_reload_replaces_the_old_parts():
    db = load(statements([big()]))
    small = big(n=10)
    for st in statements([small]):
        db.execute(st)
    assert joined(db, "30d") == compact(small)
    assert db.execute("SELECT parts FROM ci_snapshot_index WHERE range_key = '30d'").fetchone()[0] == 1


def test_quotes_are_escaped_and_never_split():
    s = snap()
    s["callers"][0]["caller"] = "D'Souza ''x'' '"
    stmts = statements([s])
    assert "D''Souza ''''x'''' ''" in stmts[1]
    assert json.loads(joined(load(stmts), "7d"))["callers"][0]["caller"] == "D'Souza ''x'' '"
    # A part limit that lands on quotes: every piece still fits once doubled, and they rejoin exactly.
    text = "'" * 50 + "ab'c" * 30
    pieces = split(text, 7)
    assert "".join(pieces) == text and all(len(p.encode()) + p.count("'") <= 7 for p in pieces)


def test_cuts_never_fall_inside_a_json_escape():
    text = compact({"a": "\x01\x02\\\"\n" * 400 + "ü₹फ" * 300})
    for limit in (13, 50, 97):
        pieces = split(text, limit)
        assert "".join(pieces) == text
        for p in pieces[:-1]:
            tail = re.search(r"(\\+)(u[0-9a-f]{0,3})?$", p)
            assert not tail or (len(tail.group(1)) % 2 == 0 and not tail.group(2)), p[-8:]


def test_refuses_what_the_dashboard_must_not_get():
    s = snap()
    for bad, why in [({**s, "privacy": {"excerpts": True, "leadNumbers": False}}, "excerpts"),
                     ({**s, "range": {**s["range"], "key": "7d'; DROP TABLE users; --"}}, "range key"),
                     ({**s, "version": "other"}, "version"), ({**s, "generatedAt": "today"}, "generatedAt")]:
        with pytest.raises(ValueError, match=why):
            statements([bad])
    with pytest.raises(ValueError, match="two snapshots"):
        statements([s, copy.deepcopy(s)])


def test_refuses_phone_numbers_and_transcript_text():
    s = snap()
    assert d1push.leaks(s) == []
    s["calls"][0]["summary"] = "The lead asked us to call +91 98765 43210 after six."
    s["calls"][1]["findings"][0]["excerpt"] = "haan main pay kar dunga"        # privacy.excerpts is false
    s["opportunities"][0]["leadNumber"] = "919000000001"
    with pytest.raises(ValueError) as e:
        statements([s])
    msg = str(e.value)
    assert "calls[0].summary, calls[1].findings[0].excerpt, opportunities[0].leadNumber" in msg
    assert "98765" not in msg and "919000000001" not in msg and "haan" not in msg
    # Ids, days, times, amounts and empty fields are not numbers.
    ok = {"leadId": "12345678-1234-5678-9012-919876543210", "day": "2026-10-08 16:20", "evidence": "₹25,000, 180-209 s",
          "excerpt": "", "number": None, "privacy": {"excerpts": False, "leadNumbers": False}}
    assert d1push.leaks(ok) == []


def test_command_line_removes_numbers_from_free_text(tmp_path, capsys):
    s = snap()
    s["calls"][0]["summary"] = "Asked for a callback on 9876543210 tomorrow."
    src, out = tmp_path / "snap.json", tmp_path / "ci.sql"
    src.write_text(json.dumps(s), encoding="utf-8")
    d1push.main([str(src), "--sql", str(out)])
    got = json.loads(joined(load([st for st in out.read_text(encoding="utf-8").split(";\n") if st.strip()]), "7d"))
    assert got["calls"][0]["summary"] == "Asked for a callback on [number removed] tomorrow."
    err = capsys.readouterr().err
    assert "removed what looked like a phone number from 1 text (calls[0].summary)" in err and "9876543210" not in err


def test_snapshot_size_cap(monkeypatch):
    monkeypatch.setattr(d1push, "MAX_SNAPSHOT_BYTES", 10_000)
    with pytest.raises(ValueError, match="cap its lists"):
        statements([snap()])


def test_fit_trims_capped_lists_and_keeps_totals():
    s = big(n=600)
    s["coverage"]["gaps"] = s["coverage"]["gaps"] * 50
    fitted, kept = d1push.fit(s, 120_000)
    assert len(compact(fitted).encode("utf-8")) <= 120_000
    assert kept["calls"][0] == 600 and 0 < kept["calls"][1] < 600 and kept["leads"] == (5, 5)
    assert fitted["calls"] == s["calls"][:kept["calls"][1]]                  # the most important first, kept
    assert fitted["callsTotal"] == 600 and fitted["coverage"]["gapsTotal"] == s["coverage"]["gapsTotal"]
    assert len(s["calls"]) == 600 and len(s["coverage"]["gaps"]) == 200      # the input is not changed
    assert d1push.fit(snap())[1]["calls"] == (8, 8)


def test_command_line_trims_to_fit_and_writes_sql(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(d1push, "MAX_SNAPSHOT_BYTES", 150_000)
    src, out = tmp_path / "snap.json", tmp_path / "ci.sql"
    src.write_text(json.dumps(big(n=600)), encoding="utf-8")
    d1push.main([str(src), "--sql", str(out)])
    db = load([st for st in out.read_text(encoding="utf-8").split(";\n") if st.strip()])
    got = json.loads(joined(db, "30d"))
    assert 0 < len(got["calls"]) < 600 and got["callsTotal"] == 600
    err = capsys.readouterr().err
    assert "trimmed to fit the dashboard (calls 600 -> " in err and "remote" in err


def test_write_sql(tmp_path):
    out = tmp_path / "ci.sql"
    n = write_sql(out, [snap()])
    text = out.read_text(encoding="utf-8")
    assert text.count(";\n") == n == 3 and text.startswith("DELETE FROM ci_snapshot WHERE range_key = '7d';")


@responses.activate
def test_push_refuses_without_the_go_ahead(capsys):
    with pytest.raises(PermissionError, match="go-ahead"):
        push([snap()], "acc", "db", "tok-secret", yes=False)
    with pytest.raises(SystemExit) as e:
        d1push.main([str(FIXTURE), "--push"])
    assert str(e.value) == GO_AHEAD
    assert len(responses.calls) == 0


@responses.activate
def test_push_refuses_over_the_row_cap(monkeypatch):
    monkeypatch.setattr(d1push, "MAX_PUSH_ROWS", 2)
    with pytest.raises(ValueError, match="over the 2 cap"):
        push([snap()], "acc", "db", "tok-secret", yes=True)
    assert len(responses.calls) == 0


@responses.activate
def test_push_posts_each_statement_and_counts_rows(capsys):
    url = API.format(account="acc", database="db")
    responses.add(responses.POST, url, json={"success": True, "errors": [], "result": [{"meta": {"rows_written": 1}}]})
    stmts = statements([snap()])
    out = push([snap()], "acc", "db", "tok-secret", yes=True)
    assert out == {"statements": len(stmts), "rowsEstimate": rows_estimate(stmts), "rowsWritten": len(stmts), "ranges": ["7d"]}
    assert [json.loads(c.request.body)["sql"] for c in responses.calls] == stmts
    assert all(c.request.headers["Authorization"] == "Bearer tok-secret" for c in responses.calls)
    responses.replace(responses.POST, url, status=400, json={"success": False, "errors": [{"code": 7500, "message": "SQLITE_ERROR"}]})
    with pytest.raises(RuntimeError) as e:
        push([snap()], "acc", "db", "tok-secret", yes=True)
    assert "HTTP 400: SQLITE_ERROR" in str(e.value) and "tok-secret" not in str(e.value)
    assert "tok-secret" not in capsys.readouterr().err


def test_ids_default_from_wrangler_config():
    acc, db = d1push.d1_ids()
    assert re.fullmatch(r"[0-9a-f]{32}", acc) and re.fullmatch(r"[0-9a-f-]{36}", db)


def test_snapshot_html_embeds_the_data_offline(tmp_path):
    s = snap()
    s["callers"][0]["caller"] = "Asha </script><script>alert(1)</script> <!--"
    out = tmp_path / "ci.html"
    snapshot_html.write_html(out, s)
    page = out.read_text(encoding="utf-8")
    data = re.search(r'<script type="application/json" id="ci-data">(.*?)</script>', page, re.S).group(1)
    assert "<" not in data and json.loads(data) == s
    assert page.count("</script>") == 2 and "<!--" not in page.split('id="ci-data">')[1]
    assert not re.search(r"https?:|//cdn|<link|src=|@import|url\(", page)
    assert "<style>" in page and "window.CI" in page and "<title>Conversation Intelligence · Last 7 days</title>" in page


def test_snapshot_html_notices_a_changed_page(tmp_path):
    for f in ("style.css", "ci.js"):
        (tmp_path / f).write_text("x", encoding="utf-8")
    (tmp_path / "ci.html").write_text("<html><body></body></html>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no longer links"):
        snapshot_html.page(snap(), tmp_path)
