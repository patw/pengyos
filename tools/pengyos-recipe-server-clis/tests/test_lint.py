"""Tests for the Buildroot recipe linter (``pengyos_recipe_server_clis.lint``).

Covers the "Implement lint" subtask.  The linter is the *reader* half of the
emitter's contract: it parses an emitted ``<tool>.mk`` and cross-checks it
against the sibling ``<tool>.hash`` and the Buildroot recipe grammar, enforcing
the four grammar rules borrowed from ``pengyos-recipe-lint``:

===================  ================================================
rule                 what it checks
===================  ================================================
``version_pinned``  ``<TOOL>_VERSION`` is a concrete pin
``hash_present``     a sibling ``<tool>.hash`` carries a ``sha256``
                     covering ``<TOOL>_SOURCE``
``license_declared`` ``<TOOL>_LICENSE`` is non-empty
``license_file_declared`` ``<TOOL>_LICENSE_FILES`` is non-empty
``source_url_present`` ``<TOOL>_SITE`` is an absolute ``https`` URL /
                     ``$(call github, …)`` macro
===================  ================================================

The spec's headline assertion — *"lint rejects a missing .hash, an unpinned
version, and an undeclared license (one mutation each)"* — is covered by the
``test_*_rejected`` cases below, which lint a *mutated* copy of the emitted
recipes and assert the target rule fires.  The linter is reused here directly
(``lint.lint_tool`` / ``lint.lint_dir``), exactly as the spec asks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pengyos_recipe_server_clis import emit, lint
from pengyos_recipe_server_clis.catalogue import TOOL_NAMES, load_catalogue

# The five lint rules, in report order — the contract, taken from lint.py's own
# RULES tuple so a rename here is a real failure.
RULES = lint.RULES


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _emit_into(tmp_path: Path) -> Path:
    """Emit all recipes into a fresh dir; return the ``package/`` path."""
    emit.emit_all(load_catalogue(), tmp_path)
    return tmp_path / "package"


def _read_mk(pkg_dir: Path, name: str) -> str:
    return (pkg_dir / name / f"{name}.mk").read_text(encoding="utf-8")


def _replace(pkg_dir: Path, name: str, old: str, new: str) -> None:
    """Replace *old* with *new* in ``<name>.mk`` (raises if absent)."""
    path = pkg_dir / name / f"{name}.mk"
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{name}.mk does not contain {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _violations(results: list[lint.LintResult]) -> set[str]:
    """Union of all violated rule names across every tool."""
    return {f.rule for r in results for f in r.violations}


def _tool_results(
    results: list[lint.LintResult], name: str
) -> list[lint.LintResult]:
    return [r for r in results if r.tool == name]


# --------------------------------------------------------------------------- #
# golden: the emitted recipes are lint-clean
# --------------------------------------------------------------------------- #


def test_emitted_recipes_are_lint_clean(tmp_path: Path) -> None:
    """Every emitted recipe passes every rule — the baseline the suite relies on."""
    pkg = _emit_into(tmp_path)
    results = lint.lint_dir(pkg)
    assert sorted(r.tool for r in results) == sorted(TOOL_NAMES)
    for r in results:
        assert r.ok, f"{r.tool}: unexpected violations: {r.violations}"
        assert len(r.findings) == len(RULES), (
            f"{r.tool}: expected {len(RULES)} findings, got {len(r.findings)}"
        )


def test_lint_dir_output_root_and_package_and_single_tool(tmp_path: Path) -> None:
    """lint --dir works on the output root, the package/ dir, and a single tool."""
    root = tmp_path / "out"
    emit.emit_all(load_catalogue(), root)
    package = root / "package"

    by_output_root = lint.lint_dir(root)
    by_package = lint.lint_dir(package)
    by_single_tool = lint.lint_dir(package / "zoxide")

    assert sorted(r.tool for r in by_output_root) == sorted(TOOL_NAMES)
    assert sorted(r.tool for r in by_package) == sorted(TOOL_NAMES)
    assert [r.tool for r in by_single_tool] == ["zoxide"]

    # All three agree: the emitted recipes are clean.
    assert all(r.ok for r in by_output_root + by_package + by_single_tool)


def test_discover_tool_dirs_three_layouts(tmp_path: Path) -> None:
    """discover_tool_dirs accepts the emit root, package/, and a bare tool dir."""
    root = tmp_path / "out"
    emit.emit_all(load_catalogue(), root)
    package = root / "package"

    assert sorted(p.name for p in lint.discover_tool_dirs(root)) == sorted(TOOL_NAMES)
    assert sorted(p.name for p in lint.discover_tool_dirs(package)) == sorted(TOOL_NAMES)
    assert [p.name for p in lint.discover_tool_dirs(package / "zoxide")] == ["zoxide"]
    # A single tool dir is detected by its top-level <name>.mk.
    assert [p.name for p in lint.discover_tool_dirs(package / "sd")] == ["sd"]


def test_lint_anything_rejects_empty_target(tmp_path: Path) -> None:
    """lint_anything raises when no recipe directory is found under the target."""
    with pytest.raises(ValueError):
        lint.lint_anything(tmp_path / "does-not-exist")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        lint.lint_anything(empty)


# --------------------------------------------------------------------------- #
# per-rule parse + check (isolated, one rule each)
# --------------------------------------------------------------------------- #


def test_parse_recipe_reads_all_fields() -> None:
    """parse_recipe extracts VERSION / SITE / LICENSE / LICENSE_FILES / hash source."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    emit.emit_tool(load_catalogue()[0], d)  # zoxide
    pkg = d / "package" / "zoxide"
    r = lint.parse_recipe(pkg)
    assert r.name == "zoxide"
    assert r.version == "0.10.0"
    assert r.site == "$(call github,ajeetdsouza,zoxide,v0.10.0)"
    assert r.license == "MIT"
    assert r.license_file == "LICENSE"
    assert r.hash_source == "zoxide-0.10.0.tar.gz"


