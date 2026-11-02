"""Read-only Google Sheets download: a whole spreadsheet as one .xlsx file, without signing in.

Works for a sheet shared "anyone with the link can view". The export holds every tab, hidden tabs included,
with formulas as their computed values (checked 2 Nov 2026 against per-tab CSV exports: rows and cells matched).
Sheet IDs are not code: they give access to student data, so they come from the environment (root ``.env``):

    COMMUNITY_COLLECTION_SHEET_ID   the "Community Webinar Collection Sheet"
    BOOTCAMP_POOL_SHEET_ID          the "Bootcamp Pool data" sheet
"""

from __future__ import annotations

import os
import time

import requests

from integrations.env import load_dotenv

EXPORT = "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx"
XLSX_MAGIC = b"PK"


class SheetError(Exception):
    pass


def sheet_id(env_name: str) -> str:
    load_dotenv()
    value = (os.environ.get(env_name) or "").strip()
    if not value:
        raise SheetError(f"{env_name} is not set: add the sheet's ID to the environment settings or .env")
    return value


def download_xlsx(sheet: str, path: str, *, session: requests.Session | None = None, retries: int = 3,
                  timeout: float = 120) -> int:
    """Save the spreadsheet ``sheet`` (an ID) as ``path``; returns its size in bytes.

    A sheet that is not shared by link answers with a Google sign-in page, not a file, so anything that is not an
    .xlsx is an error rather than an empty report.
    """
    s = session or requests.Session()
    for attempt in range(retries + 1):
        try:
            r = s.get(EXPORT.format(id=sheet), timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise SheetError(f"sheet download failed: {type(exc).__name__}") from exc
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(2 ** attempt)
            continue
        if r.status_code != 200:
            raise SheetError(f"sheet download answered HTTP {r.status_code}")
        if not r.content.startswith(XLSX_MAGIC):
            raise SheetError("sheet download did not return a spreadsheet: is it still shared 'anyone with the link'?")
        tmp = f"{path}.part"
        with open(tmp, "wb") as fh:
            fh.write(r.content)
        os.replace(tmp, path)
        return len(r.content)
    raise SheetError("sheet download failed")
