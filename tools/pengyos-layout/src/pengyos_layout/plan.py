"""Derive a golden :class:`~pengyos_layout.model.Plan` from a probe + options.

``plan`` is the *layout as data* step the install engine consumes: it turns a
:class:`~pengyos_layout.model.TargetProbe` (the target disk) plus
:class:`~pengyos_layout.model.Options` (edition / LUKS / swap) into an **ordered
list of partitions**, each with a role, size, filesystem, mountpoint, GPT
``type_guid`` and a deterministic per-partition ``guid`` and byte ``start``.

Sizing contract (the single source of truth the tests assert against):

* ``esp`` is **fixed** at :data:`ESP_BYTES`.
* ``rootA`` / ``rootB`` are **byte-symmetric** at :data:`ROOT_BYTES` each.
* ``data`` takes the **remainder** of the disk (floored to zero, aligned down).
* ``swap`` is appended last when :data:`Options.swap_size` is non-zero.

Every partition lands on a :data:`ALIGNMENT` (1 MiB) boundary, the roles are laid
out in the fixed order ``esp, rootA, rootB, data[, swap]`` and every partition
carries the GPT ``type_guid`` for its role plus a deterministic unique ``guid``.
The result is deterministic: the same probe + options always yields the same
plan, so it can be committed as a golden ``plan.json``.
"""

from __future__ import annotations

from .invariants import (
    ALIGNMENT,
    DATA_MIN_BYTES,
    ESP_MAX_MB,
    ESP_MIN_BYTES,
    ROOT_MIN_BYTES,
)
from .model import (
    DEFAULT_FS_BY_ROLE,
    LayoutError,
    Options,
    Partition,
    Plan,
    Role,
    TargetProbe,
)

# --- fixed layout constants (bytes, 1 MiB aligned) --------------------------
# These are the golden sizes. ``esp`` is a fixed EFI partition, the two root
# slots are byte-symmetric, and ``data`` is whatever the disk has left.
ESP_BYTES = 256 << 20  # 256 MiB: EFI System Partition
ROOT_BYTES = 8 << 30  # 8 GiB per A/B slot

# Mountpoints per role. Mounting is the engine's runtime concern, but the planner
# records the canonical mountpoints so the golden plan is self-describing.
MOUNT_BY_ROLE: dict[Role, str | None] = {
    Role.ESP: "/boot/efi",
    Role.ROOT_A: "/",
    Role.ROOT_B: "/mnt/b",
    Role.DATA: "/data",
    Role.SWAP: None,
}

# GPT type GUIDs per role (the values ``sgdisk -t`` expects). Deterministic and
# shared, so the golden plan and the emitted sgdisk argv agree on the type.
TYPE_GUID_BY_ROLE: dict[Role, str] = {
    Role.ESP: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",  # EFI System
    Role.ROOT_A: "0FC63DA8-8483-4772-8E79-3D69D8477DE4",  # Linux root x86-64
    Role.ROOT_B: "0FC63DA8-8483-4772-8E79-3D69D8477DE4",  # Linux root x86-64
    Role.DATA: "9FS2FC2B-66DA-4464-BF7D-6011622C226C",  # Linux filesystem data
    Role.SWAP: "0657FD6D-A4AB-434C-8E2B-9BC23F004711",  # Linux swap
}

# A fixed "pool" UUID whose low 32 bits are replaced by the partition index to
# form a deterministic per-partition unique GUID (GPT partition GUIDs must be
# unique within the table; here determinism is the whole point).
GUID_POOL = "6DA4DA21-B529-4965-8365-A25674613B6B"


def _pool_guid(index: int) -> str:
    """Deterministic unique GUID for partition ``index``.

    The pool's low 32 bits are overwritten with ``index`` so every partition
    gets a distinct, reproducible unique GUID.
    """
    head = GUID_POOL[: -8]  # the first 12 hex chars (6 bytes) are fixed
    counter = index & 0xFFFFFFFF
    return f"{head}{counter:08X}"


