"""The PengyOS Desktop driver profile, modeled as data.

This module is the single source of truth for the generic (no vendor-acceleration)
Desktop profile. It declares, as plain data:

* the driver **classes** -- ``display``, ``input``, ``wifi``, ``storage``, ``net``
 ;
* the kernel ``CONFIG_*`` **symbols** each class enables (the ``linux.fragment``
  body); and
* the ``linux-firmware`` **prefixes** curated for the Desktop image (the
  ``firmware.list`` selection).

``emit`` renders ``linux.fragment`` + ``firmware.list`` from this data; ``lint``
validates a produced profile against it. Keeping the profile as data -- instead
of a hand-edited fragment -- is the whole point of the package: it is
declarative and testable, and it cannot drift.

## Grounding

Every symbol below is asserted (in ``tests/test_profile.py``) to exist in the
vendored ``symbols.2026.08.txt``; every firmware prefix is asserted to match at
least one upstream name in ``firmware.upstream.txt``. If a symbol drops out of a
future kernel build, or a firmware prefix stops shipping, the grounding test
fails loudly rather than emitting a broken profile.

## The profile

Generic open-source drivers only -- no NVIDIA, no vendor-acceleration toggles.
KMS comes from ``i915`` / ``amdgpu`` / ``radeon`` (all mainline, open); WiFi from
``iwlwifi`` + the ``mac80211``/``cfg80211`` stack + Realtek ``rtlwifi``/``rtl_bt``.
Storage and net lean on ``virtio`` paravirtualization (plus a generic SATA/AHCI
and a generic PCIe NIC) so the installer runs in a VM or on bare metal.

Note on ``virtio``: virtio devices are paravirtualized and ship **no** firmware,
so ``virtio`` contributes kernel symbols (``VIRTIO_BLK``, ``VIRTIO_NET``,
``VIRTIO_INPUT``, ``VIRTIO``) but no firmware prefix. The spec's mention of
"virtio" among firmware prefixes therefore lands as a kernel-config class, not a
firmware selection -- there is nothing to curate there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Canonical class order. ``emit`` groups the fragment by this order and derives
# the firmware-prefix order from it, so the artifacts are deterministic.
#
# ``display`` / ``input`` / ``wifi`` are the classes the linter requires coverage
# for (``lint``'s class-coverage rule); ``storage`` / ``net`` round out the
# installer profile.
CLASS_ORDER: tuple[str, ...] = ("display", "input", "wifi", "storage", "net")

# The kernel-doc header prepended to the emitted linux.fragment. It records what
# the profile is and, importantly, that it is generic -- a marker a future reader
# (or the no-vendor-accel rule) can point at.
FRAGMENT_HEADER: str = (
    "# SPDX-License-Identifier: GPL-2.0-only\n"
    "# PengyOS Desktop generic (no vendor-acceleration) driver profile.\n"
    "# Emitted by `pengyos_desktop_drivers emit` from pengyos_desktop_drivers.profile.\n"
    "# Open-source KMS/input/wifi/virtio drivers only -- no NVIDIA / vendor-accel.\n"
)


@dataclass(frozen=True)
class DriverClass:
    """One driver class: its name, the symbols it enables, its firmware prefixes.

    ``symbols`` are the ``CONFIG_*`` names emitted into ``linux.fragment`` for this
    class. ``firmware_prefixes`` are the upstream firmware paths starting with one
    of these that ``firmware.list`` selects; empty for classes that need no
    firmware (input/storage/net -- their devices get firmware from the wifi/display
    classes, or not at all, as with virtio).
    """

    name: str
    symbols: tuple[str, ...]
    firmware_prefixes: tuple[str, ...] = ()


CLASSES: tuple[DriverClass, ...] = (
    DriverClass(
        name="display",
        symbols=(
            "CONFIG_DRM",
            "CONFIG_DRM_KMS_HELPER",
            "CONFIG_DRM_TTM",
            "CONFIG_DRM_LOAD_EDID_FIRMWARE",
            "CONFIG_DRM_I915",
            "CONFIG_DRM_AMDGPU",
            "CONFIG_DRM_RADEON",
        ),
        firmware_prefixes=("i915/", "amdgpu/", "radeon/"),
    ),
    DriverClass(
        name="input",
        symbols=(
            "CONFIG_INPUT",
            "CONFIG_INPUT_EVDEV",
            "CONFIG_INPUT_FF_MEMLESS",
            "CONFIG_INPUT_JOYDEV",
            "CONFIG_VIRTIO_INPUT",
        ),
    ),
    DriverClass(
        name="wifi",
        symbols=(
            "CONFIG_MAC80211",
            "CONFIG_CFG80211",
            "CONFIG_IWLWIFI",
        ),
        firmware_prefixes=("intel/iwlwifi/", "rtlwifi/", "rtl_bt/"),
    ),
    DriverClass(
        name="storage",
        symbols=(
            "CONFIG_BLK_DEV_SD",
            "CONFIG_VIRTIO",
            "CONFIG_VIRTIO_MMIO",
            "CONFIG_VIRTIO_BLK",
            "CONFIG_SATA_AHCI",
            "CONFIG_SATA_AHCI_PLATFORM",
        ),
    ),
    DriverClass(
        name="net",
        symbols=(
            "CONFIG_NETDEVICES",
            "CONFIG_VIRTIO_NET",
            "CONFIG_8139TOO",
            "CONFIG_R8169",
        ),
    ),
)


def classes() -> tuple[DriverClass, ...]:
    """Return the driver classes in canonical order."""
    return CLASSES


def symbols_by_class() -> dict[str, tuple[str, ...]]:
    """Map each class name to its ordered symbol tuple."""
    return {cls.name: cls.symbols for cls in CLASSES}


def all_symbols() -> tuple[str, ...]:
    """Return every fragment symbol across all classes, in class order.

    Deduplicated while preserving first-seen order, so a symbol shared by two
    classes appears once in the emitted fragment.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for cls in CLASSES:
        for symbol in cls.symbols:
            if symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
    return tuple(ordered)


def firmware_prefixes() -> tuple[str, ...]:
    """Return the curated firmware prefixes, deduped in first-seen (class) order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for cls in CLASSES:
        for prefix in cls.firmware_prefixes:
            if prefix not in seen:
                seen.add(prefix)
                ordered.append(prefix)
    return tuple(ordered)


def class_for(name: str) -> DriverClass | None:
    """Return the class with ``name``, or ``None`` if unknown."""
    for cls in CLASSES:
        if cls.name == name:
            return cls
    return None


def required_for_coverage() -> tuple[str, ...]:
    """Return the classes the linter requires at least one symbol for.

    Matches ``lint``'s class-coverage rule: display + input + wifi.
    """
    return ("display", "input", "wifi")


def iter_symbols(classes: Iterable[str] | None = None) -> Iterable[str]:
    """Yield symbols for the given class names (all ``CLASS_ORDER`` if ``None``)."""
    wanted = set(classes) if classes else set(CLASS_ORDER)
    for cls in CLASSES:
        if cls.name in wanted:
            yield from cls.symbols


# Self-check helper: raises if any declared symbol is absent from the vendored
# 2026.08 list. Not called at import (keep imports cheap/side-effect-free); the
# grounding tests call it so a broken profile fails loudly.
def assert_symbols_present(symbols: set[str]) -> None:
    """Raise ``KeyError`` naming the first declared symbol missing from ``symbols``."""
    missing = [s for s in all_symbols() if s not in symbols]
    if missing:
        raise KeyError(
            "driver profile references symbols absent from the 2026.08 list: "
            + ", ".join(sorted(missing))
        )
