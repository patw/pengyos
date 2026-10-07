"""Tests for the Buildroot recipe linter (``pengyos_cosmic_pkg.lint``).

These assert that :func:`lint_dir` reports the four reproducible-build
violations the engine cares about — missing ``.hash``, unpinned ``_VERSION``,
undeclared ``_LICENSE`` and a floating git ``_SITE`` — and that a fixture
vendor -> recipe run is lint-clean. They also cover the CLI surface
(clean/violation / JSON / missing dir).

No network, root, display, or hardware.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pengyos_cosmic_pkg.cargo import load_lock
from pengyos_cosmic_pkg.lint import LintReport, LintViolation, lint_dir
from pengyos_cosmic_pkg.manifest import load_manifest
from pengyos_cosmic_pkg.recipe import build_recipe, read_source_hash
from pengyos_cosmic_pkg.vendor import vend

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMPONENT = FIXTURES / "component.toml"
WORKSPACE = FIXTURES / "workspace"
FIXTURE_LOCK = WORKSPACE / "Cargo.lock"
FIXTURE_GIT_SOURCES = WORKSPACE / "git-sources"

NAME = "cosmic-demo"
SYMBOL = "BR2_PACKAGE_COSMIC_DEMO"
VERSION = "v0.1.0"
TARBALL = f"{NAME}-{VERSION}.tar.gz"


# --------------------------------------------------------------------------- #
# helpers — build a package directory from scratch
# --------------------------------------------------------------------------- #


def _mk_text(
    pkg: str,
    *,
    version: str | None = None,
    license_: str | None = None,
    site: str | None = None,
) -> str:
    """A minimal ``<pkg>.mk`` body with the keys the caller wants set."""
    symbol = f"BR2_PACKAGE_{pkg.upper().replace('-', '_')}"
    lines = []
    if version is not None:
        lines.append(f"{symbol}_VERSION = {version}")
    if site is not None:
        lines.append(f"{symbol}_SITE = {site}")
    if license_ is not None:
        lines.append(f"{symbol}_LICENSE = {license_}")
    lines.append("")
    lines.append("$(eval $(cargo-package))")
    lines.append("")
    return "\n".join(lines)


def _write_pkg(
    directory: Path,
    pkg: str,
    *,
    mk: str | None,
    with_hash: bool,
    with_config: bool = True,
) -> Path:
    """Write a ``package/<pkg>/`` directory (mk / hash / Config.in)."""
    directory.mkdir(parents=True, exist_ok=True)
    if mk is not None:
        (directory / f"{pkg}.mk").write_text(mk, encoding="utf-8")
    if with_hash:
        (directory / f"{pkg}.hash").write_text(
            f"sha256  {'d' * 64}  {TARBALL}\n", encoding="utf-8"
        )
    if with_config:
        (directory / "Config.in").write_text(
            f'config {SYMBOL}\n\tbool "{pkg}"\n', encoding="utf-8"
        )
    return directory


def _fixture_recipe_dir(tmp_path: Path) -> Path:
    """Vendor + recipe the fixture component; return the package dir."""
    manifest = load_manifest(COMPONENT)
    lock_path = tmp_path / "Cargo.lock"
    lock_path.write_text(FIXTURE_LOCK.read_text(), encoding="utf-8")
    lock = load_lock(lock_path)
    vendored = vend(manifest, lock, FIXTURE_GIT_SOURCES, tmp_path / "vend")
    source_hash = read_source_hash(vendored.hash_path)
    result = build_recipe(
        manifest, source_hash=source_hash, out_dir=tmp_path / "package"
    )
    return result.mk_path.parent


# --------------------------------------------------------------------------- #
# lint_dir — the clean case
# --------------------------------------------------------------------------- #


def test_lint_clean_on_fixture_recipe(tmp_path) -> None:
    """A real vendor -> recipe run is lint-clean."""
    pkg_dir = _fixture_recipe_dir(tmp_path)
    report = lint_dir(pkg_dir)
    assert report.is_clean
    assert bool(report) is True
    assert str(report).startswith(str(pkg_dir))
    assert "clean" in str(report)


def test_lint_report_str_lists_violations() -> None:
    report = LintReport(
        Path("/x"),
        (
            LintViolation("hash", "no .hash"),
            LintViolation("version", "unpinned"),
        ),
    )
    text = str(report)
    assert "2 violation(s)" in text
    assert "hash: no .hash" in text
    assert "version: unpinned" in text


def test_lint_report_bool_is_false_with_violations() -> None:
    report = LintReport(Path("/x"), (LintViolation("hash", "x"),))
    assert report.is_clean is False
    assert bool(report) is False


# --------------------------------------------------------------------------- #
# rule 1 — hash present
# --------------------------------------------------------------------------- #


def test_lint_reports_missing_hash(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg", NAME, mk=_mk_text(NAME, version=VERSION, license_="MIT"),
        with_hash=False,
    )
    report = lint_dir(pkg_dir)
    assert report.is_clean is False
    rules = [v.rule for v in report.violations]
    assert rules == ["hash"]
    assert "no <pkg>.hash" in report.violations[0].message


def test_lint_hash_present_ok(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg", NAME, mk=_mk_text(NAME, version=VERSION, license_="MIT"),
        with_hash=True,
    )
    assert lint_dir(pkg_dir).is_clean


# --------------------------------------------------------------------------- #
# rule 2 — version pinned
# --------------------------------------------------------------------------- #


def test_lint_reports_unpinned_version(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version="main", license_="MIT"),
        with_hash=True,
    )
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert rules == ["version"]
    assert "not pinned" in report.violations[0].message
    assert "main" in report.violations[0].message


def test_lint_reports_missing_version(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version=None, license_="MIT"),
        with_hash=True,
    )
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert rules == ["version"]
    assert "VERSION" in report.violations[0].message


def test_lint_pinned_version_ok(tmp_path) -> None:
    for good in ("v0.1.0", "0.1.0", "abc1234", "v1.2.3-rc1"):
        pkg_dir = _write_pkg(
            tmp_path / f"pkg-{good}",
            NAME,
            mk=_mk_text(NAME, version=good, license_="MIT"),
            with_hash=True,
        )
        assert lint_dir(pkg_dir).is_clean, f"{good} should be pinned"


# --------------------------------------------------------------------------- #
# rule 3 — license declared
# --------------------------------------------------------------------------- #


def test_lint_reports_undeclared_license(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version=VERSION, license_=None),
        with_hash=True,
    )
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert rules == ["license"]
    assert "LICENSE" in report.violations[0].message


def test_lint_license_declared_ok(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version=VERSION, license_="MIT OR Apache-2.0"),
        with_hash=True,
    )
    assert lint_dir(pkg_dir).is_clean


# --------------------------------------------------------------------------- #
# rule 4 — no floating git ref
# --------------------------------------------------------------------------- #


def test_lint_reports_floating_git_site(tmp_path) -> None:
    # A git URL with a branch query and no pinned fragment floats: the build
    # resolves whatever the branch points at.
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(
            NAME,
            version=VERSION,
            license_="MIT",
            site="git+https://github.com/pop-os/foo?branch=main",
        ),
        with_hash=True,
    )
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert rules == ["floating-ref"]
    assert "floating" in report.violations[0].message.lower()


def test_lint_reports_floating_git_site_without_query(tmp_path) -> None:
    # A bare git URL (no fragment at all) also floats.
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(
            NAME,
            version=VERSION,
            license_="MIT",
            site="git+https://github.com/pop-os/foo",
        ),
        with_hash=True,
    )
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert rules == ["floating-ref"]


def test_lint_pinned_git_site_ok(tmp_path) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(
            NAME,
            version=VERSION,
            license_="MIT",
            site="git+https://github.com/pop-os/foo#deadbeef",
        ),
        with_hash=True,
    )
    assert lint_dir(pkg_dir).is_clean


def test_lint_file_site_ok_not_a_git_ref(tmp_path) -> None:
    # The normal cargo recipe site (a local vendored tarball) is not a git URL.
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(
            NAME,
            version=VERSION,
            license_="MIT",
            site="file:///opt/vendored",
        ),
        with_hash=True,
    )
    assert lint_dir(pkg_dir).is_clean


def test_lint_no_site_ok(tmp_path) -> None:
    # No *_SITE at all (a path/registry recipe) is fine.
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version=VERSION, license_="MIT"),
        with_hash=True,
    )
    assert lint_dir(pkg_dir).is_clean


# --------------------------------------------------------------------------- #
# combined — multiple violations reported in rule order
# --------------------------------------------------------------------------- #


def test_lint_reports_all_violations_in_order(tmp_path) -> None:
    # No hash, unpinned version, no license, floating git site.
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(
            NAME,
            version="main",
            license_=None,
            site="git+https://github.com/pop-os/foo?branch=main",
        ),
        with_hash=False,
    )
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert rules == ["hash", "version", "license", "floating-ref"]


# --------------------------------------------------------------------------- #
# empty / missing recipe
# --------------------------------------------------------------------------- #


def test_lint_empty_dir_reports_no_recipe(tmp_path) -> None:
    pkg_dir = tmp_path / "empty"
    pkg_dir.mkdir()
    report = lint_dir(pkg_dir)
    rules = [v.rule for v in report.violations]
    assert "hash" in rules
    assert "version" in rules  # no .mk -> no recipe to pin
    assert report.is_clean is False


def test_lint_missing_directory_raises(tmp_path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        lint_dir(missing)


# --------------------------------------------------------------------------- #
# CLI: lint end to end
# --------------------------------------------------------------------------- #


def test_cli_lint_clean(run_cli, tmp_path, capsys) -> None:
    pkg_dir = _fixture_recipe_dir(tmp_path)
    rc = run_cli("lint", "--dir", str(pkg_dir))
    assert rc == 0
    out = capsys.readouterr().out
    assert "clean" in out


def test_cli_lint_violation_exits_nonzero(run_cli, tmp_path, capsys) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version="main", license_=None),
        with_hash=False,
    )
    rc = run_cli("lint", "--dir", str(pkg_dir))
    assert rc == 1
    err = capsys.readouterr().err
    assert "hash" in err
    assert "version" in err
    assert "license" in err


def test_cli_lint_json_mode(run_cli, tmp_path, capsys) -> None:
    pkg_dir = _fixture_recipe_dir(tmp_path)
    rc = run_cli("lint", "--dir", str(pkg_dir), "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "lint"
    assert payload["ok"] is True
    assert payload["violations"] == []


def test_cli_lint_json_reports_violations(run_cli, tmp_path, capsys) -> None:
    pkg_dir = _write_pkg(
        tmp_path / "pkg",
        NAME,
        mk=_mk_text(NAME, version="main", license_=None),
        with_hash=False,
    )
    rc = run_cli("lint", "--dir", str(pkg_dir), "--json")
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    rules = {v["rule"] for v in payload["violations"]}
    assert {"hash", "version", "license"} <= rules


def test_cli_lint_missing_dir(run_cli, capsys, tmp_path) -> None:
    rc = run_cli("lint", "--dir", str(tmp_path / "nope"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "file not found" in err


def test_cli_lint_missing_dir_json(run_cli, capsys, tmp_path) -> None:
    rc = run_cli("lint", "--dir", str(tmp_path / "nope"), "--json")
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "file not found" in payload["error"]
