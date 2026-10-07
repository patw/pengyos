"""Tests for ignore-pattern matching and heuristics:

Heuristic classifiers for known nondeterminism sources (build timestamps,
embedded user, random seeds) via configurable ignore patterns.
"""

import hashlib

import pytest

from pengyos_repro import patterns as mod
from pengyos_repro.patterns import (
    HEURISTIC_EMBEDDED_USER,
    HEURISTIC_RANDOM_SEEDS,
    HEURISTIC_TIMESTAMPS,
    HEURISTICS,
    Matcher,
    PatternError,
    expand,
    filter_manifest,
    matches_any,
)
from pengyos_repro.classify import classify
from pengyos_repro.manifest import FileEntry


def _entry(data: bytes) -> FileEntry:
    return FileEntry(size=len(data), sha256=hashlib.sha256(data).hexdigest())


# --- wildcard semantics -----------------------------------------------------


def test_star_matches_within_segment_only():
    # A slash-free pattern is a basename match: it matches at any depth, but
    # the ``*`` inside it still must not cross a ``/``.
    assert matches_any(["*.c"], "foo.c")
    assert matches_any(["*.c"], "sub/foo.c")  # basename at any depth
    # An anchored ``a/*.c`` matches the direct child only (``*`` never crosses
    # a ``/``), so the nested file does not match.
    assert matches_any(["a/*.c"], "a/foo.c")
    assert not matches_any(["a/*.c"], "a/sub/foo.c")


def test_star_matches_nested_when_anchored():
    # ``a/*.c`` is anchored: only the direct child matches.
    assert matches_any(["a/*.c"], "a/foo.c")
    assert not matches_any(["a/*.c"], "a/sub/foo.c")


def test_double_star_matches_across_segments():
    assert matches_any(["**/*.c"], "foo.c")  # at root
    assert matches_any(["**/*.c"], "a/b/c/foo.c")  # deep


def test_question_mark_matches_single_char():
    assert matches_any(["file?.txt"], "file1.txt")
    assert not matches_any(["file?.txt"], "file12.txt")
    assert not matches_any(["file?.txt"], "file/.txt")  # not across /


def test_char_class():
    assert matches_any(["[abc].txt"], "a.txt")
    assert matches_any(["[abc].txt"], "c.txt")
    assert not matches_any(["[abc].txt"], "d.txt")


def test_char_class_negation():
    assert matches_any(["[!abc].txt"], "d.txt")
    assert not matches_any(["[!abc].txt"], "a.txt")


def test_char_class_literal_bracket():
    # A ] right after [ is literal.
    assert matches_any(["[]].txt"], "].txt")
    assert not matches_any(["[]].txt"], "x].txt")


def test_char_class_range():
    assert matches_any(["[a-z].txt"], "m.txt")
    assert not matches_any(["[a-z].txt"], "M.txt")


# --- anchoring ------------------------------------------------------------


def test_basename_pattern_matches_at_any_depth():
    # No slash -> matches the basename wherever it sits.
    assert matches_any(["foo.so"], "foo.so")
    assert matches_any(["foo.so"], "usr/lib/foo.so")
    assert matches_any(["foo.so"], "a/b/c/foo.so")


def test_anchored_pattern_matches_full_path_only():
    assert matches_any(["usr/lib/foo.so"], "usr/lib/foo.so")
    assert not matches_any(["usr/lib/foo.so"], "lib/foo.so")
    assert not matches_any(["usr/lib/foo.so"], "usr/lib/foo.so.bak")


def test_double_star_trailing_matches_everything_under():
    assert matches_any(["build/**"], "build/x")
    assert matches_any(["build/**"], "build/x/y/z")
    # ``build`` itself is not matched by ``build/**`` (needs contents).
    assert not matches_any(["build/**"], "build")


# --- directory pruning ------------------------------------------------------


def test_directory_prune_matches_dir_and_descendants():
    assert matches_any(["build/"], "build")
    assert matches_any(["build/"], "build/x")
    assert matches_any(["build/"], "build/x/y")


def test_directory_prune_matches_at_any_depth():
    # A slash-free ``build/`` matches a directory named ``build`` at any depth
    # (gitignore semantics): the dir itself and everything beneath it.
    assert matches_any(["build/"], "build/x")
    assert matches_any(["build/"], "src/build/x")  # a build/ deeper in the tree
    assert not matches_any(["build/"], "src/lib/x")  # nothing named build/


def test_directory_prune_basename_at_any_depth():
    # ``__pycache__/`` as a directory prefix matches at any depth.
    assert matches_any(["__pycache__/"], "__pycache__/foo.pyc")
    assert matches_any(["__pycache__/"], "pkg/__pycache__/foo.pyc")


# --- errors -----------------------------------------------------------------


def test_empty_pattern_raises():
    # An empty *pattern* (an empty entry in the list) is rejected; a bare empty
    # string is treated as "no patterns" because it iterates to nothing.
    with pytest.raises(PatternError):
        Matcher([""])


def test_only_slashes_raises():
    with pytest.raises(PatternError):
        Matcher("///")


def test_unterminated_char_class_raises():
    with pytest.raises(PatternError):
        Matcher("[abc")


# --- heuristic expansion ----------------------------------------------------


def test_expand_leaves_plain_patterns_unchanged():
    assert expand(["*.so", "build/"]) == ["*.so", "build/"]


