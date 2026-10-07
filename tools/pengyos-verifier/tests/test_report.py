"""Tests for report aggregation and the pretty renderer.

Covers the "Aggregate into a report + non-zero exit on any fail"
subtask: ``build_report`` folds the per-check findings into a
self-describing ``Report`` whose verdict/exit code reflect any failure,
and ``Report.render`` produces the human-readable report the ``check``
command prints. Everything is offline (fixtures in ``tests/fixtures_build.py``).
"""

from __future__ import annotations

import json
import os

import pytest

from pengyos_verifier import attest, checks
from pengyos_verifier.checks_impl import build_report
from pengyos_verifier.model import Finding, Report, Status
from tests.fixtures_build import make_ctx

from conftest import make_chain


def attest_path_of(ctx) -> str:
    return ctx.attest_path


# ----------------------------------------------------------------------
# build_report
# ----------------------------------------------------------------------

def test_build_report_good_passes_all(tmp_path):
    ctx = make_ctx(tmp_path, attest_text=make_chain())
    report = build_report(ctx)
    assert isinstance(report, Report)
    assert report.ok
    assert report.exit_code == 0
    assert len(report.findings) == len(checks.CHECK_IDS)
    assert all(f.ok for f in report.findings)


def test_build_report_labels_are_self_describing(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    report = build_report(ctx)
    # The report names exactly what the caller asked to inspect.
    assert report.build == ctx.build_dir
    assert report.attest == ctx.attest_path
    assert report.build in report.render()
    assert report.attest in report.render()


def test_build_report_findings_are_in_report_order(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    report = build_report(ctx)
    assert [f.check for f in report.findings] == list(checks.CHECK_IDS)


def test_build_report_subset(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    report = build_report(ctx, check_ids=("sbom", "repro-score"))
    assert [f.check for f in report.findings] == ["sbom", "repro-score"]
    assert report.ok


def test_build_report_unknown_id_raises(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    with pytest.raises(KeyError):
        build_report(ctx, check_ids=("nope",))


def test_build_report_fail_on_missing_sbom(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    os.remove(os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0]))
    report = build_report(ctx)
    assert not report.ok
    assert report.exit_code == 1
    failed = {f.check: f for f in report.failed}
    assert set(failed) == {checks.CHECK_SBOM}
    # A FAIL finding carries evidence naming the missing file.
    assert failed[checks.CHECK_SBOM].evidence


def test_build_report_fail_on_broken_chain(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    # A single record whose self-hash is wrong breaks the chain.
    rec = {"seq": 1, "ts": "2026-09-16T00:00:00Z", "type": "build",
           "data": {}, "prev_hash": attest.GENESIS_PREV_HASH,
           "hash": "0" * 64}
    with open(ctx.attest_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    report = build_report(ctx)
    assert not report.ok
    assert report.exit_code == 1
    assert {f.check for f in report.failed} == {checks.CHECK_ATTEST_CHAIN}


def test_build_report_fail_on_low_repro(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="", threshold=1.0)
    low = json.dumps({"score": 0.5, "identical": False}) + "\n"
    with open(os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME),
              "w", encoding="utf-8") as fh:
        fh.write(low)
    report = build_report(ctx)
    assert not report.ok
    assert report.exit_code == 1
    assert {f.check for f in report.failed} == {checks.CHECK_REPRO}


def test_build_report_threshold_is_honored(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="", threshold=0.4)
    low = json.dumps({"score": 0.5, "identical": False}) + "\n"
    with open(os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME),
              "w", encoding="utf-8") as fh:
        fh.write(low)
    # 0.5 >= 0.4 passes; the same dir at the default 1.0 would fail.
    assert build_report(ctx).ok


def test_build_report_multiple_failures_all_reported(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    os.remove(os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0]))
    os.remove(os.path.join(ctx.build_dir, checks.REPRO_REPORT_FILENAME))
    report = build_report(ctx)
    assert not report.ok
    assert report.exit_code == 1
    assert {f.check for f in report.failed} == {
        checks.CHECK_SBOM, checks.CHECK_REPRO
    }
    # The checks that were fine still PASS.
    ok_ids = {f.check for f in report.findings if f.ok}
    assert checks.CHECK_ATTEST_CHAIN in ok_ids


# ----------------------------------------------------------------------
# Report.render
# ----------------------------------------------------------------------

def test_render_good(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    text = build_report(ctx).render()
    assert "PengyOS verifier report" in text
    assert "verdict: PASS (0/5 checks failed)" in text
    for cid in checks.CHECK_IDS:
        assert f"[PASS] {cid}" in text
    assert "[FAIL]" not in text


def test_render_fail_shows_evidence(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    os.remove(os.path.join(ctx.build_dir, checks.SBOM_FILENAMES[0]))
    text = build_report(ctx).render()
    assert "verdict: FAIL (1/5 checks failed)" in text
    assert f"[FAIL] {checks.CHECK_SBOM}" in text
    # Evidence is indented beneath its finding (deeper than the tag).
    lines = text.splitlines()
    fail_idx = next(i for i, l in enumerate(lines)
                    if f"[FAIL] {checks.CHECK_SBOM}" in l)
    assert lines[fail_idx + 1].startswith("         ")
    # The other checks still show as PASS.
    assert f"[PASS] {checks.CHECK_ATTEST_CHAIN}" in text


def test_render_matches_json_verdict(tmp_path):
    report = build_report(make_ctx(tmp_path, attest_text=""))
    pretty = report.render()
    data = json.loads(report.to_json())
    assert ("PASS" in pretty) == data["ok"]
    # Every finding present in JSON is present in the pretty report.
    for f in data["findings"]:
        assert f["check"] in pretty


def test_render_is_deterministic(tmp_path):
    ctx = make_ctx(tmp_path, attest_text="")
    a = build_report(ctx).render()
    b = build_report(ctx).render()
    assert a == b


def test_render_empty_report():
    report = Report(build="b", attest="", findings=())
    text = report.render()
    assert "verdict: PASS (0/0 checks failed)" in text
    assert "attest:  (none)" in text
