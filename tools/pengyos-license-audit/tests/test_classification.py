"""Tests for SPDX-ish expression parsing + family classification.

These pin the "Implement SPDX-ish license expression parsing + family
classification" subtask: the parser (``parse_expression``), the classifier
(``classify`` / ``classify_expression``), and the SPDX-id → family mapping.
"""

from __future__ import annotations

import textwrap

import pytest

from pengyos_license_audit import classify, classify_expression
from pengyos_license_audit.classification import (
    SPDX_FAMILY_BY_ID,
    _BinaryOp,
    _License,
    parse_expression,
)
from pengyos_license_audit.errors import ValidationError
from pengyos_license_audit.families import LicenseFamily


# ---------------------------------------------------------------------------
# Single-claim classification (the spec's verification fixtures)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "claim,expected",
    [
        ("MIT", LicenseFamily.PERMISSIVE),
        ("Apache-2.0", LicenseFamily.PERMISSIVE),
        ("Zlib", LicenseFamily.PERMISSIVE),
        ("ISC", LicenseFamily.PERMISSIVE),
        ("BSD-3-Clause", LicenseFamily.PERMISSIVE),
        ("LGPL-2.1-only", LicenseFamily.WEAK_COPYLEFT),
        ("LGPL-3.0-only", LicenseFamily.WEAK_COPYLEFT),
        ("GPL-2.0-only", LicenseFamily.STRONG_COPYLEFT),
        ("GPL-3.0-only", LicenseFamily.STRONG_COPYLEFT),
        ("Proprietary", LicenseFamily.PROPRIETARY),
    ],
)
def test_classify_single_claim(claim, expected):
    assert classify(claim) is expected


def test_classify_gpl2_with_linux_syscall_note_fixture():
    # The linux-libc-headers fixture: base id is GPL-2.0-only → strong copyleft,
    # the WITH exception clause does not change the family.
    assert classify("GPL-2.0-only WITH Linux-syscall-note") is (
        LicenseFamily.STRONG_COPYLEFT
    )


def test_classify_case_insensitive():
    assert classify("apache-2.0") is LicenseFamily.PERMISSIVE
    assert classify("GPL-3.0-ONLY") is LicenseFamily.STRONG_COPYLEFT
    assert classify("PrOpRiEtArY") is LicenseFamily.PROPRIETARY


def test_classify_normalises_whitespace_and_parenthetical():
    assert classify("  MIT   ") is LicenseFamily.PERMISSIVE
    assert classify("MIT (MIT License)") is LicenseFamily.PERMISSIVE
    assert classify("Apache-2.0   (Apache License 2.0)") is (
        LicenseFamily.PERMISSIVE
    )


def test_classify_synonyms_for_proprietary():
    assert classify("Commercial") is LicenseFamily.PROPRIETARY
    assert classify("All-Rights-Reserved") is LicenseFamily.PROPRIETARY


def test_classify_unmapped_is_unknown():
    assert classify("Totally-Made-Up-1.0") is LicenseFamily.UNKNOWN


def test_classify_rejects_non_string():
    with pytest.raises(ValidationError, match="must be a string"):
        classify(123)  # type: ignore[arg-type]


def test_classify_rejects_empty():
    with pytest.raises(ValidationError, match="empty"):
        classify("   ")


# ---------------------------------------------------------------------------
# Expression parsing
# ---------------------------------------------------------------------------

def test_parse_single_license_leaf():
    node = parse_expression("MIT")
    assert node == _License(id="MIT", exception=None)


def test_parse_with_exception_clause():
    node = parse_expression("GPL-2.0-only WITH Linux-syscall-note")
    assert node == _License(
        id="GPL-2.0-only", exception="Linux-syscall-note"
    )


def test_parse_or_expression():
    node = parse_expression("MIT OR Apache-2.0")
    assert isinstance(node, _BinaryOp)
    assert node.op == "OR"
    assert node.left == _License(id="MIT")
    assert node.right == _License(id="Apache-2.0")


def test_parse_and_expression():
    node = parse_expression("MIT AND ISC")
    assert isinstance(node, _BinaryOp)
    assert node.op == "AND"


