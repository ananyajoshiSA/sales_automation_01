"""Who actually assigned, transferred or followed up each lead, kept apart from whose login or pool it sat in.

Rules (user, 9 Oct 2026; docs/accountability.md): a lead sitting in an admin's account, or a change made
through a shared admin login, never makes that admin accountable. Each action goes to the person the
evidence names, or is marked Unverified.

Evidence, strongest first:

1. A manual correction a team leader confirmed (``--corrections``): status ``Corrected``.
2. The CRM's own record of the action: the owner-change log (LeadAssigned: from, to, and the login that made
   it), stage changes and call records. A personal login is the person: ``Verified``.
3. A shared login (``SHARED_ACCOUNTS``, e.g. Rinku Jhala's admin account) is never the person. The lead's
   "Assigned By" field names them only when it can be tied to this change: it is the lead's latest owner
   change, "Assigned On" was stamped at or after it, and the named person made no earlier change on the
   lead. Otherwise the field probably predates the change (on 32 of 33 checked leads, 9 Oct, it named an
   earlier assigner), so the action is ``Unverified`` and the field's name is shown as a lead to check.
4. ``System`` is LeadSquared automation, not a person: ``Automated``.

A missed follow-up belongs to whoever owned the lead when it was due; if a shared account held it then,
it is Unverified, with the last caller before the due time shown as a lead to check. Admin oversight is
reported only from explicitly listed supervisors (``--supervisors``).

Every attribution, and every later change to one, is appended to an audit log that is never rewritten.

    python -m analytics.accountability LEADS.json HISTORIES.jsonl USERS.json FROM TO OUT_DIR \
        [--shared "Rinku Jhala,Admin"] [--corrections FILE.csv] [--supervisors FILE.json] [--audit FILE.jsonl]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from integrations.leadsquared import parse_activity_note
from integrations.timeutil import IST, ist_day_start, now_utc, utc

SHARED_ACCOUNTS = ("Rinku Jhala", "Admin")   # logins more than one person uses
AUTOMATION = ("System",)
FOLLOW_UP_WINDOW = timedelta(hours=2)        # GOAL.md L6: a dial within 2 h of the due time
ASSIGNED_ON_SLACK = timedelta(minutes=5)

VERIFIED, ASSIGNED_BY, UNVERIFIED, AUTOMATED, CORRECTED = (
    "Verified", "Verified (Assigned By)", "Unverified", "Automated", "Corrected")

COLUMNS = ["key", "lead_id", "action", "action_time_ist", "account_owner", "login_used", "assigned_by",
           "assigned_to", "transferred_by", "action_performed_by", "current_responsible_person",
           "accountability_status", "evidence", "possible_actor", "assigned_by_field", "oversight"]


def _data(a: dict) -> dict:
    return {d.get("Key"): d.get("Value") for d in a.get("Data") or []}


def _ist(t: datetime | None) -> str:
    return t.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t else ""


class People:
    """Which names are shared logins, automation, or one person."""

    def __init__(self, users: list[dict], shared=SHARED_ACCOUNTS, automation=AUTOMATION):
        self.shared = {s.strip() for s in shared if s.strip()}
        self.automation = set(automation)
        self.known = {f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".strip() for u in users} - {""}

    def kind(self, name: str | None) -> str:
        name = (name or "").strip()
        if not name:
            return "blank"
        if name in self.automation:
            return "automation"
        if name in self.shared:
            return "shared"
        return "person"

    def is_person(self, name: str | None) -> bool:
        return self.kind(name) == "person"


def _call(a: dict) -> dict:
    note = parse_activity_note((a.get("ActivityFields") or {}).get("ActivityEvent_Note"))
    d = _data(a)
    inbound = a.get("EventCode") == 21
    who = note.get("Reciever") or d.get("Receiver") if inbound else note.get("Caller") or d.get("Caller")
    return {"id": a.get("Id"), "t": utc(a.get("CreatedOn")), "inbound": inbound, "by": (who or "").strip(),
            "status": note.get("Status") or (a.get("ActivityFields") or {}).get("Status") or ""}


def owner_at(assigns: list[tuple[datetime, dict]], t: datetime, current: str) -> str:
    """Who owned the lead at ``t``, from its owner-change log (sorted); the current owner if none applies."""
    before = [d.get("CurrentOwner") for at, d in assigns if at <= t]
    if before:
        return before[-1] or ""
    after = [d.get("PreviousOwner") for at, d in assigns if at > t]
    return (after[0] if after else current) or ""


def resolve_assignment(t: datetime, d: dict, assigns: list[tuple[datetime, dict]], lead: dict,
                       people: People) -> tuple[str, str, str, str]:
    """(performed_by, status, evidence, possible_actor) for one owner change."""
    login = (d.get("CreatedBy") or "").strip()
    kind = people.kind(login)
    if kind == "person":
        return login, VERIFIED, f"owner-change log: made from {login}'s own login", ""
    if kind == "automation":
        return "System (automation)", AUTOMATED, "owner-change log: made by a LeadSquared automation rule", ""
    field = (lead.get("mx_Assigned_By") or "").strip()
    how = f"made from {login}'s shared login" if login else "the log does not say who made it"
    if not people.is_person(field):
        why = "Assigned By is blank" if not field else f"Assigned By says {field}, which is not one person"
        return "", UNVERIFIED, f"owner-change log: {how}; {why}", ""
    latest = assigns[-1][0] == t
    stamped = utc(lead.get("mx_Assigned_On"))
    earlier = next((at for at, x in assigns if at < t and (x.get("CreatedBy") or "").strip() == field), None)
    if latest and stamped and stamped >= t - ASSIGNED_ON_SLACK and earlier is None:
        return field, ASSIGNED_BY, f"owner-change log: {how}; Assigned By names {field}", ""
    if not latest:
        why = "a later owner change has happened since, so Assigned By may describe that one"
    elif earlier is not None:
        why = f"{field} also changed this lead's owner on {_ist(earlier)}, so Assigned By may predate this change"
    else:
        why = "Assigned On is older than this change, so Assigned By predates it"
    return "", UNVERIFIED, f"owner-change log: {how}; Assigned By says {field}, but {why}", field


def lead_actions(lead: dict, acts: list[dict], people: People, d0: datetime, d1: datetime,
                 now: datetime) -> list[dict]:
    """Every accountable action on one lead between ``d0`` and ``d1`` (UTC-aware), as COLUMNS rows."""
    lid = lead["ProspectID"]
    current = (lead.get("OwnerIdName") or "").strip()
    assigns = sorted(((utc(a.get("CreatedOn")), {**_data(a), "_id": a.get("Id")}) for a in acts
                      if a.get("EventName") == "LeadAssigned" and utc(a.get("CreatedOn"))), key=lambda x: x[0])
    calls = sorted((c for c in (_call(a) for a in acts if a.get("EventCode") in (21, 22)) if c["t"]),
                   key=lambda c: c["t"])
    responsible_now = current if people.is_person(current) else f"No one (lead is in {current or 'no'} account)"
    field = (lead.get("mx_Assigned_By") or "").strip()
    rows = []

    def row(key, action, t, **kw):
        r = {c: "" for c in COLUMNS}
        r.update(key=key, lead_id=lid, action=action, action_time_ist=_ist(t), assigned_by_field=field,
                 current_responsible_person=responsible_now, **kw)
        rows.append(r)

    created = utc(lead.get("CreatedOn"))
    if created and d0 <= created < d1:
        creator = (lead.get("CreatedByName") or "").strip()
        kind = people.kind(creator)
        first_owner = owner_at(assigns, created, current)
        row(f"created:{lid}", "lead created", created, account_owner=first_owner, login_used=creator,
            assigned_to=first_owner,
            assigned_by=creator if kind == "person" else "", action_performed_by=creator if kind == "person" else
            "System (automation)" if kind == "automation" else "Unverified",
            accountability_status={"person": VERIFIED, "automation": AUTOMATED}.get(kind, UNVERIFIED),
            evidence=f"lead record: created from {creator}'s shared login" if kind == "shared"
            else f"lead record: created by {creator or 'unknown'}")

    for t, d in assigns:
        if not d0 <= t < d1:
            continue
        prev, to = (d.get("PreviousOwner") or "").strip(), (d.get("CurrentOwner") or "").strip()
        who, status, evidence, possible = resolve_assignment(t, d, assigns, lead, people)
        if people.kind(to) == "shared":
            action = "moved into shared account"
        elif people.is_person(prev):
            action = "transfer"
        else:
            action = "assignment"
        row(f"assign:{d['_id'] or f'{lid}:{t:%Y%m%d%H%M%S}'}", action, t, account_owner=to, login_used=d.get("CreatedBy") or "",
            assigned_by=who if action == "assignment" else "", assigned_to=to,
            transferred_by=who if action != "assignment" else "", action_performed_by=who or "Unverified",
            accountability_status=status, evidence=evidence, possible_actor=possible)

    for a in acts:
        t = utc(a.get("CreatedOn"))
        if a.get("EventName") != "StageChange" or not t or not d0 <= t < d1:
            continue
        d = _data(a)
        login = (d.get("CreatedBy") or "").strip()
        kind = people.kind(login)
        status = {"person": VERIFIED, "automation": AUTOMATED}.get(kind, UNVERIFIED)
        who = login if kind == "person" else "System (automation)" if kind == "automation" else "Unverified"
        row(f"stage:{a.get('Id')}", f"stage change to {d.get('CurrentStage') or '?'}", t,
            account_owner=owner_at(assigns, t, current), login_used=login, action_performed_by=who,
            accountability_status=status,
            evidence="stage log: " + (f"made from {login}'s shared login" if kind == "shared" else f"made by {login or 'unknown'}"))

    for c in calls:
        if not d0 <= c["t"] < d1 or people.kind(c["by"]) != "shared":
            continue
        last = next((x["by"] for x in reversed(calls) if x["t"] < c["t"] and people.is_person(x["by"])), "")
        if c["inbound"]:
            action = "inbound call answered" if c["status"] == "Answered" else "inbound call missed"
            evidence = f"call log: the lead's call rang {c['by']}'s account because the lead sat there"
        else:
            action, evidence = "outbound call", f"call log: dialled from {c['by']}'s shared login"
        row(f"call:{c['id']}", action, c["t"], account_owner=owner_at(assigns, c["t"], current), login_used=c["by"],
            action_performed_by="Unverified", accountability_status=UNVERIFIED, evidence=evidence,
            possible_actor=last)

    due = utc(lead.get("mx_Next_follow_up_date")) or utc(lead.get("mx_Follow_up_date_and_time"))
    if due and d0 <= due < d1 and due + FOLLOW_UP_WINDOW <= now:
        owner = owner_at(assigns, due, current)
        dials = [c for c in calls if not c["inbound"] and abs(c["t"] - due) <= FOLLOW_UP_WINDOW]
        by_owner = [c for c in dials if c["by"] == owner]
        done = "done" if by_owner else f"done by {dials[0]['by']}" if dials else "missed"
        if people.is_person(owner):
            who, status, possible = owner, VERIFIED, ""
            evidence = f"{owner} owned the lead when the follow-up was due"
        else:
            who, status = "Unverified", UNVERIFIED
            possible = next((c["by"] for c in reversed(calls) if c["t"] <= due and people.is_person(c["by"])), "")
            evidence = f"the lead sat in {owner or 'no'} account when the follow-up was due"
        row(f"followup:{lid}:{due:%Y%m%d%H%M}", f"follow-up {done}", due, account_owner=owner,
            action_performed_by=who, accountability_status=status, evidence=evidence, possible_actor=possible)
    return rows


LEAD_COLUMNS = ["lead_id", "account_owner", "arrived_ist", "how", "login_used", "put_there_by", "accountability_status",
                "evidence", "possible_actor", "assigned_by_field", "assigned_on_ist", "current_responsible_person"]


def lead_origin(lead: dict, acts: list[dict], people: People) -> dict:
    """Who put the lead in the account it sits in now: its last owner change into it, else its creation."""
    current = (lead.get("OwnerIdName") or "").strip()
    assigns = sorted(((utc(a.get("CreatedOn")), {**_data(a), "_id": a.get("Id")}) for a in acts
                      if a.get("EventName") == "LeadAssigned" and utc(a.get("CreatedOn"))), key=lambda x: x[0])
    into = [(t, d) for t, d in assigns if (d.get("CurrentOwner") or "").strip() == current]
    r = {"lead_id": lead["ProspectID"], "account_owner": current, "assigned_by_field": lead.get("mx_Assigned_By") or "",
         "assigned_on_ist": _ist(utc(lead.get("mx_Assigned_On"))),
         "current_responsible_person": current if people.is_person(current) else f"No one (lead is in {current or 'no'} account)"}
    if into:
        t, d = into[-1]
        who, status, evidence, possible = resolve_assignment(t, d, assigns, lead, people)
        r.update(arrived_ist=_ist(t), how="owner change", login_used=d.get("CreatedBy") or "",
                 put_there_by=who or "Unverified", accountability_status=status, evidence=evidence, possible_actor=possible)
    else:
        creator = (lead.get("CreatedByName") or "").strip()
        kind = people.kind(creator)
        r.update(arrived_ist=_ist(utc(lead.get("CreatedOn"))), how="created there", login_used=creator,
                 put_there_by=creator if kind == "person" else "System (automation)" if kind == "automation" else "Unverified",
                 accountability_status={"person": VERIFIED, "automation": AUTOMATED}.get(kind, UNVERIFIED),
                 evidence=f"lead record: created by {creator or 'unknown'}" + (" (shared login)" if kind == "shared" else ""),
                 possible_actor="")
    return r


def apply_corrections(rows: list[dict], corrections: list[dict]) -> None:
    """A team leader's confirmed answer for one action key wins over the evidence above."""
    fix = {c["key"].strip(): c for c in corrections if (c.get("key") or "").strip() and (c.get("performed_by") or "").strip()}
    for r in rows:
        c = fix.get(r["key"])
        if not c:
            continue
        who = c["performed_by"].strip()
        r["action_performed_by"] = who
        for col in ("assigned_by", "transferred_by"):
            if r[col] or (col == "assigned_by" and r["action"] == "assignment") or (col == "transferred_by" and r["action"] in ("transfer", "moved into shared account")):
                r[col] = who
        r["accountability_status"] = CORRECTED
        r["evidence"] = f"confirmed by {c.get('confirmed_by') or 'unknown'}: {c.get('note') or ''}".strip(": ") + f" (was: {r['evidence']})"


