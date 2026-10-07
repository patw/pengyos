"""Tests for the canonical input data model (``pengyos_update_triage.models``)."""

from pengyos_update_triage.models import (
    Advisory,
    DEFAULT_REACHABILITY,
    REACHABILITY_LEVELS,
    SEVERITY_LEVELS,
    SEVERITY_NONE,
    Affected,
    Changelog,
    ChangelogSection,
    Pin,
    Range,
    RangeEvent,
    Severity,
    reachability_rank,
    severity_rank,
)


# --- reachability ordering --------------------------------------------------


def test_reachability_levels_are_ordered_and_disjoint():
    # opt < std < core, from least to most reachable.
    assert REACHABILITY_LEVELS == ("opt", "std", "core")
    assert len(set(REACHABILITY_LEVELS)) == len(REACHABILITY_LEVELS)


def test_reachability_rank_orders_low_to_high():
    assert reachability_rank("opt") < reachability_rank("std")
    assert reachability_rank("std") < reachability_rank("core")
    assert reachability_rank("core") == len(REACHABILITY_LEVELS) - 1


def test_reachability_rank_unknown_degrades_to_lowest():
    # An unfamiliar label must not crash; it degrades to the lowest rank.
    assert reachability_rank("never-heard-of") == 0


def test_default_reachability_is_a_known_level():
    assert DEFAULT_REACHABILITY in REACHABILITY_LEVELS


# --- severity ordering ------------------------------------------------------


def test_severity_levels_are_ordered_and_disjoint():
    # low < medium < high < critical.
    assert SEVERITY_LEVELS == ("low", "medium", "high", "critical")
    assert len(set(SEVERITY_LEVELS)) == len(SEVERITY_LEVELS)


def test_severity_rank_orders_low_to_high():
    assert severity_rank("low") < severity_rank("medium")
    assert severity_rank("medium") < severity_rank("high")
    assert severity_rank("high") < severity_rank("critical")
    assert severity_rank("critical") == len(SEVERITY_LEVELS) - 1


def test_severity_none_sorts_below_low():
    # An unquantified finding sorts below the lowest level (never over-claimed),
    # and the label / None / an unknown value all agree.
    assert severity_rank(SEVERITY_NONE) == -1
    assert severity_rank(None) == -1
    assert severity_rank("apocalyptic") == -1
    assert severity_rank(SEVERITY_NONE) < severity_rank("low")


# --- Pin --------------------------------------------------------------------


def test_pin_matches_primary_name_and_aliases():
    pin = Pin(name="openssl", version="3.0.13", aliases=["libssl", "libcrypto"])
    assert pin.matches_name("openssl")
    assert pin.matches_name("libssl")
    assert not pin.matches_name("libfoo")


def test_pin_defaults_are_sane():
    pin = Pin(name="zlib", version="1.3.1")
    assert pin.ecosystem == "generic"
    assert pin.aliases == []
    assert pin.reachability == DEFAULT_REACHABILITY
    assert pin.note == ""
    assert pin.line is None


def test_pin_is_immutable():
    pin = Pin(name="zlib", version="1.3.1")
    # frozen=True: mutating should raise.
    try:
        pin.version = "2.0"
    except Exception:
        return
    raise AssertionError("Pin should be frozen (immutable)")


# --- Advisory ---------------------------------------------------------------


def test_advisory_all_identifiers_includes_id_and_aliases():
    adv = Advisory(id="GHSA-1", aliases=["CVE-2024-1", "GHSA-1", "CVE-2024-2"])
    ids = adv.all_identifiers()
    assert ids[0] == "GHSA-1"
    assert "CVE-2024-1" in ids
    assert "CVE-2024-2" in ids
    # the duplicate self-reference is not repeated.
    assert ids.count("GHSA-1") == 1


def test_advisory_defaults():
    adv = Advisory(id="CVE-1")
    assert adv.severities == []
    assert adv.affected == []
    assert adv.references == []
    assert adv.source_file is None
    assert adv.all_identifiers() == ["CVE-1"]


def test_range_event_defaults_all_none():
    ev = RangeEvent()
    assert ev.introduced is None
    assert ev.fixed is None
    assert ev.last_affected is None
    assert ev.vulnerable is None


def test_affected_default_fields():
    aff = Affected(package_name="glibc", ecosystem="alpine")
    assert aff.ranges == []
    assert aff.versions == []
    assert aff.database_specific == {}


def test_severity_carries_scheme_and_score():
    sev = Severity(type="CVSS_V3", score="CVSS:3.1/AV:N/AC:L")
    assert sev.type == "CVSS_V3"
    assert "CVSS:3.1" in sev.score


def test_range_holds_events():
    r = Range(type="SEMVER", events=[RangeEvent(introduced="0"), RangeEvent(fixed="2.40")])
    assert r.type == "SEMVER"
    assert len(r.events) == 2
    assert r.events[1].fixed == "2.40"


# --- Changelog --------------------------------------------------------------


def test_changelog_section_and_changelog():
    sec = ChangelogSection(version="2.40.0", header_line="## 2.40.0", entries=["fix CVE-1"])
    chg = Changelog(component="glibc", path="/x/glibc.txt", sections=[sec])
    assert chg.component == "glibc"
    assert chg.sections[0].version == "2.40.0"
    assert chg.sections[0].entries == ["fix CVE-1"]
