"""PengyOS cosmic-pkg — the COSMIC packaging engine.

Turns a git Rust component (a component manifest: URL + pinned tag + Cargo.lock)
into a Buildroot cargo package with a vendored, git-dependency-aware source tree.

The public surface is the CLI (``pengyos_cosmic_pkg.cli``) with three
subcommands, each delegated to a focused module:

* ``vendor`` — :mod:`pengyos_cosmic_pkg.vendor`
* ``recipe`` — :mod:`pengyos_cosmic_pkg.recipe`
* ``lint``   — :mod:`pengyos_cosmic_pkg.lint`

Every subcommand consumes the same input: a :class:`ComponentManifest`
(:mod:`pengyos_cosmic_pkg.manifest`), the data model + validator for one git
Rust component (URL + pinned tag + license + cargo features/binaries + deps).

This package is standard-library-only at runtime.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
