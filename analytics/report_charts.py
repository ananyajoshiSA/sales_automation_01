"""Chart pieces for the PDF reports: inline SVG and HTML bars, so no plotting library is needed.

The PDF is printed, so nothing hovers: values sit on the marks or in a table beside the chart. Colours
follow a palette checked for colour-blind readers (blue and orange, plus a context gray); text never
takes a series colour, and every two-colour chart has a legend.
"""

from __future__ import annotations

import html
import math

BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#b9b7ae"
INK, MUTED, GRID, AXIS = "#1d2433", "#5b6475", "#e6e5df", "#c3c2b7"
e = html.escape


def bar(value: float | None, top: float | None, width: int = 110, color: str = BLUE) -> str:
    """An inline bar sized to value / top; nothing for a missing or zero value."""
    if not value or not top or value <= 0:
        return ""
    return f"<span class='bar' style='width:{max(2, round(width * value / top))}px;background:{color}'></span>"


def legend(items: list[tuple[str, str]]) -> str:
    return "<div class='legend'>" + "".join(f"<span><i style='background:{c}'></i>{e(t)}</span>" for c, t in items) + "</div>"


def ticks(top: float, n: int = 4) -> list[float]:
    """Round axis ticks from 0 that cover ``top``."""
    if top <= 0:
        return [0, 1]
    raw = top / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return [round(i * step, 6) for i in range(math.ceil(top / step - 1e-9) + 1)]


def _num(v: float) -> str:
    return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"


def _column(x: float, y: float, w: float, base: float) -> str:
    """A column with a 3px rounded top and a square foot on the baseline."""
    r = min(3, w / 2, max(base - y, 0))
    return (f"M{x:.1f},{base:.1f}L{x:.1f},{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}L{x + w - r:.1f},{y:.1f}"
            f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}L{x + w:.1f},{base:.1f}Z")


def columns(labels: list[str], values: list[float | None], *, unit: str = "", label_at: set[int] | None = None,
            highlight: set[int] | None = None, height: int = 130, width: int = 690, top: float | None = None) -> str:
    """One series of vertical columns on one axis. Values at ``label_at`` are written on their column;
    columns at ``highlight`` are blue and the rest gray when a highlight is given."""
    left, bottom, head = 40, 18, 12
    vals = [v or 0 for v in values]
    tk = ticks(top if top is not None else max(vals, default=0))
    hi = tk[-1] or 1
    plot_h = height - bottom - head
    slot = (width - left) / max(len(values), 1)
    w = min(24, slot * 0.62)
    base = head + plot_h
    out = [f"<svg class='chart' viewBox='0 0 {width} {height}' width='100%' role='img'>"]
    for t in tk:
        y = base - plot_h * t / hi
        out.append(f"<line x1='{left}' x2='{width}' y1='{y:.1f}' y2='{y:.1f}' stroke='{AXIS if t == 0 else GRID}' stroke-width='1'/>"
                   f"<text x='{left - 5}' y='{y + 3:.1f}' text-anchor='end' class='tick'>{_num(t)}{unit}</text>")
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = left + slot * i + (slot - w) / 2
        if v:
            y = base - plot_h * v / hi
            color = BLUE if highlight is None or i in highlight else GRAY
            out.append(f"<path d='{_column(x, y, w, base)}' fill='{color}'/>")
            if label_at and i in label_at:
                out.append(f"<text x='{x + w / 2:.1f}' y='{y - 3:.1f}' text-anchor='middle' class='val'>{_num(v)}{unit}</text>")
        out.append(f"<text x='{x + w / 2:.1f}' y='{height - 5}' text-anchor='middle' class='tick'>{e(lab)}</text>")
    return "".join(out) + "</svg>"


def paired_bars(rows: list[tuple[str, float | None, float | None]], names: tuple[str, str],
                colors: tuple[str, str] = (BLUE, GRAY), width: int = 690) -> str:
    """Two thin horizontal bars per row on one 0-100% axis, each with its value at the tip."""
    left, right, row_h, head = 230, 40, 26, 14
    plot_w = width - left - right
    height = head + row_h * len(rows) + 4
    out = [f"<svg class='chart' viewBox='0 0 {width} {height}' width='100%' role='img'>"]
    for t in (0, 25, 50, 75, 100):
        x = left + plot_w * t / 100
        out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{head - 2}' y2='{height}' stroke='{AXIS if t == 0 else GRID}'/>"
                   f"<text x='{x:.1f}' y='{head - 5}' text-anchor='middle' class='tick'>{t}%</text>")
    for i, (lab, a, b) in enumerate(rows):
        y = head + row_h * i + 4
        out.append(f"<text x='{left - 8}' y='{y + 12}' text-anchor='end' class='lab'>{e(lab)}</text>")
        for j, (v, c) in enumerate(((a, colors[0]), (b, colors[1]))):
            yy = y + j * 10
            if v is None:
                out.append(f"<text x='{left + 4}' y='{yy + 8}' class='val'>–</text>")
                continue
            w = plot_w * v / 100
            if w > 0:
                out.append(f"<rect x='{left}' y='{yy}' width='{max(w, 1.5):.1f}' height='8' rx='2' fill='{c}'/>")
            out.append(f"<text x='{left + w + 4:.1f}' y='{yy + 7.5}' class='val'>{_num(v)}%</text>")
    out.append("</svg>")
    return legend(list(zip(colors, names))) + "".join(out)


def stacked_bar(parts: list[float], top: float, colors: list[str], width: int = 260) -> str:
    """One horizontal bar split into parts, with a 2px gap between them."""
    spans = [f"<span class='bar' style='width:{max(2, round(width * v / top))}px;background:{c}'></span>"
             for v, c in zip(parts, colors) if v and top]
    return "<span class='stack'>" + "".join(spans) + "</span>"
