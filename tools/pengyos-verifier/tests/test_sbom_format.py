"""Tests for the SBOM structural validity rules (sbom_format.py)."""

from __future__ import annotations

import copy

import pytest

from pengyos_verifier import sbom_format as bf


def make_spdx_doc():
    """A minimal structurally-valid SPDX 2.3 document (witness level)."""
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "PengyOS Core M1 SBOM",
        "documentNamespace": "https://pengyos.catbee.ca/sbom/core-m1/00000000",
        "documentDescribes": ["SPDXRef-RootArtifact"],
        "creationInfo": {
            "created": "2026-01-01T00:00:00Z",
            "creators": ["Tool: pengyos-sbom-0.1.0"],
        },
        "packages": [
            {
                "SPDXID": "SPDXRef-RootArtifact",
                "name": "rootfs-ext4",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            },
            {
                "SPDXID": "SPDXRef-Package-target-busybox",
                "name": "busybox",
                "downloadLocation": "https://busybox.net",
                "filesAnalyzed": False,
                "licenseConcluded": "GPL-2.0-or-later",
                "licenseDeclared": "GPL-2.0-or-later",
                "copyrightText": "NOASSERTION",
            },
        ],
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT",
             "relatedSpdxElement": "SPDXRef-RootArtifact",
             "relationshipType": "DESCRIBES"},
            {"spdxElementId": "SPDXRef-RootArtifact",
             "relatedSpdxElement": "SPDXRef-Package-target-busybox",
             "relationshipType": "CONTAINS"},
        ],
    }


