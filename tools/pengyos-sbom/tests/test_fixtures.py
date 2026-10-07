"""Tests that the committed fixture sample is a complete, realistic and
self-consistent Buildroot ``legal-info`` + ``show-info`` pair.

This locks in the "Fixtures" deliverable. The sample under ``tests/fixtures``
is the canonical input every other test module runs against, so it must:

* contain exactly the expected files (no silent rot — a deleted or renamed
  fixture file fails this);
* be shaped like *real* Buildroot 2026.x output (unquoted header, fully-quoted
  rows, the 6-column buildroot row, sanitized license/source dir names);
* exercise every shape the parsers have to handle (target/host/build scopes, a
  package present in both target and host, a virtual alias, a rootfs target, a
  ``not saved`` source, a non-redistributable package, a multi-URI download, a
  dependency carrying a multi-comma license, a CPE id, a saved patch + series);
* be internally consistent (referenced license files exist where a package
  saved them; saved-source dirs match the packages that were actually saved;
  manifest rows and show-info entries line up); and
* be a *working* end-to-end input: generate → validate → sign → verify for
  both the SPDX 2.3 and CycloneDX 1.5 outputs.

Nothing here needs the network, root, a display or hardware.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
LEGAL = FIX / "legal-info"
SHOWINFO = FIX / "show-info.json"

from pengyos_sbom import constants, ed25519, legalinfo, showinfo  # noqa: E402
from pengyos_sbom.ingest import ingest  # noqa: E402

# The RFC 8032 TEST 1 key, used (like test_sign_verify.py) for the
# end-to-end sign/verify over the committed fixtures.
SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
PUB = ed25519.publickey(SEED)

#: The exact set of files the committed fixture sample must contain.
EXPECTED_FIXTURE_FILES = {
    "legal-info/buildroot.config",
    "legal-info/host-licenses/buildroot-2026.02.3/COPYING",
    "legal-info/host-licenses/zlib-1.3.1/LICENSE",
    "legal-info/host-manifest.csv",
    "legal-info/host-sources/zlib-1.3.1/zlib-1.3.1.tar.xz",
    "legal-info/licenses/busybox-1.37.0/COPYING",
    "legal-info/licenses/zlib-1.3.1/LICENSE",
    "legal-info/manifest.csv",
    "legal-info/sources/busybox-1.37.0/0001-foo.patch",
    "legal-info/sources/busybox-1.37.0/busybox-1.37.0.tar.bz2",
    "legal-info/sources/busybox-1.37.0/series",
    "show-info.json",
}


def _rel_files(root: Path):
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "pengyos_sbom", *map(str, args)],
        cwd=REPO,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- 1. The committed tree is exactly the expected sample ----------------
def test_fixture_tree_is_complete():
    assert _rel_files(FIX) == EXPECTED_FIXTURE_FILES


def test_both_input_formats_are_present():
    # A legal-info *directory* (not a file) and a show-info *JSON* file.
    assert LEGAL.is_dir()
    assert (LEGAL / "manifest.csv").is_file()
    assert (LEGAL / "host-manifest.csv").is_file()
    assert SHOWINFO.is_file()


def test_show_info_fixture_is_valid_json_object():
    data = json.loads(SHOWINFO.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # every key is a lower-case package/rootfs identifier
    for key in data:
        assert key == key.lower()


# --- 2. Real Buildroot manifest shape ------------------------------------
def test_manifest_header_and_quoting_are_real_buildroot_shape():
    lines = (LEGAL / "manifest.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "PACKAGE,VERSION,LICENSE,LICENSE FILES," \
                       "SOURCE ARCHIVE,SOURCE SITE,DEPENDENCIES WITH LICENSES"
    # one unquoted header, then one fully-quoted data row per package
    for line in lines[1:]:
        assert line.startswith('"'), line


def test_host_manifest_carries_the_six_column_buildroot_row():
    lines = (LEGAL / "host-manifest.csv").read_text(encoding="utf-8").splitlines()
    buildroot_rows = [l for l in lines[1:] if l.startswith('"buildroot"')]
    assert len(buildroot_rows) == 1
    # six quoted fields (no dependencies column)
    assert buildroot_rows[0].count('"') == 12


# --- 3. The sample exercises every parser shape ---------------------------
def test_fixture_covers_every_scope_and_special_case():
    target_rows, _ = legalinfo.parse_manifest(LEGAL / "manifest.csv", "target")
    host_rows, br_version = legalinfo.parse_manifest(LEGAL / "host-manifest.csv", "host")

    target_names = {r.name for r in target_rows}
    assert target_names == {"busybox", "zlib", "zstd"}

    # host manifest: a build-scope buildroot row + a host-scope package
    assert br_version == "2026.02.3"
    by_name = {r.name: r for r in host_rows}
    assert by_name["buildroot"].scope == "build"
    assert by_name["zlib"].scope == "host"

    # a package present in BOTH target and host
    assert "zlib" in target_names and "zlib" in by_name

    # a "not saved" source (buildroot is not redistributable)
    assert by_name["buildroot"].source_archive == ""
    assert by_name["buildroot"].source_site == ""

    # a dependency whose bracketed license contains a comma and a space
    zstd = next(r for r in target_rows if r.name == "zstd")
    dep_names = [d[0] for d in zstd.dependencies]
    assert dep_names == ["zlib", "host-zlib"]
    assert zstd.dependencies[0][1] == "MIT, GPL-2.0+"

    # a non-redistributable package (zstd) and a CPE id (busybox)
    si = showinfo.parse_show_info(SHOWINFO)
    assert si["zstd"].redistributable is False
    assert si["busybox"].cpe_id == \
        "cpe:2.3:a:busybox:busybox:1.37.0:*:*:*:*:*:*:*"


def test_fixture_covers_virtual_rootfs_and_multi_uri_downloads():
    si = showinfo.parse_show_info(SHOWINFO)
    # a virtual alias (not a component)
    assert si["linux-headers"].virtual is True
    # a rootfs target (becomes the described root, not a component)
    assert si["rootfs-ext4"].kind == "rootfs"
    assert si["rootfs-ext4"].image_name == "rootfs.ext4"
    # a package with multiple download URIs (mirror sites)
    assert len(si["busybox"].downloads[0].uris) == 2


def test_saved_source_dirs_match_the_packages_that_were_saved():
    # exactly two packages saved their sources: target busybox (tarball +
    # patch + series) and host zlib (tarball only). Everything else is absent.
    saved = {
        "sources/busybox-1.37.0/busybox-1.37.0.tar.bz2",
        "sources/busybox-1.37.0/0001-foo.patch",
        "sources/busybox-1.37.0/series",
        "host-sources/zlib-1.3.1/zlib-1.3.1.tar.xz",
    }
    got = {
        f.relative_to(LEGAL).as_posix()
        for base in (LEGAL / "sources", LEGAL / "host-sources")
        for f in base.rglob("*")
        if f.is_file()
    }
    assert got == saved


def test_referenced_license_files_exist_for_saved_packages():
    # The packages that saved their license text have it on disk, non-empty.
    for rel in (
        "licenses/busybox-1.37.0/COPYING",
        "licenses/zlib-1.3.1/LICENSE",
        "host-licenses/zlib-1.3.1/LICENSE",
        "host-licenses/buildroot-2026.02.3/COPYING",
    ):
        p = LEGAL / rel
        assert p.is_file(), rel
        assert p.read_text(encoding="utf-8").strip(), f"{rel} is empty"


# --- 4. Manifest <-> show-info consistency through the real ingester ------
def test_ingested_model_is_consistent_and_complete():
    result = ingest(LEGAL, SHOWINFO)

    # all five manifest rows become components (buildroot, host zlib, and the
    # three target packages); virtual and rootfs are routed elsewhere.
    got = [(c.scope, c.name) for c in result.components]
    assert got == [
        ("build", "buildroot"),
        ("host", "zlib"),
        ("target", "busybox"),
        ("target", "zlib"),
        ("target", "zstd"),
    ]
    assert result.buildroot_version == "2026.02.3"
    assert [r.name for r in result.rootfs] == ["rootfs-ext4"]
    assert result.skipped_virtual == ("linux-headers",)

    # every manifest row found its show-info entry except buildroot, which is
    # intentionally absent from show-info (manifest-only) and warns.
    assert any(
        "buildroot" in w and "no matching show-info" in w for w in result.warnings
    )
    # zstd lists a COPYING license file in the manifest but it was not saved.
    assert any(
        "zstd" in w and "license file not found" in w for w in result.warnings
    )
    # zstd's archive is listed but no sources/ dir exists for it.
    assert any(
        "zstd" in w and "was not saved under sources/" in w for w in result.warnings
    )

    # busybox: fully enriched from show-info, with the saved tarball digest.
    bb = result.component("target", "busybox")
    assert bb.origin == "https://www.busybox.net/downloads/busybox-1.37.0.tar.bz2"
    assert bb.source_hash is not None
    assert bb.source_hash.algorithm == "sha256"
    assert len(bb.hashes) == 3  # tarball + patch + series


# --- 5. The fixtures drive the whole tool end-to-end ----------------------
@pytest.fixture
def keys(tmp_path):
    seed = tmp_path / "seed.key"
    seed.write_bytes(SEED)
    pub = tmp_path / "pub.key"
    pub.write_bytes(PUB)
    pub_b64 = tmp_path / "pub.b64.key"
    pub_b64.write_text(base64.b64encode(PUB).decode() + "\n")
    return {"seed": seed, "pub": pub, "pub_b64": pub_b64}


def test_end_to_end_spdx_and_cyclonedx_from_committed_fixtures(tmp_path, keys):
    spdx_out = tmp_path / "sbom.spdx.json"
    cdx_out = tmp_path / "sbom.cdx.json"

    res = run_cli(
        "generate", "--legal-info", LEGAL, "--show-info", SHOWINFO,
        "--out", spdx_out, "--cdx", cdx_out,
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert spdx_out.is_file() and cdx_out.is_file()

    # both outputs validate
    r = run_cli("validate", spdx_out)
    assert r.returncode == constants.EXIT_OK, r.stderr
    assert "OK (valid SPDX 2.3 SBOM)" in r.stdout
    r = run_cli("validate", cdx_out)
    assert r.returncode == constants.EXIT_OK, r.stderr
    assert "OK (valid CycloneDX 1.5 SBOM)" in r.stdout

    # the SPDX document describes every component and the root artifact
    doc = json.loads(spdx_out.read_text(encoding="utf-8"))
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert len(doc["packages"]) == 6  # 5 components + the described root artifact
    rel_types = {r["relationshipType"] for r in doc["relationships"]}
    assert rel_types == {"DESCRIBES", "CONTAINS"}
    contains = [
        r for r in doc["relationships"] if r["relationshipType"] == "CONTAINS"
    ]
    assert len(contains) == 5

    # sign + verify both formats over the committed sample
    for path, label in ((spdx_out, "SPDX 2.3"), (cdx_out, "CycloneDX 1.5")):
        r = run_cli("sign", path, "--key", keys["seed"])
        assert r.returncode == constants.EXIT_OK, r.stderr
        r = run_cli("verify", path, "--key", keys["pub_b64"])
        assert r.returncode == constants.EXIT_OK, r.stderr
        assert "signature OK" in r.stdout
        assert f"OK (verified {label} SBOM)" in r.stdout


def test_committed_fixture_is_deterministic_input(tmp_path):
    # The same committed sample ingested twice yields an identical model, so
    # the fixtures are a stable, reproducible test input.
    a = ingest(LEGAL, SHOWINFO)
    b = ingest(LEGAL, SHOWINFO)
    assert [(c.scope, c.name) for c in a.components] == \
        [(c.scope, c.name) for c in b.components]
    for ca, cb in zip(a.components, b.components):
        assert ca.hashes == cb.hashes
        assert ca.license_texts == cb.license_texts
        assert ca.origin == cb.origin