def test_expand_timestamps():
    out = expand(["heuristic:timestamps"])
    assert out == HEURISTIC_TIMESTAMPS
    assert "*.pyc" in out


def test_expand_all_three_heuristics():
    out = expand(
        [
            "heuristic:timestamps",
            "heuristic:embedded-user",
            "heuristic:random-seeds",
        ]
    )
    assert set(out) == (
        set(HEURISTIC_TIMESTAMPS)
        | set(HEURISTIC_EMBEDDED_USER)
        | set(HEURISTIC_RANDOM_SEEDS)
    )


def test_expand_case_insensitive_name():
    assert expand(["heuristic:TIMESTAMPS"]) == HEURISTIC_TIMESTAMPS


def test_expand_mixed_plain_and_heuristic():
    out = expand(["*.log", "heuristic:random-seeds"])
    assert out[0] == "*.log"
    assert out[1:] == HEURISTIC_RANDOM_SEEDS


def test_expand_unknown_heuristic_raises():
    with pytest.raises(PatternError) as excinfo:
        expand(["heuristic:nope"])
    assert "nope" in str(excinfo.value)


def test_heuristics_registry_is_complete():
    # Every curated list is registered under a stable name.
    assert HEURISTICS["timestamps"] == HEURISTIC_TIMESTAMPS
    assert HEURISTICS["embedded-user"] == HEURISTIC_EMBEDDED_USER
    assert HEURISTICS["random-seeds"] == HEURISTIC_RANDOM_SEEDS


def test_heuristics_target_the_named_sources():
    # Timestamps: python bytecode.
    assert any("pyc" in p for p in HEURISTIC_TIMESTAMPS)
    # Embedded user: archives / object files.
    assert any(p.endswith("*.a") for p in HEURISTIC_EMBEDDED_USER)
    assert any(p.endswith("*.so") for p in HEURISTIC_EMBEDDED_USER)
    # Random seeds: seed files.
    assert any("random-seed" in p for p in HEURISTIC_RANDOM_SEEDS)


# --- filter_manifest --------------------------------------------------------


def test_filter_manifest_removes_matching_paths():
    m = {
        "keep.txt": _entry(b"k"),
        "noise.pyc": _entry(b"n"),
        "lib/noise.so": _entry(b"s"),
    }
    out = filter_manifest(m, ["*.pyc", "**/*.so"])
    assert set(out) == {"keep.txt"}


def test_filter_manifest_keeps_everything_when_no_match():
    m = {"a.txt": _entry(b"a"), "b/c.txt": _entry(b"c")}
    out = filter_manifest(m, ["*.xyz"])
    assert set(out) == {"a.txt", "b/c.txt"}


def test_filter_manifest_empty_patterns_is_noop():
    m = {"a.txt": _entry(b"a")}
    out = filter_manifest(m, [])
    assert out == m


def test_filter_manifest_expands_heuristics():
    m = {
        "main.o": _entry(b"obj"),
        "data.bin": _entry(b"bin"),
    }
    out = filter_manifest(m, ["heuristic:embedded-user"])
    assert set(out) == {"data.bin"}


def test_filter_manifest_is_pure():
    m = {"a.pyc": _entry(b"a")}
    filter_manifest(m, ["*.pyc"])
    # The input manifest is not mutated.
    assert set(m) == {"a.pyc"}


# --- Matcher object contract ------------------------------------------------


def test_matcher_len_and_predicate():
    mt = Matcher(["*.so", "build/"])
    assert len(mt) == 2
    assert mt.is_ignored("x.so")
    assert mt.is_ignored("build/out")
    assert not mt.is_ignored("keep.c")


def test_matcher_empty_is_noop():
    mt = Matcher([])
    assert len(mt) == 0
    assert not mt.is_ignored("anything")


# --- composition with classification (end-to-end noise hiding) --------------


def test_hiding_nondeterministic_file_restores_identical():
    """Two trees that differ only in a noisy file classify as identical once
    that file is filtered out — the whole point of the heuristics."""
    noisy = b"bytecode \x00 mtime"
    a = {"main.c": _entry(b"source"), "main.pyc": _entry(noisy)}
    b = {"main.c": _entry(b"source"), "main.pyc": _entry(noisy + b"x")}

    # Without hiding: the .pyc differs.
    diff = classify(a, b)
    assert [e.path for e in diff.differing] == ["main.pyc"]

    # With the timestamps heuristic: identical.
    filtered_a = filter_manifest(a, ["heuristic:timestamps"])
    filtered_b = filter_manifest(b, ["heuristic:timestamps"])
    diff2 = classify(filtered_a, filtered_b)
    assert diff2.identical == ["main.c"]
    assert diff2.differing == []


def test_hiding_random_seed_raises_effective_score():
    """Filtering a random-seed file removes it from the missing/differing set,
    which is what lifts the reproducibility score (score itself is the next
    subtask)."""
    a = {"payload": _entry(b"same"), "random-seed": _entry(b"aaa")}
    b = {"payload": _entry(b"same"), "random-seed": _entry(b"zzz")}

    diff = classify(a, b)
    assert [e.path for e in diff.differing] == ["random-seed"]

    filtered_a = filter_manifest(a, ["heuristic:random-seeds"])
    filtered_b = filter_manifest(b, ["heuristic:random-seeds"])
    diff2 = classify(filtered_a, filtered_b)
    assert diff2.identical == ["payload"]
    assert diff2.differing == []
