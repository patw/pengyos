"""Tests for the five check implementations (checks_impl.py).

Each check is tested individually (its pluggable function contract)
and the registry is pinned against the definitions in checks.py.
Fixtures: ``tests/fixtures_build.py`` builds a fully good build dir
in tmp_path; bad variants mutate one conventional file at a time.
"""

from __future__ import annotations

import json
import os

import pytest

from pengyos_verifier import attest, checks, signature_format
from pengyos_verifier.checks_impl import (
    CheckContext,
    CHECK_RUNNERS,
    check_attest_chain,
    check_expected_files,
    check_repro,
    check_sbom,
    check_signatures,
    run_checks,
)
from pengyos_verifier.model import Finding, Status
from tests.fixtures_build import (
    BZIMAGE_BYTES,
    GOOD_CDX,
    GOOD_REPRO,
    GOOD_SPDX,
    ROOTFS_BYTES,
    build_good_dir,
    make_ctx,
    make_sig_bytes,
)

TS1 = "2026-09-16T00:00:00Z"
TS2 = "2026-09-16T00:05:00Z"


def attest_line(seq, ts, type_, data, prev_hash) -> str:
    """One schema-valid, self-hashed attest log line."""
    rec = {"seq": seq, "ts": ts, "type": type_, "data": data,
           "prev_hash": prev_hash}
    rec["hash"] = attest.record_hash(rec)
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def good_attest_text() -> str:
    prev = attest.GENESIS_PREV_HASH
    lines = [
        attest_line(1, TS1, "build", {"commit": "abc123"}, prev),
    ]
    prev = json.loads(lines[0])["hash"]
    lines.append(attest_line(2, TS2, "sbom", {"packages": 7}, prev))
    return "\n".join(lines) + "\n"


def write_attest(ctx: CheckContext, text: str) -> None:
    with open(ctx.attest_path, "w", encoding="utf-8") as fh:
        fh.write(text)


def evidence_text(finding: Finding) -> str:
    return " | ".join(finding.evidence)


def is_fail(finding: Finding, check_id: str) -> bool:
    assert finding.check == check_id
    assert finding.status is Status.FAIL
    return True


def is_pass(finding: Finding, check_id: str) -> bool:
    assert finding.check == check_id
    assert finding.status is Status.PASS
    return True


# ----------------------------------------------------------------------
# Registry / pluggability
# ----------------------------------------------------------------------

def test_registry_covers_every_definition_in_report_order():
    assert list(CHECK_RUNNERS) == list(checks.CHECK_IDS)


def test_registry_functions_carry_their_check_id():
    for check_id, func in CHECK_RUNNERS.items():
        assert func.check_id == check_id


def test_run_checks_returns_one_finding_per_check_in_order(tmp_path):
    ctx = make_ctx(tmp_path, good_attest_text())
    findings = run_checks(ctx)
    assert [f.check for f in findings] == list(checks.CHECK_IDS)
    assert all(isinstance(f, Finding) for f in findings)


def test_run_checks_can_run_a_subset(tmp_path):
    ctx = make_ctx(tmp_path, good_attest_text())
    findings = run_checks(ctx, [checks.CHECK_SBOM, checks.CHECK_REPRO])
    assert [f.check for f in findings] == \
        [checks.CHECK_SBOM, checks.CHECK_REPRO]


