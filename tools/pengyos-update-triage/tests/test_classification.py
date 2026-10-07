"""Tests for the classification rule engine.

Covers ``pengyos_update_triage.classification``: the transparent rule chain
(advisory → ``security``/``ignore``; changelog → ``relevant``), the CVSS-derived
severity with its documented fallbacks, the suggested action + target, the
min-severity filter, and the full end-to-end classification of the committed
fixture pins / advisories / changelogs.

All inputs are self-contained (no network).
"""

from pathlib import Path

import pytest

from pengyos_update_triage.advisories import load_advisories
from pengyos_update_triage.changelog_scan import ChangelogFinding, scan_changelog
from pengyos_update_triage.changelogs import load_changelogs, parse_changelog_text
from pengyos_update_triage.classification import (
    ACTION_MONITOR,
    ACTION_NOOP,
    ACTION_UPDATE,
    CLASSIFY_IGNORE,
    CLASSIFY_RELEVANT,
    CLASSIFY_SECURITY,
    OUTCOME_FIRE,
    Classification,
    classify,
    classify_advisory_match,
    classify_changelog_finding,
    derive_severity,
    meets_min_severity,
)
from pengyos_update_triage.matching import match_advisory
from pengyos_update_triage.models import (
    Advisory,
    Affected,
    Pin,
    Range,
    RangeEvent,
    Severity,
)
from pengyos_update_triage.pins import load_pins

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ADV = FIXTURES / "advisories"
CHG = FIXTURES / "changelogs"
PINS_OK = FIXTURES / "pins" / "pins_ok.toml"


# --- builders ---------------------------------------------------------------


def make_pin(name="openssl", version="3.0.13", ecosystem="alpine",
             aliases=None, reachability="core"):
    return Pin(name=name, version=version, ecosystem=ecosystem,
               aliases=list(aliases or []), reachability=reachability)


def make_advisory(adv_id="TEST-1", vector=None, name="openssl",
                  ecosystem="alpine", events=None, versions=None, aliases=None):
    sev = [Severity(type="CVSS_V3", score=vector)] if vector else []
    return Advisory(
        id=adv_id,
        aliases=list(aliases or []),
        severities=sev,
        affected=[Affected(
            package_name=name,
            ecosystem=ecosystem,
            ranges=[Range("SEMVER", list(events or []))],
            versions=list(versions or []),
        )],
    )


def sec_finding(component="openssl", version="3.0.14", pin_version="3.0.13",
                entries=None):
    entries = entries if entries is not None else [
        "* Security fix: cert verification bypass (CVE-2024-0727)"
    ]
    return ChangelogFinding(
        component=component,
        version=version,
        kind="security",
        keywords=["cve", "bypass", "security"],
        entries=list(entries),
        pin_version=pin_version,
        rationale=f"{component} {version} security signal",
        evidence=[f"release {version}: entry '{entries[0]}' matches cve; references CVE-2024-0727"],
    )


def bump_finding(component="curl", version="8.9.0", pin_version="8.8.0"):
    return ChangelogFinding(
        component=component,
        version=version,
        kind="bump",
        keywords=[],
        entries=[],
        pin_version=pin_version,
        rationale=f"{component} {version} > {pin_version}",
        evidence=[f"pinned {pin_version}; latest listed in changelog {version}"],
    )


# --- advisory → security ----------------------------------------------------


