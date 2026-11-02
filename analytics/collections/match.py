"""Join the sheets to LeadSquared with no manual step: students by phone then email, bootcamps to their LeadSquared
tag, communities to a type. Anything the code cannot place with confidence is kept and labelled ("Not matched in
LeadSquared", "Not paired", "Other"), never guessed, and its amounts stay in every total.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from analytics.collections.sheets import email_key, phone10
from analytics.collections.tags import tag_course, tag_date, tag_kind

NOT_MATCHED = "Not matched in LeadSquared"
NOT_PAIRED = "Not paired"
OTHER = "Other"
COLLECTED_STAGES = {"collections done", "course enrolled"}
LOST_STAGES = {"not interested", "invalid", "invalid lead", "irrelevant lead", "invalid number", "duplicate", "drop"}
BOOKING_STAGES = {"booking fees received"}
# Community families: the first key found in the cleaned name wins.
COMMUNITY_FAMILIES = (
    ("independent director", "Independent Director"), ("contract dra", "Contract Drafting"),
    ("criminal", "Criminal Litigation"), ("ai for legal", "Legal AI"), ("legal ai", "Legal AI"),
    ("women", "AI for Women"), ("sqe", "SQE"), ("arbitration", "Arbitration"), ("m&a", "M&A"),
    ("clerkship", "Supreme Court Clerkship"), ("judiciary", "Judiciary"), ("ibps", "IBPS"), ("ugc", "UGC NET"),
    ("law firm", "Law Firm"),
)


def lead_name(l: dict) -> str:
    return " ".join(x for x in (l.get("FirstName"), l.get("LastName")) if x).strip()


class LeadIndex:
    """LeadSquared tagged leads, looked up by phone (Phone or Alternate Number) and by email."""

    def __init__(self, tagged: list[tuple[str, dict]]):
        self.by_phone: dict[str, list[dict]] = defaultdict(list)
        self.by_email: dict[str, list[dict]] = defaultdict(list)
        self.leads: dict[str, dict] = {}
        for tag, l in tagged:
            l = {**l, "_tag": tag, "_kind": tag_kind(tag)}
            self.leads[l["ProspectID"]] = l
            for p in {phone10(l.get("Phone")), phone10(l.get("Mobile"))} - {""}:
                self.by_phone[p].append(l)
            if e := email_key(l.get("EmailAddress")):
                self.by_email[e].append(l)

    def find(self, phone: str, email: str, kind: str) -> tuple[dict | None, str]:
        """(lead, how) for one student; prefers a lead tagged for the same kind of collection when a number is shared."""
        for key, index, how in ((phone, self.by_phone, "phone"), (email, self.by_email, "email")):
            hits = (index.get(key) or []) if key else []
            same = [l for l in hits if l["_kind"] == kind] or hits
            if len({l["ProspectID"] for l in same}) == 1:
                return same[0], how
            if len(same) > 1:
                return None, f"{how} shared by {len(same)} leads"
        return None, "no lead with this phone or email"


def match_students(rows: list[dict], index: LeadIndex, kind: str) -> None:
    """Adds the LeadSquared lead (or why there is none) to each sheet row, in place."""
    for r in rows:
        lead, how = index.find(r["phone"], r["email"], kind)
        r["lsq_id"] = lead["ProspectID"] if lead else ""
        r["matched_by"] = how if lead else ""
        r["match_note"] = "" if lead else how
        r["lsq_owner"] = (lead or {}).get("OwnerIdName") or ""
        r["lsq_owner_id"] = (lead or {}).get("OwnerId") or ""
        r["lsq_stage"] = (lead or {}).get("ProspectStage") or ""
        r["lsq_tag"] = (lead or {}).get("_tag") or ""
        r["lsq_funnel"] = (lead or {}).get("mx_Funnel_name") or ""


def community_type(raw: str) -> str:
    """"Contract Drafting" from "57/CONTRACT/DRAFTING", "Independent Director" from "22 – Independent Director
    Community"; a name outside the known families is cleaned and kept; nothing left is Other."""
    s = re.sub(r"^\s*[:\-–]?\s*\d+\s*[/–\-:.]*\s*", "", str(raw or ""))
    s = re.sub(r"[/_]+", " ", s)
    s = re.sub(r"\((.*?)\)|\bweek\s*\d+\b|\bcommun\w*\b|\bcomm\b|\bgroup\b.*$", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -–:,").lower()
    if not s or not re.search(r"[a-z]", s):
        return OTHER
    for key, family in COMMUNITY_FAMILIES:
        if key in s:
            return family
    return s.title()


def assign_community(rows: list[dict]) -> None:
    """Type from LeadSquared's Funnel name (the agreed source); else from the sheet's Community Name; else Other."""
    for r in rows:
        from_lsq = community_type(r.get("lsq_funnel"))
        from_sheet = community_type(r.get("community_raw"))
        if from_lsq != OTHER:
            r["community_type"], r["community_from"] = from_lsq, "LeadSquared Funnel name"
        elif from_sheet != OTHER:
            r["community_type"], r["community_from"] = from_sheet, "sheet Community Name"
        else:
            r["community_type"], r["community_from"] = OTHER, ""
        r["community_differs"] = from_lsq != OTHER and from_sheet != OTHER and from_lsq != from_sheet


def _words(s: str) -> set[str]:
    stop = {"bootcamp", "collection", "collections", "the", "and", "for", "of", "a", "in", "re", "2025", "2026"}
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) > 1 and w not in stop}


