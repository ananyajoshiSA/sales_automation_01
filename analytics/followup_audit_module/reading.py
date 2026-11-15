"""Claude reads every lead's follow-up calls (no model API, no keyword rules) and writes one result file per lead.

Same shape, checks and scoring as the discovery audit (``analytics.discovery_audit_module.reading``): the audit prompt
is Final_Prompt_Oct_2026 unchanged; the follow-up context goes into its CUSTOM INSTRUCTIONS slot, written per lead by
code (``custom_instructions``). One more field: ``not_a_conversation`` lists the scored calls Claude judged voicemail,
IVR, a recorded message or one-sided; they never count as the lead's real conversation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from analytics.discovery_audit_module import reading as dr
from analytics.followup_audit_module.find import STAGE, real
from integrations.timeutil import IST, utc

PROMPT = dr.PROMPT
READER = Path(__file__).with_name("prompts") / "reader.md"
PER_PACK = 4

lead_key, totals, relaxed_label, strict_label, pdf_band = dr.lead_key, dr.totals, dr.relaxed_label, dr.strict_label, dr.pdf_band
finished, SHEET_COLUMNS, IIT = dr.finished, dr.SHEET_COLUMNS, dr.IIT


def ist(s: str | None, fmt: str = "%d-%m-%Y %H:%M") -> str:
    t = utc(s)
    return t.astimezone(IST).strftime(fmt) if t else ""


def label(c: dict) -> str:
    """How a call is named in the text and in ``not_a_conversation``: "28-10 10:38"."""
    return ist(c["start_utc"], "%d-%m %H:%M")


def _call(c: dict) -> str:
    head = (f"=== CALL {label(c)} IST · {c.get('direction')} · {c.get('duration') or 0} s · "
            f"{c.get('caller') or 'no caller logged'} ===")
    return head + "\n" + (c.get("transcript") or "(no transcript found)")


START = {"discovery": "the move to Discovery Call Done on {dcd} IST",
         "previous_follow_up": "the lead's previous move into Follow Up For Closure on {pfu} IST",
         "30_days": "30 days before the move (the audit never reaches further back)"}


def start_text(entry: dict) -> str:
    return START[entry["start_reason"]].format(dcd=ist(entry.get("dcd_utc")), pfu=ist(entry.get("prev_follow_up_utc")))


def lead_text(entry: dict) -> str:
    """Background calls, then the scored calls, as the reader sees them (and as quotes are checked against)."""
    parts = []
    if entry.get("background"):
        parts.append(f"##### BACKGROUND, NOT SCORED: the discovery call(s) on {ist(entry['dcd_utc'], '%d-%m-%Y')} before the move "
                     f"to Discovery Call Done at {ist(entry['dcd_utc'], '%H:%M')} IST #####")
        parts += [_call(c) for c in entry["background"]]
    parts.append(f"##### SCORED CALLS: from {start_text(entry)} up to the move to {STAGE} on {ist(entry['moved_utc'])} IST #####")
    parts += [_call(c) for c in entry["calls"]]
    nr = entry.get("not_read") or {}
    if nr.get("unanswered") or nr.get("short"):
        parts.append(f"({nr.get('unanswered', 0)} unanswered and {nr.get('short', 0)} under-30-second calls in this window are not shown)")
    return "\n\n".join(parts)


def calls_word(n: int) -> str:
    return f"{n} call" if n == 1 else f"{n} calls"


def custom_instructions(entry: dict) -> str:
    n = len(entry["calls"])
    bg = (f" The {calls_word(len(entry['background']))} under BACKGROUND are the discovery call: use them only to understand the lead "
          "(profile, pain, goals, what was promised); never score them, flag them or coach on them.") if entry.get("background") else ""
    return (f"These are {calls_word(n).replace('call', 'follow-up call')} with this lead, from {start_text(entry)} up to the move to {STAGE} on "
            f"{ist(entry['moved_utc'])} IST, read together as one conversation in time order.{bg} Judge the lead as it stood at "
            f"the end of the last scored call; the next 48 hours count from the move. Discovery-focused cells describe what these "
            f"calls show (write \"Not discussed\" where they do not cover it). Any scored call that is voicemail, an IVR menu, a "
            f"recorded message or one-sided goes in not_a_conversation and is not judged.")


def pack_item(entry: dict) -> dict:
    l = entry["lead"]
    names = sorted({c.get("caller") or "" for c in entry["calls"]} - {""})
    return {"lead_key": lead_key(entry), "moved_on": entry["calls_day"],
            "lead_name_in_crm": " ".join(x for x in (l.get("FirstName"), l.get("LastName")) if x) or None,
            "course_in_crm": l.get("mx_Enquired_Course"), "callers_in_crm": names,
            "scored_call_labels": [label(c) for c in entry["calls"]],
            "custom_instructions": custom_instructions(entry), "note": entry.get("note") or "", "transcript": lead_text(entry)}


def instructions() -> str:
    return READER.read_text() + "\n\n---\n\n# THE AUDIT PROMPT (apply it as written)\n\n" + PROMPT.read_text()


def prepare(entries: list[dict], folder: str, per_pack: int = PER_PACK) -> list[str]:
    """Writes INSTRUCTIONS.md and the packs for every lead still to read. Returns the pack paths."""
    os.makedirs(f"{folder}/results", exist_ok=True)
    Path(f"{folder}/INSTRUCTIONS.md").write_text(instructions())
    todo = [e for e in entries if not finished(f"{folder}/results/{lead_key(e)}.json", lead_text(e))]
    start = max((int(p.stem[5:]) for p in Path(folder).glob("pack_*.json")), default=0)
    paths = []
    for i in range(0, len(todo), per_pack):
        p = f"{folder}/pack_{start + i // per_pack + 1:02d}.json"
        json.dump([pack_item(e) for e in todo[i:i + per_pack]], open(p, "w"), indent=1, ensure_ascii=False)
        paths.append(p)
    return paths


def problems(result: dict, entry: dict) -> list[str]:
    out = dr.problems(result, lead_text(entry))
    nac = result.get("not_a_conversation")
    labels = {label(c) for c in entry["calls"]}
    if not isinstance(nac, list) or set(nac) - labels:
        out.append(f"not_a_conversation must be a list of scored call labels (from {sorted(labels)}), empty if all were conversations")
    return out


def check(path: str, entry: dict) -> list[str]:
    """Validates one result file; writes its .ok mark when it passes."""
    try:
        result = json.load(open(path))
    except (OSError, ValueError) as e:
        return [f"not readable JSON: {e}"]
    out = problems(result, entry)
    if not out:
        Path(path[:-5] + ".ok").write_text(dr._mark(path, lead_text(entry)))
    return out


def real_left(entry: dict, result: dict) -> list[dict]:
    """The real conversations (answered, 2+ min) left once Claude's not-a-conversation calls are taken out."""
    nac = set(result.get("not_a_conversation") or [])
    return [c for c in entry["calls"] if real(c) and label(c) not in nac]

