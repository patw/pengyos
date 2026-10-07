"""``non_cargo`` — the separate handling path for non-cargo components.

The recipes lane authors Buildroot *cargo* recipes for Rust components only.
Some components the task list names are **not** cargo components and have no
place in the cargo manifest. Historically the recipes lane handled this by
*absence*: ``cosmic-workspaces`` is simply not in ``cosmic.toml``, and tests
assert that absence. That works, but it is a silent, negative contract — a
component could be added to the manifest and the cargo engine would happily try
to build a recipe for a JS GNOME Shell extension.

This module establishes an **explicit, positive handling path** for non-cargo
components:

* the known non-cargo components are **registered** here, each with the kind of
  non-cargo recipe it needs and the lane that owns it;
* the recipes lane **recognises** a non-cargo component by name
  (:func:`recipe_kind` / :func:`is_non_cargo`);
* the manifest loader **rejects** a non-cargo component if it is added to the
  cargo manifest (:func:`assert_manifest_non_cargo_free`) — the exclusion is now
  structural, not an accident of absence;
* a non-cargo component is **routed** to :func:`handling_path` (a non-cargo
  Buildroot recipe in a *separate* lane) — never through the cargo engine's
  ``build_recipe``.

This is the "separate handling path" for non-cargo components. It is **not**
the non-cargo recipe itself (that is a follow-up: the recipes lane does not
author GNOME shell-extension recipes). It is the recognition + routing + guard
that make the exclusion explicit and tested.

All checks are stdlib and need no network, root, display, or hardware.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pengyos_cosmic_pkg.manifest import ManifestError

#: The kind of Buildroot recipe a non-cargo component needs. ``gnome_shell_extension``
#: is a shell-extension install path (``metadata.json`` + ``extension.js`` +
#: gschema, installed under ``GNOME_EXTENSION_UUID``); other kinds may be added
#: as non-cargo components are discovered.
GNOME_SHELL_EXTENSION = "gnome_shell_extension"

#: Every non-cargo recipe kind the recipes lane knows about.
NON_CARGO_KINDS: tuple[str, ...] = (GNOME_SHELL_EXTENSION,)


@dataclass(frozen=True)
class NonCargoComponent:
    """A component that is NOT a Rust cargo component and needs a *non-cargo* recipe.

    ``recipe_kind`` is a stable identifier for the non-cargo Buildroot recipe it
    needs (see :data:`NON_CARGO_KINDS`). ``needs_recipe`` records that it still
    needs a Buildroot recipe — just a *non-cargo* one. ``handled_by`` names the
    lane that owns that recipe; ``lane_out_of_scope`` records that the cargo
    recipes lane does **not** author it. ``note`` is a human one-liner.
    """

    name: str
    git_url: str
    recipe_kind: str
    needs_recipe: bool
    handled_by: str
    lane_out_of_scope: bool
    note: str


#: The known non-cargo components, in the order they were established. This is
#: the separate handling path's registry: every non-cargo component the recipes
#: lane is aware of lives here, with the recipe kind it needs and the lane that
#: owns it. Add a component here (do not silently exclude it) when a non-cargo
#: component is discovered.
NON_CARGO_COMPONENTS: tuple[NonCargoComponent, ...] = (
    NonCargoComponent(
        name="cosmic-workspaces",
        git_url="https://github.com/pop-os/cosmic-workspaces",
        recipe_kind=GNOME_SHELL_EXTENSION,
        needs_recipe=True,
        handled_by=(
            "a separate non-cargo Buildroot recipe lane "
            "(GNOME shell-extension install path)"
        ),
        lane_out_of_scope=True,
        note=(
            "A JavaScript GNOME Shell extension (extension.js / prefs.js / "
            "metadata.json with a shell-version gate, GPLv3, gschema) — no "
            "Cargo.toml, no binary, no is_pinned-acceptable tag (default branch "
            "master_jammy). It is named among the apps but is not a cargo "
            "component, so it has no place in the cargo recipes manifest."
        ),
    ),
)

#: A name -> :class:`NonCargoComponent` map over :data:`NON_CARGO_COMPONENTS`.
_REGISTRY: dict[str, NonCargoComponent] = {c.name: c for c in NON_CARGO_COMPONENTS}


def lookup(name: str) -> NonCargoComponent | None:
    """The registered non-cargo component ``name``, or ``None`` if unknown/cargo."""
    return _REGISTRY.get(name)


def is_non_cargo(name: str) -> bool:
    """``True`` when ``name`` is a recognised non-cargo component."""
    return name in _REGISTRY


def recipe_kind(name: str) -> str | None:
    """The non-cargo recipe kind for ``name`` (``None`` when it is cargo/unknown).

    A cargo component returns ``None`` — :func:`recipe_kind` only classifies the
    non-cargo components the recipes lane explicitly recognises.
    """
    component = _REGISTRY.get(name)
    return component.recipe_kind if component is not None else None


def handling_path(name: str) -> str:
    """Where ``name`` (a non-cargo component) is handled, as a human string.

    Raises :class:`KeyError` when ``name`` is not a recognised non-cargo
    component — a cargo component has no "handling path" here (it is built by
    the cargo engine, not routed).
    """
    component = _REGISTRY.get(name)
    if component is None:
        raise KeyError(
            f"{name} is not a recognised non-cargo component "
            "(no non-cargo handling path)"
        )
    return component.handled_by


def assert_manifest_non_cargo_free(entries: Iterable[object]) -> None:
    """Raise :class:`ManifestError` if any entry is a recognised non-cargo component.

    ``entries`` is an iterable of objects carrying a ``name`` attribute (the
    manifest's :class:`~pengyos_cosmic_recipes.manifest.ComponentEntry`
    instances). The cargo recipes lane rejects a non-cargo component from its
    manifest: it must be handled by the non-cargo lane instead. This is what
    makes the exclusion structural rather than an accident of absence — adding
    ``cosmic-workspaces`` to ``cosmic.toml`` now fails loudly at load time.
    """
    for entry in entries:
        name = getattr(entry, "name", None)
        if name is not None and is_non_cargo(name):
            component = _REGISTRY[name]
            raise ManifestError(
                f"{name}: a non-cargo component ({component.recipe_kind}) must "
                f"not be in the cargo recipes manifest; it is handled by "
                f"{component.handled_by} (out of scope for the cargo recipes "
                f"lane). See pengyos_cosmic_recipes.non_cargo."
            )
