"""Client for the centralized call-transcript API.

Credentials are read from the environment, never hard-coded:

    TRANSCRIPT_API_BASE   default https://centralized-transcript-api.altlapps.com/api/v1/
    TRANSCRIPT_API_KEY
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

DEFAULT_BASE = "https://centralized-transcript-api.altlapps.com/api/v1/"
IST = timezone(timedelta(hours=5, minutes=30))


class TranscriptError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def normalize_phone(phone: str | None) -> str | None:
    """Return ``91XXXXXXXXXX`` for an Indian mobile in any common format, else digits as-is.

    Handles ``+91-98765 43210``, ``098765 43210``, ``9876543210``, ``91917...``.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if len(digits) == 14 and digits.startswith("9191"):
        return digits[2:]
    return digits


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fix_call_tz(start: datetime | None, created: datetime | None, now: datetime | None = None) -> bool:
    """Whether a call's ``start_time`` is really IST despite its ``Z`` suffix.

    The API mixes dialers: some send true UTC, others send IST wall-clock time
    labelled ``Z``. A record can't be created before its call started, nor can a
    call start in the future, so either of those means the time is IST.
    """
    if start is None:
        return False
    now = now or datetime.now(timezone.utc)
    return start > now or (created is not None and created < start)


@dataclass
class Call:
    phone: str               # the number that was searched (normalized)
    kind: str                # "sales" or "support"
    caller_id: str | None
    agent_name: str | None
    start_time: datetime | None
    end_time: datetime | None
    duration: int | None     # seconds
    transcript: str
    transcript_url: str | None
    audio_url: str | None
    created_at: datetime | None = None

    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript.strip())

    @classmethod
    def from_api(cls, phone: str, kind: str, raw: dict) -> "Call":
        start = _parse_dt(raw.get("start_time"))
        end = _parse_dt(raw.get("end_time"))
        created = _parse_dt(raw.get("createdAt"))
        if _fix_call_tz(start, created):
            start = start.replace(tzinfo=IST)
            end = end.replace(tzinfo=IST) if end else None
        return cls(
            phone=phone,
            kind=kind,
            caller_id=raw.get("caller_id"),
            agent_name=(raw.get("agent_name") or "").strip() or None,
            start_time=start,
            end_time=end,
            duration=raw.get("call_duration"),
            transcript=((raw.get("transcript") or {}).get("text") or ""),
            transcript_url=raw.get("s3_transcript_url"),
            audio_url=raw.get("s3_audio_file_url"),
            created_at=created,
        )


class TranscriptClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60,
        max_retries: int = 3,
        batch_size: int = 10,  # API maximum per request
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or os.environ.get("TRANSCRIPT_API_KEY")
        if not self.api_key:
            raise TranscriptError("Missing credentials: set TRANSCRIPT_API_KEY")
        base = base_url or os.environ.get("TRANSCRIPT_API_BASE") or DEFAULT_BASE
        self.base_url = base.rstrip("/") + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.batch_size = batch_size
        self.session = session or requests.Session()

    def request(self, method: str, path: str, params: dict | None = None, json: Any = None) -> Any:
        url = self.base_url + path.lstrip("/")
        headers = {"x-api-key": self.api_key}
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(
                    method, url, params=params, json=json, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise TranscriptError(f"{method} {path} failed: {exc}") from exc
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            break

        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = resp.text
        if not resp.ok:
            raise TranscriptError(
                f"{method} {path} -> HTTP {resp.status_code}: {data}",
                status_code=resp.status_code,
                payload=data,
            )
        return data

    # ------------------------------------------------------------- search

    def search_raw(self, numbers: Iterable[str], call_status: str | None = "answered") -> dict:
        """Raw API response keyed by number, batching large lists."""
        nums = list(dict.fromkeys(n for n in (normalize_phone(x) for x in numbers) if n))
        result: dict = {}
        for i in range(0, len(nums), self.batch_size):
            chunk = nums[i : i + self.batch_size]
            data = self.request(
                "GET",
                "webhook/search-by-numbers-v1",
                params={"numbers": ",".join(chunk), "call_status": call_status},
            )
            result.update(data or {})
        return result

    def search(self, numbers: Iterable[str], call_status: str | None = "answered") -> list[Call]:
        """All sales and support calls for the given numbers, newest first."""
        calls: list[Call] = []
        for phone, groups in self.search_raw(numbers, call_status).items():
            groups = groups or {}
            for kind, key in (("sales", "sales_call"), ("support", "support_calls")):
                calls.extend(Call.from_api(phone, kind, raw) for raw in groups.get(key) or [])
        calls.sort(key=lambda c: c.start_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return calls

    # ----------------------------------------------------------- generate

    def generate_transcripts(self, phones: Iterable[str]) -> Any:
        """Queue transcript generation for calls that don't have one yet.

        The API takes plain 10-digit student numbers.
        """
        body = []
        for p in phones:
            n = normalize_phone(p)
            if n:
                body.append({"student_phone": n[-10:]})
        if not body:
            return None
        return self.request("POST", "webhook/generate-transcripts-by-phone", json=body)