def apply_oversight(rows: list[dict], supervisors: dict[str, str]) -> None:
    """Only explicitly listed supervisors: person (or shared account) -> who oversees them."""
    for r in rows:
        who = r["action_performed_by"] if r["accountability_status"] != UNVERIFIED else r["account_owner"]
        r["oversight"] = supervisors.get(who, "")


def append_audit(path: str, rows: list[dict], now: datetime) -> Counter:
    """Append-only: first sight of each action, and every later change to who is accountable."""
    last: dict[str, dict] = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            last[e["key"]] = e
    changes = Counter()
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            prev = last.get(r["key"])
            same = prev and (prev["performed_by"], prev["status"]) == (r["action_performed_by"], r["accountability_status"])
            if same:
                continue
            e = {"recorded_ist": _ist(now), "key": r["key"], "lead_id": r["lead_id"], "action": r["action"],
                 "action_time_ist": r["action_time_ist"], "performed_by": r["action_performed_by"],
                 "status": r["accountability_status"], "evidence": r["evidence"],
                 "change": "first seen" if not prev else "re-attributed"}
            if prev:
                e["previous"] = {"performed_by": prev["performed_by"], "status": prev["status"],
                                 "recorded_ist": prev["recorded_ist"]}
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            changes[e["change"]] += 1
    return changes


