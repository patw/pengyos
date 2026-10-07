"""The medium plan as data (spec.md, item 2).

``pengyos-media`` builds a bootable install medium (an ISO or a disk image) for
an edition. Before any build step can be emitted (item 3) or any runner driven
(item 4), the medium has to be *described* as a plain value: what kind of
medium it is, what edition, the live/installer rootfs it carries, the installer
embedded on it, the boot entry it exposes, the release it ships, and the
os-release that names the edition.

This module is that description. It is intentionally pure and serialisable:

* :class:`MediumKind` / :class:`Edition` / :class:`LiveRootfsFormat` are small
  string enums so the plan is a closed vocabulary (an unknown kind or edition
  cannot silently slip into a plan).
* :class:`MediumPlan` is a frozen dataclass whose :meth:`MediumPlan.to_dict`
  emits a plain ``dict`` with a fixed key order, and :func:`to_json` renders it
  deterministically (sorted keys, fixed indent, no trailing whitespace). The
  ``plan --json`` output and the ``--out media.json`` file are both this value,
  so the plan is golden-testable and byte-for-byte reproducible.
* :func:`validate_plan` is the single source of truth for "a well-formed plan"
  (see :mod:`pengyos_media.validate`). ``plan`` produces validated plans;
  ``lint`` checks them.

Stdlib only.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, fields
from typing import Any


class MediumKind(str, enum.Enum):
    """The shape of the medium being built (spec.md "Deliverable").

    * ``ISO`` — an ISO image, assembled with ``xorriso`` (item 3).
    * ``DISK_IMAGE`` — a disk image (GPT: ESP + live partition), assembled with
      ``mkfs``/``mformat``/``dd`` (item 3, a follow-up).
    """

    ISO = "iso"
    DISK_IMAGE = "disk-image"


class Edition(str, enum.Enum):
    """The edition the medium is built for (spec.md + pengyos-live).

    The values are the edition ids, matching the ``--edition`` choices on the
    ``plan`` CLI and the ``os-release`` ``ID`` (item 5).
    """

    DESKTOP = "desktop"
    CORE = "core"
    SERVER = "server"


class LiveRootfsFormat(str, enum.Enum):
    """How the live rootfs is packaged on the medium.

    * ``SQUASHFS`` — a compressed squashfs image (the live stick ships this).
    * ``DIR`` — an unpacked rootfs (an ext4/loop-mounted directory).
    """

    SQUASHFS = "squashfs"
    DIR = "dir"


@dataclass(frozen=True)
class LiveRootfs:
    """The live/installer rootfs carried on the medium.

    The live rootfs is what the live session boots from and what the installer
    installs from. It is packaged as a squashfs image (default) or an unpacked
    directory (:attr:`format`).
    """

    format: LiveRootfsFormat = LiveRootfsFormat.SQUASHFS
    #: The artifact name on the medium, e.g. ``pengyos-desktop.sqfs``.
    name: str = "pengyos-live.sqfs"
    #: Volume label of the live rootfs partition/image.
    label: str = "PENGYOS-LIVE"


@dataclass(frozen=True)
class Installer:
    """The installer embedded on the medium.

    The installer runs in the live session and installs PengyOS onto a target
    disk (see pengyos-installer). Shared naming with the installer tool: the
    binary is ``pengyos-install`` and the live user is ``live``.
    """

    #: The installer binary name (shared with pengyos-installer).
    binary: str = "pengyos-install"
    #: The live user the installer runs as (shared with pengyos-live).
    live_user: str = "live"


@dataclass(frozen=True)
class BootEntry:
    """The boot entry the medium exposes.

    PengyOS ships an EFI boot entry (item 3 produces a hybrid MBR+GPT image so
    any firmware finds a path — see the "USB boot dark arts" note on the bus);
    the entry itself names the loader the firmware hands control to.
    """

    #: The boot entry kind. PengyOS ships an EFI entry.
    kind: str = "efi"
    #: The boot-menu label shown by the bootloader.
    label: str = "PengyOS"
    #: The loader path the entry points at (the removable EFI loader).
    loader: str = "/EFI/BOOT/bootx64.efi"


@dataclass(frozen=True)
class Release:
    """The edition release this medium is built from.

    A *release* is the versioned artifact ``pengyos-release`` produces (see the
    install/update architecture doc); the medium is built from one. ``channel``
    is the release channel (``stable`` / ``testing``).
    """

    #: The edition id of the release (``desktop`` / ``core`` / ``server``).
    edition: str
    #: The release version, e.g. ``2026.09.1``.
    version: str
    #: The release channel (``stable`` / ``testing``).
    channel: str = "testing"


@dataclass(frozen=True)
class OsRelease:
    """The ``os-release`` the medium carries (item 5: it names the edition).

    A minimal, deterministic subset of the freedesktop ``os-release`` spec:
    enough to identify the edition and version the medium ships.
    """

    #: The ``ID`` field — the edition id (``desktop`` / ``core`` / ``server``).
    id: str
    #: The ``PRETTY_NAME`` field.
    pretty_name: str = "PengyOS"
    #: The ``VERSION_ID`` field.
    version_id: str = ""


@dataclass(frozen=True)
class MediumPlan:
    """The full medium plan: the bootable install medium for an edition.

    A frozen, serialisable value. :meth:`to_dict` emits a plain dict with a
    fixed key order; :meth:`to_json` renders it deterministically. Construct a
    plan and validate it with :func:`validate_plan` before emitting it.
    """

    kind: MediumKind
    edition: Edition
    release: Release
    os_release: OsRelease
    live_rootfs: LiveRootfs = field(default_factory=LiveRootfs)
    installer: Installer = field(default_factory=Installer)
    boot_entry: BootEntry = field(default_factory=BootEntry)

    def to_dict(self) -> dict[str, Any]:
        """Render the plan as a plain dict with a fixed key order."""
        return {
            "kind": self.kind.value,
            "edition": self.edition.value,
            "release": {
                "edition": self.release.edition,
                "version": self.release.version,
                "channel": self.release.channel,
            },
            "os_release": {
                "id": self.os_release.id,
                "pretty_name": self.os_release.pretty_name,
                "version_id": self.os_release.version_id,
            },
            "live_rootfs": {
                "format": self.live_rootfs.format.value,
                "name": self.live_rootfs.name,
                "label": self.live_rootfs.label,
            },
            "installer": {
                "binary": self.installer.binary,
                "live_user": self.installer.live_user,
            },
            "boot_entry": {
                "kind": self.boot_entry.kind,
                "label": self.boot_entry.label,
                "loader": self.boot_entry.loader,
            },
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Render the plan as deterministic JSON (sorted keys, fixed indent).

        The canonical form used by ``plan --json`` and ``--out``. The keys are
        already fixed by :meth:`to_dict`; ``sort_keys`` keeps nested dicts
        stable too, and ``ensure_ascii`` keeps the output pure ASCII.
        """
        return json.dumps(
            self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=True
        ) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediumPlan":
        """Rebuild a plan from a :meth:`to_dict` rendering (round-trip)."""
        return cls(
            kind=MediumKind(data["kind"]),
            edition=Edition(data["edition"]),
            release=Release(
                edition=data["release"]["edition"],
                version=data["release"]["version"],
                channel=data["release"].get("channel", "testing"),
            ),
            os_release=OsRelease(
                id=data["os_release"]["id"],
                pretty_name=data["os_release"].get("pretty_name", "PengyOS"),
                version_id=data["os_release"].get("version_id", ""),
            ),
            live_rootfs=LiveRootfs(
                format=LiveRootfsFormat(data["live_rootfs"]["format"]),
                name=data["live_rootfs"].get("name", "pengyos-live.sqfs"),
                label=data["live_rootfs"].get("label", "PENGYOS-LIVE"),
            ),
            installer=Installer(
                binary=data["installer"].get("binary", "pengyos-install"),
                live_user=data["installer"].get("live_user", "live"),
            ),
            boot_entry=BootEntry(
                kind=data["boot_entry"].get("kind", "efi"),
                label=data["boot_entry"].get("label", "PengyOS"),
                loader=data["boot_entry"].get("loader", "/EFI/BOOT/bootx64.efi"),
            ),
        )


