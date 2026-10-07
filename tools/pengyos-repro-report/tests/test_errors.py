"""Error-handling contract: missing/unreadable manifests or dirs must produce a
clean, non-zero exit (no traceback).

These pin the behaviour that the spec calls out: a missing or unreadable
``build-manifest.json``, an unreadable ``images/`` directory, or a missing /
malformed report must surface as a single :class:`ReproReportError` that the CLI
maps to exit code 1 and a one-line message -- never a raw Python traceback.

No network, root, or external tools -- only tmp files, the stdlib, and a couple
of ``chmod`` calls. The ``chmod``-000 cases restore permissions in a ``finally``
so the tmp_path fixture can still tear the tree down.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat

import pytest

from pengyos_repro_report import cli, core


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_manifest(path, mapping: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


# ---- core.load_manifest: unreadable / malformed ----------------------------


def test_load_manifest_unreadable_file_raises(tmp_path):
    """A manifest that exists but cannot be read is a clean ReproReportError."""
    manifest = _write_manifest(tmp_path / "build-manifest.json", {"images/f.bin": _sha(b"x")})
    os.chmod(manifest, 0)
    try:
        with pytest.raises(core.ReproReportError, match="cannot read manifest"):
            core.load_manifest(str(manifest))
    finally:
        os.chmod(manifest, stat.S_IRUSR | stat.S_IWUSR)


def test_load_manifest_is_directory_raises(tmp_path):
    """A path named like a manifest that is actually a directory is clean."""
    weird = tmp_path / "build-manifest.json"
    weird.mkdir()
    with pytest.raises(core.ReproReportError, match="cannot read manifest"):
        core.load_manifest(str(weird))


def test_load_manifest_malformed_json_raises(tmp_path):
    """A syntactically invalid manifest is a clean ReproReportError."""
    bad = tmp_path / "build-manifest.json"
    bad.write_bytes(b"{not json")
    with pytest.raises(core.ReproReportError, match="invalid JSON"):
        core.load_manifest(str(bad))


# ---- core.walk_images / resolve_source: unreadable dir ---------------------


def test_walk_images_unreadable_dir_raises(tmp_path):
    """An existing but unreadable images/ dir raises instead of silently emptying."""
    build = tmp_path / "build"
    images = build / "images"
    images.mkdir(parents=True)
    (images / "f.bin").write_bytes(b"data")
    os.chmod(images, 0)
    try:
        with pytest.raises(core.ReproReportError, match="cannot read"):
            core.walk_images(str(build))
    finally:
        os.chmod(images, stat.S_IRWXU)


def test_resolve_source_unreadable_images_raises(tmp_path):
    """resolve_source surfaces an unreadable images/ walk as a clean error."""
    build = tmp_path / "build"
    images = build / "images"
    images.mkdir(parents=True)
    (images / "f.bin").write_bytes(b"data")
    os.chmod(images, 0)
    try:
        with pytest.raises(core.ReproReportError, match="cannot read"):
            core.resolve_source(str(build))
    finally:
        os.chmod(images, stat.S_IRWXU)


def test_resolve_source_manifest_beats_unreadable_images(tmp_path):
    """When the manifest is readable, an unreadable images/ dir is never walked.

    The primary source (manifest) wins, so the unreadable images/ subdir does
    not error -- it is simply not consulted. This keeps a build whose manifest
    is intact usable even if images/ is locked down.
    """
    build = tmp_path / "build"
    (build / "images").mkdir(parents=True)
    _write_manifest(build / "build-manifest.json", {"images/f.bin": _sha(b"A")})
    os.chmod(build / "images", 0)
    try:
        resolved = core.resolve_source(str(build))
    finally:
        os.chmod(build / "images", stat.S_IRWXU)
    assert resolved == {"images/f.bin": _sha(b"A")}


# ---- core.load_report: missing / unreadable / malformed --------------------


def test_load_report_missing_file_raises(tmp_path):
    """A missing report is a clean ReproReportError (not a FileNotFoundError escape)."""
    with pytest.raises(core.ReproReportError, match="cannot read report"):
        core.load_report(str(tmp_path / "nope.json"))


def test_load_report_malformed_raises(tmp_path):
    """A malformed report is a clean ReproReportError."""
    bad = tmp_path / "repro-report.json"
    bad.write_bytes(b"{not json")
    with pytest.raises(core.ReproReportError, match="invalid JSON"):
        core.load_report(str(bad))


def test_load_report_unreadable_file_raises(tmp_path):
    """An unreadable report is a clean ReproReportError."""
    report = tmp_path / "repro-report.json"
    report.write_bytes(b"{}")
    os.chmod(report, 0)
    try:
        with pytest.raises(core.ReproReportError, match="cannot read report"):
            core.load_report(str(report))
    finally:
        os.chmod(report, stat.S_IRUSR | stat.S_IWUSR)


# ---- CLI end to end: clean non-zero exit, no traceback ---------------------


def test_cli_unreadable_manifest_clean_error(tmp_path, capsys):
    manifest = _write_manifest(tmp_path / "a.json", {"images/f.bin": _sha(b"x")})
    os.chmod(manifest, 0)
    try:
        rc = cli.main(
            ["diff", "--a", str(manifest), "--b", str(manifest), "--out", str(tmp_path / "r.json")]
        )
    finally:
        os.chmod(manifest, stat.S_IRUSR | stat.S_IWUSR)

    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_cli_unreadable_dir_clean_error(tmp_path, capsys):
    build = tmp_path / "build"
    images = build / "images"
    images.mkdir(parents=True)
    (images / "f.bin").write_bytes(b"data")
    os.chmod(images, 0)
    try:
        rc = cli.main(["diff", "--a", str(build), "--b", str(build), "--out", str(tmp_path / "r.json")])
    finally:
        os.chmod(images, stat.S_IRWXU)

    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_cli_malformed_report_clean_error(tmp_path, capsys):
    bad = tmp_path / "repro-report.json"
    bad.write_bytes(b"{not json")
    rc = cli.main(["score", "--report", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_cli_missing_report_clean_error(tmp_path, capsys):
    rc = cli.main(["score", "--report", str(tmp_path / "nope.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err
