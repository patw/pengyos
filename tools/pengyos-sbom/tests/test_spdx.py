"""Tests for SPDX 2.3 emission (and the license-expression conversion).

Covers the subtask: "Emit SPDX 2.3 JSON: document, packages, relationships
(DESCRIBES/CONTAINS), license expressions, checksums."

Everything is driven from the committed fixtures (no network / root). The
emitter function is tested with an *injected* creation timestamp so the
document is byte-comparable; the CLI ``generate`` subcommand is exercised as a
real subprocess against the same fixtures.
"""

from __future__ import annotations

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

from pengyos_sbom import constants, spdx  # noqa: E402
from pengyos_sbom.ingest import ingest  # noqa: E402
from pengyos_sbom.licenses import LicenseConverter, _split_top  # noqa: E402
from pengyos_sbom.model import (  # noqa: E402
    SCOPE_BUILD,
    SCOPE_HOST,
    SCOPE_TARGET,
    Component,
    IngestResult,
)

CREATED = "2026-01-01T00:00:00+00:00"


def run_cli(*args):
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "pengyos_sbom", *map(str, args)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def fixture_result():
    return ingest(LEGAL, SHOWINFO)


# --- license-expression conversion --------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "NOASSERTION"),
        ("   ", "NOASSERTION"),
        ("MIT", "MIT"),
        ("BSD-2-Clause", "BSD-2-Clause"),
        ("GPL-2.0+", "GPL-2.0-or-later"),
        ("LGPL-2.1+", "LGPL-2.1-or-later"),
        ("MIT, GPL-2.0+", "MIT OR GPL-2.0-or-later"),
        ("Apache-2.0,MIT,GPL-3.0+", "Apache-2.0 OR MIT OR GPL-3.0-or-later"),
        ("LGPL-2.1+ && MPL-2.0", "(LGPL-2.1-or-later AND MPL-2.0)"),
        ("GPL-2.0+ WITH GCC-exception-2.0", "GPL-2.0-or-later WITH GCC-exception-2.0"),
        ("NONE", "NONE"),
        ("NOASSERTION", "NOASSERTION"),
    ],
)
def test_convert_known(raw, expected):
    assert LicenseConverter().convert(raw) == expected


@pytest.mark.parametrize(
    "raw,expected_ref",
    [
        ("MegaCorp-Lic", "LicenseRef-MEGACORP-LIC"),
        ("Some  Weird  License", "LicenseRef-SOME-WEIRD-LICENSE"),
        ("GPL-2.0", "LicenseRef-GPL-2-0"),  # bare (neither -only nor -or-later)
    ],
)
def test_convert_unknown_mints_licenseref(raw, expected_ref):
    conv = LicenseConverter()
    assert conv.convert(raw) == expected_ref
    assert (expected_ref, raw) in conv.extracted()


def test_licenseref_disambiguation_for_colliding_slugs():
    conv = LicenseConverter()
    assert conv.convert("Foo Bar") == "LicenseRef-FOO-BAR"
    assert conv.convert("Foo-Bar") == "LicenseRef-FOO-BAR-2"
    assert conv.extracted() == [
        ("LicenseRef-FOO-BAR", "Foo Bar"),
        ("LicenseRef-FOO-BAR-2", "Foo-Bar"),
    ]


def test_licenseref_same_token_is_stable():
    conv = LicenseConverter()
    assert conv.convert("MegaCorp-Lic") == "LicenseRef-MEGACORP-LIC"
    assert conv.convert("MegaCorp-Lic") == "LicenseRef-MEGACORP-LIC"
    assert conv.extracted() == [("LicenseRef-MEGACORP-LIC", "MegaCorp-Lic")]


def test_split_top_respects_parentheses():
    assert _split_top("(a, b), c", ",") == ["(a, b)", "c"]
    assert _split_top("a && (b && c)", "&&") == ["a", "(b && c)"]


# --- SPDX document structure -------------------------------------------
def test_document_top_level_fields():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["dataLicense"] == "CC0-1.0"
    assert doc["SPDXID"] == "SPDXRef-DOCUMENT"
    assert doc["documentDescribes"] == ["SPDXRef-RootArtifact"]
    assert doc["creationInfo"]["created"] == CREATED
    assert doc["creationInfo"]["creators"] == ["Tool:pengyos-sbom/0.1.0"]
    assert doc["documentNamespace"].startswith(constants.SPDX_NAMESPACE_PREFIX + "/")


