"""Chart geometry: the pie angles and the treemap areas.

These are the promises the spec makes about the pictures, asserted directly:
slices sum to 360°, a fat slice uses the large-arc flag, treemap rectangles are
inside the canvas, do not overlap, and their areas are proportional to bytes.
"""
from __future__ import annotations

import math

import pytest

from pengyos_storage.chart import MIN_LABEL_PCT, PALETTE, merge_other, pie, treemap


def _area(r) -> float:
    return r.w * r.h


# ---------------------------------------------------------------------------
# Pie
# ---------------------------------------------------------------------------


def test_pie_is_empty_for_nothing_to_show():
    assert pie([]) == []
    assert pie([("zero", 0)]) == []


def test_pie_slices_sum_to_a_full_circle():
    slices = pie([("a", 10), ("b", 20), ("c", 30), ("d", 40)])
    sweeps = [s.end_deg - s.start_deg for s in slices]
    assert sum(sweeps) == pytest.approx(360.0)
    assert slices[0].start_deg == -90.0  # 12 o'clock
    assert slices[-1].end_deg == pytest.approx(270.0)


def test_pie_slices_are_contiguous():
    slices = pie([("a", 3), ("b", 5), ("c", 2)])
    for prev, nxt in zip(slices, slices[1:]):
        assert prev.end_deg == nxt.start_deg


def test_pie_percentages_sum_to_100():
    slices = pie([("a", 7), ("b", 11), ("c", 5)])
    assert sum(s.percent for s in slices) == pytest.approx(100.0)


def test_pie_large_arc_flag_only_for_a_fat_slice():
    slices = pie([("big", 70), ("small", 30)])
    big, small = slices
    assert " 1 1 " in big.path  # sweep 252° -> large-arc-flag = 1
    assert " 0 1 " in small.path  # sweep 108° -> 0


def test_pie_single_slice_draws_a_full_ring():
    slices = pie([("all", 42)])
    assert len(slices) == 1
    d = slices[0].path
    # Two arcs: an arc from a point back to itself renders nothing.
    assert d.count("A ") == 2
    assert slices[0].percent == 100.0


def test_pie_colours_cycle_from_the_palette():
    slices = pie([(f"n{i}", 1) for i in range(len(PALETTE) + 3)])
    assert slices[0].color == slices[len(PALETTE)].color
    assert len({s.color for s in slices[: len(PALETTE)]}) == len(PALETTE)


def test_pie_labels_only_where_text_would_fit():
    slices = pie([("big", 90), ("tiny", 1)])
    labelled = {s.label: s.label_xy for s in slices}
    assert labelled["big"] is not None
    assert labelled["tiny"] is None  # 1% wedge: a label would spill out
    for s in slices:
        if s.label_xy is not None:
            assert s.percent >= MIN_LABEL_PCT


def test_pie_geometry_is_finite_and_inside_the_canvas():
    cx, cy, r = 170.0, 170.0, 150.0
    for s in pie([("a", 5), ("b", 6), ("c", 7)]):
        nums = [float(t) for t in s.path.replace("M", " ").replace("L", " ")
                .replace("A", " ").replace("Z", " ").split()]
        assert all(math.isfinite(n) for n in nums)
        coords = nums[0:2] + nums[-2:]
        assert all(abs(n) < 1e6 for n in coords)


# ---------------------------------------------------------------------------
# Treemap
# ---------------------------------------------------------------------------

W, H = 900.0, 320.0


def test_treemap_is_empty_for_nothing_to_show():
    assert treemap([], 0, 0, W, H) == []
    assert treemap([("zero", 0)], 0, 0, W, H) == []
    assert treemap([("a", 1)], 0, 0, 0, H) == []


def test_treemap_areas_are_proportional_to_bytes():
    items = [("a", 50), ("b", 30), ("c", 15), ("d", 5)]
    rects = treemap(items, 0, 0, W, H)
    total = sum(v for _, v in items)
    canvas = W * H
    for r, (_, value) in zip(rects, items):
        expected = value / total * canvas
        assert _area(r) == pytest.approx(expected, rel=0.01)


def test_treemap_rects_stay_inside_the_canvas():
    rects = treemap([(f"n{i}", 100 - i) for i in range(12)], 0, 0, W, H)
    for r in rects:
        assert r.x >= -1e-9 and r.y >= -1e-9
        assert r.x + r.w <= W + 1e-9
        assert r.y + r.h <= H + 1e-9


def test_treemap_rects_do_not_overlap():
    rects = treemap([(f"n{i}", 200 - i * 7) for i in range(10)], 0, 0, W, H)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            overlap_x = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
            overlap_y = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
            assert not (overlap_x > 1e-6 and overlap_y > 1e-6), (a.label, b.label)


def test_treemap_covers_the_canvas():
    rects = treemap([(f"n{i}", 50 - i) for i in range(8)], 0, 0, W, H)
    assert sum(_area(r) for r in rects) == pytest.approx(W * H, rel=0.01)


def test_treemap_gap_shrinks_rects_without_leaving_the_canvas():
    rects = treemap([("a", 60), ("b", 40)], 0, 0, W, H, gap=4)
    for r in rects:
        assert r.x >= 0 and r.y >= 0
        assert r.x + r.w <= W and r.y + r.h <= H
        assert r.w > 0 and r.h > 0


def test_treemap_labels_only_where_text_would_fit():
    rects = treemap([("huge", 1000), ("sliver", 1)], 0, 0, W, H)
    by = {r.label: r for r in rects}
    assert by["huge"].label_xy is not None
    assert by["sliver"].label_xy is None


def test_treemap_is_squarish():
    # The point of squarify: no long thin slivers for a normal workload.
    rects = treemap([(f"n{i}", 100 - i * 3) for i in range(10)], 0, 0, W, H)
    ratios = [max(r.w / r.h, r.h / r.w) for r in rects if r.w > 0 and r.h > 0]
    assert max(ratios) < 8.0


# ---------------------------------------------------------------------------
# Merging the tail
# ---------------------------------------------------------------------------


def test_merge_other_folds_the_tail_into_one_slice():
    items = [(f"n{i}", 100 - i) for i in range(10)]
    merged = merge_other(items, 3)
    assert [label for label, _ in merged][-1] == "Other"
    assert sum(v for _, v in merged) == sum(v for _, v in items)
    assert len(merged) == 4


def test_merge_other_leaves_a_short_list_alone():
    items = [("a", 3), ("b", 2)]
    assert merge_other(items, 15) == items
    assert merge_other(items, 0) == items


def test_merge_other_drops_zero_entries():
    assert merge_other([("a", 5), ("b", 0)], 15) == [("a", 5)]


# ---------------------------------------------------------------------------
# The two views agree
# ---------------------------------------------------------------------------


def test_pie_and_treemap_describe_the_same_numbers():
    items = [("a", 40), ("b", 35), ("c", 25)]
    slices = pie(items)
    rects = treemap(items, 0, 0, W, H)
    assert [s.label for s in slices] == [r.label for r in rects]
    assert [s.value for s in slices] == [r.value for r in rects]
    assert sum(s.percent for s in slices) == pytest.approx(
        sum(r.percent for r in rects)
    )