def make_cdx_doc():
    """A minimal structurally-valid CycloneDX 1.5 BOM (witness level)."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:1b9dd6a7-159a-41f8-95bb-d63f8d56d922",
        "version": 1,
        "metadata": {
            "timestamp": "2026-01-01T00:00:00Z",
            "tools": [{"name": "pengyos-sbom", "version": "0.1.0"}],
            "component": {
                "type": "operating-system",
                "name": "rootfs-ext4",
                "bom-ref": "pkg-root",
            },
        },
        "components": [
            {
                "type": "other",
                "name": "busybox",
                "bom-ref": "pkg-target-busybox",
                "purl": "pkg:generic/busybox@1.36.1",
                "hashes": [{"alg": "SHA-256", "content": "cd" * 32}],
            }
        ],
    }


# --- detection ------------------------------------------------------------------

def test_detect_format():
    assert bf.detect_format(make_spdx_doc()) == "spdx"
    assert bf.detect_format(make_cdx_doc()) == "cyclonedx"
    assert bf.detect_format({"foo": 1}) is None
    assert bf.detect_format({"spdxVersion": "x", "bomFormat": "y"}) is None
    assert bf.detect_format([1]) is None
    assert bf.detect_format("doc") is None


def test_unrecognizable_documents_are_errors():
    assert bf.sbom_errors({"foo": 1}) == [
        "document has neither spdxVersion nor bomFormat — "
        "not a recognizable SBOM"]
    assert bf.sbom_errors({"spdxVersion": "x", "bomFormat": "y"})[
        0].startswith("document carries BOTH")
    assert bf.sbom_errors([1]) == ["document is not a JSON object"]


# --- SPDX ------------------------------------------------------------------------

def test_valid_spdx_document_has_no_errors():
    assert bf.sbom_errors(make_spdx_doc()) == []


def test_spdx_document_field_rules():
    doc = make_spdx_doc()
    doc["spdxVersion"] = "SPDX-2.2"
    assert any("spdxVersion" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["dataLicense"] = "CC-BY-4.0"
    assert any("dataLicense" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["SPDXID"] = "SPDXRef-OTHER"
    assert any("SPDXID" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["documentNamespace"] = ""
    assert any("documentNamespace" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["documentDescribes"] = []
    assert any("documentDescribes" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    del doc["creationInfo"]
    assert any("creationInfo" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["creationInfo"]["creators"] = []
    assert any("creators" in e for e in bf.sbom_errors(doc))


def test_spdx_package_rules():
    doc = make_spdx_doc()
    doc["packages"] = "not-a-list"
    assert any("packages must be a list" in e for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    del doc["packages"][1]["name"]
    assert any("packages[1] missing 'name'" in e
               for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["packages"][0]["filesAnalyzed"] = "false"
    assert any("filesAnalyzed must be a boolean" in e
               for e in bf.sbom_errors(doc))


def test_spdx_relationship_rules():
    doc = make_spdx_doc()
    doc["relationships"][0]["relationshipType"] = "FRIEND_OF"
    assert any("not in the SPDX 2.3 enum" in e
               for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["relationships"][1]["relatedSpdxElement"] = "SPDXRef-Ghost"
    assert any("SPDXRef-Ghost" in e and "does not resolve" in e
               for e in bf.sbom_errors(doc))

    doc = make_spdx_doc()
    doc["documentDescribes"] = ["SPDXRef-Nowhere"]
    assert any("documentDescribes entry 'SPDXRef-Nowhere'" in e
               for e in bf.sbom_errors(doc))


def test_spdx_duplicate_ids_are_reported():
    doc = make_spdx_doc()
    doc["packages"][1]["SPDXID"] = doc["packages"][0]["SPDXID"]
    errors = bf.sbom_errors(doc)
    assert any("duplicate SPDX id 'SPDXRef-RootArtifact'" in e
               for e in errors)


def test_spdx_extracted_license_ids_join_the_id_space():
    doc = make_spdx_doc()
    doc["hasExtractedLicensingInfos"] = [
        {"licenseId": "LicenseRef-foo", "name": "Foo"}]
    assert bf.sbom_errors(doc) == []
    doc["relationships"].append(
        {"spdxElementId": "SPDXRef-DOCUMENT",
         "relatedSpdxElement": "LicenseRef-foo",
         "relationshipType": "OTHER"})
    assert bf.sbom_errors(doc) == []
    doc["relationships"][-1]["relatedSpdxElement"] = "LicenseRef-ghost"
    assert any("LicenseRef-ghost" in e for e in bf.sbom_errors(doc))


# --- CycloneDX ----------------------------------------------------------------------

def test_valid_cyclonedx_document_has_no_errors():
    assert bf.sbom_errors(make_cdx_doc()) == []


def test_cyclonedx_document_field_rules():
    doc = make_cdx_doc()
    doc["bomFormat"] = "CycloneDX2"
    assert any("bomFormat" in e for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["specVersion"] = "1.4"
    assert any("specVersion" in e for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["serialNumber"] = "not-a-uuid"
    assert any("serialNumber" in e for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["version"] = "1"
    assert any("version must be an integer" in e
               for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["components"] = "nope"
    assert any("components must be a list" in e
               for e in bf.sbom_errors(doc))


def test_cyclonedx_component_rules():
    doc = make_cdx_doc()
    doc["components"][0]["type"] = "gadget"
    assert any("component type enum" in e for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    del doc["components"][0]["name"]
    assert any("missing non-empty name" in e
               for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["components"][0]["purl"] = "cpe:2.3:a:busybox"
    assert any("purl must start with 'pkg:'" in e
               for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["components"][0]["cpe"] = "pkg:generic/busybox"
    assert any("cpe must start with 'cpe:'" in e
               for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["components"][0]["hashes"] = [{"alg": "SHA-256"}]
    assert any("hashes[0] must carry" in e
               for e in bf.sbom_errors(doc))

    doc = make_cdx_doc()
    doc["metadata"]["component"]["type"] = "gadget"
    assert any("metadata.component.type" in e
               for e in bf.sbom_errors(doc))


def test_cyclonedx_duplicate_bom_refs_are_reported():
    doc = make_cdx_doc()
    doc["components"][0]["bom-ref"] = "pkg-root"  # collides w/ metadata
    assert any("duplicate bom-ref 'pkg-root'" in e
               for e in bf.sbom_errors(doc))


def test_cyclonedx_top_level_license_rules():
    doc = make_cdx_doc()
    doc["licenses"] = [
        {"licenseId": "LicenseRef-a", "license": {"name": "A"}},
        {"licenseId": "LicenseRef-a", "license": {"name": "A2"}},
    ]
    assert any("duplicate top-level licenseId 'LicenseRef-a'" in e
               for e in bf.sbom_errors(doc))
    doc["licenses"] = [{"licenseId": "LicenseRef-a"}]
    assert any("licenses[0].license must be an object" in e
               for e in bf.sbom_errors(doc))
