"""Tests for :mod:`pengyos_hwprofile.emitter` — deterministic ``profile.toml``.

These lock down the TOML writer: valid output, a byte-stable round-trip through
``tomllib``, fixed key ordering, sorted list sections, and correct escaping. No
fixtures tree, root, network, or hardware required.
"""

from __future__ import annotations

import tomllib

import pytest

from pengyos_hwprofile.emitter import emit
from pengyos_hwprofile.schema import ProfileError, validate

_METADATA = {"hostname": "pengyos-laptop", "kernel": "6.8.0-pengyos", "tool_version": "0.3.0"}
_CPU = {"vendor": "AuthenticAMD", "model": "Ryzen 7", "cores": 8, "threads": 16, "mhz": 1800.0}


def _full_document() -> dict:
    return {
        "metadata": _METADATA,
        "cpu": _CPU,
        "memory": [
            {"locator": "ChannelA-DIMM0", "size_bytes": 8589934592, "speed_mhz": 3200},
            {"locator": "ChannelB-DIMM0", "size_bytes": 8589934592, "speed_mhz": 3200},
        ],
        "gpu": [{"vendor": "AMD", "model": "Vega", "bus": "0000:00:02.0", "driver": "amdgpu"}],
        "storage": [
            {"bus": "sata", "model": "SSD 870", "size_bytes": 500000000000},
            {"bus": "nvme", "model": "NVMe 980", "size_bytes": 476000000000, "transport": "NVMe"},
        ],
        "network": [{"interface": "enp3s0", "mac": "aa:bb:cc:dd:ee:ff", "speed_mbps": 1000}],
        "peripherals": [{"bus": "3:002", "vendor_id": "8086", "product_id": "1901", "name": "Cam"}],
    }


# ---------------------------------------------------------------------------
# Structure / ordering.
# ---------------------------------------------------------------------------


def test_emit_renders_metadata_and_cpu_as_tables():
    text = emit({"metadata": _METADATA, "cpu": _CPU})
    assert text.startswith("[metadata]\n")
    assert "[cpu]" in text
    # metadata keys come before cpu keys.
    assert text.index("hostname") < text.index("[cpu]")


def test_emit_uses_fixed_key_order_for_metadata_and_cpu():
    # The order is fixed regardless of dict insertion order.
    text = emit({"cpu": {"mhz": 1.0, "threads": 1, "cores": 1, "model": "m", "vendor": "v"},
                 "metadata": {"tool_version": "0", "kernel": "k", "hostname": "h"}})
    order = [line.split(" = ")[0] for line in text.splitlines() if " = " in line]
    assert order == ["hostname", "kernel", "tool_version", "vendor", "model", "cores", "threads", "mhz"]


def test_emit_renders_list_sections_as_array_of_tables():
    text = emit({"metadata": _METADATA, "cpu": _CPU, "gpu": [
        {"vendor": "A", "model": "B", "bus": "0000:00:02.0"}]})
    # One [[gpu]] header per item.
    assert text.count("[[gpu]]") == 1
    assert "[[gpu]]" in text


def test_emit_one_header_per_list_item():
    doc = {"metadata": _METADATA, "cpu": _CPU, "storage": [
        {"bus": "sata", "model": "A", "size_bytes": 1},
        {"bus": "nvme", "model": "B", "size_bytes": 2},
    ]}
    text = emit(doc)
    assert text.count("[[storage]]") == 2
    # A header must precede every element, else tomllib merges them.
    assert text.count("bus = ") == 2


def test_emit_skips_absent_list_sections():
    text = emit({"metadata": _METADATA, "cpu": _CPU})
    assert "[[" not in text  # no array-of-tables headers at all


# ---------------------------------------------------------------------------
# Values: strings, ints, floats, escaping.
# ---------------------------------------------------------------------------


def test_emit_ints_are_bare_and_floats_have_a_point():
    text = emit({"metadata": _METADATA, "cpu": _CPU})
    assert "cores = 8" in text
    assert "mhz = 1800.0" in text  # float keeps its decimal point


def test_emit_escapes_special_characters_in_strings():
    text = emit({"metadata": {"hostname": 'line1\nline2\t"q"', "kernel": "k", "tool_version": "0"},
                 "cpu": _CPU})
    assert 'hostname = "line1\\nline2\\t\\"q\\""' in text


def test_emit_omits_trailing_blank_lines_and_ends_with_one_newline():
    text = emit(_full_document())
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


# ---------------------------------------------------------------------------
# Determinism / sorting.
# ---------------------------------------------------------------------------


def test_emit_is_deterministic_regardless_of_input_order():
    doc = _full_document()
    shuffled = {
        "peripherals": doc["peripherals"][::-1],
        "network": doc["network"][::-1],
        "storage": doc["storage"][::-1],
        "gpu": doc["gpu"][::-1],
        "memory": doc["memory"][::-1],
        "metadata": dict(reversed(list(doc["metadata"].items()))),
        "cpu": dict(reversed(list(doc["cpu"].items()))),
    }
    assert emit(doc) == emit(shuffled)


def test_emit_sorts_list_items_by_identity():
    doc = {"metadata": _METADATA, "cpu": _CPU, "storage": [
        {"bus": "sata", "model": "Zeta", "size_bytes": 1},
        {"bus": "nvme", "model": "Alpha", "size_bytes": 2},
    ]}
    text = emit(doc)
    assert text.index("Alpha") < text.index("Zeta")


# ---------------------------------------------------------------------------
# Round-trip through tomllib.
# ---------------------------------------------------------------------------


def test_emit_round_trips_through_tomllib():
    doc = _full_document()
    reloaded = tomllib.loads(emit(doc))
    # Valid, and content-equal (ignoring list order, which emit sorts).
    validate(reloaded)
    for section in ("memory", "gpu", "storage", "network", "peripherals"):
        assert {tuple(sorted(x.items())) for x in reloaded[section]} == {
            tuple(sorted(x.items())) for x in doc[section]
        }
    assert reloaded["metadata"] == doc["metadata"]
    assert reloaded["cpu"] == doc["cpu"]


def test_emit_round_trips_minimal_document():
    doc = {"metadata": _METADATA, "cpu": _CPU}
    reloaded = tomllib.loads(emit(doc))
    validate(reloaded)
    assert reloaded == doc


def test_emit_accepts_int_float_and_string_numbers():
    doc = {"metadata": _METADATA, "cpu": _CPU, "network": [
        {"interface": "eth0", "mac": "00:00", "speed_mbps": 1000}]}
    reloaded = tomllib.loads(emit(doc))
    assert reloaded["network"][0]["speed_mbps"] == 1000


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------


def test_emit_rejects_non_mapping():
    with pytest.raises(ValueError):
        emit([1, 2, 3])
