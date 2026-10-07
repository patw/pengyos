"""Tests for the pengyos-cosmic-build lint engine (:mod:`lint`).

:func:`lint` asserts the wired ``BR2_EXTERNAL`` tree is buildable-shaped: no
leftover placeholder ``SITE`` / hash, every ``BR2_PACKAGE_*`` the desktop
defconfig references resolves to a recipe, the native deps the defconfig
references are present, and the ``pengyos-desktop`` target is registered in
``targets.toml``.

The pure helpers are unit-tested directly; the four checks are grounded in a
full emit + wire into a temp tree (the "clean" case), with one mutation per
rule that fires exactly one category; the CLI path is exercised as a
subprocess (with the sibling lanes on ``PYTHONPATH``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pengyos_cosmic_build.lint import (
    Issue,
    LintCategory,
    LintReport,
    _defconfig_referenced_symbols,
    _native_dep_reference_symbols,
    _parse_config_symbols,
    _placeholder_hash_violations,
    _placeholder_site_violations,
    _read_defconfig,
    _recipe_symbols,
    _target_registered,
    lint_repo,
)
from pengyos_cosmic_build.emit import emit_group
from pengyos_cosmic_build.records import VENDORED_SITE_PLACEHOLDER, VendorRecord
from pengyos_cosmic_build.records import write_record
from pengyos_cosmic_build.wire import wire

# --- fixtures: a cosmic-recipes manifest + vendor records (from test_emit) ----


def _manifest(tmp_path: Path) -> Path:
    """A cosmic-recipes-shape manifest (matches the committed fixture shape)."""
    text = """
[[component]]
name = "cosmic-comp"
group = "core"
git_url = "https://github.com/pop-os/libcosmic/cosmic-comp"
tag = "v0.6.0"
license = "MIT OR Apache-2.0"
features = []
binaries = ["cosmic-comp"]
deps = []

