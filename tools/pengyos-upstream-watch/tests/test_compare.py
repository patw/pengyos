"""Tests for semver-ish version comparison.

Covers :func:`parse_version`, :func:`compare`, :func:`is_newer`,
:func:`classify` and the :class:`ParsedVersion` value object, including the
spec's required edge cases:

* LTS ``x.y`` vs ``x.y.z`` equivalence (``1.2`` == ``1.2.0``),
* prerelease ranking below the stable release and among themselves,
* numeric (not lexical) core ordering (``1.10`` > ``1.9``).

No I/O, no network, no fetcher — the comparison is pure.
"""

from __future__ import annotations

import itertools

import pytest

from pengyos_upstream_watch.compare import (
    Drift,
    ParsedVersion,
    classify,
    compare,
    is_newer,
    parse_version,
)


# --- parse_version ----------------------------------------------------------


def test_parse_version_core_is_int_tuple():
    v = parse_version("1.2.3")
    assert v.core == (1, 2, 3)
    assert v.prerelease == ()
    assert v.raw == "1.2.3"
    assert not v.is_prerelease()


def test_parse_version_two_part_core():
    """An LTS-style two-part core parses to a two-tuple (not padded)."""
    v = parse_version("1.2")
    assert v.core == (1, 2)
    assert v.prerelease == ()


def test_parse_version_strips_leading_v_and_V():
    assert parse_version("v1.2.3").core == (1, 2, 3)
    assert parse_version("V1.2.3").core == (1, 2, 3)


def test_parse_version_leading_v_only_when_followed_by_digit():
    # "v" alone has no numeric core and must not be silently accepted.
    with pytest.raises(ValueError):
        parse_version("v")


def test_parse_version_prerelease_numeric_id():
    v = parse_version("1.0.0-rc1")
    assert v.core == (1, 0, 0)
    assert v.prerelease == ("rc", 1)
    assert v.is_prerelease()


def test_parse_version_prerelease_dotted_and_mixed():
    v = parse_version("1.0.0-alpha.2")
    assert v.prerelease == ("alpha", 2)


def test_parse_version_build_metadata_ignored():
    v = parse_version("1.2.3+build.7")
    assert v.core == (1, 2, 3)
    assert v.prerelease == ()


def test_parse_version_prerelease_with_build():
    v = parse_version("1.0.0-rc1+git123")
    assert v.core == (1, 0, 0)
    assert v.prerelease == ("rc", 1)


def test_parse_version_trims_whitespace():
    assert parse_version("  1.2.3  ").core == (1, 2, 3)


def test_parse_version_none_raises():
    with pytest.raises(ValueError):
        parse_version(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "latest", "abc", "x.y.z", "1.2.3.4.5.extra"])
def test_parse_version_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_version(bad)


# --- compare: LTS x.y vs x.y.z equivalence ----------------------------------


def test_compare_lts_two_part_equals_three_part():
    assert compare("1.2", "1.2.0") == 0
    assert compare("1.2.0", "1.2") == 0
    assert compare("1.2", "1.2.0.0") == 0


def test_compare_lts_two_part_below_patch():
    assert compare("1.2", "1.2.1") == -1
    assert compare("1.2.1", "1.2") == 1


def test_compare_lts_two_part_below_next_minor():
    assert compare("1.36", "1.37.0") == -1


def test_compare_lts_two_part_above_older_three_part():
    assert compare("1.37.0", "1.36.1") == 1


# --- compare: numeric core ordering -----------------------------------------


def test_compare_numeric_not_lexical():
    # Lexically "1.9" > "1.10"; numerically it is the opposite.
    assert compare("1.10", "1.9") == 1
    assert compare("1.9", "1.10") == -1


def test_compare_major_padding():
    assert compare("2", "1.9.9") == 1
    assert compare("1.9.9", "2") == -1


def test_compare_minor():
    assert compare("1.2", "1.3") == -1
    assert compare("1.3", "1.2") == 1


def test_compare_equal():
    assert compare("1.2.3", "1.2.3") == 0


def test_compare_v_prefix_equivalent():
    assert compare("v1.2.3", "1.2.3") == 0