def _align_down(value: int, alignment: int = ALIGNMENT) -> int:
    """Round ``value`` down to the nearest ``alignment`` boundary."""
    return (value // alignment) * alignment


def _root_size(used: int) -> int:
    """Root slot size: at least :data:`ROOT_BYTES`, then step up to the next
    1 MiB multiple of 4 GiB so the A/B pair stays symmetric and lands on a
    coarse boundary.

    ``used`` is the free space left after the ESP. Stepping up in 4 GiB keeps
    the roots on a natural boundary and leaves the remainder for data.

    Raises :class:`~pengyos_layout.model.LayoutError` when ``used`` is too small
    to hold even the minimum symmetric A/B pair plus a minimal data volume. That
    guard is what keeps the step-up loop below from running forever: once we
    know ``2 * ROOT_BYTES + DATA_MIN_BYTES <= used`` exists, the loop is
    guaranteed to terminate (``size`` grows past ``used`` eventually).
    """
    # The smallest valid layout after the ESP: two ROOT_BYTES roots plus the
    # mandatory data minimum. If the disk gives us less, no root size works.
    min_pair = 2 * ROOT_BYTES + DATA_MIN_BYTES
    if used < min_pair:
        raise LayoutError(
            f"only {used} bytes free after the ESP; need at least "
            f"{min_pair} for two {ROOT_BYTES}-byte root slots plus a "
            f"{DATA_MIN_BYTES}-byte data volume"
        )

    # Round the minimum up to the next 4 GiB multiple.
    step = 4 << 30
    size = ((ROOT_BYTES + step - 1) // step) * step  # = 8 GiB, aligned up
    # Grow in 4 GiB steps until the pair (2 slots) plus the data minimum fits.
    # Guaranteed to terminate by the guard above.
    while 2 * size + DATA_MIN_BYTES > used:
        size += step
    return size


def plan(probe: TargetProbe, options: Options) -> Plan:
    """Derive the golden layout for ``probe`` under ``options``.

    Raises :class:`~pengyos_layout.model.LayoutError` when the disk is too small
    to hold a valid layout (ESP + two roots + a minimal data volume).
    """
    if options.swap_size and options.swap_size < 1:
        raise LayoutError("swap_size must be 0 (disabled) or a positive size")

    # Free space after the fixed ESP.
    free_after_esp = probe.total_size - ESP_BYTES
    if free_after_esp <= 0:
        raise LayoutError(
            f"disk {probe.total_size} bytes is too small for even the ESP "
            f"(ESP needs {ESP_BYTES} bytes)"
        )

    root_size = _root_size(free_after_esp)

    # The two symmetric roots plus the mandatory data minimum must fit after the
    # ESP; otherwise the disk cannot hold a valid layout.
    if 2 * root_size + DATA_MIN_BYTES > free_after_esp:
        raise LayoutError(
            f"disk {probe.total_size} bytes is too small to hold an ESP "
            f"({ESP_BYTES}), two root slots ({root_size} each) and a data "
            f"volume (>= {DATA_MIN_BYTES}); need at least "
            f"{ESP_BYTES + 2 * root_size + DATA_MIN_BYTES} bytes"
        )

    # Swap is a fixed-size slot carved out of the data remainder so the whole
    # layout still fills the fixed disk exactly (data + swap == remainder). The
    # golden layout fills the disk, so swap cannot be appended on top without
    # overflowing it — it shrinks data instead.
    swap_alloc = _align_down(options.swap_size) if options.swap_size else 0
    data_size = _align_down(free_after_esp - 2 * root_size - swap_alloc)
    if data_size < DATA_MIN_BYTES:
        raise LayoutError(
            f"disk {probe.total_size} bytes cannot yield a data volume >= "
            f"{DATA_MIN_BYTES} plus a {swap_alloc}-byte swap after two "
            f"{root_size}-byte roots"
        )

    partitions: list[Partition] = []

    def _add(role: Role, size: int, luks: bool) -> Partition:
        p = Partition(
            role=role,
            size=size,
            fs=DEFAULT_FS_BY_ROLE[role],
            mount=MOUNT_BY_ROLE[role],
            luks=luks,
            type_guid=TYPE_GUID_BY_ROLE[role],
            guid=_pool_guid(len(partitions)),
        )
        partitions.append(p)
        return p

    # --- lay the partitions out sequentially, aligned -----------------------
    # The ESP is never LUKS-wrapped: firmware must read its FAT32 directly from
    # the raw partition, before any decryption key is available.
    cursor = 0
    esp = _add(Role.ESP, ESP_BYTES, False)
    esp.start = cursor
    cursor += esp.size

    root_a = _add(Role.ROOT_A, root_size, options.luks)
    root_a.start = cursor
    cursor += root_a.size

    root_b = _add(Role.ROOT_B, root_size, options.luks)
    root_b.start = cursor
    cursor += root_b.size

    data = _add(Role.DATA, data_size, options.luks)
    data.start = cursor
    cursor += data.size

    if options.swap_size:
        swap_size = _align_down(options.swap_size) or options.swap_size
        swap = _add(Role.SWAP, swap_size, False)  # swap is never LUKS-wrapped
        swap.start = cursor

    return Plan(probe=probe, options=options, partitions=partitions)
