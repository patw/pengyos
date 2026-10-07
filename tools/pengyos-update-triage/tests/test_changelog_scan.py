"""Tests for changelog scanning.

Covers ``pengyos_update_triage.changelog_scan``: the security-keyword table
(true and *false* positives on realistic lines), per-section security
findings, CVE/GHSA id evidence, version-bump detection against pins,
attribution by name/alias, determinism, and the committed fixtures
end-to-end (load pins + changelogs → scan).
"""

from pathlib import Path

import pytest

from pengyos_update_triage.changelogs import load_changelogs, parse_changelog_text
from pengyos_update_triage.changelog_scan import (
    FINDING_KINDS,
    KIND_BUMP,
    KIND_SECURITY,
    ChangelogFinding,
    find_pin_for,
    scan_changelog,
    scan_changelogs,
    scan_entry,
)
from pengyos_update_triage.models import Pin
from pengyos_update_triage.pins import load_pins

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHG = FIXTURES / "changelogs"
PINS_OK = FIXTURES / "pins" / "pins_ok.toml"


def _chg(text: str, component: str = "comp") -> "object":
    return parse_changelog_text(text, default_component=component)


# --- keyword table: true positives ------------------------------------------


@pytest.mark.parametrize(
    "line,keyword",
    [
        ("Fix CVE-2024-0727", "cve"),
        ("cve-2023-1234 fixed", "cve"),  # case-insensitive
        ("GHSA-8h47-92pf-6jhr patched", "ghsa"),
        ("buffer overflow in parser", "buffer overflow"),
        ("fix buffer overread in inflate", "buffer overflow"),
        ("heap overflow in iconv", "heap overflow"),
        ("stack overflow on deep input", "stack overflow"),
        ("integer overflow in length check", "integer overflow"),
        ("out-of-bounds read", "out-of-bounds"),
        ("out of bounds write", "out-of-bounds"),
        ("use-after-free in cleanup", "use-after-free"),
        ("UAF in socket teardown", "use-after-free"),
        ("denial of service via regex", "denial of service"),
        ("remote code execution via gadget", "remote code execution"),
        ("RCE in deserialization path", "remote code execution"),
        ("privilege escalation via setuid", "privilege escalation"),
        ("path traversal in file API", "path traversal"),
        ("cross-site scripting in admin", "cross-site"),
        ("XSS in the profile editor", "cross-site"),
        ("sandbox escape in the renderer", "sandbox escape"),
        ("race condition in lock-free queue", "race condition"),
        ("credential leak in debug log", "credential leak"),
        ("password leakage via core dump", "credential leak"),
        ("certificate verification bypass", "bypass"),
        ("exploit for the parser bug", "exploit"),
        ("mark the buffer as vulnerable", "vulnerable"),
        ("mitigates a memory vulnerability", "vulnerability"),
        ("reject maliciously crafted input", "malicious"),
        ("command injection in shell out", "injection"),
        ("unauthorized access to the API", "unauthorized"),
        ("various security hardening", "security"),
        ("Security: hardened the TLS path", "security"),
    ],
)
def test_keyword_true_positives(line, keyword):
    keywords, _ = scan_entry(line)
    assert keyword in keywords


def test_one_line_multiple_keywords_deduped_and_ordered():
    # "security" + "hardening" + a CVE id on one line; table order is kept
    # (identifier bucket first, generic terms toward the end).
    keywords, ids = scan_entry(
        "Security fix: certificate verification bypass via crafted X509 (CVE-2024-0727)"
    )
    assert keywords == ["cve", "bypass", "security"]
    assert ids == ["CVE-2024-0727"]


def test_ids_extracted_in_appearance_order_deduped():
    _, ids = scan_entry(
        "see CVE-2023-1111, CVE-2023-1111 and CVE-2022-2222 for details"
    )
    assert ids == ["CVE-2023-1111", "CVE-2022-2222"]


# --- keyword table: false positives must stay clean -------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Regular release",
        "Update locales and timezone data",
        "Bug fixes for the file manager",
        "Release of the 3.0.13 point update",
        "Improve startup performance by 12%",
        "Fix a typo in the documentation",
        "Add support for DOS legacy filesystems",  # 'DOS' is not DoS
        "Refactor the buffer management code",  # 'buffer' alone is not a hit
        "New secure default for the logger",  # 'secure' is not in the table
        "Upgrade to zlib 1.3.1 for the build",
        "The DOS window now renders correctly",
    ],
)
def test_keyword_false_negatives_stay_clean(line):
    assert scan_entry(line) == ([], [])


# --- security findings per section ------------------------------------------


def test_security_finding_per_hit_section():
    chg = _chg(
        "## 2.40.0\n- Fix crash via huge regex (CVE-2023-4911)\n- regular work\n"
        "## 2.39.0\n- nothing notable\n"
    )
    findings = scan_changelog(chg)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, ChangelogFinding)
    assert f.kind == KIND_SECURITY
    assert f.component == "comp"
    assert f.version == "2.40.0"
    assert f.keywords == ["cve"]
    assert f.entries == ["- Fix crash via huge regex (CVE-2023-4911)"]  # bullets kept
    assert f.pin_version is None
    assert f.rationale
    assert f.evidence and all(e for e in f.evidence)
    assert "CVE-2023-4911" in " ".join(f.evidence)


