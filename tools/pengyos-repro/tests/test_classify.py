"""Tests for path classification:

Classify each path: identical / differs / only-in-A / only-in-B.
"""

import hashlib

from pengyos_repro import classify as mod
from pengyos_repro.classify import (
    DIFFERS,
    IDENTICAL,
    ONLY_IN_A,
    ONLY_IN_B,
    Diff,
    DiffEntry,
    classify,
    classify_trees,
)
from pengyos_repro.manifest import FileEntry, manifest


def _entry(data: bytes) -> FileEntry:
    """Build a FileEntry from raw bytes (size + sha256)."""
    return FileEntry(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def _empty() -> dict:
    return {}


# --- the four buckets -------------------------------------------------------


def test_classify_all_identical():
    a = {"a.txt": _entry(b"hello\n"), "sub/b": _entry(b"world")}
    b = {"a.txt": _entry(b"hello\n"), "sub/b": _entry(b"world")}
    diff = classify(a, b)
    assert diff.identical == ["a.txt", "sub/b"]
    assert diff.differing == []
    assert diff.only_in_a == []
    assert diff.only_in_b == []
    assert diff.counts() == {IDENTICAL: 2, DIFFERS: 0, ONLY_IN_A: 0, ONLY_IN_B: 0}


def test_classify_detects_differing_file():
    a = {"f.txt": _entry(b"one")}
    b = {"f.txt": _entry(b"two")}
    diff = classify(a, b)
    assert diff.identical == []
    assert len(diff.differing) == 1
    assert diff.differing[0].path == "f.txt"
    assert diff.differing[0].reason  # a non-empty human-readable reason


def test_classify_only_in_a():
    a = {"a.txt": _entry(b"x"), "gone_from_b.txt": _entry(b"y")}
    b = {"a.txt": _entry(b"x")}
    diff = classify(a, b)
    assert diff.identical == ["a.txt"]
    assert diff.only_in_a == ["gone_from_b.txt"]
    assert diff.only_in_b == []


def test_classify_only_in_b():
    a = {"a.txt": _entry(b"x")}
    b = {"a.txt": _entry(b"x"), "only_here.txt": _entry(b"z")}
    diff = classify(a, b)
    assert diff.only_in_a == []
    assert diff.only_in_b == ["only_here.txt"]


def test_classify_missing_bubbles_up_as_missing():
    """The report's ``missing`` list is only-in-A plus only-in-B."""
    a = {"a.txt": _entry(b"x"), "left.txt": _entry(b"l")}
    b = {"a.txt": _entry(b"x"), "right.txt": _entry(b"r")}
    diff = classify(a, b)
    assert sorted(diff.missing) == ["left.txt", "right.txt"]
    assert diff.only_in_a == ["left.txt"]
    assert diff.only_in_b == ["right.txt"]


def test_classify_empty_manifests():
    diff = classify(_empty(), _empty())
    assert diff.identical == []
    assert diff.differing == []
    assert diff.only_in_a == []
    assert diff.only_in_b == []
    assert diff.counts() == {IDENTICAL: 0, DIFFERS: 0, ONLY_IN_A: 0, ONLY_IN_B: 0}


def test_classify_only_one_side_nonempty():
    a = {"a.txt": _entry(b"x"), "b.txt": _entry(b"y")}
    diff = classify(a, _empty())
    assert diff.identical == []
    assert diff.differing == []
    assert diff.only_in_a == ["a.txt", "b.txt"]
    assert diff.only_in_b == []


# --- the reason for a differing file ----------------------------------------


def test_diff_reason_reports_size_mismatch():
    a = {"big.txt": _entry(b"a" * 100)}
    b = {"big.txt": _entry(b"a" * 99)}
    (entry,) = classify(a, b).differing
    assert entry.reason == "size differs: 100 vs 99"


def test_diff_reason_reports_content_when_sizes_equal():
    a = {"f.txt": _entry(b"aaa")}
    b = {"f.txt": _entry(b"bbb")}
    (entry,) = classify(a, b).differing
    assert entry.reason == "content differs"


def test_diff_reason_is_a_diff_entry():
    a = {"f.txt": _entry(b"aaa")}
    b = {"f.txt": _entry(b"bbb")}
    (entry,) = classify(a, b).differing
    assert isinstance(entry, DiffEntry)
    assert entry.path == "f.txt"


# --- ordering and mixed cases -----------------------------------------------


def test_classify_outputs_are_sorted():
    a = {
        "z.txt": _entry(b"1"),
        "a.txt": _entry(b"1"),
        "m/medium.txt": _entry(b"1"),
        "only_a.txt": _entry(b"2"),
    }
    b = {
        "z.txt": _entry(b"1"),
        "a.txt": _entry(b"2"),
        "m/medium.txt": _entry(b"1"),
        "only_b.txt": _entry(b"2"),
    }
    diff = classify(a, b)
    assert diff.identical == ["m/medium.txt", "z.txt"]
    assert [e.path for e in diff.differing] == ["a.txt"]
    assert diff.only_in_a == ["only_a.txt"]
    assert diff.only_in_b == ["only_b.txt"]


def test_classify_mixed_bucket():
    a = {
        "same.txt": _entry(b"same"),
        "changed.txt": _entry(b"old"),
        "gone.txt": _entry(b"gone"),
    }
    b = {
        "same.txt": _entry(b"same"),
        "changed.txt": _entry(b"new"),
        "added.txt": _entry(b"added"),
    }
    diff = classify(a, b)
    assert diff.identical == ["same.txt"]
    assert [e.path for e in diff.differing] == ["changed.txt"]
    assert diff.only_in_a == ["gone.txt"]
    assert diff.only_in_b == ["added.txt"]
    assert diff.counts() == {IDENTICAL: 1, DIFFERS: 1, ONLY_IN_A: 1, ONLY_IN_B: 1}


def test_classify_no_path_in_two_buckets():
    """Every path lands in exactly one bucket."""
    a = {"a.txt": _entry(b"x"), "b.txt": _entry(b"old"), "gone.txt": _entry(b"g")}
    b = {"a.txt": _entry(b"x"), "b.txt": _entry(b"new"), "added.txt": _entry(b"n")}
    diff = classify(a, b)
    all_paths = (
        list(diff.identical)
        + [e.path for e in diff.differing]
        + list(diff.only_in_a)
        + list(diff.only_in_b)
    )
    assert len(all_paths) == len(set(all_paths)) == 4


# --- composition with the manifest walker -----------------------------------


def test_classify_composes_with_manifest(tmp_path):
    """End-to-end: walk two real trees on disk and classify them.

    The two roots are *sibling* directories: neither may nest inside the other,
    or walking one root would absorb the other's files.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "same.txt").write_bytes(b"identical")
    (a / "changed.txt").write_bytes(b"first build")
    (a / "gone.txt").write_bytes(b"only in A")
    (b / "same.txt").write_bytes(b"identical")
    (b / "changed.txt").write_bytes(b"second build")
    (b / "added.txt").write_bytes(b"only in B")

    diff = classify(manifest(str(a)), manifest(str(b)))
    assert diff.identical == ["same.txt"]
    assert [e.path for e in diff.differing] == ["changed.txt"]
    assert diff.only_in_a == ["gone.txt"]
    assert diff.only_in_b == ["added.txt"]


def test_classify_trees_walks_trees(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "a.txt").write_bytes(b"same")
    (b / "a.txt").write_bytes(b"same")
    (b / "b.txt").write_bytes(b"extra")

    diff = classify_trees(str(a), str(b))
    assert diff.identical == ["a.txt"]
    assert diff.only_in_b == ["b.txt"]


def test_classify_trees_handles_identical_trees(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "sub").mkdir()
    (b / "sub").mkdir()
    (a / "keep.txt").write_bytes(b"content")
    (a / "sub/nested.bin").write_bytes(b"\x00\x01\x02" * 10)
    (b / "keep.txt").write_bytes(b"content")
    (b / "sub/nested.bin").write_bytes(b"\x00\x01\x02" * 10)

    diff = classify_trees(str(a), str(b))
    assert set(diff.identical) == {"keep.txt", "sub/nested.bin"}
    assert diff.differing == []
    assert diff.only_in_a == []
    assert diff.only_in_b == []


def test_classify_ignores_symlinks_like_manifest(tmp_path):
    """classify_trees must not choke on symlinks (manifest skips them)."""
    import os

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "real.txt").write_bytes(b"data")
    os.symlink(a / "real.txt", a / "link.txt")
    (b / "real.txt").write_bytes(b"data")

    diff = classify_trees(str(a), str(b))
    assert diff.identical == ["real.txt"]
    assert diff.only_in_a == []  # the symlink was skipped on both sides
