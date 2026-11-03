from __future__ import annotations

from analytics import agent_alerts as aa


def lead(phone, slug, at="2026-10-20T10:00"):
    return {"whatsapp": phone, "email": "", "pageUrl": f"https://x/f/{slug}?u=1", "captured_ist": at}


def call(cid, phone, disp, status="Contacted", agent="A"):
    return {"call_id": cid, "to": "91" + phone, "status": status, "disposition": disp, "agent_id": agent}


def camp(n_pos, n_hang, n_unfit, n_noconn, slug="p1", start=0, agent="A", full=True):
    leads, logs, i = [], [], start
    for kind, n in (("pos", n_pos), ("hang", n_hang), ("unfit", n_unfit), ("no", n_noconn)):
        for _ in range(n):
            ph = f"90000{i:05d}"
            leads.append(lead(ph, slug))
            d = {"pos": "Full Conversion NEW" if full else "Bootcamp Attendance Confirmed NEW",
                 "hang": "Hangup Early NEW", "unfit": "Irrelevant NEW", "no": "USER_BUSY"}[kind]
            logs.append(call(i, ph, d, "Contacted" if kind != "no" else "No Contact", agent))
            logs.append(call(i, ph, d, "Contacted" if kind != "no" else "No Contact", agent))  # duplicate row
            i += 1
    return leads, logs


def stats_for(leads, logs, agent="A"):
    tp, agents = aa.outcomes(logs)
    return aa.campaign_stats(leads, tp, agents, agent)


def test_counts_each_call_once_and_rates():
    s = stats_for(*camp(4, 3, 1, 2))
    assert (s["called"], s["connected"], s["positive"], s["hangup_early"], s["unfit"], s["calls"]) == (10, 8, 4, 3, 1, 10)
    assert aa.rates(s)["positive_rate"] == 0.4


def test_below_target_opening_and_icp_fit_against_median():
    good = stats_for(*camp(40, 5, 5, 10))
    good2 = stats_for(*camp(38, 6, 6, 10, start=1000))
    weak = stats_for(*camp(10, 30, 20, 10, start=2000))
    found = aa.alerts({"g": good, "g2": good2, "w": weak}, {})
    codes = {(a["campaign"], a["code"]) for a in found}
    assert ("w", "BELOW_TARGET") in codes and ("w", "OPENING") in codes and ("w", "ICP_FIT") in codes
    assert not any(c == "g" for c, _ in codes)
    short = next(a for a in found if a["code"] == "BELOW_TARGET")
    assert "10 of 70" in short["detail"] and "11 short" in short["detail"]


def test_discovery_wrong_agent_missing_new_and_weak_funnel():
    a = stats_for(*camp(40, 5, 5, 0))
    b = stats_for(*camp(40, 5, 5, 0, start=1000))
    l1, g1 = camp(40, 5, 5, 0, start=2000, agent="B", full=False)
    l2, g2 = camp(2, 30, 10, 0, slug="new-page", start=3000)
    l3, g3 = camp(5, 5, 5, 0, slug="teachers", start=4000)
    c = stats_for(l1 + l2 + l3, g1 + g2 + g3, agent="A")
    maps = {"c": {"pages": {"p1": "Seg"}, "missing_pages": {"teachers": "Teachers"}}}
    found = aa.alerts({"a": a, "b": b, "c": c}, maps, {"missing_min_leads": 10, "page_min_connected": 40})
    codes = [x["code"] for x in found if x["campaign"] == "c"]
    for code in ("DISCOVERY", "WRONG_AGENT", "MISSING_ICP", "NEW_FUNNEL", "WEAK_FUNNEL"):
        assert code in codes, code
    assert any("15 leads from ads for 'Teachers'" in x["detail"] for x in found)


def test_campaign_with_no_connections_is_skipped_and_message_lists_all():
    s = stats_for(*camp(0, 0, 0, 5))
    assert aa.alerts({"x": s}, {}) == []
    assert "no alerts" in aa.message({"x": s}, [], "now", {"x": "X"})


def test_partial_pages_alert_and_every_saved_map_loads():
    a = stats_for(*camp(40, 5, 5, 0, slug="p1"))
    found = aa.alerts({"a": a}, {"a": {"pages": {"p1": "Seg"}, "partial_pages": {"p1": "Seg — no story"}}},
                      {"partial_min_leads": 10})
    assert any(x["code"] == "PARTIAL_ICP" and "50 leads: Seg — no story" in x["detail"] for x in found)
    import json
    cfg = json.load(open(aa.CONFIG))
    for c in cfg["campaigns"].values():
        m = aa.load_map(c["map"])
        assert set(m["missing_pages"]) <= set(m["pages"]) and set(m["partial_pages"]) <= set(m["pages"])