def test_non_matching_section_yields_no_finding():
    chg = _chg("## 1.0\n- Regular release\n## 0.9\n- bug fixes\n")
    assert scan_changelog(chg) == []


def test_section_with_no_entries_yields_no_finding():
    chg = _chg("## 1.0\n\n## 0.9\n- security fix\n")
    findings = scan_changelog(chg)
    assert [f.version for f in findings] == ["0.9"]


def test_keywords_deduped_across_entries_of_section():
    chg = _chg(
        "## 1.0\n- security fix for the parser\n- another security hardening pass\n"
    )
    f = scan_changelog(chg)[0]
    # "security" is first seen in entry 1, "hardening" only in entry 2 →
    # first-seen order across the section's entries.
    assert f.keywords == ["security", "hardening"]
    assert f.entries == [
        "- security fix for the parser",
        "- another security hardening pass",
    ]


def test_finding_is_immutable():
    chg = _chg("## 1.0\n- security fix\n")
    f = scan_changelog(chg)[0]
    with pytest.raises(Exception):
        f.kind = "other"  # frozen dataclass


# --- version-bump detection -------------------------------------------------


def _pin(version: str, name: str = "comp") -> Pin:
    return Pin(name=name, version=version)


def test_bump_when_latest_newer_than_pin():
    chg = _chg("## 8.9.0\n- x\n## 8.4.0\n- y\n")
    findings = scan_changelog(chg, pin=_pin("8.8.0"))
    bumps = [f for f in findings if f.kind == KIND_BUMP]
    assert len(bumps) == 1
    b = bumps[0]
    assert b.version == "8.9.0"
    assert b.pin_version == "8.8.0"
    assert b.keywords == [] and b.entries == []
    assert "8.9.0" in b.rationale and "8.8.0" in b.rationale
    joined = " | ".join(b.evidence)
    assert "pinned 8.8.0" in joined
    assert "8.9.0" in joined


def test_no_bump_when_latest_equals_pin():
    chg = _chg("## 1.3.1\n- x\n## 1.2.13\n- y\n")
    assert [f.kind for f in scan_changelog(chg, pin=_pin("1.3.1"))] == []


def test_no_bump_when_pin_is_newer_than_changelog():
    chg = _chg("## 1.2.13\n- x\n")
    assert [f.kind for f in scan_changelog(chg, pin=_pin("1.3.1"))] == []


def test_no_bump_for_unpinned_changelog():
    chg = _chg("## 8.9.0\n- x\n")
    assert [f.kind for f in scan_changelog(chg, pin=None)] == []


def test_no_bump_without_sections():
    chg = _chg("just prose\nno versions here\n")
    assert scan_changelog(chg, pin=_pin("1.0")) == []


def test_latest_uses_numeric_version_order():
    # Numeric (not lexicographic) comparison: 8.10.0 > 8.9.0.
    chg = _chg("## 8.10.0\n- x\n## 8.9.0\n- y\n")
    b = [f for f in scan_changelog(chg, pin=_pin("8.8.0")) if f.kind == KIND_BUMP][0]
    assert b.version == "8.10.0"


def test_pre_release_sorts_below_its_release():
    # A listed 9.0-rc1 preview is *below* the 9.0 GA in version order, so the
    # bump finding points at the GA, not the preview.
    chg = _chg("## 9.0-rc1\n- z\n## 9.0\n- x\n## 8.10.0\n- y\n")
    b = [f for f in scan_changelog(chg, pin=_pin("8.8.0")) if f.kind == KIND_BUMP][0]
    assert b.version == "9.0"


def test_tied_latest_prefers_first_section():
    # "1.0" and "1.0.0" are equal under compare_versions (missing components
    # count as 0); the first section wins.
    chg = _chg("## 1.0.0\n- x\n## 1.0\n- y\n")
    b = [f for f in scan_changelog(chg, pin=_pin("0.9")) if f.kind == KIND_BUMP][0]
    assert b.version == "1.0.0"


def test_all_newer_releases_listed_in_changelog_order():
    chg = _chg("## 2.0\n- x\n## 1.5\n- y\n## 1.2\n- z\n")
    b = [f for f in scan_changelog(chg, pin=_pin("1.0")) if f.kind == KIND_BUMP][0]
    joined = " | ".join(b.evidence)
    assert "2.0, 1.5, 1.2" in joined


# --- attribution + multi-changelog scanning ---------------------------------


def test_find_pin_by_name_or_alias():
    pins = [Pin(name="openssl", version="3.0.13", aliases=["libssl"])]
    assert find_pin_for("openssl", pins) is pins[0]
    assert find_pin_for("libssl", pins) is pins[0]
    assert find_pin_for("vim", pins) is None
    assert find_pin_for("openssl", []) is None