def test_run_checks_rejects_unknown_ids(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(KeyError):
        run_checks(ctx, ["nope"])


def test_every_check_runs_individually_and_reports_its_own_id(tmp_path):
    ctx = make_ctx(tmp_path, good_attest_text())
    for func in CHECK_RUNNERS.values():
        finding = func(ctx)
        assert isinstance(finding, Finding)
        assert finding.check == func.check_id
        assert finding.status in (Status.PASS, Status.FAIL)


# ----------------------------------------------------------------------
# 1. attest-chain
# ----------------------------------------------------------------------

def test_attest_good_chain_passes(tmp_path):
    ctx = make_ctx(tmp_path, good_attest_text())
    finding = check_attest_chain(ctx)
    assert is_pass(finding, checks.CHECK_ATTEST_CHAIN)
    assert "2 records" in evidence_text(finding)


def test_attest_missing_log_is_valid_empty_chain(tmp_path):
    ctx = make_ctx(tmp_path, attest_text=None)
    finding = check_attest_chain(ctx)
    assert is_pass(finding, checks.CHECK_ATTEST_CHAIN)
    assert "empty chain" in finding.message


def test_attest_empty_log_is_valid_empty_chain(tmp_path):
    ctx = make_ctx(tmp_path, "")
    finding = check_attest_chain(ctx)
    assert is_pass(finding, checks.CHECK_ATTEST_CHAIN)


def test_attest_blank_lines_are_skipped(tmp_path):
    ctx = make_ctx(tmp_path, "\n" + good_attest_text() + "\n")
    assert is_pass(check_attest_chain(ctx), checks.CHECK_ATTEST_CHAIN)


def test_attest_non_json_line_fails(tmp_path):
    ctx = make_ctx(tmp_path, good_attest_text() + "not json\n")
    finding = check_attest_chain(ctx)
    assert is_fail(finding, checks.CHECK_ATTEST_CHAIN)
    assert "not valid JSON" in evidence_text(finding)
    assert "line 3" in evidence_text(finding)


def test_attest_self_hash_mismatch_fails(tmp_path):
    line1 = good_attest_text().splitlines()[0]
    prev = json.loads(line1)["hash"]
    # Tampered data without recomputing the hash.
    bad = json.loads(attest_line(2, TS2, "sbom", {"packages": 7}, prev))
    bad["data"] = {"packages": 999}
    ctx = make_ctx(tmp_path, line1 + "\n" + json.dumps(bad, sort_keys=True)
                   + "\n")
    finding = check_attest_chain(ctx)
    assert is_fail(finding, checks.CHECK_ATTEST_CHAIN)
    assert "self-hash mismatch" in evidence_text(finding)
    assert "line 2" in evidence_text(finding)


def test_attest_broken_linkage_fails(tmp_path):
    line1 = good_attest_text().splitlines()[0]
    # Correct self-hash for a WRONG prev_hash -> linkage (not
    # self-hash) is the failure the walk reports.
    bad = attest_line(2, TS2, "sbom", {"packages": 7}, "f" * 64)
    ctx = make_ctx(tmp_path, line1 + "\n" + bad + "\n")
    finding = check_attest_chain(ctx)
    assert is_fail(finding, checks.CHECK_ATTEST_CHAIN)
    assert "prev_hash linkage broken" in evidence_text(finding)


def test_attest_seq_gap_fails(tmp_path):
    prev = attest.GENESIS_PREV_HASH
    line1 = attest_line(1, TS1, "build", {}, prev)
    prev = json.loads(line1)["hash"]
    line3 = attest_line(3, TS2, "sbom", {}, prev)  # skips seq 2
    ctx = make_ctx(tmp_path, line1 + "\n" + line3 + "\n")
    finding = check_attest_chain(ctx)
    assert is_fail(finding, checks.CHECK_ATTEST_CHAIN)
    assert "seq not contiguous" in evidence_text(finding)


def test_attest_schema_error_fails(tmp_path):
    bad = json.loads(attest_line(1, TS1, "build", {},
                                 attest.GENESIS_PREV_HASH))
    bad["ts"] = "2026-09-16 00:00:00"  # not the canonical form
    bad["hash"] = attest.record_hash(bad)  # self-hash still correct
    ctx = make_ctx(tmp_path, json.dumps(bad, sort_keys=True) + "\n")
    finding = check_attest_chain(ctx)
    assert is_fail(finding, checks.CHECK_ATTEST_CHAIN)
    assert "schema:" in evidence_text(finding)


def test_attest_unreadable_log_fails(tmp_path):
    ctx = make_ctx(tmp_path, "")
    with open(ctx.attest_path, "wb") as fh:
        fh.write(b"\xff\xfe\x00")  # invalid UTF-8
    finding = check_attest_chain(ctx)
    assert is_fail(finding, checks.CHECK_ATTEST_CHAIN)
    assert "could not be read" in finding.message


# ----------------------------------------------------------------------
# 2. sbom
# ----------------------------------------------------------------------

def test_sbom_good_spdx_passes(tmp_path):
    ctx = make_ctx(tmp_path)
    finding = check_sbom(ctx)
    assert is_pass(finding, checks.CHECK_SBOM)
    assert "format=spdx" in evidence_text(finding)


def test_sbom_good_cyclonedx_passes(tmp_path):
    ctx = make_ctx(tmp_path)
    spdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0])
    cdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[1])
    os.remove(spdx_path)
    with open(cdx_path, "w", encoding="utf-8") as fh:
        json.dump(GOOD_CDX, fh)
    finding = check_sbom(ctx)
    assert is_pass(finding, checks.CHECK_SBOM)
    assert "format=cyclonedx" in evidence_text(finding)


