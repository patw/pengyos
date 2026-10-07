"""Tests for the recipes-lane apps group (``cosmic.toml`` + emit + closure).

Exercise the committed ``apps`` group: the six cargo applications (files, edit,
term, store, screenshot, player), their pinned references / licenses /
inter-component deps, recipe emission (three files per component, each
forwarding its single cargo binary), the app dep DAG (cosmic-edit and
cosmic-term build on cosmic-files; cosmic-store builds on the shell's
cosmic-panel), and the cross-group closure. All checks are stdlib and need no
network, root, display, or hardware.

``cosmic-workspaces`` is deliberately excluded: it is a JavaScript GNOME Shell
extension (no Rust, no Cargo.toml, no ``is_pinned``-acceptable tag), so it is
not a cargo recipe and has no place in this manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_cosmic_recipes import closure_lint, emit_group, load_manifest
from pengyos_cosmic_recipes.manifest import is_pinned
from pins import shipping_pins

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


# The six cargo apps, in file order.
APPS_NAMES = (
    "cosmic-files",
    "cosmic-edit",
    "cosmic-term",
    "cosmic-store",
    "cosmic-screenshot",
    "cosmic-player",
)

# name -> expected license (GPL-3.0-only for every app, from each Cargo.toml
# `license` field or, where absent, the repo's GPLv3 LICENSE file).
APPS_LICENSES = {
    "cosmic-files": "GPL-3.0-only",
    "cosmic-edit": "GPL-3.0-only",
    "cosmic-term": "GPL-3.0-only",
    "cosmic-store": "GPL-3.0-only",
    "cosmic-screenshot": "GPL-3.0-only",
    "cosmic-player": "GPL-3.0-only",
}

# name -> expected inter-component deps (declared by name in cosmic.toml,
# resolved from each Cargo.toml's `pop-os/*` git deps).
APPS_DEPS = {
    "cosmic-files": ("libcosmic", "cosmic-protocols"),
    "cosmic-edit": ("cosmic-files", "libcosmic"),
    "cosmic-term": ("cosmic-files", "libcosmic"),
    "cosmic-store": ("cosmic-panel", "libcosmic"),
    "cosmic-screenshot": (),
    "cosmic-player": ("libcosmic",),
}

# name -> expected cargo binaries (forwarded into CARGO_BINARIES). Every app
# ships exactly one binary target.
APPS_BINARIES = {
    "cosmic-files": ("cosmic-files",),
    "cosmic-edit": ("cosmic-edit",),
    "cosmic-term": ("cosmic-term",),
    "cosmic-store": ("cosmic-store",),
    "cosmic-screenshot": ("cosmic-screenshot",),
    "cosmic-player": ("cosmic-player",),
}


def _read_mk(pkg_dir: Path) -> dict[str, str]:
    """Parse the ``<pkg>.mk`` KEY = value lines into a dict."""
    mk = pkg_dir / f"{pkg_dir.name}.mk"
    values: dict[str, str] = {}
    for line in mk.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _symbol(pkg: str) -> str:
    return f"BR2_PACKAGE_{pkg.upper().replace('-', '_')}"


def test_apps_group_has_six_components():
    """The apps group holds exactly the six cargo apps, in order."""
    manifest = load_manifest()
    assert manifest.names("apps") == APPS_NAMES


def test_apps_components_are_pinned_and_licensed():
    """Every app carries a pinned tag and a declared license."""
    manifest = load_manifest()
    for entry in manifest.entries("apps"):
        assert is_pinned(entry.tag), f"{entry.name}: {entry.tag!r} is not pinned"
        assert entry.license.strip(), f"{entry.name}: license is empty"


def test_apps_licenses_declared(tmp_path):
    """Each app recipe's LICENSE line is the manifest's license."""
    manifest = load_manifest()
    emit_group(manifest, "apps", out_dir=tmp_path)
    for name, license_ in APPS_LICENSES.items():
        values = _read_mk(tmp_path / name)
        assert values[f"{_symbol(name)}_LICENSE"] == license_


def test_apps_pins_match_the_shipping_component_set(tmp_path):
    """Each app recipe pins what `scripts/cosmic-components.tsv` pins today.

    Same invariant as the shell group: two COSMIC pin sets (the shipping TSV and this
    manifest) must agree, or the recipes lane generates recipes for a COSMIC the
    image does not contain.
    """
    manifest = load_manifest()
    emit_group(manifest, "apps", out_dir=tmp_path)
    by = manifest.by_name()
    shipping = shipping_pins()
    for name in APPS_NAMES:
        values = _read_mk(tmp_path / name)
        assert values[f"{_symbol(name)}_VERSION"] == by[name].tag
        assert not by[name].tag.startswith("epoch-")
        if name in shipping:
            assert by[name].tag == shipping[name], (
                f"{name}: cosmic.toml pins {by[name].tag[:12]} but the shipping TSV "
                f"pins {shipping[name][:12]} - the two COSMIC pin sets disagree"
            )


def test_apps_forwards_single_binary(tmp_path):
    """Each app forwards exactly its own cargo binary target."""
    manifest = load_manifest()
    emit_group(manifest, "apps", out_dir=tmp_path)
    for name, binaries in APPS_BINARIES.items():
        values = _read_mk(tmp_path / name)
        assert values[f"{_symbol(name)}_CARGO_BINARIES"] == " ".join(binaries)


def test_apps_three_files_per_component(tmp_path):
    """Every app ships Config.in, <pkg>.mk and <pkg>.hash."""
    report = emit_group(load_manifest(), "apps", out_dir=tmp_path)
    assert report.emitted_names() == APPS_NAMES
    for name in APPS_NAMES:
        pkg_dir = tmp_path / name
        assert (pkg_dir / "Config.in").is_file()
        assert (pkg_dir / f"{name}.mk").is_file()
        assert (pkg_dir / f"{name}.hash").is_file()


def test_apps_dep_graph():
    """The app dependency graph is the COSMIC app crate chain."""
    manifest = load_manifest()
    by = manifest.by_name()
    for name, deps in APPS_DEPS.items():
        assert by[name].deps == deps, f"{name}: {by[name].deps} != {deps}"


def test_apps_deps_reference_real_components():
    """Every app dep references a real component in the manifest."""
    manifest = load_manifest()
    all_names = set(manifest.names())
    for name in APPS_NAMES:
        for dep in APPS_DEPS[name]:
            assert dep in all_names, f"{name}: dep '{dep}' is not a component"


def test_apps_alone_is_unclosed(tmp_path):
    """Emitting the apps group alone leaves its libcosmic / cosmic-panel deps unclosed."""
    root = tmp_path / "package"
    emit_group(load_manifest(), "apps", out_dir=root)
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    # Every app builds on libcosmic (core), and cosmic-store on cosmic-panel
    # (shell); neither has a recipe here.
    assert "unclosed-dep" in rules
    assert any("libcosmic" in str(v) for v in report.violations)
    assert any("cosmic-panel" in str(v) for v in report.violations)


def test_apps_closes_with_core_and_shell(tmp_path):
    """The whole manifest closes once core + login + shell + apps are emitted."""
    root = tmp_path / "package"
    emit_group(load_manifest(), "apps", out_dir=root)
    assert closure_lint(load_manifest(), root).is_closed is False  # apps alone
    emit_group(load_manifest(), "core", out_dir=root)
    emit_group(load_manifest(), "login", out_dir=root)
    emit_group(load_manifest(), "shell", out_dir=root)
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is True
    assert report.violations == ()


def test_apps_emit_is_deterministic(tmp_path):
    """Emitting the apps group twice yields byte-identical recipe files."""
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    emit_group(load_manifest(), "apps", out_dir=out1)
    emit_group(load_manifest(), "apps", out_dir=out2)
    for name in APPS_NAMES:
        for suffix in ("Config.in", f"{name}.mk", f"{name}.hash"):
            assert (out1 / name / suffix).read_bytes() == (
                out2 / name / suffix
            ).read_bytes()
