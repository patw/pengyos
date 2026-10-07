"""The five checks, defined.

This module is the *definition* of what the witness checks. Each definition
has one pluggable, individually-testable function; the registry and the
parameters here are the contract those functions must honor, so the
check ids, titles, pass criteria, conventional filenames, and the
repro threshold are fixed in one place and cannot drift.

Each :class:`CheckDefinition` carries:

* ``check_id``   — the stable id that appears in findings and the JSON
  report (``checks.CHECK_IDS``, in report order).
* ``title``      — short human name for the pretty report.
* ``criterion``  — the full pass criterion in plain words.
* ``artifacts``  — conventional relative paths under the build output
  dir the check inspects (empty for the attest check: its log is
  given by ``--attest``, not by convention).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# ----------------------------------------------------------------------
# Stable check ids (order in CHECKS == order in the report).
# ----------------------------------------------------------------------
CHECK_ATTEST_CHAIN = "attest-chain"
CHECK_SBOM = "sbom"
CHECK_SIGNATURES = "signatures"
CHECK_REPRO = "repro-score"
CHECK_EXPECTED_FILES = "expected-files"

# ----------------------------------------------------------------------
# Build-output-dir conventions (M1 layout per pengyos-build /
# buildroot-external board; see spec.md "Check definitions" for why
# each of these is pinned here).
# ----------------------------------------------------------------------

#: Conventional SBOM names, in preference order. The first one that
#: exists is the one the SBOM check inspects; if more than one exists,
#: the check FAILs with the collision listed as evidence.
SBOM_FILENAMES: Tuple[str, ...] = ("sbom.spdx.json", "sbom.cdx.json")

#: Conventional name of the pengyos-repro JSON report written into the
#: build output dir.
REPRO_REPORT_FILENAME = "repro-report.json"

#: Default minimum repro score. 1.0 = byte-identical rebuild, the
#: reproducibility promise of the SPEC ("Rebuilds reproducibly").
#: The criterion is "score >= threshold"; the later CLI subtask may
#: expose a lower bar for triage, but the witness default is 1.0.
DEFAULT_REPRO_THRESHOLD: float = 1.0

#: The artifacts a build output dir is expected to contain (M1 layout:
#: the ext4 rootfs and the kernel image produced by Buildroot's
#: images/ step). The signatures check requires a sibling
#: ``<artifact>.sig`` for EVERY one of these; the expected-files check
#: requires each to exist as a regular, non-empty file.
EXPECTED_ARTIFACTS: Tuple[str, ...] = ("images/rootfs.ext4", "images/bzImage")

#: Suffix of a detached signature for a file (pengyos-sign convention).
SIGNATURE_SUFFIX = ".sig"


@dataclass(frozen=True)
class CheckDefinition:
    """The definition of one check: what it inspects and when it passes."""

    check_id: str
    title: str
    criterion: str
    artifacts: Tuple[str, ...] = ()


CHECKS: Tuple[CheckDefinition, ...] = (
    CheckDefinition(
        CHECK_ATTEST_CHAIN,
        "Attest chain valid",
        "The attest log is an append-only JSONL hash chain: every "
        "non-blank line is a record with the required schema fields "
        "(attest.REQUIRED_FIELDS), a self-hash that recomputes "
        "(attest.record_hash), a 1-based contiguous seq, and "
        "prev_hash linkage against the previous record (the first "
        "record against the genesis value). The walk stops at the "
        "first failing record and reports it. A missing or empty log "
        "is a valid empty chain.",
        artifacts=(),  # the log path is given by --attest, not the build dir
    ),
    CheckDefinition(
        CHECK_SBOM,
        "SBOM present and valid",
        "The build dir contains exactly one SBOM under one of the "
        "conventional names (checks.SBOM_FILENAMES); it parses as a "
        "JSON object and is structurally valid for its detected format "
        "(SPDX 2.3 or CycloneDX 1.5) per the sbom_format rules.",
        artifacts=SBOM_FILENAMES,
    ),
    CheckDefinition(
        CHECK_SIGNATURES,
        "Artifacts signed",
        "Every expected artifact (checks.EXPECTED_ARTIFACTS) has a "
        "sibling <artifact>.sig in the pengyos-sig-v1 wire format: a "
        "well-formed six-field header whose embedded sha256 equals the "
        "artifact's actual sha256, followed by exactly 64 raw "
        "signature bytes. A missing, malformed, or digest-mismatched "
        ".sig is a FAIL finding naming the artifact.",
        artifacts=EXPECTED_ARTIFACTS,
    ),
    CheckDefinition(
        CHECK_REPRO,
        "Repro report present and >= threshold",
        "The build dir contains repro-report.json: a JSON object with "
        "numeric score in [0, 1] and boolean identical (per the "
        "repro_format rules); it passes iff score is >= the threshold "
        "(checks.DEFAULT_REPRO_THRESHOLD, default 1.0).",
        artifacts=(REPRO_REPORT_FILENAME,),
    ),
    CheckDefinition(
        CHECK_EXPECTED_FILES,
        "Expected files exist",
        "Every expected artifact (checks.EXPECTED_ARTIFACTS) exists "
        "under the build dir as a regular, non-empty file. A path that "
        "is missing, empty, or not a regular file is a FAIL finding "
        "naming the path.",
        artifacts=EXPECTED_ARTIFACTS,
    ),
)

#: The stable check ids in report order.
CHECK_IDS: Tuple[str, ...] = tuple(c.check_id for c in CHECKS)


def check_by_id(check_id: str) -> CheckDefinition:
    """Return the CheckDefinition with ``check_id`` (KeyError if unknown)."""
    for check in CHECKS:
        if check.check_id == check_id:
            return check
    raise KeyError(
        f"unknown check id: {check_id!r} (expected one of {CHECK_IDS!r})"
    )
