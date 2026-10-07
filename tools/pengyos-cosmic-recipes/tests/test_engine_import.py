"""Tests that the pengyos-cosmic-pkg engine is importable and usable.

These are the teeth of the "import the engine" contract: they don't merely
assert ``import`` succeeds, they exercise the engine's public surface so a
stale or missing engine is caught. All checks are pure/stdlib and need no
network, root, display, or hardware.
"""

from __future__ import annotations

import importlib

import pengyos_cosmic_pkg
from pengyos_cosmic_pkg import cli, lint, manifest, recipe


def test_engine_package_imports():
    """The engine package imports and carries its version."""
    assert pengyos_cosmic_pkg.__version__ == "0.1.0"


def test_engine_submodules_importable():
    """Every documented engine submodule imports."""
    for submodule in ("manifest", "recipe", "lint", "cli"):
        imported = importlib.import_module(f"pengyos_cosmic_pkg.{submodule}")
        assert imported is not None


def test_engine_public_symbols_present():
    """The documented public surface of each submodule is present."""
    for name in (
        "ComponentManifest",
        "Dependency",
        "ManifestError",
        "is_pinned",
        "parse_manifest",
        "load_manifest",
    ):
        assert hasattr(manifest, name), f"manifest.{name} missing"

    for name in (
        "build_recipe",
        "recipe_symbol",
        "license_files",
        "read_source_hash",
    ):
        assert hasattr(recipe, name), f"recipe.{name} missing"

    assert hasattr(lint, "lint_dir"), "lint.lint_dir missing"
    assert hasattr(cli, "main"), "cli.main missing"


def test_engine_is_pinned_behaviour():
    """The imported engine pins a real tag and rejects a floating ref.

    A behavioural check (not just an import) so a stub engine that defines
    ``is_pinned`` but returns the wrong answer is caught.
    """
    assert manifest.is_pinned("v0.6.0") is True
    assert manifest.is_pinned("1.2.3") is True
    assert manifest.is_pinned("abc1234") is True
    for floating in ("main", "master", "stable", "latest", ""):
        assert manifest.is_pinned(floating) is False


def test_engine_recipe_symbol():
    """The imported engine renders Buildroot Kconfig symbols the Buildroot way."""
    assert recipe.recipe_symbol("cosmic-comp") == "BR2_PACKAGE_COSMIC_COMP"
    # Dashes -> underscores, upper-cased.
    assert recipe.recipe_symbol("cosmic-settings-daemon") == (
        "BR2_PACKAGE_COSMIC_SETTINGS_DAEMON"
    )


def test_engine_license_files():
    """The imported engine maps an SPDX expression to license filenames."""
    assert recipe.license_files("MIT OR Apache-2.0") == [
        "LICENSE-MIT",
        "LICENSE-APACHE",
    ]
    assert recipe.license_files("MIT") == ["LICENSE-MIT"]


def test_engine_parse_manifest_valid():
    """The imported engine parses a valid component manifest."""
    data = manifest.component_table(
        name="cosmic-comp",
        tag="v0.6.0",
        binaries=["cosmic-comp"],
    )
    manifest_obj = manifest.parse_manifest(data)
    assert manifest_obj.name == "cosmic-comp"
    assert manifest_obj.version == "v0.6.0"
    assert manifest_obj.binaries == ("cosmic-comp",)
    assert manifest_obj.has_binaries() is True
    assert manifest_obj.has_deps() is False


def test_engine_parse_manifest_rejects_floating_tag():
    """The imported engine rejects a floating (unpinned) tag."""
    data = manifest.component_table(name="cosmic-comp", tag="main")
    try:
        manifest.parse_manifest(data)
    except manifest.ManifestError:
        pass
    else:  # pragma: no cover - failure must surface
        raise AssertionError("expected ManifestError for a floating tag")
