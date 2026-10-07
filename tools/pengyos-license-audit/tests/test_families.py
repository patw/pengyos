"""Tests for the license family taxonomy (``pengyos_license_audit.families``)."""

from __future__ import annotations

from pengyos_license_audit import FAMILY_ORDER, LicenseFamily


def test_license_family_names():
    assert {f.value for f in LicenseFamily} == {
        "permissive",
        "weak-copyleft",
        "strong-copyleft",
        "proprietary",
        "unknown",
    }


def test_family_order_is_freedominated_and_stable():
    # Index 0 is the freest family; unknown is the least known.
    assert FAMILY_ORDER[0] is LicenseFamily.PERMISSIVE
    assert FAMILY_ORDER[-1] is LicenseFamily.UNKNOWN
    # Free families rank ahead of strong copyleft, which ranks ahead of proprietary.
    assert FAMILY_ORDER.index(LicenseFamily.PERMISSIVE) < FAMILY_ORDER.index(
        LicenseFamily.STRONG_COPYLEFT
    )
    assert FAMILY_ORDER.index(LicenseFamily.STRONG_COPYLEFT) < FAMILY_ORDER.index(
        LicenseFamily.PROPRIETARY
    )


def test_family_str_is_value():
    # The taxonomy is a str-enum; its repr is the human value, not the member.
    assert str(LicenseFamily.WEAK_COPYLEFT) == "weak-copyleft"
    assert LicenseFamily.PERMISSIVE == "permissive"
