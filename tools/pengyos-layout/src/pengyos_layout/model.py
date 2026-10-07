"""Data model for the PengyOS disk/layout planner.

The layout is *data*: an ordered list of partitions, each with a role, size,
filesystem, mountpoint and optional LUKS2 flag, plus the target probe and the
options that produced it. The install engine consumes a :class:`Plan` as plain
data — it never hardcodes the GPT + ESP + A/B + data layout itself.

Everything here is serializable to/from a plain ``dict`` (and therefore JSON) so
the ``plan`` command can write a golden ``plan.json`` the executor reads back.

The layout (single GPT, in order)::

    #  role    fs      mount        notes
    1  esp     vfat    /boot/efi    EFI System Partition (required)
    2  rootA   ext4    /            A/B slot A (mirrored size)
    3  rootB   ext4    /mnt/b       A/B slot B (mirrored size)
    4  data    btrfs   /data        user data; optional swap slot

Optionally every root/data slot is wrapped in **LUKS2** and a **swap** slot is
appended. Sizes are derived from the release and the target disk: ``esp`` is
fixed, ``rootA``/``rootB`` are byte-symmetric, and ``data`` takes the remainder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class LayoutError(Exception):
    """Base class for all planner layout errors."""


class ValidationError(LayoutError):
    """Raised when a :class:`Plan` violates one or more invariants.

    Carries every violation so a caller (e.g. the ``validate`` command) can
    report them all at once rather than failing on the first.
    """

    def __init__(self, messages: list[str]) -> None:
        self.messages = list(messages)
        super().__init__(";\n".join(self.messages) or "layout is invalid")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class Role(str, Enum):
    """Partition role within the GPT.

    ``str`` subclass so the enum member serializes to its plain string value.
    """

    ESP = "esp"
    ROOT_A = "rootA"
    ROOT_B = "rootB"
    DATA = "data"
    SWAP = "swap"


class Fs(str, Enum):
    """Filesystem (or container marker) for a partition.

    ``luks2`` marks a partition that is a LUKS2 container wrapping an inner
    filesystem (the :attr:`Partition.inner_fs`).
    """

    VFAT = "vfat"
    EXT4 = "ext4"
    BTRFS = "btrfs"
    SWAP = "swap"
    LUKS2 = "luks2"


# Default filesystem per role, used by the planner (later iteration) and as a
# sanity check when a hand-authored plan omits ``fs``.
DEFAULT_FS_BY_ROLE: dict[Role, Fs] = {
    Role.ESP: Fs.VFAT,
    Role.ROOT_A: Fs.EXT4,
    Role.ROOT_B: Fs.EXT4,
    Role.DATA: Fs.BTRFS,
    Role.SWAP: Fs.SWAP,
}


# ---------------------------------------------------------------------------
# Target probe
# ---------------------------------------------------------------------------
@dataclass
class TargetProbe:
    """A description of the target disk the layout will be written to.

    ``total_size`` is the usable disk size in bytes. ``sector_size`` (default
    512) is recorded for completeness; all planner math is byte-based.
    """

    device: str
    total_size: int
    sector_size: int = 512
    is_live: bool = False

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "total_size": self.total_size,
            "sector_size": self.sector_size,
            "is_live": self.is_live,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TargetProbe":
        return cls(
            device=data["device"],
            total_size=int(data["total_size"]),
            sector_size=int(data.get("sector_size", 512)),
            is_live=bool(data.get("is_live", False)),
        )


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
@dataclass
class Options:
    """Edition-independent installation options.

    ``luks`` wraps every root/data slot in LUKS2; ``swap_size`` bytes (0 = no
    swap) appends a swap slot. ``edition`` is informational for later sizing.
    """

    luks: bool = False
    swap_size: int = 0
    edition: str = "server"

    def to_dict(self) -> dict:
        return {
            "luks": self.luks,
            "swap_size": self.swap_size,
            "edition": self.edition,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        return cls(
            luks=bool(data.get("luks", False)),
            swap_size=int(data.get("swap_size", 0)),
            edition=str(data.get("edition", "server")),
        )


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------
@dataclass
class Partition:
    """A single GPT partition.

    ``size`` is required (bytes). ``start`` is the byte offset once the layout
    is laid out on disk (the planner emits a start; the executor writes it).
    ``type_guid``/``guid`` are the GPT type and unique GUIDs — filled in by the
    planner for a deterministic, reproducible layout. ``inner_fs`` records the
    filesystem inside a LUKS2 container (a LUKS partition's public ``fs`` stays
    ``luks2``).
    """

    role: Role
    size: int
    fs: Fs = Fs.EXT4
    mount: Optional[str] = None
    luks: bool = False
    start: int = 0
    type_guid: str = ""
    guid: str = ""
    inner_fs: Optional[Fs] = None
    name: str = ""

    def __post_init__(self) -> None:
        # Normalize enums from raw strings so hand-authored/JSON plans survive.
        self.role = Role(self.role)
        self.fs = Fs(self.fs)
        if self.inner_fs is not None:
            self.inner_fs = Fs(self.inner_fs)

        # A LUKS partition's public fs is luks2; the inner fs is the real one.
        if self.luks and self.fs != Fs.LUKS2:
            self.fs = Fs.LUKS2
            if self.inner_fs is None:
                self.inner_fs = self.fs_default_inner()
        if not self.luks and self.fs == Fs.LUKS2:
            raise LayoutError(
                f"partition '{self.role}' is not LUKS but fs is luks2"
            )

        if not self.name:
            self.name = self.role.value

    def fs_default_inner(self) -> Fs:
        """Inner fs for a LUKS container: swap stays plaintext, else role default."""
        if self.role == Role.SWAP:
            return Fs.SWAP
        return DEFAULT_FS_BY_ROLE[self.role]

    def inner_fs_value(self) -> str:
        """The inner filesystem name for a LUKS slot (or its own fs otherwise).

        Used by :mod:`pengyos_layout.emit` to pick the right ``mkfs`` for a slot:
        a LUKS slot is formatted with its *inner* fs on the decrypted mapper, a
        plaintext slot with its own fs on the raw partition.
        """
        fs = self.inner_fs if self.luks else self.fs
        return fs.value if hasattr(fs, "value") else str(fs)

    def is_root_slot(self) -> bool:
        return self.role in (Role.ROOT_A, Role.ROOT_B)

    def to_dict(self) -> dict:
        d = {
            "role": self.role.value,
            "size": self.size,
            "start": self.start,
            "fs": self.fs.value,
            "mount": self.mount,
            "luks": self.luks,
            "type_guid": self.type_guid,
            "guid": self.guid,
            "name": self.name,
        }
        if self.inner_fs is not None:
            d["inner_fs"] = self.inner_fs.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Partition":
        return cls(
            role=data["role"],
            size=int(data["size"]),
            start=int(data.get("start", 0)),
            fs=data.get("fs", Fs.EXT4.value),
            mount=data.get("mount"),
            luks=bool(data.get("luks", False)),
            type_guid=data.get("type_guid", ""),
            guid=data.get("guid", ""),
            inner_fs=data.get("inner_fs"),
            name=data.get("name", ""),
        )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    """The full computed layout: an ordered list of partitions over a probe."""

    probe: TargetProbe
    options: Options
    partitions: list[Partition] = field(default_factory=list)

    # --- invariants ---------------------------------------------------------
    @property
    def total_size(self) -> int:
        """Sum of every partition's size in bytes."""
        return sum(p.size for p in self.partitions)

    def role(self, role: Role) -> Optional[Partition]:
        """Return the first partition with ``role`` (or None)."""
        for p in self.partitions:
            if p.role == role:
                return p
        return None

    def root_slots(self) -> list[Partition]:
        return [p for p in self.partitions if p.is_root_slot()]

    def to_dict(self) -> dict:
        return {
            "probe": self.probe.to_dict(),
            "options": self.options.to_dict(),
            "partitions": [p.to_dict() for p in self.partitions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        return cls(
            probe=TargetProbe.from_dict(data["probe"]),
            options=Options.from_dict(data["options"]),
            partitions=[Partition.from_dict(d) for d in data["partitions"]],
        )
