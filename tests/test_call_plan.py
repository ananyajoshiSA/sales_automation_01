from openpyxl import load_workbook

from analytics import call_plan
from analytics.call_plan import build_rows, read_carry


def plan(**kw):
    p = {"reviewed": [], "tiered": [], "first": [], "candidates": [], "stages": {}, "owner_order": ["Asha K", "Ravi S"]}
    p.update(kw)
    return p


def cand(lead, owner="Asha K", missed=0, stage="Call Back Later"):
    return {"lead_id": lead, "owner": owner, "name": f"Lead {lead}", "phone": "9", "stage": stage,
            "inbound_missed_unreturned": missed, "last_conversation_ist": "2026-10-06 10:00"}


def tiered(lead, tier, owner="Asha K", prob=10):
    return {"lead_id": lead, "owner": owner, "name": f"Lead {lead}", "tier": tier, "prob_3d": prob, "why": "w",
            "opening_line": "hi", "next_action": "n"}


def test_missed_call_from_lead_becomes_tier_m_and_goes_first():
    p = plan(candidates=[cand("L1", missed=2), cand("L2"), cand("L3", missed=1)],
             tiered=[tiered("L2", "A", prob=40), tiered("L3", "B")])
    rows = build_rows(p)["Asha K"]
    assert [r["lead_id"] for r in rows] == ["L3", "L1", "L2"]  # within M, the higher chance first
    assert rows[0]["tier"] == rows[1]["tier"] == "M" and "Called us 2 time(s)" in rows[1]["why"]


def test_link_sent_not_paid_carried_from_yesterdays_workbook(tmp_path):
    p = plan(candidates=[cand("L1"), cand("L2"), cand("L3")],
             tiered=[tiered("L1", "B"), tiered("L2", "C"), tiered("L3", "B")])
    by_owner = build_rows(p)
    wb_path = str(tmp_path / "wed.xlsx")
    wb = call_plan.Workbook()
    call_plan.guide_sheet(wb)
    _, first, _ = call_plan.caller_sheet(wb, "Asha K", by_owner["Asha K"])
    ws = wb["Asha K"]
    col = call_plan.COL
    ids = {ws.cell(row=first + i, column=col["Lead ID"]).value: first + i for i in range(3)}
    ws.cell(row=ids["L1"], column=col["Payment status"], value="Link sent")
    ws.cell(row=ids["L2"], column=col["Payment status"], value="Token paid")
    ws.cell(row=ids["L2"], column=col["Attempt 1 outcome"], value="Enrolled / paid")  # paid after all: not carried
    ws.cell(row=ids["L3"], column=col["Payment status"], value="Paid")
    wb.save(wb_path)

    carry = read_carry([wb_path])
    assert [c["lead_id"] for c in carry] == ["L1"] and carry[0]["owner"] == "Asha K"
    rows = build_rows(plan(candidates=[cand("L1")], carry=carry))["Asha K"]
    assert rows[0]["tier"] == "P" and "Link sent" in rows[0]["why"]


def test_summary_sheet_counts_new_tiers(tmp_path):
    rows = build_rows(plan(candidates=[cand("L1", missed=1)], tiered=[tiered("L2", "A")]))
    wb = call_plan.Workbook()
    call_plan.guide_sheet(wb)
    _, f, l = call_plan.caller_sheet(wb, "Asha K", rows["Asha K"])
    call_plan.summary_sheet(wb, ["Asha K"], {"Asha K": (f, l)})
    path = str(tmp_path / "p.xlsx")
    wb.save(path)
    ws = load_workbook(path)["Team summary"]
    heads = [c.value for c in ws[4]]
    assert heads[1:3] == ["Return calls (M)", "Unpaid links (P)"]
    assert ws.cell(row=5, column=2).value.startswith('=COUNTIF(') and '"M ·*"' in ws.cell(row=5, column=2).value
