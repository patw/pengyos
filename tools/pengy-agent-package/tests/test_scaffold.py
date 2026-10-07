"""Scaffold-level tests.

Covers the scaffold deliverable:

* ``pyproject.toml`` is valid PEP 621 metadata (name, version, MIT,
  ``setuptools`` backend, pytest configured to run ``tests/`` from the tool
  root);
* the ``package/pengy/`` Buildroot recipe directory exists;
* the ``tests/`` harness (``conftest.py`` path fix-up + the ``recipe_check``
  parser) is in place and the parser correctly joins make line-continuations and
  reads kconfig blocks (unit-tested against a synthetic fixture so the helper has
  teeth before the real ``.mk`` lands in item 2).
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PACKAGE_DIR = REPO_ROOT / "package" / "pengy"
TESTS_DIR = REPO_ROOT / "tests"


def _tracked_files(directory: Path) -> list[Path]:
    """Return the git-tracked files under ``directory`` (repo-root relative)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", str(directory)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / p for p in out.splitlines() if p.strip()]


# --- pyproject metadata -----------------------------------------------------


def test_pyproject_is_valid_pep621():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["name"] == "pengy-agent-package"
    assert data["project"]["version"]
    assert data["project"]["license"]["text"] == "MIT"
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"


def test_pyproject_configures_pytest_on_tests_dir():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pytest_cfg = data["tool"]["pytest"]["ini_options"]
    assert pytest_cfg["testpaths"] == ["tests"]


def test_pyproject_version_is_semver():
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    parts = version.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# --- package directory ------------------------------------------------------


def test_package_dir_exists():
    assert PACKAGE_DIR.is_dir(), "package/pengy/ directory must exist"


def test_package_dir_is_tracked_by_git():
    # An empty directory is not tracked by git, so the scaffold dropped a
    # .gitkeep placeholder (item 1). The real recipe files (item 2) will replace
    # it, so the directory is tracked via those files — assert the directory has
    # at least one tracked file (forward-compatible with the .mk landing later).
    tracked = _tracked_files(PACKAGE_DIR)
    assert tracked, (
        "package/pengy/ has no git-tracked files (an empty dir would not be "
        "tracked by git)"
    )


# --- recipe_check parser (unit-tested against a synthetic fixture) ----------

_SAMPLE_MK = """\
PENGY_VERSION = 0.4.0
PENGY_SOURCE = pengy-$(PENGY_VERSION).tar.gz
PENGY_SITE = https://github.com/patw/PengyR/releases/download/v$(PENGY_VERSION)
PENGY_LICENSE = \\
\tMIT, \\
\tApache-2.0 and/or MIT (subproject)
# a comment line
PENGY_LICENSE_FILES = LICENSE
"""

_SAMPLE_KCONFIG = """\
config BR2_PACKAGE_PENGY
\tbool "pengy"
\tdepends on BR2_USE_MMU
\tdepends on BR2_PACKAGE_SYSTEMD
\thelp
\t  PengyR agent (pengy-cli + pengy-web), headless.
"""


def test_mk_variable_single_line():
    assert recipe_check.mk_variable(_SAMPLE_MK, "PENGY_VERSION") == "0.4.0"


def test_mk_variable_continuations_joined():
    value = recipe_check.mk_variable(_SAMPLE_MK, "PENGY_LICENSE")
    assert "MIT" in value
    assert "Apache-2.0" in value
    # the trailing backslash + newline collapse, no stray "\\" remains
    assert "\\" not in value


def test_mk_variable_undefined_returns_empty():
    assert recipe_check.mk_variable(_SAMPLE_MK, "NOPE") == ""


def test_mk_defined():
    assert recipe_check.mk_defined(_SAMPLE_MK, "PENGY_VERSION")
    assert not recipe_check.mk_defined(_SAMPLE_MK, "NOPE")


def test_mk_license_values_split_on_and_or():
    value = recipe_check.mk_variable(_SAMPLE_MK, "PENGY_LICENSE")
    assert "MIT" in value
    assert "Apache-2.0" in value
    # the and/or boundary is a real separator between two license clauses
    values = recipe_check.mk_license_values(_SAMPLE_MK)
    assert len(values) >= 2
    assert any("Apache-2.0" in v for v in values)


def test_installed_systemd_units_scans_target_dir():
    text = '\t$(INSTALL) -D -m 0644 $(X)/pengy-web.service \\\n\t$(TARGET_DIR)/usr/lib/systemd/system/pengy-web.service\n'
    units = recipe_check.installed_systemd_units(text)
    assert units == ["systemd/system/pengy-web.service"]


def test_installed_init_files_scans_target_dir():
    text = (
        "\t$(INSTALL) -D -m 0644 $(PENGY_PKGDIR)/settings.json \\\n"
        "\t$(TARGET_DIR)/etc/pengy/settings.json\n"
    )
    dests = recipe_check.installed_init_files(text)
    assert "etc/pengy/settings.json" in dests


def test_kconfig_block_and_depends():
    block = recipe_check.kconfig_block(_SAMPLE_KCONFIG, "BR2_PACKAGE_PENGY")
    assert "BR2_PACKAGE_PENGY" in block
    depends = recipe_check.kconfig_depends_on(block)
    assert "BR2_PACKAGE_SYSTEMD" in depends
    assert "BR2_USE_MMU" in depends


def test_kconfig_block_missing():
    assert recipe_check.kconfig_block(_SAMPLE_KCONFIG, "BR2_PACKAGE_NOPE") == ""


# --- harness ----------------------------------------------------------------


def test_conftest_present():
    assert (TESTS_DIR / "conftest.py").is_file()


def test_recipe_check_helper_present():
    assert (TESTS_DIR / "recipe_check.py").is_file()


def test_recipe_check_exposes_public_api():
    """Smoke test: the shared parser exposes the functions the suite needs."""
    for name in (
        "mk_variable",
        "mk_defined",
        "mk_license_values",
        "installed_systemd_units",
        "installed_init_files",
        "kconfig_block",
        "kconfig_depends_on",
    ):
        assert callable(getattr(recipe_check, name)), f"recipe_check.{name} missing"
