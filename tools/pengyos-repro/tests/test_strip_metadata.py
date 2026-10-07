"""Tests for the ``--strip-metadata`` second-pass comparison.

Covers:

* :func:`pengyos_repro.patterns.strip_metadata_patterns` — the effective ignore
  list for the second pass (user patterns + every curated heuristic),
* :func:`pengyos_repro.patterns.explain_ignore` — which pattern explained a
  path (the ``heuristic:<name>`` alias, or the literal glob),
* :func:`pengyos_repro.report.metadata_analysis` — the metadata-only
  classification (differs in the raw pass, identical once metadata is stripped),
* :func:`pengyos_repro.report.report_dict` / ``format_report`` / ``emit`` /
  ``dumps_report`` — the second-pass keys in every view,
* the CLI ``--strip-metadata`` flag on ``compare`` and ``score``.
"""

import hashlib
import json

import pytest

from pengyos_repro.classify import classify
from pengyos_repro.manifest import FileEntry
from pengyos_repro.patterns import (
    HEURISTIC_EMBEDDED_USER,
    HEURISTIC_RANDOM_SEEDS,
    HEURISTIC_TIMESTAMPS,
    PatternError,
    explain_ignore,
    strip_metadata_patterns,
)
from pengyos_repro.report import (
    dumps_report,
    emit,
    format_report,
    metadata_analysis,
    report_dict,
    score,
)


def _entry(data: bytes) -> FileEntry:
    return FileEntry(size=len(data), sha256=hashlib.sha256(data).hexdigest())


# --- strip_metadata_patterns ------------------------------------------------


def test_strip_metadata_patterns_appends_all_heuristics():
    out = strip_metadata_patterns()
    assert set(out) == (
        set(HEURISTIC_TIMESTAMPS)
        | set(HEURISTIC_EMBEDDED_USER)
        | set(HEURISTIC_RANDOM_SEEDS)
    )


def test_strip_metadata_patterns_prepends_user_patterns():
    out = strip_metadata_patterns(["*.tmp"])
    assert out[0] == "*.tmp"
    # The curated heuristics come after the caller's patterns.
    assert "*.tmp" not in HEURISTIC_TIMESTAMPS


def test_strip_metadata_patterns_does_not_mutate_input():
    extra = ["*.tmp", "heuristic:timestamps"]
    strip_metadata_patterns(extra)
    assert extra == ["*.tmp", "heuristic:timestamps"]


def test_strip_metadata_patterns_expands_user_heuristic_aliases():
    out = strip_metadata_patterns(["heuristic:timestamps"])
    # The alias is expanded to its concrete globs, and the curated set is added.
    assert "*.pyc" in out
    assert set(out) >= set(HEURISTIC_TIMESTAMPS)


# --- explain_ignore ---------------------------------------------------------


def test_explain_ignore_returns_heuristic_alias():
    assert explain_ignore("main.pyc", ["heuristic:timestamps"]) == "heuristic:timestamps"
    assert explain_ignore("lib/x.so", ["heuristic:embedded-user"]) == "heuristic:embedded-user"
    assert explain_ignore("random-seed", ["heuristic:random-seeds"]) == "heuristic:random-seeds"


def test_explain_ignore_returns_literal_glob_for_plain_pattern():
    assert explain_ignore("a.tmp", ["*.tmp"]) == "*.tmp"
    assert explain_ignore("sub/a.tmp", ["*.tmp"]) == "*.tmp"  # basename at any depth


def test_explain_ignore_returns_none_when_nothing_matches():
    assert explain_ignore("keep.txt", ["*.tmp"]) is None


def test_explain_ignore_short_circuits_on_first_heuristic():
    # A path matching the first heuristic reports that one, not a later glob.
    assert explain_ignore("main.pyc", ["heuristic:timestamps", "heuristic:random-seeds"]) == (
        "heuristic:timestamps"
    )


def test_explain_ignore_validates_unknown_alias():
    with pytest.raises(PatternError):
        explain_ignore("x", ["heuristic:nope"])


# --- metadata_analysis ------------------------------------------------------


