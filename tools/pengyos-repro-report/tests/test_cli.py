"""Tests for pengyos_repro_report.cli."""

from __future__ import annotations

import hashlib
import json

import pytest

from pengyos_repro_report import cli, core


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_manifest(path, mapping: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


def _make_build(tmp_path, name, files):
    build = tmp_path / name
    for rel, data in files.items():
        target = build / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return build


def test_diff_identical_writes_report_and_scores_one(tmp_path, capsys):
    a = _make_build(tmp_path, "a", {"images/kernel.img": b"same", "images/initrd.img": b"payload"})
    b = _make_build(tmp_path, "b", {"images/kernel.img": b"same", "images/initrd.img": b"payload"})

    out = a / "images" / "repro-report.json"
    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])

    assert rc == 0
    assert out.exists()
    report = json.loads(out.read_text())
    assert report["score"] == 1.0
    assert all(art["status"] == core.STATUS_IDENTICAL for art in report["artifacts"])

    printed = capsys.readouterr().out.strip()
    assert "score=1.0000" in printed


def test_diff_json_flag_prints_full_report(tmp_path, capsys):
    a = _make_build(tmp_path, "a", {"images/f.bin": b"x"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"x"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out), "--json"])
    assert rc == 0

    out_err = capsys.readouterr().out
    parsed = json.loads(out_err)
    assert parsed["schema"] == core.SCHEMA_VERSION
    assert "artifacts" in parsed


