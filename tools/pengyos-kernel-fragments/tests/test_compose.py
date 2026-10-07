"""Tests for the ``compose`` command: merge, precedence, warnings, determinism.

Covers spec.md "Verification":
- compose order/precedence + deterministic output,
- later-wins conflict warnings,
- missing profile / missing fragment usage errors.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from pengyos_kernel_fragments.cli import main
from pengyos_kernel_fragments.compose import (
    ComposeReport,
    ConflictWarning,
    compose_directory,
    merge_fragments,
    render_error,
    render_merged,
    render_warnings,
)
from pengyos_kernel_fragments.fragments import load_directory, parse_fragment_text


def _write_fragments(dirpath: Path, fragments: dict[str, str]) -> None:
    """Write ``name`` -> ``text`` as ``<name>.fragment`` files in *dirpath*."""
    for name, text in fragments.items():
        (dirpath / f"{name}.fragment").write_text(text, encoding="utf-8")


def _write_profile(dirpath: Path, name: str, lines: list[str]) -> None:
    (dirpath / f"{name}.profile").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _library(dirpath: Path) -> dict[str, object]:
    return {f.name: f for f in load_directory(dirpath)}


# --------------------------------------------------------------------------- #
# Profile parsing
# --------------------------------------------------------------------------- #


def test_profile_lists_fragments_ignoring_comments_and_blanks(tmp_path):
    # Comment/blank filtering is a profile-parsing concern (_load_profile);
    # merge_fragments receives a clean, ordered list.
    from pengyos_kernel_fragments.compose import _load_profile

    (tmp_path / "p.profile").write_text(
        "# a comment\nqemu\n\n   \nintel-igpu\n", encoding="utf-8"
    )
    _text, fragments = _load_profile(tmp_path, "p")
    assert fragments == ["qemu", "intel-igpu"]


def test_merge_receives_clean_order():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    library = {"a.fragment": a}
    report = merge_fragments(["a"], library, "p")
    assert report.order == ("a",)
    assert not report.missing
    assert report.merged[0].symbol == "CONFIG_A"


def test_profile_preserves_order():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_B=y", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["b", "a"], library, "p")
    assert report.order == ("b", "a")


# --------------------------------------------------------------------------- #
# Merge + later-wins precedence
# --------------------------------------------------------------------------- #


def test_merge_unions_symbols():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_B=m", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["a", "b"], library, "p")
    symbols = [e.symbol for e in report.merged]
    assert symbols == ["CONFIG_A", "CONFIG_B"]
    assert report.merged[0].value == "y"
    assert report.merged[1].value == "m"


def test_later_fragment_wins():
    a = parse_fragment_text("CONFIG_DUP=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_DUP=m", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["a", "b"], library, "p")
    assert len(report.merged) == 1
    assert report.merged[0].value == "m"


def test_later_wins_conflict_warning():
    a = parse_fragment_text("CONFIG_DUP=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_DUP=n", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["a", "b"], library, "p")
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert isinstance(warning, ConflictWarning)
    assert warning.symbol == "CONFIG_DUP"
    assert warning.previous_value == "y"
    assert warning.previous_source == "a.fragment"
    assert warning.new_value == "n"
    assert warning.new_source == "b.fragment"
    # The merged value is the later one.
    assert report.merged[0].value == "n"


def test_same_value_is_not_a_conflict():
    a = parse_fragment_text("CONFIG_DUP=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_DUP=y", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["a", "b"], library, "p")
    assert report.warnings == ()
    assert report.ok


def test_conflict_warning_format():
    warning = ConflictWarning(
        symbol="CONFIG_X",
        previous_value="y",
        previous_source="a.fragment",
        new_value="n",
        new_source="b.fragment",
    )
    text = warning.format()
    assert "CONFIG_X" in text
    assert "a.fragment=y" in text
    assert "b.fragment=n" in text


# --------------------------------------------------------------------------- #
# Missing fragments / usage errors
# --------------------------------------------------------------------------- #


def test_missing_fragment_is_reported_and_not_ok():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    library = {"a.fragment": a}
    report = merge_fragments(["a", "ghost"], library, "p")
    assert not report.ok
    assert report.missing == ("ghost",)
    assert report.merged == ()
    assert report.exit_code == 2


def test_resolve_name_tolerates_missing_extension():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    library = {"a.fragment": a}
    # Both the bare name and the full basename resolve.
    assert merge_fragments(["a"], library, "p").ok
    assert merge_fragments(["a.fragment"], library, "p").ok


# --------------------------------------------------------------------------- #
# Deterministic output
# --------------------------------------------------------------------------- #


def test_merged_is_sorted_by_symbol():
    c = parse_fragment_text("CONFIG_C=y", name="c.fragment")
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_B=y", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b, "c.fragment": c}
    report = merge_fragments(["c", "a", "b"], library, "p")
    assert [e.symbol for e in report.merged] == [
        "CONFIG_A",
        "CONFIG_B",
        "CONFIG_C",
    ]


def test_merged_set_independent_of_input_order():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_B=y", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    first = merge_fragments(["a", "b"], library, "p")
    second = merge_fragments(["b", "a"], library, "p")
    assert render_merged(first) == render_merged(second)


# --------------------------------------------------------------------------- #
# render_* helpers
# --------------------------------------------------------------------------- #


def test_render_merged_roundtrip():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_B=m", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["a", "b"], library, "p")
    assert render_merged(report) == "CONFIG_A=y\nCONFIG_B=m"


def test_render_warnings_roundtrip():
    a = parse_fragment_text("CONFIG_DUP=y", name="a.fragment")
    b = parse_fragment_text("CONFIG_DUP=n", name="b.fragment")
    library = {"a.fragment": a, "b.fragment": b}
    report = merge_fragments(["a", "b"], library, "p")
    assert "CONFIG_DUP" in render_warnings(report)


def test_render_error_none_when_ok():
    a = parse_fragment_text("CONFIG_A=y", name="a.fragment")
    library = {"a.fragment": a}
    report = merge_fragments(["a"], library, "p")
    assert render_error(report) is None


def test_render_error_missing_fragment():
    library: dict[str, object] = {}
    report = merge_fragments(["ghost"], library, "p")
    msg = render_error(report)
    assert msg is not None
    assert "ghost" in msg


def test_render_error_missing_profile():
    report = compose_directory("/nonexistent-xyz", "ghost")
    msg = render_error(report)
    assert msg is not None
    assert "no profile named" in msg


# --------------------------------------------------------------------------- #
# End-to-end directory composition
# --------------------------------------------------------------------------- #


def test_compose_directory_merges(tmp_path):
    _write_fragments(
        tmp_path,
        {
            "a": "CONFIG_A=y\n",
            "b": "CONFIG_B=m\nCONFIG_A=n\n",
        },
    )
    _write_profile(tmp_path, "p", ["a", "b"])
    report = compose_directory(tmp_path, "p")
    assert report.ok
    assert report.order == ("a", "b")
    merged = render_merged(report)
    assert merged == "CONFIG_A=n\nCONFIG_B=m"
    assert len(report.warnings) == 1


def test_compose_directory_missing_profile(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    report = compose_directory(tmp_path, "ghost")
    assert not report.ok
    assert report.exit_code == 2
    assert render_error(report) is not None


def test_compose_directory_ignores_non_profile_files(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    (tmp_path / "notes.md").write_text("not a profile", encoding="utf-8")
    _write_profile(tmp_path, "p", ["a"])
    report = compose_directory(tmp_path, "p")
    assert report.ok
    assert report.merged[0].symbol == "CONFIG_A"


def test_empty_profile_composes_clean(tmp_path):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    _write_profile(tmp_path, "empty", ["# nothing here"])
    report = compose_directory(tmp_path, "empty")
    assert report.ok
    assert report.merged == ()
    assert report.exit_code == 0


# --------------------------------------------------------------------------- #
# Committed n100 profile (regression guard)
# --------------------------------------------------------------------------- #


def test_committed_n100_profile_composes_clean():
    repo = Path(__file__).resolve().parents[1]
    report = compose_directory(repo / "fragments", "n100")
    assert report.ok, render_error(report) or "profile did not compose"
    symbols = {e.symbol for e in report.merged}
    # n100 = qemu + systemd + intel-igpu: one symbol from each concern.
    assert "CONFIG_VIRTIO" in symbols
    assert "CONFIG_SYSTEMD" in symbols
    assert "CONFIG_DRM_INTEL" in symbols
    # The three concerns do not share conflicting symbols.
    assert report.warnings == ()
    # Output is sorted.
    assert [e.symbol for e in report.merged] == sorted(
        e.symbol for e in report.merged
    )


# --------------------------------------------------------------------------- #
# Shipped per-hardware profiles (task 5)
# --------------------------------------------------------------------------- #

# Every shipped profile is qemu + systemd + one GPU fragment. Each must compose
# clean (no later-wins warnings — the concerns share no conflicting symbols),
# satisfy the systemd required-symbol rule, and expose one symbol from each of
# the three concerns.
SHIPPED_PROFILES: dict[str, tuple[str, str, str]] = {
    "n100": ("CONFIG_VIRTIO", "CONFIG_SYSTEMD", "CONFIG_DRM_INTEL"),
    "amdv1000": ("CONFIG_VIRTIO", "CONFIG_SYSTEMD", "CONFIG_DRM_AMDGPU"),
    "nvidia": ("CONFIG_VIRTIO", "CONFIG_SYSTEMD", "CONFIG_NVIDIA"),
}


@pytest.mark.parametrize("profile,expected", list(SHIPPED_PROFILES.items()))
def test_shipped_profile_composes_clean(profile, expected):
    qemu, systemd, gpu = expected
    repo = Path(__file__).resolve().parents[1]
    report = compose_directory(repo / "fragments", profile)
    assert report.ok, render_error(report)
    # The three concerns never fight over a symbol in a shipped profile.
    assert report.warnings == ()
    symbols = {e.symbol for e in report.merged}
    assert qemu in symbols
    assert systemd in symbols
    assert gpu in symbols


def test_shipped_profiles_reference_only_existing_fragments():
    # A shipped profile must not reference a fragment that is absent from the
    # library (a completeness guard for the shipped set).
    repo = Path(__file__).resolve().parents[1]
    for name in SHIPPED_PROFILES:
        report = compose_directory(repo / "fragments", name)
        assert report.missing == (), (
            f"profile {name} references an unknown fragment"
        )


def test_shipped_profiles_satisfy_systemd_rule():
    # The committed library (which includes systemd.fragment) validates clean
    # under all rules, which includes the systemd required-symbol rule that
    # every shipped profile (all of which include systemd) must satisfy.
    from pengyos_kernel_fragments.validate import (
        CODE_MISSING_REQUIRED,
        validate_directory,
    )

    repo = Path(__file__).resolve().parents[1]
    report = validate_directory(repo / "fragments")
    assert report.ok, "committed library must satisfy the systemd rule"
    assert not any(
        f.code == CODE_MISSING_REQUIRED for f in report.findings
    )


def test_shipped_profile_files_are_documented():
    # A shipped profile is documented: it carries one or more leading `#`
    # comment lines describing the hardware it targets.
    repo = Path(__file__).resolve().parents[1]
    for name in SHIPPED_PROFILES:
        lines = (repo / "fragments" / f"{name}.profile").read_text(
            encoding="utf-8"
        ).splitlines()
        comment_lines = [ln for ln in lines if ln.strip().startswith("#")]
        assert comment_lines, f"profile {name} has no documenting comment"
        # And it lists at least one fragment.
        fragment_lines = [
            ln for ln in lines if ln.strip() and not ln.strip().startswith("#")
        ]
        assert fragment_lines, f"profile {name} lists no fragments"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_compose_success_stdout(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n", "b": "CONFIG_B=m\n"})
    _write_profile(tmp_path, "p", ["a", "b"])
    rc = main(["compose", str(tmp_path), "-p", "p"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "CONFIG_A=y\nCONFIG_B=m\n"


def test_cli_compose_out_writes_file(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    _write_profile(tmp_path, "p", ["a"])
    out_path = tmp_path / "merged.fragment"
    rc = main(["compose", str(tmp_path), "-p", "p", "--out", str(out_path)])
    assert rc == 0
    assert capsys.readouterr().out == ""  # nothing on stdout
    assert out_path.read_text() == "CONFIG_A=y\n"


def test_cli_compose_warnings_go_to_stderr(tmp_path, capsys):
    _write_fragments(
        tmp_path,
        {"a": "CONFIG_DUP=y\n", "b": "CONFIG_DUP=n\n"},
    )
    _write_profile(tmp_path, "p", ["a", "b"])
    rc = main(["compose", str(tmp_path), "-p", "p"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == "CONFIG_DUP=n\n"
    assert "conflict: CONFIG_DUP" in captured.err


def test_cli_compose_missing_fragment_errors(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    _write_profile(tmp_path, "p", ["a", "ghost"])
    rc = main(["compose", str(tmp_path), "-p", "p"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown fragment(s)" in err
    assert "ghost" in err


def test_cli_compose_missing_profile_errors(tmp_path, capsys):
    _write_fragments(tmp_path, {"a": "CONFIG_A=y\n"})
    rc = main(["compose", str(tmp_path), "-p", "ghost"])
    assert rc == 2
    assert "no profile named" in capsys.readouterr().err


def test_cli_compose_not_a_directory(capsys):
    rc = main(["compose", "/nonexistent-xyz", "-p", "ghost"])
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_cli_compose_requires_profile(capsys):
    # --profile is required; argparse reports a usage error (SystemExit, code 2).
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["compose", "/"])
    assert excinfo.value.code == 2
    assert "the following arguments are required: --profile/-p" in capsys.readouterr().err


@pytest.mark.parametrize("profile,expected", list(SHIPPED_PROFILES.items()))
def test_cli_composes_shipped_profile(profile, expected, capsys):
    # End-to-end: the shipped amd/nvidia profiles compose via the CLI with the
    # expected GPU symbol present on stdout.
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    _, _, gpu = expected
    rc = main(["compose", str(repo / "fragments"), "-p", profile])
    assert rc == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert gpu in out
