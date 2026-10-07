"""Tests for the recipes lane's manifest loader (``pengyos_cosmic_recipes.manifest``).

These exercise the committed ``cosmic.toml`` (the core group) and the loader's
validation rules. All checks are pure/stdlib and need no network, root, display,
or hardware.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pengyos_cosmic_pkg.manifest import parse_manifest as engine_parse

from pins import shipping_pins
from pengyos_cosmic_recipes import manifest as M
from pengyos_cosmic_recipes.manifest import (
    ComponentEntry,
    CosmicManifest,
    ManifestError,
    component_manifest,
    load_manifest,
    parse_manifest,
)

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


def test_committed_manifest_loads():
    """The committed cosmic.toml loads the core, login and shell groups."""
    manifest = load_manifest()
    assert isinstance(manifest, CosmicManifest)
    assert manifest.path == (ROOT / M.MANIFEST_NAME).resolve()
    assert manifest.names() == (
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
        "cosmic-greeter",
        "cosmic-panel",
        "cosmic-launcher",
        "cosmic-applets",
        "cosmic-bg",
        "cosmic-randr",
        "cosmic-settings",
        "cosmic-settings-daemon",
        "cosmic-osd",
        "cosmic-notifications",
        "cosmic-idle",
        "cosmic-icons",
        "cosmic-files",
        "cosmic-edit",
        "cosmic-term",
        "cosmic-store",
        "cosmic-screenshot",
        "cosmic-player",
    )


def test_committed_manifest_core_group():
    """The core group holds the four bootable-session components."""
    manifest = load_manifest()
    assert manifest.names("core") == (
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
    )
    # The login group holds the greeter.
    assert manifest.names("login") == ("cosmic-greeter",)
    # The apps group holds the six cargo applications.
    assert manifest.names("apps") == (
        "cosmic-files",
        "cosmic-edit",
        "cosmic-term",
        "cosmic-store",
        "cosmic-screenshot",
        "cosmic-player",
    )


def test_committed_manifest_pins_and_licenses():
    """Every core component carries a pinned tag and a declared license."""
    manifest = load_manifest()
    for entry in manifest.components:
        assert M.is_pinned(entry.tag), f"{entry.name}: {entry.tag!r} is not pinned"
        assert entry.license.strip(), f"{entry.name}: license is empty"


def test_every_manifest_pin_matches_the_shipping_component_set():
    """Every component in this manifest pins the commit the shipping path pins.

    COSMIC has two pin sets: `scripts/cosmic-components.tsv`, which
    `scripts/build-cosmic.sh` clones and builds today, and this manifest, which the
    recipes lane emits from. If they drift, the tree ships one COSMIC and generates
    recipes for another - and nothing in either lane would notice. This covers every
    group (core/login/shell/apps), which the per-group emit tests do not.
    """
    manifest = load_manifest()
    shipping = shipping_pins()
    missing = [c.name for c in manifest.components if c.name not in shipping]
    assert missing == [], f"manifest components absent from the shipping set: {missing}"
    for entry in manifest.components:
        assert not entry.tag.startswith("epoch-"), (
            f"{entry.name}: an epoch-* tag is a release snapshot, not a pin"
        )
        assert entry.tag == shipping[entry.name], (
            f"{entry.name}: cosmic.toml pins {entry.tag[:12]} but the shipping TSV "
            f"pins {shipping[entry.name][:12]}"
        )


def test_committed_manifest_dep_graph():
    """The core dependency graph is the COSMIC compositor/session chain."""
    manifest = load_manifest()
    by = manifest.by_name()
    assert by["cosmic-protocols"].deps == ()
    assert by["libcosmic"].deps == ("cosmic-protocols",)
    assert set(by["cosmic-comp"].deps) == {"libcosmic", "cosmic-protocols"}
    assert by["cosmic-session"].deps == ()


def test_committed_manifest_login_greeter():
    """The login group holds cosmic-greeter: pinned, GPL-3.0-only, on libcosmic."""
    from pengyos_cosmic_recipes.manifest import is_pinned

    manifest = load_manifest()
    by = manifest.by_name()
    assert manifest.group_of("cosmic-greeter") == "login"
    greeter = by["cosmic-greeter"]
    # Pinned to a commit SHA, never the `epoch-*` tag: an epoch tag is a release
    # snapshot (several of them peel to the same commit) and is_pinned rejects it.
    assert is_pinned(greeter.tag)
    assert not greeter.tag.startswith("epoch-")
    assert greeter.license == "GPL-3.0-only"
    # It ships a `cosmic-greeter` binary.
    assert greeter.binaries == ("cosmic-greeter",)
    # It is built on the libcosmic crate; greetd is a Buildroot system dep, not
    # tracked here as an inter-component dep.
    assert greeter.deps == ("libcosmic",)


def test_component_manifest_expands_deps_for_engine():
    """Name-only deps expand to the target's git_url/tag for the engine."""
    manifest = load_manifest()
    by = manifest.by_name()
    table = M.component_manifest(by["cosmic-comp"], by)
    # The recipes-lane group field is dropped; the engine's identity fields remain.
    assert "group" not in table
    assert table["name"] == "cosmic-comp"
    assert table["tag"] == by["cosmic-comp"].tag
    # Deps are expanded to full Dependency tables.
    dep_names = {d["name"] for d in table["deps"]}
    assert dep_names == {"libcosmic", "cosmic-protocols"}
    for dep in table["deps"]:
        target = by[dep["name"]]
        assert dep["git_url"] == target.git_url
        assert dep["tag"] == target.tag


