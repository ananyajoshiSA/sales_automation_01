"""Stage 2 of the funnel ICP report: one row per lead for human sales callers. No lead is left out.

Each lead gets the ICP to use (what they told the AI agent, when known, else the ad they came from), how
that ICP has sold so far, their answer on the AI call (Yes / Callback / No clear answer / No / Not reached),
the AI call summary, a priority, and the opening line, story and bootcamp day for their ICP from the
map's ``playbook``. Leads who already bought go to their own tab; everyone else stays on the call list.

Priority (no invented probabilities; "above average" is the ICP's lead → sale rate so far vs the campaign's):
    1  Said yes, and their ICP buys at or above average
    2  Said yes, ICP buys below average (or has no sales yet)
    3  Callback or no clear answer, ICP above average
    4  Callback or no clear answer, ICP below average
    5  The AI agent never reached them
    6  Said no, wrong number or blacklisted: call last, after checking
Inside each priority, the best-selling ICP comes first.

Written by ``python -m analytics.funnel_icp report ...`` next to the stage 1 file; ``--stated-icp`` adds
the ICP read from each lead's call summary (a TSV of phone, code).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from analytics.funnel_icp import POSITIVE, first_touch, page_slug, person_key, phone10, email_key

DO_NOT_CALL = (  # a "No" on the AI call: these leads go last, never off the list
    "Not Interested", "Wrong Number", "Blacklisted", "Irrelevant", "Busy – No Callback")
CALLBACK = ("Call Back", "Third Party Callback")
PRIORITY_TEXT = {1: "1 · Call first", 2: "2 · Call next", 3: "3 · Then these", 4: "4 · Then these",
                 5: "5 · Not reached yet", 6: "6 · Said no: call last"}


def ai_answer(t: dict) -> str:
    """The lead's answer on the AI call, best first: Yes, Callback, No clear answer, No, Not reached."""
    if not t:
        return "Not dialled"
    dispos = t.get("dispositions", [])
    if t.get("best", 99) < len(POSITIVE):
        return "Yes"
    if any(d.startswith(CALLBACK) for d in dispos):
        return "Callback"
    if any(d.startswith(DO_NOT_CALL) for d in dispos):
        return "No"
    return "No clear answer" if t.get("connected") else "Not reached"


def read_stated(path: str | None, codes: dict[str, str]) -> dict[str, str]:
    """phone -> ICP from a TSV of ``phone<TAB>code`` (codes as in the map's ``stated_icp_codes``)."""
    out: dict[str, str] = {}
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip() in codes:
                out[phone10(parts[0])] = codes[parts[1].strip()]
    return out