[[component]]
name = "cosmic-protocols"
group = "core"
git_url = "https://github.com/pop-os/cosmic-protocols"
tag = "v0.6.0"
license = "MIT"
features = []
binaries = []
deps = []
"""
    path = tmp_path / "cosmic.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _record(
    name: str,
    *,
    version: str = "v0.6.0",
    sha256: str = "a" * 64,
    site: str | None = None,
) -> VendorRecord:
    """A valid VendorRecord, overridable per-test."""
    return VendorRecord(
        name=name,
        version=version,
        tarball=f"{name}-{version}.tar.gz",
        sha256=sha256,
        site=site or f"file:///home/ops/dl/{name}",
    )


def _remove_recipe(repo: Path, name: str) -> None:
    """Drop a recipe dir from the wired tree (the mutation fixture)."""
    shutil.rmtree(repo / "package" / name)


def _buildable_recipe_dir(name: str) -> Path:
    """A buildable recipe dir (``Config.in`` + ``<name>.mk`` + ``<name>.hash``).

    A buildable recipe carries a ``.mk`` (the hash check only applies to
    buildable recipes — data-only recipes have no build step and no hash); the
    ``.mk`` is minimal, the ``.hash`` is what each test overrides.
    """
    pkg_dir = Path(tempfile.mkdtemp()) / "package"
    recipe = pkg_dir / name
    recipe.mkdir(parents=True)
    (recipe / "Config.in").write_text(
        f"config BR2_PACKAGE_{name.upper()}\n\tbool {name}\n", encoding="utf-8"
    )
    (recipe / f"{name}.mk").write_text(
        f"{name.upper()}_SITE = file:///dl/{name}\n", encoding="utf-8"
    )
    return recipe


# --- a clean, wired BR2_EXTERNAL tree ---------------------------------------


def _clean_tree(tmp_path: Path) -> Path:
    """Emit + wire a full, buildable-shaped tree into a temp ``BR2_EXTERNAL``.

    The emitted cargo recipes (``cosmic-comp`` / ``cosmic-protocols``) plus the
    sibling native-dep recipes (``greetd`` / ``xdg-desktop-portal`` /
    ``cosmic-portal`` / ``fonts``) are all installed, the Desktop defconfig
    references only resolvable symbols, and the ``pengyos-desktop`` target is
    registered. :func:`lint_repo` over this must be clean.
    """
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = _manifest(tmp_path)
    vendor = tmp_path / "vendor"
    write_record(
        _record("cosmic-comp", site="file:///home/ops/dl/cosmic-comp"),
        vendor / "cosmic-comp.json",
    )
    write_record(
        _record("cosmic-protocols", site="file:///home/ops/dl/cosmic-protocols"),
        vendor / "cosmic-protocols.json",
    )
    sources = tmp_path / "sources"
    emit_group(load_manifest(manifest), "core", vendor, sources)

    # A Desktop defconfig that references only resolvable symbols: the two cargo
    # recipes (resolved by the emitted recipes) + two native deps (resolved by
    # the installed native-dep recipes) + a 2026.08 core package (MESA3D).
    (sources / "configs").mkdir(parents=True)
    (sources / "configs" / "pengyos_desktop_defconfig").write_text(
        "# resolvable: cargo recipes + native deps + a 2026.08 core package\n"
        "BR2_PACKAGE_COSMIC_COMP=y\n"
        "BR2_PACKAGE_COSMIC_PROTOCOLS=y\n"
        "BR2_PACKAGE_GREETD=y\n"
        "BR2_PACKAGE_XDG_DESKTOP_PORTAL=y\n"
        "BR2_PACKAGE_MESA3D=y\n"
        # A non-package assignment must be ignored by the reference scan.
        "BR2_x86_64=y\n",
        encoding="utf-8",
    )
    (sources / "board" / "desktop").mkdir(parents=True)

    repo = tmp_path / "repo"
    (repo / "package").mkdir(parents=True)
    (repo / "Config.in").write_text(
        'menu "PengyOS (external tree)"\n\nendmenu\n', encoding="utf-8"
    )
    # The target is registered in the tree's own targets.toml (that is where
    # lint looks for it).
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-desktop"\n', encoding="utf-8"
    )

    wire(repo, sources, targets=repo / "targets.toml")
    return repo


# --- the pure helpers --------------------------------------------------------


def test_defconfig_referenced_symbols_only_packages_and_dedup():
    text = (
        "BR2_PACKAGE_COSMIC_COMP=y\n"
        "BR2_PACKAGE_COSMIC_COMP=y\n"  # duplicate -> collapsed
        "BR2_PACKAGE_MESA3D=m\n"
        "BR2_x86_64=y\n"  # not a package symbol -> ignored
        'BR2_TARGET_GENERIC_HOSTNAME="x"\n'  # not a package symbol -> ignored
        "BR2_PACKAGE_GREETD=y # inline comment\n"
    )
    refs = _defconfig_referenced_symbols(text)
    assert refs == [
        "BR2_PACKAGE_COSMIC_COMP",
        "BR2_PACKAGE_MESA3D",
        "BR2_PACKAGE_GREETD",
    ]


def test_parse_config_symbols_reads_config_lines():
    config_in = (
        "config BR2_PACKAGE_COSMIC_COMP\n"
        "\tbool cosmic-comp\n"
        "config BR2_PACKAGE_HOST_RUSTC\n"
        "\tbool host rustc\n"
    )
    assert _parse_config_symbols(config_in) == {
        "BR2_PACKAGE_COSMIC_COMP",
        "BR2_PACKAGE_HOST_RUSTC",
    }


def test_recipe_symbols_scans_recipe_dirs():

    pkg_dir = Path(tempfile.mkdtemp()) / "package"
    (pkg_dir / "cosmic-comp").mkdir(parents=True)
    (pkg_dir / "cosmic-comp" / "Config.in").write_text(
        "config BR2_PACKAGE_COSMIC_COMP\n\tbool x\n", encoding="utf-8"
    )
    (pkg_dir / "greetd").mkdir(parents=True)
    (pkg_dir / "greetd" / "Config.in").write_text(
        "config BR2_PACKAGE_GREETD\n\tbool x\n", encoding="utf-8"
    )
    # A hidden dir and a dir without Config.in are skipped.
    (pkg_dir / ".hidden").mkdir()
    (pkg_dir / "no-config").mkdir()
    assert _recipe_symbols([pkg_dir / "cosmic-comp", pkg_dir / "greetd"]) == {
        "BR2_PACKAGE_COSMIC_COMP",
        "BR2_PACKAGE_GREETD",
    }


def test_placeholder_site_violations_detects_placeholder():

    pkg_dir = Path(tempfile.mkdtemp()) / "package"
    (pkg_dir / "cosmic-comp").mkdir(parents=True)
    (pkg_dir / "cosmic-comp" / "cosmic-comp.mk").write_text(
        f"COSMIC_COMP_SITE = {VENDORED_SITE_PLACEHOLDER}\n", encoding="utf-8"
    )
    issues = _placeholder_site_violations([pkg_dir / "cosmic-comp"])
    assert len(issues) == 1
    assert issues[0].category is LintCategory.PLACEHOLDER_SITE
    assert "placeholder" in issues[0].message


def test_placeholder_site_violations_clean_for_real_site():

    pkg_dir = Path(tempfile.mkdtemp()) / "package"
    (pkg_dir / "cosmic-comp").mkdir(parents=True)
    (pkg_dir / "cosmic-comp" / "cosmic-comp.mk").write_text(
        "COSMIC_COMP_SITE = file:///home/ops/dl/cosmic-comp\n", encoding="utf-8"
    )
    assert not _placeholder_site_violations([pkg_dir / "cosmic-comp"])


def test_placeholder_hash_violations_detects_short_digest():
    recipe = _buildable_recipe_dir("cosmic-comp")
    (recipe / "cosmic-comp.hash").write_text(
        "sha256  " + "a" * 40 + "  cosmic-comp.tar.gz\n", encoding="utf-8"
    )
    issues = _placeholder_hash_violations([recipe])
    assert len(issues) == 1
    assert issues[0].category is LintCategory.PLACEHOLDER_HASH


def test_placeholder_hash_violations_detects_missing_file():
    recipe = _buildable_recipe_dir("cosmic-comp")
    issues = _placeholder_hash_violations([recipe])
    assert len(issues) == 1
    assert issues[0].category is LintCategory.PLACEHOLDER_HASH
    assert "missing" in issues[0].message.lower()


def test_placeholder_hash_violations_clean_for_real_hash():
    recipe = _buildable_recipe_dir("cosmic-comp")
    (recipe / "cosmic-comp.hash").write_text(
        "sha256  " + "a" * 64 + "  cosmic-comp.tar.gz\n", encoding="utf-8"
    )
    assert not _placeholder_hash_violations([recipe])


def test_read_defconfig_first_non_hidden_file():

    configs = Path(tempfile.mkdtemp())
    (configs / "pengyos_desktop_defconfig").write_text("BR2_PACKAGE_X=y\n")
    assert _read_defconfig(configs) == "BR2_PACKAGE_X=y\n"
    # A missing configs dir yields None (the symbol checks are vacuous).
    assert _read_defconfig(Path("/nope/does/not/exist")) is None


def test_target_registered_matches_name_key():
    assert _target_registered('[[targets]]\nname = "pengyos-desktop"\n')
    assert not _target_registered('[[targets]]\nname = "pengyos-server"\n')
    assert not _target_registered("")


def test_native_dep_reference_symbols_union():
    symbols = _native_dep_reference_symbols(
        {"greetd": ("BR2_PACKAGE_GREETD",), "fonts": ("BR2_PACKAGE_FIRA_FONT",)}
    )
    assert symbols == {"BR2_PACKAGE_GREETD", "BR2_PACKAGE_FIRA_FONT"}


# --- the clean case ----------------------------------------------------------


def test_lint_repo_clean_for_a_fully_wired_tree():
    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    report = lint_repo(repo)
    assert report.ok, [i.message for i in report.issues]


# --- one mutation per rule (the heart of the lint suite) ---------------------


def test_mutation_placeholder_site_fires():

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    # The one mutation: a hand-edited recipe re-pins the placeholder SITE.
    (repo / "package" / "cosmic-comp" / "cosmic-comp.mk").write_text(
        f"COSMIC_COMP_SITE = {VENDORED_SITE_PLACEHOLDER}\n", encoding="utf-8"
    )
    report = lint_repo(repo)
    assert not report.ok
    assert report.categories() == (LintCategory.PLACEHOLDER_SITE,)


def test_mutation_placeholder_hash_fires():

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    # The one mutation: a short (placeholder-style) digest.
    (repo / "package" / "cosmic-comp" / "cosmic-comp.hash").write_text(
        "sha256  " + "a" * 40 + "  cosmic-comp-v0.6.0.tar.gz\n",
        encoding="utf-8",
    )
    report = lint_repo(repo)
    assert not report.ok
    assert report.categories() == (LintCategory.PLACEHOLDER_HASH,)


def test_mutation_unresolved_symbol_fires():
    """The canonical cosmic-portal mismatch fires unresolved_symbol.

    The Desktop defconfig references ``BR2_PACKAGE_COSMIC_PORTAL`` (as the real
    target does), but the ``cosmic-portal`` recipe enables
    ``BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC`` — so the referenced symbol
    resolves to no recipe and is not a 2026.08 core symbol.
    """

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    # The one mutation: reference the transitional symbol the defconfig uses.
    (repo / "configs" / "pengyos_desktop_defconfig").write_text(
        "BR2_PACKAGE_COSMIC_COMP=y\nBR2_PACKAGE_COSMIC_PORTAL=y\n",
        encoding="utf-8",
    )
    report = lint_repo(repo)
    assert not report.ok
    assert report.categories() == (LintCategory.UNRESOLVED_SYMBOL,)
    assert "BR2_PACKAGE_COSMIC_PORTAL" in report.issues[0].message


def test_mutation_missing_native_dep_fires():

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    # The one mutation: drop the greetd recipe after wiring.
    _remove_recipe(repo, "greetd")
    report = lint_repo(repo)
    assert not report.ok
    # greetd is referenced by the clean defconfig and is a native dep, so it is
    # reported as a missing native dep (and only that — the unresolved check
    # defers to this one for native-dep symbols).
    assert report.categories() == (LintCategory.MISSING_NATIVE_DEP,)
    assert "greetd" in report.issues[0].message


def test_mutation_unregistered_target_fires():

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    # The one mutation: targets.toml registers a different target.
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-server"\n', encoding="utf-8"
    )
    report = lint_repo(repo)
    assert not report.ok
    assert report.categories() == (LintCategory.UNREGISTERED_TARGET,)


def test_missing_targets_file_is_not_a_violation():
    """A tree wired without a target (bare ``wire --repo``) still lints clean."""

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    (repo / "targets.toml").unlink()
    report = lint_repo(repo)
    assert report.ok, [i.message for i in report.issues]


def test_categories_preserve_first_seen_order():

    tmp_path = Path(tempfile.mkdtemp())
    repo = _clean_tree(tmp_path)
    # Place a placeholder SITE + drop greetd + reference the transitional
    # symbol + unregister the target — every category at once.
    (repo / "package" / "cosmic-comp" / "cosmic-comp.mk").write_text(
        f"COSMIC_COMP_SITE = {VENDORED_SITE_PLACEHOLDER}\n", encoding="utf-8"
    )
    _remove_recipe(repo, "greetd")
    (repo / "configs" / "pengyos_desktop_defconfig").write_text(
        "BR2_PACKAGE_COSMIC_COMP=y\nBR2_PACKAGE_COSMIC_PORTAL=y\nBR2_PACKAGE_GREETD=y\n",
        encoding="utf-8",
    )
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-server"\n', encoding="utf-8"
    )
    report = lint_repo(repo)
    assert not report.ok
    # Issues are concatenated in check order: placeholder SITE, placeholder
    # hash, unresolved symbol, missing native dep, unregistered target.
    assert report.categories() == (
        LintCategory.PLACEHOLDER_SITE,
        LintCategory.UNRESOLVED_SYMBOL,
        LintCategory.MISSING_NATIVE_DEP,
        LintCategory.UNREGISTERED_TARGET,
    )


# --- the report API ----------------------------------------------------------


def test_lint_report_as_dict_shape():
    report = LintReport(
        repo=Path("/repo"),
        issues=(
            Issue(LintCategory.PLACEHOLDER_SITE, "bad site"),
            Issue(LintCategory.UNRESOLVED_SYMBOL, "bad symbol"),
        ),
    )
    data = report.as_dict()
    assert data["ok"] is False
    assert data["counts"]["violations"] == 2
    assert {i["category"] for i in data["issues"]} == {
        "placeholder_site",
        "unresolved_symbol",
    }


def test_lint_report_ok_when_clean():
    report = LintReport(repo=Path("/repo"), issues=())
    assert report.ok
    assert report.as_dict()["ok"] is True


# --- CLI ---------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m pengyos_cosmic_build`` as a subprocess (siblings on path)."""
    tools_dir = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    siblings = ("pengyos-cosmic-pkg", "pengyos-cosmic-recipes", "pengyos-cosmic-native-deps")
    parts = [str(tools_dir / name) for name in siblings]
    env["PYTHONPATH"] = os.pathsep.join(parts) + (
        os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", "pengyos_cosmic_build", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_lint_success(tmp_path: Path):
    repo = _clean_tree(tmp_path)
    proc = _run("lint", "--repo", str(repo))
    assert proc.returncode == 0, proc.stderr
    assert "buildable-shaped" in proc.stdout


def test_cli_lint_json_success(tmp_path: Path):
    repo = _clean_tree(tmp_path)
    proc = _run("lint", "--repo", str(repo), "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["counts"]["violations"] == 0


def test_cli_lint_violation_exits_nonzero(tmp_path: Path):
    repo = _clean_tree(tmp_path)
    _remove_recipe(repo, "greetd")
    proc = _run("lint", "--repo", str(repo))
    assert proc.returncode == 1
    assert "not buildable-shaped" in proc.stderr
    assert "greetd" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_lint_violation_json_reports_ok_false(tmp_path: Path):
    repo = _clean_tree(tmp_path)
    _remove_recipe(repo, "greetd")
    proc = _run("lint", "--repo", str(repo), "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert any(
        i["category"] == "missing_native_dep" for i in payload["issues"]
    )
    assert "Traceback" not in proc.stderr


# --- opt-in integration: emit + wire + lint against a real defconfig ---------


@pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="opt-in integration test (set PENGYOS_IT=1)",
)
def test_integration_emit_wire_lint_pipeline(tmp_path: Path):
    """The full emit -> wire -> lint pipeline lints clean end-to-end."""
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = _manifest(tmp_path)
    vendor = tmp_path / "vendor"
    write_record(
        _record("cosmic-comp", site="file:///home/ops/dl/cosmic-comp"),
        vendor / "cosmic-comp.json",
    )
    write_record(
        _record("cosmic-protocols", site="file:///home/ops/dl/cosmic-protocols"),
        vendor / "cosmic-protocols.json",
    )
    sources = tmp_path / "sources"
    emit_group(load_manifest(manifest), "core", vendor, sources)
    (sources / "configs").mkdir(parents=True)
    (sources / "configs" / "pengyos_desktop_defconfig").write_text(
        "BR2_PACKAGE_COSMIC_COMP=y\nBR2_PACKAGE_GREETD=y\nBR2_PACKAGE_MESA3D=y\n",
        encoding="utf-8",
    )
    (sources / "board" / "desktop").mkdir(parents=True)

    repo = tmp_path / "repo"
    (repo / "package").mkdir(parents=True)
    (repo / "Config.in").write_text(
        'menu "PengyOS (external tree)"\n\nendmenu\n', encoding="utf-8"
    )
    targets = tmp_path / "targets.toml"
    targets.write_text("[[targets]]\n", encoding="utf-8")

    wire(repo, sources, targets=targets)
    report = lint_repo(repo)
    assert report.ok, [i.message for i in report.issues]
