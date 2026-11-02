"""What a kernel version change leaves stale that Buildroot's stamps cannot see.

Moving ``BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE`` gives ``linux`` a new build dir
(``linux-<new>``), so the kernel itself is rebuilt. Two things are not:

* **Packages built against the kernel.** An out-of-tree module package
  (nvidia-open, v4l2loopback) depends on ``linux`` by name, and the name does
  not change with the version, so neither the option check nor the
  dependency check in :mod:`pengyos_build.stale` marks it. Its stamps stay
  green, it is never rebuilt, and the image ships no module for the new kernel
  (the NVIDIA images would boot without a GPU driver). :func:`module_packages`
  names every package whose resolved dependencies include ``linux``.
* **The old kernel's module tree.** Buildroot never deletes from ``target/``,
  so ``usr/lib/modules/<old>`` stays beside ``usr/lib/modules/<new>`` and is
  in the image: 82-175 MB per edition at 7.2.13, enough to push both Server
  root filesystems past their fixed size. Nothing can load from it once the
  kernel moved. :func:`old_module_trees` finds it; :mod:`pengyos_build.leftovers`
  would only have reported it, after the build.

Found 2026-11-02 on the 7.2.13 -> 7.3.1 bump, checked by hand before the
night's build; this makes the next one automatic.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .stale import parse_config

#: The resolved kernel version Buildroot writes to ``.config`` for every way
#: of choosing one (custom version, latest, a tarball's own version string).
VERSION_SYMBOL = "BR2_LINUX_KERNEL_VERSION"

#: Where modules live in ``target/``. ``lib`` is a symlink to ``usr/lib`` on a
#: merged-usr image; :func:`old_module_trees` resolves and de-duplicates.
MODULE_ROOTS = ("usr/lib/modules", "lib/modules")


def kernel_version(config_text: str) -> str | None:
    """The kernel version a ``.config`` builds, or None if it builds no kernel."""
    values = parse_config(config_text)
    if values.get("BR2_LINUX_KERNEL") != "y":
        return None
    version = values.get(VERSION_SYMBOL, "").strip('"')
    return version or None


def module_packages(deps: dict[str, set[str]]) -> set[str]:
    """Packages whose resolved dependency list includes ``linux``."""
    return {name for name, d in deps.items() if "linux" in d and name != "linux"}


def _belongs_to(release: str, version: str) -> bool:
    """``uname -r`` ``release`` is kernel ``version``'s, plain or with a local suffix."""
    return release == version or release.startswith((version + "-", version + "+"))


def old_module_trees(target: Path, old: str, new: str) -> tuple[Path, ...]:
    """``target/`` module dirs of kernel ``old`` that kernel ``new`` will not own.

    Only the old version's own release dirs are returned, so nothing another
    kernel, or the new one, installs is ever touched.
    """
    found: dict[Path, Path] = {}
    for root in MODULE_ROOTS:
        base = target / root
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            if _belongs_to(entry.name, old) and not _belongs_to(entry.name, new):
                found.setdefault(entry.resolve(), entry.resolve())
    return tuple(sorted(found))


def remove_trees(paths: tuple[Path, ...]) -> None:
    """Delete the given module trees."""
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
