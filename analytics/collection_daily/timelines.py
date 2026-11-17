"""One reading file per open lead for the graders: every call (with its Salesa transcript where one matched), every
LeadSquared comment (stage changes, disposition notes and call-back times, other activity notes), every Note from the
Notes tab and the lead's comment fields — never the pool sheet's comments (the user's rule, 12 Nov 2026). Also fetches
what those files need: the lead comment fields and the transcripts (within TranscriptClient's limits, saving after
every batch so a refusal loses nothing).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from analytics.bootcamp_collections import _call, _data, _fields, _note_text
from analytics.collection_scorecard import load
from integrations.leadsquared import LeadSquaredClient
from integrations.timeutil import IST, utc
from integrations.transcripts import TranscriptClient
from integrations.transcripts.client import Call, match_to_calls, normalize_phone

LEAD_FIELDS = ("mx_Last_Remark", "mx_Followup_comments")


def ist(t: datetime | None) -> str:
    return t.astimezone(IST).strftime("%a %d %b %H:%M") if t else "—"


def load_notes(run_dir: str) -> dict[str, list[dict]]:
    out = {}
    path = os.path.join(run_dir, "notes.jsonl")
    if os.path.exists(path):
        for line in open(path):
            d = json.loads(line)
            out[d["lead_id"]] = d["notes"]
    return out


def fetch_lead_fields(run_dir: str, dirs: list[str]) -> dict:
    """Last Remark and Follow-up comments for every lead of the run's tags (a few requests per tag). Read-only."""
    tags = sorted({l["mx_Bootcamp_collections"] for d in dirs for l in json.load(open(os.path.join(d, "tagged_leads.json")))})
    got, c = {}, LeadSquaredClient()
    for t in tags:
        for l in c.iter_leads("mx_Bootcamp_collections", t, page_size=1000, columns=["ProspectID", *LEAD_FIELDS]):
            v = {k: l.get(k) for k in LEAD_FIELDS if l.get(k)}
            if v:
                got[l["ProspectID"]] = v
    json.dump(got, open(os.path.join(run_dir, "lead_fields.json"), "w"), indent=1, ensure_ascii=False)
    return got


def fetch_transcripts(tx_dir: str, phones: list[str]) -> tuple[int, int]:
    """One run (at most 9 requests of 10 numbers) for numbers not fetched yet; returns (fetched, still to fetch)."""
    os.makedirs(tx_dir, exist_ok=True)
    need = sorted({n for n in (normalize_phone(p) for p in phones) if n})
    todo = [n for n in need if not os.path.exists(os.path.join(tx_dir, n.lstrip("+") + ".json"))]
    client, got = TranscriptClient(max_retries=0), 0
    for i in range(0, min(len(todo), 90), 10):
        chunk = todo[i:i + 10]
        try:
            raw = client.search_raw(chunk)
        except Exception:  # the service limits requests a minute account-wide; what is saved stays saved
            break
        for n in chunk:
            key = next((k for k in raw if normalize_phone(k) == n), n)
            json.dump(raw.get(key) or {}, open(os.path.join(tx_dir, n.lstrip("+") + ".json"), "w"))
        got += len(chunk)
    return got, len(todo) - got


def transcripts_for(phone: str, tx_dirs: list[str]) -> list[Call]:
    n = normalize_phone(phone)
    for d in tx_dirs:  # the first copy found: the run's own, then older runs' for earlier calls
        p = os.path.join(d, (n or "").lstrip("+") + ".json")
        if n and os.path.exists(p):
            g = json.load(open(p)) or {}
            return [Call.from_api(n, kind, r) for kind, key in (("sales", "sales_call"), ("support", "support_calls")) for r in g.get(key) or []]
    return []