def test_metadata_analysis_explains_metadata_only_difference():
    """A path that differs in the raw pass but is identical once the heuristic
    hides it is metadata-only, annotated with the explaining heuristic."""
    a = {"main.c": _entry(b"src"), "main.pyc": _entry(b"bc1")}
    b = {"main.c": _entry(b"src"), "main.pyc": _entry(b"bc2")}
    raw = classify(a, b)
    # The stripped pass hides main.pyc (timestamps), so it is identical there.
    stripped = classify({"main.c": _entry(b"src")}, {"main.c": _entry(b"src")})
    analysis = metadata_analysis(raw, stripped, [])
    assert analysis.count == 1
    assert analysis.explained == [("main.pyc", "heuristic:timestamps")]
    # The stripped score is higher (fewer differing paths).
    assert analysis.stripped_score > score(raw)


def test_metadata_analysis_ignores_content_differences():
    """A path that differs in BOTH passes is a real content difference, not
    metadata — it is never listed as explained."""
    a = {"payload": _entry(b"v1")}
    b = {"payload": _entry(b"v2")}
    raw = classify(a, b)
    stripped = classify(a, b)  # no heuristic hides "payload"
    analysis = metadata_analysis(raw, stripped, [])
    assert analysis.count == 0
    assert analysis.explained == []
    assert analysis.stripped_score == score(raw)


def test_metadata_analysis_stripped_score_never_below_raw():
    a = {
        "same": _entry(b"s"),
        "pyc": _entry(b"1"),
        "so": _entry(b"2"),
        "real": _entry(b"r1"),
    }
    b = {
        "same": _entry(b"s"),
        "pyc": _entry(b"3"),
        "so": _entry(b"4"),
        "real": _entry(b"r2"),
    }
    raw = classify(a, b)
    # Stripping hides pyc + so, leaving same + real (real still differs).
    stripped = classify({"same": _entry(b"s"), "real": _entry(b"r2")},
                        {"same": _entry(b"s"), "real": _entry(b"r2")})
    analysis = metadata_analysis(raw, stripped, [])
    assert analysis.stripped_score >= score(raw)


def test_metadata_analysis_only_in_paths_are_not_explained():
    """only-in-* paths are a score matter, not a metadata matter — a path that
    is only in one tree is never in the differing set, so never explained."""
    a = {"keep": _entry(b"k"), "left": _entry(b"l")}
    b = {"keep": _entry(b"k")}
    raw = classify(a, b)
    stripped = classify(a, b)
    analysis = metadata_analysis(raw, stripped, [])
    assert analysis.count == 0
    assert analysis.explained == []


def test_metadata_analysis_identical_is_noop():
    a = {"a": _entry(b"x")}
    diff = classify(a, a)
    analysis = metadata_analysis(diff, diff, [])
    assert analysis.count == 0
    assert analysis.stripped_score == 1.0


def test_metadata_analysis_reports_user_glob_not_alias():
    """When the caller's own glob (not a heuristic) explains a path, it is
    reported as the literal glob — but note the second pass only ever strips
    heuristics, so this path exercises explain_ignore directly."""
    a = {"data.tmp": _entry(b"1")}
    b = {"data.tmp": _entry(b"2")}
    raw = classify(a, b)
    stripped = classify({}, {})  # everything filtered
    # explain_ignore with the caller's glob names it literally.
    assert explain_ignore("data.tmp", ["*.tmp"]) == "*.tmp"


# --- report_dict / format_report / emit with analysis -----------------------


def test_report_dict_includes_second_pass_keys():
    a = {"main.c": _entry(b"src"), "main.pyc": _entry(b"1")}
    b = {"main.c": _entry(b"src"), "main.pyc": _entry(b"2")}
    raw = classify(a, b)
    stripped = classify({"main.c": _entry(b"src")}, {"main.c": _entry(b"src")})
    analysis = metadata_analysis(raw, stripped, [])
    rep = report_dict(raw, analysis=analysis)
    assert set(rep) == {"score", "identical", "differing", "missing",
                        "stripped_score", "metadata_only"}
    assert rep["stripped_score"] == analysis.stripped_score
    assert rep["metadata_only"] == [{"path": "main.pyc", "reason": "heuristic:timestamps"}]


