"""PengyOS desktop-drivers.

The generic (no vendor-acceleration) driver set for the PengyOS Desktop live and
installed image: a kernel ``linux.fragment`` for KMS/input/wifi/virtio, a curated
``linux-firmware`` subset, and a linter that keeps that profile lint-clean.

This package is the declarative, tested home for that profile. It is split into
small, single-responsibility modules:

* :mod:`pengyos_desktop_drivers.emit` -- render the Desktop ``linux.fragment`` and
  ``firmware.list`` deterministically from the driver profile.
* :mod:`pengyos_desktop_drivers.lint` -- validate a fragment + firmware list
  against the vendored 2026.08 symbol list and the subset / class-coverage rules.

The public API is intentionally small; the CLI in :mod:`pengyos_desktop_drivers.__main__`
is the only user-facing surface.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

# The driver profile is the package's declarative heart; re-export it so callers
# can `from pengyos_desktop_drivers import profile` without reaching into a
# submodule path.
from . import profile  # noqa: E402,F401
