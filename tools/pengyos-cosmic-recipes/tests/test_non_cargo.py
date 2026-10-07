"""Tests for the recipes-lane's non-cargo handling path
(``pengyos_cosmic_recipes.non_cargo``).

``cosmic-workspaces`` is named among the apps in the task list, but it is a
JavaScript GNOME Shell extension — not a Rust cargo component. This suite pins
the **separate handling path** the recipes lane establishes for it (and any
future non-cargo component): it is *recognised* by name, *routed* to a
non-cargo Buildroot recipe in a separate lane, and *rejected* from the cargo
manifest by a structural loader guard (not merely absent).

All checks are pure/stdlib and need no network, root, display, or hardware.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from pengyos_cosmic_recipes import (
    NON_CARGO_COMPONENTS,
    NON_CARGO_KINDS,
    GNOME_SHELL_EXTENSION,
    is_non_cargo,
    lookup,
    recipe_kind,
)
from pengyos_cosmic_recipes.cli import main
from pengyos_cosmic_recipes.manifest import parse_manifest
from pengyos_cosmic_recipes.non_cargo import handling_path
from pengyos_cosmic_pkg.manifest import ManifestError


def test_registry_has_cosmic_workspaces():
    """cosmic-workspaces is the one registered non-cargo component."""
    assert NON_CARGO_COMPONENTS, "the registry must not be empty"
    names = {c.name for c in NON_CARGO_COMPONENTS}
    assert "cosmic-workspaces" in names


def test_registry_fields_valid():
    """Every registered non-cargo component has well-formed fields."""
    for component in NON_CARGO_COMPONENTS:
        assert component.name  # a name
        assert component.git_url.startswith(("https://", "http://", "git://"))
        # The recipe kind is one the recipes lane actually knows about.
        assert component.recipe_kind in NON_CARGO_KINDS
        # cosmic-workspaces needs a (non-cargo) recipe, and it is out of scope.
        assert component.needs_recipe is True
        assert component.lane_out_of_scope is True
        assert component.handled_by  # a handling path is recorded
        assert component.note  # a reason is recorded


def test_recipe_kind_is_gnome_shell_extension():
    """cosmic-workspaces is classified as a GNOME shell extension."""
    assert recipe_kind("cosmic-workspaces") == GNOME_SHELL_EXTENSION
    assert recipe_kind("cosmic-workspaces") == "gnome_shell_extension"


def test_is_non_cargo_true_and_false():
    """is_non_cargo distinguishes the registered component from cargo/unknown."""
    assert is_non_cargo("cosmic-workspaces") is True
    # A cargo component is not non-cargo.
    assert is_non_cargo("cosmic-comp") is False
    # An unknown name is not non-cargo.
    assert is_non_cargo("does-not-exist") is False


def test_recipe_kind_none_for_cargo():
    """recipe_kind returns None for a cargo component (not a non-cargo kind)."""
    assert recipe_kind("cosmic-comp") is None
    assert recipe_kind("cosmic-protocols") is None


def test_lookup_returns_component():
    """lookup returns the registered component, or None for cargo/unknown."""
    component = lookup("cosmic-workspaces")
    assert component is not None
    assert component.recipe_kind == GNOME_SHELL_EXTENSION
    assert lookup("cosmic-comp") is None
    assert lookup("nope") is None


def test_handling_path_returns_lane():
    """handling_path names the out-of-lane lane that owns the recipe."""
    text = handling_path("cosmic-workspaces")
    assert text  # non-empty
    assert "lane" in text.lower()


def test_handling_path_rejects_cargo():
    """handling_path raises KeyError for a cargo component (it has no path here)."""
    with pytest.raises(KeyError):
        handling_path("cosmic-comp")


def test_guard_rejects_non_cargo_in_manifest():
    """A non-cargo component in the manifest fails load with ManifestError."""
    # A valid *pinned* tag so the tag check passes and the non-cargo guard fires.
    data = {
        "component": [
            {
                "name": "cosmic-workspaces",
                "group": "apps",
                "git_url": "https://github.com/pop-os/cosmic-workspaces",
                "tag": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
                "license": "GPL-3.0-only",
            }
        ]
    }
    with pytest.raises(ManifestError) as exc:
        parse_manifest(data)
    message = str(exc.value).lower()
    assert "non-cargo" in message
    assert "cosmic-workspaces" in message
    assert "gnome_shell_extension" in message


def test_guard_allows_cargo_manifest():
    """A manifest of only cargo components still loads (guard is silent)."""
    data = {
        "component": [
            {
                "name": "cosmic-comp",
                "group": "core",
                "git_url": "https://github.com/pop-os/cosmic-comp",
                "tag": "a55785993e8ef6aad38862cb1a9e1ccaad3c340d",
                "license": "GPL-3.0-only",
            }
        ]
    }
    manifest = parse_manifest(data)
    assert manifest.components[0].name == "cosmic-comp"


def test_committed_manifest_is_non_cargo_free():
    """The committed manifest excludes cosmic-workspaces (guard passes)."""
    from pengyos_cosmic_recipes import load_manifest

    manifest = load_manifest()
    assert "cosmic-workspaces" not in manifest.names()
    for group in ("core", "login", "shell", "apps"):
        assert "cosmic-workspaces" not in manifest.names(group)


def _run(*args: str) -> int:
    """Invoke the CLI in-process and return its exit code."""
    return main(list(args))


def _capture_stdout(*args: str) -> str:
    """Run a CLI subcommand and return its stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        _run(*args)
    return buf.getvalue()


def test_non_cargo_cli_human(capsys):
    """``non_cargo`` lists cosmic-workspaces and its handling path."""
    assert _run("non_cargo") == 0
    out = capsys.readouterr().out
    assert "cosmic-workspaces" in out
    assert "gnome_shell_extension" in out
    assert "handled by" in out


def test_non_cargo_cli_json():
    """``non_cargo --json`` emits the registry as a machine-readable summary."""
    import json

    payload = json.loads(_capture_stdout("non_cargo", "--json"))
    assert payload["command"] == "non_cargo"
    assert payload["ok"] is True
    names = [c["name"] for c in payload["components"]]
    assert "cosmic-workspaces" in names
    by = {c["name"]: c for c in payload["components"]}
    assert by["cosmic-workspaces"]["recipe_kind"] == "gnome_shell_extension"
    assert by["cosmic-workspaces"]["lane_out_of_scope"] is True
    assert by["cosmic-workspaces"]["needs_recipe"] is True