def test_document_packages_count_and_root():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    # 1 root artifact + 5 components (buildroot, host-zlib, busybox, zlib, zstd)
    assert len(doc["packages"]) == 6
    root = doc["packages"][0]
    assert root["SPDXID"] == "SPDXRef-RootArtifact"
    assert root["name"] == "rootfs.ext4"  # rootfs-ext4's image_name
    assert root["primaryPackagePurpose"] == "OPERATING-SYSTEM"
    assert root["versionInfo"] == "2026.02.3"  # buildroot version


def test_document_relationships_describes_and_contains():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    rels = doc["relationships"]
    describes = [
        r for r in rels
        if r["spdxElementId"] == "SPDXRef-DOCUMENT"
        and r["relationshipType"] == "DESCRIBES"
    ]
    contains = [
        r for r in rels
        if r["spdxElementId"] == "SPDXRef-RootArtifact"
        and r["relationshipType"] == "CONTAINS"
    ]
    assert len(describes) == 1
    assert describes[0]["relatedSpdxElement"] == "SPDXRef-RootArtifact"
    assert len(contains) == 5  # one per component
    # every CONTAINS target is an actual package SPDXID
    pkg_ids = {p["SPDXID"] for p in doc["packages"]}
    for r in contains:
        assert r["relatedSpdxElement"] in pkg_ids


def test_spdxid_uniqueness():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    ids = [p["SPDXID"] for p in doc["packages"]]
    assert len(ids) == len(set(ids))
    # relationship endpoints must all resolve to declared package ids
    all_pkg = {p["SPDXID"] for p in doc["packages"]} | {"SPDXRef-DOCUMENT"}
    for r in doc["relationships"]:
        assert r["relatedSpdxElement"] in all_pkg


def test_busybox_package_fields():
    import hashlib

    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    bb = next(p for p in doc["packages"] if p["name"] == "busybox")
    assert bb["SPDXID"] == "SPDXRef-Package-target-busybox"
    assert bb["versionInfo"] == "1.37.0"
    assert bb["licenseConcluded"] == "GPL-2.0-or-later"
    assert bb["licenseDeclared"] == "GPL-2.0-or-later"
    assert bb["downloadLocation"] == "https://www.busybox.net/downloads/busybox-1.37.0.tar.bz2"
    # checksum = SHA-256 of the saved original tarball
    expect = hashlib.sha256(
        (LEGAL / "sources/busybox-1.37.0/busybox-1.37.0.tar.bz2").read_bytes()
    ).hexdigest()
    assert bb["checksums"] == [{"algorithm": "SHA256", "checksumValue": expect}]
    assert bb["externalRefs"] == [
        {
            "referenceCategory": "SECURITY",
            "referenceType": "cpe23Type",
            "referenceLocator": "cpe:2.3:a:busybox:busybox:1.37.0:*:*:*:*:*:*:*",
        }
    ]
    assert bb["primaryPackagePurpose"] == "OTHER"


def test_host_and_target_zlib_are_distinct_packages():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    by_id = {p["SPDXID"]: p for p in doc["packages"]}
    assert "SPDXRef-Package-host-zlib" in by_id
    assert "SPDXRef-Package-target-zlib" in by_id
    # the buildroot row is a build-scope package
    assert "SPDXRef-Package-build-buildroot" in by_id
    # zlib's dual license becomes an OR expression
    assert by_id["SPDXRef-Package-target-zlib"]["licenseConcluded"] == (
        "MIT OR GPL-2.0-or-later"
    )


def test_no_checksum_when_no_source_saved():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    by_id = {p["SPDXID"]: p for p in doc["packages"]}
    # target zlib's source was not saved -> no checksums field
    assert "checksums" not in by_id["SPDXRef-Package-target-zlib"]
    # buildroot (6-col row, not saved) -> no checksums
    assert "checksums" not in by_id["SPDXRef-Package-build-buildroot"]


