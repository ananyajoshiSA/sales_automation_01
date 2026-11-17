"""The AI voice agent's (TimePay) calls to given leads, looked up by phone, for the call plan (read-only).

The bot does discovery for bootcamp leads before a counsellor or closer calls. Its notes are not in
LeadSquared, so a lead whose discovery the bot did looks untouched there. This reads each lead's TimePay
calls (newest 30) and keeps the real conversations: contacted and BOT_MIN_SECS or longer, with the
summary and outcome. Input is a JSON list of {lead_id, phone}; output maps lead_id to its calls, newest
first. Holds lead details: write it under data/.

    PYTHONPATH=. python scripts/fetch_bot_calls.py data/leads.json data/bot_calls.json
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import integrations  # noqa: F401  (loads .env)
from integrations.timepay import TimePayClient

BOT_MIN_SECS = 120  # the same 2-minute bar as a real conversation with a person
WORKERS = 8


def phone10(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def real_bot_calls(logs: list[dict]) -> list[dict]:
    keep = [{"start_ist": l.get("start_time"), "campaign": l.get("campaign_name"), "secs": int(l.get("duration") or 0),
             "outcome": l.get("disposition"), "summary": (l.get("summary") or "").strip()}
            for l in logs if l.get("status") == "Contacted" and int(l.get("duration") or 0) >= BOT_MIN_SECS]
    return sorted(keep, key=lambda c: c["start_ist"] or "", reverse=True)


def main(src: str, out: str) -> None:
    leads = [l for l in json.load(open(src)) if len(phone10(l.get("phone"))) == 10]
    tp = TimePayClient()

    def one(l: dict) -> tuple[str, list[dict]]:
        return l["lead_id"], real_bot_calls(list(tp.iter_logs(None, None, max_pages=3, type="call", phone=phone10(l["phone"]))))

    with ThreadPoolExecutor(WORKERS) as pool:
        found = {lid: calls for lid, calls in pool.map(one, leads) if calls}
    json.dump(found, open(out, "w"), ensure_ascii=False)
    print(f"{len(leads)} leads looked up, {len(found)} had a real bot conversation -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
