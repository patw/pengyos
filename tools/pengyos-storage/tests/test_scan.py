"""The walker: sizes, hard links, mounts, symlinks, exclusions, warnings.

Every case here runs against the synthetic tree from ``conftest.py``, so the
numbers are exact and the suite needs no root, no mount and no network.
"""
from __future__ import annotations

import os
import stat as stat_mod

import pytest

from pengyos_storage import scan_tree
from pengyos_storage.model import BLOCK, FILE_CAP, ScanError
from pengyos_storage.scan import is_excluded

# The fixture tree, in blocks mode (st_blocks * 512):
#   a/one.txt 1000 -> 1024   a/two.txt 2000 -> 2048   => a = 3072
#   b/big.bin 5000 -> 5120   b/c/deep.log 100 -> 512  => b = 5632
#   loose.txt   10 ->  512                            => root self = 512
TOTAL_BLOCKS = 9216
TOTAL_APPARENT = 8110


def _child(node, name):
    """Find a child by name, given a ScanResult or a Node."""
    node = getattr(node, "root_node", node)
    for c in node.children:
        if c.name == name:
            return c
    raise AssertionError(f"no child {name} in {[c.name for c in node.children]}")


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------


def test_totals_and_counts(tree):
    r = tree.scan()
    assert r.mode == "blocks"
    assert r.total_bytes == TOTAL_BLOCKS
    assert r.root_node.total_files == 5
    assert r.root_node.total_dirs == 3
    assert _child(r, "a").bytes == 3072
    assert _child(r, "b").bytes == 5632
    assert _child(_child(r, "b"), "c").bytes == 512


def test_apparent_mode_uses_st_size(tree):
    r = tree.scan(mode="apparent")
    assert r.mode == "apparent"
    assert r.total_bytes == TOTAL_APPARENT


def test_disk_usage_is_at_least_apparent_and_block_aligned(tree):
    r = tree.scan()
    a = tree.scan(mode="apparent")
    assert r.total_bytes >= a.total_bytes
    assert r.total_bytes % BLOCK == 0


def test_children_sorted_biggest_first(tree):
    r = tree.scan()
    sizes = [c.bytes for c in r.root_node.children]
    assert sizes == sorted(sizes, reverse=True)
    assert r.root_node.children[0].name == "b"


