"""Tests for advisory ↔ pin matching.

Covers ``pengyos_update_triage.matching``:
* the dotted-version comparator (numeric components, leading ``v``,
  pre-release ordering, build metadata, total ordering on odd input);
* OSV range-event semantics (introduced / fixed / last_affected / vulnerable,
  unbounded intervals, interval splitting on repeated ``introduced``);
* name/alias matching, the ecosystem compatibility rule, explicit
  ``versions[]`` listings;
* end-to-end cross-matching of the committed fixture pins against the
  committed advisory fixtures (affected / not-affected / not-pinned cases,
  per the spec's verification list).
"""

from pathlib import Path

import pytest

from pengyos_update_triage.advisories import load_advisories
from pengyos_update_triage.matching import (
    AdvisoryMatch,
    compare_versions,
    match_advisory,
    match_all,
    version_in_ranges,
)
from pengyos_update_triage.models import Advisory, Affected, Pin, Range, RangeEvent
from pengyos_update_triage.pins import load_pins

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ADV = FIXTURES / "advisories"
PINS_OK = FIXTURES / "pins" / "pins_ok.toml"


def make_advisory(
    events=None,
    name="openssl",
    ecosystem="alpine",
    versions=None,
    adv_id="TEST-1",
):
    return Advisory(
        id=adv_id,
        affected=[
            Affected(
                package_name=name,
                ecosystem=ecosystem,
                ranges=[Range(type="SEMVER", events=list(events or []))],
                versions=list(versions or []),
            )
        ],
    )


def make_pin(name="openssl", version="3.0.13", ecosystem="alpine", aliases=None):
    return Pin(name=name, version=version, ecosystem=ecosystem, aliases=list(aliases or []))


# --- compare_versions -------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("1.0.0", "1.0.0", 0),
        ("1.0", "1.0.0", 0),          # missing component == 0
        ("1.0", "1.0.1", -1),
        ("1.1", "1.0.9", 1),
        ("9", "10", -1),              # numeric, not lexicographic
        ("9.1.0444", "9.1.444", 0),   # leading zeros
        ("v1.2.3", "1.2.3", 0),       # leading v ignored
        ("V2.0", "2.0", 0),
        ("1.0-rc1", "1.0", -1),       # pre-release sorts below release
        ("1.0", "1.0-rc1", 1),
        ("1.0-rc1", "1.0.1", -1),
        ("1.0-alpha", "1.0-beta", -1),
        ("1.0-1", "1.0.1", -1),       # numeric pre-release still below
        ("1.0+build5", "1.0", 0),     # build metadata ignored
        ("0", "0", 0),
        ("1.3.1", "1.3.2", -1),
    ],
)
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected
    assert compare_versions(b, a) == -expected


def test_compare_versions_total_on_odd_input():
    # Never raises: anything left over compares deterministically.
    assert compare_versions("1.0.3a", "1.0.3b") < 0
    assert compare_versions("1.0.3a", "1.0.3") < 0
    assert compare_versions("", "1") < 0
    assert compare_versions("garbage", "garbage") == 0


# --- range evaluation --------------------------------------------------------