def caller_rows(leads: list[dict], result: dict, icp_map: dict, tp: dict[str, dict] | None,
                stated: dict[str, str]) -> list[dict]:
    pages, segs, book = icp_map["pages"], icp_map.get("general_segments", {}), icp_map.get("playbook", {})
    people = first_touch(leads)
    sold = {s["key"]: s for s in result["sales"] if s["key"]}
    total_leads = sum(v["leads"] for v in result["icp"].values()) or 1
    avg = sum(v["sales"] for v in result["icp"].values()) / total_leads
    rate = {k: (v["sales"] / v["leads"] if v["leads"] else 0.0) for k, v in result["icp"].items()}
    rows = []
    for key, row in people.items():
        slug = page_slug(row.get("pageUrl"))
        ad_icp = pages.get(slug, "UNMAPPED")
        said = stated.get(key, "")
        use = said or ad_icp
        # "General" from the call only means none of the call ICPs fit, so it never replaces a specific ad
        # ICP; teacher ads keep Teachers when the reading had no teacher option (Retired / General / Senior women).
        if said and ad_icp not in ("General", "UNMAPPED") and (said == "General" or (
                ad_icp == "Teachers / educators" and said in ("Retired / about to retire", "Senior women professionals"))):
            use = ad_icp
        t = (tp or {}).get(key) or {}
        dispos = t.get("dispositions", [])
        answer = ai_answer(t)
        r_icp = rate.get(use, 0.0)
        high = r_icp >= avg and r_icp > 0
        prio = {("Yes", True): 1, ("Yes", False): 2}.get((answer, high)) or (
            (3 if high else 4) if answer in ("Callback", "No clear answer") else 5 if answer in ("Not reached", "Not dialled")
            else 6)
        a = result["icp"].get(use, {"leads": 0, "sales": 0})
        sales_so_far = f"{a['sales']} of {a['leads']:,} leads bought ({100 * r_icp:.1f}%)" if a["leads"] else "No data"
        tier = "buys more than average" if high else "no sales yet" if not a.get("sales") else "buys less than average"
        why = {1: f"Said yes, and {use} {tier}", 2: f"Said yes; {use} {tier}",
               3: f"{answer} on the AI call; {use} {tier}", 4: f"{answer} on the AI call; {use} {tier}",
               5: f"The AI agent never reached them; {use} {tier}",
               6: "Blacklisted on the dialer: check before calling" if any(d.startswith("Blacklisted") for d in dispos)
               else "Wrong number on the AI call: check the number first" if any(d.startswith("Wrong Number") for d in dispos)
               else "Said no to the AI agent: one polite call to confirm"}[prio]
        pb = book.get(use, {})
        rows.append({
            "tab": "Already enrolled" if key in sold else "Call list", "prio": prio,
            "Priority": PRIORITY_TEXT[prio], "Why this priority": why,
            "Name": row.get("name") or row.get("fullName") or "", "Phone": key if key.isdigit() else "",
            "Email": email_key(row.get("email")), "ICP": use, "ICP sales so far": sales_so_far,
            "Said yes to the AI agent?": answer, "AI call summary": t.get("summary", ""),
            "ICP from ad": ad_icp, "ICP from what they told the AI": said or "(not read yet)",
            "Check": "Ad and call disagree: using what they told the AI" if said and use != ad_icp else "",
            "Segment": segs.get(slug, "") if ad_icp == "General" else "",
            "Ad they responded to": row.get("funnel") or "", "Signed up (IST)": row.get("captured_ist") or "",
            "AI call result": t.get("disposition") or ("No answer" if t else "Not dialled"),
            "AI attempts": t.get("attempts", 0), "Last AI call (IST)": t.get("last_call", ""),
            "Pain to touch": pb.get("pain", ""), "Open with": pb.get("open_with", ""),
            "Story to tell": pb.get("story", ""), "Day to point to": pb.get("day", ""),
            "rate": r_icp, "Enrolled status": sold[key]["status"] if key in sold else "",
            "Call outcome (fill in)": "", "Next step (fill in)": "",
        })
    for r in rows:
        if r["tab"] == "Already enrolled":
            r["Priority"], r["Why this priority"] = "", "Already enrolled: hand over to onboarding"
    rows.sort(key=lambda r: (r["prio"], -r["rate"], r["Last AI call (IST)"] or "~"))
    return rows


COLS = ["Priority", "Name", "Phone", "ICP", "ICP sales so far", "Said yes to the AI agent?", "AI call summary",
        "Why this priority", "Open with", "Story to tell", "Day to point to", "Pain to touch", "AI call result",
        "AI attempts", "Last AI call (IST)", "ICP from ad", "ICP from what they told the AI", "Check", "Segment",
        "Ad they responded to", "Signed up (IST)", "Email", "Call outcome (fill in)", "Next step (fill in)"]


