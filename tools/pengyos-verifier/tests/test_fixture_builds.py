"""Tests for the COMMITTED fixture build dirs + attest logs.

This is the "Fixture build dirs (good + several bad variants)"
subtask: ``tests/fixtures/`` ships deterministic on-disk inputs so
the CLI, docs, and future iterations work against the same bytes
without any generator at test time (a generator-free fixture survives
clone/CI exactly like real build output does).

The committed bytes are pinned three ways:

* the good build dir is byte-for-byte the output of the reference
  generator (``tests/fixtures_build.build_good_dir``);
* every bad variant differs from the good dir by EXACTLY its intended
  one-file mutation (nothing else may have drifted);
* every committed attest log verifies with exactly the intended
  outcome under ``attest.verify_attest_text``.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

import pytest

from pengyos_verifier import attest, checks
from pengyos_verifier.checks_impl import CheckContext, run_checks
from pengyos_verifier.model import Status
from pengyos_verifier.cli import main
from tests.fixtures_build import build_good_dir

FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures")
GOOD_DIR = os.path.join(FIX_DIR, "build", "good")
BAD_DIR = os.path.join(FIX_DIR, "build", "bad")
ATT_DIR = os.path.join(FIX_DIR, "attest")

SBOM = checks.SBOM_FILENAMES[0]                    # sbom.spdx.json
ROOTFS = checks.EXPECTED_ARTIFACTS[0]              # images/rootfs.ext4
BZIMAGE = checks.EXPECTED_ARTIFACTS[1]             # images/bzImage
REPRO = checks.REPRO_REPORT_FILENAME               # repro-report.json
SIG_SUFFIX = checks.SIGNATURE_SUFFIX

GOOD_LOG = os.path.join(ATT_DIR, "good.log")


def _ctx(build_dir: str, attest_path: str = GOOD_LOG) -> CheckContext:
    return CheckContext(build_dir=build_dir, attest_path=attest_path)


def _findings(build_dir: str,
              attest_path: str = GOOD_LOG) -> Dict[str, object]:
    return {f.check: f for f in run_checks(_ctx(build_dir, attest_path))}


def _status(findings: Dict[str, object], check_id: str) -> str:
    return findings[check_id].status.value  # type: ignore[union-attr]


def _text(findings: Dict[str, object], check_id: str) -> str:
    """The finding's message plus all evidence, in one searchable string."""
    f = findings[check_id]  # type: ignore[union-attr]
    return " | ".join([f.message] + list(f.evidence))


def _tree(root: str) -> Dict[str, bytes]:
    """rel path -> file bytes for every regular file under ``root``."""
    out: Dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = fh.read()
    return out


def _diff(good: Dict[str, bytes],
          variant: Dict[str, bytes]) -> Tuple[frozenset, frozenset, frozenset]:
    """(only_in_good, only_in_variant, changed) between two trees."""
    only_good = frozenset(set(good) - set(variant))
    only_variant = frozenset(set(variant) - set(good))
    changed = frozenset(
        rel for rel in set(good) & set(variant)
        if good[rel] != variant[rel]
    )
    return only_good, only_variant, changed


# ----------------------------------------------------------------------
# the good build dir: present, and byte-for-byte the reference generator
# ----------------------------------------------------------------------

def test_good_build_dir_is_committed():
    assert os.path.isdir(GOOD_DIR)
    tree = _tree(GOOD_DIR)
    expected_files = {
        ROOTFS, ROOTFS + SIG_SUFFIX,
        BZIMAGE, BZIMAGE + SIG_SUFFIX,
        SBOM, REPRO,
    }
    assert set(tree) == expected_files


def test_no_fixture_file_is_git_ignored():
    """The fixture tree must be TRACKABLE by git.

    Regression guard: the scaffold's generic ``build/`` rule in
    ``.gitignore`` also matches ``tests/fixtures/build/`` — the whole
    fixture build tree silently vanished from a commit once (tests
    still passed, because they read the working tree, not git). The
    explicit ``!tests/fixtures/build/`` negation is what makes the
    tree committable; this test fails if it is ever removed.
    """
    import subprocess

    tool_root = os.path.dirname(os.path.dirname(FIX_DIR))  # holds .gitignore
    fixture_files = sorted(_tree(FIX_DIR))
    assert fixture_files, "no fixture files found at all"
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(f"tests/fixtures/{name}" for name in fixture_files),
        capture_output=True, text=True, cwd=tool_root,
    )
    ignored = result.stdout.splitlines()
    assert not ignored, (
        "git is ignoring committed fixture files — the tree would "
        f"silently drop from the repo: {ignored}"
    )