def test_range_fixed_exclusive():
    rng = [RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")]
    assert version_in_ranges("3.0.13", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("3.0.14", [Range("SEMVER", rng)]) is None
    assert version_in_ranges("3.0.15", [Range("SEMVER", rng)]) is None


def test_range_last_affected_inclusive():
    rng = [RangeEvent(introduced="0"), RangeEvent(last_affected="1.3.1")]
    assert version_in_ranges("1.3.1", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("1.3.2", [Range("SEMVER", rng)]) is None
    assert version_in_ranges("1.2", [Range("SEMVER", rng)]) is not None


def test_range_no_introduced_is_unbounded_below():
    rng = [RangeEvent(fixed="1.0.0")]
    assert version_in_ranges("0.9", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("1.0.0", [Range("SEMVER", rng)]) is None


def test_range_open_ended_after_introduced():
    rng = [RangeEvent(introduced="2.0.0")]
    assert version_in_ranges("1.9", [Range("SEMVER", rng)]) is None
    assert version_in_ranges("2.0.0", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("9.9.9", [Range("SEMVER", rng)]) is not None


def test_range_vulnerable_point():
    rng = [RangeEvent(vulnerable="2.5.1")]
    assert version_in_ranges("2.5.1", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("2.5.0", [Range("SEMVER", rng)]) is None
    assert version_in_ranges("2.5.2", [Range("SEMVER", rng)]) is None


def test_range_gap_between_affected_segments():
    # A gap between affected segments is expressed by terminating the first
    # interval (fixed) before starting the next (introduced):
    # [1.0.0, 2.0.0) ∪ [3.0.0, 4.0.0)
    rng = [
        RangeEvent(introduced="1.0.0"),
        RangeEvent(fixed="2.0.0"),
        RangeEvent(introduced="3.0.0"),
        RangeEvent(fixed="4.0.0"),
    ]
    assert version_in_ranges("1.5", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("2.5", [Range("SEMVER", rng)]) is None
    assert version_in_ranges("3.5", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("4.0", [Range("SEMVER", rng)]) is None


def test_range_repeated_introduced_closes_and_reopens():
    # Per OSV, a repeated `introduced` closes the open interval exclusive of
    # the new value and reopens it there, so 1.5 and 2.5 are both vulnerable
    # but 3.0 (the `fixed` boundary) is not.
    rng = [
        RangeEvent(introduced="1.0.0"),
        RangeEvent(introduced="2.0.0"),
        RangeEvent(fixed="3.0.0"),
    ]
    assert version_in_ranges("1.5", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("2.5", [Range("SEMVER", rng)]) is not None
    assert version_in_ranges("3.0", [Range("SEMVER", rng)]) is None


def test_version_in_ranges_returns_none_when_absent():
    assert version_in_ranges("9.9.9", []) is None
    assert version_in_ranges(
        "9.9.9", [Range("SEMVER", [RangeEvent(fixed="1.0.0")])]
    ) is None


def test_range_evidence_is_human_readable():
    rng = [RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")]
    evidence = version_in_ranges("3.0.13", [Range("SEMVER", rng)])
    assert "range SEMVER" in evidence
    assert "introduced 0" in evidence
    assert "fixed 3.0.14" in evidence
    assert "3.0.13" in evidence


# --- name / alias / ecosystem matching ---------------------------------------


def test_match_by_alias():
    pin = make_pin(name="glibc", version="2.39", aliases=["libc"])
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="2.40")],
                        name="libc")
    m = match_advisory(pin, adv)
    assert m.matched and m.affected
    assert m.package_name == "libc"
    assert "alias" in m.reason() or "alias" in "; ".join(m.evidence)


def test_ecosystem_mismatch_skips_entry():
    pin = make_pin(name="python", version="3.12.4", ecosystem="pypi")
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="99")],
                        name="python", ecosystem="alpine")
    m = match_advisory(pin, adv)
    assert not m.matched and not m.affected
    assert "ecosystem" in "; ".join(m.evidence)


def test_generic_pin_matches_any_ecosystem():
    pin = make_pin(name="curl", version="8.8.0", ecosystem="generic")
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="9")],
                        name="curl", ecosystem="alpine")
    m = match_advisory(pin, adv)
    assert m.matched and m.affected


def test_advisory_without_ecosystem_matches_specific_pin():
    pin = make_pin(name="curl", version="8.8.0", ecosystem="alpine")
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="9")],
                        name="curl", ecosystem="")
    assert match_advisory(pin, adv).affected


def test_ecosystem_compare_is_case_insensitive():
    pin = make_pin(name="curl", version="8.8.0", ecosystem="Alpine")
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="9")],
                        name="curl", ecosystem="alpine")
    assert match_advisory(pin, adv).affected


def test_explicit_versions_list_exact_match():
    adv = make_advisory(
        events=[],
        versions=["2.39", "2.38"],
        name="glibc",
    )
    pin = make_pin(name="glibc", version="2.39")
    m = match_advisory(pin, adv)
    assert m.matched and m.affected
    assert any("explicitly listed" in e for e in m.evidence)


def test_not_affected_name_matched_keeps_evidence():
    adv = make_advisory(
        events=[RangeEvent(introduced="7.9.0"), RangeEvent(fixed="8.4.0")],
        name="curl",
    )
    pin = make_pin(name="curl", version="8.8.0")
    m = match_advisory(pin, adv)
    assert m.matched and not m.affected
    joined = "; ".join(m.evidence)
    assert "outside the affected set" in joined
    assert "[7.9.0, 8.4.0)" in joined  # the checked interval, described


def test_not_applicable_evidence():
    pin = make_pin(name="make", version="4.4.1")
    adv = make_advisory(name="vim")
    m = match_advisory(pin, adv)
    assert not m.matched and not m.affected
    assert "does not apply" in m.reason()