def test_report_dict_without_analysis_has_no_second_pass_keys():
    diff = classify({"a": _entry(b"x")}, {"a": _entry(b"y")})
    rep = report_dict(diff)
    assert set(rep) == {"score", "identical", "differing", "missing"}
    assert "stripped_score" not in rep
    assert "metadata_only" not in rep


def test_format_report_includes_metadata_section():
    a = {"main.c": _entry(b"src"), "main.pyc": _entry(b"1")}
    b = {"main.c": _entry(b"src"), "main.pyc": _entry(b"2")}
    raw = classify(a, b)
    stripped = classify({"main.c": _entry(b"src")}, {"main.c": _entry(b"src")})
    analysis = metadata_analysis(raw, stripped, [])
    text = format_report(raw, analysis=analysis)
    assert "Metadata-stripped score" in text
    assert "Metadata-only" in text
    assert "main.pyc" in text
    assert "heuristic:timestamps" in text


def test_format_report_identical_notes_stripped_score():
    diff = classify({"a": _entry(b"x")}, {"a": _entry(b"x")})
    analysis = metadata_analysis(diff, diff, [])
    text = format_report(diff, analysis=analysis)
    assert "REPRODUCIBLE" in text
    assert "no metadata-only differences" in text


def test_emit_writes_second_pass_keys_to_json(tmp_path):
    a = {"main.c": _entry(b"src"), "main.pyc": _entry(b"1")}
    b = {"main.c": _entry(b"src"), "main.pyc": _entry(b"2")}
    raw = classify(a, b)
    stripped = classify({"main.c": _entry(b"src")}, {"main.c": _entry(b"src")})
    analysis = metadata_analysis(raw, stripped, [])
    out = tmp_path / "r.json"
    emit(raw, json_out=str(out), analysis=analysis)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["stripped_score"] == analysis.stripped_score
    assert written["metadata_only"] == [
        {"path": "main.pyc", "reason": "heuristic:timestamps"}
    ]


def test_dumps_report_includes_analysis():
    a = {"main.c": _entry(b"src"), "main.pyc": _entry(b"1")}
    b = {"main.c": _entry(b"src"), "main.pyc": _entry(b"2")}
    raw = classify(a, b)
    stripped = classify({"main.c": _entry(b"src")}, {"main.c": _entry(b"src")})
    analysis = metadata_analysis(raw, stripped, [])
    parsed = json.loads(dumps_report(raw, analysis=analysis))
    assert "stripped_score" in parsed
    assert "metadata_only" in parsed


def test_metadata_analysis_uses_user_patterns_for_explanation():
    """The caller's own ignore patterns are consulted first when explaining;
    a heuristic match still reports as ``heuristic:<name>``."""
    a = {"main.pyc": _entry(b"1"), "data.tmp": _entry(b"2")}
    b = {"main.pyc": _entry(b"3"), "data.tmp": _entry(b"4")}
    raw = classify(a, b)
    # Second pass hides both a user glob and a heuristic.
    stripped = classify({}, {})
    analysis = metadata_analysis(raw, stripped, ["*.tmp"])
    reasons = dict(analysis.explained)
    assert reasons["data.tmp"] == "*.tmp"
    assert reasons["main.pyc"] == "heuristic:timestamps"


# --- CLI: --strip-metadata on compare / score -------------------------------


