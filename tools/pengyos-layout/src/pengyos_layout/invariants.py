"""The invariants every valid :class:`~pengyos_layout.model.Plan` must satisfy.

Each check is a pure function returning ``None`` when the plan is fine or a
human-readable message when it is not. The :class:`~pengyos_layout.validator.
Validator` runs them all and collects the messages. Keeping the checks as small,
named, composable functions means every invariant has exactly one mutation that
fires it — the ground for the "each invariant has a firing test" contract.

The numbers below are the layout contract, not arbitrary:

* ``ALIGNMENT`` (1 MiB) — the GPT alignment the executor honours, so every
  partition start/size lands on a sector boundary the firmware trusts.
* ``ESP_MIN_BYTES`` — the UEFI FAT32 recommendation floor.
* ``ESP_MAX_BYTES`` — ESP must stay inside the first ``ESP_MAX_MB`` of the disk
  so firmware finds it before the A/B roots.
* ``ROOT_MIN_BYTES`` — a root slot smaller than this cannot hold the release.
"""

from __future__ import annotations

from typing import Callable, List

from .model import Fs, Plan, Role

# --- layout constants ------------------------------------------------------
ALIGNMENT = 1 << 20  # 1 MiB byte alignment for every start/size
ESP_MIN_BYTES = 100 << 20  # 100 MiB: UEFI FAT32 recommendation floor
ESP_MAX_MB = 512  # ESP must live inside the first N MiB of the disk
ROOT_MIN_BYTES = 4 << 30  # 4 GiB: below this a root slot cannot hold the release
DATA_MIN_BYTES = 1 << 30  # 1 GiB: below this the data volume is useless
SWAP_MIN_BYTES = 1 << 20  # 1 MiB: a swap slot must be non-trivial

# The ordered roles that make up a complete layout. ``swap`` is optional and,
# when present, is always last; everything else is required and in this order.
REQUIRED_ORDER: List[Role] = [Role.ESP, Role.ROOT_A, Role.ROOT_B, Role.DATA]
ROOT_ROLES: List[Role] = [Role.ROOT_A, Role.ROOT_B]


# --- individual checks ------------------------------------------------------
def _aligned(size: int) -> bool:
    return size > 0 and size % ALIGNMENT == 0


def check_alignment(plan: Plan) -> str | None:
    """Every partition start and size must be 1 MiB aligned."""
    for p in plan.partitions:
        if p.start % ALIGNMENT != 0:
            return f"partition '{p.role}' start {p.start} is not {ALIGNMENT}-aligned"
        if p.size % ALIGNMENT != 0:
            return f"partition '{p.role}' size {p.size} is not {ALIGNMENT}-aligned"
    return None


def check_esp_present(plan: Plan) -> str | None:
    """A layout must contain exactly one ESP."""
    esps = [p for p in plan.partitions if p.role == Role.ESP]
    if not esps:
        return "missing ESP partition"
    if len(esps) > 1:
        return f"expected one ESP, found {len(esps)}"
    return None


def check_esp_fs(plan: Plan) -> str | None:
    """The ESP must be FAT32."""
    esp = plan.role(Role.ESP)
    if esp is not None and esp.fs != Fs.VFAT:
        return f"ESP fs is {esp.fs.value}, must be vfat"
    return None


def check_esp_within_bounds(plan: Plan) -> str | None:
    """The ESP must fit inside the first ``ESP_MAX_MB`` of the disk."""
    esp = plan.role(Role.ESP)
    if esp is None:
        return None  # reported by check_esp_present
    if esp.start + esp.size > ESP_MAX_MB << 20:
        return (
            f"ESP extends to byte {esp.start + esp.size} "
            f"beyond the {ESP_MAX_MB} MiB bound"
        )
    return None


def check_esp_min_size(plan: Plan) -> str | None:
    """The ESP must be at least ``ESP_MIN_BYTES``."""
    esp = plan.role(Role.ESP)
    if esp is not None and esp.size < ESP_MIN_BYTES:
        return f"ESP {esp.size} bytes is below the {ESP_MIN_BYTES} byte minimum"
    return None


def check_root_present_and_symmetric(plan: Plan) -> str | None:
    """Both A/B root slots must exist and be byte-symmetric in size."""
    a = plan.role(Role.ROOT_A)
    b = plan.role(Role.ROOT_B)
    if a is None or b is None:
        return "both rootA and rootB slots are required for A/B"
    if a.size != b.size:
        return (
            f"A/B slots are asymmetric: rootA={a.size} rootB={b.size}"
        )
    return None


def check_root_min_size(plan: Plan) -> str | None:
    """Each root slot must be at least ``ROOT_MIN_BYTES``."""
    for role in ROOT_ROLES:
        slot = plan.role(role)
        if slot is not None and slot.size < ROOT_MIN_BYTES:
            return (
                f"{role.value} {slot.size} bytes is below the "
                f"{ROOT_MIN_BYTES} byte minimum"
            )
    return None


def check_data_present_and_min(plan: Plan) -> str | None:
    """The data volume must exist and be at least ``DATA_MIN_BYTES``."""
    data = plan.role(Role.DATA)
    if data is None:
        return "missing data partition"
    if data.size < DATA_MIN_BYTES:
        return (
            f"data {data.size} bytes is below the {DATA_MIN_BYTES} byte minimum"
        )
    return None


def check_swap_min_size(plan: Plan) -> str | None:
    """If a swap slot exists it must be at least ``SWAP_MIN_BYTES``."""
    swap = plan.role(Role.SWAP)
    if swap is not None and swap.size < SWAP_MIN_BYTES:
        return (
            f"swap {swap.size} bytes is below the {SWAP_MIN_BYTES} byte minimum"
        )
    return None


def check_no_overlap(plan: Plan) -> str | None:
    """Partitions, laid out in order from ``start``, must not overlap."""
    for prev, cur in zip(plan.partitions, plan.partitions[1:]):
        if cur.start < prev.start + prev.size:
            return (
                f"partition '{cur.role}' at {cur.start} overlaps "
                f"'{prev.role}' ending at {prev.start + prev.size}"
            )
    return None


def check_fits_disk(plan: Plan) -> str | None:
    """The whole layout must fit within the target disk (bytes)."""
    total = plan.total_size
    if total > plan.probe.total_size:
        return (
            f"layout {total} bytes exceeds disk "
            f"{plan.probe.total_size} bytes (over by {total - plan.probe.total_size})"
        )
    return None


def check_not_live_device(plan: Plan) -> str | None:
    """Refuse to plan against the live boot device."""
    if plan.probe.is_live:
        return (
            f"refusing to plan against live device {plan.probe.device!r}"
        )
    return None


# Ordered list of (name, callable). Order matters: a plan must first be a valid
# layout (roles present) before geometry (overlap/fit) is meaningful, and the
# live-device refusal is reported alongside the others.
CHECKS: List[tuple[str, Callable[[Plan], str | None]]] = [
    ("live_device", check_not_live_device),
    ("alignment", check_alignment),
    ("esp_present", check_esp_present),
    ("esp_fs", check_esp_fs),
    ("esp_within_bounds", check_esp_within_bounds),
    ("esp_min_size", check_esp_min_size),
    ("root_symmetric", check_root_present_and_symmetric),
    ("root_min_size", check_root_min_size),
    ("data_present_min", check_data_present_and_min),
    ("swap_min_size", check_swap_min_size),
    ("no_overlap", check_no_overlap),
    ("fits_disk", check_fits_disk),
]