def test_suggested_fixes_collected_deduped():
    adv = Advisory(
        id="T",
        affected=[
            Affected(
                package_name="openssl",
                ecosystem="alpine",
                ranges=[
                    Range("SEMVER", [
                        RangeEvent(introduced="0"),
                        RangeEvent(fixed="3.0.14"),
                    ]),
                    Range("ECOSYSTEM", [
                        RangeEvent(introduced="0"),
                        RangeEvent(fixed="3.0.14"),
                        RangeEvent(fixed="3.0.99"),
                    ]),
                ],
            )
        ],
    )
    m = match_advisory(make_pin(), adv)
    assert m.affected
    assert m.suggested_fixes == ["3.0.14", "3.0.99"]


def test_match_identifiers_carry_all_source_ids():
    adv = make_advisory(adv_id="GHSA-x")
    adv = Advisory(id="GHSA-x", aliases=["CVE-2024-0727"],
                   affected=adv.affected)
    m = match_advisory(make_pin(), adv)
    assert m.identifiers == ["GHSA-x", "CVE-2024-0727"]


def test_reason_is_single_human_line():
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    m = match_advisory(make_pin(), adv)
    reason = m.reason()
    assert "\n" not in reason
    assert "affected by" in reason
    assert "TEST-1" in reason


# --- end-to-end over the committed fixtures ----------------------------------


def _fixture_matches():
    pins = load_pins(PINS_OK).pins
    advisories = load_advisories(ADV)
    return pins, advisories, match_all(pins, advisories)


def test_fixture_openssl_affected():
    pins, advisories, matches = _fixture_matches()
    by_id = {a.id: a for a in advisories}
    pin = next(p for p in pins if p.name == "openssl")
    m = match_advisory(pin, by_id["CVE-2024-4741"])
    assert m.matched and m.affected
    assert m.suggested_fixes == ["3.0.14"]
    assert "3.0.13" in m.reason()


def test_fixture_glibc_affected_via_range_and_versions():
    pins, advisories, matches = _fixture_matches()
    by_id = {a.id: a for a in advisories}
    pin = next(p for p in pins if p.name == "glibc")
    m = match_advisory(pin, by_id["CVE-2023-4911"])
    assert m.affected
    joined = "; ".join(m.evidence)
    # glibc 2.39 is both explicitly listed AND inside the range.
    assert "explicitly listed" in joined
    assert "range" in joined
    assert "GHSA-m77w-6vjw-wh2f" in m.reason()


def test_fixture_curl_one_affected_one_not():
    pins, advisories, matches = _fixture_matches()
    by_id = {a.id: a for a in advisories}
    pin = next(p for p in pins if p.name == "curl")
    m_aff = match_advisory(pin, by_id["CVE-2024-2392"])
    assert m_aff.matched and m_aff.affected
    assert m_aff.suggested_fixes == ["8.9.0"]
    m_not = match_advisory(pin, by_id["CVE-2023-38545"])
    assert m_not.matched and not m_not.affected
    assert "outside the affected set" in "; ".join(m_not.evidence)


def test_fixture_zlib_last_affected_inclusive():
    pins, advisories, matches = _fixture_matches()
    by_id = {a.id: a for a in advisories}
    pin = next(p for p in pins if p.name == "zlib")
    m = match_advisory(pin, by_id["CVE-2023-45853"])
    assert m.matched and m.affected
    # last_affected semantics: no fixed release exists → no suggested fix.
    assert m.suggested_fixes == []
    assert "last_affected 1.3.1" in "; ".join(m.evidence)


def test_fixture_non_pinned_component_not_applicable():
    pins, advisories, matches = _fixture_matches()
    by_id = {a.id: a for a in advisories}
    vim = by_id["CVE-2019-20079"]
    for pin in pins:
        m = match_advisory(pin, vim)
        assert not m.matched and not m.affected, pin.name


def test_match_all_deterministic_and_complete():
    pins, advisories, matches = _fixture_matches()
    assert len(matches) == len(pins) * len(advisories)
    # Deterministic order: advisories outer, pins inner.
    expected_order = [(a.id, p.name) for a in advisories for p in pins]
    actual_order = [(m.advisory.id, m.pin.name) for m in matches]
    assert actual_order == expected_order
    assert all(isinstance(m, AdvisoryMatch) for m in matches)


def test_match_all_affected_counts_on_fixture_set():
    pins, advisories, matches = _fixture_matches()
    affected_pairs = {(m.advisory.id, m.pin.name) for m in matches if m.affected}
    assert affected_pairs == {
        ("CVE-2024-4741", "openssl"),
        ("CVE-2023-4911", "glibc"),
        ("CVE-2024-2392", "curl"),
        ("CVE-2023-45853", "zlib"),
    }


def test_every_match_has_nonempty_evidence():
    _, _, matches = _fixture_matches()
    for m in matches:
        assert m.evidence, m
        assert m.reason()
