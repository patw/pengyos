"""Tests for :mod:`pengyos_hwprofile_kernel.mapper`.

These lock in the "encode the device-class -> fragment mapping as data; support
an explicit unknown-device path (warn, never silently drop)" contract
. They are table-driven — each fixture profile
yields an expected fragment set — and exercise the unknown-device path with a
real fixture device that has no bound driver.

No network, no root, no hardware: the mapping runs over plain dicts (the
hwprofile document shape) and the committed fixture.
"""

from __future__ import annotations

import os

from pengyos_hwprofile_kernel.mapper import (
    MAPPED_SECTIONS,
    MAPPING,
    MapperReport,
    select_fragments,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> dict:
    import tomllib

    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return tomllib.load(fh)


def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, name)


# ---------------------------------------------------------------------------
# The mapping is data: the table is well-formed and section-scoped.
# ---------------------------------------------------------------------------


def test_mapping_rules_are_section_scoped():
    """Every rule names a section the mapping actually reads."""
    for rule in MAPPING:
        assert rule.section in MAPPED_SECTIONS
        # Each rule selects a known fragment basename (no ``.fragment`` suffix).
        assert rule.fragment.endswith(".fragment") is False


def test_mapping_has_every_device_class_covered():
    """At least one rule exists for each mapped device section."""
    covered = {rule.section for rule in MAPPING}
    assert {"cpu", "gpu", "network", "storage"} <= covered


# ---------------------------------------------------------------------------
# Table-driven: the committed AMD-laptop fixture.
# ---------------------------------------------------------------------------


def test_amd_laptop_maps_to_expected_fragments():
    """The fixture (AMD Ryzen 7 5700G laptop) maps to its build fragments."""
    report = select_fragments(_fixture("profile.toml"))
    # AMD CPU + AMD IGPU (by driver) + qemu (e1000e NIC) + nvme + sata storage.
    assert report.fragments == ("amd-cpu", "amd-igpu", "nvme", "qemu", "sata")


def test_amd_laptop_reports_the_driverless_device_as_unknown():
    """A device with no matching rule is reported, never dropped/raised."""
    report = select_fragments(_fixture("profile.toml"))
    # ``wlo1`` is a Wi-Fi iface with no bound driver -> unknown.
    assert len(report.unknown) == 1
    unknown = report.unknown[0]
    assert unknown.section == "network"
    assert unknown.device["interface"] == "wlo1"
    assert unknown.fragment is None
    # The per-network matches are exactly: wlo1 (unknown) and enp3s0 (selects
    # qemu via its e1000e driver). The driverless iface contributed no fragment.
    net_matches = {m.device["interface"]: m.fragment for m in report.matches if m.section == "network"}
    assert net_matches == {"wlo1": None, "enp3s0": "qemu"}


def test_select_is_deterministic_and_sorted():
    """Repeated mapping of the same document yields identical ordering."""
    doc = _fixture("profile.toml")
    first = select_fragments(doc)
    second = select_fragments(doc)
    assert first.fragments == second.fragments
    assert list(first.fragments) == sorted(first.fragments)


def test_fragments_are_deduplicated():
    """Multiple devices selecting the same fragment collapse to one."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        # Two Intel GPUs -> the intel-igpu fragment is selected twice, deduped.
        "gpu": [
            {"vendor": "Intel Corporation", "model": "A", "bus": "00:02.0", "driver": "i915"},
            {"vendor": "Intel Corporation", "model": "B", "bus": "01:02.0", "driver": "i915"},
        ],
        "network": [],
        "storage": [],
    }
    report = select_fragments(doc)
    assert report.fragments == ("intel-cpu", "intel-igpu")
    # Two GPU matches, both selecting intel-igpu.
    gpu_matches = [m for m in report.matches if m.section == "gpu"]
    assert len(gpu_matches) == 2
    assert all(m.fragment == "intel-igpu" for m in gpu_matches)


# ---------------------------------------------------------------------------
# Selection signals: driver beats vendor-name fallback; bus vs transport.
# ---------------------------------------------------------------------------


def test_gpu_matches_by_driver_preferred_over_vendor():
    """A GPU with a bound driver selects by driver, not by vendor name."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [{"vendor": "Intel Corporation", "model": "X", "bus": "00:02.0", "driver": "i915"}],
        "network": [],
        "storage": [],
    }
    report = select_fragments(doc)
    assert report.fragments == ("intel-cpu", "intel-igpu")


def test_gpu_falls_back_to_vendor_name_when_no_driver():
    """A GPU with no bound driver still selects by vendor name."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        # No "driver" key -> falls back to the vendor-name rule.
        "gpu": [{"vendor": "Intel Corporation", "model": "X", "bus": "00:02.0"}],
        "network": [],
        "storage": [],
    }
    report = select_fragments(doc)
    assert report.fragments == ("intel-cpu", "intel-igpu")


def test_gpu_unknown_vendor_is_reported_not_dropped():
    """A GPU whose vendor matches no rule is reported as unknown."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [{"vendor": "Some New_VENDOR Inc.", "model": "X", "bus": "00:02.0"}],
        "network": [],
        "storage": [],
    }
    report = select_fragments(doc)
    assert report.fragments == ("intel-cpu",)
    assert len(report.unknown) == 1
    assert report.unknown[0].section == "gpu"


def test_storage_matches_by_bus():
    """NVMe/SATA storage selects by the bus field."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [],
        "storage": [
            {"bus": "nvme", "model": "N", "size_bytes": 1, "transport": "NVMe"},
            {"bus": "sata", "model": "S", "size_bytes": 1, "transport": "SATA"},
        ],
    }
    report = select_fragments(doc)
    assert report.fragments == ("intel-cpu", "nvme", "sata")


def test_network_without_driver_is_unknown():
    """A network iface with no bound driver is reported as unknown."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [{"interface": "wlx", "mac": "aa:bb:cc:dd:ee:ff"}],
        "storage": [],
    }
    report = select_fragments(doc)
    assert report.fragments == ("intel-cpu",)
    assert len(report.unknown) == 1
    assert report.unknown[0].section == "network"


# ---------------------------------------------------------------------------
# Base fragments: always included, additive.
# ---------------------------------------------------------------------------


def test_base_fragments_are_always_included():
    """Base fragments (e.g. a CPU-family base build) are always added."""
    doc = {
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [],
        "storage": [],
    }
    report = select_fragments(doc, base_fragments={"cpu": "intel-cpu"})
    # intel-cpu is selected both as a base fragment and by the CPU device;
    # it appears once in the deduplicated, sorted set.
    assert report.fragments == ("intel-cpu",)


def test_base_fragment_added_even_when_no_device_matches():
    """A base fragment is present even with no devices at all."""
    report = select_fragments({}, base_fragments={"cpu": "amd-cpu"})
    assert report.fragments == ("amd-cpu",)
    # No devices -> nothing unknown.
    assert report.unknown == ()


# ---------------------------------------------------------------------------
# Empty / minimal documents degrade gracefully.
# ---------------------------------------------------------------------------


def test_empty_document_yields_no_fragments():
    report = select_fragments({})
    assert report.fragments == ()
    assert report.unknown == ()
    assert report.matches == ()


def test_report_helpers():
    """MapperReport fragment_set deduplicates."""
    report = select_fragments({
        "cpu": {"vendor": "GenuineIntel", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [],
        "storage": [{"bus": "nvme", "model": "N", "size_bytes": 1}],
    })
    assert report.fragment_set == frozenset({"intel-cpu", "nvme"})
