"""Write one self-contained page for a conversation-intelligence snapshot, to open offline or send around.

The page is dashboard/public/ci.html with style.css and ci.js inlined and the snapshot embedded as
<script type="application/json" id="ci-data">, so the dashboard's own renderer draws it from a file:// URL with
no server and no external requests. It holds lead ids and caller names (and excerpts, if the snapshot has them):
write it under data/ or exports/ (git-ignored) and share it only inside the team.

    python -m analytics.convintel.snapshot_html SNAPSHOT.json OUT.html
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[2] / "dashboard" / "public"
CSS_TAG = '<link rel="stylesheet" href="style.css">'
JS_TAG = '<script src="ci.js"></script>'
TITLE = re.compile(r"<title>([^<]*)</title>")


def embed(snapshot: dict) -> str:
    """The snapshot as JSON that can sit inside a <script> element: every '<' becomes \\u003c, so neither '</'
    nor '<!--' can end or confuse the element, and JSON.parse still reads the same text."""
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def page(snapshot: dict, public: Path = PUBLIC) -> str:
    doc = (public / "ci.html").read_text(encoding="utf-8")
    css = (public / "style.css").read_text(encoding="utf-8")
    js = (public / "ci.js").read_text(encoding="utf-8")
    if CSS_TAG not in doc or JS_TAG not in doc:
        raise RuntimeError("dashboard/public/ci.html no longer links style.css and ci.js as expected")
    # Inside an inline element only these sequences matter; "<\/" means the same in JavaScript strings.
    js = re.sub(r"</(script)", r"<\\/\1", js, flags=re.I).replace("<!--", "<\\!--")
    css = re.sub(r"</(style)", r"<\\/\1", css, flags=re.I)
    label = (snapshot.get("range") or {}).get("label") or (snapshot.get("range") or {}).get("key") or ""
    if label:
        doc = TITLE.sub(lambda m: f"<title>{m.group(1)} · {html.escape(label)}</title>", doc, count=1)
    doc = doc.replace(CSS_TAG, f"<style>\n{css}</style>", 1)
    return doc.replace(JS_TAG, f'<script type="application/json" id="ci-data">{embed(snapshot)}</script>\n'
                               f"  <script>\n{js}</script>", 1)


def write_html(path: str | os.PathLike, snapshot: dict) -> None:
    Path(path).write_text(page(snapshot), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    with open(args[0], encoding="utf-8") as fh:
        snapshot = json.load(fh)
    write_html(args[1], snapshot)
    print(f"wrote {args[1]} (open it in a browser; it works offline)", file=sys.stderr)


if __name__ == "__main__":
    main()
