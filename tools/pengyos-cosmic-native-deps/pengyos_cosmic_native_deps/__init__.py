"""pengyos_cosmic_native_deps — Buildroot recipes for COSMIC's non-Rust deps.

COSMIC, the PengyOS desktop, needs a handful of native (non-Rust) dependencies that the pinned
Buildroot ``2026.08`` release does not ship: a login daemon (``greetd``), a
desktop portal (``xdg-desktop-portal`` + the ``cosmic`` backend), seat
management (``seatd``/``libseat``), and its fonts (Fira + Noto). This package
emits Buildroot ``BR2_EXTERNAL`` recipes for exactly those missing dependencies
and lints them, then emits a ``cosmic-native.fragment`` that enables the
corresponding kconfig symbols.

The package holds the recipe catalogue, the emit templates, the linter and the
``pengyos-cosmic-native-deps`` command-line interface.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
