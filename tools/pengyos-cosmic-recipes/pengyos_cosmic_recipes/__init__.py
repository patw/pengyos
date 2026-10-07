"""PengyOS cosmic-recipes — Buildroot recipes for the COSMIC components.

A manifest-driven front-end over the :mod:`pengyos_cosmic_pkg` engine
(the "COSMIC packaging engine"): it turns the COSMIC component manifest into a
set of pinned, lint-clean Buildroot cargo recipes and closes the dependency
loop with a closure linter.

This package is the *recipes* lane — it owns the manifest data and the
group/emit/lint CLI that delegates to the engine. The engine itself
(``pengyos_cosmic_pkg``) is a **sibling tool** that this package imports; see
the README's "Dependencies" section for how it is put on ``sys.path``.

This package is standard-library-only at runtime.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .emit import (  # noqa: F401
    PLACEHOLDER_HASH,
    EmitReport,
    EmitResult,
    emit_group,
)
from .lint import (  # noqa: F401
    ClosureReport,
    ClosureViolation,
    closure_lint,
)
from .non_cargo import (  # noqa: F401
    NON_CARGO_COMPONENTS,
    NON_CARGO_KINDS,
    GNOME_SHELL_EXTENSION,
    NonCargoComponent,
    handling_path,
    is_non_cargo,
    lookup,
    recipe_kind,
)
from .manifest import (  # noqa: F401
    GROUPS,
    ComponentEntry,
    CosmicManifest,
    MANIFEST_NAME,
    ManifestError,
    component_manifest,
    load_manifest,
    parse_manifest,
)

__all__ = [
    "__version__",
    "GROUPS",
    "ComponentEntry",
    "CosmicManifest",
    "MANIFEST_NAME",
    "ManifestError",
    "component_manifest",
    "load_manifest",
    "parse_manifest",
    "emit_group",
    "EmitReport",
    "EmitResult",
    "PLACEHOLDER_HASH",
    "closure_lint",
    "ClosureReport",
    "ClosureViolation",
    "NonCargoComponent",
    "NON_CARGO_COMPONENTS",
    "NON_CARGO_KINDS",
    "GNOME_SHELL_EXTENSION",
    "handling_path",
    "is_non_cargo",
    "lookup",
    "recipe_kind",
]