def summarise(rows: list[dict]) -> dict:
    by_status = Counter(r["accountability_status"] for r in rows)
    by_action = Counter(r["action"].split(" to ")[0] for r in rows)
    people = defaultdict(Counter)
    for r in rows:
        if r["accountability_status"] in (VERIFIED, ASSIGNED_BY, CORRECTED):
            people[r["action_performed_by"]][r["action"].split(" to ")[0]] += 1
    unverified = defaultdict(Counter)
    for r in rows:
        if r["accountability_status"] == UNVERIFIED:
            unverified[r["login_used"] or r["account_owner"] or "(unknown)"][r["action"].split(" to ")[0]] += 1
    leads_ = Counter((r["possible_actor"]) for r in rows if r["accountability_status"] == UNVERIFIED and r["possible_actor"])
    return {"actions": len(rows), "by_status": dict(by_status), "by_action": dict(by_action),
            "people": {p: dict(c) for p, c in sorted(people.items(), key=lambda x: -sum(x[1].values()))},
            "unverified_by_account": {k: dict(c) for k, c in unverified.items()},
            "possible_actors_to_check": dict(leads_.most_common())}


def write_summary(path: str, s: dict, d0: str, d1: str, n_leads: int, fresh: str) -> None:
    lines = [f"# Who did what, {d0} to {d1} (IST)", "",
             f"Data read {fresh} IST. {n_leads} leads, {s['actions']} actions.", ""]
    st = s["by_status"]
    named = st.get(VERIFIED, 0) + st.get(ASSIGNED_BY, 0) + st.get(CORRECTED, 0)
    lines += [f"- Named person: {named} ({st.get(VERIFIED, 0)} from their own login, {st.get(ASSIGNED_BY, 0)} from Assigned By, "
              f"{st.get(CORRECTED, 0)} confirmed by a team leader)",
              f"- Automation: {st.get(AUTOMATED, 0)}", f"- Unverified: {st.get(UNVERIFIED, 0)}", "",
              "## By person", "", "| Person | Actions |", "|---|---|"]
    lines += [f"| {p} | {', '.join(f'{k} {v}' for k, v in c.items())} |" for p, c in s["people"].items()]
    for acc, c in s.get("put_in_account_by", {}).items():
        lines += ["", f"## Who put the leads into {acc}'s account", "", "| Done by | Leads |", "|---|---|"]
        lines += [f"| {p} | {n} |" for p, n in c.items()]
    lines += ["", "## Unverified, by the account they went through", "", "| Account | Actions |", "|---|---|"]
    lines += [f"| {a} | {', '.join(f'{k} {v}' for k, v in c.items())} |" for a, c in s["unverified_by_account"].items()]
    if s["possible_actors_to_check"]:
        lines += ["", "Names to check with the team (from a stale Assigned By or the last caller; not proof): "
                  + ", ".join(f"{p} ({n})" for p, n in s["possible_actors_to_check"].items())]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def run(leads: list[dict], histories: dict[str, list[dict]], users: list[dict], d0: str, d1: str, out_dir: str,
        shared=SHARED_ACCOUNTS, corrections=(), supervisors=None, audit=None, now: datetime | None = None) -> dict:
    now = now or now_utc()
    start, end = ist_day_start(d0), ist_day_start(d1) + timedelta(days=1)
    people = People(users, shared)
    rows = [r for l in leads if l["ProspectID"] in histories
            for r in lead_actions(l, histories[l["ProspectID"]], people, start, end, now)]
    apply_corrections(rows, list(corrections))
    apply_oversight(rows, supervisors or {})
    rows.sort(key=lambda r: (r["action_time_ist"], r["lead_id"]))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "actions.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    origins = [lead_origin(l, histories[l["ProspectID"]], people) for l in leads if l["ProspectID"] in histories]
    with open(os.path.join(out_dir, "leads.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEAD_COLUMNS)
        w.writeheader()
        w.writerows(sorted(origins, key=lambda r: r["arrived_ist"]))
    s = summarise(rows)
    s["put_in_account_by"] = {acc: dict(Counter(o["put_there_by"] if o["accountability_status"] != UNVERIFIED
                                                 else f"Unverified ({o['login_used'] or 'no'} login)" for o in origins
                                                 if o["account_owner"] == acc).most_common())
                              for acc in sorted({o["account_owner"] for o in origins if people.kind(o["account_owner"]) == "shared"})}
    s["audit"] = dict(append_audit(audit or os.path.join(out_dir, "audit.jsonl"), rows, now))
    write_summary(os.path.join(out_dir, "summary.md"), s, d0, d1, len(leads), _ist(now))
    json.dump(s, open(os.path.join(out_dir, "summary.json"), "w"), indent=1, ensure_ascii=False)
    return s


def load_histories(path: str) -> dict[str, list[dict]]:
    out = {}
    for line in open(path, encoding="utf-8"):
        d = json.loads(line)
        if d.get("activities") is not None and not d.get("error"):
            out[d["lead_id"]] = d["activities"]
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for a in ("leads", "histories", "users", "start", "end", "out_dir"):
        p.add_argument(a)
    p.add_argument("--shared", default=",".join(SHARED_ACCOUNTS))
    p.add_argument("--corrections")
    p.add_argument("--supervisors")
    p.add_argument("--audit")
    a = p.parse_args(argv)
    corrections = list(csv.DictReader(open(a.corrections, encoding="utf-8"))) if a.corrections else []
    s = run(json.load(open(a.leads)), load_histories(a.histories), json.load(open(a.users)), a.start, a.end, a.out_dir,
            shared=a.shared.split(","), corrections=corrections,
            supervisors=json.load(open(a.supervisors)) if a.supervisors else None, audit=a.audit)
    print(json.dumps(s, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