def test_sbom_missing_fails_listing_both_names(tmp_path):
    ctx = make_ctx(tmp_path)
    cdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[1])
    with open(cdx_path, "w", encoding="utf-8") as fh:
        json.dump(GOOD_CDX, fh)
    for name in checks.SBOM_FILENAMES:
        os.remove(os.path.join(ctx.build_dir, name))
    finding = check_sbom(ctx)
    assert is_fail(finding, checks.CHECK_SBOM)
    for name in checks.SBOM_FILENAMES:
        assert name in evidence_text(finding)


def test_sbom_collision_fails_listing_both(tmp_path):
    ctx = make_ctx(tmp_path)
    cdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[1])
    with open(cdx_path, "w", encoding="utf-8") as fh:
        json.dump(GOOD_CDX, fh)
    finding = check_sbom(ctx)
    assert is_fail(finding, checks.CHECK_SBOM)
    assert "multiple SBOMs" in finding.message
    for name in checks.SBOM_FILENAMES:
        assert name in evidence_text(finding)


def test_sbom_invalid_json_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    spdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0])
    with open(spdx_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    finding = check_sbom(ctx)
    assert is_fail(finding, checks.CHECK_SBOM)
    assert "not valid JSON" in finding.message


def test_sbom_wrong_spdx_version_fails_with_evidence(tmp_path):
    ctx = make_ctx(tmp_path)
    doc = dict(GOOD_SPDX, spdxVersion="SPDX-2.2")
    spdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0])
    with open(spdx_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    finding = check_sbom(ctx)
    assert is_fail(finding, checks.CHECK_SBOM)
    assert "SPDX-2.2" in evidence_text(finding)


def test_sbom_unrecognizable_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    spdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0])
    with open(spdx_path, "w", encoding="utf-8") as fh:
        json.dump({"hello": "world"}, fh)
    finding = check_sbom(ctx)
    assert is_fail(finding, checks.CHECK_SBOM)
    assert "not a recognizable SBOM" in evidence_text(finding)


def test_sbom_a_directory_counts_as_missing(tmp_path):
    ctx = make_ctx(tmp_path)
    cdx_path = os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[1])
    with open(cdx_path, "w", encoding="utf-8") as fh:
        json.dump(GOOD_CDX, fh)
    for name in checks.SBOM_FILENAMES:
        p = os.path.join(ctx.build_dir, name)
        os.remove(p)
        os.makedirs(p)
    finding = check_sbom(ctx)
    assert is_fail(finding, checks.CHECK_SBOM)


# ----------------------------------------------------------------------
# 3. signatures
# ----------------------------------------------------------------------