def _build_pair(tmp_path, *, files):
    """Create two sibling build dirs sharing ``files`` (relpath -> bytes), with
    differing content where the bytes differ between the two sides.

    ``files`` maps a relative path to either a single bytes value (identical on
    both sides) or a ``(a_bytes, b_bytes)`` tuple (differing content).
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for rel, data in files.items():
        if isinstance(data, tuple):
            (a / rel).parent.mkdir(parents=True, exist_ok=True)
            (b / rel).parent.mkdir(parents=True, exist_ok=True)
            (a / rel).write_bytes(data[0])
            (b / rel).write_bytes(data[1])
        else:
            (a / rel).parent.mkdir(parents=True, exist_ok=True)
            (b / rel).parent.mkdir(parents=True, exist_ok=True)
            (a / rel).write_bytes(data)
            (b / rel).write_bytes(data)
    return a, b


def test_cli_compare_strip_metadata_reports_explained(capsys, tmp_path):
    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(
        tmp_path,
        files={
            "same.txt": b"same",
            "main.pyc": (b"bc1", b"bc2"),  # timestamps heuristic
            "lib/x.so": (b"obj1", b"obj2"),  # embedded-user heuristic
            "real.md": (b"v1", b"v2"),  # real content difference
        },
    )
    rc = main(["compare", str(a), str(b), "--strip-metadata"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    # Raw score is present, and the stripped score is higher.
    assert "NOT REPRODUCIBLE" in out
    assert "Metadata-stripped score" in out
    # The two heuristic differences are explained; the real one is not.
    assert "heuristic:timestamps" in out
    assert "heuristic:embedded-user" in out
    assert "main.pyc" in out
    assert "lib/x.so" in out
    # real.md differs in both passes, so it is NOT explained as metadata-only.
    assert out.count("real.md") == 1  # only the differing list, not the explained list


def test_cli_compare_strip_metadata_identical(capsys, tmp_path):
    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(tmp_path, files={"same.txt": b"same"})
    rc = main(["compare", str(a), str(b), "--strip-metadata"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "REPRODUCIBLE" in out
    assert "no metadata-only differences" in out


def test_cli_compare_without_flag_has_no_metadata_section(capsys, tmp_path):
    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(tmp_path, files={"main.pyc": (b"bc1", b"bc2")})
    rc = main(["compare", str(a), str(b)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "Metadata-stripped" not in out
    assert "Metadata-only" not in out


def test_cli_compare_strip_metadata_writes_json_keys(capsys, tmp_path, monkeypatch):
    import json

    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(
        tmp_path, files={"main.pyc": (b"bc1", b"bc2"), "real.md": (b"v1", b"v2")}
    )
    out_json = tmp_path / "report.json"
    rc = main(["compare", str(a), str(b), "--strip-metadata", "--json", str(out_json)])
    written = json.loads(out_json.read_text(encoding="utf-8"))
    assert rc == EXIT_OK
    assert "stripped_score" in written
    assert "metadata_only" in written
    paths = {m["path"] for m in written["metadata_only"]}
    assert paths == {"main.pyc"}  # real.md is a genuine content difference


def test_cli_score_strip_metadata_prints_stripped(capsys, tmp_path):
    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(
        tmp_path,
        files={
            "same1": b"s",
            "same2": b"s",
            "main.pyc": (b"1", b"2"),  # hidden by timestamps
            "real.md": (b"v1", b"v2"),  # still differs
        },
    )
    # Raw: 2 identical of 4 total -> 0.5
    main(["score", str(a), str(b)])
    raw_out = capsys.readouterr().out.strip()
    # Stripped: hides main.pyc, so 2 identical of 3 total -> 0.6667
    rc = main(["score", str(a), str(b), "--strip-metadata"])
    stripped_out = capsys.readouterr().out.strip()
    assert rc == EXIT_OK
    assert float(stripped_out) > float(raw_out)
    assert stripped_out == f"{round(2 / 3, 4):.4f}"


def test_cli_score_without_flag_prints_raw(capsys, tmp_path):
    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(tmp_path, files={"same.txt": b"same"})
    rc = main(["score", str(a), str(b)])
    out = capsys.readouterr().out.strip()
    assert rc == EXIT_OK
    assert out == "1.0000"


def test_cli_strip_metadata_additive_to_ignore(capsys, tmp_path):
    """--strip-metadata layers the heuristics on top of the caller's --ignore."""
    from pengyos_repro.cli import EXIT_OK, main

    a, b = _build_pair(
        tmp_path,
        files={
            "same.txt": b"same",
            "data.tmp": (b"1", b"2"),  # hidden by the caller's --ignore
            "main.pyc": (b"bc1", b"bc2"),  # hidden by --strip-metadata
        },
    )
    rc = main(
        [
            "compare",
            str(a),
            str(b),
            "--ignore",
            "*.tmp",
            "--strip-metadata",
        ]
    )
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    # data.tmp is gone from the differing list (caller's ignore) ...
    assert "data.tmp" not in out
    # ... but main.pyc is explained by the heuristic in the second pass.
    assert "heuristic:timestamps" in out
