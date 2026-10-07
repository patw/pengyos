"""PengyOS hwprofile — hardware detection → declarative profile.

Detect hardware and emit a declarative, version-controllable per-machine
profile; diff and validate.

See ``spec.md`` for the full contract. This package is stdlib-only at runtime;
``pytest`` is an optional dev dependency (see ``pyproject.toml``).
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
