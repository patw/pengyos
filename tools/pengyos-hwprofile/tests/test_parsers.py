"""Tests for :mod:`pengyos_hwprofile.parsers` — pure parsing primitives.

These cover the two non-trivial parsers — ``/proc/cpuinfo`` splitting and PCI
class-code decoding — plus the small integer helper. No filesystem, no
fixtures, no root.
"""

from __future__ import annotations

import pytest

from pengyos_hwprofile.parsers import (
    parse_cpuinfo,
    parse_int,
    parse_pci_class,
)


# ---------------------------------------------------------------------------
# parse_pci_class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("0x030000", "display"),   # display controller
        ("0x010802", "storage"),   # NVMe mass storage
        ("0x020000", "network"),   # Ethernet
        ("0x060000", None),        # host bridge — not profiled
        ("0x060400", None),        # PCI bridge — not profiled
        ("0x0c0500", None),        # USB host controller — not profiled
        ("0x080600", None),        # generic serial — not profiled
    ],
)
def test_parse_pci_class_maps_base_octet(code, expected):
    assert parse_pci_class(code) == expected


def test_parse_pci_class_tolerates_case_and_whitespace():
    assert parse_pci_class("  0X030000  ") == "display"


def test_parse_pci_class_without_prefix():
    # A code may arrive without the "0x" prefix (some tools emit bare hex).
    assert parse_pci_class("030000") == "display"


# ---------------------------------------------------------------------------
# parse_cpuinfo
# ---------------------------------------------------------------------------

_CPUINFO = (
    "processor\t: 0\n"
    "vendor_id\t: AuthenticAMD\n"
    "model name\t: AMD Ryzen 7 5700G\n"
    "cpu cores\t: 8\n"
    "cpu MHz\t: 1800.000\n"
    "\n"
    "processor\t: 1\n"
    "vendor_id\t: AuthenticAMD\n"
    "model name\t: AMD Ryzen 7 5700G\n"
    "cpu cores\t: 8\n"
    "cpu MHz\t: 1800.000\n"
)


def test_parse_cpuinfo_splits_blocks():
    procs = parse_cpuinfo(_CPUINFO)
    assert len(procs) == 2


def test_parse_cpuinfo_lowercases_keys_and_strips_values():
    (first, _) = parse_cpuinfo(_CPUINFO)
    assert "vendor_id" in first
    assert first["vendor_id"] == "AuthenticAMD"
    assert first["model name"] == "AMD Ryzen 7 5700G"
    assert first["cpu cores"] == "8"
    # Leading/trailing whitespace around the value is stripped.
    assert first["cpu mhz"] == "1800.000"


def test_parse_cpuinfo_ignores_garbage_lines():
    text = "not a key line\n\nprocessor\t: 0\nvendor_id\t: AMD\n"
    procs = parse_cpuinfo(text)
    assert len(procs) == 1
    assert procs[0]["vendor_id"] == "AMD"


def test_parse_cpuinfo_empty_input():
    assert parse_cpuinfo("") == []


def test_parse_cpuinfo_no_trailing_blank_still_flushes():
    # A final block with no trailing blank line must still be returned.
    procs = parse_cpuinfo("processor\t: 0\nvendor_id\t: AMD\n")
    assert len(procs) == 1


# ---------------------------------------------------------------------------
# parse_int
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0x1022", 0x1022),
        ("0X8086", 0x8086),
        ("42", 42),
        ("  42  ", 42),
        ("0", 0),
    ],
)
def test_parse_int_hex_and_dec(text, expected):
    assert parse_int(text) == expected


def test_parse_int_explicit_base():
    assert parse_int("ff", base=16) == 255


def test_parse_int_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse_int("not-a-number")