def test_affected_advisory_is_security_with_update():
    adv = make_advisory(
        adv_id="GHSA-x", aliases=["CVE-2024-0727"],
        vector="CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H",  # 8.2 → high
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.severity == "high"
    assert c.severity_source == "cvss"
    assert c.suggested_action == ACTION_UPDATE
    assert c.target_version == "3.0.14"
    assert "SECURITY" in c.rationale
    # Exactly one rule fires — the one that decided the category (affected).
    assert c.fired_rules() == ["advisory-affected"]
    # The full chain records every evaluated rule, in order.
    assert [s.rule for s in c.rationale_chain] == [
        "advisory-applicable", "advisory-affected", "severity", "suggested-action"
    ]


def test_security_identifiers_carry_advisory_ids():
    adv = make_advisory(adv_id="GHSA-x", aliases=["CVE-2024-0727"],
                        vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.identifiers == ("GHSA-x", "CVE-2024-0727")
    assert c.source == "GHSA-x"
    assert c.evidence and all(e for e in c.evidence)


def test_security_last_affected_has_no_target_but_still_updates():
    # zlib-style: last_affected means no fixed release → no target, still update.
    adv = make_advisory(
        adv_id="CVE-z", name="zlib",
        vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L",
        events=[RangeEvent(introduced="0"), RangeEvent(last_affected="1.3.1")],
    )
    c = classify_advisory_match(match_advisory(make_pin(name="zlib", version="1.3.1"), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.target_version is None
    assert c.suggested_action == ACTION_UPDATE
    assert "patched build" in c.rationale
    assert "no fixed version" in c.rationale_chain[-1].detail


def test_security_multiple_fixes_picks_highest_target():
    adv = Advisory(
        id="M",
        severities=[Severity(type="CVSS_V3",
                             score="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")],
        affected=[Affected(
            package_name="openssl", ecosystem="alpine",
            ranges=[
                Range("SEMVER", [RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")]),
                Range("ECOSYSTEM", [RangeEvent(introduced="0"), RangeEvent(fixed="3.0.99")]),
            ],
        )],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.target_version == "3.0.99"  # highest by compare_versions


def test_security_without_cvss_is_unquantified_not_dropped():
    adv = make_advisory(events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY  # still security
    assert c.severity == "none"
    assert c.severity_source == "none"


def test_security_with_unsupported_cvss_scheme_is_unquantified():
    # A CVSS 2.0 vector is a recognised "cvss" scheme but not a version the
    # tool computes → an affected finding stays security, severity unquantified.
    adv = make_advisory(
        vector="CVSS:2.0/AV:N/AC:M/Au:N/C:C/I:C/A:C",  # CVSS 2.0, unsupported
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.severity == "none"
    assert c.severity_source == "unquantified"


def test_security_with_v40_vector_is_quantified_without_exploitability():
    # A CVSS v4.0 vector *is* quantified (base score) but has no v3-style
    # Exploitability sub-score → exploitability stays 0.0 / "none".
    adv = make_advisory(
        vector="CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N",
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.severity == "high"  # 8.2 -> high
    assert c.severity_source == "cvss"
    assert c.exploitability == 0.0
    assert c.exploitability_source == "none"


def test_security_with_raw_numeric_score_is_quantified():
    # A bare numeric score on a CVSS-typed severity entry is a base score.
    adv = make_advisory(
        vector="9.8",  # raw numeric score, type defaults to a cvss scheme
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.severity == "critical"
    assert c.severity_source == "cvss"
    assert c.exploitability == 0.0
    assert c.exploitability_source == "none"


# --- CVSS v4.0 full BTE severity (Threat + Environmental) ---------------------


def test_security_with_v40_full_bte_vector_uses_the_bte_score():
    # Same Base metrics score 9.2 (high), but the Threat + Environmental
    # tail (E:P + full mitigation) brings the BTE score to 5.5 → medium.
    # The classification must use the BTE score, not the Base score.
    adv = make_advisory(
        vector=("CVSS:4.0/AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/"
                "E:P/MAC:L/MAT:N/MVC:N/MVI:N/MVA:L"),
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.category == CLASSIFY_SECURITY
    assert c.severity == "medium"  # 5.5, not the 9.2 high of the Base metrics
    assert c.severity_source == "cvss"
    # The rationale names the applied group (CVSS-BTE) and the BTE score.
    details = [s.detail for s in c.rationale_chain if s.rule == "severity"]
    assert any("CVSS-BTE score 5.5" in d for d in details)
    assert c.exploitability == 0.0
    assert c.exploitability_source == "none"


def test_security_with_v40_threat_only_vector_is_cvss_bt():
    # E:P on the log4j base: Base 9.3, BT 8.9 — the nomenclature says BT.
    adv = make_advisory(
        vector="CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:P",
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.severity == "high"  # 8.9
    details = [s.detail for s in c.rationale_chain if s.rule == "severity"]
    assert any("CVSS-BT score 8.9" in d for d in details)


def test_security_with_v40_base_only_vector_is_cvss_b():
    adv = make_advisory(
        vector="CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")],
    )
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.severity == "critical"  # 9.3
    details = [s.detail for s in c.rationale_chain if s.rule == "severity"]
    assert any("CVSS-B score 9.3" in d for d in details)


def test_derive_severity_names_the_bte_group():
    level, source, detail = derive_severity(Advisory(
        id="A",
        severities=[Severity(type="CVSS_V4",
                             score=("CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/"
                                    "SC:N/SI:N/SA:N/E:A/MVC:N/MVI:N/MVA:N"))],
    ))
    # Full mitigation drives the BTE score to 0 → none, named CVSS-BTE.
    assert (level, source) == ("none", "cvss")
    assert "CVSS-BTE score 0.0" in detail


def test_rationale_is_a_single_line():
    adv = make_advisory(vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert "\n" not in c.rationale
    assert c.rationale.strip()


def test_every_rule_step_is_complete():
    adv = make_advisory(vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    assert c.rationale_chain  # non-empty
    for step in c.rationale_chain:
        assert step.rule
        assert step.outcome in ("fire", "pass", "skip")
        assert step.detail.strip()


def test_classification_is_immutable():
    adv = make_advisory(vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    c = classify_advisory_match(match_advisory(make_pin(), adv))
    with pytest.raises(Exception):
        c.category = "other"  # frozen dataclass


# --- advisory → ignore ------------------------------------------------------


def test_not_applicable_advisory_is_ignored():
    pin = make_pin(name="glibc", version="2.39")
    adv = make_advisory(adv_id="CVE-vim", name="vim",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="9.1")])
    c = classify_advisory_match(match_advisory(pin, adv))
    assert c.category == CLASSIFY_IGNORE
    assert c.suggested_action == ACTION_NOOP
    assert c.severity == "none"
    assert c.fired_rules() == ["advisory-applicable"]  # stopped at rule 1
    assert "not applicable" in c.rationale


def test_not_affected_version_is_ignored():
    # curl 8.8.0 vs a vuln fixed in 8.4.0 → already past it.
    pin = make_pin(name="curl", version="8.8.0")
    adv = make_advisory(adv_id="CVE-2023-38545", name="curl",
                        vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        events=[RangeEvent(introduced="7.9.0"), RangeEvent(fixed="8.4.0")])
    c = classify_advisory_match(match_advisory(pin, adv))
    assert c.category == CLASSIFY_IGNORE
    assert c.fired_rules() == ["advisory-affected"]  # passed applicability
    assert "not affected" in c.rationale or "NOT in the affected set" in c.rationale


def test_ecosystem_mismatch_is_ignored():
    pin = make_pin(name="python", version="3.12.4", ecosystem="pypi")
    adv = make_advisory(adv_id="CVE-py", name="python", ecosystem="alpine",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="99")])
    c = classify_advisory_match(match_advisory(pin, adv))
    assert c.category == CLASSIFY_IGNORE
    assert c.fired_rules() == ["advisory-applicable"]


# --- changelog → relevant ---------------------------------------------------


def test_changelog_security_finding_is_relevant():
    c = classify_changelog_finding(sec_finding())
    assert c.category == CLASSIFY_RELEVANT
    assert c.severity == "none"
    assert c.severity_source == "none"
    assert c.suggested_action == ACTION_MONITOR
    assert c.target_version is None
    assert c.identifiers == ("CVE-2024-0727",)
    assert c.component == "openssl"
    assert c.pin_version == "3.0.13"


def test_changelog_bump_finding_is_relevant_with_target():
    c = classify_changelog_finding(bump_finding())
    assert c.category == CLASSIFY_RELEVANT
    assert c.suggested_action == ACTION_MONITOR
    assert c.target_version == "8.9.0"
    assert c.identifiers == ()  # a bump references no CVE ids
    assert "newer release" in c.rationale


def test_changelog_security_with_corroborating_severity():
    c = classify_changelog_finding(sec_finding(), corroborating_severity="high")
    assert c.category == CLASSIFY_RELEVANT
    assert c.severity == "high"
    assert c.severity_source == "advisory-corroborated"


def test_changelog_finding_rule_chain_includes_signal_and_action():
    c = classify_changelog_finding(sec_finding())
    rules = [step.rule for step in c.rationale_chain]
    assert "changelog-signal" in rules
    assert "changelog-corroboration" in rules
    assert "suggested-action" in rules
    # The category is decided by the changelog-signal rule.
    assert c.fired_rules() == ["changelog-signal"]


def test_changelog_ids_extracted_from_entries():
    f = sec_finding(entries=[
        "- Fix heap overflow (CVE-2023-4911)",
        "- More hardening for the regex engine (CVE-2022-0001)",
    ])
    c = classify_changelog_finding(f)
    assert c.identifiers == ("CVE-2023-4911", "CVE-2022-0001")


# --- derive_severity --------------------------------------------------------


def test_derive_severity_prefers_first_quantifiable_vector():
    adv = Advisory(
        id="A",
        severities=[
            Severity(type="CVSS_V3", score="CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),  # unquantified
            Severity(type="CVSS_V3", score="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),  # 9.8
        ],
    )
    severity, source, detail = derive_severity(adv)
    assert severity == "critical"
    assert source == "cvss"
    assert "9.8" in detail


def test_derive_severity_none_when_no_cvss():
    severity, source, _ = derive_severity(Advisory(id="A"))
    assert severity == "none" and source == "none"


# --- classify() orchestrator ------------------------------------------------


def test_classify_splits_into_three_lists():
    pins = [make_pin(name="openssl", version="3.0.13"),
            make_pin(name="curl", version="8.8.0")]
    affected = make_advisory(adv_id="ADV-SEC", name="openssl",
                             vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                             events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    notaff = make_advisory(adv_id="ADV-NA", name="openssl",
                           events=[RangeEvent(introduced="9"), RangeEvent(fixed="99")])
    from pengyos_update_triage.matching import match_all
    matches = match_all(pins, [affected, notaff])
    findings = [sec_finding(), bump_finding()]
    security, relevant, ignore = classify(matches, findings)
    assert [c.category for c in security] == [CLASSIFY_SECURITY]
    assert [c.id for c in security] == ["advisory:ADV-SEC:openssl"]
    assert all(c.category == CLASSIFY_RELEVANT for c in relevant)
    assert len(relevant) == 2
    assert all(c.category == CLASSIFY_IGNORE for c in ignore)
    # 2 pins x 2 advisories = 4 matches: 1 security (openssl/ADV-SEC), the other
    # 3 are ignore (ADV-SEC vs curl, ADV-NA vs openssl, ADV-NA vs curl). The
    # 2 changelog findings are separate (relevant).
    assert len(ignore) == 3
    assert len(security) + len(ignore) == 4


def test_classify_is_deterministic():
    pins = [make_pin(name="openssl", version="3.0.13")]
    adv = make_advisory(vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        events=[RangeEvent(introduced="0"), RangeEvent(fixed="3.0.14")])
    from pengyos_update_triage.matching import match_all
    matches = match_all(pins, [adv])
    findings = [sec_finding()]
    a = classify(matches, findings)
    b = classify(matches, findings)
    assert [c.id for c in a[0]] == [c.id for c in b[0]]
    assert [c.rationale for c in a[1]] == [c.rationale for c in b[1]]


# --- min-severity filter ----------------------------------------------------


@pytest.mark.parametrize(
    "severity, min_level, expected",
    [
        ("low", "low", True),
        ("low", "medium", False),
        ("medium", "medium", True),
        ("medium", "high", False),
        ("high", "high", True),
        ("high", "critical", False),
        ("critical", "critical", True),
        ("none", "critical", True),   # unquantified always passes
        (None, "critical", True),      # unquantified always passes
        ("medium", "low", True),
    ],
)
def test_meets_min_severity(severity, min_level, expected):
    assert meets_min_severity(severity, min_level) == expected


def test_min_severity_defaults_to_low():
    assert meets_min_severity("low") is True
    assert meets_min_severity("critical") is True


# --- end-to-end over the committed fixtures ---------------------------------


def _fixture_classify():
    pins = load_pins(PINS_OK).pins
    advisories = load_advisories(ADV)
    changelogs = load_changelogs(CHG)
    from pengyos_update_triage.matching import match_all
    from pengyos_update_triage.changelog_scan import scan_changelogs
    matches = match_all(pins, advisories)
    findings = scan_changelogs(changelogs, pins)
    return classify(matches, findings)


def test_fixture_security_classifications():
    security, _, _ = _fixture_classify()
    by_component = {c.component: c for c in security}
    assert set(by_component) == {"openssl", "glibc", "curl", "zlib"}
    assert by_component["curl"].severity == "critical"
    assert by_component["curl"].target_version == "8.9.0"
    assert by_component["openssl"].severity == "high"
    assert by_component["openssl"].target_version == "3.0.14"
    # Real CVE-2023-4911 vector (AV:L/AC:L/PR:L/...) scores 7.8 high.
    assert by_component["glibc"].severity == "high"
    assert by_component["glibc"].target_version == "2.40"
    assert by_component["zlib"].severity == "medium"
    assert by_component["zlib"].target_version is None
    for c in security:
        assert c.suggested_action == ACTION_UPDATE
        assert c.rationale.startswith("SECURITY")


def test_fixture_relevant_are_the_changelog_findings():
    _, relevant, _ = _fixture_classify()
    # 2 glibc + 1 openssl + 2 curl + 1 zlib = 6 security sections, plus bumps
    # for glibc/openssl/curl (3) = 9 relevant changelog classifications.
    assert len(relevant) == 9
    assert all(c.category == CLASSIFY_RELEVANT for c in relevant)
    assert all(c.suggested_action == ACTION_MONITOR for c in relevant)
    bump_targets = {c.component: c.target_version for c in relevant
                    if c.target_version is not None}
    assert bump_targets == {"glibc": "2.40.0", "openssl": "3.0.14", "curl": "8.9.0"}


def test_fixture_ignore_is_the_complement_of_security():
    security, _, ignore = _fixture_classify()
    # 6 advisories x 6 pins = 36 matches; 4 are security, the rest ignore.
    assert len(security) == 4
    assert len(ignore) == 32
    assert all(c.category == CLASSIFY_IGNORE for c in ignore)
    assert all(c.suggested_action == ACTION_NOOP for c in ignore)
    # The already-fixed curl CVE is explicitly ignored (not affected).
    reasons = {c.source for c in ignore}
    assert "CVE-2023-38545" in reasons


def test_every_classification_carries_rationale_and_evidence():
    security, relevant, ignore = _fixture_classify()
    for c in [*security, *relevant, *ignore]:
        assert c.rationale.strip()
        assert c.rationale_chain and all(s.detail.strip() for s in c.rationale_chain)
        assert c.evidence and all(e.strip() for e in c.evidence)
        assert isinstance(c, Classification)


def test_security_classifications_carry_source_ids():
    security, _, _ = _fixture_classify()
    for c in security:
        assert c.identifiers  # every security proposal carries source ids
        assert c.source