def test_diff_differring_bytes_marks_real(tmp_path, capsys):
    a = _make_build(tmp_path, "a", {"images/f.bin": b"original"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    assert rc == 0

    report = json.loads(out.read_text())
    assert report["score"] < 1.0
    assert report["artifacts"][0]["status"] == core.STATUS_REAL


def test_score_reads_report(tmp_path, capsys):
    a = _make_build(tmp_path, "a", {"images/f.bin": b"x"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"x"})
    out = a / "images" / "repro-report.json"
    cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])

    # Drain the diff output first.
    capsys.readouterr()
    rc = cli.main(["score", "--report", str(out)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "1.0000"


def test_diff_missing_dir_clean_error(tmp_path, capsys):
    a = _make_build(tmp_path, "a", {"images/f.bin": b"x"})
    missing = tmp_path / "does-not-exist"
    rc = cli.main(["diff", "--a", str(a), "--b", str(missing), "--out", str(tmp_path / "r.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    # No Python traceback.
    assert "Traceback" not in err


def test_score_missing_file_clean_error(tmp_path, capsys):
    rc = cli.main(["score", "--report", str(tmp_path / "nope.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "cannot read report" in err
    # No Python traceback.
    assert "Traceback" not in err


# ---- manifest-source inputs ------------------------------------------------


def test_diff_two_manifest_files_identical(tmp_path, capsys):
    a = _write_manifest(
        tmp_path / "a.json",
        {"images/kernel.img": _sha(b"k"), "images/initrd.img": _sha(b"i")},
    )
    b = _write_manifest(
        tmp_path / "b.json",
        {"images/kernel.img": _sha(b"k"), "images/initrd.img": _sha(b"i")},
    )
    out = tmp_path / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    assert rc == 0

    report = json.loads(out.read_text())
    assert report["score"] == 1.0
    assert all(art["status"] == core.STATUS_IDENTICAL for art in report["artifacts"])


def test_diff_two_manifest_files_differs_is_real(tmp_path, capsys):
    a = _write_manifest(tmp_path / "a.json", {"images/f.bin": _sha(b"original")})
    b = _write_manifest(tmp_path / "b.json", {"images/f.bin": _sha(b"changed")})
    out = tmp_path / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    assert rc == 0

    report = json.loads(out.read_text())
    assert report["score"] < 1.0
    # Manifest-only differing digests (no bytes to inspect) are a real difference.
    assert report["artifacts"][0]["status"] == core.STATUS_REAL


def test_diff_dir_uses_manifest_primary_over_images_walk(tmp_path, capsys):
    # Side a has a manifest AND a divergent image file; the manifest wins.
    build_a = tmp_path / "a"
    _write_manifest(build_a / "build-manifest.json", {"images/f.bin": _sha(b"A")})
    (build_a / "images").mkdir(parents=True)
    (build_a / "images" / "f.bin").write_bytes(b"TAMPERED")  # ignored
    (build_a / "images" / "ghost.bin").write_bytes(b"ghost")  # ignored

    build_b = tmp_path / "b"
    _write_manifest(build_b / "build-manifest.json", {"images/f.bin": _sha(b"A")})
    out = build_a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(build_a), "--b", str(build_b), "--out", str(out)])
    assert rc == 0

    report = json.loads(out.read_text())
    assert report["score"] == 1.0
    assert [art["name"] for art in report["artifacts"]] == ["images/f.bin"]


def test_diff_dir_falls_back_to_images_walk(tmp_path, capsys):
    # No manifest on either side -> content-hash walk of images/.
    build_a = _make_build(tmp_path, "a", {"images/f.bin": b"data"})
    build_b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = build_a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(build_a), "--b", str(build_b), "--out", str(out)])
    assert rc == 0

    report = json.loads(out.read_text())
    assert report["score"] < 1.0
    assert report["artifacts"][0]["status"] == core.STATUS_REAL


def test_diff_missing_input_clean_error(tmp_path, capsys):
    a = _make_build(tmp_path, "a", {"images/f.bin": b"x"})
    missing = tmp_path / "does-not-exist"
    rc = cli.main(["diff", "--a", str(a), "--b", str(missing), "--out", str(tmp_path / "r.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    # No Python traceback.
    assert "Traceback" not in err


# ---- default witness path (the path the witness reads) --------------------


def test_diff_defaults_to_witness_path(tmp_path, capsys):
    """With no ``--out``, ``diff`` writes to ``<build-a>/images/repro-report.json``.

    This is the crux of the witness contract: the report must land where the
    witness looks, with no explicit path from the caller.
    """
    a = _make_build(tmp_path, "a", {"images/f.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same"})

    rc = cli.main(["diff", "--a", str(a), "--b", str(b)])
    assert rc == 0

    witness = a / "images" / core.REPORT_FILENAME
    assert witness.exists(), "report was not written to the conventional witness path"
    report = json.loads(witness.read_text())
    assert report["score"] == 1.0
    assert [art["name"] for art in report["artifacts"]] == ["images/f.bin"]


def test_diff_witness_path_report_is_parseable_and_scores(tmp_path, capsys):
    """The report at the default witness path round-trips through ``score``."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same"})

    rc = cli.main(["diff", "--a", str(a), "--b", str(b)])
    assert rc == 0
    capsys.readouterr()

    witness = a / "images" / core.REPORT_FILENAME
    # The witness reads this exact path; reading the score back must work.
    rc = cli.main(["score", "--report", str(witness)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "1.0000"


def test_diff_explicit_out_overrides_default(tmp_path, capsys):
    """An explicit ``--out`` wins over the derived witness path."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same"})
    custom = tmp_path / "custom" / "report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(custom)])
    assert rc == 0

    assert custom.exists()
    # The default witness path was NOT written.
    assert not (a / "images" / core.REPORT_FILENAME).exists()


def test_diff_manifest_input_without_out_is_clean_error(tmp_path, capsys):
    """A manifest-file ``--a`` has no conventional witness path, so ``--out``
    is required; omitting it is a clean non-zero exit (no traceback)."""
    a = _write_manifest(tmp_path / "a.json", {"images/f.bin": _sha(b"x")})
    b = _write_manifest(tmp_path / "b.json", {"images/f.bin": _sha(b"x")})

    rc = cli.main(["diff", "--a", str(a), "--b", str(b)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "--out" in err
    assert "Traceback" not in err


def test_diff_missing_a_without_out_is_clean_error(tmp_path, capsys):
    """A non-existent ``--a`` with no ``--out`` is a clean non-zero exit."""
    missing = tmp_path / "does-not-exist"
    b = _make_build(tmp_path, "b", {"images/f.bin": b"x"})
    rc = cli.main(["diff", "--a", str(missing), "--b", str(b)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


# ---- --strict: gate CI on reproducibility ---------------------------------
#
# ``--strict`` makes the CLI exit non-zero (a clean ReproReportError -> exit 1,
# no traceback) whenever the reproducibility score is below 1.0, so CI can fail
# a build on a genuine reproducibility failure. A score of 1.0 (identical, or
# differing only in a normalized field) still passes.


def test_diff_strict_passes_when_score_is_one(tmp_path, capsys):
    """``--strict`` with a perfect score exits 0."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out), "--strict"])
    assert rc == 0
    assert capsys.readouterr().err == ""  # no error on a passing build


def test_diff_strict_fails_when_score_below_one(tmp_path, capsys):
    """``--strict`` with a real difference exits 1 with a clean one-line error."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"original"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out), "--strict"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "below threshold" in err
    # No Python traceback.
    assert "Traceback" not in err


def test_diff_strict_still_writes_witness_report_on_failure(tmp_path, capsys):
    """Even when ``--strict`` fails, the report is written for the witness to read.

    The report is the artifact the witness consumes; the exit code is what gates
    CI. So a strict failure must not delete the report the witness reads.
    """
    a = _make_build(tmp_path, "a", {"images/f.bin": b"original"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out), "--strict"])
    assert rc == 1

    # The witness report is still on disk and parseable.
    assert out.exists()
    report = json.loads(out.read_text())
    assert report["score"] < 1.0
    assert report["artifacts"][0]["status"] == core.STATUS_REAL


def test_diff_non_strict_allows_score_below_one(tmp_path, capsys):
    """Without ``--strict``, a real difference still exits 0 (the report is the artifact)."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"original"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    assert rc == 0
    assert capsys.readouterr().err == ""


def test_diff_strict_passes_when_only_normalized_difference(tmp_path, capsys):
    """A difference explained by a known non-determinism still scores 1.0, so ``--strict`` passes.

    The two payloads differ only in a trailing ISO-8601 build timestamp, which
    the classification table normalizes away -> score 1.0.
    """
    a = _make_build(tmp_path, "a", {"images/f.bin": b"payload-2026-01-01T00:00:00"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"payload-2021-06-15T12:30:00"})
    out = a / "images" / "repro-report.json"

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out), "--strict"])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["score"] == 1.0
    assert report["artifacts"][0]["status"] == core.STATUS_NORMALIZED


def test_score_strict_fails_when_score_below_one(tmp_path, capsys):
    """``score --strict`` exits 1 when the report's score is below 1.0."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"original"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = a / "images" / "repro-report.json"
    cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    capsys.readouterr()  # drain the diff summary

    rc = cli.main(["score", "--report", str(out), "--strict"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "below threshold" in err
    assert "Traceback" not in err


def test_score_strict_passes_when_score_is_one(tmp_path, capsys):
    """``score --strict`` exits 0 when the report's score is 1.0."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same"})
    out = a / "images" / "repro-report.json"
    cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    capsys.readouterr()

    rc = cli.main(["score", "--report", str(out), "--strict"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "1.0000"


def test_score_non_strict_allows_score_below_one(tmp_path, capsys):
    """``score`` without ``--strict`` exits 0 and prints the score even when < 1."""
    a = _make_build(tmp_path, "a", {"images/f.bin": b"original"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"changed"})
    out = a / "images" / "repro-report.json"
    cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    capsys.readouterr()

    rc = cli.main(["score", "--report", str(out)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "0.0000"


def test_strict_flag_present_on_both_subparsers():
    """Both ``diff`` and ``score`` expose the ``--strict`` flag (argparse 3.9-3.14)."""
    parser = cli.build_parser()
    # argparse 3.14 dropped get_actions() and changed what add_subparsers()
    # returns; resolve subcommand names via the stable _actions list.
    actions = [a for a in parser._actions if type(a).__name__ == "_SubParsersAction"]
    assert actions, "expected a subparsers action"
    subparsers = actions[0].choices
    for name in ("diff", "score"):
        dests = {a.dest for a in subparsers[name]._actions}
        assert "strict" in dests, f"--strict flag missing on the {name} subcommand"