def events(lead: dict, acts: list[dict], notes: list[dict], cut: datetime, tx_dirs: list[str]) -> list[tuple[datetime, str]]:
    api = [x for x in transcripts_for(lead["phone"], tx_dirs) if x.has_transcript]
    matched = match_to_calls(api, [{"activity_id": c["id"], "lead_number": lead["phone"], "t": c["t"], "duration": c["dur"]}
                                   for c in lead["calls"] if c["answered"]])
    ev = []
    for a in acts:
        t, code = utc(a.get("CreatedOn")), a.get("EventCode")
        if not t or t > cut:
            continue
        if code in (21, 22):
            c = _call(a)
            line = f"CALL {'OUT' if c['out'] else 'IN '} by {c['by'] or '?'} · {c['status']} · {c['dur']}s"
            if (m := matched.get(a.get("Id"))):
                line += f"\n    TRANSCRIPT (no speaker labels): {' '.join(m[0].transcript.split())[:6000]}"
            ev.append((t, line))
        elif code == 3002:
            d = _data(a)
            com = d.get("Comment") or ""
            ev.append((t, f"STAGE {d.get('PreviousStage')} -> {d.get('CurrentStage')} by {d.get('CreatedBy')}"
                       + (f" · comment: {com}" if com and not com.startswith('{"ActionType"') else "")))
        elif code == 3001:
            d = _data(a)
            ev.append((t, f"OWNER {d.get('PreviousOwner')} -> {d.get('CurrentOwner')}"))
        elif code == 103:
            f = _fields(a)
            due = utc(f.get("mx_Custom_1"))
            ev.append((t, f"DISPOSITION by {_data(a).get('CreatedByName')} · {f.get('Status')}"
                       + (f" · call-back set for {ist(due)}" if due else "") + f" · note: {_note_text(a)}"))
        elif (txt := _note_text(a)) and code != 97:  # 97: the disposition form itself, already read above
            ev.append((t, f"NOTE ({a.get('EventName')}) by {_data(a).get('CreatedByName') or ''}: {txt}"))
    for n in notes:
        t = utc(n["t"])
        if t and t <= cut:
            ev.append((t, f"NOTE (Notes tab) by {n['by']}: {n['text']}"))
    return sorted(ev, key=lambda e: e[0])


def header(l: dict, cut: datetime, fields: dict) -> str:
    fu = "; ".join(f"due {ist(f['due'])} ({'kept' if f['kept'] else 'not kept' if f['kept'] is False else 'not due yet'})"
                   for f in l["follow_ups"]) or "none saved"
    return (f"LEAD {l['lead_id']} · number {l['phone']} · {l['bootcamp']} · D{l['day']}\n"
            f"caller {l['caller']} (TL {l['tl']}, AD {l['ad']}) · allocated {ist(l['allocated'])} · 48-hour deadline {ist(l['deadline'])} · "
            f"LSQ stage at cut {l['stage'] or l['stage_now']}\n"
            f"facts at cut {cut.astimezone(IST):%Y-%m-%d %H:%M}: dials {l['dials']} on {l['dial_days']} days · real talks (answered 120s+) "
            f"{l['real_talks']} · first pick-up {ist(l['first_pickup'])} · first real talk {ist(l['first_real'])} · last real talk {ist(l['last_real'])}\n"
            f"call-backs saved in LSQ: {fu} · lead's calls not returned: {len(l['unreturned_calls'])}\n"
            + (f"LEAD COMMENT FIELDS (LSQ): {fields}\n" if fields else ""))


def write(run_dir: str, leads: list[dict], cut: datetime, tx_dirs: list[str], since: datetime | None = None) -> list[dict]:
    """Timelines for the open leads (only those with new activity since ``since``, if given); returns the index."""
    out_dir = os.path.join(run_dir, "tl")
    os.makedirs(out_dir, exist_ok=True)
    notes = load_notes(run_dir)
    fields = json.load(open(os.path.join(run_dir, "lead_fields.json"))) if os.path.exists(os.path.join(run_dir, "lead_fields.json")) else {}
    hists = {}
    index = []
    for l in leads:
        if l["enrolled"] or l["dropped"]:
            continue
        if l["data_dir"] not in hists:
            hists[l["data_dir"]] = load(l["data_dir"])[1]
        start = datetime.strptime(l["bootcamp_day"], "%Y-%m-%d").replace(tzinfo=IST) - timedelta(days=3)
        acts = [a for a in hists[l["data_dir"]].get(l["lead_id"]) or [] if utc(a.get("CreatedOn")) and utc(a["CreatedOn"]) >= start]
        ev = events(l, acts, notes.get(l["lead_id"], []), cut, tx_dirs)
        if since and not any(t > since for t, _ in ev):
            continue
        path = os.path.join(out_dir, l["lead_id"] + ".txt")
        open(path, "w").write(header(l, cut, fields.get(l["lead_id"])) + "\n" + "\n".join(f"{ist(t)} IST  {x}" for t, x in ev) + "\n")
        index.append({"lead_id": l["lead_id"], "side": l["side"], "real_talks": l["real_talks"], "file": path,
                      "transcripts": sum(1 for _, x in ev if "TRANSCRIPT" in x)})
    json.dump(index, open(os.path.join(out_dir, "index.json"), "w"), indent=1)
    return index