def test_component_manifest_accepted_by_engine():
    """The expanded table parses cleanly through the engine's validator."""
    manifest = load_manifest()
    by = manifest.by_name()
    table = M.component_manifest(by["cosmic-comp"], by)
    parsed = engine_parse(table)
    assert parsed.name == "cosmic-comp"
    assert parsed.version == by["cosmic-comp"].tag
    assert {d.name for d in parsed.deps} == {"libcosmic", "cosmic-protocols"}
    # cosmic-comp ships a binary target.
    assert "cosmic-comp" in parsed.binaries


def test_group_helpers():
    """The grouping helpers (names/entries/group_of/by_name) behave."""
    manifest = load_manifest()
    assert manifest.group_of("cosmic-comp") == "core"
    assert manifest.entries("core") == tuple(
        c for c in manifest.components if c.group == "core"
    )
    # The login group holds just the greeter; the apps group holds the six
    # cargo applications (files, edit, term, store, screenshot, player).
    assert manifest.entries("login") == (manifest.by_name()["cosmic-greeter"],)
    assert [c.name for c in manifest.entries("apps")] == [
        "cosmic-files",
        "cosmic-edit",
        "cosmic-term",
        "cosmic-store",
        "cosmic-screenshot",
        "cosmic-player",
    ]
    assert manifest.by_name()["libcosmic"].license == "MPL-2.0"


def test_parse_manifest_rejects_missing_component_key():
    """A component without a required field is rejected."""
    with pytest.raises(ManifestError, match="missing required field 'git_url'"):
        parse_manifest(
            {"component": [{"name": "cosmic-comp", "group": "core"}]},
            source="test.toml",
        )


def test_parse_manifest_rejects_floating_tag():
    """A floating (unpinned) tag is rejected by the shared is_pinned predicate."""
    data = tomllib.loads(
        "[[component]]\n"
        'name = "cosmic-comp"\n'
        'group = "core"\n'
        'git_url = "https://github.com/pop-os/cosmic-comp"\n'
        'tag = "main"\n'
        'license = "GPL-3.0-only"\n'
    )
    with pytest.raises(ManifestError, match="not a pinned reference"):
        parse_manifest(data, source="test.toml")


def test_parse_manifest_rejects_undeclared_dep():
    """A dep that references no component in the manifest is rejected."""
    data = tomllib.loads(
        "[[component]]\n"
        'name = "cosmic-comp"\n'
        'group = "core"\n'
        'git_url = "https://github.com/pop-os/cosmic-comp"\n'
        'tag = "v0.6.0"\n'
        'license = "GPL-3.0-only"\n'
        'deps = ["ghost"]\n'
    )
    with pytest.raises(ManifestError, match="undeclared dependency 'ghost'"):
        parse_manifest(data, source="test.toml")


def test_parse_manifest_rejects_unknown_field():
    """An unknown field on a component is rejected (strict schema)."""
    data = tomllib.loads(
        "[[component]]\n"
        'name = "cosmic-comp"\n'
        'group = "core"\n'
        'git_url = "https://github.com/pop-os/cosmic-comp"\n'
        'tag = "v0.6.0"\n'
        'license = "GPL-3.0-only"\n'
        'bogus = "x"\n'
    )
    with pytest.raises(ManifestError, match="unknown field"):
        parse_manifest(data, source="test.toml")


def test_parse_manifest_rejects_bad_group():
    """A component in an unknown group is rejected."""
    data = tomllib.loads(
        "[[component]]\n"
        'name = "cosmic-comp"\n'
        'group = "corw"\n'
        'git_url = "https://github.com/pop-os/cosmic-comp"\n'
        'tag = "v0.6.0"\n'
        'license = "GPL-3.0-only"\n'
    )
    with pytest.raises(ManifestError, match="not a known group"):
        parse_manifest(data, source="test.toml")


def test_parse_manifest_rejects_duplicate_name():
    """Duplicate component names are rejected."""
    data = tomllib.loads(
        "[[component]]\n"
        'name = "cosmic-comp"\n'
        'group = "core"\n'
        'git_url = "https://github.com/pop-os/cosmic-comp"\n'
        'tag = "v0.6.0"\n'
        'license = "GPL-3.0-only"\n'
        "\n"
        "[[component]]\n"
        'name = "cosmic-comp"\n'
        'group = "core"\n'
        'git_url = "https://github.com/pop-os/cosmic-comp"\n'
        'tag = "v0.6.0"\n'
        'license = "GPL-3.0-only"\n'
    )
    with pytest.raises(ManifestError, match="duplicate component name"):
        parse_manifest(data, source="test.toml")


def test_parse_manifest_rejects_empty_component_array():
    """A manifest with an empty component array is rejected."""
    with pytest.raises(ManifestError, match="no 'component' entries"):
        parse_manifest({"component": []}, source="test.toml")


def test_load_manifest_missing_file(tmp_path):
    """A missing manifest file raises ManifestError with its path."""
    with pytest.raises(ManifestError, match="manifest file not found"):
        load_manifest(tmp_path / "nope.toml")


def test_component_entry_is_immutable():
    """ComponentEntry instances are frozen dataclasses."""
    import dataclasses

    entry = ComponentEntry(
        group="core",
        name="cosmic-comp",
        git_url="https://github.com/pop-os/cosmic-comp",
        tag="v0.6.0",
        license="GPL-3.0-only",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.name = "x"  # type: ignore[misc]