def test_signatures_good_passes_with_both_artifacts(tmp_path):
    ctx = make_ctx(tmp_path)
    finding = check_signatures(ctx)
    assert is_pass(finding, checks.CHECK_SIGNATURES)
    for artifact in checks.EXPECTED_ARTIFACTS:
        assert artifact in evidence_text(finding)


def test_signatures_missing_sig_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    victim = checks.EXPECTED_ARTIFACTS[0]
    os.remove(os.path.join(ctx.build_dir, victim + checks.SIGNATURE_SUFFIX))
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert "missing" in evidence_text(finding)
    assert victim + checks.SIGNATURE_SUFFIX in evidence_text(finding)


def test_signatures_missing_artifact_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    victim = checks.EXPECTED_ARTIFACTS[1]
    art = os.path.join(ctx.build_dir, victim)
    os.remove(art)
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert victim in evidence_text(finding)


def test_signatures_both_missing_reports_both(tmp_path):
    ctx = make_ctx(tmp_path)
    for artifact in checks.EXPECTED_ARTIFACTS:
        for suffix in ("", checks.SIGNATURE_SUFFIX):
            os.remove(os.path.join(ctx.build_dir, artifact + suffix))
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    # 2 artifacts x (artifact missing + sig missing) = 4 problems.
    assert len(finding.evidence) == 4
    assert "4 signature problem(s)" in finding.message


def test_signatures_bad_magic_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    sig = os.path.join(ctx.build_dir,
                       checks.EXPECTED_ARTIFACTS[0] + checks.SIGNATURE_SUFFIX)
    raw = make_sig_bytes(ROOTFS_BYTES)
    raw = raw.replace(b"pengyos-sig-v1", b"evil-sig-v1", 1)
    with open(sig, "wb") as fh:
        fh.write(raw)
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert "bad magic" in evidence_text(finding)


def test_signatures_short_body_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    sig = os.path.join(ctx.build_dir,
                       checks.EXPECTED_ARTIFACTS[1] + checks.SIGNATURE_SUFFIX)
    with open(sig, "wb") as fh:
        fh.write(make_sig_bytes(BZIMAGE_BYTES, body=b"\x00" * 63))
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert "64 bytes" in evidence_text(finding)


def test_signatures_no_newline_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    sig = os.path.join(ctx.build_dir,
                       checks.EXPECTED_ARTIFACTS[0] + checks.SIGNATURE_SUFFIX)
    raw = make_sig_bytes(ROOTFS_BYTES)
    with open(sig, "wb") as fh:
        fh.write(raw.replace(b"\n", b" ", 1))
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert "missing newline" in evidence_text(finding)


def test_signatures_digest_mismatch_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    sig = os.path.join(ctx.build_dir,
                       checks.EXPECTED_ARTIFACTS[0] + checks.SIGNATURE_SUFFIX)
    # A well-formed sig computed for DIFFERENT bytes.
    with open(sig, "wb") as fh:
        fh.write(make_sig_bytes(ROOTFS_BYTES + b"tampered"))
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert "does not match" in evidence_text(finding)


def test_signatures_reports_every_problem_not_just_first(tmp_path):
    ctx = make_ctx(tmp_path)
    for artifact in checks.EXPECTED_ARTIFACTS:
        sig = os.path.join(ctx.build_dir,
                           artifact + checks.SIGNATURE_SUFFIX)
        with open(sig, "wb") as fh:
            fh.write(b"garbage")
    finding = check_signatures(ctx)
    assert is_fail(finding, checks.CHECK_SIGNATURES)
    assert len(finding.evidence) == 2


# ----------------------------------------------------------------------
# 4. repro-score
# ----------------------------------------------------------------------

def test_repro_good_passes(tmp_path):
    ctx = make_ctx(tmp_path)
    finding = check_repro(ctx)
    assert is_pass(finding, checks.CHECK_REPRO)
    assert "score=1.0" in evidence_text(finding)


def test_repro_missing_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    os.remove(os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME))
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert checks.REPRO_REPORT_FILENAME in evidence_text(finding)


