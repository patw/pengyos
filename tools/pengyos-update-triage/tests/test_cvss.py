"""Tests for CVSS 3.1 base-score computation.

Covers ``pengyos_update_triage.cvss``: the float-safe ``roundup``, vector
parsing and validation, the base-score formulae (verified against the FIRST
CVSS v3.1 specification and its canonical worked examples), and the
score → ``low/medium/high/critical`` band mapping.

All test vectors are self-contained (no network). The canonical scores
(9.8, 10.0, 8.8, 7.5, 6.5) are widely-known reference points for CVSS 3.1.
"""

import pytest

from pengyos_update_triage.cvss import (
    CvssError,
    EXPLOITABILITY_MAX,
    V4_BASE_METRICS,
    V4_ENVIRONMENTAL_METRICS,
    V4_SUPPLEMENTAL_METRICS,
    V4_THREAT_METRICS,
    base_score,
    bte_score,
    cvss_kind,
    cvss_version,
    exploitability,
    exploitability_source,
    parse_v4_vector,
    parse_vector,
    roundup,
    score_components,
    severity_for_score,
    severity_from_vector,
)


# --- roundup ----------------------------------------------------------------


@pytest.mark.parametrize(
    "x, expected",
    [
        (0.0, 0.0),
        (0.1, 0.1),
        (0.2, 0.2),
        (0.29, 0.3),
        (0.3, 0.3),
        (3.887, 3.9),
        (5.0356, 5.1),
        (9.773, 9.8),
        (5.1, 5.1),        # exact multiple of 0.1 stays
        (10.0, 10.0),
        (9.999, 10.0),
    ],
)
def test_roundup(x, expected):
    assert roundup(x) == expected


def test_roundup_is_float_safe():
    # The spec's multiply-by-100000 guard must absorb binary-float drift such
    # as 0.1 + 0.2 == 0.30000000000000004 (which must not round up to 0.4).
    assert roundup(0.1 + 0.2) == 0.3


# --- version + parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "vector, expected",
    [
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "3.1"),
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "3.0"),
        ("CVSS:4.0/AV:N/AC:L/PR:N/UI:N/VC:H/VI:H/VA:H", "4.0"),
        ("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", None),  # no version prefix
    ],
)
def test_cvss_version(vector, expected):
    assert cvss_version(vector) == expected


def test_parse_vector_yields_eight_base_metrics():
    m = parse_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert m == {"AV": "N", "AC": "L", "PR": "N", "UI": "N", "S": "U",
                 "C": "H", "I": "H", "A": "H"}


def test_parse_vector_ignores_non_base_tail_metrics():
    # A temporal tail must not break base scoring.
    m = parse_vector("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:F")
    assert m["AV"] == "N" and m["A"] == "H"


def test_parse_vector_missing_metric_raises():
    with pytest.raises(CvssError):
        parse_vector("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H")  # no A


def test_parse_vector_malformed_token_raises():
    with pytest.raises(CvssError):
        parse_vector("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/GARBAGE")


def test_parse_vector_empty_raises():
    with pytest.raises(CvssError):
        parse_vector("")


# --- base score: canonical + fixture vectors --------------------------------


@pytest.mark.parametrize(
    "vector, expected",
    [
        # Canonical, widely-known CVSS 3.1 reference scores.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H", 8.8),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", 7.5),
        ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", 6.5),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
        # The committed advisory fixtures (see tests/fixtures/advisories/).
        ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H", 8.1),  # openssl
        ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H", 5.5),   # glibc
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),   # curl
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L", 5.3),   # zlib
        ("CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N", 4.4),   # vim
    ],
)
def test_base_score(vector, expected):
    assert base_score(vector) == expected


