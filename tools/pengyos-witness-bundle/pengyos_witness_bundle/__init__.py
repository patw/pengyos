"""PengyOS witness-bundle.

Present a ``pengyos-build`` output in the canonical witness layout and verify its
SBOM signature, so an external witness can actually see what was built (closing the
"witness can't find the artifact" gap).

See ``spec.md`` for the full contract. This package is stdlib-only at the scaffold
stage; any real dependency is declared in ``pyproject.toml``.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
