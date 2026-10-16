"""The HTML report: a page that must render with nothing else on the box.

The spec's promise is that the artifact is self-contained — inline CSS, inline
SVG, no scripts, no network references. A report that needs a CDN is not a
report: it is a request for an outage. These tests are the guard.
"""
from __future__ import annotations

import re

from pengyos_storage.html import render_disks_html, render_html
from pengyos_storage.model import Filesystem


def _html(tree, **kwargs):
    return render_html(tree.scan(), **kwargs)


# ---------------------------------------------------------------------------
# Self-containment
# ---------------------------------------------------------------------------


def test_page_is_a_complete_document(tree):
    page = _html(tree)
    assert page.startswith("<!DOCTYPE html>")
    assert "<title>Storage — /tree</title>" in page
    assert page.rstrip().endswith("</html>")


def test_page_has_no_external_references(tree):
    page = _html(tree)
    for forbidden in ("http://", "https://", "//cdn", "@import", "<link", "<img",
                      "<iframe", "<object", "<embed", "url("):
        assert forbidden not in page, f"page pulls in {forbidden}"


def test_page_has_no_script(tree):
    page = _html(tree)
    assert "<script" not in page
    assert "javascript:" not in page
    # No inline event handlers either.
    assert not re.search(r"\son[a-z]+\s*=", page)


def test_page_has_no_third_party_css(tree):
    page = _html(tree)
    assert "<style>" in page  # the CSS is inline
    assert page.count("<style>") == 1


def test_page_carries_the_charts_as_svg(tree):
    page = _html(tree)
    assert page.count("<svg") == 2  # the pie and the treemap
    assert "<path" in page and "<rect" in page


def test_page_shows_the_numbers(tree):
    page = _html(tree)
    assert "/tree" in page
    assert "9.0 KiB" in page  # 9216 bytes
    assert "big.bin" in page  # the top-files table
    assert "Other" not in page  # 3 entries, nothing folded


def test_page_folds_a_long_tail_into_other(tree):
    for i in range(8):
        tree.mkdir(f"/tree/d{i}")
        tree.touch(f"/tree/d{i}/f.txt", 1000 + i)
    page = _html(tree, top=3)
    assert "Other" in page


def test_page_escapes_a_hostile_path(tree):
    # A directory AND a file whose names are markup. A path component cannot
    # contain "/", so the hostile names are slash-free. The depth table, the pie
    # legend, the treemap and the top-files table all render these names, and
    # every one of them must escape them.
    tree.mkdir("/tree/<img src=y>")
    tree.touch("/tree/<img src=y>/payload.bin", 1000)
    tree.touch("/tree/a/<img src=x>", 1000)
    # Positive control: the fixture really is a top-level entry, so the legend
    # and the treemap do render this name.
    assert "<img src=y>" in [c.name for c in tree.scan().root_node.children]
    page = _html(tree)
    assert "<img src=y>" not in page
    assert "<img src=x>" not in page
    assert "&lt;img src=y&gt;" in page
    assert "&lt;img src=x&gt;" in page


def test_page_reports_warnings(tree):
    tree.mark_unreadable("/tree/b/c")
    page = _html(tree)
    assert "Warnings" in page and "c" in page


def test_page_shows_the_filesystem_bar(tree):
    r = tree.scan()
    r.filesystem = Filesystem("/dev/vg0", "/tree", "btrfs", size=1000, used=250, free=750)
    page = render_html(r)
    assert "Filesystem" in page and "25.0%" in page


def test_page_of_an_empty_tree_still_renders(tree):
    from conftest import FakeTree

    page = render_html(FakeTree("/empty").scan())
    assert page.startswith("<!DOCTYPE html>")
    assert "<svg" in page  # an empty donut is still a valid page


def test_page_is_deterministic(tree):
    assert _html(tree) == _html(tree)


def test_page_size_is_bounded(tree):
    for i in range(200):
        tree.touch(f"/tree/many/f{i}.txt", 1000 + i)
    page = _html(tree, top=10)
    assert len(page) < 200_000  # a report, not a dump of the tree


# ---------------------------------------------------------------------------
# The disks page
# ---------------------------------------------------------------------------


def test_disks_page_lists_filesystems():
    fs = [Filesystem("/dev/nvme1n1p1", "/data", "ext4", 1000, 500, 500)]
    page = render_disks_html(fs)
    assert "/dev/nvme1n1p1" in page and "/data" in page
    assert "<script" not in page and "http://" not in page
