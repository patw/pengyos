"""PengyOS hwprofile-kernel — hardware profile → kernel fragments (US-CORE-5).

Turn a ``pengyos-hwprofile`` hardware profile into a minimal kernel fragment set
plus a defconfig fragment, so the PengyOS image carries only the drivers and
services the target machine needs.

See ``spec.md`` for the full contract. This package is standard-library-only at runtime; ``pytest`` is an
optional dev dependency (see ``pyproject.toml``).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
