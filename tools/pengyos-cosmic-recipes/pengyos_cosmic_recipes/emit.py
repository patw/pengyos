"""``emit`` — emit the Buildroot cargo recipes for a group of COSMIC components.

The recipes lane owns the manifest data and drives the engine's ``recipe``
builder (:func:`pengyos_cosmic_pkg.recipe.build_recipe`) once per component.
This module expands each component's name-only ``deps`` to the target's
``git_url`` / ``tag`` (via :func:`pengyos_cosmic_recipes.manifest.component_manifest`)
and hands the resulting single-component manifest to the engine, so the emitted
recipe pins exactly the reference the manifest declares.

### The hash seam

The engine's ``build_recipe`` pins the sha256 of the *vendored* source tarball
(the digest the ``vendor`` engine computes). Vendoring is out of scope for the
recipes lane (it needs the git sources over the network), so a real digest is
not available here. Two cases are distinguished:

* A digest **is** supplied (``source_hash``) — the operator has vendored and is
  feeding the digest back in. It is pinned verbatim.
* No digest is supplied — a deterministic placeholder (64 zero hex digits) is
  written so the recipe is structurally complete and lint-clean (the ``lint``
  rule only requires the ``.hash`` file to *be present*, not that its digest
  match a real tarball — that match is the operator's build-time check). The
  report lists every component left on the placeholder, so a later ``emit`` with
  the real digest is unambiguous.

The placeholder is a deliberate, documented stand-in; it is never mistaken for a
real digest because :data:`PLACEHOLDER_HASH` is a named constant and the report
flags it.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from pengyos_cosmic_pkg.recipe import (
    RecipeResult,
    build_recipe,
    VENDORED_SITE_PLACEHOLDER,
)

from .manifest import ComponentEntry, CosmicManifest, component_manifest

#: The deterministic placeholder digest written when no vendored digest is
#: supplied: 64 hex zeros. It is a well-formed sha256 so the emitted ``.hash``
#: line is valid, and it is lint-clean (the lint rule only requires the hash
#: file to be present). See :func:`emit_group`.
PLACEHOLDER_HASH = "0" * 64


class EmitError(RuntimeError):
    """A group has no components to emit, or the engine rejected a component."""


@dataclass(frozen=True)
class EmitResult:
    """The outcome of emitting one component: its identity and emitted files.

    ``name`` / ``symbol`` / ``version`` are the component's identity; ``files``
    are the three emitted recipe files in ``Config.in`` / ``.mk`` / ``.hash``
    order; ``placeholder_hash`` is ``True`` when the written ``.hash`` is the
    placeholder (i.e. no vendored digest was supplied for this component).
    """

    name: str
    symbol: str
    version: str
    files: tuple[Path, ...]
    placeholder_hash: bool

    @property
    def hash_path(self) -> Path:
        """The emitted ``<pkg>.hash`` file."""
        return self.files[2]


@dataclass(frozen=True)
class EmitReport:
    """The outcome of :func:`emit_group`: the recipes emitted for a group.

    ``results`` is one :class:`EmitResult` per component in the group (file
    order). ``missing_digest`` lists the components left on the placeholder
    digest (no vendored digest was supplied); an empty tuple means every recipe
    pins a real digest.
    """

    group: str
    out_dir: Path
    results: tuple[EmitResult, ...]
    missing_digest: tuple[str, ...] = field(default_factory=tuple)

    def emitted_names(self) -> tuple[str, ...]:
        """The emitted component names, in file order."""
        return tuple(r.name for r in self.results)

    @property
    def is_complete(self) -> bool:
        """``True`` when every recipe pins a real (non-placeholder) digest."""
        return not self.missing_digest


def _resolve_hash(
    component: str,
    *,
    source_hash: str | None,
) -> tuple[str, bool]:
    """Return ``(digest, used_placeholder)`` for ``component``.

    A supplied ``source_hash`` wins (used verbatim). Otherwise the placeholder
    digest is used and flagged.
    """
    if source_hash:
        return source_hash, False
    return PLACEHOLDER_HASH, True


def _engine_table(entry: ComponentEntry, by_name: dict[str, ComponentEntry]) -> dict:
    """Build the engine-compatible single-component table for ``entry``."""
    return component_manifest(entry, by_name)


def emit_group(
    manifest: CosmicManifest,
    group: str,
    *,
    out_dir: Path | str,
    source_hash: str | None = None,
    site: str | None = None,
) -> EmitReport:
    """Emit the Buildroot recipes for every component in ``group``.

    * ``manifest`` — the loaded recipes-lane manifest (see
      :mod:`pengyos_cosmic_recipes.manifest`).
    * ``group`` — the group to emit (one of ``core`` / ``login`` / ``shell`` /
      ``apps``); components are emitted in file order.
    * ``out_dir`` — the Buildroot ``package/`` directory; each component's
      ``Config.in`` / ``<pkg>.mk`` / ``<pkg>.hash`` lands in
      ``out_dir/<pkg>/``.
    * ``source_hash`` — an optional sha256 digest pinned into every recipe; when
      omitted each recipe gets the placeholder digest (see :data:`PLACEHOLDER_HASH`)
      and is flagged in the report.
    * ``site`` — an optional ``*_SITE`` override (defaults to the engine's
      documented vendored-site placeholder).

    Raises :class:`EmitError` when the group has no components.

    Every component is expanded to the engine's single-component form (deps
    expanded to the target's ``git_url`` / ``tag``, the recipes-lane ``group``
    field dropped) and handed to :func:`pengyos_cosmic_pkg.recipe.build_recipe`,
    so the emitted recipe pins the same reference the manifest declares.
    """
    from pengyos_cosmic_pkg.manifest import parse_manifest as engine_parse

    entries = manifest.entries(group)
    if not entries:
        raise EmitError(
            f"group '{group}' has no components to emit "
            f"(known groups: {', '.join(manifest.groups)})"
        )

    out_dir = Path(out_dir)
    by_name = manifest.by_name()
    resolved_site = site if site is not None else VENDORED_SITE_PLACEHOLDER

    results: list[EmitResult] = []
    missing_digest: list[str] = []

    for entry in entries:
        table = _engine_table(entry, by_name)
        component_manifest_obj = engine_parse(table)

        digest, used_placeholder = _resolve_hash(entry.name, source_hash=source_hash)

        result: RecipeResult = build_recipe(
            component_manifest_obj,
            source_hash=digest,
            out_dir=out_dir,
            site=resolved_site,
        )

        emit_result = EmitResult(
            name=entry.name,
            symbol=result.symbol,
            version=result.version,
            files=tuple(result.files()),
            placeholder_hash=used_placeholder,
        )
        results.append(emit_result)
        if used_placeholder:
            missing_digest.append(entry.name)

    return EmitReport(
        group=group,
        out_dir=out_dir,
        results=tuple(results),
        missing_digest=tuple(missing_digest),
    )
