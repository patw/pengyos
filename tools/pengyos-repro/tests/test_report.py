"""Tests for reproducibility score and report emission.

Covers ``pengyos_repro/report.py``:

* :func:`score` — the reproducibility score in ``[0.0, 1.0]``,
* :func:`report_dict` — the machine-readable ``{score, identical, differing,
  missing}`` report (the fixed JSON schema),
* :func:`format_report` — the human-readable multi-line report,
* :func:`emit` / :func:`dumps_report` — the emission helpers.
"""

import io
import json

from pengyos_repro.classify import classify
from pengyos_repro.manifest import FileEntry
from pengyos_repro.report import (
    SCORE_PRECISION,
    dumps_report,
    emit,
    format_report,
    report_dict,
    score,
)


def _entry(data: bytes) -> FileEntry:
    return FileEntry(size=len(data), sha256=__import__("hashlib").sha256(data).hexdigest())


# --- score ------------------------------------------------------------------


def test_score_identical_trees_is_one():
    a = {"a.txt": _entry(b"x"), "sub/b": _entry(b"y")}
    b = {"a.txt": _entry(b"x"), "sub/b": _entry(b"y")}
    assert score(classify(a, b)) == 1.0


def test_score_empty_diff_is_one():
    """No paths to compare is vacuously reproducible (not a divide-by-zero)."""
    assert score(classify({}, {})) == 1.0


def test_score_differing_below_one():
    a = {"a.txt": _entry(b"x"), "b.txt": _entry(b"one")}
    b = {"a.txt": _entry(b"x"), "b.txt": _entry(b"two")}
    s = score(classify(a, b))
    assert s < 1.0
    # one differing of two total paths -> 1/2
    assert s == round(1 / 2, SCORE_PRECISION)


def test_score_fraction_is_identical_over_total():
    a = {
        "same1": _entry(b"s"),
        "same2": _entry(b"s"),
        "same3": _entry(b"s"),
        "diff": _entry(b"one"),
        "gone": _entry(b"g"),
    }
    b = {
        "same1": _entry(b"s"),
        "same2": _entry(b"s"),
        "same3": _entry(b"s"),
        "diff": _entry(b"two"),
        "added": _entry(b"a"),
    }
    # 3 identical out of 6 total (diff differs, gone only-in-a, added only-in-b)
    assert score(classify(a, b)) == round(3 / 6, SCORE_PRECISION)


def test_score_missing_counts_against():
    """A path present in only one tree lowers the score like a differing one."""
    a = {"keep": _entry(b"k"), "left": _entry(b"l")}
    b = {"keep": _entry(b"k")}
    s = score(classify(a, b))
    assert s == round(1 / 2, SCORE_PRECISION)
    assert s < 1.0


def test_score_is_rounded():
    """The score is rounded to the documented precision for stable output."""
    a = {"a": _entry(b"x" * 10)}
    b = {"a": _entry(b"y" * 10)}
    s = score(classify(a, b))
    assert s == round(s, SCORE_PRECISION)
    assert s == round(0.0, SCORE_PRECISION)  # fully differing -> 0


def test_score_bounds_in_unit_interval():
    for a, b in [
        ({}, {}),
        ({"a": _entry(b"x")}, {"a": _entry(b"x")}),
        ({"a": _entry(b"x")}, {"a": _entry(b"y")}),
        ({"a": _entry(b"x"), "b": _entry(b"y")}, {}),
    ]:
        s = score(classify(a, b))
        assert 0.0 <= s <= 1.0


# --- report_dict ------------------------------------------------------------


def test_report_dict_has_exactly_the_documented_keys():
    diff = classify({"a": _entry(b"x")}, {"a": _entry(b"y")})
    keys = set(report_dict(diff))
    assert keys == {"score", "identical", "differing", "missing"}


def test_report_dict_contents_match_diff():
    diff = classify(
        {"same": _entry(b"s"), "changed": _entry(b"one"), "gone": _entry(b"g")},
        {"same": _entry(b"s"), "changed": _entry(b"two"), "added": _entry(b"a")},
    )
    rep = report_dict(diff)
    assert rep["score"] == round(1 / 4, SCORE_PRECISION)
    assert rep["identical"] == ["same"]
    assert rep["differing"] == [{"path": "changed", "reason": "content differs"}]
    # only-in-A (gone) then only-in-B (added)
    assert rep["missing"] == ["gone", "added"]


