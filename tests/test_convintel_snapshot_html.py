"""The conversation-intelligence snapshot fixture and the offline page built from it (snapshot_html)."""

import json
import re
from pathlib import Path

import pytest

from analytics.convintel import snapshot_html

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "convintel_snapshot.json"
CONTRACT_KEYS = {"version", "range", "generatedAt", "dataAsOf", "privacy", "definitions", "coverage", "org", "teams",
                 "callers", "leads", "leadsTotal", "calls", "callsTotal", "objections", "words", "integrity", "opportunities",
                 "opportunitiesTotal", "opportunitiesByKind", "zip", "coachingTeams", "accountability", "revenue",
                 "reconciliation", "filters"}


def snap() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_follows_the_snapshot_contract():
    s = snap()
    assert set(s) == CONTRACT_KEYS and s["version"] == "ci-snapshot-1"
    assert s["privacy"] == {"excerpts": False, "leadNumbers": False}
    assert len([t for t in s["teams"] if t.get("kind") == "team"]) == 2
    assert len(s["callers"]) == 4 and {c["kind"] for c in s["callers"]} == {"person", "shared"}
    assert s["revenue"]["measurable"] is False and s["coverage"]["gapsTotal"] >= len(s["coverage"]["gaps"])
    assert not re.search(r"\b91\d{10}\b", FIXTURE.read_text(encoding="utf-8"))     # no phone numbers


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
