"""Load credentials from the project's single secrets file into ``os.environ``.

The one credential file is ``.env`` in the repo root (git-ignored). Point
``SALES_SKILL_ENV_FILE`` at another path to use a copy kept elsewhere.

Stdlib only. Values are taken literally (no ``$`` expansion, so keys like
``u$r...`` survive), surrounding quotes are stripped, empty values are skipped,
and variables already set in the real environment always win.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_FILE = REPO_ROOT / ".env"


def secrets_path() -> Path:
    override = os.environ.get("SALES_SKILL_ENV_FILE")
    return Path(override).expanduser() if override else SECRETS_FILE


def load_dotenv(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Set every variable from ``path`` (default: the secrets file) that isn't already set.

    Returns the variables it set. A missing file is not an error.
    """
    p = Path(path) if path else secrets_path()
    if not p.is_file():
        return {}
    loaded: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
