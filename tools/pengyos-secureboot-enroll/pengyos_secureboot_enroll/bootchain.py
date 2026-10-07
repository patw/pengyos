"""Pin the PengyOS ESP boot-chain layout.

This module encodes the *concrete, researched* set of EFI binaries the PengyOS
ESP ships, and how each is identified. It is the manifest source of truth that
:mod:`pengyos_secureboot_enroll.sign_chain` signs and
:mod:`pengyos_secureboot_enroll.lint` verifies: the concrete set of EFI
binaries the ESP must ship (UKI + systemd-boot paths).

It is deliberately a *pinned layout*, not a filesystem scan: the tool decides
which binaries are expected to be present and signed, independently of whatever
happens to be on a given ESP. ``sign-chain`` signs exactly these; ``lint``
verifies exactly these.

The layout is pinned by the PengyOS install & update architecture
(``docs/install-update-architecture.md``,
pinned 2026-09-24): the ESP carries systemd-boot + a UEFI-spec fallback + one
UKI per installed version/slot (A/B), with loader entries carrying boot
counting. The systemd-boot paths are the canonical ``bootctl install`` layout
(``EFI/systemd/systemd-bootx64.efi`` + the mandatory ``EFI/BOOT/BOOTX64.EFI``
fallback), and the UKIs follow the Boot Loader Specification ``EFI/Linux``
directory convention.

Key-id note
-----------
Each :class:`~pengyos_secureboot_enroll.truststate.EspBinary` records the
specific signing-key id it must be signed with. That id is the SHA-256
fingerprint of the ``db`` certificate, produced by the ``keys`` subtask
(``pengyos_secureboot_enroll.truststate.key_fingerprint``). Until that key
material exists, every entry pins ``key_id = None`` -- the semantics of
``EspBinary.key_id`` are "any key present in ``db`` is acceptable" -- so this
module builds a valid manifest today and the precise pin is filled in by
``keys`` without changing the layout.
"""

from __future__ import annotations

from typing import List

from .truststate import EspBinary, EspManifest

__all__ = [
    "ARCH_X64",
    "SYSTEMD_BOOT_DIR",
    "SYSTEMD_BOOT_FALLBACK_DIR",
    "EFI_LINUX_DIR",
    "SYSTEMD_BOOT_PATH",
    "SYSTEMD_BOOT_FALLBACK_PATH",
    "uki_path_for",
    "fixed_bootloader_entries",
    "uki_entry",
    "expected_manifest",
]

# --- pinned paths -----------------------------------------------------------

#: The EFI architecture PengyOS ships. Only x64 is pinned today; the path
#: builder is parameterised so an aarch64 build could add ``EFI/arm64`` later.
ARCH_X64 = "x64"

#: Directory systemd-boot installs its boot manager into (the ``bootctl install``
#: path). Case is preserved on FAT32; this is the lowercase form systemd ships.
SYSTEMD_BOOT_DIR = "EFI/systemd"

#: The UEFI-spec mandatory fallback directory. The firmware boots ``BOOTX64.EFI``
#: here when there is no loader entry to fall back to.
SYSTEMD_BOOT_FALLBACK_DIR = "EFI/BOOT"

#: The Boot Loader Specification directory for signed UKIs. systemd-boot scans
#: this directory and presents one boot entry per ``*.efi`` it finds.
EFI_LINUX_DIR = "EFI/Linux"

#: The systemd-boot boot manager, at its ``bootctl install`` path. The architecture
#: suffix is lowercase, matching the file systemd ships.
SYSTEMD_BOOT_PATH = f"/{SYSTEMD_BOOT_DIR}/systemd-boot{ARCH_X64}.efi"

#: The mandatory UEFI-spec fallback, a copy of systemd-boot. The UEFI specification
#: names this ``BOOTX64.EFI`` (uppercase), which is what systemd ships on disk.
#: FAT32 is case-insensitive, but we pin the canonical on-disk spelling.
SYSTEMD_BOOT_FALLBACK_PATH = f"/{SYSTEMD_BOOT_FALLBACK_DIR}/BOOTX64.EFI"

#: Suffix of every EFI binary / UKI on the ESP.
UKI_SUFFIX = ".efi"


def uki_path_for(edition: str, version: str, slot: str) -> str:
    """Return the ESP path of the UKI for one PengyOS version/slot.

    Mirrors the pinned artifact layout ``EFI/Linux/pengyos-<edition>+<ver>-<slot>.efi``
    (``install-update-architecture.md``). ``version`` is the dotted release
    (``1.0.0``); ``slot`` is the A/B slot (``a`` / ``b``).

    The path is returned in canonical POSIX form (leading ``/``, no trailing
    slash) so it compares equal to a manifest lookup.
    """
    return f"/{EFI_LINUX_DIR}/pengyos-{edition}+{version}-{slot}{UKI_SUFFIX}"


def fixed_bootloader_entries() -> List[EspBinary]:
    """Return the two systemd-boot binaries the ESP must ship, in boot order.

    1. ``/EFI/systemd/systemd-bootx64.efi`` -- the systemd-boot boot manager at
       its ``bootctl install`` path.
    2. ``/EFI/BOOT/BOOTX64.EFI`` -- the UEFI-spec mandatory fallback (a copy of
       systemd-boot) so the firmware can boot even with no loader entry.

    Both are pinned to ``key_id = None`` ("any db key") until the ``keys``
    subtask pins the ``db`` fingerprint.
    """
    return [
        EspBinary(
            path=SYSTEMD_BOOT_PATH,
            key_id=None,
            description="systemd-boot boot manager (bootctl install path)",
        ),
        EspBinary(
            path=SYSTEMD_BOOT_FALLBACK_PATH,
            key_id=None,
            description="UEFI-spec mandatory fallback (copy of systemd-boot)",
        ),
    ]


def uki_entry(edition: str, version: str, slot: str) -> EspBinary:
    """Return the :class:`EspBinary` for the UKI of one version/slot.

    Pinned to ``key_id = None`` ("any db key") until the ``keys`` subtask pins
    the ``db`` fingerprint.
    """
    return EspBinary(
        path=uki_path_for(edition, version, slot),
        key_id=None,
        description=f"PengyOS unified kernel image (edition={edition}, v{version}, slot {slot})",
    )


def expected_manifest(
    edition: str,
    version: str,
    slots: List[str],
) -> EspManifest:
    """Return the full PengyOS ESP manifest for a set of installed slots.

    The manifest is the fixed systemd-boot pair plus one UKI per entry in
    ``slots`` (the A/B boot state -- the boot menu *is* the A/B state). ``slots``
    is the ordered list of installed slots, e.g. ``["a"]`` for a fresh install
    or ``["a", "b"]`` after an update. The manifest is deduped by path and
    insertion-ordered: systemd-boot, fallback, then one UKI per slot.

    Every entry is pinned to ``key_id = None`` ("any db key") until the ``keys``
    subtask pins the ``db`` fingerprint.
    """
    if not slots:
        raise ValueError("expected_manifest requires at least one installed slot")

    manifest = EspManifest()
    for binary in fixed_bootloader_entries():
        manifest = manifest.add(binary)
    for slot in slots:
        manifest = manifest.add(uki_entry(edition, version, slot))
    return manifest