def test_parse_recipe_missing_mk_raises() -> None:
    """parse_recipe raises FileNotFoundError when <name>.mk is absent."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    with pytest.raises(FileNotFoundError):
        lint.parse_recipe(d / "nope")


def test_parse_recipe_strips_trailing_comment() -> None:
    """A trailing ``# comment`` on an mk RHS is stripped before the value."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    emit.emit_tool(load_catalogue()[0], d)
    pkg = d / "package" / "zoxide"
    mk = pkg / "zoxide.mk"
    mk.write_text(mk.read_text() + "ZOXIDE_LICENSE = MIT # inline\n")
    r = lint.parse_recipe(pkg)
    assert r.license == "MIT"


def test_hash_source_none_when_missing() -> None:
    """_hash_source_of returns None when the .hash file is absent."""
    import tempfile

    assert lint._hash_source_of(Path(tempfile.mkdtemp())) is None


def test_version_pinned_passes_on_emitted(tmp_path: Path) -> None:
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_VERSION_PINNED])
    assert r.ok
    assert r.findings[0].message.startswith("zoxide: version 0.10.0 pinned")


def test_version_pinned_rejects_floating_latest(tmp_path: Path) -> None:
    """A floating `latest` version fails version_pinned (the spec's floating case)."""
    pkg = _emit_into(tmp_path)
    _replace(pkg, "zoxide", "ZOXIDE_VERSION = 0.10.0", "ZOXIDE_VERSION = latest")
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_VERSION_PINNED])
    assert not r.ok
    assert "not a concrete pin" in r.findings[0].message


def test_hash_present_passes_on_emitted(tmp_path: Path) -> None:
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_HASH_PRESENT])
    assert r.ok


def test_hash_present_rejects_missing_hash(tmp_path: Path) -> None:
    """A missing sibling .hash fails hash_present (the spec's missing-hash case)."""
    pkg = _emit_into(tmp_path)
    (pkg / "zoxide" / "zoxide.hash").unlink()
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_HASH_PRESENT])
    assert not r.ok
    assert "missing" in r.findings[0].message


def test_license_declared_passes_on_emitted(tmp_path: Path) -> None:
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_LICENSE_DECLARED])
    assert r.ok


def test_license_declared_rejects_undeclared(tmp_path: Path) -> None:
    """An empty license fails license_declared (the spec's undeclared-license case)."""
    pkg = _emit_into(tmp_path)
    _replace(pkg, "zoxide", "ZOXIDE_LICENSE = MIT", "ZOXIDE_LICENSE = ")
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_LICENSE_DECLARED])
    assert not r.ok
    assert "missing / empty" in r.findings[0].message


def test_license_file_declared_passes_on_emitted(tmp_path: Path) -> None:
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "neovim", rules=[lint.RULE_LICENSE_FILE_DECLARED])
    assert r.ok
    assert "LICENSE.txt" in r.findings[0].message


def test_license_file_declared_rejects_missing(tmp_path: Path) -> None:
    """An empty LICENSE_FILES fails license_file_declared."""
    pkg = _emit_into(tmp_path)
    _replace(pkg, "zoxide", "ZOXIDE_LICENSE_FILES = LICENSE", "ZOXIDE_LICENSE_FILES = ")
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_LICENSE_FILE_DECLARED])
    assert not r.ok


def test_source_url_present_passes_on_emitted(tmp_path: Path) -> None:
    """The emitted ``$(call github, …)`` SITE is accepted as an https source."""
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_SOURCE_URL_PRESENT])
    assert r.ok


