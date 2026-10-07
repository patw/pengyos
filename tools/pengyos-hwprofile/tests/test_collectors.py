"""Tests for :mod:`pengyos_hwprofile.collectors`.

These drive every collector against the committed fixture tree
(``tests/fixtures/sys``) — a realistic, hand-authored mirror of the parts of
``/sys`` + ``/proc`` the collectors read. No root, no network, no real
hardware. Each test asserts the exact shape and value the collector must emit,
so the fixture and the collector evolve together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_hwprofile import __version__, collectors
from pengyos_hwprofile.collectors import (
    collect,
    collect_all,
    collect_cpu,
    collect_gpu,
    collect_memory,
    collect_metadata,
    collect_network,
    collect_peripherals,
    collect_storage,
    collect_profile,
)
from pengyos_hwprofile.schema import Profile, ProfileError
from pengyos_hwprofile.source import Source, open_source

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "sys"


@pytest.fixture
def source():
    return open_source(FIXTURES)


# ---------------------------------------------------------------------------
# Metadata.
# ---------------------------------------------------------------------------


def test_collect_metadata_hostname_kernel_toolsource(source):
    md = collect_metadata(source)
    assert md == {
        "hostname": "pengyos-laptop",
        "kernel": "6.8.0-pengyos",
        "tool_version": __version__,
    }


def test_collect_metadata_requires_hostname():
    # Point at an empty tree (no hostname) — a clear, path-qualified error.
    empty = open_source(Path(__file__).resolve().parents[0] / "fixtures" / "empty")
    with pytest.raises(ProfileError, match="metadata.hostname"):
        collect_metadata(empty)


def test_collect_metadata_requires_kernel(tmp_path):
    # A tree with a hostname but no osrelease fails on the kernel field.
    (tmp_path / "proc" / "sys" / "kernel").mkdir(parents=True)
    (tmp_path / "proc" / "sys" / "kernel" / "hostname").write_text("host\n")
    empty = open_source(tmp_path)
    with pytest.raises(ProfileError, match="metadata.kernel"):
        collect_metadata(empty)


# ---------------------------------------------------------------------------
# CPU.
# ---------------------------------------------------------------------------


def test_collect_cpu_from_cpuinfo(source):
    cpu = collect_cpu(source)
    assert cpu == {
        "vendor": "AuthenticAMD",
        "model": "AMD Ryzen 7 5700G with Radeon Graphics",
        "cores": 8,
        "threads": 16,
        "mhz": 1800.0,
    }


def test_collect_cpu_threads_equals_processor_block_count(source):
    # 16 logical processors in the fixture -> threads == 16, cores == 8.
    assert collect_cpu(source)["threads"] == 16
    assert collect_cpu(source)["cores"] == 8


def test_collect_cpu_requires_cpuinfo():
    empty = open_source(Path(__file__).resolve().parents[0] / "fixtures" / "empty")
    with pytest.raises(ProfileError, match="cpu"):
        collect_cpu(empty)


# ---------------------------------------------------------------------------
# Memory (DMI).
# ---------------------------------------------------------------------------


def test_collect_memory_two_installed_modules(source):
    mem = collect_memory(source)
    assert len(mem) == 2  # slot 2 ("No Module Installed") is skipped.
    assert mem[0] == {
        "locator": "ChannelA-DIMM0",
        "size_bytes": 8589934592,
        "speed_mhz": 3200,
        "manufacturer": "Crucial",
        "memory_type": "DDR4",
    }
    assert mem[1]["locator"] == "ChannelB-DIMM0"
    assert mem[1]["size_bytes"] == 8589934592


def test_collect_memory_size_bytes_converts_mb():
    # "8192 MB" -> 8 GiB (8 * 1024^3 bytes).
    assert collect_memory(open_source(FIXTURES))[0]["size_bytes"] == 8 * 1024**3


def test_collect_memory_empty_list_when_no_slots(source):
    # A fixture with no memory/ dir yields an empty (valid) list.
    src = Source(FIXTURES)
    assert isinstance(collect_memory(src), list)


# ---------------------------------------------------------------------------
# GPU (PCI display).
# ---------------------------------------------------------------------------


def test_collect_gpu_single_display_controller(source):
    gpu = collect_gpu(source)
    assert len(gpu) == 1
    assert gpu[0]["vendor"] == "Advanced Micro Devices, Inc. [AMD]"
    assert gpu[0]["model"] == "Vega [Radeon RX Vega]"
    assert gpu[0]["bus"] == "0000:00:02.0"


def test_collect_gpu_reads_bound_driver_when_present(source):
    # The fixture binds "amdgpu" to the display device's driver symlink.
    gpu = collect_gpu(source)
    assert gpu[0].get("driver") == "amdgpu"


def test_collect_gpu_omits_driver_when_unbound(tmp_path):
    # A display device with no driver symlink yields no driver key.
    dev = tmp_path / "bus" / "pci" / "devices" / "0000:00:02.0"
    dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x8086\n")
    (dev / "device").write_text("0x3eaf\n")
    (dev / "class").write_text("0x030000\n")
    src = open_source(tmp_path)
    gpu = collect_gpu(src)
    assert len(gpu) == 1
    assert "driver" not in gpu[0]


def test_collect_gpu_filters_non_display_devices(source):
    # The fixture has host/PCI bridges (0x060000, 0x060400) that must be
    # excluded; only the display controller remains.
    gpu = collect_gpu(source)
    assert all(g["bus"].startswith("0000:00:02") for g in gpu)


# ---------------------------------------------------------------------------
# Storage (NVMe + SATA).
# ---------------------------------------------------------------------------


def test_collect_storage_nvme_and_sata(source):
    storage = collect_storage(source)
    by_model = {s["model"]: s for s in storage}
    assert "Samsung NVMe SSD 980" in by_model
    assert "Samsung SSD 870 EVO" in by_model


def test_collect_storage_nvme_fields(source):
    nvme = next(s for s in collect_storage(source) if s["bus"] == "nvme")
    assert nvme == {
        "bus": "nvme",
        "model": "Samsung NVMe SSD 980",
        "size_bytes": 476944979456,
        "transport": "NVMe",
        "serial": "S1W1NY0KA12345",
    }


def test_collect_storage_sata_bus_is_sata(source):
    sata = next(s for s in collect_storage(source) if s["bus"] == "sata")
    assert sata["model"] == "Samsung SSD 870 EVO"
    assert sata["transport"] == "sata"
    # 8940807584 * 512 bytes.
    assert sata["size_bytes"] == 8940807584 * 512


def test_collect_storage_skips_loop_devices(source):
    models = {s["model"] for s in collect_storage(source)}
    # loop0 exists in the fixture but must never appear.
    assert not any("loop" in m.lower() for m in models)


def test_collect_storage_sectors_convert_to_bytes(source):
    # 931533163 sectors * 512.
    nvme = next(s for s in collect_storage(source) if s["bus"] == "nvme")
    assert nvme["size_bytes"] == 931533163 * 512


# ---------------------------------------------------------------------------
# Network (sysfs interfaces).
# ---------------------------------------------------------------------------


def test_collect_network_interfaces(source):
    net = collect_network(source)
    ifaces = {n["interface"]: n for n in net}
    assert set(ifaces) == {"enp3s0", "wlo1"}  # loopback skipped


def test_collect_network_skips_loopback(source):
    net = collect_network(source)
    assert all(n["interface"] != "lo" for n in net)


def test_collect_network_reads_speed_driver_and_bus(source):
    eth = next(n for n in collect_network(source) if n["interface"] == "enp3s0")
    assert eth["mac"] == "2c:f0:5d:71:52:31"
    assert eth["speed_mbps"] == 1000
    assert eth["driver"] == "e1000e"
    assert eth["bus"] == "0000:00:1f.6"


def test_collect_network_wifi_without_speed(source):
    wifi = next(n for n in collect_network(source) if n["interface"] == "wlo1")
    assert wifi["mac"] == "f8:ac:65:b2:1e:84"
    assert "speed_mbps" not in wifi  # no speed file -> omitted


# ---------------------------------------------------------------------------
# Peripherals (USB).
# ---------------------------------------------------------------------------


def test_collect_peripherals_skips_hubs(source):
    per = collect_peripherals(source)
    # Only the three end devices; the Linux Foundation hubs (1d6b) are dropped.
    assert len(per) == 3
    assert all(p["vendor_id"] != "1d6b" for p in per)


def test_collect_peripherals_vendor_product_and_name(source):
    per = collect_peripherals(source)
    by_name = {p["name"]: p for p in per}
    assert by_name["Intel UVC Camera"]["vendor_id"] == "8086"
    assert by_name["Intel UVC Camera"]["product_id"] == "1901"
    assert by_name["PRO CARBON"]["bus"] == "5:001"


def test_collect_peripherals_bus_token_derived_from_name(source):
    per = collect_peripherals(source)
    # Device "5-2" -> bus token "5:002".
    real_sense = next(p for p in per if "RealSense" in p["name"])
    assert real_sense["bus"] == "5:002"


# ---------------------------------------------------------------------------
# Assembly + validation.
# ---------------------------------------------------------------------------


def test_collect_all_has_every_section(source):
    doc = collect_all(source)
    assert set(doc) == {
        "metadata",
        "cpu",
        "memory",
        "gpu",
        "storage",
        "network",
        "peripherals",
    }


def test_collect_validates_and_returns_document(source):
    # collect() must raise on an invalid document, return the dict otherwise.
    doc = collect(source)
    assert doc["metadata"]["hostname"] == "pengyos-laptop"


def test_collect_profile_builds_frozen_model(source):
    profile = collect_profile(source)
    assert isinstance(profile, Profile)
    assert profile.metadata.hostname == "pengyos-laptop"
    assert profile.cpu.cores == 8
    assert len(profile.memory) == 2
    assert len(profile.gpu) == 1
    assert len(profile.storage) == 2
    assert len(profile.network) == 2
    assert len(profile.peripherals) == 3


def test_collect_all_is_deterministic(source):
    # Two collections over the same fixture must be byte-identical.
    assert collect_all(source) == collect_all(source)


def test_collect_empty_source_raises_profile_error():
    # An empty tree has no cpu/metadata, so collect() must raise a
    # path-qualified ProfileError rather than emit a bogus minimal profile.
    empty = open_source(Path(__file__).resolve().parents[0] / "fixtures" / "empty")
    with pytest.raises(ProfileError):
        collect(empty)
