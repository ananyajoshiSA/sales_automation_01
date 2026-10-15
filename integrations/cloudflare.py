"""Cloudflare switch: while ``SWITCHED_OFF`` is True nothing in this repo deploys to or writes to Cloudflare.

Switched off completely on 14 Oct 2026 at the user's request. The dashboard Worker has no cron and answers
"switched off" (dashboard/wrangler.jsonc), its npm deploy and remote-migration scripts only print a notice,
and the Python paths that exist only to load the dashboard's D1 database (``analytics.convintel.d1push --push``
and ``scripts/d1_backfill.py``) stop before any request. To switch back on, follow dashboard/README.md.
"""

from __future__ import annotations

SWITCHED_OFF = True
MESSAGE = ("Cloudflare is switched off (since 14 Oct 2026, the user's request): nothing is deployed or written "
           "to D1. dashboard/README.md says how to switch it back on.")


class CloudflareOff(RuntimeError):
    pass


def ensure_on() -> None:
    """Raise ``CloudflareOff`` while Cloudflare is switched off; call it before any Cloudflare request."""
    if SWITCHED_OFF:
        raise CloudflareOff(MESSAGE)
