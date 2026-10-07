"""Tests for the ``explain`` command: source tracing + final merged value.

Covers spec.md "Verification":
- explain traces a symbol to the fragment(s) that set it,
- reports its final (merged) value, later-wins, with/without a profile.
"""

from __future__ import annotations

from pathlib import Path

from pengyos_kernel_fragments.cli import build_parser, main
from pengyos_kernel_fragments.explain import (
    Assignment,
    ExplainReport,
    explain_directory,
    render_explain,
)


def _write_fragments(dirpath: Path, fragments: dict[str, str]) -> None:
    for name, text in fragments.items():
        (dirpath / f"{name}.fragment").write_text(text, encoding="utf-8")


def _write_profile(dirpath: Path, name: str, lines: list[str]) -> None:
    (dirpath / f"{name}.profile").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# explain_directory — no profile (whole library)
# --------------------------------------------------------------------------- #


def test_explain_single_source(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n", "b": "CONFIG_B=m\n"})
    report = explain_directory(tmp_path, "CONFIG_A")
    assert report.ok
    assert len(report.assignments) == 1
    assignment = report.assignments[0]
    assert isinstance(assignment, Assignment)
    assert assignment.source == "a.fragment"
    assert assignment.value == "y"
    assert assignment.line_no == 1
    assert report.final_value == "y"
    assert report.final_source == "a.fragment"
    assert report.profile is None


def test_explain_multiple_sources_sorted_by_source(tmp_path):
    _write_fragments(
        tmp_path,
        {
            "z": "CONFIG_DUP=y\n",
            "a": "CONFIG_DUP=m\n",
            "m": "CONFIG_DUP=n\n",
        },
    )
    report = explain_directory(tmp_path, "CONFIG_DUP")
    assert report.ok
    assert [a.source for a in report.assignments] == [
        "a.fragment",
        "m.fragment",
        "z.fragment",
    ]
    # Later-wins over the sorted-by-name order -> the last fragment wins.
    assert report.final_value == "y"
    assert report.final_source == "z.fragment"


def test_explain_symbol_not_set(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    report = explain_directory(tmp_path, "CONFIG_ZED")
    assert report.ok  # not found is a clean answer, not an error
    assert report.assignments == ()
    assert report.final_value is None
    assert report.final_source is None


def test_explain_empty_library(tmp_path):
    report = explain_directory(tmp_path, "CONFIG_A")
    assert report.ok
    assert report.assignments == ()
    assert report.final_value is None


def test_explain_reports_line_number(tmp_path):
    _write_fragments(tmp_path, {"a": "# comment\nCONFIG_A=y\n"})
    report = explain_directory(tmp_path, "CONFIG_A")
    assert report.assignments[0].line_no == 2


# --------------------------------------------------------------------------- #
# explain_directory — with a profile (scoped merge order)
# --------------------------------------------------------------------------- #


def test_explain_profile_final_value_uses_profile_order(tmp_path):
    # Two fragments set CONFIG_DUP to different values. Sorted-by-name order
    # (a, b) would yield 'n'; the profile lists them (b, a) so later-wins
    # yields 'a.fragment's value. A different result proves the *profile*
    # order drives the final value, not the sorted-by-name order.
    _write_fragments(tmp_path, {"a": "CONFIG_DUP=y\n", "b": "CONFIG_DUP=n\n"})
    _write_profile(tmp_path, "p", ["b", "a"])
    report = explain_directory(tmp_path, "CONFIG_DUP", profile="p")
    assert report.ok
    assert report.profile == "p"
    assert report.final_value == "y"
    assert report.final_source == "a.fragment"


def test_explain_profile_scopes_to_profile_fragments(tmp_path):
    # CONFIG_DUP is also set by a fragment NOT in the profile; it must not
    # appear in the profile-scoped explanation.
    _write_fragments(
        tmp_path,
        {"a": "CONFIG_DUP=y\n", "b": "CONFIG_DUP=n\n", "c": "CONFIG_DUP=m\n"},
    )
    _write_profile(tmp_path, "p", ["a", "b"])
    report = explain_directory(tmp_path, "CONFIG_DUP", profile="p")
    assert report.ok
    assert {a.source for a in report.assignments} == {
        "a.fragment",
        "b.fragment",
    }
    assert report.final_value == "n"


def test_explain_profile_missing_is_usage_error(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    report = explain_directory(tmp_path, "CONFIG_A", profile="ghost")
    assert not report.ok
    assert report.exit_code == 2
    assert report.error and "ghost" in report.error


def test_explain_profile_unknown_fragment_is_usage_error(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    _write_profile(tmp_path, "p", ["a", "ghost"])
    report = explain_directory(tmp_path, "CONFIG_A", profile="p")
    assert not report.ok
    assert report.exit_code == 2
    assert "ghost" in (report.error or "")


# --------------------------------------------------------------------------- #
# ExplainReport helpers
# --------------------------------------------------------------------------- #


def test_explain_report_exit_codes():
    clean = ExplainReport(
        symbol="CONFIG_A",
        final_value="y",
        final_source="a.fragment",
    )
    assert clean.ok
    assert clean.exit_code == 0
    broken = ExplainReport(symbol="CONFIG_A", error="boom")
    assert not broken.ok
    assert broken.exit_code == 2


# --------------------------------------------------------------------------- #
# render_explain
# --------------------------------------------------------------------------- #


def test_render_explain_not_found():
    report = ExplainReport(symbol="CONFIG_NOPE")
    text = render_explain(report)
    assert "CONFIG_NOPE:" in text
    assert "not set by any fragment" in text


def test_render_explain_single_source():
    report = ExplainReport(
        symbol="CONFIG_A",
        assignments=(Assignment("a.fragment", "y", 1),),
        final_value="y",
        final_source="a.fragment",
    )
    text = render_explain(report)
    assert "CONFIG_A:" in text
    assert "set by 1 fragment(s):" in text
    assert "a.fragment (line 1): =y" in text
    assert "final value: =y (from a.fragment)" in text


def test_render_explain_multiple_sources():
    report = ExplainReport(
        symbol="CONFIG_DUP",
        assignments=(
            Assignment("a.fragment", "m", 1),
            Assignment("z.fragment", "y", 1),
        ),
        final_value="y",
        final_source="z.fragment",
    )
    text = render_explain(report)
    assert "set by 2 fragment(s):" in text
    assert "final value: =y (from z.fragment)" in text


def test_render_explain_profile_header():
    report = ExplainReport(
        symbol="CONFIG_A",
        assignments=(Assignment("a.fragment", "y", 1),),
        final_value="y",
        final_source="a.fragment",
        profile="n100",
    )
    text = render_explain(report)
    assert "CONFIG_A (profile 'n100'):" in text


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_explain_success_stdout(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    rc = main(["explain", "CONFIG_A", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CONFIG_A:" in out
    assert "a.fragment (line 1): =y" in out
    assert "final value: =y (from a.fragment)" in out


def test_cli_explain_not_found_is_clean(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    rc = main(["explain", "CONFIG_ZED", str(tmp_path)])
    assert rc == 0
    assert "not set by any fragment" in capsys.readouterr().out


def test_cli_explain_with_profile(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n", "b": "CONFIG_A=m\n"})
    _write_profile(tmp_path, "p", ["a", "b"])
    rc = main(["explain", "CONFIG_A", str(tmp_path), "-p", "p"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "profile 'p'" in out
    assert "final value: =m (from b.fragment)" in out


def test_cli_explain_bad_symbol_errors(capsys):
    rc = main(["explain", "not_a_symbol", "/"])
    assert rc == 2
    assert "not a CONFIG_ symbol" in capsys.readouterr().err


def test_cli_explain_missing_profile_errors(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    rc = main(["explain", "CONFIG_A", str(tmp_path), "-p", "ghost"])
    assert rc == 2
    assert "ghost" in capsys.readouterr().err


def test_cli_explain_not_a_directory(capsys):
    rc = main(["explain", "CONFIG_A", "/nonexistent-xyz"])
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_cli_explain_default_dir(tmp_path, monkeypatch, capsys):
    # With no dir argument, explain defaults to ./fragments.
    (tmp_path / "fragments").mkdir()
    _write_fragments(tmp_path / "fragments", {"a": "CONFIG_A=y\n"})
    monkeypatch.chdir(tmp_path)
    rc = main(["explain", "CONFIG_A"])
    assert rc == 0
    assert "a.fragment (line 1): =y" in capsys.readouterr().out


def test_parser_has_explain_subcommand():
    parser = build_parser()
    # Parsing explain populates the explain handler.
    args = parser.parse_args(["explain", "CONFIG_A", "/"])
    assert args.command == "explain"
    assert args.symbol == "CONFIG_A"
    assert args.dir == "/"
    assert args.profile is None


def test_parser_explain_dir_is_optional_default():
    parser = build_parser()
    args = parser.parse_args(["explain", "CONFIG_A"])
    assert args.dir == "./fragments"
    assert args.profile is None


# --------------------------------------------------------------------------- #
# End-to-end on the committed library (regression guard)
# --------------------------------------------------------------------------- #


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def test_explain_committed_library_virtio():
    report = explain_directory(_repo() / "fragments", "CONFIG_VIRTIO")
    assert report.ok
    assert [a.source for a in report.assignments] == ["qemu.fragment"]
    assert report.final_value == "y"


def test_explain_committed_library_drm_multiple_sources():
    # CONFIG_DRM is set by the three GPU fragments (all =y).
    report = explain_directory(_repo() / "fragments", "CONFIG_DRM")
    assert report.ok
    assert {a.source for a in report.assignments} == {
        "amd-igpu.fragment",
        "intel-igpu.fragment",
        "nvidia-proprietary.fragment",
    }
    assert report.final_value == "y"


def test_explain_committed_library_profile_scoped():
    report = explain_directory(
        _repo() / "fragments", "CONFIG_DRM_INTEL", profile="n100"
    )
    assert report.ok
    assert report.profile == "n100"
    assert [a.source for a in report.assignments] == ["intel-igpu.fragment"]
    assert report.final_value == "y"


def test_cli_composes_and_explies_shipped_symbols(capsys):
    # explain CONFIG_VIRTIO against the committed library, via the CLI.
    rc = main(["explain", "CONFIG_VIRTIO", str(_repo() / "fragments")])
    assert rc == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "qemu.fragment" in out
    assert "final value: =y" in out
