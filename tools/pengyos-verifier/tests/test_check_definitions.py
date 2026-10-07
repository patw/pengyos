"""Tests for the check definitions (checks.py).

These pin the registry — the five stable check ids in report order,
their titles, criteria, and the build-output-dir conventions the
implementations (next subtask) must honor.
"""

from __future__ import annotations

import pytest

from pengyos_verifier import checks
from pengyos_verifier.checks import (
    CHECK_ATTEST_CHAIN,
    CHECK_EXPECTED_FILES,
    CHECK_IDS,
    CHECK_REPRO,
    CHECK_SBOM,
    CHECK_SIGNATURES,
    CheckDefinition,
    check_by_id,
)


def test_registry_has_exactly_the_five_defined_checks_in_order():
    assert CHECK_IDS == (
        "attest-chain",
        "sbom",
        "signatures",
        "repro-score",
        "expected-files",
    )
    assert [c.check_id for c in checks.CHECKS] == list(CHECK_IDS)


def test_every_definition_is_complete():
    titles, criteria = set(), set()
    for c in checks.CHECKS:
        assert isinstance(c, CheckDefinition)
        assert c.check_id
        assert c.title
        assert c.criterion, f"{c.check_id} lacks a pass criterion"
        titles.add(c.title)
        criteria.add(c.criterion)
        for path in c.artifacts:
            assert path and not path.startswith("/"), \
                f"{c.check_id}: artifacts must be relative build-dir paths"
    # Unique titles and unique criteria: the report must be able to
    # point at exactly one check by name or by criterion.
    assert len(titles) == len(checks.CHECKS)
    assert len(criteria) == len(checks.CHECKS)


def test_check_ids_are_unique_and_stable():
    ids = [c.check_id for c in checks.CHECKS]
    assert len(set(ids)) == len(ids)
    for c in checks.CHECKS:
        assert check_by_id(c.check_id) is c


def test_check_by_id_rejects_unknown_ids():
    with pytest.raises(KeyError, match="unknown check id"):
        check_by_id("nope")
    with pytest.raises(KeyError):
        check_by_id("")


def test_conventional_filenames_are_pinned():
    assert checks.SBOM_FILENAMES == ("sbom.spdx.json", "sbom.cdx.json")
    assert checks.REPRO_REPORT_FILENAME == "repro-report.json"
    assert checks.SIGNATURE_SUFFIX == ".sig"


def test_default_repro_threshold_is_full_reproducibility():
    # The SPEC promise is "Rebuilds reproducibly" (byte-identical);
    # the witness default is the bar the build actually has to meet.
    assert checks.DEFAULT_REPRO_THRESHOLD == 1.0
    assert 0.0 <= checks.DEFAULT_REPRO_THRESHOLD <= 1.0


def test_expected_artifacts_are_the_m1_layout():
    assert checks.EXPECTED_ARTIFACTS == (
        "images/rootfs.ext4",
        "images/bzImage",
    )


def test_definitions_reference_the_pinned_parameters():
    by_id = {c.check_id: c for c in checks.CHECKS}
    assert by_id[CHECK_SBOM].artifacts == checks.SBOM_FILENAMES
    assert by_id[CHECK_REPRO].artifacts == (checks.REPRO_REPORT_FILENAME,)
    assert by_id[CHECK_SIGNATURES].artifacts == checks.EXPECTED_ARTIFACTS
    assert by_id[CHECK_EXPECTED_FILES].artifacts == checks.EXPECTED_ARTIFACTS
    # The attest log comes from --attest, not from the build dir.
    assert by_id[CHECK_ATTEST_CHAIN].artifacts == ()