def test_good_build_dir_matches_reference_generator(tmp_path):
    """The committed bytes ARE the generator's output — no drift."""
    gen = tmp_path / "generated"
    gen.mkdir()
    build_good_dir(gen)
    assert _tree(GOOD_DIR) == _tree(str(gen))


def test_good_build_dir_passes_all_checks():
    findings = _findings(GOOD_DIR)
    for cid in checks.CHECK_IDS:
        assert findings[cid].status is Status.PASS, (  # type: ignore[union-attr]
            cid, findings[cid].evidence)               # type: ignore[union-attr]


def test_good_build_dir_passes_without_attest_log():
    # --attest omitted (empty chain) must still pass the whole set.
    findings = _findings(GOOD_DIR, attest_path=os.path.join(
        ATT_DIR, "definitely-absent.log"))
    assert all(f.status is Status.PASS for f in findings.values())  # type: ignore[union-attr]


# ----------------------------------------------------------------------
# the good attest log: a valid 3-record chain
# ----------------------------------------------------------------------

def test_good_attest_log_is_a_valid_three_record_chain():
    report = attest.verify_attest_text(open(GOOD_LOG, encoding="utf-8").read())
    assert report.ok is True
    assert report.record_count == 3
    assert report.first_bad_seq is None
    assert report.first_bad_line is None


# ----------------------------------------------------------------------
# the bad build variants: each is EXACTLY one mutation of the good dir
# and fails exactly the checks that mutation should break
# ----------------------------------------------------------------------

def test_all_committed_bad_variant_dirs_exist():
    names = sorted(os.listdir(BAD_DIR))
    assert names == ["bad-repro", "digest-mismatch", "invalid-sbom",
                     "missing-artifact", "missing-sbom", "unsigned"]


@pytest.mark.parametrize("variant_name, missing_only, changed_only", [
    # (name, files the variant lacks, files the variant altered)
    ("missing-sbom", frozenset({SBOM}), frozenset()),
    ("invalid-sbom", frozenset(), frozenset({SBOM})),
    ("unsigned", frozenset({BZIMAGE + SIG_SUFFIX}), frozenset()),
    ("digest-mismatch", frozenset(), frozenset({BZIMAGE + SIG_SUFFIX})),
    ("bad-repro", frozenset(), frozenset({REPRO})),
    ("missing-artifact", frozenset({BZIMAGE, BZIMAGE + SIG_SUFFIX}),
     frozenset()),
])
def test_variant_differs_exactly_by_its_intended_mutation(
        variant_name: str, missing_only: frozenset,
        changed_only: frozenset):
    good = _tree(GOOD_DIR)
    variant = _tree(os.path.join(BAD_DIR, variant_name))
    only_good, only_variant, changed = _diff(good, variant)
    assert only_good == missing_only, variant_name
    assert only_variant == frozenset(), (
        f"{variant_name} must not add files: {sorted(only_variant)}")
    assert changed == changed_only, variant_name


