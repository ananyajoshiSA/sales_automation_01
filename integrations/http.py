"""Shared JSON-over-HTTP helper for the read-only ad, Zoom and TimePay clients.

Retries 429 and 5xx with backoff (honouring Retry-After), raises ``ApiError`` with
the status and parsed body otherwise. Never logs request headers or query strings,
because they carry tokens.
"""

from __future__ import annotations

import time
from typing import Any

import requests


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    label: str,
    max_retries: int = 3,
    timeout: float = 30,
    error: type[ApiError] = ApiError,
    **kwargs: Any,
) -> Any:
    """``label`` names the call in errors (e.g. ``"GET /users"``) so URLs with secrets never leak."""
    for attempt in range(max_retries + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if attempt < max_retries:
                time.sleep(2**attempt)
                continue
            raise error(f"{label} failed: {type(exc).__name__}") from exc
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
            wait = resp.headers.get("Retry-After")
            time.sleep(float(wait) if wait and wait.isdigit() else 2**attempt)
            continue
        break
    try:
        data = resp.json() if resp.content else None
    except ValueError:
        data = resp.text[:500]
    if not resp.ok:
        raise error(f"{label} returned HTTP {resp.status_code}: {_short(data)}", resp.status_code, data)
    return data


def _short(data: Any) -> str:
    s = str(data)
    return s if len(s) <= 300 else s[:297] + "..."
