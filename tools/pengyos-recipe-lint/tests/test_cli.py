"""Tests for the CLI: argument handling, exit codes, and outputs."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from pengyos_recipe_lint import __version__


# --------------------------------------------------------------------------- #
# check: discovery now lints, so violations gate the exit code.
# --------------------------------------------------------------------------- #
def test_check_reports_recipes_with_violations(run_cli, recipe_tree, capsys):
    # The recipe_tree has recipes that break rules (no sha256 / no license),
    # so check exits non-zero and prints a file:line report.
    rc = run_cli("check", str(recipe_tree))
    assert rc == 1
    out = capsys.readouterr().out
    assert "violation(s)" in out
    assert "3 recipe(s)" in out
    # Stable ordering: good first, then nested/deep, then sub/bare by path.
    assert out.index("good") < out.index("deep") < out.index("bare")


def test_check_flags_missing_hash(run_cli, recipe_tree, capsys):
    rc = run_cli("check", str(recipe_tree))
    assert rc == 1
    out = capsys.readouterr().out
    # sub/bare.mk has no .hash sibling; good/deep have a .hash but no sha256.
    assert "no .hash sibling" in out
    assert "no sha256" in out


def test_check_compliant_returns_zero(run_cli, compliant_tree, capsys):
    rc = run_cli("check", str(compliant_tree))
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 violations" in out


def test_check_reports_file_line_and_hint(run_cli, recipe_tree, capsys):
    rc = run_cli("check", str(recipe_tree))
    assert rc == 1
    out = capsys.readouterr().out
    # A missing-variable violation renders just the file path (no :line).
    assert any(line.endswith(".mk") for line in out.splitlines())
    # A fix hint is emitted.
    assert "hint:" in out
    # Each violation names its rule.
    assert "license_declared" in out


def test_check_json_output(run_cli, recipe_tree, tmp_path):
    out_json = tmp_path / "report.json"
    rc = run_cli("check", str(recipe_tree), "--json", str(out_json))
    assert rc == 1
    assert out_json.exists()
    payload = json.loads(out_json.read_text())
    assert payload["version"] == __version__
    assert payload["count"] == 3
    names = {r["name"] for r in payload["recipes"]}
    assert names == {"good", "bare", "deep"}
    assert payload["violation_count"] >= 1
    rules = {violation["rule"] for violation in payload["violations"]}
    assert {"hash_present", "license_declared"} <= rules
    # Every violation carries a recipe path and (when known) a line number.
    for violation in payload["violations"]:
        assert violation["file"].endswith(".mk")
        assert violation["line"] is None or isinstance(violation["line"], int)


def test_check_json_compliant_has_no_violations(run_cli, compliant_tree, tmp_path):
    out_json = tmp_path / "report.json"
    rc = run_cli("check", str(compliant_tree), "--json", str(out_json))
    assert rc == 0
    payload = json.loads(out_json.read_text())
    assert payload["violation_count"] == 0
    assert payload["violations"] == []


def test_check_missing_dir_returns_1(run_cli):
    rc = run_cli("check", "/nope/does/not/exist")
    assert rc == 1


# --------------------------------------------------------------------------- #
# fix-hints: prints file:line + fix hint for each violation.
# --------------------------------------------------------------------------- #
def test_fix_hints_reports_ok(run_cli, compliant_tree, capsys):
    rc = run_cli("fix-hints", str(compliant_tree / "good.mk"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "recipe: good" in out
    assert "present" in out
    assert "OK" in out


def test_fix_hints_reports_hash(run_cli, recipe_tree, capsys):
    rc = run_cli("fix-hints", str(recipe_tree / "good.mk"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "recipe: good" in out
    assert "present" in out


def test_fix_hints_flags_missing_hash(run_cli, recipe_tree, capsys):
    rc = run_cli("fix-hints", str(recipe_tree / "sub" / "bare.mk"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "recipe: bare" in out
    assert "missing sibling" in out
    # A missing-variable violation renders just the file path.
    assert ".mk" in out
    # Fix hints are printed.
    assert "hint:" in out


def test_fix_hints_missing_file_returns_1(run_cli):
    rc = run_cli("fix-hints", "/nope/does/not/exist.mk")
    assert rc == 1


def test_fix_hints_non_mk_returns_1(run_cli):
    rc = run_cli("fix-hints", __file__)
    assert rc == 1


def test_missing_command_is_usage_error(run_cli):
    with pytest.raises(SystemExit) as exc:
        run_cli()
    assert exc.value.code == 2


def test_version_flag(run_cli, capsys):
    with pytest.raises(SystemExit) as exc:
        run_cli("--version")
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_module_invocation(compliant_tree, tmp_path):
    """`python -m pengyos_recipe_lint` works end to end (compliant tree -> exit 0)."""
    out_json = tmp_path / "report.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pengyos_recipe_lint",
            "check",
            str(compliant_tree),
            "--json",
            str(out_json),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out_json.exists()
    payload = json.loads(out_json.read_text())
    assert payload["count"] == 2
    assert payload["violation_count"] == 0