def caller_checks(rows: list[dict], result: dict) -> list[tuple[str, str, str]]:
    out = []
    n = len(rows)
    out.append(("PASS" if n == result["people"] else "FAIL", "Caller list: one row per lead, none left out",
                f"{n:,} rows vs {result['people']:,} leads"))
    tabs = Counter(r["tab"] for r in rows)
    out.append(("PASS" if set(tabs) <= {"Call list", "Already enrolled"} else "FAIL",
                "Caller list: every lead on the call list or the enrolled tab", str(dict(tabs))))
    keys = len({s["key"] for s in result["sales"] if s["key"]})
    out.append(("PASS" if tabs.get("Already enrolled", 0) == keys else "FAIL",
                "Caller list: enrolled tab = matched sales (people)", f"{tabs.get('Already enrolled', 0)} vs {keys}"))
    by_ad = Counter(r["ICP from ad"] for r in rows)
    bad = {k: (v, result["icp"].get(k, {}).get("leads")) for k, v in by_ad.items()
           if v != result["icp"].get(k, {}).get("leads")}
    out.append(("PASS" if not bad else "FAIL", "Caller list: leads per ad ICP = stage 1", str(bad) if bad else "ok"))
    noprio = sum(1 for r in rows if r["tab"] == "Call list" and r["prio"] not in PRIORITY_TEXT)
    out.append(("PASS" if not noprio else "FAIL", "Caller list: every lead to call has a priority", f"{noprio} without"))
    return out


def write_caller_xlsx(rows: list[dict], path: str, title: str, fresh: str, icp_map: dict, result: dict) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "How to use"
    for line in [
        [title], [fresh], [],
        ["Who this is for: sales callers. Every lead is on the Call list, best first. Nobody is left out."],
        ["1. Work the Call list top to bottom: priority 1 first, down to 6. Low priority still gets a call."],
        ["2. Before dialling, read 'AI call summary'. Don't ask again what they already told our AI agent."],
        ["3. 'ICP sales so far' shows how often people like this lead have bought. Higher = easier to convert."],
        ["4. Use 'Open with', 'Story to tell' and 'Day to point to' for their ICP. Never say the word ICP."],
        ["5. 'ICP' is what they told our AI agent when we know it; otherwise the ad they came from."],
        ["6. Fill in 'Call outcome' and 'Next step' after every call."],
        ["7. 'Already enrolled' is for onboarding, not sales."],
        [],
        ["Priority", "Meaning"],
        *[[PRIORITY_TEXT[k], v] for k, v in {
            1: "Said yes to the AI agent, and their ICP buys at or above the campaign average",
            2: "Said yes, but their ICP buys below average (or has no sales yet)",
            3: "Asked for a callback or gave no clear answer; ICP buys above average",
            4: "Asked for a callback or gave no clear answer; ICP buys below average",
            5: "The AI agent never reached them",
            6: "Said no, wrong number or blacklisted: call last, after checking"}.items()],
        [], ["ICP (best sellers first)", "Leads", "Lead → sale so far", "Open with", "Story", "Day"],
        *[[k, v["leads"], round(v["sales"] / v["leads"], 4) if v["leads"] else 0,
           icp_map.get("playbook", {}).get(k, {}).get("open_with", ""),
           icp_map.get("playbook", {}).get(k, {}).get("story", ""),
           icp_map.get("playbook", {}).get(k, {}).get("day", "")]
          for k, v in sorted(result["icp"].items(), key=lambda x: -(x[1]["sales"] / x[1]["leads"] if x[1]["leads"] else 0))
          if v["leads"]],
    ]:
        ws.append(line)
    ws["A1"].font = Font(bold=True, size=13)
    ws.column_dimensions["A"].width = 36
    for col, w in zip("BCDEF", [12, 14, 70, 60, 8]):
        ws.column_dimensions[col].width = w
    widths = [20, 22, 13, 28, 30, 16, 70, 40, 60, 50, 10, 30, 22, 9, 18, 26, 26, 24, 26, 40, 16, 26, 24, 24]
    for tab in ("Call list", "Already enrolled"):
        t = wb.create_sheet(tab)
        cols = COLS + (["Enrolled status"] if tab == "Already enrolled" else [])
        t.append(cols)
        for r in rows:
            if r["tab"] == tab:
                t.append([r[c] for c in cols])
        for c in t[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DDEBF7")
        t.freeze_panes = "D2"
        t.auto_filter.ref = t.dimensions
        for i, w in enumerate(widths + [16], start=1):
            t.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        for row in t.iter_rows(min_row=2):
            for c in list(row[6:7]) + list(row[8:12]):
                c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
