"""Tests for CycloneDX 1.5 emission from the component model.

Covers the subtask: "Emit CycloneDX 1.5 JSON from the same model."

Everything is driven from the committed fixtures (no network / root). The
emitter function is tested with an *injected* timestamp so the BOM is
byte-comparable; the CLI ``generate --cdx`` path is exercised as a real
subprocess against the same fixtures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
LEGAL = FIX / "legal-info"
SHOWINFO = FIX / "show-info.json"

from pengyos_sbom import constants, cyclonedx  # noqa: E402
from pengyos_sbom.ingest import ingest  # noqa: E402
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


def sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- BOM structure --------------------------------------------------------
def test_bom_top_level_fields():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert doc["version"] == 1
    assert doc["metadata"]["timestamp"] == CREATED
    assert doc["metadata"]["tools"] == [
        {"name": "pengyos-sbom", "version": "0.1.0"}
    ]


def test_serial_number_is_valid_uuid_v5_shape_and_deterministic():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    serial = doc["serialNumber"]
    assert serial.startswith("urn:uuid:")
    # must parse as a valid UUID and be a name-based (version 5) UUID
    u = uuid.UUID(serial[len("urn:uuid:"):])
    assert u.version == 5
    # deterministic: two runs, same serial
    doc2 = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    assert doc["serialNumber"] == doc2["serialNumber"]


def test_serial_number_is_content_sensitive():
    a = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    # a different component set -> a different serial
    other = IngestResult(components=(Component(name="x", scope="target", version="9.9"),))
    b = cyclonedx.build_cyclonedx_document(other, created=CREATED)
    assert a["serialNumber"] != b["serialNumber"]


def test_root_component_is_operating_system():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    root = doc["metadata"]["component"]
    assert root["type"] == "operating-system"
    assert root["name"] == "rootfs.ext4"  # rootfs-ext4's image_name
    assert root["version"] == "2026.02.3"  # buildroot version
    assert root["bom-ref"] == "pkg-root"


def test_components_count_matches_model():
    result = fixture_result()
    doc = cyclonedx.build_cyclonedx_document(result, created=CREATED)
    assert len(doc["components"]) == len(result.components)
    assert len(doc["components"]) == 5


def test_components_preserve_model_order():
    result = fixture_result()
    doc = cyclonedx.build_cyclonedx_document(result, created=CREATED)
    got = [(c.get("type"), c["name"]) for c in doc["components"]]
    # ordered build < host < target, then name; all component type is "other"
    assert got == [
        ("other", "buildroot"),
        ("other", "zlib"),
        ("other", "busybox"),
        ("other", "zlib"),
        ("other", "zstd"),
    ]


def test_bom_refs_are_unique_and_scoped():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    refs = [c["bom-ref"] for c in doc["components"]]
    assert len(refs) == len(set(refs))
    assert "pkg-host-zlib" in refs
    assert "pkg-target-zlib" in refs
    assert "pkg-build-buildroot" in refs


def test_purl_and_version():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    by_ref = {c["bom-ref"]: c for c in doc["components"]}
    assert by_ref["pkg-target-busybox"]["purl"] == "pkg:generic/busybox@1.37.0"
    assert by_ref["pkg-target-zlib"]["purl"] == "pkg:generic/zlib@1.3.1"
    assert by_ref["pkg-build-buildroot"]["version"] == "2026.02.3"


def test_hash_present_only_for_saved_source():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    by_ref = {c["bom-ref"]: c for c in doc["components"]}
    # busybox's original tarball was saved -> one SHA-256 hash
    expect = sha256_of(LEGAL / "sources/busybox-1.37.0/busybox-1.37.0.tar.bz2")
    assert by_ref["pkg-target-busybox"]["hashes"] == [
        {"alg": "SHA-256", "content": expect}
    ]
    # host zlib source was saved under host-sources -> has a hash
    expect_host = sha256_of(LEGAL / "host-sources/zlib-1.3.1/zlib-1.3.1.tar.xz")
    assert by_ref["pkg-host-zlib"]["hashes"] == [
        {"alg": "SHA-256", "content": expect_host}
    ]
    # target zlib / zstd / buildroot had no saved source -> no hashes field
    assert "hashes" not in by_ref["pkg-target-zlib"]
    assert "hashes" not in by_ref["pkg-target-zstd"]
    assert "hashes" not in by_ref["pkg-build-buildroot"]


def test_cpe_and_external_reference():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    by_ref = {c["bom-ref"]: c for c in doc["components"]}
    bb = by_ref["pkg-target-busybox"]
    assert bb["cpe"] == "cpe:2.3:a:busybox:busybox:1.37.0:*:*:*:*:*:*:*"
    assert bb["externalReferences"] == [
        {"type": "distribution", "url": "https://www.busybox.net/downloads/busybox-1.37.0.tar.bz2"}
    ]
    # host zlib's show-info has no cpe-id -> no cpe field, but has an origin
    hz = by_ref["pkg-host-zlib"]
    assert "cpe" not in hz
    assert hz["externalReferences"][0]["url"] == "https://zlib.net/zlib-1.3.1.tar.xz"
    # buildroot (no show-info) -> no origin, no externalReferences, no cpe
    br = by_ref["pkg-build-buildroot"]
    assert "externalReferences" not in br
    assert "cpe" not in br


def test_license_expressions_reuse_spdx_conversion():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    by_ref = {c["bom-ref"]: c for c in doc["components"]}
    # GPL-2.0+ -> GPL-2.0-or-later
    assert by_ref["pkg-target-busybox"]["licenses"] == [
        {"expression": "GPL-2.0-or-later"}
    ]
    # dual license -> OR expression
    assert by_ref["pkg-target-zlib"]["licenses"] == [
        {"expression": "MIT OR GPL-2.0-or-later"}
    ]
    assert by_ref["pkg-target-zstd"]["licenses"] == [{"expression": "BSD-3-Clause"}]


def test_no_document_level_licenses_when_all_known():
    doc = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    # the fixture uses only known SPDX ids -> nothing custom is declared
    assert "licenses" not in doc


def test_unknown_license_declared_at_document_level():
    comp = Component(name="mystery", scope="target", version="1.0", licenses="MegaCorp-Lic")
    res = IngestResult(components=(comp,))
    doc = cyclonedx.build_cyclonedx_document(res, created=CREATED)
    assert doc["licenses"] == [
        {"licenseId": "LicenseRef-MEGACORP-LIC", "license": {"name": "MegaCorp-Lic"}}
    ]
    pkg = doc["components"][0]
    assert pkg["licenses"] == [{"expression": "LicenseRef-MEGACORP-LIC"}]


def test_deterministic_serialization():
    a = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    b = cyclonedx.build_cyclonedx_document(fixture_result(), created=CREATED)
    assert json.dumps(a, indent=2, sort_keys=True) == json.dumps(b, indent=2, sort_keys=True)


def test_document_without_rootfs_uses_synthetic_root():
    comp = Component(name="solo", scope="target", version="1.0", licenses="MIT")
    res = IngestResult(components=(comp,))
    doc = cyclonedx.build_cyclonedx_document(res, created=CREATED)
    root = doc["metadata"]["component"]
    assert root["name"] == "PengyOS rootfs"
    assert root["type"] == "operating-system"
    assert len(doc["components"]) == 1


# --- CLI generate --cdx (real subprocess) --------------------------------
def test_cli_generate_cdx_writes_valid_bom(tmp_path):
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
    assert out.is_file() and cdx.is_file()
    doc = json.loads(cdx.read_text(encoding="utf-8"))
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert len(doc["components"]) == 5
    assert "wrote CycloneDX 1.5 BOM" in res.stdout


def test_cli_generate_without_cdx_does_not_write_cdx(tmp_path):
    out = tmp_path / "sbom.spdx.json"
    cdx = tmp_path / "sbom.cdx.json"
    res = run_cli(
        "generate", "--legal-info", LEGAL, "--show-info", SHOWINFO, "--out", out
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert out.is_file()
    assert not cdx.exists()


def test_cli_generate_cdx_missing_legal_dir_errors(tmp_path):
    out = tmp_path / "sbom.spdx.json"
    cdx = tmp_path / "sbom.cdx.json"
    res = run_cli(
        "generate",
        "--legal-info", tmp_path / "nope",
        "--show-info", SHOWINFO,
        "--out", out,
        "--cdx", cdx,
    )
    assert res.returncode == constants.EXIT_ERROR
    assert "error" in res.stderr
    # neither file is written on ingest failure
    assert not out.exists()
    assert not cdx.exists()
