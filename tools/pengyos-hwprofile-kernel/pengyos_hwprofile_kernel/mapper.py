"""Map a hardware profile's device classes to kernel fragment names.

This is the heart of the tool: given a validated
hwprofile document, decide which kernel **fragments** (from
``pengyos-kernel-fragments``) the target machine needs. The decision is made by
a **data-driven** device-class -> fragment mapping — see :data:`MAPPING` below —
rather than a tangle of ``if/elif`` branches, so adding a device class is a
data change, not a code change.

The mapping keys off the fields the hwprofile schema actually carries for each
device (see :mod:`pengyos_hwprofile.schema`):

* **cpu** — ``vendor`` (decoded PCI vendor, e.g. ``"AuthenticAMD"``).
* **gpu** — bound ``driver`` first (``"amdgpu"`` / ``"i915"`` / ``"nvidia"``),
  then the human-readable ``vendor`` name as a fallback.
* **network** — bound ``driver`` (``"virtio_net"`` / ``"e1000e"`` / ...).
* **storage** — ``bus`` / ``transport`` (``"nvme"`` / ``"sata"`` / ...).

Unknown-device path
--------------------
A device that matches **no** rule is *not* silently dropped: it is recorded in
the report's ``unknown`` list so the caller can warn about it (the spec's
"warn, never silently drop" requirement). The tool never raises on an unknown
device — an unrecognized device simply contributes no fragment and is reported.

Standard library only at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# The section names the mapping reads from a validated hwprofile document.
CPU = "cpu"
GPU = "gpu"
NETWORK = "network"
STORAGE = "storage"

# The set of device sections the mapping understands. Sections outside this set
# (memory, peripherals, ...) are intentionally ignored by the mapping.
MAPPED_SECTIONS: tuple[str, ...] = (CPU, GPU, NETWORK, STORAGE)


# ---------------------------------------------------------------------------
# The mapping — declared as data.
#
# Each entry is a :class:`Rule`: the ``section`` it applies to, the
# ``attribute`` to read from each device dict (a key present in the hwprofile
# schema for that section), and the ``value`` that key must equal to select
# ``fragment``. Matching is *exact* on the attribute value, so the table is the
# single source of truth for "which device selects which fragment".
#
# ``fragment`` is the *basename* of a fragment file in the
# ``pengyos-kernel-fragments`` library (``<fragment>.fragment``), which is the
# unit :func:`select_fragments` consumes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """A single ``(section, attribute, value) -> fragment`` mapping rule."""

    section: str
    attribute: str
    value: str
    fragment: str


# The mapping table. It is intentionally small and explicit: one row per
# (device-class signal, fragment) pair the tool knows about. Extend this table
# to support a new device class or GPU — no code changes required.
MAPPING: tuple[Rule, ...] = (
    # CPU vendor -> a vendor base fragment (the machine's CPU family build).
    Rule(CPU, "vendor", "AuthenticAMD", "amd-cpu"),
    Rule(CPU, "vendor", "GenuineIntel", "intel-cpu"),
    # GPU by bound kernel driver (the most reliable signal).
    Rule(GPU, "driver", "i915", "intel-igpu"),
    Rule(GPU, "driver", "amdgpu", "amd-igpu"),
    Rule(GPU, "driver", "nvidia", "nvidia-proprietary"),
    # GPU by human-readable vendor name (fallback when no driver is bound).
    Rule(GPU, "vendor", "Intel Corporation", "intel-igpu"),
    Rule(GPU, "vendor", "Advanced Micro Devices, Inc. [AMD]", "amd-igpu"),
    Rule(GPU, "vendor", "NVIDIA Corp", "nvidia-proprietary"),
    # Network by bound kernel driver.
    Rule(NETWORK, "driver", "virtio_net", "qemu"),
    Rule(NETWORK, "driver", "e1000e", "qemu"),
    # Storage by bus/transport.
    Rule(STORAGE, "bus", "nvme", "nvme"),
    Rule(STORAGE, "bus", "sata", "sata"),
    Rule(STORAGE, "transport", "NVMe", "nvme"),
    Rule(STORAGE, "transport", "SATA", "sata"),
)


# ---------------------------------------------------------------------------
# Reports.
# ---------------------------------------------------------------------------


@dataclass
class DeviceMatch:
    """One device and the fragment its selection produced (or ``None``).

    :ivar device: the original device dict from the profile (for reporting).
    :ivar section: the profile section the device came from.
    :ivar fragment: the selected fragment basename, or ``None`` when the device
        matched no rule (it is then "unknown").
    :ivar rule: the :class:`Rule` that matched, or ``None`` for an unknown
        device.
    """

    device: Mapping[str, Any]
    section: str
    fragment: str | None
    rule: Rule | None


@dataclass
class MapperReport:
    """The outcome of mapping a profile's devices to fragments.

    :ivar fragments: the selected fragment basenames, deduplicated and sorted
        (deterministic), excluding the base fragments.
    :ivar unknown: the devices that matched no rule — reported, never dropped.
    :ivar matches: every per-device match, in profile section order.
    """

    fragments: tuple[str, ...] = ()
    unknown: tuple[DeviceMatch, ...] = ()
    matches: tuple[DeviceMatch, ...] = ()

    @property
    def fragment_set(self) -> frozenset[str]:
        """The selected fragments as a set (deduplication happens here)."""
        return frozenset(self.fragments)


# ---------------------------------------------------------------------------
# Selection.
# ---------------------------------------------------------------------------


def _match_device(rule_iter, device: Mapping[str, Any]) -> Rule | None:
    """Return the first :class:`Rule` in *rule_iter* that matches *device*.

    A rule matches when the device has the rule's ``attribute`` and its value
    equals the rule's ``value`` (exact, case-sensitive match). A missing
    attribute never matches.
    """

    for rule in rule_iter:
        if device.get(rule.attribute) == rule.value:
            return rule
    return None


def _rules_for(section: str) -> tuple[Rule, ...]:
    """Return the rules that apply to *section*, in table order."""

    return tuple(r for r in MAPPING if r.section == section)


def select_fragments(
    document: Mapping[str, Any],
    base_fragments: Mapping[str, str] | None = None,
) -> MapperReport:
    """Map a validated hwprofile *document* to the fragments it needs.

    Parameters:
        document: A validated hwprofile document (the plain dict returned by
            :func:`pengyos_hwprofile_kernel.profiles.load_profile`).
        base_fragments: An optional ``{section: fragment}`` map of *base*
            fragments that are always included regardless of device matching
            (e.g. ``{"cpu": "amd-cpu"}`` for a CPU-family base build). Base
            fragments are added to the result but are **not** reported as
            unknowns and are excluded from :attr:`MapperReport.fragments`
            dedup semantics for device matches (they are added to the final
            set). ``None`` means no base fragments.

    Returns:
        A :class:`MapperReport`. ``fragments`` is the sorted, deduplicated set
        of device-selected fragment basenames; ``unknown`` lists every device
        that matched no rule (so the caller can warn); ``matches`` lists every
        per-device match.
    """

    base_fragments = base_fragments or {}
    matches: list[DeviceMatch] = []
    unknown: list[DeviceMatch] = []
    selected: set[str] = set()

    for section in MAPPED_SECTIONS:
        devices = document.get(section)
        # ``cpu`` is a single table in the schema; gpu/network/storage are
        # lists. Normalise both shapes to a list of device dicts so the match
        # loop below is uniform across sections.
        if isinstance(devices, Mapping):
            devices = [devices]
        elif not isinstance(devices, list):
            devices = []
        for device in devices:
            if not isinstance(device, Mapping):
                continue
            rule = _match_device(_rules_for(section), device)
            fragment = rule.fragment if rule is not None else None
            match = DeviceMatch(device, section, fragment, rule)
            matches.append(match)
            if fragment is None:
                unknown.append(match)
            else:
                selected.add(fragment)

    # Base fragments are always included.
    selected.update(base_fragments.values())

    return MapperReport(
        fragments=tuple(sorted(selected)),
        unknown=tuple(unknown),
        matches=tuple(matches),
    )
