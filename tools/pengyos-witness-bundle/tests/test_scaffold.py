"""Tests for the repository scaffold: package import, CLI surface, docs."""

import json
import os

import pytest

from pengyos_witness_bundle import __version__
from pengyos_witness_bundle import cli


# --- package ---------------------------------------------------------------


def test_version_is_set():
    assert isinstance(__version__, str)
    assert __version__.count(".") >= 2  # semver-ish: X.Y.Z


def test_canonical_layout_documented():
    # The documented contract the `layout` command prints.
    assert cli.CANONICAL_LAYOUT["sbom"] == "sbom.spdx.json"
    assert cli.CANONICAL_LAYOUT["sbom_signature"].endswith(".sig")


# --- CLI: layout -----------------------------------------------------------


def test_layout_human(capsys):
    rc = cli.main(["layout"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sbom.spdx.json" in out
    assert "->" in out  # human table uses "kind -> name"


def test_layout_json(capsys):
    rc = cli.main(["layout", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload == cli.CANONICAL_LAYOUT


def test_layout_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["layout", "--help"])
    assert exc.value.code == 0
    assert "canonical witness artifact layout" in capsys.readouterr().out


# --- CLI: run (implemented) -------------------------------------------------
# The `run` pipeline (discovery -> verify) is implemented one subtask at a time.
# The scaffold placeholder is gone: a missing build dir is a clean runtime
# failure (exit 1), never a traceback. Parse errors still raise SystemExit(2).


def test_run_missing_build_dir_exits_one():
    # A build dir that does not exist is a runtime error (exit 1), not a usage
    # error (exit 2) and never a traceback.
    assert cli.main(["run", "--build", "/nonexistent"]) == 1


def test_run_missing_build_arg_exits_two():
    with pytest.raises(SystemExit) as exc:
        cli.main(["run"])
    assert exc.value.code == 2


def test_run_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--help"])
    assert exc.value.code == 0
    assert "--build" in capsys.readouterr().out


def test_run_resolves_real_sbom_over_spurious_host_spdx(tmp_path):
    # A spurious host-package SPDX doc (sorts before images/) must not shadow the
    # real SBOM: with all four kinds present, the build is still witness-ready.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json").write_text("{}")
    (tmp_path / "images" / "pengyos-x86_64.cyclonedx.json").write_text("{}")
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_text("sig")
    (tmp_path / "repro-report.txt").write_text("repro")
    (tmp_path / "host-python3-3.14.7" / "Misc").mkdir(parents=True)
    (
        tmp_path / "host-python3-3.14.7" / "Misc" / "externals.spdx.json"
    ).write_text("{}")

    rc = cli.main(["run", "--build", str(tmp_path)])
    assert rc == 0  # all four kinds resolved -> witness-ready, clean exit


# --- CLI: dispatch / top level ---------------------------------------------


def test_no_command_exits_two():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


# --- GitHub-ready contract -------------------------------------------------


def test_docs_present():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        assert os.path.isfile(os.path.join(root, name)), f"missing {name}"


def test_license_has_copyright_line():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "LICENSE"), encoding="utf-8") as fh:
        assert "Copyright (c) 2026 Pat Wendorf" in fh.read()