def test_compare_build_metadata_ignored():
    assert compare("1.0.0+build", "1.0.0") == 0
    assert compare("1.0.0+aaa", "1.0.0+bbb") == 0


# --- compare: prerelease ranking --------------------------------------------


def test_compare_prerelease_below_stable():
    assert compare("1.0.0", "1.0.0-rc1") == 1
    assert compare("1.0.0-rc1", "1.0.0") == -1
    assert compare("1.0.0", "1.0.0-alpha") == 1


def test_compare_prerelease_numeric_order():
    assert compare("1.0.0-rc1", "1.0.0-rc2") == -1
    assert compare("1.0.0-rc2", "1.0.0-rc10") == -1
    assert compare("1.0.0-rc10", "1.0.0-rc2") == 1


def test_compare_prerelease_alpha_before_beta():
    assert compare("1.0.0-alpha", "1.0.0-beta") == -1


def test_compare_prerelease_dotted_order():
    assert compare("1.0.0-alpha.1", "1.0.0-beta.2") == -1


def test_compare_prerelease_more_ids_wins():
    # 1.0.0-alpha.1 vs 1.0.0-alpha  -> longer prerelease wins.
    assert compare("1.0.0-alpha", "1.0.0-alpha.1") == -1


def test_compare_numeric_prerelease_above_alpha():
    # SemVer: numeric identifiers outrank alphanumeric ones.
    assert compare("1.0.0-1", "1.0.0-alpha") == -1


# --- compare: algebraic properties ------------------------------------------


def test_compare_reflexive():
    for v in ["1", "1.2", "1.2.3", "1.2.3-rc1", "v9.9.9"]:
        assert compare(v, v) == 0


def test_compare_antisymmetric():
    samples = ["1", "1.2", "1.2.0", "1.2.3", "1.2.3-rc1", "1.0.0-rc10", "v2.0"]
    for a, b in itertools.permutations(samples, 2):
        assert compare(a, b) == -compare(b, a)


def test_compare_is_total_order():
    # Transitivity on a small chain.
    assert compare("1.0.0", "1.0.0-rc2") >= 0
    assert compare("1.0.0-rc2", "1.0.0-rc1") >= 0
    assert compare("1.0.0", "1.0.0-rc1") >= 0


# --- is_newer ---------------------------------------------------------------


def test_is_newer_true_when_strictly_newer():
    assert is_newer("1.36.1", "1.37.0") is True


def test_is_newer_false_when_equal_lts():
    assert is_newer("1.2", "1.2.0") is False


def test_is_newer_false_when_older():
    assert is_newer("1.37.0", "1.36.1") is False


def test_is_newer_false_when_equal():
    assert is_newer("1.2.3", "1.2.3") is False


def test_is_newer_matches_compare():
    samples = ["1", "1.2", "1.2.0", "1.2.3", "1.2.3-rc1", "v2.0"]
    for a, b in itertools.product(samples, repeat=2):
        assert is_newer(a, b) == (compare(b, a) > 0)


# --- classify / Drift -------------------------------------------------------


def test_classify_drift():
    assert classify("1.36.1", "1.37.0") is Drift.DRIFT


def test_classify_behind():
    assert classify("1.37.0", "1.36.1") is Drift.BEHIND


def test_classify_up_to_date_lts_equivalence():
    assert classify("1.2", "1.2.0") is Drift.UP_TO_DATE
    assert classify("1.2.0", "1.2") is Drift.UP_TO_DATE


def test_drift_str_is_human():
    assert str(Drift.DRIFT) == "drift"
    assert str(Drift.UP_TO_DATE) == "up-to-date"
    assert str(Drift.BEHIND) == "behind"


# --- ParsedVersion value object ---------------------------------------------


def test_parsed_version_is_frozen_and_hashable():
    v = parse_version("1.2.3")
    with pytest.raises(AttributeError):
        v.core = (1, 2, 4)  # type: ignore[misc]
    assert {v} == {parse_version("1.2.3")}


def test_parsed_version_core_lengths_preserved():
    assert parse_version("1.2").core == (1, 2)
    assert parse_version("1.2.3").core == (1, 2, 3)
