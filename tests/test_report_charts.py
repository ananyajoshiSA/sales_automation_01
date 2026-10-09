from analytics.report_charts import bar, columns, paired_bars, stacked_bar, ticks


def test_ticks_are_round_and_cover_the_top():
    assert ticks(3052) == [0, 1000, 2000, 3000, 4000]
    assert ticks(32.9) == [0, 10, 20, 30, 40]
    assert ticks(0) == [0, 1]
    assert ticks(7)[-1] >= 7


def test_bars_skip_missing_values():
    assert bar(None, 10) == "" and bar(0, 10) == "" and bar(5, 0) == ""
    assert "width:55px" in bar(5, 10, 110)
    assert stacked_bar([3, 0], 6, ["#a", "#b"], 100).count("class='bar'") == 1


def test_columns_and_paired_bars_label_what_they_say():
    svg = columns(["10", "11"], [100, 250], label_at={1}, highlight={1})
    assert svg.startswith("<svg") and "class='val'>250<" in svg and "class='val'>100<" not in svg
    pb = paired_bars([("Talked about the fee", 98, 82), ("Gave a deadline", None, 28)], ("enrolled", "not enrolled"))
    assert "98%" in pb and "82%" in pb and "–" in pb and "enrolled" in pb