def test_report_dict_differing_has_path_and_reason():
    a = {"f": _entry(b"aaa")}
    b = {"f": _entry(b"bbb")}
    (entry,) = report_dict(classify(a, b))["differing"]
    assert set(entry) == {"path", "reason"}
    assert entry["path"] == "f"
    assert entry["reason"]  # non-empty human-readable reason


def test_report_dict_missing_aggregates_both_sides():
    """missing is only-in-A (sorted) then only-in-B (sorted) — not globally
    re-sorted."""
    a = {"m-b": _entry(b"l"), "m-a": _entry(b"l2")}
    b = {"z-only-b": _entry(b"r"), "a-only-b": _entry(b"r2")}
    rep = report_dict(classify(a, b))
    assert rep["missing"] == ["m-a", "m-b", "a-only-b", "z-only-b"]


def test_report_dict_lists_are_sorted():
    a = {"z": _entry(b"1"), "a": _entry(b"1"), "gone": _entry(b"2")}
    b = {"z": _entry(b"1"), "a": _entry(b"2")}
    rep = report_dict(classify(a, b))
    assert rep["identical"] == sorted(rep["identical"])
    # only-in-A then only-in-B, each side sorted
    assert rep["missing"] == ["gone"]


def test_report_dict_identical_empty_when_all_differ():
    a = {"a": _entry(b"1")}
    b = {"a": _entry(b"2")}
    rep = report_dict(classify(a, b))
    assert rep["identical"] == []
    assert len(rep["differing"]) == 1


# --- format_report ----------------------------------------------------------


def test_format_report_identical_is_reproducible(tmp_path):
    a = {"a.txt": _entry(b"x")}
    b = {"a.txt": _entry(b"x")}
    text = format_report(classify(a, b))
    assert "REPRODUCIBLE" in text
    assert "1.0" in text


def test_format_report_not_reproducible_lists_differing():
    a = {"a.txt": _entry(b"x"), "changed": _entry(b"one")}
    b = {"a.txt": _entry(b"x"), "changed": _entry(b"two")}
    text = format_report(classify(a, b))
    assert "NOT REPRODUCIBLE" in text
    assert "changed" in text
    assert "content differs" in text


def test_format_report_reports_size_mismatch():
    a = {"big.txt": _entry(b"a" * 100)}
    b = {"big.txt": _entry(b"a" * 99)}
    text = format_report(classify(a, b))
    assert "size differs: 100 vs 99" in text


def test_format_report_lists_missing_with_origin():
    a = {"a.txt": _entry(b"x"), "left.txt": _entry(b"l")}
    b = {"a.txt": _entry(b"x"), "right.txt": _entry(b"r")}
    text = format_report(classify(a, b))
    assert "Missing" in text
    assert "only in A" in text
    assert "only in B" in text


def test_format_report_has_counts_line():
    a = {"a.txt": _entry(b"x"), "gone": _entry(b"g")}
    b = {"a.txt": _entry(b"x"), "added": _entry(b"a")}
    text = format_report(classify(a, b))
    assert "Identical: 1" in text
    assert "Differing: 0" in text
    assert "Missing: 2" in text


# --- emit / dumps -----------------------------------------------------------


def test_dumps_report_matches_json_dump_of_dict():
    diff = classify({"a": _entry(b"x")}, {"a": _entry(b"y")})
    assert json.dumps(report_dict(diff)) == json.dumps(json.loads(dumps_report(diff)))


def test_emit_writes_json_file(tmp_path):
    diff = classify({"a.txt": _entry(b"x")}, {"a.txt": _entry(b"y")})
    out = tmp_path / "report.json"
    rep = emit(diff, json_out=str(out))
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == rep
    assert set(written) == {"score", "identical", "differing", "missing"}


def test_emit_prints_human_report():
    diff = classify({"a.txt": _entry(b"x")}, {"a.txt": _entry(b"y")})
    stream = io.StringIO()
    emit(diff, stream=stream)
    assert "NOT REPRODUCIBLE" in stream.getvalue()


def test_emit_returns_report_dict():
    diff = classify({"a": _entry(b"x")}, {"a": _entry(b"y")})
    rep = emit(diff)
    assert rep == report_dict(diff)


def test_emit_json_only_does_not_print(capsys, tmp_path):
    """With only json_out given (stream=None) nothing is printed."""
    diff = classify({"a": _entry(b"x")}, {"a": _entry(b"y")})
    out = tmp_path / "r.json"
    emit(diff, json_out=str(out))
    captured = capsys.readouterr().out
    assert captured == ""
    assert out.is_file()
