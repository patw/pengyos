"""Chart geometry for pengyos-storage: the pie and the treemap.

Pure maths, no filesystem, no drawing library — the renderers turn these into
SVG. Two views of the same numbers, because they answer two questions:

* the **pie** answers "how is the space divided" (share);
* the **treemap** answers "which box is the big one" (area = bytes), which is
  readable at a glance where a pie of 15 slices is not.

Both take ``items`` as ``[(label, bytes)]`` in report order (biggest first).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

#: A short, colour-blind-friendly palette; cycles for more slices than colours.
PALETTE = (
    "#2f6f9f", "#e08214", "#7b9e3a", "#b0413e", "#6a51a3", "#d1622b",
    "#3d8f8f", "#8c6d3f", "#4e79a7", "#a55f9e", "#59a14f", "#c68a2b",
)

#: Below this share of the pie a slice gets no inline label (it would not fit).
MIN_LABEL_PCT = 4.0


@dataclass
class Slice:
    """One pie wedge."""

    label: str
    value: int
    percent: float
    start_deg: float
    end_deg: float
    color: str
    path: str
    label_xy: tuple[float, float] | None = None


@dataclass
class Rect:
    """One treemap rectangle."""

    label: str
    value: int
    percent: float
    x: float
    y: float
    w: float
    h: float
    color: str
    label_xy: tuple[float, float] | None = None


def _point(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _fmt(v: float) -> str:
    """Round to 3 decimals and drop trailing zeros, so the SVG is stable."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def pie(
    items: Iterable[tuple[str, int]],
    cx: float = 170.0,
    cy: float = 170.0,
    r: float = 150.0,
    inner: float = 62.0,
) -> list[Slice]:
    """Lay out a donut. Slices start at 12 o'clock and run clockwise.

    ``inner`` is the donut hole (0 gives a plain pie). A single 100 % slice is
    drawn as a full ring, because an arc from a point back to itself draws
    nothing.
    """
    pairs = [(str(label), int(value)) for label, value in items if int(value) > 0]
    total = sum(v for _, v in pairs)
    if total <= 0:
        return []

    slices: list[Slice] = []
    angle = -90.0
    for i, (label, value) in enumerate(pairs):
        sweep = 360.0 * value / total
        start, end = angle, angle + sweep
        color = PALETTE[i % len(PALETTE)]
        pct = value / total * 100.0

        if sweep >= 359.999:
            # Full circle: two half arcs (a single arc to the same point is empty).
            x0, y0 = _point(cx, cy, r, -90.0)
            x1, y1 = _point(cx, cy, r, 90.0)
            d = (
                f"M {_fmt(x0)} {_fmt(y0)} "
                f"A {_fmt(r)} {_fmt(r)} 0 1 1 {_fmt(x1)} {_fmt(y1)} "
                f"A {_fmt(r)} {_fmt(r)} 0 1 1 {_fmt(x0)} {_fmt(y0)} Z"
            )
        else:
            x0, y0 = _point(cx, cy, r, start)
            x1, y1 = _point(cx, cy, r, end)
            large = 1 if sweep > 180.0 else 0
            d = (
                f"M {_fmt(cx)} {_fmt(cy)} "
                f"L {_fmt(x0)} {_fmt(y0)} "
                f"A {_fmt(r)} {_fmt(r)} 0 {large} 1 {_fmt(x1)} {_fmt(y1)} Z"
            )

        mid = start + sweep / 2.0
        label_xy = None
        if pct >= MIN_LABEL_PCT:
            lr = (r + inner) / 2.0 if inner > 0 else r * 0.6
            label_xy = _point(cx, cy, lr, mid)

        slices.append(Slice(
            label=label, value=value, percent=pct,
            start_deg=start, end_deg=end, color=color, path=d, label_xy=label_xy,
        ))
        angle = end

    return slices


def _worst(row: list[float], side: float) -> float:
    """The squarify cost of a row laid against ``side``: the worst aspect ratio."""
    if not row or side <= 0:
        return float("inf")
    s = sum(row)
    if s <= 0:
        return float("inf")
    return max((side * side * max(row)) / (s * s), (s * s) / (side * side * min(row)))


def _layout_row(row: list[tuple[str, int, float]], x: float, y: float, w: float, h: float):
    """Lay one row along the shorter side of ``x,y,w,h``; return rects + thickness."""
    area = sum(a for _, _, a in row)
    if w >= h:  # vertical strip on the left edge
        thick = area / h
        rects, yy = [], y
        for label, value, a in row:
            hh = a / thick
            rects.append((label, value, x, yy, thick, hh))
            yy += hh
        return rects, thick, True
    thick = area / w  # horizontal strip on the top edge
    rects, xx = [], x
    for label, value, a in row:
        ww = a / thick
        rects.append((label, value, xx, y, ww, thick))
        xx += ww
    return rects, thick, False


def treemap(
    items: Iterable[tuple[str, int]],
    x: float = 0.0,
    y: float = 0.0,
    w: float = 900.0,
    h: float = 320.0,
    gap: float = 0.0,
) -> list[Rect]:
    """Squarified treemap: rectangle area is proportional to bytes.

    ``gap`` shrinks each rectangle for visual separation; the layout maths is
    done on the gap-free geometry, so areas stay proportional.
    """
    pairs = [(str(label), int(value)) for label, value in items if int(value) > 0]
    total = sum(v for _, v in pairs)
    if total <= 0 or w <= 0 or h <= 0:
        return []

    scale = (w * h) / total
    row_in = [(label, value, value * scale) for label, value in pairs]

    placed: list[tuple[str, int, float, float, float, float]] = []
    cx, cy, cw, ch = x, y, w, h
    row: list[tuple[str, int, float]] = []
    i = 0
    while i < len(row_in):
        side = min(cw, ch)
        if row and _worst([a for _, _, a in row] + [row_in[i][2]], side) > _worst(
            [a for _, _, a in row], side
        ):
            rects, thick, vertical = _layout_row(row, cx, cy, cw, ch)
            placed.extend(rects)
            if vertical:
                cx, cw = cx + thick, cw - thick
            else:
                cy, ch = cy + thick, ch - thick
            row = []
        else:
            row.append(row_in[i])
            i += 1
    if row:
        rects, thick, vertical = _layout_row(row, cx, cy, cw, ch)
        placed.extend(rects)

    out: list[Rect] = []
    for label, value, rx, ry, rw, rh in placed:
        g = gap / 2.0
        rx, ry, rw, rh = rx + g, ry + g, max(0.0, rw - gap), max(0.0, rh - gap)
        pct = value / total * 100.0
        out.append(Rect(
            label=label, value=value, percent=pct,
            x=rx, y=ry, w=rw, h=rh,
            color=PALETTE[len(out) % len(PALETTE)],
            label_xy=(rx, ry) if rw >= 46.0 and rh >= 16.0 else None,
        ))
    return out


def merge_other(items: list[tuple[str, int]], top: int, other_label: str = "Other") -> list[tuple[str, int]]:
    """Keep the ``top`` biggest entries and fold the rest into one slice.

    A pie with 40 slices is unreadable and a treemap with 40 slices is
    unreadable *and* unlabelable; the tail is one number, so show it as one.
    """
    items = [(str(l), int(v)) for l, v in items if int(v) > 0]
    if top <= 0 or len(items) <= top:
        return items
    head, tail = items[:top], items[top:]
    return head + [(other_label, sum(v for _, v in tail))]


def to_dict(items) -> list[dict[str, Any]]:
    """[(label, bytes)] -> [{"label","bytes"}], for embedding in JSON."""
    return [{"label": str(l), "bytes": int(v)} for l, v in items]
