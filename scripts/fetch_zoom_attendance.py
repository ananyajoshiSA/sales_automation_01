"""Who attended each meeting or webinar a Zoom host ran in a date range, one row per person (read-only).

Output holds attendee PII, so write it under data/ (git-ignored). Times are converted to IST.

    PYTHONPATH=. python scripts/fetch_zoom_attendance.py main host@lawsikho.in 2026-10-01 2026-10-07 data/zoom_attendance.csv
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import integrations  # noqa: F401  (loads .env)
from integrations.http import ApiError
from integrations.zoom import ZoomClient, attendance

IST = timezone(timedelta(hours=5, minutes=30))
COLUMNS = ["meeting_id", "topic", "start_ist", "email", "name", "minutes", "sessions", "first_join_ist",
           "last_leave_ist"]


def to_ist(utc: str | None) -> str | None:
    if not utc:
        return None
    return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(IST).strftime("%Y-%m-%d %H:%M")


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 1
    account, host, start, end, path = argv
    try:
        z = ZoomClient(account)
        out = []
        for m in z.past_meetings(host, start, end):
            for p in attendance(z.meeting_participants(m["uuid"])):
                out.append({"meeting_id": m.get("id"), "topic": m.get("topic"), "start_ist": to_ist(m.get("start_time")),
                            "email": p["email"], "name": p["name"], "minutes": p["minutes"], "sessions": p["sessions"],
                            "first_join_ist": to_ist(p["first_join"]), "last_leave_ist": to_ist(p["last_leave"])})
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} attendee rows -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