def test_base_score_accepts_vector_without_version_prefix():
    assert base_score("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8


def test_base_score_rejects_unsupported_version():
    # CVSS 2.0 (and any other non-3.0/3.1/4.0 version) is not supported.
    with pytest.raises(CvssError):
        base_score("CVSS:2.0/AV:N/AC:M/Au:N/C:C/I:C/A:C")
    with pytest.raises(CvssError):
        base_score("CVSS:3.2/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")


def test_base_score_rejects_malformed_vector():
    # A v4 metric name without the required v4 metric set is unparseable.
    with pytest.raises(CvssError):
        base_score("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N")  # no SA
    # A v3 vector missing a required metric.
    with pytest.raises(CvssError):
        base_score("CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")


# --- severity band mapping --------------------------------------------------


@pytest.mark.parametrize(
    "score, band",
    [
        (0.0, "none"),
        (0.1, "low"),
        (3.9, "low"),
        (4.0, "medium"),
        (6.9, "medium"),
        (7.0, "high"),
        (8.9, "high"),
        (9.0, "critical"),
        (10.0, "critical"),
    ],
)
def test_severity_for_score(score, band):
    assert severity_for_score(score) == band


def test_severity_from_vector_returns_level_and_score():
    level, score = severity_from_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert level == "critical"
    assert score == 9.8


def test_severity_from_vector_unquantified_zero():
    level, score = severity_from_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N")
    assert level == "none"
    assert score == 0.0


# --- exploitability sub-score (the item 6 exploitability signal) ------------


def _v(av, ac, pr, ui, s="U"):
    return f"CVSS:3.1/AV:{av}/AC:{ac}/PR:{pr}/UI:{ui}/S:{s}/C:H/I:H/A:H"


@pytest.mark.parametrize(
    "vector, expected",
    [
        # Spec §7.2: Exploitability = Roundup(8.22 * AV * AC * PR * UI).
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 3.9),  # the maximum
        ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H", 2.3),  # AC:H  (0.44)
        ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H", 1.9),  # AV:L+PR:L (0.55*0.62)
        ("CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N", 1.9),  # UI:R  (0.62)
        ("CVSS:3.1/AV:N/AC:L/PR:H/UI:R/S:U/C:H/I:H/A:H", 1.0),  # PR:H+UI:R (0.27*0.62)
        ("CVSS:3.1/AV:N/AC:L/PR:L/UI:R/S:U/C:H/I:H/A:H", 2.1),  # PR:L+UI:R (0.62*0.62)
        # Local-privilege worst case: the lowest achievable sub-score.
        ("CVSS:3.1/AV:P/AC:H/PR:H/UI:R/S:U/C:H/I:H/A:H", 0.2),
        # No-prefix form is accepted like base_score.
        ("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 3.9),
    ],
)
def test_exploitability_vectors(vector, expected):
    assert exploitability(vector) == expected


def test_exploitability_scope_changed_uses_changed_pr_multipliers():
    # PR:L with S:C uses 0.68 (changed scope), not 0.62 (unchanged).
    assert exploitability(_v("N", "L", "L", "N", s="C")) == 3.2


def test_exploitability_monotonic_in_attack_difficulty():
    # Network/low-complex/no-priv/no-interaction is always the easiest target.
    easiest = exploitability(_v("N", "L", "N", "N"))
    assert easiest == EXPLOITABILITY_MAX
    for av, ac, pr, ui in [
        ("A", "L", "N", "N"), ("L", "L", "N", "N"),
        ("N", "H", "N", "N"), ("N", "L", "L", "N"), ("N", "L", "N", "R"),
    ]:
        assert exploitability(_v(av, ac, pr, ui)) < easiest


def test_exploitability_rejects_unsupported_version():
    with pytest.raises(CvssError):
        exploitability("CVSS:2.0/AV:N/AC:M/Au:N/C:C/I:C/A:C")


def test_exploitability_of_v40_is_unquantified():
    # A v4.0 vector has no v3-style sub-score: 0.0, source "none".
    v4 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    assert exploitability(v4) == 0.0
    assert exploitability_source(v4) == "none"


def test_exploitability_of_raw_numeric_is_unquantified():
    assert exploitability("9.8") == 0.0
    assert exploitability_source("9.8") == "none"


def test_exploitability_of_v30_uses_v30_pr_multipliers():
    # PR:L with the legacy 3.0 multiplier (0.77) yields a *higher* sub-score
    # than the same vector under 3.1 (0.62): 8.22*0.85*0.77*0.77*0.85 -> 3.6
    # vs 8.22*0.85*0.77*0.62*0.85 -> 2.9.
    assert exploitability("CVSS:3.0/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H") == 3.6
    assert exploitability("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H") == 2.9
    assert exploitability_source("CVSS:3.0/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H") == "cvss"


# =============================================================================
# CVSS v3.0 — spec-verified corpus
#
# The 3.0 closed form is identical to 3.1 except the Privileges Required
# multipliers. The PR:N vectors are cross-checked against the independent 3.1
# reference (they must be *identical*, since PR:N is 0.85 in both); the PR:L /
# PR:H(unchanged) vectors use the legacy 2015 3.0 multipliers (0.77 / 0.44) and
# differ from their 3.1 counterparts.
# =============================================================================


@pytest.mark.parametrize(
    "vector, expected",
    [
        # PR:N — identical to 3.1 (verified against an independent 3.1 reference).
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H", 8.8),
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", 7.5),
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
        # PR:L / PR:H (unchanged) — legacy 2015 3.0 multipliers (0.77 / 0.44).
        ("CVSS:3.0/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 9.4),
        ("CVSS:3.0/AV:N/AC:L/PR:H/UI:R/S:U/C:H/I:H/A:H", 7.4),
        ("CVSS:3.0/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", 5.9),
        ("CVSS:3.0/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H", 10.0),
        ("CVSS:3.0/AV:P/AC:H/PR:H/UI:R/S:U/C:H/I:H/A:H", 6.1),
    ],
)
def test_base_score_v30(vector, expected):
    assert base_score(vector) == expected
    assert cvss_kind(vector) == "3.0"


@pytest.mark.parametrize(
    "tail",
    [
        "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:L/A:L",
        "AV:P/AC:H/PR:N/UI:R/S:U/C:H/I:H/A:H",
    ],
)
def test_v30_pr_none_matches_v31(tail):
    # PR:N (0.85 in both) → the base score is version-independent.
    assert base_score("CVSS:3.0/" + tail) == base_score("CVSS:3.1/" + tail)


@pytest.mark.parametrize(
    "tail",
    [
        "AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H",  # 9.4 (3.0) vs 8.8 (3.1)
        "AV:N/AC:L/PR:H/UI:R/S:U/C:H/I:H/A:H",   # 7.4 (3.0) vs 6.8 (3.1)
    ],
)
def test_v30_differs_from_v31_only_in_pr(tail):
    # The *only* 3.0↔3.1 difference is the PR multiplier, so any PR:L/PR:H
    # (unchanged-scope) vector must score differently from its 3.1 twin.
    assert base_score("CVSS:3.0/" + tail) != base_score("CVSS:3.1/" + tail)


# =============================================================================
# CVSS v4.0 — spec-verified corpus
#
# CVSS v4.0 has no closed-form base score: it is the MacroVector lookup-table
# value (spec §8.3) minus an interpolated proportional distance (spec §8.2).
# The values below are the official reference-implementation results for a
# spread of Base vectors across every severity band (they match the FIRST
# reference calculator and an independent port over the full metric space).
# =============================================================================


@pytest.mark.parametrize(
    "vector, expected",
    [
        # Critical band (9.0–10.0).
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.3),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H", 10.0),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H", 9.4),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:H/VA:N/SC:H/SI:H/SA:H", 9.3),
        ("CVSS:4.0/AV:N/AC:H/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.2),
        ("CVSS:4.0/AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.2),
        # High band (7.0–8.9).
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N", 8.7),
        ("CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N", 8.2),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:A/VC:H/VI:H/VA:H/SC:L/SI:L/SA:N", 8.6),
        ("CVSS:4.0/AV:L/AC:L/AT:P/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 7.3),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N", 7.1),
        # Medium band (4.0–6.9).
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N", 6.9),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:H/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N", 6.9),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:L/VA:N/SC:L/SI:L/SA:L", 6.9),
        ("CVSS:4.0/AV:A/AC:L/AT:N/PR:N/UI:N/VC:N/VI:L/VA:N/SC:H/SI:N/SA:H", 6.4),
        ("CVSS:4.0/AV:L/AC:L/AT:N/PR:H/UI:N/VC:N/VI:N/VA:N/SC:H/SI:N/SA:N", 5.9),
        ("CVSS:4.0/AV:P/AC:L/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 5.4),
        # Low band (0.1–3.9).
        ("CVSS:4.0/AV:L/AC:L/AT:N/PR:N/UI:A/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N", 4.6),
        # Zero-impact shortcut.
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N", 0.0),
        ("CVSS:4.0/AV:L/AC:L/AT:N/PR:H/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N", 0.0),
    ],
)
def test_base_score_v40(vector, expected):
    assert base_score(vector) == expected
    assert cvss_kind(vector) == "4.0"


def test_v40_without_version_prefix_is_detected():
    # Version-less v4 vectors are inferred from their metric names.
    assert base_score("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N") == 9.3


def test_v40_supplemental_metrics_are_ignored():
    # Supplemental metrics (S, V) never affect the Base score.
    assert base_score("CVSS:4.0/AV:P/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:H/SA:N/S:P/V:D") == 8.3


def test_v40_requires_all_eleven_base_metrics():
    with pytest.raises(CvssError):
        base_score("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N")  # no SA


def test_v40_rejects_invalid_metric_value():
    with pytest.raises(CvssError):
        base_score("CVSS:4.0/AV:Z/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N")


def test_v40_severity_from_vector_and_bands():
    assert severity_from_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H") == ("critical", 10.0)
    assert severity_from_vector("CVSS:4.0/AV:P/AC:L/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N") == ("medium", 5.4)
    assert severity_from_vector("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N") == ("none", 0.0)


# =============================================================================
# Raw numeric (non-vector) scores
# =============================================================================


@pytest.mark.parametrize(
    "score, level",
    [
        ("0.0", "none"), ("0.1", "low"), ("3.9", "low"),
        ("4.0", "medium"), ("6.9", "medium"),
        ("7.0", "high"), ("8.9", "high"),
        ("9.0", "critical"), ("10", "critical"), ("9.8", "critical"),
    ],
)
def test_raw_numeric_score(score, level):
    assert cvss_kind(score) == "raw"
    assert base_score(score) == float(score)
    assert severity_from_vector(score) == (level, float(score))
    assert exploitability(score) == 0.0
    assert exploitability_source(score) == "none"


def test_raw_numeric_out_of_range_raises():
    with pytest.raises(CvssError):
        base_score("10.5")
    with pytest.raises(CvssError):
        base_score("-1")


# =============================================================================
# Version dispatch (cvss_kind)
# =============================================================================


@pytest.mark.parametrize(
    "value, kind",
    [
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "3.0"),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "3.1"),
        ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", "4.0"),
        ("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "3.1"),  # version-less v3
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", "4.0"),  # version-less v4
        ("9.8", "raw"),
        ("10", "raw"),
    ],
)
def test_cvss_kind_dispatch(value, kind):
    assert cvss_kind(value) == kind


def test_cvss_kind_rejects_empty_and_unsupported():
    with pytest.raises(CvssError):
        cvss_kind("")
    with pytest.raises(CvssError):
        cvss_kind("CVSS:2.0/AV:N/AC:M/Au:N/C:C/I:C/A:C")
    with pytest.raises(CvssError):
        cvss_kind("not a cvss value at all")

# =============================================================================
# CVSS v4.0 full BTE — Threat + Environmental metrics (spec-verified corpus)
#
# Per the FIRST v4.0 Specification (specification-document v1.2, §8) there is
# ONE 6-digit MacroVector and ONE lookup table (the FIRST reference
# calculator's ``cvss_lookup.js``, spec §8.3); the MacroVector is built from
# the *effective* metric values, so a vector's score already reflects its
# Threat metric (E, X/absent → worst case A) and its Environmental group
# (CR/IR/AR X/absent → worst case H, M* overrides, MSI/MSA). ``bte_score`` is
# therefore the score of the full vector as given; ``base_score`` is the
# score of the Base metrics only.
#
# The expected values below are the official scores from the FIRST v4.0
# *Examples* page (every Base / Base+Threat / Base+Environmental / BTE row of
# that page) and were re-verified against an independent transcription of
# the reference calculator's JS over 40,000 random full vectors (0
# mismatches). Two BASE rows on the Examples page carry stale hand-typed
# values (``AV:N/AC:H/AT:P/.../SA:N`` printed as 8.2, actually 9.2; and
# ``AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N`` printed as 8.5,
# actually 8.7) — both independent reference ports agree with the values
# locked here, so the reference wins over the prose.
# =============================================================================

_V4 = "CVSS:4.0/"


@pytest.mark.parametrize(
    "vector, expected",
    [
        # --- Base (CVSS-B) rows from the FIRST Examples page ---------------
        ("AV:L/AC:L/AT:P/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 7.3),   # NGINX mp4
        ("AV:N/AC:L/AT:P/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 7.7),   # FMC/FTD sftunnel
        ("AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:L/VA:L/SC:N/SI:N/SA:N", 8.3),   # OpenShift FIPS
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:L", 8.7),   # Junos SIP DoS
        ("AV:L/AC:L/AT:N/PR:H/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N", 8.3),   # Intel SMM
        ("AV:A/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N", 8.6),   # BD PCU
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:N/VI:N/VA:N/SC:H/SI:L/SA:H", 6.4),   # reflected XSS
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H", 10.0),  # log4j
        ("AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.2),   # log4j immutable (page says 8.2 — stale)
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 8.7),   # regreSSHion (page says 8.5 — stale)
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:N", 7.1),   # PrestaShop SQLi
        ("AV:N/AC:L/AT:P/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H", 9.0),   # CODESYS (page's 9.4 row is the AT:N variant below)
        ("AV:L/AC:L/AT:N/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 8.5),   # UEFI BIOS lock
        ("AV:P/AC:L/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 5.4),   # Intel DCI debug
        ("AV:L/AC:L/AT:N/PR:N/UI:A/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N", 4.6),   # Acrobat protocol warning
        ("AV:N/AC:L/AT:N/PR:N/UI:A/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N", 5.1),   # RocketChat self-XSS
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N", 6.9),   # Junos EX4650 forwarding
        ("AV:L/AC:L/AT:N/PR:H/UI:N/VC:N/VI:N/VA:N/SC:H/SI:N/SA:N", 5.9),   # VirtualBox infra
        ("AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H", 9.4),   # VMware vmnetdhcp
        ("AV:P/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:H/SA:N", 8.3),   # BD PCU (S:P/V:D supplemental — ignored)
        ("AV:N/AC:L/AT:N/PR:N/UI:A/VC:L/VI:L/VA:N/SC:L/SI:L/SA:N", 5.1),   # Zimbra XSS variant
        ("AV:N/AC:L/AT:N/PR:L/UI:P/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N", 5.1),   # SharePoint XSS
        ("AV:N/AC:L/AT:N/PR:L/UI:A/VC:H/VI:H/VA:H/SC:L/SI:L/SA:N", 8.5),   # XSS into web app
        ("AV:N/AC:L/AT:N/PR:N/UI:A/VC:N/VI:L/VA:N/SC:N/SI:N/SA:N", 5.1),   # WordPress CSRF
        ("AV:A/AC:L/AT:N/PR:N/UI:N/VC:N/VI:L/VA:N/SC:H/SI:N/SA:H", 6.4),   # rogue forwarding table
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/S:P/AU:Y/V:C/RE:L", 9.4),  # CODESYS (supplemental ignored)
        # Supplemental-only tails never change the Base score.
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:N/V:C", 8.7),
        ("AV:P/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:H/SA:N/S:P/V:D", 8.3),
    ],
)
def test_bte_corpus_base_rows(vector, expected):
    vector = _V4 + vector
    assert base_score(vector) == expected
    assert bte_score(vector) == expected  # Base-only: BTE == B
    assert severity_from_vector(vector)[1] == expected


@pytest.mark.parametrize(
    "vector, expected",
    [
        # --- Threat (CVSS-BT) rows ------------------------------------------
        # Same base vector, E:U → the score drops (exploit unreported).
        ("AV:N/AC:L/AT:P/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 7.7),
        ("AV:N/AC:L/AT:P/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:U", 5.2),
        # cpanel Redis (E:A, full base worst case).
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:A", 10.0),
        # OpenSSL heartbeat (E:A).
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N/E:A", 8.7),
        # log4j (E:A) — E:A does not raise a score already at its MacroVector top.
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A", 9.3),
        # Junos SIP DoS (E:U) + mitigation MVA:H/MSA:N (no-op overrides).
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:L/E:U", 6.6),
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:L/E:U/MVA:H/MSA:N", 6.6),
        # Intel SMM (E:U).
        ("AV:L/AC:L/AT:N/PR:H/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N/E:U", 5.6),
        # regreSSHion (E:P).
        ("AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:P", 8.2),
        # UEFI BIOS lock (E:U) + MUI:A (raises the effective UI level).
        ("AV:L/AC:L/AT:N/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:U/MUI:A", 5.7),
        # log4j AC:H variant (E:A).
        ("AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A", 9.2),
    ],
)
def test_bte_corpus_threat_rows(vector, expected):
    assert bte_score(_V4 + vector) == expected


def test_threat_metric_lowers_the_score():
    base = _V4 + "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    attacked = bte_score(base + "/E:A")
    poc = bte_score(base + "/E:P")
    unreported = bte_score(base + "/E:U")
    assert base_score(base) == attacked == 9.3  # E:X (absent) = worst case = E:A
    assert poc < attacked
    assert unreported < poc


@pytest.mark.parametrize(
    "vector, expected",
    [
        # --- Environmental (CVSS-BE) rows -----------------------------------
        # OpenShift FIPS: consumer requirements + mitigations (no threat metric).
        ("AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:L/VA:L/SC:N/SI:N/SA:N/"
         "CR:H/IR:L/AR:L/MAV:N/MAC:H/MVC:H/MVI:L/MVA:L", 8.1),
        # Safety override: MSI:S forces EQ4=0 — the score goes UP (9.7 > 8.6).
        ("AV:A/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N/MSI:S/S:P", 9.7),
        # Explicit MSI:N/MSA:N (not X) leaves the score at its Base value.
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:L/VA:N/SC:L/SI:L/SA:L/MSI:N/MSA:N", 6.9),
    ],
)
def test_bte_corpus_environmental_rows(vector, expected):
    assert bte_score(_V4 + vector) == expected


def test_msi_safety_raises_the_score_above_base():
    base = _V4 + "AV:A/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N"
    assert bte_score(base + "/MSI:S") > base_score(base)


@pytest.mark.parametrize(
    "vector, expected",
    [
        # --- Full CVSS-BTE rows ---------------------------------------------
        # cpanel Redis full BTE (E:A + Low security requirements on a low-
        # value asset).
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/"
         "E:A/CR:L/IR:L/AR:L", 9.6),
        # log4j immutable containers (E:P + mitigations).
        ("AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/"
         "E:P/MAC:L/MAT:N/MVC:N/MVI:N/MVA:L", 5.5),
        # reflected XSS with requirements + attack-vector upgrade + threat.
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:N/VI:N/VA:N/SC:H/SI:L/SA:H/"
         "E:U/MAV:A/R:U/V:C", 2.4),
        # critical base + E:A + heavy environmental (mitigations + requirements).
        ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/"
         "E:A/CR:H/IR:H/AR:H/MAV:L/MAT:P", 8.9),
        # full mitigation drives the BTE score to 0 (MVC/MVI/MVA → N).
        ("AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/"
         "E:A/MVC:N/MVI:N/MVA:N", 0.0),
        # The three consumer-environment variations of the SMM example.
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/"
         "E:P/CR:L/IR:H/AR:L/MAV:L/MAC:H/MAT:N/MPR:N/MUI:N/MVC:N/MVI:H/MVA:L/"
         "MSC:N/MSI:S/MSA:L", 7.0),
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/"
         "MAV:A/MAC:H/MAT:N/MPR:L/MUI:N/MVC:L/MVI:H/MVA:H/MSC:L/MSI:S/MSA:S/"
         "CR:L/IR:H/AR:H/E:P", 7.4),
        ("AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/"
         "MAV:N/MAC:H/MAT:N/MPR:L/MUI:N/MVC:H/MVI:H/MVA:H/MSC:H/MSI:S/MSA:H/"
         "CR:M/IR:H/AR:M/E:P", 8.7),
    ],
)
def test_bte_corpus_full_bte_rows(vector, expected):
    assert bte_score(_V4 + vector) == expected


def test_full_bte_env_mitigation_can_zero_the_score():
    base = _V4 + "AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    assert base_score(base) > 0.0
    assert bte_score(base + "/E:A/MVC:N/MVI:N/MVA:N") == 0.0


def test_full_bte_ordering_follows_mitigation_strength():
    # The three consumer-environment variations of the SMM example, from the
    # FIRST Examples page: stronger mitigations → lower BTE score.
    head = "AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/"
    weak = bte_score(_V4 + head +
                     "MAV:N/MAC:H/MAT:N/MPR:L/MUI:N/MVC:H/MVI:H/MVA:H/MSC:H/"
                     "MSI:S/MSA:H/CR:M/IR:H/AR:M/E:P")
    mid = bte_score(_V4 + head +
                    "MAV:A/MAC:H/MAT:N/MPR:L/MUI:N/MVC:L/MVI:H/MVA:H/MSC:L/"
                    "MSI:S/MSA:S/CR:L/IR:H/AR:H/E:P")
    strong = bte_score(_V4 + head +
                       "E:P/CR:L/IR:H/AR:L/MAV:L/MAC:H/MAT:N/MPR:N/MUI:N/"
                       "MVC:N/MVI:H/MVA:L/MSC:N/MSI:S/MSA:L")
    assert (weak, mid, strong) == (8.7, 7.4, 7.0)


def test_bte_score_of_non_v4_is_the_base_score():
    v3 = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    assert bte_score(v3) == base_score(v3) == 9.8
    assert bte_score("9.8") == 9.8


def test_bte_score_rejects_malformed_v4_like_base_score():
    with pytest.raises(CvssError):
        bte_score("CVSS:4.0/AV:Z/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N")
    with pytest.raises(CvssError):
        bte_score("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:Z")


# --- v4 parsing: all 32 metrics ---------------------------------------------

def test_v4_metric_groups_cover_all_32_metrics():
    groups = (V4_BASE_METRICS + V4_THREAT_METRICS +
              V4_ENVIRONMENTAL_METRICS + V4_SUPPLEMENTAL_METRICS)
    assert len(groups) == 32
    assert len(set(groups)) == 32


def test_parse_v4_vector_reads_every_group():
    vector = (_V4 + "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
              "/E:P/CR:H/IR:L/AR:M/MAV:A/MAC:H/MAT:N/MPR:L/MUI:A"
              "/MVC:L/MVI:N/MVA:H/MSC:H/MSI:S/MSA:S"
              "/S:P/AU:Y/R:I/V:C/RE:H/U:Red")
    m = parse_v4_vector(vector)
    assert m["AV"] == "N" and m["SA"] == "N"          # base
    assert m["E"] == "P"                               # threat
    assert m["CR"] == "H" and m["MSA"] == "S"          # environmental
    assert m["S"] == "P" and m["U"] == "Red"           # supplemental


def test_parse_v4_vector_rejects_bad_non_base_values():
    base = "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    for bad in ("E:Z", "CR:Y", "MAV:M", "MSI:Z", "U:Blue", "R:Z", "V:E"):
        with pytest.raises(CvssError):
            parse_v4_vector(_V4 + base + "/" + bad)


def test_parse_v4_vector_accepts_msi_safety_value():
    m = parse_v4_vector(_V4 + "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/MSI:S/MSA:S")
    assert m["MSI"] == "S" and m["MSA"] == "S"


def test_parse_v4_vector_ignores_unknown_metrics_and_order():
    m = parse_v4_vector(_V4 + "SA:N/AV:N/FUTURE:Q/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N")
    assert m["AV"] == "N" and m["SA"] == "N" and "FUTURE" not in m


def test_parse_v4_vector_rejects_duplicates():
    with pytest.raises(CvssError):
        parse_v4_vector(_V4 + "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:P/E:U")
    with pytest.raises(CvssError):
        parse_v4_vector(_V4 + "AV:N/AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N")


# --- score_components (the classification's group nomenclature) --------------

@pytest.mark.parametrize(
    "tail, expected",
    [
        ("", ("base",)),
        ("/E:X", ("base", "threat")),
        ("/E:P", ("base", "threat")),
        ("/CR:L", ("base", "environmental")),
        ("/MSI:S", ("base", "environmental")),
        ("/E:U/MUI:A", ("base", "threat", "environmental")),
        ("/S:P/V:C", ("base",)),  # supplemental never counts
    ],
)
def test_score_components_v4(tail, expected):
    assert score_components(_V4 + "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N" + tail) == expected


def test_score_components_non_v4_is_base_only():
    assert score_components("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == ("base",)
    assert score_components("9.8") == ("base",)


# --- rounding contract: the official Math.round(value*10)/10 -----------------

def test_v4_rounding_matches_the_reference_calculator():
    # Raw pre-rounded score is 8.549999999999999: the official FIRST
    # calculator rounds with Math.round(value*10)/10 → 8.5 (the epsilon
    # Decimal ports say 8.6 — the reference wins, see _round1's note).
    assert base_score(_V4 + "AV:N/AC:L/AT:N/PR:N/UI:P/VC:N/VI:L/VA:H/SC:H/SI:H/SA:H") == 8.5

