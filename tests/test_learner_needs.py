from __future__ import annotations

import json
import os

from analytics.learner_needs import aggregate as agg
from analytics.learner_needs import sample as smp
from analytics.learner_needs.__main__ import main
from analytics.learner_needs.pdf import html_doc


def log(to, dur, tid="t", disp="Discovery Successful NEW"):
    return {"to": to, "duration": dur, "transcript_id": tid, "disposition": disp, "start_time": "2026-11-20T10:00:00"}


def test_longest_per_lead_keeps_one_long_call_with_transcript():
    rows = [log("+919800000001", 70, "a"), log("9800000001", 200, "b"), log("9800000002", 59, "c"),
            log("9800000003", 300, None), log("9800000004", 400, "d", "Out of Business Hour")]
    out = smp.longest_per_lead(rows)
    assert [(c["p"], c["tid"]) for c in out] == [("9800000001", "b")]


def test_stratified_floor_and_size():
    calls = [{"p": f"{i:010d}", "slug": "big"} for i in range(200)] + \
            [{"p": f"9{i:09d}", "slug": "small"} for i in range(5)] + \
            [{"p": f"8{i:09d}", "slug": "mid"} for i in range(40)]
    out = smp.stratified(calls, 60, min_per=8)
    by = {s: sum(1 for c in out if c["slug"] == s) for s in ("big", "small", "mid")}
    assert len(out) == 60 and by["small"] == 5 and by["mid"] >= 8 and by["big"] > by["mid"]
    assert out == smp.stratified(calls, 60, min_per=8)


def test_stratified_returns_all_when_few():
    calls = [{"p": "1", "slug": "a"}, {"p": "2", "slug": "b"}]
    assert len(smp.stratified(calls, 10)) == 2


def test_chunks_near_equal():
    assert [len(c) for c in smp.chunks(list(range(400)), 130)] == [100, 100, 100, 100]
    assert smp.chunks([], 10) == []


def test_format_call_labels_speakers():
    txt = smp.format_call(3, {"slug": "f1", "start": "2026-11-20T10:00:00", "dur": 90, "disp": "X"},
                          [{"speaker": "BOT", "message": "Hi"}, {"speaker": "HUMAN", "message": " yes  ma'am "}])
    assert txt.splitlines() == ["===== LEAD 3 | funnel f1 | 2026-11-20T10:00:00 IST | 90 s | disposition: X",
                                "AGENT: Hi", "LEAD: yes ma'am"]


def write_tsv(path, rows):
    head = "\t".join(agg.COLS)
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n" + "\n".join("\t".join(r) for r in rows) + "\n")


def row(n, seg, funnel="f1"):
    return [n, funnel, "teacher", seg, "income", "20k", "-", "fees", "job", "is it free", "-", "free work"]


def test_load_summary_and_themes(tmp_path):
    write_tsv(tmp_path / "chunk_01.tsv", [row("1", "Teacher"), row("2 Asha", "too short"), row("x", "bad")])
    write_tsv(tmp_path / "chunk_02.tsv", [row("1", "home tutor", "f2"), row("short", "x")[:5]])
    rows = agg.load(str(tmp_path))
    assert [r["key"] for r in rows] == ["chunk_01-1", "chunk_01-2", "chunk_02-1"]
    groups = {"teacher": "Teachers", "too short": agg.NOT_HEARD}
    assert agg.unmapped(rows, groups) == {"home tutor": 1}
    groups["home tutor"] = "Teachers"
    (tmp_path / "themes.tsv").write_text(
        "chunk_01-1\tdoubt\tFees\nchunk_01-1\tdoubt\tFees\nchunk_02-1\tdoubt\tFees\n"
        "chunk_09-9\tdoubt\tFees\nchunk_01-1\tbogus\tX\n", encoding="utf-8")
    s = agg.summarise(rows, groups, agg.read_themes(str(tmp_path / "themes.tsv")))
    assert s["total"] == 3 and s["heard"] == 2
    assert s["by_group"] == {"Teachers": 2, agg.NOT_HEARD: 1}
    assert s["themes"] == {"doubt": {"Fees": 2}} and s["themes_unknown_leads"] == 1
    assert s["by_funnel"]["f1"] == {"Teachers": 1, agg.NOT_HEARD: 1}


def test_html_counts_from_themes_not_text():
    summary = {"total": 10, "heard": 8, "by_group": {"Teachers": 8, agg.NOT_HEARD: 2},
               "by_funnel": {"f1": {"Teachers": 8}}, "themes": {"doubt": {"Fees": 4}}}
    findings = {"headline": ["Say the fee early"], "groups": {"Teachers": {"who": "school teachers", "wants": "side income"}},
                "doubts": [{"doubt": "Fees later", "answer": "Say it", "theme": "Fees"},
                           {"doubt": "Untracked", "answer": "-", "theme": "Missing"}],
                "quotes": [{"q": "is it <free>", "who": "teacher"}]}
    h = html_doc({"name": "Test camp", "dates": "1 Dec"}, summary, findings, [])
    assert "4 (50%)" in h and "8 (80%)" in h and "is it &lt;free&gt;" in h
    assert h.count("<td class='n'>-</td>") == 1


def test_cli_aggregate_flags_unmapped(tmp_path, monkeypatch, capsys):
    run = tmp_path / "data" / "ZZ"
    run.mkdir(parents=True)
    write_tsv(run / "chunk_01.tsv", [row("1", "Teacher"), row("2", "new label")])
    (run / "chunk_01.txt").write_text("x", encoding="utf-8")
    cfg_dir = os.path.join(os.path.dirname(agg.__file__), "bootcamps")
    path = os.path.join(cfg_dir, "ZZ.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": "Z", "groups": {"Teacher": "Teachers"}}, f)
    try:
        code = main(["aggregate", "ZZ", "--data", str(tmp_path / "data"), "--out", str(tmp_path / "out")])
    finally:
        os.remove(path)
    assert code == 1 and "new label" in capsys.readouterr().out
    assert (tmp_path / "out" / "Learner_needs_ZZ_leads.csv").exists()