def test_source_url_present_rejects_http(tmp_path: Path) -> None:
    """A bare `http://` source fails source_url_present (unverified download)."""
    pkg = _emit_into(tmp_path)
    _replace(
        pkg,
        "zoxide",
        "$(call github,ajeetdsouza,zoxide,v0.10.0)",
        "http://example.com/zoxide.tar.gz",
    )
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_SOURCE_URL_PRESENT])
    assert not r.ok
    assert "not an absolute https" in r.findings[0].message


def test_source_url_present_accepts_literal_https(tmp_path: Path) -> None:
    """A literal https SITE also passes (the rule accepts both shapes)."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    emit.emit_tool(load_catalogue()[0], d)
    pkg = d / "package" / "zoxide"
    (pkg / "zoxide.mk").write_text(
        (pkg / "zoxide.mk").read_text().replace(
            "$(call github,ajeetdsouza,zoxide,v0.10.0)",
            "https://github.com/ajeetdsouza/zoxide/archive/v0.10.0.tar.gz",
        )
    )
    r = lint.lint_tool(pkg, rules=[lint.RULE_SOURCE_URL_PRESENT])
    assert r.ok


# --------------------------------------------------------------------------- #
# the headline contract — one mutation each, rejected
# --------------------------------------------------------------------------- #


def test_reject_missing_hash(tmp_path: Path) -> None:
    """lint rejects a recipe with a missing .hash (one mutation each)."""
    pkg = _emit_into(tmp_path)
    (pkg / "zoxide" / "zoxide.hash").unlink()
    results = lint.lint_dir(pkg)
    assert _violations(results) >= {lint.RULE_HASH_PRESENT}
    assert any(not r.ok for r in results)


def test_reject_unpinned_version(tmp_path: Path) -> None:
    """lint rejects a recipe with a floating `latest` version."""
    pkg = _emit_into(tmp_path)
    _replace(pkg, "zoxide", "ZOXIDE_VERSION = 0.10.0", "ZOXIDE_VERSION = latest")
    results = lint.lint_dir(pkg)
    assert lint.RULE_VERSION_PINNED in _violations(results)
    assert any(not r.ok for r in results)


def test_reject_undeclared_license(tmp_path: Path) -> None:
    """lint rejects a recipe with an undeclared (empty) license."""
    pkg = _emit_into(tmp_path)
    _replace(pkg, "zoxide", "ZOXIDE_LICENSE = MIT", "ZOXIDE_LICENSE = ")
    results = lint.lint_dir(pkg)
    assert lint.RULE_LICENSE_DECLARED in _violations(results)
    assert any(not r.ok for r in results)


def test_reject_all_mutations_at_once(tmp_path: Path) -> None:
    """Mutating every rule at once trips all five rules (the full rejection set)."""
    pkg = _emit_into(tmp_path)
    (pkg / "zoxide" / "zoxide.hash").unlink()
    _replace(pkg, "zoxide", "ZOXIDE_VERSION = 0.10.0", "latest")
    _replace(pkg, "zoxide", "ZOXIDE_LICENSE = MIT", "ZOXIDE_LICENSE = ")
    _replace(pkg, "zoxide", "ZOXIDE_LICENSE_FILES = LICENSE", "ZOXIDE_LICENSE_FILES = ")
    _replace(
        pkg,
        "zoxide",
        "$(call github,ajeetdsouza,zoxide,v0.10.0)",
        "http://example.com/zoxide.tar.gz",
    )
    results = lint.lint_dir(pkg)
    zoxide = _tool_results(results, "zoxide")[0]
    assert not zoxide.ok
    assert _violations(results) == set(RULES)


# --------------------------------------------------------------------------- #
# rule selection + result shape
# --------------------------------------------------------------------------- #


def test_rule_selection_filters_results(tmp_path: Path) -> None:
    """Only the requested rules are reported; the rest are omitted."""
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "zoxide", rules=[lint.RULE_VERSION_PINNED])
    assert [f.rule for f in r.findings] == [lint.RULE_VERSION_PINNED]
    assert r.ok


def test_lintresult_ok_and_violations_accessors(tmp_path: Path) -> None:
    """LintResult.ok is True iff every finding passed; .violations lists the rest."""
    pkg = _emit_into(tmp_path)
    clean = lint.lint_tool(pkg / "zoxide")
    assert clean.ok and clean.violations == []

    (pkg / "sd" / "sd.hash").unlink()
    broken = lint.lint_tool(pkg / "sd")
    assert not broken.ok
    assert [f.rule for f in broken.violations] == [lint.RULE_HASH_PRESENT]


def test_result_to_dict_shape(tmp_path: Path) -> None:
    """LintResult.to_dict / Finding.to_dict are JSON-serialisable dicts."""
    pkg = _emit_into(tmp_path)
    r = lint.lint_tool(pkg / "zoxide")
    d = r.to_dict()
    assert set(d) == {"tool", "ok", "violations"}
    assert d["tool"] == "zoxide" and d["ok"] is True
    assert d["violations"] == []


# --------------------------------------------------------------------------- #
# CLI: lint exit codes, output, and --json
# --------------------------------------------------------------------------- #


def test_cli_lint_clean_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pkg = _emit_into(tmp_path)
    assert lint.main(["lint", "--dir", str(pkg)]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "6 recipe(s)" in out


def test_cli_lint_violation_exit_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pkg = _emit_into(tmp_path)
    (pkg / "zoxide" / "zoxide.hash").unlink()
    assert lint.main(["lint", "--dir", str(pkg)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "violation" in out


def test_cli_lint_json_is_valid_and_reports_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pkg = _emit_into(tmp_path)
    assert lint.main(["lint", "--dir", str(pkg), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is True
    assert {r["tool"] for r in payload["results"]} == set(TOOL_NAMES)
    for r in payload["results"]:
        assert r["ok"] is True


def test_cli_lint_json_reports_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pkg = _emit_into(tmp_path)
    (pkg / "zoxide" / "zoxide.hash").unlink()
    assert lint.main(["lint", "--dir", str(pkg), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is False
    zoxide = next(r for r in payload["results"] if r["tool"] == "zoxide")
    assert zoxide["ok"] is False
    assert zoxide["violations"][0]["rule"] == lint.RULE_HASH_PRESENT


def test_cli_lint_unknown_rule_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        lint.main(["lint", "--dir", "x", "--rules", "bogus"])
    assert exc.value.code != 0


def test_cli_lint_subset_rules(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--rules selects a subset; only those rules are reported."""
    pkg = _emit_into(tmp_path)
    assert lint.main(["lint", "--dir", str(pkg), "--rules", "version_pinned"]) == 0
    out = capsys.readouterr().out
    assert "version_pinned" in out