@pytest.mark.parametrize("variant_name, failing, evidence_snippets, passing", [
    ("missing-sbom",
     {checks.CHECK_SBOM},
     {checks.CHECK_SBOM: "no SBOM found"},
     [checks.CHECK_ATTEST_CHAIN, checks.CHECK_SIGNATURES,
      checks.CHECK_REPRO, checks.CHECK_EXPECTED_FILES]),
    ("invalid-sbom",
     {checks.CHECK_SBOM},
     {checks.CHECK_SBOM: "not structurally valid"},
     [checks.CHECK_ATTEST_CHAIN, checks.CHECK_SIGNATURES,
      checks.CHECK_REPRO, checks.CHECK_EXPECTED_FILES]),
    ("unsigned",
     {checks.CHECK_SIGNATURES},
     {checks.CHECK_SIGNATURES: f"{BZIMAGE}{SIG_SUFFIX}: missing"},
     [checks.CHECK_ATTEST_CHAIN, checks.CHECK_SBOM,
      checks.CHECK_REPRO, checks.CHECK_EXPECTED_FILES]),
    ("digest-mismatch",
     {checks.CHECK_SIGNATURES},
     {checks.CHECK_SIGNATURES: "does not match"},
     [checks.CHECK_ATTEST_CHAIN, checks.CHECK_SBOM,
      checks.CHECK_REPRO, checks.CHECK_EXPECTED_FILES]),
    ("bad-repro",
     {checks.CHECK_REPRO},
     {checks.CHECK_REPRO: "score 0.5 < threshold 1.0"},
     [checks.CHECK_ATTEST_CHAIN, checks.CHECK_SBOM,
      checks.CHECK_SIGNATURES, checks.CHECK_EXPECTED_FILES]),
    # A missing artifact trips BOTH its own check and the signature
    # check (no artifact -> no bound signature possible).
    ("missing-artifact",
     {checks.CHECK_SIGNATURES, checks.CHECK_EXPECTED_FILES},
     {checks.CHECK_EXPECTED_FILES: f"{BZIMAGE}: missing",
      checks.CHECK_SIGNATURES: f"{BZIMAGE}: missing"},
     [checks.CHECK_ATTEST_CHAIN, checks.CHECK_SBOM,
      checks.CHECK_REPRO]),
])
def test_bad_variant_fails_exactly_its_intended_checks(
        variant_name: str, failing: set,
        evidence_snippets: Dict[str, str], passing: List[str]):
    build_dir = os.path.join(BAD_DIR, variant_name)
    findings = _findings(build_dir)
    for cid in checks.CHECK_IDS:
        if cid in failing:
            assert findings[cid].status is Status.FAIL, (  # type: ignore[union-attr]
                variant_name, cid)
            assert evidence_snippets[cid] in _text(findings, cid), (
                variant_name, cid)
        else:
            assert cid in passing
            assert findings[cid].status is Status.PASS, (  # type: ignore[union-attr]
                variant_name, cid, findings[cid].evidence)  # type: ignore[union-attr]


# ----------------------------------------------------------------------
# the bad attest logs: each verifies with exactly the intended failure
# ----------------------------------------------------------------------

@pytest.mark.parametrize("log_name, reason, bad_seq", [
    ("tampered.log", "self-hash mismatch", 2),
    ("gapped.log", "seq not contiguous", 3),
    ("broken-linkage.log", "prev_hash linkage broken", 2),
])
def test_bad_attest_log_fails_exactly_where_intended(
        log_name: str, reason: str, bad_seq: int):
    path = os.path.join(ATT_DIR, log_name)
    report = attest.verify_attest_text(open(path, encoding="utf-8").read())
    assert report.ok is False
    assert report.reason == reason
    assert report.first_bad_seq == bad_seq

    # And the same log makes the attest check FAIL through the check
    # layer, naming the log, the line, and the reason.
    finding = run_checks(_ctx(GOOD_DIR, attest_path=path),
                         [checks.CHECK_ATTEST_CHAIN])[0]
    assert finding.status is Status.FAIL
    text = " | ".join(finding.evidence)
    assert reason in text
    assert f"seq {bad_seq}" in text
    assert path in text


def test_tampered_log_differs_from_good_only_in_record_two():
    """The tamper is exactly one JSON field — no renumbering."""
    good_lines = open(GOOD_LOG, encoding="utf-8").read().splitlines()
    bad_lines = open(os.path.join(ATT_DIR, "tampered.log"),
                     encoding="utf-8").read().splitlines()
    assert len(good_lines) == 3 and len(bad_lines) == 2
    g1, g2 = (json.loads(x) for x in good_lines[:2])
    t1, t2 = (json.loads(x) for x in bad_lines[:2])
    assert t1 == g1
    assert t2["seq"] == g2["seq"] and t2["ts"] == g2["ts"]
    assert t2["data"] != g2["data"]
    assert t2["hash"] == g2["hash"]  # the hash still says what WAS signed


# ----------------------------------------------------------------------
# the fixtures work at the CLI boundary (the real witness surface)
# ----------------------------------------------------------------------

def test_cli_check_good_fixture_exits_zero(capsys):
    rc = main(["check", "--build", GOOD_DIR, "--attest", GOOD_LOG])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verdict: PASS" in out


def test_cli_check_bad_fixture_exits_one_with_finding(capsys):
    rc = main(["check", "--build", os.path.join(BAD_DIR, "digest-mismatch"),
               "--attest", GOOD_LOG])
    assert rc == 1
    out = capsys.readouterr().out
    assert "verdict: FAIL (1/5 checks failed)" in out
    assert f"[FAIL] {checks.CHECK_SIGNATURES}" in out
    assert "does not match" in out
