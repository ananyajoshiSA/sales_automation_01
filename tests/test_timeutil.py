import ast
import pathlib
from datetime import datetime, timezone

from integrations.timeutil import IST, ist_day, ist_day_start, unreadable, utc

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_utc_parsing_and_ist_day_boundary():
    assert utc("2026-10-05 18:29:59.000") == datetime(2026, 10, 5, 18, 29, 59, tzinfo=timezone.utc)
    assert ist_day(utc("2026-10-05 18:29:59")) == "2026-10-05"   # 23:59:59 IST
    assert ist_day(utc("2026-10-05 18:30:00")) == "2026-10-06"   # 00:00 IST the next day
    assert ist_day_start("2026-10-05") == datetime(2026, 10, 5, tzinfo=IST)
    assert utc(None) is None and utc("10/5/2026 2:29:02 PM") is None
    assert unreadable("10/5/2026 2:29:02 PM") and not unreadable("") and not unreadable("2026-10-05 10:00:00")


def _sources():
    for d in ("analytics", "integrations", "scripts"):
        yield from (ROOT / d).rglob("*.py")


def test_one_definition_of_ist_and_no_zoneless_now():
    """Time zones live in integrations/timeutil.py; a second IST or a zoneless now() is how 5 h 30 m errors creep in."""
    problems = []
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Assign) and rel != "integrations/timeutil.py"
                    and any(isinstance(t, ast.Name) and t.id == "IST" for t in node.targets)):
                problems.append(f"{rel}:{node.lineno} defines IST")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("now", "today", "utcnow")
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in ("datetime", "date")
                    and not node.args and not node.keywords):
                problems.append(f"{rel}:{node.lineno} reads the clock without a time zone")
    assert problems == []


def test_no_code_reads_a_call_start_from_the_note():
    """A call note's StartTime is UTC in one place and IST in another, with no label; CreatedOn is the call start."""
    hits = [f"{p.relative_to(ROOT).as_posix()}" for p in _sources()
            if p.name != "timeutil.py" and ('"StartTime"' in p.read_text(encoding="utf-8") or "'StartTime'" in p.read_text(encoding="utf-8"))]
    assert hits == []