def test_files_here_sorted_and_capped(tree):
    for i in range(FILE_CAP * 3):
        tree.touch(f"/tree/many/f{i:04d}.txt", 10 + i)
    r = tree.scan()
    many = _child(r, "many")
    assert many.files == FILE_CAP * 3
    assert len(many.files_here) == FILE_CAP
    # The trim happens after the sum, so the total is still exact.
    assert many.bytes == sum(
        ((10 + i + BLOCK - 1) // BLOCK) * BLOCK for i in range(FILE_CAP * 3)
    )
    assert many.files_here[0].bytes >= many.files_here[-1].bytes


# ---------------------------------------------------------------------------
# Hard links, symlinks
# ---------------------------------------------------------------------------


def test_hard_link_counted_once(tree):
    tree.hardlink("/tree/a/one.copy", "/tree/a/one.txt")
    r = tree.scan()
    # The 1024 blocks are counted once: a stays 3072, total stays 9216.
    assert _child(r, "a").bytes == 3072
    assert r.total_bytes == TOTAL_BLOCKS
    assert r.root_node.total_files == 6  # counted as a file, just worth 0


def test_hard_link_without_dedup_counts_twice(tree):
    tree.hardlink("/tree/a/one.copy", "/tree/a/one.txt")
    r = tree.scan(dedup_hardlinks=False)
    assert _child(r, "a").bytes == 3072 + 1024


def test_symlink_is_never_followed(tree):
    tree.symlink("/tree/a/link", "/tree/b")
    r = tree.scan()
    # The link contributes its own size (0 blocks), and b is not double-counted.
    assert _child(r, "a").bytes == 3072
    assert r.total_bytes == TOTAL_BLOCKS
    # A symlink is a file to the walker, never a directory to descend into,
    # and never a warning.
    assert _child(r, "a").files == 3
    assert _child(r, "a").dirs == 0
    assert r.warnings == []


def test_symlink_apparent_counts_its_own_size(tree):
    tree.symlink("/tree/a/link", "/tree/b", size=12)
    r = tree.scan(mode="apparent")
    assert _child(r, "a").bytes == 1000 + 2000 + 12  # a's files + the link itself


# ---------------------------------------------------------------------------
# Mounts
# ---------------------------------------------------------------------------


def test_one_file_system_does_not_descend_into_a_foreign_device(tree):
    tree.mkdir("/tree/other", dev=42)
    tree.touch("/tree/other/huge.bin", 10 ** 6, dev=42)
    r = tree.scan(one_file_system=True)
    other = _child(r, "other")
    assert other.mount is True
    assert other.bytes == 0
    assert other.children == []
    assert "not scanned" in other.note
    assert r.total_bytes == TOTAL_BLOCKS


def test_without_one_file_system_the_foreign_tree_is_walked_and_marked(tree):
    tree.mkdir("/tree/other", dev=42)
    tree.touch("/tree/other/huge.bin", 10 ** 6, dev=42)
    r = tree.scan(one_file_system=False)
    other = _child(r, "other")
    assert other.mount is True
    assert other.bytes > TOTAL_BLOCKS
    assert r.total_bytes == TOTAL_BLOCKS + other.bytes


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


def test_exclude_basename_pattern(tree):
    r = tree.scan(excludes=("*.bin",))
    assert r.excluded_bytes == 5120
    assert r.total_bytes == TOTAL_BLOCKS - 5120
    assert _child(r, "b").excluded_count == 1


def test_exclude_path_pattern(tree):
    # Excluding a directory excludes its whole subtree, and says so.
    r = tree.scan(excludes=("b/c",))
    assert r.excluded_bytes == 512
    assert r.total_bytes == TOTAL_BLOCKS - 512
    assert _child(r, "b").excluded_count == 1
    assert "c" not in [c.name for c in _child(r, "b").children]


def test_is_excluded_matches_basename_and_path():
    assert is_excluded("a/one.txt", "one.txt", ("*.txt",))
    assert is_excluded("a/one.txt", "one.txt", ("a/*",))
    assert not is_excluded("a/one.txt", "one.txt", ("b/*",))
    assert not is_excluded("a/one.txt", "one.txt", ())


def test_excluded_space_is_visible_not_dropped(tree):
    r = tree.scan(excludes=("*.bin",))
    root = r.root_node
    listed = root.self_bytes + sum(c.bytes for c in root.children)
    assert listed + r.excluded_bytes == TOTAL_BLOCKS
    assert r.excluded_bytes == 5120


# ---------------------------------------------------------------------------
# Failures that are not failures
# ---------------------------------------------------------------------------


def test_unreadable_directory_is_a_warning_not_a_crash(tree):
    tree.mkdir("/tree/locked")
    tree.touch("/tree/locked/secret.bin", 4096)
    tree.mark_unreadable("/tree/locked")
    r = tree.scan()
    assert r.warnings and "locked" in r.warnings[0]
    assert r.total_bytes == TOTAL_BLOCKS  # the unreadable space is simply unknown


def test_missing_root_raises_scan_error():
    with pytest.raises(ScanError):
        scan_tree("/definitely/not/here", filesystem=False)


def test_root_that_is_a_file_raises_scan_error(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("x")
    with pytest.raises(ScanError):
        scan_tree(str(f), filesystem=False)


def test_bad_mode_is_a_value_error(tree):
    with pytest.raises(ValueError):
        tree.scan(mode="wat")


# ---------------------------------------------------------------------------
# Determinism + the real filesystem
# ---------------------------------------------------------------------------


def test_two_scans_of_an_unchanged_tree_are_identical(tree):
    a = tree.scan()
    b = tree.scan()
    assert a.to_dict() == b.to_dict()


def test_the_json_document_carries_no_timing_field(tree):
    """A scan duration is not part of the tree; a diff-able document needs that."""
    doc = tree.scan().to_dict()
    assert "elapsed_s" not in doc
    assert set(doc) == {
        "tool", "version", "schema", "root", "mode", "generated", "total_bytes",
        "file_count", "dir_count", "excluded_bytes", "warnings", "filesystem", "tree",
    }


def test_real_tree_sizes_are_block_aligned(real_tree):
    r = scan_tree(str(real_tree), filesystem=False)
    assert r.total_bytes % BLOCK == 0
    assert r.total_bytes >= 5000 + 700 + 100
    assert r.root_node.total_files == 3
    assert r.root_node.total_dirs == 2


def test_scan_never_writes_anything(real_tree):
    before = sorted(os.listdir(real_tree))
    scan_tree(str(real_tree), filesystem=False)
    assert sorted(os.listdir(real_tree)) == before
