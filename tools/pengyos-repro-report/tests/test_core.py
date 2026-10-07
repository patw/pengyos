"""Tests for pengyos_repro_report.core."""

from __future__ import annotations

import json
import os

import pytest

from pengyos_repro_report import core


def _write(path, text=b"hello world\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    return path


def test_identical_dirs_score_one(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "images" / "kernel.img", b"same-bytes")
    _write(a / "images" / "initrd.img", b"payload-1234")
    _write(b / "images" / "kernel.img", b"same-bytes")
    _write(b / "images" / "initrd.img", b"payload-1234")

    comparison = core.compare_dirs(str(a), str(b))
    assert comparison.score() == 1.0
    assert all(a.identical for a in comparison.artifacts)
    assert comparison.names == ["images/initrd.img", "images/kernel.img"]


def test_differing_bytes_classified_real(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "artifact.bin", b"original")
    _write(b / "artifact.bin", b"tampered-bytes")

    comparison = core.compare_dirs(str(a), str(b))
    assert comparison.score() < 1.0
    (artifact,) = comparison.artifacts
    # A byte difference with no known non-determinism is a *real* difference.
    assert artifact.status == core.STATUS_REAL
    assert artifact.hash_a != artifact.hash_b
    assert artifact.normalized is False
    assert not artifact.matching


def test_missing_in_a_and_b(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "only_a.bin", b"A")
    _write(b / "only_b.bin", b"B")

    comparison = core.compare_dirs(str(a), str(b))
    by_name = {a.name: a for a in comparison.artifacts}
    assert by_name["only_a.bin"].status == core.STATUS_MISSING_A
    assert by_name["only_b.bin"].status == core.STATUS_MISSING_B


def test_score_zero_when_no_artifacts():
    # Empty comparison scores 1.0 (nothing differs); an all-missing set scores 0.
    assert core.Comparison().score() == 1.0


def test_build_report_schema_and_ordering(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "z.bin", b"z")
    _write(a / "a.bin", b"a")
    _write(b / "z.bin", b"z")
    _write(b / "a.bin", b"a")

    report = core.build_report(core.compare_dirs(str(a), str(b)))
    assert report["schema"] == core.SCHEMA_VERSION
    assert report["score"] == 1.0
    # Deterministic ordering by name.
    assert [art["name"] for art in report["artifacts"]] == ["a.bin", "z.bin"]
    assert report["summary"]["total"] == 2
    assert report["summary"]["matching"] == 2
    assert report["summary"]["counts"][core.STATUS_IDENTICAL] == 2


def test_roundtrip_write_load(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "f.bin", b"data")
    _write(b / "f.bin", b"data")

    report = core.build_report(core.compare_dirs(str(a), str(b)))
    out = tmp_path / "nested" / "images" / "repro-report.json"
    written = core.write_report(report, str(out))
    assert written == str(out)
    assert out.exists()

    loaded = core.load_report(str(out))
    assert loaded["score"] == report["score"]
    assert core.compute_score(loaded) == 1.0
    assert core.valid_artifacts(loaded["artifacts"])


def test_summarizes_counts():
    summary = core.summarize(core.Comparison())
    assert "score=1.0000" in summary
    assert "total=0" in summary


def test_witness_path_shape():
    """The conventional witness path is <build>/images/repro-report.json."""
    expected = os.path.join("build", core.IMAGES_DIRNAME, core.REPORT_FILENAME)
    assert core.witness_path("build") == expected
    # A nested build dir is honoured verbatim (no path normalisation surprises).
    assert core.witness_path("a/b/c") == os.path.join("a", "b", "c", "images", "repro-report.json")
