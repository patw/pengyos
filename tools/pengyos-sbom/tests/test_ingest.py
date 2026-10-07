"""Tests for legal-info ingestion + show-info merge into the component model.

Covers the single subtask: "Implement legal-info ingestion (manifest.csv) +
show-info (JSON) merge into an internal component model (name, version,
license, source, hash)."

All inputs come from committed fixtures under ``tests/fixtures`` -- no
network, no root. The fixture layout mirrors the *real* Buildroot 2026.02.3
``make legal-info`` / ``make show-info`` output (see the module docstrings in
``pengyos_sbom.legalinfo`` / ``pengyos_sbom.showinfo``).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from pengyos_sbom import legalinfo, showinfo as si_mod
from pengyos_sbom.errors import LegalInfoError, ShowInfoError
from pengyos_sbom.ingest import ingest
from pengyos_sbom.model import (
    SCOPE_BUILD,
    SCOPE_HOST,
    SCOPE_TARGET,
    Component,
    IngestResult,
)

FIX = Path(__file__).resolve().parent / "fixtures"
LEGAL = FIX / "legal-info"
SHOWINFO = FIX / "show-info.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- sanitize_version -----------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3", "1.2.3"),
        ("remotes/origin/1_10_stable", "remotes_origin_1_10_stable"),
        ("git:abc", "git_abc"),
        ("  1.2  ", "1.2"),
        ("a b/c:d", "a_b_c_d"),
        ("", ""),
    ],
)
def test_sanitize_version(raw, expected):
    assert legalinfo.sanitize_version(raw) == expected


# --- _parse_deps_field ----------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ()),
        (" ", ()),
        ("zlib [MIT, GPL-2.0+]", (("zlib", "MIT, GPL-2.0+"),)),
        ("zlib [MIT, GPL-2.0+] host-zlib [MIT, GPL-2.0+]", (
            ("zlib", "MIT, GPL-2.0+"),
            ("host-zlib", "MIT, GPL-2.0+"),
        )),
        ("busybox [GPL-2.0+]", (("busybox", "GPL-2.0+"),)),
        # dependency with no bracket -> empty license
        ("glibc", (("glibc", ""),)),
        # license with spaces and a WITH clause
        ("foo [GPL-2.0 WITH GCC-exception-2]", (("foo", "GPL-2.0 WITH GCC-exception-2"),)),
    ],
)
def test_parse_deps_field(raw, expected):
    assert legalinfo._parse_deps_field(raw) == expected


def test_parse_deps_field_rejects_unbalanced():
    with pytest.raises(LegalInfoError):
        legalinfo._parse_deps_field("zlib [MIT")


def test_parse_deps_field_rejects_malformed():
    with pytest.raises(LegalInfoError):
        legalinfo._parse_deps_field("[MIT] orphan")


# --- parse_manifest -------------------------------------------------------
def test_parse_target_manifest_fixture():
    rows, br_version = legalinfo.parse_manifest(LEGAL / "manifest.csv", "target")
    assert br_version is None
    assert [r.name for r in rows] == ["busybox", "zlib", "zstd"]
    busybox = rows[0]
    assert busybox.scope == SCOPE_TARGET
    assert busybox.version == "1.37.0"
    assert busybox.license == "GPL-2.0+"
    assert busybox.license_files == ("COPYING",)
    assert busybox.source_archive == "busybox-1.37.0.tar.bz2"
    assert busybox.source_site == "https://www.busybox.net/downloads/"
    assert busybox.dependencies == (("host-pkgconf", "BSD-3-Clause"),)


def test_parse_host_manifest_buildroot_row_is_build_scope():
    rows, br_version = legalinfo.parse_manifest(LEGAL / "host-manifest.csv", "host")
    assert br_version == "2026.02.3"
    by_name = {r.name: r for r in rows}
    br = by_name["buildroot"]
    assert br.scope == SCOPE_BUILD
    assert br.license == "GPL-2.0+"
    # "not saved" cells are normalized to ""
    assert br.source_archive == ""
    assert br.source_site == ""
    # the 6-column buildroot row has no dependencies
    assert br.dependencies == ()
    host_zlib = by_name["zlib"]
    assert host_zlib.scope == SCOPE_HOST


def test_parse_manifest_bad_header(tmp_path):
    p = tmp_path / "manifest.csv"
    p.write_text("WRONG,HEADER\n\"a\",\"1\",\"MIT\"\"\"\"\"\n", encoding="utf-8")
    with pytest.raises(LegalInfoError, match="unexpected header"):
        legalinfo.parse_manifest(p, "target")


def test_parse_manifest_wrong_column_count(tmp_path):
    p = tmp_path / "manifest.csv"
    p.write_text(
        legalinfo.MANIFEST_HEADER[0]
        + ","
        + ",".join(legalinfo.MANIFEST_HEADER[1:])
        + "\n"
        + '"busybox","1.37.0","GPL-2.0+"\n',
        encoding="utf-8",
    )
    with pytest.raises(LegalInfoError, match="expected 7"):
        legalinfo.parse_manifest(p, "target")


def test_parse_manifest_six_col_in_target_rejected(tmp_path):
    p = tmp_path / "manifest.csv"
    hdr = ",".join(legalinfo.MANIFEST_HEADER)
    p.write_text(hdr + '\n"a","1","MIT","LICENSE","not saved","not saved"\n', encoding="utf-8")
    with pytest.raises(LegalInfoError, match="six-column row in target manifest"):
        legalinfo.parse_manifest(p, "target")


def test_parse_manifest_duplicate_package(tmp_path):
    p = tmp_path / "manifest.csv"
    hdr = ",".join(legalinfo.MANIFEST_HEADER)
    row = '"busybox","1.37.0","GPL-2.0+","COPYING","x.tar","https://x",""'
    p.write_text(hdr + "\n" + row + "\n" + row + "\n", encoding="utf-8")
    with pytest.raises(LegalInfoError, match="duplicate package"):
        legalinfo.parse_manifest(p, "target")


def test_parse_manifest_missing_file(tmp_path):
    with pytest.raises(LegalInfoError, match="manifest not found"):
        legalinfo.parse_manifest(tmp_path / "nope.csv", "target")


# --- load_license_texts / hashes ------------------------------------------
def test_load_license_texts_found_and_missing(tmp_path):
    d = tmp_path / "licenses"
    sub = d / "busybox-1.37.0"
    sub.mkdir(parents=True)
    (sub / "COPYING").write_text("LICENSE BODY", encoding="utf-8")
    texts, warns = legalinfo.load_license_texts(
        tmp_path, "target", "busybox", "1.37.0", ("COPYING", "MISSING")
    )
    assert texts == [("COPYING", "LICENSE BODY")]
    assert any("MISSING" in w for w in warns)


def test_load_saved_source_hashes(tmp_path):
    src = tmp_path / "sources" / "busybox-1.37.0"
    src.mkdir(parents=True)
    (src / "busybox-1.37.0.tar.bz2").write_bytes(b"tarball")
    (src / "series").write_bytes(b"0001.patch\n")
    hashes = legalinfo.load_saved_source_hashes(tmp_path, "target", "busybox", "1.37.0")
    rels = [p for p, _ in hashes]
    assert rels == [
        "sources/busybox-1.37.0/busybox-1.37.0.tar.bz2",
        "sources/busybox-1.37.0/series",
    ]
    h = dict(hashes)["sources/busybox-1.37.0/busybox-1.37.0.tar.bz2"]
    assert h.algorithm == "sha256"
    assert h.value == hashlib.sha256(b"tarball").hexdigest()


def test_load_saved_source_hashes_absent(tmp_path):
    assert legalinfo.load_saved_source_hashes(tmp_path, "target", "x", "1") == ()


# --- show-info parsing ----------------------------------------------------
def test_parse_show_info_fixture():
    entries = si_mod.parse_show_info(SHOWINFO)
    assert set(entries) == {
        "busybox", "zlib", "zstd", "host-zlib", "host-pkgconf",
        "linux-headers", "rootfs-ext4",
    }
    bb = entries["busybox"]
    assert bb.kind == "target"
    assert bb.version == "1.37.0"
    assert bb.licenses == "GPL-2.0+"
    assert bb.cpe_id == "cpe:2.3:a:busybox:busybox:1.37.0:*:*:*:*:*:*:*"
    assert len(bb.downloads) == 1
    assert bb.downloads[0].source == "busybox-1.37.0.tar.bz2"
    assert len(bb.downloads[0].uris) == 2
    assert entries["linux-headers"].virtual is True
    assert entries["rootfs-ext4"].kind == "rootfs"
    assert entries["rootfs-ext4"].image_name == "rootfs.ext4"


def test_rootfs_entries():
    entries = si_mod.parse_show_info(SHOWINFO)
    rf = si_mod.rootfs_entries(entries)
    assert len(rf) == 1
    assert rf[0].name == "rootfs-ext4"
    assert rf[0].image_name == "rootfs.ext4"
    assert rf[0].dependencies == ("busybox", "zlib", "zstd")


def test_showinfo_missing_file(tmp_path):
    with pytest.raises(ShowInfoError, match="not found"):
        si_mod.parse_show_info(tmp_path / "nope.json")


def test_showinfo_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ShowInfoError, match="invalid JSON"):
        si_mod.parse_show_info(p)


def test_showinfo_top_level_not_object(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ShowInfoError, match="must be a JSON object"):
        si_mod.parse_show_info(p)


def test_showinfo_entry_missing_type(tmp_path):
    p = tmp_path / "notype.json"
    p.write_text('{"x": {"name": "x"}}', encoding="utf-8")
    with pytest.raises(ShowInfoError, match="missing or invalid 'type'"):
        si_mod.parse_show_info(p)


def test_showinfo_bad_downloads(tmp_path):
    p = tmp_path / "baddl.json"
    p.write_text('{"x": {"type": "target", "downloads": "oops"}}', encoding="utf-8")
    with pytest.raises(ShowInfoError, match="'downloads' must be a list"):
        si_mod.parse_show_info(p)


# --- full ingest merge ----------------------------------------------------
def test_ingest_component_set_and_scopes():
    result = ingest(LEGAL, SHOWINFO)
    assert isinstance(result, IngestResult)
    got = [(c.scope, c.name) for c in result.components]
    assert got == [
        (SCOPE_BUILD, "buildroot"),
        (SCOPE_HOST, "zlib"),
        (SCOPE_TARGET, "busybox"),
        (SCOPE_TARGET, "zlib"),
        (SCOPE_TARGET, "zstd"),
    ]
    assert result.buildroot_version == "2026.02.3"


def test_ingest_busybox_enriched_from_showinfo():
    result = ingest(LEGAL, SHOWINFO)
    bb = result.component(SCOPE_TARGET, "busybox")
    assert bb is not None
    # origin = first URI of first download
    assert bb.origin == "https://www.busybox.net/downloads/busybox-1.37.0.tar.bz2"
    assert bb.source_site == "https://www.busybox.net/downloads/"
    assert bb.licenses == "GPL-2.0+"
    assert bb.license_files == ("COPYING",)
    assert bb.license_texts == (("COPYING", "BUSYBOX LICENSE TEXT (GPL-2.0+) - fixture\nThis is a small stand-in for the real COPYING.\n"),)
    assert bb.dependencies == ("host-pkgconf", "zlib")
    assert bb.patches == ("package/busybox/0001-foo.patch",)
    assert bb.cpe_id == "cpe:2.3:a:busybox:busybox:1.37.0:*:*:*:*:*:*:*"
    assert bb.redistributable is True
    # three saved artifacts: tarball, patch, series
    assert len(bb.hashes) == 3
    # source_hash = digest of the original tarball
    expect = sha256_of(LEGAL / "sources/busybox-1.37.0/busybox-1.37.0.tar.bz2")
    assert bb.source_hash is not None
    assert bb.source_hash.value == expect
    assert bb.source_hash.spdx().startswith("sha256=")


def test_ingest_host_and_target_zlib_are_distinct():
    result = ingest(LEGAL, SHOWINFO)
    t = result.component(SCOPE_TARGET, "zlib")
    h = result.component(SCOPE_HOST, "zlib")
    assert t is not None and h is not None
    assert t.scope == SCOPE_TARGET and h.scope == SCOPE_HOST
    # host zlib's source was saved; target zlib's was not
    assert len(h.hashes) == 1
    assert t.hashes == ()
    # both point at the zlib net origin from show-info
    assert t.origin == "https://zlib.net/zlib-1.3.1.tar.xz"
    assert h.origin == "https://zlib.net/zlib-1.3.1.tar.xz"


def test_ingest_rootfs_and_virtual():
    result = ingest(LEGAL, SHOWINFO)
    assert [r.name for r in result.rootfs] == ["rootfs-ext4"]
    assert result.skipped_virtual == ("linux-headers",)
    # virtual must NOT be a component
    assert result.component(SCOPE_TARGET, "linux-headers") is None


def test_ingest_buildrow_without_showinfo_is_manifest_only():
    result = ingest(LEGAL, SHOWINFO)
    br = result.component(SCOPE_BUILD, "buildroot")
    assert br is not None
    assert br.version == "2026.02.3"
    assert br.licenses == "GPL-2.0+"
    # no show-info entry -> no origin, no dependencies from show-info
    assert br.origin == ""
    assert br.dependencies == ()
    # license text still read from host-licenses
    assert br.license_texts and br.license_texts[0][0] == "COPYING"
    assert any("buildroot" in w and "no matching show-info" in w for w in result.warnings)


def test_ingest_unmanifested_showinfo_warns():
    result = ingest(LEGAL, SHOWINFO)
    assert any("host-pkgconf" in w and "no legal-manifest" in w for w in result.warnings)


def test_ingest_source_archive_not_saved_warns():
    result = ingest(LEGAL, SHOWINFO)
    assert any(
        "zstd" in w and "was not saved under sources/" in w for w in result.warnings
    )


def test_ingest_no_showinfo_gives_manifest_only(tmp_path):
    import shutil

    legal_copy = tmp_path / "legal-info"
    shutil.copytree(LEGAL, legal_copy)
    result = ingest(legal_copy, None)
    # still get all five components
    assert len(result.components) == 5
    bb = result.component(SCOPE_TARGET, "busybox")
    # no show-info -> origin falls back to source_site
    assert bb.origin == "https://www.busybox.net/downloads/"
    # no show-info -> dependencies fall back to the manifest dep names
    assert bb.dependencies == ("host-pkgconf",)
    assert any("no show-info file given" in w for w in result.warnings)
    assert result.rootfs == ()


def test_ingest_deterministic_ordering():
    a = ingest(LEGAL, SHOWINFO)
    b = ingest(LEGAL, SHOWINFO)
    assert [(c.scope, c.name) for c in a.components] == [(c.scope, c.name) for c in b.components]
    # components sorted build < host < target, then by name
    keys = [(c.scope, c.name) for c in a.components]
    assert keys == sorted(keys, key=lambda sn: ({SCOPE_BUILD: 0, SCOPE_HOST: 1, SCOPE_TARGET: 2}[sn[0]], sn[1]))


def test_ingest_missing_legal_dir(tmp_path):
    with pytest.raises(LegalInfoError, match="legal-info directory not found"):
        ingest(tmp_path / "nope", SHOWINFO)


def test_ingest_warns_when_host_manifest_missing(tmp_path):
    import shutil

    legal_copy = tmp_path / "legal-info"
    shutil.copytree(LEGAL, legal_copy)
    (legal_copy / "host-manifest.csv").unlink()
    result = ingest(legal_copy, SHOWINFO)
    assert any("host-manifest.csv not found" in w for w in result.warnings)
    # host components gone
    assert result.component(SCOPE_HOST, "zlib") is None
    assert result.component(SCOPE_BUILD, "buildroot") is None
