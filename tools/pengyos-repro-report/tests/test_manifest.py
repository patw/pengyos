"""Tests for build-manifest.json consumption and the images/ walk fallback.

These exercise :mod:`pengyos_repro_report.core`'s source resolution: a
``build-manifest.json`` (artifact name -> sha256) is the primary source, and a
content-hash walk of ``images/`` is the fallback.

No network, root, or external tools — only tmp files and the stdlib.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from pengyos_repro_report import core


def _sha(data: bytes) -> str:
    """SHA-256 hex of ``data`` (a realistic build-manifest digest)."""
    return hashlib.sha256(data).hexdigest()


def _write(path, data: bytes = b"data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _write_manifest(path, mapping: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


# ---- load_manifest ---------------------------------------------------------


def test_load_manifest_object_shape(tmp_path):
    manifest = _write_manifest(
        tmp_path / "build-manifest.json",
        {"images/kernel.img": _sha(b"k"), "images/initrd.img": _sha(b"i")},
    )
    loaded = core.load_manifest(str(manifest))
    assert loaded == {"images/kernel.img": _sha(b"k"), "images/initrd.img": _sha(b"i")}


def test_load_manifest_list_of_records_shape(tmp_path):
    manifest = _write_manifest(
        tmp_path / "build-manifest.json",
        [{"name": "images/kernel.img", "sha256": _sha(b"k")}],
    )
    loaded = core.load_manifest(str(manifest))
    assert loaded == {"images/kernel.img": _sha(b"k")}


def test_load_manifest_invalid_json_raises(tmp_path):
    bad = _write(tmp_path / "build-manifest.json", b"{not json")
    with pytest.raises(core.ReproReportError, match="invalid JSON"):
        core.load_manifest(str(bad))


def test_load_manifest_missing_keys_raises(tmp_path):
    manifest = _write_manifest(
        tmp_path / "build-manifest.json",
        [{"name": "images/kernel.img"}],  # no sha256
    )
    with pytest.raises(core.ReproReportError, match="missing"):
        core.load_manifest(str(manifest))


def test_load_manifest_unsupported_shape_raises(tmp_path):
    manifest = _write(tmp_path / "build-manifest.json", b'["not", "an", "object"]')
    with pytest.raises(core.ReproReportError, match="unsupported"):
        core.load_manifest(str(manifest))


# ---- resolve_source --------------------------------------------------------


def test_resolve_source_prefers_manifest_over_images_walk(tmp_path):
    # A dir with BOTH a manifest and an images/ dir: the manifest wins. The
    # extra image file and the tampered content are invisible to the diff.
    build = tmp_path / "a"
    _write_manifest(build / "build-manifest.json", {"images/f.bin": _sha(b"A")})
    _write(build / "images" / "f.bin", b"TAMPERED")  # differs from manifest
    _write(build / "images" / "other.bin", b"extra")  # not in manifest

    resolved = core.resolve_source(str(build))
    assert resolved == {"images/f.bin": _sha(b"A")}


def test_resolve_source_falls_back_to_images_walk(tmp_path):
    # No manifest -> content-hash walk of images/.
    build = tmp_path / "a"
    _write(build / "images" / "f.bin", b"data")
    _write(build / "images" / "sub" / "g.bin", b"nested")

    resolved = core.resolve_source(str(build))
    assert resolved == {
        "images/f.bin": _sha(b"data"),
        "images/sub/g.bin": _sha(b"nested"),
    }


def test_resolve_source_treats_json_file_as_manifest(tmp_path):
    manifest = _write_manifest(
        tmp_path / "custom-manifest.json",
        {"images/f.bin": _sha(b"A")},
    )
    resolved = core.resolve_source(str(manifest))
    assert resolved == {"images/f.bin": _sha(b"A")}


def test_resolve_source_dir_without_manifest_or_images_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(core.ReproReportError, match="no .*manifest"):
        core.resolve_source(str(empty))


def test_resolve_source_missing_input_raises(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(core.ReproReportError, match="not found"):
        core.resolve_source(str(missing))


def test_manifest_from_build_dir_none_when_absent(tmp_path):
    build = tmp_path / "a"
    build.mkdir()
    assert core.manifest_from_build_dir(str(build)) is None


def test_walk_images_none_when_no_images_dir(tmp_path):
    build = tmp_path / "a"
    build.mkdir()
    assert core.walk_images(str(build)) is None


# ---- compare_sources -------------------------------------------------------


def test_compare_sources_manifest_identical(tmp_path):
    a = _write_manifest(
        tmp_path / "a.json",
        {"images/kernel.img": _sha(b"k"), "images/initrd.img": _sha(b"i")},
    )
    b = _write_manifest(
        tmp_path / "b.json",
        {"images/kernel.img": _sha(b"k"), "images/initrd.img": _sha(b"i")},
    )
    comparison = core.compare_sources(str(a), str(b))
    assert comparison.score() == 1.0
    assert all(a.identical for a in comparison.artifacts)
    assert comparison.names == ["images/initrd.img", "images/kernel.img"]


def test_compare_sources_manifest_differring_bytes_is_real(tmp_path):
    a = _write_manifest(
        tmp_path / "a.json",
        {"images/f.bin": _sha(b"original")},
    )
    b = _write_manifest(
        tmp_path / "b.json",
        {"images/f.bin": _sha(b"tampered")},
    )
    comparison = core.compare_sources(str(a), str(b))
    assert comparison.score() < 1.0
    (artifact,) = comparison.artifacts
    # Manifest-only differing digests (no bytes to inspect) are a real difference.
    assert artifact.status == core.STATUS_REAL
    assert artifact.normalized is False


def test_compare_sources_manifest_missing_on_one_side(tmp_path):
    a = _write_manifest(
        tmp_path / "a.json",
        {"images/only_a.bin": _sha(b"A")},
    )
    b = _write_manifest(
        tmp_path / "b.json",
        {"images/only_b.bin": _sha(b"B")},
    )
    comparison = core.compare_sources(str(a), str(b))
    by_name = {a.name: a for a in comparison.artifacts}
    assert by_name["images/only_a.bin"].status == core.STATUS_MISSING_A
    assert by_name["images/only_b.bin"].status == core.STATUS_MISSING_B


def test_compare_sources_manifest_primary_beats_walk(tmp_path):
    # Side a has a manifest + a divergent image file; side b is the same manifest.
    # The manifest is authoritative: the extra image file is not compared.
    build_a = tmp_path / "a"
    _write_manifest(build_a / "build-manifest.json", {"images/f.bin": _sha(b"A")})
    _write(build_a / "images" / "f.bin", b"TAMPERED")  # ignored (manifest wins)
    _write(build_a / "images" / "ghost.bin", b"ghost")  # ignored (not in manifest)

    build_b = tmp_path / "b"
    _write_manifest(build_b / "build-manifest.json", {"images/f.bin": _sha(b"A")})

    comparison = core.compare_sources(str(build_a), str(build_b))
    assert comparison.score() == 1.0
    assert comparison.names == ["images/f.bin"]
