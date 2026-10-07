"""Tests for the hwprofile schema (data model + validator).

These lock down the contract defined in ``pengyos_hwprofile.schema``: the
shape of a valid ``profile.toml`` and the exact error the validator raises for
each kind of violation. No fixtures tree, root, network, or hardware required.
"""

from __future__ import annotations

import pytest

from pengyos_hwprofile.schema import (
    CPU,
    GPU,
    MEMORY,
    METADATA,
    NETWORK,
    PERIPHERALS,
    STORAGE,
    Profile,
    ProfileError,
    validate,
    load_profile,
    SECTIONS,
    REQUIRED_SECTIONS,
    TOP_LEVEL_SECTIONS,
)


# ---------------------------------------------------------------------------
# Fixtures — valid documents.
# ---------------------------------------------------------------------------
def make_full_profile() -> dict:
    """A complete, valid profile exercising every section and optional field."""

    return {
        "metadata": {
            "hostname": "pengyos-laptop",
            "kernel": "6.8.0-pengyos",
            "tool_version": "0.2.0",
        },
        "cpu": {
            "vendor": "GenuineIntel",
            "model": "Intel(R) Core(TM) i7-1068G7 @ 2.30GHz",
            "cores": 4,
            "threads": 8,
            "mhz": 2.3,
        },
        "memory": [
            {
                "locator": "ChannelA-DIMM0",
                "size_bytes": 8589934592,
                "speed_mhz": 3200,
                "manufacturer": "Crucial",
                "memory_type": "DDR4",
            }
        ],
        "gpu": [
            {
                "vendor": "Intel",
                "model": "Iris Xe Graphics",
                "bus": "0000:00:02.0",
                "driver": "i915",
                "vram_bytes": 1073741824,
            }
        ],
        "storage": [
            {
                "bus": "nvme",
                "model": "Samsung NVMe SSD 980",
                "size_bytes": 512110199552,
                "serial": "S1W1NY0KA12345",
                "speed": "6.0 Gb/s",
                "transport": "NVMe",
            }
        ],
        "network": [
            {
                "interface": "eth0",
                "mac": "00:11:22:33:44:55",
                "driver": "e1000e",
                "speed_mbps": 1000,
                "bus": "0000:00:1f.6",
            }
        ],
        "peripherals": [
            {
                "bus": "001:003",
                "vendor_id": "8086",
                "product_id": "1901",
                "name": "Intel UVC Camera",
            }
        ],
    }


def make_minimal_profile() -> dict:
    """The smallest valid profile: metadata + cpu only."""

    return {
        "metadata": {
            "hostname": "headless",
            "kernel": "6.8.0-pengyos",
            "tool_version": "0.2.0",
        },
        "cpu": {
            "vendor": "GenuineIntel",
            "model": "Xeon",
            "cores": 2,
            "threads": 2,
            "mhz": 2.0,
        },
    }


# ---------------------------------------------------------------------------
# Valid documents.
# ---------------------------------------------------------------------------
def test_full_profile_validates():
    assert validate(make_full_profile()) is None


def test_minimal_profile_validates():
    assert validate(make_minimal_profile()) is None


def test_empty_list_sections_are_valid():
    # A machine with no memory modules reported still validates.
    doc = make_minimal_profile()
    doc.update({"memory": [], "gpu": [], "storage": []})
    validate(doc)


def test_number_accepts_int_and_float():
    for value in (2, 2.0, 2.5):
        doc = make_minimal_profile()
        doc["cpu"]["mhz"] = value
        validate(doc)


def test_bool_is_rejected_as_int():
    doc = make_minimal_profile()
    doc["cpu"]["cores"] = True
    with pytest.raises(ProfileError, match="cpu.cores: expected int, got bool"):
        validate(doc)


def test_load_profile_builds_frozen_data_model():
    prof = load_profile(make_full_profile())
    assert isinstance(prof, Profile)
    assert prof.metadata.hostname == "pengyos-laptop"
    assert prof.cpu.cores == 4
    assert prof.cpu.threads == 8
    assert prof.cpu.mhz == 2.3
    assert len(prof.memory) == 1
    assert prof.memory[0].size_bytes == 8589934592
    assert prof.memory[0].speed_mhz == 3200
    assert prof.gpu[0].driver == "i915"
    assert prof.storage[0].transport == "NVMe"
    assert prof.network[0].speed_mbps == 1000
    assert prof.peripherals[0].name == "Intel UVC Camera"


def test_load_profile_optional_fields_are_none_when_absent():
    prof = load_profile(make_minimal_profile())
    assert prof.memory == ()
    assert prof.gpu == ()
    assert prof.storage == ()
    assert prof.network == ()
    assert prof.peripherals == ()


def test_profile_is_frozen():
    prof = load_profile(make_minimal_profile())
    with pytest.raises(Exception):  # noqa: B017 - any frozen-instance error
        prof.cpu.model = "hacker"


def test_load_profile_rejects_invalid_document():
    bad = make_minimal_profile()
    del bad["cpu"]
    with pytest.raises(ProfileError):
        load_profile(bad)


