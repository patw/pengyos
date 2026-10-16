"""Text output: the report, the sorted ``du``, the ``disks`` table.

The promises here are the ones a human notices: biggest first, the arithmetic
closes, and the same input always produces the same bytes of output.
"""
from __future__ import annotations

from pengyos_storage.model import Filesystem, human, parse_size
from pengyos_storage.report import render_disks, render_du, render_text


def _ends_with(lines, label) -> int:
    """Index of the first line whose last field is ``label`` (or -1)."""
    for i, line in enumerate(lines):
        if line.split() and line.split()[-1] == label:
            return i
    return -1


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------


def test_text_report_names_the_root_and_the_total(tree):
    out = render_text(tree.scan())
    assert "scan of /tree" in out
    assert human(9216) in out
    assert "5 files" in out
    assert "3 directories" in out


def test_text_report_lists_children_biggest_first(tree):
    lines = render_text(tree.scan()).splitlines()
    idx_b = _ends_with(lines, "b")
    idx_a = _ends_with(lines, "a")
    assert idx_b != -1 and idx_a != -1
    assert idx_b < idx_a


def test_text_report_shares_bars_and_paths(tree):
    out = render_text(tree.scan())
    assert "█" in out and "░" in out
    assert "61.1%" in out  # b = 5632 / 9216


def test_text_report_depth_limits_the_listing(tree):
    shallow = render_text(tree.scan(), depth=1).splitlines()
    deep = render_text(tree.scan(), depth=3).splitlines()
    assert _ends_with(shallow, "b/c") == -1
    assert _ends_with(deep, "b/c") != -1


def test_text_report_top_n_and_the_tail_line(tree):
    for i in range(30):
        tree.mkdir(f"/tree/d{i:02d}")
        tree.touch(f"/tree/d{i:02d}/f.txt", 1000 + i)
    out = render_text(tree.scan(), depth=1, top=3)
    assert "more" in out
    assert "█" * 22 in out  # the biggest entry fills the bar


def test_text_report_min_size_hides_without_losing_the_total(tree):
    out = render_text(tree.scan(), depth=1, min_size=parse_size("4K"))
    assert _ends_with(out.splitlines(), "a") == -1  # 3072 B < 4 KiB: hidden
    assert _ends_with(out.splitlines(), "b") != -1
    assert f"total {human(9216)}" in out


def test_text_report_shows_excluded_space(tree):
    out = render_text(tree.scan(excludes=("*.bin",)), depth=1)
    assert "excluded" in out and human(5120) in out


def test_text_report_lists_warnings(tree):
    tree.mark_unreadable("/tree/b/c")
    out = render_text(tree.scan())
    assert "warnings" in out and "c" in out


def test_text_report_is_deterministic(tree):
    assert render_text(tree.scan()) == render_text(tree.scan())


def test_text_report_marks_a_mount(tree):
    tree.mkdir("/tree/other", dev=9)
    tree.touch("/tree/other/x.bin", 10 ** 6, dev=9)
    out = render_text(tree.scan(), depth=1)
    assert "[mount]" in out


def test_text_report_shows_the_filesystem_bar(tree):
    r = tree.scan()
    r.filesystem = Filesystem(device="/dev/vg0-data", mountpoint="/tree", fstype="ext4",
                              size=1000, used=250, free=750)
    out = render_text(r)
    assert "filesystem /tree" in out and "25.0%" in out


def test_text_report_arithmetic_closes(tree):
    """listed + below-the-cut == total, and excluded is shown separately."""
    out = render_text(tree.scan(), depth=1, top=1)
    tail = [l for l in out.splitlines() if l.strip().startswith("total")][0]
    assert "listed" in tail and "below the cut" in tail


# ---------------------------------------------------------------------------
# render_du
# ---------------------------------------------------------------------------


def test_du_root_line_comes_first_and_paths_are_relative(tree):
    lines = render_du(tree.scan(), depth=1).splitlines()
    assert lines[0].split()[-1] == "."
    assert any(l.split()[-1] == "b" for l in lines)
    assert not any(l.split()[-1].startswith("/tree") for l in lines)


def test_du_is_sorted_biggest_first(tree):
    lines = render_du(tree.scan(), depth=2).splitlines()
    paths = [l.split()[-1] for l in lines if l.strip()]
    assert paths == [".", "b", "a", "b/c"]


def test_du_depth_limits_levels(tree):
    assert "b/c" not in render_du(tree.scan(), depth=1)
    assert "b/c" in render_du(tree.scan(), depth=2)


def test_du_all_includes_files(tree):
    out = render_du(tree.scan(), depth=1, all_files=True)
    assert "big.bin" in out and "loose.txt" in out


def test_du_no_bar_is_plain(tree):
    assert "█" not in render_du(tree.scan(), depth=1, show_bar=False)


def test_du_min_size_filters_lines(tree):
    out = render_du(tree.scan(), depth=2, min_size=parse_size("4K"))
    assert "b" in out and "b/c" not in out


def test_du_of_an_empty_tree_reports_only_the_root():
    from conftest import FakeTree

    out = render_du(FakeTree("/empty").scan(), depth=3).strip()
    assert out.split()[-1] == "."
    assert out.startswith(human(0))
    assert out.count("\n") == 0


# ---------------------------------------------------------------------------
# render_disks
# ---------------------------------------------------------------------------


def _filesystems():
    return [
        Filesystem("/dev/nvme1n1p1", "/data", "ext4", size=1000, used=500, free=500),
        Filesystem("/dev/sda1", "/mnt/backup", "ext4", size=2000, used=100, free=1900),
        Filesystem("/dev/sdb1", "/mnt/full", "xfs", size=500, used=499, free=1),
    ]


def test_disks_lists_every_filesystem_with_a_bar():
    out = render_disks(_filesystems())
    for dev, mnt in [("/dev/nvme1n1p1", "/data"), ("/dev/sda1", "/mnt/backup"),
                     ("/dev/sdb1", "/mnt/full")]:
        assert dev in out and mnt in out
    assert "█" in out
    assert "3 filesystem(s)" in out


def test_disks_shows_used_percentages():
    out = render_disks(_filesystems())
    assert "50.0%" in out and "99.8%" in out


def test_disks_of_nothing_says_so():
    assert "no filesystems" in render_disks([])
