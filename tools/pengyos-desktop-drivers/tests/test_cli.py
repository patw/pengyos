"""CLI tests.

These exercise the real argparse wiring: help/version output, subcommand
dispatch, argument validation, and the expected exit-code contract. ``emit`` is
implemented (writes artifacts, exits 0); ``lint`` is a later task, so it is
tested for *dispatch + clean error surfacing* (exit 2 from its
``NotYetImplemented``) rather than for producing output.
"""

from __future__ import annotations

import pathlib

import pengyos_desktop_drivers
import pytest

from pengyos_desktop_drivers import cli


def test_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "emit" in out
    assert "lint" in out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert pengyos_desktop_drivers.__version__ in capsys.readouterr().out


def test_emit_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["emit", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--out" in out
    assert "--json" in out


def test_lint_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["lint", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--fragment" in out
    assert "--firmware" in out


def test_emit_missing_out_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["emit"])
    assert exc.value.code == 2


def test_lint_missing_firmware_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["lint", "--fragment", "x"])
    assert exc.value.code == 2


def test_emit_dispatches_and_writes_artifacts(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    # emit is implemented: it writes both artifacts and exits 0.
    code = cli.main(["emit", "--out", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "linux.fragment").is_file()
    assert (tmp_path / "firmware.list").is_file()
    err = capsys.readouterr().err
    assert not err.strip()


def test_emit_dispatches_json(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    code = cli.main(["emit", "--out", str(tmp_path), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    import json

    report = json.loads(out)
    assert report["fragment"]["path"] == str(tmp_path / "linux.fragment")
    assert len(report["fragment"]["sha256"]) == 64


def test_lint_dispatches_and_surfaces_pending(capsys: pytest.CaptureFixture[str]) -> None:
    # lint is implemented; with nonexistent files it surfaces a usage-style
    # error (exit 2). The old "surfaces pending" expectation is gone now that
    # task 5 lands -- see test_lint for the real rule behavior.
    code = cli.main(["lint", "--fragment", "x", "--firmware", "y"])
    assert code == 2
    err = capsys.readouterr().err
    assert "error:" in err.lower()


def test_lint_dispatches_clean_profile(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    # The real Desktop profile is lint-clean: dispatch + exit 0.
    assert cli.main(["emit", "--out", str(tmp_path)]) == 0
    code = cli.main(
        [
            "lint",
            "--fragment",
            str(tmp_path / "linux.fragment"),
            "--firmware",
            str(tmp_path / "firmware.list"),
        ]
    )
    assert code == 0
    assert "lint: ok" in capsys.readouterr().out
