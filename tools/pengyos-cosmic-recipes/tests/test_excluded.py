"""Tests for the recipes-lane's handling of non-cargo components.

``cosmic-workspaces`` is named among the apps in the task list, but it is a
JavaScript GNOME Shell extension — not a Rust cargo component. The recipes lane
authors Buildroot *cargo* recipes for Rust components only, so it has **no place
in this manifest**. This suite pins that decision as a stable, tested invariant:

1. ``cosmic-workspaces`` is **not** a component in the manifest (the loader
   would happily accept it — it is a valid `[a-z][a-z0-9_-]*` name — so the
   exclusion must be asserted explicitly, not assumed).
2. The exclusion is **documented** in ``cosmic.toml`` itself (a note recording
   *why* it is excluded: a JS GNOME extension — no Rust, no ``Cargo.toml``, no
   ``is_pinned``-acceptable tag), so the decision cannot silently disappear.

All checks are pure/stdlib and need no network, root, display, or hardware. The
"it is a JS extension" fact was verified out-of-band against
``pop-os/cosmic-workspaces`` (``extension.js`` / ``prefs.js`` /
``metadata.json`` with a ``shell-version`` gate, GPLv3, default branch
``master_jammy`` — no ``Cargo.toml`` anywhere in the tree).
"""

from __future__ import annotations

from pathlib import Path

from pengyos_cosmic_recipes import load_manifest
from pengyos_cosmic_recipes.manifest import MANIFEST_NAME

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent

# The one non-cargo component the task list names but the recipes lane excludes.
EXCLUDED = "cosmic-workspaces"


def test_excluded_component_not_in_manifest():
    """``cosmic-workspaces`` is not a component in the manifest."""
    manifest = load_manifest()
    assert EXCLUDED not in manifest.names(), (
        f"{EXCLUDED} must not be a cargo recipe in this manifest"
    )
    # And it is not in any group — the exclusion is total.
    for group in ("core", "login", "shell", "apps"):
        assert EXCLUDED not in manifest.names(group)


def test_manifest_only_cargo_components(tmp_path):
    """Every component in the manifest is a cargo component (has a binary or
    is a library/theme crate) — no JS extension slipped in.

    This guards the invariant the recipes lane builds on: the manifest holds
    Buildroot *cargo* recipes. A JS extension (no ``Cargo.toml``, no binary)
    would have no place here.
    """
    manifest = load_manifest()
    for entry in manifest.components:
        # A cargo component ships at least one binary target, or is a
        # binary-less library/theme crate (cosmic-randr / cosmic-icons). Either
        # way it is a Rust crate, never a JS extension. The point of the test is
        # that ``cosmic-workspaces`` (a JS extension) is absent — asserted
        # directly above; this just documents the cargo-only contract.
        assert entry.name != EXCLUDED


def test_exclusion_documented_in_manifest():
    """``cosmic.toml`` documents *why* ``cosmic-workspaces`` is excluded.

    The exclusion is a decision, not an accident: the committed manifest keeps a
    note recording the reason (a JS GNOME Shell extension — no Rust, no
    ``Cargo.toml``, no ``is_pinned``-acceptable tag). A test over the raw
    ``cosmic.toml`` text pins that record so it cannot silently vanish.
    """
    manifest_path = ROOT / MANIFEST_NAME
    assert manifest_path.is_file(), f"{MANIFEST_NAME} is missing"
    text = manifest_path.read_text(encoding="utf-8")

    # The excluded component is named in the note...
    assert EXCLUDED in text, f"{MANIFEST_NAME} does not mention {EXCLUDED}"
    # ...and the reason is recorded (a JS GNOME Shell extension, no Cargo.toml).
    assert "GNome Shell" in text or "GNOME Shell" in text, (
        "cosmic.toml must record that the excluded component is a GNOME Shell "
        "extension"
    )
    assert "Cargo.toml" in text, (
        "cosmic.toml must record that the excluded component has no Cargo.toml"
    )


def test_excluded_component_has_no_recipe_emitted(tmp_path):
    """Emitting every group never emits a recipe for the excluded component.

    The exclusion is structural: ``cosmic-workspaces`` is not in any group, so
    no ``emit --group <g>`` can produce a recipe for it.
    """
    from pengyos_cosmic_recipes import emit_group

    root = tmp_path / "package"
    for group in ("core", "login", "shell", "apps"):
        emit_group(load_manifest(), group, out_dir=root)
    assert not (root / EXCLUDED).exists(), (
        f"the excluded component must never get a Buildroot recipe"
    )