def test_document_namespace_is_deterministic_and_content_sensitive():
    a = spdx.build_spdx_document(fixture_result(), created=CREATED)
    b = spdx.build_spdx_document(fixture_result(), created=CREATED)
    assert a["documentNamespace"] == b["documentNamespace"]

    # a different doc name yields a different namespace
    c = spdx.build_spdx_document(fixture_result(), created=CREATED, doc_name="Other")
    assert a["documentNamespace"] != c["documentNamespace"]


def test_deterministic_serialization():
    a = spdx.build_spdx_document(fixture_result(), created=CREATED)
    b = spdx.build_spdx_document(fixture_result(), created=CREATED)
    ja = json.dumps(a, indent=2, sort_keys=True)
    jb = json.dumps(b, indent=2, sort_keys=True)
    assert ja == jb


def test_document_without_rootfs_uses_synthetic_root():
    comp = Component(name="solo", scope="target", version="1.0", licenses="MIT")
    res = IngestResult(components=(comp,))  # no rootfs
    doc = spdx.build_spdx_document(res, created=CREATED)
    assert doc["documentDescribes"] == ["SPDXRef-RootArtifact"]
    root = doc["packages"][0]
    assert root["name"] == "PengyOS rootfs"
    assert len(doc["packages"]) == 2
    contains = [
        r for r in doc["relationships"] if r["relationshipType"] == "CONTAINS"
    ]
    assert len(contains) == 1
    assert contains[0]["relatedSpdxElement"] == "SPDXRef-Package-target-solo"


def test_unknown_license_declared_in_document():
    comp = Component(name="mystery", scope="target", version="1.0", licenses="MegaCorp-Lic")
    res = IngestResult(components=(comp,))
    doc = spdx.build_spdx_document(res, created=CREATED)
    assert "hasExtractedLicensingInfos" in doc
    info = doc["hasExtractedLicensingInfos"][0]
    assert info["licenseId"] == "LicenseRef-MEGACORP-LIC"
    assert info["name"] == "MegaCorp-Lic"
    pkg = next(p for p in doc["packages"] if p["name"] == "mystery")
    assert pkg["licenseConcluded"] == "LicenseRef-MEGACORP-LIC"


def test_no_extracted_infos_when_all_known():
    doc = spdx.build_spdx_document(fixture_result(), created=CREATED)
    # the fixture only uses known SPDX ids, so nothing is minted
    assert "hasExtractedLicensingInfos" not in doc


# --- CLI generate (real subprocess) ------------------------------------
def test_cli_generate_writes_valid_spdx(tmp_path):
    out = tmp_path / "sbom.spdx.json"
    res = run_cli(
        "generate", "--legal-info", LEGAL, "--show-info", SHOWINFO, "--out", out
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert out.is_file()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["documentDescribes"] == ["SPDXRef-RootArtifact"]
    assert len(doc["packages"]) == 6
    assert "wrote SPDX 2.3 SBOM" in res.stdout


def test_cli_generate_with_cdx_writes_both(tmp_path):
    # --cdx is now implemented (see tests/test_cyclonedx.py); both files are
    # written and both summary lines are printed.
    out = tmp_path / "sbom.spdx.json"
    cdx = tmp_path / "sbom.cdx.json"
    res = run_cli(
        "generate",
        "--legal-info", LEGAL,
        "--show-info", SHOWINFO,
        "--out", out,
        "--cdx", cdx,
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert out.is_file()
    assert cdx.is_file()
    assert "wrote SPDX 2.3 SBOM" in res.stdout
    assert "wrote CycloneDX 1.5 BOM" in res.stdout


def test_cli_generate_missing_legal_dir_errors(tmp_path):
    out = tmp_path / "sbom.spdx.json"
    res = run_cli(
        "generate",
        "--legal-info", tmp_path / "nope",
        "--show-info", SHOWINFO,
        "--out", out,
    )
    assert res.returncode == constants.EXIT_ERROR
    assert "error" in res.stderr
    assert not out.exists()


def test_cli_generate_malformed_showinfo_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    out = tmp_path / "sbom.spdx.json"
    res = run_cli(
        "generate", "--legal-info", LEGAL, "--show-info", bad, "--out", out
    )
    assert res.returncode == constants.EXIT_ERROR
    assert "error" in res.stderr
    assert not out.exists()