def test_parse_nested_parens():
    node = parse_expression("(MIT OR Apache-2.0) AND ISC")
    assert isinstance(node, _BinaryOp)
    assert node.op == "AND"
    assert node.right == _License(id="ISC")
    assert isinstance(node.left, _BinaryOp)
    assert node.left.op == "OR"


def test_parse_allows_glued_parens():
    # "(MIT" and "2.0)" are the common real-world shapes, not errors.
    assert parse_expression("(MIT OR Apache-2.0)") == _BinaryOp(
        "OR", _License(id="MIT"), _License(id="Apache-2.0")
    )


@pytest.mark.parametrize("bad", ["(MIT", "MIT)", "MIT OR )ISC"])
def test_parse_rejects_unbalanced_glued_parens(bad):
    with pytest.raises(ValidationError):
        parse_expression(bad)


# ---------------------------------------------------------------------------
# Expression classification + combination semantics
# ---------------------------------------------------------------------------

def test_classify_or_returns_freest():
    # OR — you may pick the most permissive operand.
    assert classify_expression("MIT OR GPL-2.0-only") is (
        LicenseFamily.PERMISSIVE
    )
    assert classify_expression("GPL-2.0-only OR ISC") is LicenseFamily.PERMISSIVE


def test_classify_and_returns_least_free():
    # AND — you must satisfy every operand, so the least free wins.
    assert classify_expression("MIT AND GPL-2.0-only") is (
        LicenseFamily.STRONG_COPYLEFT
    )
    assert classify_expression("GPL-3.0-only AND Proprietary") is (
        LicenseFamily.PROPRIETARY
    )


def test_classify_grouped_expression():
    assert classify_expression("(MIT OR Apache-2.0) AND ISC") is (
        LicenseFamily.PERMISSIVE
    )
    # The OR group of two strong-copyleft ids stays strong; AND MIT (permissive)
    # then yields the least free = strong-copyleft.
    assert classify_expression("(GPL-2.0-only OR GPL-3.0-only) AND MIT") is (
        LicenseFamily.STRONG_COPYLEFT
    )


def test_classify_with_exception_ignores_clause():
    # The WITH exception must not affect the resulting family.
    assert classify_expression("GPL-2.0-only WITH Linux-syscall-note") is (
        LicenseFamily.STRONG_COPYLEFT
    )


def test_classify_unknown_in_expression():
    # OR collapses to the freest; an unmapped operand is UNKNOWN (least free),
    # so `MIT OR Nope` is permissive while `MIT AND Nope` is unknown.
    assert classify_expression("MIT OR Nope-1.0") is LicenseFamily.PERMISSIVE
    assert classify_expression("MIT AND Nope-1.0") is LicenseFamily.UNKNOWN


# ---------------------------------------------------------------------------
# Mapping table
# ---------------------------------------------------------------------------

def test_spdx_mapping_covers_expected_ids():
    # Every family member in the mapping maps to a real family.
    for _id, family_value in SPDX_FAMILY_BY_ID.items():
        assert family_value in {f.value for f in LicenseFamily}
    # Spot-check the taxonomy boundaries.
    assert SPDX_FAMILY_BY_ID["MIT"] == "permissive"
    assert SPDX_FAMILY_BY_ID["LGPL-2.1-only"] == "weak-copyleft"
    assert SPDX_FAMILY_BY_ID["GPL-2.0-only"] == "strong-copyleft"
    assert SPDX_FAMILY_BY_ID["Proprietary"] == "proprietary"


def test_spdx_mapping_keys_are_canonical_cased():
    # The public view keeps canonical SPDX casing (not upper-cased).
    assert "Apache-2.0" in SPDX_FAMILY_BY_ID
    assert "Apache-2.0".upper() not in SPDX_FAMILY_BY_ID


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "MIT @@@",  # invalid token
        "(MIT OR Apache-2.0",  # unbalanced parens
        "MIT OR",  # dangling operator
        "OR MIT",  # leading operator
        "MIT AND )",  # stray close paren
        "MIT )",  # stray close paren
        "GPL-2.0-only WITH",  # WITH missing exception
        "MIT ()",  # empty parens
    ],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValidationError):
        parse_expression(bad)


def test_classify_rejects_malformed_expression():
    with pytest.raises(ValidationError):
        classify("MIT OR")