# ---------------------------------------------------------------------------
# Top-level structure errors.
# ---------------------------------------------------------------------------
def test_top_level_must_be_table():
    with pytest.raises(ProfileError, match="top level"):
        validate([])


def test_unknown_top_level_section_rejected():
    doc = make_minimal_profile()
    doc["disk"] = []
    with pytest.raises(ProfileError, match="unknown top-level section\\(s\\): disk"):
        validate(doc)


def test_metadata_required():
    doc = make_minimal_profile()
    del doc[METADATA]
    with pytest.raises(ProfileError, match="missing required section\\(s\\): metadata"):
        validate(doc)


def test_cpu_required():
    doc = make_minimal_profile()
    del doc[CPU]
    with pytest.raises(ProfileError, match="missing required section\\(s\\): cpu"):
        validate(doc)


# ---------------------------------------------------------------------------
# Table-level errors (metadata + cpu).
# ---------------------------------------------------------------------------
def test_missing_required_field_reports_path():
    doc = make_minimal_profile()
    del doc["cpu"]["mhz"]
    with pytest.raises(ProfileError, match="cpu: missing required field 'mhz'"):
        validate(doc)


def test_unknown_field_in_table_rejected():
    doc = make_minimal_profile()
    doc["cpu"]["temperature_c"] = 45
    with pytest.raises(ProfileError, match="cpu: unknown field\\(s\\): temperature_c"):
        validate(doc)


def test_metadata_unknown_field_rejected():
    doc = make_minimal_profile()
    doc["metadata"]["uptime_s"] = 123
    with pytest.raises(ProfileError, match="metadata: unknown field\\(s\\): uptime_s"):
        validate(doc)


def test_wrong_type_reports_path_and_kind():
    doc = make_minimal_profile()
    doc["cpu"]["cores"] = "four"
    with pytest.raises(ProfileError, match="cpu.cores: expected int, got str"):
        validate(doc)


def test_mhz_must_be_number_not_string():
    doc = make_minimal_profile()
    doc["cpu"]["mhz"] = "2.3"
    with pytest.raises(ProfileError, match="cpu.mhz: expected number, got str"):
        validate(doc)


# ---------------------------------------------------------------------------
# List-section errors (memory / gpu / storage / network / peripherals).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "section,kind",
    [
        (MEMORY, "memory"),
        (GPU, "gpu"),
        (STORAGE, "storage"),
        (NETWORK, "network"),
        (PERIPHERALS, "peripherals"),
    ],
)
def test_list_section_must_be_list(section, kind):
    doc = make_minimal_profile()
    doc[section] = {"not": "a list"}
    with pytest.raises(ProfileError, match=f"{kind}: expected a list of tables"):
        validate(doc)


def test_list_item_must_be_table_reports_index():
    doc = make_minimal_profile()
    doc[GPU] = [{"vendor": "Intel", "model": "Iris", "bus": "00:02.0"}, "not-a-table"]
    with pytest.raises(ProfileError, match="gpu\\[1\\]: expected a table"):
        validate(doc)


def test_list_item_missing_required_field_reports_index_and_path():
    doc = make_minimal_profile()
    doc[STORAGE] = [{"bus": "sata", "model": "WD", "size_bytes": 1000}]
    doc[STORAGE].append({"bus": "sata", "model": "Seagate"})  # missing size_bytes
    with pytest.raises(ProfileError, match="storage\\[1\\]: missing required field 'size_bytes'"):
        validate(doc)


def test_list_item_unknown_field_rejected():
    doc = make_minimal_profile()
    doc[PERIPHERALS] = [
        {"bus": "001:002", "vendor_id": "8086", "product_id": "1234", "name": "Mouse", "warranty": "2y"}
    ]
    with pytest.raises(ProfileError, match="peripherals\\[0\\]: unknown field\\(s\\): warranty"):
        validate(doc)


def test_optional_fields_may_be_absent_or_present():
    base = {"locator": "A", "size_bytes": 1000}
    doc = make_minimal_profile()
    doc[MEMORY] = [base]
    validate(doc)  # optional members absent is fine

    doc[MEMORY] = [{**base, "speed_mhz": 2666, "manufacturer": "Kingston", "memory_type": "DDR4"}]
    validate(doc)  # optional members present is fine

    loaded = load_profile(doc)
    assert loaded.memory[0].speed_mhz == 2666
    assert loaded.memory[0].manufacturer == "Kingston"


# ---------------------------------------------------------------------------
# Schema introspection — the declarative description is self-consistent.
# ---------------------------------------------------------------------------
def test_sections_cover_all_required_and_optional_sections():
    assert REQUIRED_SECTIONS <= set(SECTIONS)
    assert METADATA in SECTIONS and CPU in SECTIONS
    assert set(SECTIONS) == TOP_LEVEL_SECTIONS


def test_every_section_has_fields():
    for name, fields in SECTIONS.items():
        assert fields, f"{name} has no field spec"
        for f in fields:
            assert f.name and f.kind in ("str", "int", "number")


def test_memory_and_gpu_are_list_sections():
    # Sanity: these are the plural ("(s)") sections of the schema.
    assert MEMORY in TOP_LEVEL_SECTIONS
    assert GPU in TOP_LEVEL_SECTIONS
