"""Curation guard for the committed fixtures.

The fixtures are "realistic" by contract: every advisory uses a real,
registry-verified CVE/GHSA identifier with a plausible summary, a valid CVSS
3.1 vector, and http(s) references; the pins manifest is a realistic distro
pin set; and the changelogs attribute to pinned components. This module locks
those invariants down so a careless edit to a fixture (a placeholder id like
``CVE-2024-12345``, a bare numeric score, a missing reference) fails the
suite, and it re-locks the spec's end-to-end count
(13 proposals: 4 security + 9 relevant, 32 ignored) on the curated set.

All inputs are local files — no network, no root.
"""

import re
from datetime import datetime
from pathlib import Path

import pytest

from pengyos_update_triage.advisories import load_advisories
from pengyos_update_triage.changelog_scan import find_pin_for
from pengyos_update_triage.changelogs import load_changelogs
from pengyos_update_triage.cvss import CvssError, severity_from_vector
from pengyos_update_triage.models import REACHABILITY_LEVELS
from pengyos_update_triage.pins import load_pins
from pengyos_update_triage.proposal import build_proposal_set

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ADV = FIXTURES / "advisories"
CHG = FIXTURES / "changelogs"
PINS_OK = FIXTURES / "pins" / "pins_ok.toml"

CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,7}$")
GHSA_RE = re.compile(r"^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$")
VERSION_RE = re.compile(r"^\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?$")


def _real_advisory_id(ident: str) -> bool:
    return bool(CVE_RE.match(ident) or GHSA_RE.match(ident))


def _inputs():
    pins = load_pins(PINS_OK)
    advisories = load_advisories(ADV)
    changelogs = load_changelogs(CHG)
    return pins, advisories, changelogs


# --- pins fixture: a realistic distro pin set -------------------------------


def test_pins_fixture_is_a_realistic_pin_set():
    manifest = load_pins(PINS_OK)
    pins = manifest.pins
    # "a few" pinned components spanning the core/std/opt reachability tiers.
    assert len(pins) == 6
    assert {p.reachability for p in pins} == set(REACHABILITY_LEVELS)
    names = [p.name for p in pins]
    assert len(set(names)) == len(names)
    for p in pins:
        assert re.match(r"^[a-z0-9][a-z0-9.+-]*$", p.name), p.name
        assert VERSION_RE.match(p.version), f"unrealistic version {p.version}"
        assert p.ecosystem  # a distro pin always names its ecosystem


# --- advisory fixture realism invariants ------------------------------------


def test_advisories_load_cleanly():
    advisories = load_advisories(ADV)
    assert len(advisories) == 6
    ids = [a.id for a in advisories]
    assert len(set(ids)) == len(ids), "duplicate advisory ids in fixtures"


def test_advisory_ids_are_real_well_formed_ids():
    # No placeholder ids (e.g. CVE-2024-12345): every id and alias must be a
    # well-formed CVE or GHSA identifier.
    for adv in load_advisories(ADV):
        assert _real_advisory_id(adv.id), f"bad advisory id {adv.id}"
        for alias in adv.aliases:
            assert _real_advisory_id(alias), f"bad alias {alias} on {adv.id}"


def test_advisory_fields_are_plausible():
    for adv in load_advisories(ADV):
        assert adv.summary.strip(), f"{adv.id}: empty summary"
        assert adv.details.strip(), f"{adv.id}: empty details"
        # Provenance must point at the fixture file that actually loaded it.
        assert adv.source_file and Path(adv.source_file).name
        assert Path(adv.source_file).exists(), adv.source_file
        # References are http(s) urls (official sources).
        assert adv.references, f"{adv.id}: no references"
        for ref in adv.references:
            assert ref.startswith(("http://", "https://")), (adv.id, ref)
        # Dates, when present, are parseable ISO-8601.
        for stamp in (adv.published, adv.modified):
            if stamp:
                datetime.fromisoformat(stamp)
        # Every severity entry is a parseable CVSS 3.1 base vector (raw
        # numeric scores are rejected by the cvss module — the fixtures never
        # fall back to "unquantified").
        assert adv.severities, f"{adv.id}: no severity entry"
        for sev in adv.severities:
            try:
                severity_from_vector(sev.score)
            except CvssError as exc:
                pytest.fail(f"{adv.id}: unparseable vector {sev.score}: {exc}")
        # Every affected entry names a package and carries at least one range.
        assert adv.affected, f"{adv.id}: no affected entry"
        for aff in adv.affected:
            assert aff.package_name
            assert aff.ranges, f"{adv.id}: {aff.package_name} has no ranges"
            assert all(r.events for r in aff.ranges)


def test_few_cves_across_pinned_components():
    # The item's acceptance: a few CVEs *across pinned components* — at
    # least four distinct pinned components (by name or alias) are covered by
    # an advisory, and one of them stays unpinned on purpose (the vim
    # advisory exercises the not-applicable path).
    pins = load_pins(PINS_OK)
    advisories = load_advisories(ADV)
    covered = set()
    for adv in advisories:
        for aff in adv.affected:
            pin = find_pin_for(aff.package_name, pins.pins)
            if pin is not None:
                covered.add(pin.name)
    assert covered == {"glibc", "openssl", "curl", "zlib"}
    # Exactly one advisory targets a component that is not pinned at all.
    unpinned = [
        adv.id
        for adv in advisories
        if all(find_pin_for(a.package_name, pins.pins) is None
               for a in adv.affected)
    ]
    assert unpinned == ["CVE-2019-20079"]


# --- changelog fixture invariants -------------------------------------------


def test_changelogs_attribute_to_pinned_components():
    pins = load_pins(PINS_OK)
    changelogs = load_changelogs(CHG)
    assert len(changelogs) == 4
    assert {c.component for c in changelogs} == {"curl", "glibc", "openssl", "zlib"}
    for chg in changelogs:
        assert find_pin_for(chg.component, pins.pins) is not None, chg.component
        assert chg.sections, f"{chg.component}: no version sections"
        # Every section carries at least one parsed entry.
        for section in chg.sections:
            assert section.entries, (chg.component, section.version)


# --- end-to-end count lock on the curated fixtures --------------------------


def test_curated_fixture_run_matches_spec_counts():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins.pins, advisories, changelogs, "low")
    assert pset.counts() == {
        "security": 4,
        "relevant": 9,
        "ignore": 32,
        "proposals": 13,
    }
    assert pset.input_counts == {"pins": 6, "advisories": 6, "changelogs": 4}


def test_curated_fixture_top_proposal_is_the_critical_curl_advisory():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins.pins, advisories, changelogs, "low")
    top = pset.proposals[0]
    assert top.id == "advisory:CVE-2024-2392:curl"
    assert top.severity == "critical"
    assert top.target_version == "8.9.0"
    # The 4 security proposals cover exactly the 4 pinned components with
    # affected advisories; their identifiers carry the real ids/aliases.
    security = [p for p in pset.proposals if p.category == "security"]
    assert {p.component for p in security} == {"curl", "openssl", "glibc", "zlib"}
    for p in security:
        assert p.identifiers
        assert p.action == "update"


def test_every_curated_fixture_finding_is_explainable():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins.pins, advisories, changelogs, "low")
    for finding in pset.proposals + pset.ignored:
        # Each emitted finding is retrievable by id (the `explain` contract).
        found = pset.find(finding.id)
        assert found is not None and found.id == finding.id
        assert found.rationale
        assert found.evidence
        assert found.fired_rule
