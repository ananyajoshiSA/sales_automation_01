from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from integrations import cloudflare

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"


def test_cloudflare_is_switched_off():
    assert cloudflare.SWITCHED_OFF
    with pytest.raises(cloudflare.CloudflareOff):
        cloudflare.ensure_on()


def test_no_npm_script_deploys_or_writes_remotely():
    scripts = json.loads((DASHBOARD / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert not [k for k, v in scripts.items() if "deploy" in v.replace("deployed", "") or "--remote" in v]
    assert "switched off" in scripts["deploy:prod"]


def test_worker_has_no_cron_no_public_url_and_is_off():
    text = (DASHBOARD / "wrangler.jsonc").read_text(encoding="utf-8")
    assert re.search(r'"crons"\s*:\s*\[\s*\]', text)
    assert re.search(r'"workers_dev"\s*:\s*false', text)
    assert re.search(r'"DASHBOARD_OFF"\s*:\s*"1"', text)


def test_d1_backfill_stops_before_reading_leadsquared(monkeypatch):
    from scripts import d1_backfill

    def no_client(*a, **k):
        raise AssertionError("LeadSquared must not be read")

    monkeypatch.setattr(d1_backfill, "LeadSquaredClient", no_client)
    monkeypatch.setattr(sys, "argv", ["d1_backfill.py", "2026-10-05", "2026-10-05", "out.sql"])
    with pytest.raises(SystemExit) as e:
        d1_backfill.main()
    assert str(e.value) == cloudflare.MESSAGE