def test_lint_cli_isolated_from_emit_cli() -> None:
    """The lint CLI entry point is its own parser (no list/emit subcommands leak in)."""
    parser = lint.build_parser()
    # `lint` parses cleanly and selects the lint subcommand.
    args = parser.parse_args(["lint", "--dir", "x"])
    assert args.command == "lint"
    # The emit / list subcommands belong to emit.py's parser, not lint's.
    for leaked in ("emit", "list"):
        with pytest.raises(SystemExit):
            parser.parse_args([leaked, "--dir", "x"])


# --------------------------------------------------------------------------- #
# top-level CLI: ``emit.main(["lint", ...])`` delegates to the lint module
# --------------------------------------------------------------------------- #


def _emit_root(tmp_path: Path) -> Path:
    """Emit all recipes into a fresh dir; return the *output root* (not package/)."""
    emit.emit_all(load_catalogue(), tmp_path)
    return tmp_path


def test_emit_cli_exposes_lint_subcommand() -> None:
    """The top-level parser exposes a ``lint`` subcommand (spec interface)."""
    parser = emit.build_parser()
    args = parser.parse_args(["lint", "--dir", "x"])
    assert args.command == "lint"
    # The list / emit subcommands still live on the top-level parser.
    assert parser.parse_args(["list"]).command == "list"
    assert parser.parse_args(["emit", "--out", "x"]).command == "emit"


def test_emit_cli_lint_clean_delegates_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``emit.main(["lint", ...])`` delegates to lint.py and exits 0 on clean."""
    root = _emit_root(tmp_path)
    assert emit.main(["lint", "--dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "6 recipe(s)" in out


def test_emit_cli_lint_violation_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing .hash surfaces through the top-level CLI as exit code 1."""
    root = _emit_root(tmp_path)
    (root / "package" / "zoxide" / "zoxide.hash").unlink()
    assert emit.main(["lint", "--dir", str(root)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "violation" in out


def test_emit_cli_lint_json_is_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``emit lint --json`` returns valid JSON with ``all_ok`` and per-tool results."""
    root = _emit_root(tmp_path)
    assert emit.main(["lint", "--dir", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is True
    assert {r["tool"] for r in payload["results"]} == set(TOOL_NAMES)


def test_emit_cli_lint_subset_rules_propagates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--rules`` on the top-level CLI reaches the lint module (subset reported)."""
    root = _emit_root(tmp_path)
    assert emit.main(["lint", "--dir", str(root), "--rules", "version_pinned"]) == 0
    assert "version_pinned" in capsys.readouterr().out