def field_order() -> tuple[str, ...]:
    """The top-level field order of a plan (the key order of :meth:`to_dict`)."""
    return tuple(f.name for f in fields(MediumPlan))


def make_plan(
    edition: str | Edition,
    version: str,
    *,
    kind: MediumKind = MediumKind.ISO,
    channel: str = "testing",
    os_release_id: str | None = None,
    live_rootfs: LiveRootfs | None = None,
    installer: Installer | None = None,
    boot_entry: BootEntry | None = None,
) -> MediumPlan:
    """Build a fully-populated :class:`MediumPlan` with sensible defaults.

    ``plan`` (item 3) constructs a plan the same way: pass the edition + the
    release version and every optional part gets its default (a squashfs live
    rootfs, the embedded ``pengyos-install`` installer, the removable EFI boot
    entry, and an ``os-release`` whose ``ID`` is the edition). This is the
    ergonomic half of the model; :func:`validate_plan` is the other.
    """
    if isinstance(edition, str):
        edition = Edition(edition)
    if os_release_id is None:
        os_release_id = edition.value
    return MediumPlan(
        kind=kind,
        edition=edition,
        release=Release(edition=edition.value, version=version, channel=channel),
        os_release=OsRelease(id=os_release_id),
        live_rootfs=live_rootfs or LiveRootfs(),
        installer=installer or Installer(),
        boot_entry=boot_entry or BootEntry(),
    )


def render_plan_text(plan: MediumPlan) -> str:
    """A human-readable summary of a plan's layout.

    The default (non-``--json``) output of ``plan``: a short, ordered description
    of the medium the plan describes. (The ordered *build argv* is item 3.)
    """
    return "\n".join(
        (
            f"medium:        {plan.kind.value}",
            f"edition:       {plan.edition.value}",
            f"release:       {plan.release.edition}/{plan.release.version} "
            f"({plan.release.channel})",
            f"os-release:    id={plan.os_release.id} "
            f"{plan.os_release.pretty_name} {plan.os_release.version_id}".rstrip(),
            f"live rootfs:   {plan.live_rootfs.format.value} "
            f"{plan.live_rootfs.name} (label={plan.live_rootfs.label})",
            f"installer:     {plan.installer.binary} "
            f"(live user {plan.installer.live_user})",
            f"boot entry:    {plan.boot_entry.kind} {plan.boot_entry.label} "
            f"-> {plan.boot_entry.loader}",
        )
    )
