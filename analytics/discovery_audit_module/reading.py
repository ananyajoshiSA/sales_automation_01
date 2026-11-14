"""Claude reads every lead's day of calls (no model API, no keyword rules) and writes one result file per lead.

``prepare`` writes data/discovery_audit/<day>/reading/: INSTRUCTIONS.md (the audit prompt, how it applies to a
lead's whole day, the exact JSON shape) and one pack per few leads. A Claude Code session (or its helper readers)
writes results/<lead_key>.json and runs ``check`` until it passes; a passing check writes results/<lead_key>.ok
holding the file's SHA-256, so a later edit is noticed. Scores, labels and the PDF score are computed here from the
five subscores, never typed by the reader.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

PROMPT = Path(__file__).with_name("prompts") / "final_prompt_oct_2026.txt"
READER = Path(__file__).with_name("prompts") / "reader.md"
PER_PACK = 5

READER_COLUMNS = ["Caller Name", "Lead Name", "Profile Summary (Discovery Quality)", "Pain Point Discovery",
                  "Primary Pain Identified", "Discovery Framework Adherence", "Goal Setting Clarity", "Pitch Discipline",
                  "IIT Compliance", "Red Flags — Missed Discovery Signals", "Coaching Action (Caller-Specific)",
                  "Closer Briefing"]
SHEET_COLUMNS = ["Caller Name", "Lead Name", "Call Date", "Duration", "Mobile Number", "Conversion Potential (H,M,L)",
                 "Profile Summary (Discovery Quality)", "Pain Point Discovery", "Primary Pain Identified",
                 "Discovery Framework Adherence", "Goal Setting Clarity", "Pitch Discipline", "IIT Compliance",
                 "Red Flags — Missed Discovery Signals", "Coaching Action (Caller-Specific)", "Closer Briefing"]
PDF_FIELDS = ["stage_of_call", "education", "immediate_goal", "current_plan", "current_income", "burning_problem_note",
              "score_reason", "call_summary", "highlight_strongest", "highlight_miss", "highlight_end",
              "what_went_wrong", "improvements"]
SUBSCORES = {"interest": {0, 1, 2, 3}, "burning_problem": {0, 1, 2}, "buying_signals": {0, 1, 2},
             "objections": {0, 0.75, 1.5}, "commitment": {0, 0.5, 1, 1.5}}
IIT = {"Compliant", "Non-Compliant", "Not Mentioned"}
STRENGTHS = {"explains_programme_clearly", "dated_next_step", "thorough_background_discovery", "explores_pain",
             "challenges_backup_plan", "maps_decision_maker", "checks_financial_readiness", "direct_close_ask"}
GAPS = {"income_claim", "client_or_job_guarantee", "financial_readiness_not_assessed", "pitch_before_discovery",
        "cost_of_inaction_not_explored", "pain_not_connected_to_course", "decision_maker_not_mapped",
        "no_dated_next_step", "buying_signal_not_escalated", "backup_plan_accepted", "lead_question_left_unclear"}
# The quote must sit in straight double quotes, so the word-for-word check reaches it.
COMPLIANCE = re.compile(r'^CRITICAL: [^()|"]+ \("[^"]{6,}"\)$')
QUOTE = re.compile(r'"([^"]{6,})"')


# ------------------------------------------------------------------ scoring (code, not the reader)


def totals(s: dict) -> tuple[float, float]:
    """(relaxed, strict) totals out of 10. Relaxed: no objection raised = full 1.5 (the prompt's rule). Strict: no
    objection but something left open (fee deferred, "will think", someone to consult) = 0.75."""
    base = s["interest"] + s["burning_problem"] + s["buying_signals"] + s["commitment"]
    if s.get("objection_raised"):
        return base + s["objections"], base + s["objections"]
    return base + 1.5, base + (0.75 if s.get("open_concern") else 1.5)


def relaxed_label(total: float) -> str:
    return "H" if total >= 6.5 else "M" if total >= 4.0 else "L"


def strict_label(total: float) -> str:
    return "H" if total >= 7.0 else "M" if total >= 4.0 else "L"


def pdf_band(total: float) -> str:
    """The PDF's band on the strict total (provisional until the PDF prompt file is in the repo)."""
    return "HOT" if total >= 7.0 else "WARM" if total >= 4.0 else "COLD"


# ------------------------------------------------------------------ packs


def lead_key(entry: dict) -> str:
    return f"{entry['calls_day']}_{entry['lead']['ProspectID'][:8]}"


def day_text(entry: dict) -> str:
    """Every call of the day in time order, as the reader sees it."""
    from analytics.discovery_audit_module.find import ist_hm
    parts = []
    for c in entry["calls"]:
        head = f"=== CALL at {ist_hm(c['start_utc'])} IST · {c.get('direction')} · {c.get('status')} · {c.get('duration') or 0} s"
        if c.get("status") != "Answered":
            parts.append(head + " · not answered ===")
        else:
            parts.append(head + " ===\n" + (c.get("transcript") or "(no transcript found)"))
    return "\n\n".join(parts)


def pack_item(entry: dict) -> dict:
    l = entry["lead"]
    callers = sorted({c.get("caller") or "" for c in entry["calls"] if c.get("status") == "Answered"} - {""})
    return {"lead_key": lead_key(entry), "calls_day": entry["calls_day"],
            "lead_name_in_crm": " ".join(x for x in (l.get("FirstName"), l.get("LastName")) if x) or None,
            "course_in_crm": l.get("mx_Enquired_Course"), "callers_in_crm": callers,
            "note": entry.get("note") or "", "transcript": day_text(entry)}


def instructions() -> str:
    return READER.read_text() + "\n\n---\n\n# THE AUDIT PROMPT (apply it as written)\n\n" + PROMPT.read_text()


def prepare(entries: list[dict], folder: str, per_pack: int = PER_PACK) -> list[str]:
    """Writes INSTRUCTIONS.md and the packs for every lead still to read. Returns the pack paths."""
    os.makedirs(f"{folder}/results", exist_ok=True)
    Path(f"{folder}/INSTRUCTIONS.md").write_text(instructions())
    todo = [e for e in entries if not finished(f"{folder}/results/{lead_key(e)}.json")]
    paths = []
    for i in range(0, len(todo), per_pack):
        p = f"{folder}/pack_{i // per_pack + 1:02d}.json"
        json.dump([pack_item(e) for e in todo[i:i + per_pack]], open(p, "w"), indent=1, ensure_ascii=False)
        paths.append(p)
    return paths


# ------------------------------------------------------------------ checking


def _sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finished(path: str) -> bool:
    ok = path[:-5] + ".ok"
    return os.path.exists(path) and os.path.exists(ok) and Path(ok).read_text().strip() == _sha(path)


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^\w']+", " ", s.lower()).strip()


def quotes_missing(result: dict, transcript: str) -> list[str]:
    """Quoted phrases (in straight double quotes) that are not word for word in the day's transcripts."""
    text = norm(transcript)
    fields = list(result.get("sheet", {}).values()) + [result.get("compliance_flags", "")] + \
        [v if isinstance(v, str) else " ".join(v) for v in result.get("pdf", {}).values()]
    return [q for f in fields for q in QUOTE.findall(f or "") if norm(q) not in text]


def problems(result: dict, transcript: str) -> list[str]:
    out = []
    sheet, pdf, s = result.get("sheet") or {}, result.get("pdf") or {}, result.get("subscores") or {}
    for c in READER_COLUMNS:
        v = sheet.get(c)
        if not isinstance(v, str) or not v.strip():
            out.append(f"sheet column missing or empty: {c}")
        elif "\n" in v:
            out.append(f"line break inside: {c}")
    extra = set(sheet) - set(READER_COLUMNS)
    if extra:
        out.append(f"sheet has columns the reader must not fill: {sorted(extra)}")
    for k, allowed in SUBSCORES.items():
        if s.get(k) not in allowed:
            out.append(f"subscore {k} must be one of {sorted(allowed)}")
    for k in ("objection_raised", "open_concern"):
        if not isinstance(s.get(k), bool):
            out.append(f"subscores.{k} must be true or false")
    if not isinstance(result.get("subscore_reasons"), dict) or set(result["subscore_reasons"]) != set(SUBSCORES):
        out.append(f"subscore_reasons needs one short reason for each of {sorted(SUBSCORES)}")
    if sheet.get("IIT Compliance") not in IIT:
        out.append(f"IIT Compliance must be one of {sorted(IIT)}")
    bp = result.get("burning_problem")
    if bp not in ("IDENTIFIED", "NOT IDENTIFIED"):
        out.append("burning_problem must be IDENTIFIED or NOT IDENTIFIED")
    elif (bp == "NOT IDENTIFIED") != (sheet.get("Primary Pain Identified") == "Not identified"):
        out.append('Primary Pain Identified is "Not identified" exactly when burning_problem is NOT IDENTIFIED')
    flags = result.get("compliance_flags")
    if flags != "NONE" and not all(COMPLIANCE.match(x.strip()) for x in str(flags or "").split(" | ")):
        out.append('compliance_flags must be NONE or "CRITICAL: <type> (<verbatim quote>)" items joined by " | "')
    elif flags != "NONE" and "CRITICAL" not in (sheet.get("Pitch Discipline") or ""):
        out.append("Pitch Discipline must name the CRITICAL claim when compliance_flags is not NONE")
    for c in ("Red Flags — Missed Discovery Signals", "Coaching Action (Caller-Specific)"):
        if len((sheet.get(c) or "").split(" | ")) > 3:
            out.append(f"{c}: at most 3 items")
    for f in PDF_FIELDS:
        v = pdf.get(f)
        if f in ("what_went_wrong", "improvements"):
            if not isinstance(v, list) or not 1 <= len(v) <= 5 or not all(isinstance(x, str) and x.strip() for x in v):
                out.append(f"pdf.{f} must be a list of 1-5 non-empty strings")
        elif not isinstance(v, str) or not v.strip():
            out.append(f"pdf.{f} missing or empty")
    tags = result.get("tags") or {}
    if set(tags.get("strengths") or []) - STRENGTHS or set(tags.get("gaps") or []) - GAPS:
        out.append(f"tags must come from the fixed lists (strengths {sorted(STRENGTHS)}; gaps {sorted(GAPS)})")
    missing = quotes_missing(result, transcript)
    if missing:
        out.append(f"quoted words not found word for word in the transcripts: {missing}")
    return out


def check(path: str, transcript: str) -> list[str]:
    """Validates one result file; writes its .ok mark when it passes."""
    try:
        result = json.load(open(path))
    except (OSError, ValueError) as e:
        return [f"not readable JSON: {e}"]
    out = problems(result, transcript)
    if not out:
        Path(path[:-5] + ".ok").write_text(_sha(path))
    return out