def test_first_matching_pin_wins_deterministically():
    a = Pin(name="a", version="1.0", aliases=["shared"])
    b = Pin(name="b", version="2.0", aliases=["shared"])
    assert find_pin_for("shared", [a, b]) is a
    assert find_pin_for("shared", [b, a]) is b


def test_scan_changelogs_orders_by_changelog_then_kinds():
    first = _chg("## 1.0\n- security fix\n## 2.0\n- x\n", component="one")
    second = _chg("## 9.0\n- x\n", component="two")
    pins = [Pin(name="one", version="0.5"), Pin(name="two", version="8.0")]
    findings = scan_changelogs([first, second], pins)
    assert [f.kind for f in findings] == [KIND_SECURITY, KIND_BUMP, KIND_BUMP]
    assert [f.component for f in findings] == ["one", "one", "two"]


def test_scan_changelogs_security_findings_for_unpinned_components():
    chg = _chg("## 1.0\n- CVE-2025-1234 fixed\n", component="vim")
    findings = scan_changelogs([chg], [])  # no pins at all
    assert len(findings) == 1
    assert findings[0].kind == KIND_SECURITY
    assert findings[0].pin_version is None


def test_finding_kinds_contract():
    assert FINDING_KINDS == (KIND_SECURITY, KIND_BUMP)


# --- committed fixtures: end-to-end true/false positives --------------------


def _fixture_scan() -> "list":
    pins = load_pins(PINS_OK).pins
    changelogs = load_changelogs(CHG)
    return scan_changelogs(changelogs, pins)


def test_fixtures_security_findings_per_component():
    findings = _fixture_scan()
    sec: dict = {}
    for f in findings:
        if f.kind == KIND_SECURITY:
            sec.setdefault(f.component, []).append(f)
    # glibc: 2.40.0 (CVE + buffer overflow + hardening) and 2.39.0
    # (heap overflow); 2.38.0 "Regular release" must stay clean.
    glibc = sec["glibc"]
    assert [f.version for f in glibc] == ["2.40.0", "2.39.0"]
    top = glibc[0]
    assert "cve" in top.keywords
    assert "buffer overflow" in top.keywords
    assert "hardening" in top.keywords
    assert "security" in top.keywords
    assert "CVE-2023-4911" in " ".join(top.evidence)
    assert "heap overflow" in glibc[1].keywords
    # openssl: only 3.0.14; "Release of the 3.0.13 point update" stays clean.
    openssl = sec["openssl"]
    assert [f.version for f in openssl] == ["3.0.14"]
    assert "cve" in openssl[0].keywords and "use-after-free" in openssl[0].keywords
    assert "CVE-2024-4741" in " ".join(openssl[0].evidence)
    # curl: both sections carry CVEs; "heap buffer overflow" hits the buffer
    # overflow bucket, "heap overflow" its own.
    curl = sec["curl"]
    assert [f.version for f in curl] == ["8.9.0", "8.4.0"]
    assert "buffer overflow" in curl[0].keywords
    assert "heap overflow" in curl[1].keywords
    curl_evidence = " ".join(e for f in curl for e in f.evidence)
    assert "CVE-2024-2392" in curl_evidence
    assert "CVE-2023-38545" in curl_evidence
    # zlib: only 1.3.1 ("security patch release"); 1.2.13 stays clean.
    zlib = sec["zlib"]
    assert [f.version for f in zlib] == ["1.3.1"]
    assert "buffer overflow" in zlib[0].keywords
    assert "security" in zlib[0].keywords
    assert sec.setdefault("python", []) == []  # pinned but no changelog


def test_fixtures_bump_findings():
    bumps = {f.component: f for f in _fixture_scan() if f.kind == KIND_BUMP}
    # glibc 2.40.0 > 2.39, openssl 3.0.14 > 3.0.13, curl 8.9.0 > 8.8.0,
    # but zlib's latest (1.3.1) equals the pin → NO bump for zlib.
    assert set(bumps) == {"glibc", "openssl", "curl"}
    assert bumps["glibc"].version == "2.40.0" and bumps["glibc"].pin_version == "2.39"
    assert bumps["openssl"].version == "3.0.14"
    assert bumps["curl"].version == "8.9.0"
    assert "zlib" not in bumps


def test_fixtures_scan_without_pins_is_security_only():
    changelogs = load_changelogs(CHG)
    findings = scan_changelogs(changelogs, [])
    assert findings  # non-empty
    assert all(f.kind == KIND_SECURITY for f in findings)
    assert all(f.pin_version is None for f in findings)
    assert len(findings) == 6  # 2 glibc + 1 openssl + 2 curl + 1 zlib


def test_fixtures_scan_is_deterministic():
    a = _fixture_scan()
    b = _fixture_scan()
    assert [
        (f.component, f.version, f.kind, tuple(f.keywords), tuple(f.entries))
        for f in a
    ] == [
        (f.component, f.version, f.kind, tuple(f.keywords), tuple(f.entries))
        for f in b
    ]


def test_every_finding_carries_rationale_and_evidence():
    for f in _fixture_scan():
        assert f.rationale.strip()
        assert f.evidence and all(e.strip() for e in f.evidence)