def test_repro_below_default_threshold_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": 0.9, "identical": False}, fh)
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert "0.9" in evidence_text(finding) and "1.0" in evidence_text(finding)


def test_repro_threshold_is_context_configured(tmp_path):
    ctx = make_ctx(tmp_path, threshold=0.5)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": 0.9, "identical": False}, fh)
    finding = check_repro(ctx)
    assert is_pass(finding, checks.CHECK_REPRO)


def test_repro_identical_zero_scores_still_pass(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": 1, "identical": True}, fh)  # int 1 is a number
    assert is_pass(check_repro(ctx), checks.CHECK_REPRO)


def test_repro_invalid_json_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("nope")
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert "not valid JSON" in evidence_text(finding)


def test_repro_bool_score_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": True, "identical": True}, fh)
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert "score must be a number" in evidence_text(finding)


def test_repro_out_of_range_score_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": 1.2, "identical": True}, fh)
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert "outside [0, 1]" in evidence_text(finding)


def test_repro_missing_identical_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": 1.0}, fh)
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert "identical" in evidence_text(finding)


def test_repro_differing_must_be_list(tmp_path):
    ctx = make_ctx(tmp_path)
    path = os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"score": 0.5, "identical": False, "differing": "x"}, fh)
    finding = check_repro(ctx)
    assert is_fail(finding, checks.CHECK_REPRO)
    assert "differing must be a list" in evidence_text(finding)


# ----------------------------------------------------------------------
# 5. expected-files
# ----------------------------------------------------------------------

def test_expected_files_good_passes_with_sizes(tmp_path):
    ctx = make_ctx(tmp_path)
    finding = check_expected_files(ctx)
    assert is_pass(finding, checks.CHECK_EXPECTED_FILES)
    assert f"{len(ROOTFS_BYTES)} bytes" in evidence_text(finding)
    assert f"{len(BZIMAGE_BYTES)} bytes" in evidence_text(finding)


def test_expected_files_missing_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    victim = checks.EXPECTED_ARTIFACTS[0]
    os.remove(os.path.join(ctx.build_dir, victim))
    finding = check_expected_files(ctx)
    assert is_fail(finding, checks.CHECK_EXPECTED_FILES)
    assert victim in evidence_text(finding)
    assert "missing" in evidence_text(finding)


def test_expected_files_empty_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    victim = checks.EXPECTED_ARTIFACTS[1]
    with open(os.path.join(ctx.build_dir, victim), "wb") as fh:
        pass  # truncate to zero bytes
    finding = check_expected_files(ctx)
    assert is_fail(finding, checks.CHECK_EXPECTED_FILES)
    assert "empty" in evidence_text(finding)


def test_expected_files_directory_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    victim = checks.EXPECTED_ARTIFACTS[0]
    path = os.path.join(ctx.build_dir, victim)
    os.remove(path)
    os.makedirs(path)
    finding = check_expected_files(ctx)
    assert is_fail(finding, checks.CHECK_EXPECTED_FILES)
    assert "not a regular file" in evidence_text(finding)


# ----------------------------------------------------------------------
# All five together (the checks-level smoke; Report aggregation is the
# next subtask).
# ----------------------------------------------------------------------

def test_all_checks_pass_on_a_good_build(tmp_path):
    ctx = make_ctx(tmp_path, good_attest_text())
    findings = run_checks(ctx)
    assert len(findings) == len(checks.CHECK_IDS)
    assert all(f.status is Status.PASS for f in findings)


def test_fail_findings_always_carry_evidence(tmp_path):
    # Break one thing per check and confirm the model's invariant holds
    # through the real implementations.
    ctx = make_ctx(tmp_path, "garbage\n")
    os.remove(os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0]))
    os.remove(os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME))
    for f in run_checks(ctx):
        if f.status is Status.FAIL:
            assert f.evidence, f"FAIL without evidence: {f}"
