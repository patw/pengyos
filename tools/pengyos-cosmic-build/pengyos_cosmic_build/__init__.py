"""PengyOS cosmic-build — vendored, buildable COSMIC recipes + Desktop target wiring.

Closes the gap between the COSMIC recipes' placeholder 'shapes' and a real
Buildroot build:

* ``vendor-all`` — drive the vendoring engine per component (from the pinned
  sources + each component's ``Cargo.lock``) to obtain a real source tarball,
  its sha256, and a real SITE.
* ``emit`` — emit recipes that pin those real digests + SITEs, with the symbol
  prefix the desktop defconfig uses.
* ``wire`` — install the recipes + the desktop defconfig + board overlay + the
  sibling native-dep recipes into a ``BR2_EXTERNAL`` tree, wire ``Config.in``,
  and register the ``pengyos-desktop`` target.
* ``lint`` — assert the tree is buildable-shaped: no placeholder SITE/hash,
  every ``BR2_PACKAGE_*`` the defconfig references resolves to a recipe, the
  native deps are present, and the target is registered.

This package is the *wiring* lane: it owns the BR2_EXTERNAL tree, the desktop
defconfig/board overlay, and the ``pengyos-desktop`` target registration. The
feature bodies (``vendor_all`` / ``emit`` / ``wire`` / ``lint``) live in their
focused modules.

The engine and sibling lanes are imported from their own ``tools/`` dirs (see
``conftest.py``); this package is standard-library-only at runtime.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