def pair_bootcamps(rows: list[dict], tags: list[str]) -> dict[tuple[str, str], dict]:
    """Each sheet bootcamp (name, start date) to one LeadSquared bootcamp tag.

    First by the students themselves: the tag most of its matched students carry, if more than half do. Otherwise by
    date and name: the one tag with the same bootcamp date whose course words overlap the name best. No clear
    winner means "Not paired".
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["bootcamp_raw"] or "(no bootcamp name)", r.get("start_date") or "")].append(r)
    by_date: dict[str, list[str]] = defaultdict(list)
    for t in tags:
        if tag_kind(t) == "bootcamp" and (d := tag_date(t)):
            by_date[d].append(t)
    pairs = {}
    for key, rs in groups.items():
        votes = Counter(r["lsq_tag"] for r in rs if r["lsq_tag"] and tag_kind(r["lsq_tag"]) == "bootcamp")
        matched = sum(votes.values())
        tag, how = "", ""
        if votes:
            best, n = votes.most_common(1)[0]
            if n * 2 > matched:
                tag, how = best, f"{n} of {matched} matched students carry this tag"
        if not tag and key[1]:
            words = _words(key[0])
            scored = sorted(((len(words & _words(tag_course(t))), t) for t in by_date.get(key[1], [])), reverse=True)
            if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                tag, how = scored[0][1], "same bootcamp date and course name"
        pairs[key] = {"tag": tag or NOT_PAIRED, "how": how, "students": len(rs), "matched": matched}
        for r in rs:
            r["bootcamp_tag"] = tag or NOT_PAIRED
    return pairs


def disagreement(r: dict) -> str:
    """Where the sheet and LeadSquared tell different stories; the report flags these instead of choosing one."""
    stage = (r.get("lsq_stage") or "").strip().lower()
    if not r.get("lsq_id") or not stage:
        return ""
    o = r["outcome"]
    if o == "Collected" and stage in LOST_STAGES:
        return f"Sheet says collected; LeadSquared stage is {r['lsq_stage']}"
    if o == "Collected" and stage in BOOKING_STAGES:
        return "Sheet says collected; LeadSquared still at Booking fees received"
    if o == "Dropped" and stage in COLLECTED_STAGES:
        return f"Sheet says dropped; LeadSquared stage is {r['lsq_stage']}"
    if o in ("Pending", "Instalments") and r["balance"] > 0 and stage == "collections done":
        return f"LeadSquared says Collections done; sheet shows Rs {r['balance']:,.0f} still due"
    return ""
