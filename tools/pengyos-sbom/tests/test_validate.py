"""Tests for the ``validate`` subcommand (SPDX 2.3 + CycloneDX 1.5).

Covers the subtask: "Implement ``validate`` (structural + required-fields
checks for each format)."

Strategy: the *positive* contract is that every document this tool emits
(from the committed fixtures) validates cleanly — both in-process via
``validate.validate_document`` and end-to-end via the CLI. The *negative*
contract is a battery of tampered documents (missing required fields, wrong
types, bad enum values, duplicate ids, dangling references) — each must
produce at least one problem, and the CLI must exit non-zero with the
problems on stderr. No network / root involved.
"""

from __future__ import annotations

import copy
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

from pengyos_sbom import cli, constants  # noqa: E402
from pengyos_sbom.cyclonedx import build_cyclonedx_document  # noqa: E402
from pengyos_sbom.errors import SBOMError  # noqa: E402
from pengyos_sbom.ingest import ingest  # noqa: E402
from pengyos_sbom.spdx import build_spdx_document  # noqa: E402
from pengyos_sbom.validate import (  # noqa: E402
    detect_format,
    validate_cyclonedx,
    validate_document,
    validate_file,
    validate_spdx,
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


def fixture_spdx():
    return build_spdx_document(ingest(LEGAL, SHOWINFO), created=CREATED)


def fixture_cdx():
    return build_cyclonedx_document(ingest(LEGAL, SHOWINFO), created=CREATED)


def write_json(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def has_problem(problems, needle):
    return any(needle.lower() in p.lower() for p in problems)


# --- positive: our emitters' output is valid ------------------------------
def test_emitted_spdx_is_valid():
    fmt, problems = validate_document(fixture_spdx())
    assert fmt == "spdx"
    assert problems == []


def test_emitted_cyclonedx_is_valid():
    fmt, problems = validate_document(fixture_cdx())
    assert fmt == "cyclonedx"
    assert problems == []


def test_forced_format_matches_emitted_documents():
    assert validate_document(fixture_spdx(), fmt="spdx")[1] == []
    assert validate_document(fixture_cdx(), fmt="cyclonedx")[1] == []


# --- format detection -----------------------------------------------------
@pytest.mark.parametrize(
    "doc,expected",
    [
        ({"spdxVersion": "SPDX-2.3"}, "spdx"),
        ({"bomFormat": "CycloneDX"}, "cyclonedx"),
        ({}, None),
        ([], None),
        ("nope", None),
        ({"spdxVersion": "SPDX-2.3", "bomFormat": "CycloneDX"}, None),
        ({"someOtherKey": 1}, None),
    ],
    ids=["spdx", "cyclonedx", "empty-object", "list", "string", "both-markers", "unmarked"],
)
def test_detect_format(doc, expected):
    assert detect_format(doc) == expected


def test_validate_document_unknown_format():
    fmt, problems = validate_document({"foo": 1})
    assert fmt == "unknown"
    assert len(problems) == 1
    assert "cannot detect" in problems[0]


def test_validate_document_forced_format_beats_detection():
    # An SPDX doc forced to CycloneDX validation must fail loudly rather
    # than being silently detected as SPDX.
    fmt, problems = validate_document(fixture_spdx(), fmt="cyclonedx")
    assert fmt == "cyclonedx"
    assert has_problem(problems, "bomFormat")


def test_validate_document_bad_format_argument():
    with pytest.raises(SBOMError):
        validate_document({}, fmt="bogus")


def test_non_object_root_reports_problem():
    assert has_problem(validate_spdx([1, 2, 3]), "root must be a JSON object")
    assert has_problem(validate_cyclonedx("s"), "root must be a JSON object")


# --- SPDX structural checks ----------------------------------------------
@pytest.mark.parametrize(
    "field",
    [
        "spdxVersion",
        "dataLicense",
        "SPDXID",
        "name",
        "documentNamespace",
        "documentDescribes",
        "creationInfo",
        "packages",
    ],
)
def test_spdx_missing_document_field(field):
    doc = fixture_spdx()
    del doc[field]
    problems = validate_spdx(doc)
    assert has_problem(problems, f"'{field}'") or has_problem(problems, field)


def test_spdx_wrong_version():
    doc = fixture_spdx()
    doc["spdxVersion"] = "SPDX-2.2"
    assert has_problem(validate_spdx(doc), "SPDX-2.2")


def test_spdx_wrong_data_license():
    doc = fixture_spdx()
    doc["dataLicense"] = "Apache-2.0"
    assert has_problem(validate_spdx(doc), "dataLicense")


def test_spdx_wrong_document_spdxid():
    doc = fixture_spdx()
    doc["SPDXID"] = "SPDXRef-OTHER"
    assert has_problem(validate_spdx(doc), "SPDXID")


def test_spdx_bad_namespace():
    doc = fixture_spdx()
    doc["documentNamespace"] = "not a uri"
    assert has_problem(validate_spdx(doc), "documentNamespace")


def test_spdx_empty_describes():
    doc = fixture_spdx()
    doc["documentDescribes"] = []
    assert has_problem(validate_spdx(doc), "documentDescribes")


def test_spdx_dangling_describes():
    doc = fixture_spdx()
    doc["documentDescribes"] = ["SPDXRef-Nothing"]
    # The id does not resolve to any package/document id.
    assert has_problem(validate_spdx(doc), "SPDXRef-Nothing")


def test_spdx_bad_created_timestamp():
    doc = fixture_spdx()
    doc["creationInfo"]["created"] = "yesterday"
    assert has_problem(validate_spdx(doc), "created")


def test_spdx_accepts_z_suffix_timestamp():
    doc = fixture_spdx()
    doc["creationInfo"]["created"] = "2026-01-01T00:00:00Z"
    assert validate_spdx(doc) == []


def test_spdx_missing_creators():
    doc = fixture_spdx()
    del doc["creationInfo"]["creators"]
    assert has_problem(validate_spdx(doc), "creators")


def test_spdx_bad_creator_prefix():
    doc = fixture_spdx()
    doc["creationInfo"]["creators"] = ["a random tool name"]
    assert has_problem(validate_spdx(doc), "creators[0]")


@pytest.mark.parametrize(
    "field",
    ["SPDXID", "name", "downloadLocation", "licenseConcluded", "licenseDeclared", "copyrightText"],
)
def test_spdx_package_missing_field(field):
    doc = fixture_spdx()
    # A component package (index 0 is the root artifact — tamper with 1).
    del doc["packages"][1][field]
    problems = validate_spdx(doc)
    assert has_problem(problems, "packages[1]")
    assert has_problem(problems, field)


def test_spdx_package_wrong_files_analyzed_type():
    doc = fixture_spdx()
    doc["packages"][1]["filesAnalyzed"] = "false"
    assert has_problem(validate_spdx(doc), "filesAnalyzed")


def test_spdx_duplicate_package_spdxid():
    doc = fixture_spdx()
    doc["packages"].append(copy.deepcopy(doc["packages"][1]))
    assert has_problem(validate_spdx(doc), "duplicate SPDXID")


def test_spdx_package_spdxid_collides_with_document_id():
    doc = fixture_spdx()
    doc["packages"][1]["SPDXID"] = "SPDXRef-DOCUMENT"
    assert has_problem(validate_spdx(doc), "collides")


def test_spdx_bad_relationship_type():
    doc = fixture_spdx()
    doc["relationships"][0]["relationshipType"] = "MATES_WITH"
    assert has_problem(validate_spdx(doc), "relationshipType")


def test_spdx_dangling_relationship():
    doc = fixture_spdx()
    doc["relationships"][0]["relatedSpdxElement"] = "SPDXRef-Missing"
    assert has_problem(validate_spdx(doc), "unknown SPDX id")


def test_spdx_relationship_missing_field():
    doc = fixture_spdx()
    del doc["relationships"][0]["spdxElementId"]
    assert has_problem(validate_spdx(doc), "spdxElementId")


def test_spdx_bad_checksum():
    doc = fixture_spdx()
    for pkg in doc["packages"]:
        if "checksums" in pkg:
            pkg["checksums"] = [{"algorithm": "", "checksumValue": "x"}]
            break
    assert has_problem(validate_spdx(doc), "checksums[0]")


def test_spdx_bad_external_ref():
    doc = fixture_spdx()
    for pkg in doc["packages"]:
        if "externalRefs" in pkg:
            pkg["externalRefs"][0]["referenceLocator"] = ""
            break
    assert has_problem(validate_spdx(doc), "referenceLocator")


def test_spdx_extracted_license_missing_name():
    doc = fixture_spdx()
    if "hasExtractedLicensingInfos" not in doc:
        doc["hasExtractedLicensingInfos"] = [{"licenseId": "LicenseRef-foo"}]
    else:
        del doc["hasExtractedLicensingInfos"][0]["name"]
    assert has_problem(validate_spdx(doc), "name")


def test_spdx_extracted_license_empty_id():
    doc = fixture_spdx()
    doc["hasExtractedLicensingInfos"] = [{"licenseId": "", "name": "X"}]
    assert has_problem(validate_spdx(doc), "licenseId")


def test_spdx_extracted_license_collides_with_package_id():
    doc = fixture_spdx()
    doc["hasExtractedLicensingInfos"] = [
        {"licenseId": doc["packages"][1]["SPDXID"], "name": "X"}
    ]
    assert has_problem(validate_spdx(doc), "collides")


# --- CycloneDX structural checks ------------------------------------------
@pytest.mark.parametrize(
    "field",
    ["bomFormat", "specVersion", "serialNumber", "version", "components"],
)
def test_cdx_missing_document_field(field):
    doc = fixture_cdx()
    del doc[field]
    assert has_problem(validate_cyclonedx(doc), field)


def test_cdx_wrong_bom_format():
    doc = fixture_cdx()
    doc["bomFormat"] = "Spdx"
    assert has_problem(validate_cyclonedx(doc), "bomFormat")


def test_cdx_wrong_spec_version():
    doc = fixture_cdx()
    doc["specVersion"] = "1.4"
    assert has_problem(validate_cyclonedx(doc), "specVersion")


def test_cdx_bad_serial_number():
    doc = fixture_cdx()
    doc["serialNumber"] = "not-a-uuid"
    assert has_problem(validate_cyclonedx(doc), "serialNumber")


def test_cdx_accepts_bare_uuid_serial():
    doc = fixture_cdx()
    doc["serialNumber"] = doc["serialNumber"].replace("urn:uuid:", "")
    assert validate_cyclonedx(doc) == []


def test_cdx_version_must_be_int():
    doc = fixture_cdx()
    doc["version"] = "1"
    assert has_problem(validate_cyclonedx(doc), "version")


def test_cdx_component_missing_name():
    doc = fixture_cdx()
    del doc["components"][0]["name"]
    assert has_problem(validate_cyclonedx(doc), "components[0]")


def test_cdx_component_bad_type():
    doc = fixture_cdx()
    doc["components"][0]["type"] = "widget"
    assert has_problem(validate_cyclonedx(doc), "'type' must be one of")


def test_cdx_component_bad_purl():
    doc = fixture_cdx()
    doc["components"][0]["purl"] = "https://example.com/pkg"
    assert has_problem(validate_cyclonedx(doc), "purl")


def test_cdx_component_bad_cpe():
    doc = fixture_cdx()
    for comp in doc["components"]:
        if "cpe" in comp:
            comp["cpe"] = "cpe-nope"
            break
    assert has_problem(validate_cyclonedx(doc), "cpe")


def test_cdx_component_bad_hash():
    doc = fixture_cdx()
    for comp in doc["components"]:
        if "hashes" in comp:
            comp["hashes"] = [{"alg": "", "content": "x"}]
            break
    assert has_problem(validate_cyclonedx(doc), "hashes[0]")


def test_cdx_component_bad_license_entry():
    doc = fixture_cdx()
    doc["components"][0]["licenses"] = [{"bogus": 1}]
    assert has_problem(validate_cyclonedx(doc), "licenses")


def test_cdx_component_bad_external_reference():
    doc = fixture_cdx()
    for comp in doc["components"]:
        if "externalReferences" in comp:
            comp["externalReferences"][0]["url"] = ""
            break
    assert has_problem(validate_cyclonedx(doc), "url")


def test_cdx_duplicate_bom_ref():
    doc = fixture_cdx()
    first_ref = doc["components"][0]["bom-ref"]
    doc["components"][1]["bom-ref"] = first_ref
    assert has_problem(validate_cyclonedx(doc), "duplicate bom-ref")


def test_cdx_metadata_component_is_validated():
    doc = fixture_cdx()
    doc["metadata"]["component"]["type"] = "widget"
    assert has_problem(validate_cyclonedx(doc), "metadata.component")


def test_cdx_metadata_component_shares_bomref_space():
    doc = fixture_cdx()
    doc["components"][0]["bom-ref"] = "pkg-root"  # metadata.component's ref
    assert has_problem(validate_cyclonedx(doc), "duplicate bom-ref")


def test_cdx_metadata_bad_timestamp():
    doc = fixture_cdx()
    doc["metadata"]["timestamp"] = "soon"
    assert has_problem(validate_cyclonedx(doc), "timestamp")


def test_cdx_metadata_bad_tool():
    doc = fixture_cdx()
    doc["metadata"]["tools"] = [{"noName": 1}]
    assert has_problem(validate_cyclonedx(doc), "tools[0]")


def test_cdx_top_level_licenses_entry():
    doc = fixture_cdx()
    doc["licenses"] = [{"licenseId": "", "license": {"name": "X"}}]
    assert has_problem(validate_cyclonedx(doc), "licenseId")
    doc["licenses"] = [{"licenseId": "LicenseRef-a"}]
    assert has_problem(validate_cyclonedx(doc), "'name'")
    doc["licenses"] = [
        {"licenseId": "LicenseRef-a"},
        {"licenseId": "LicenseRef-a"},
    ]
    assert has_problem(validate_cyclonedx(doc), "duplicate 'licenseId'")


# --- file-level + CLI -----------------------------------------------------
def test_validate_file_round_trips(tmp_path):
    path = write_json(tmp_path, "sbom.spdx.json", fixture_spdx())
    fmt, problems = validate_file(path)
    assert fmt == "spdx"
    assert problems == []


def test_validate_file_forced_format(tmp_path):
    path = write_json(tmp_path, "bom.cdx.json", fixture_cdx())
    fmt, problems = validate_file(path, fmt="cyclonedx")
    assert fmt == "cyclonedx"
    assert problems == []


def test_validate_file_invalid_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SBOMError) as excinfo:
        validate_file(path)
    assert "invalid JSON" in str(excinfo.value)


def test_validate_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_file(tmp_path / "does-not-exist.json")


def test_cli_validate_generated_files(tmp_path):
    # Full pipeline: generate (real subprocess) then validate both outputs.
    out = tmp_path / "sbom.spdx.json"
    cdx = tmp_path / "sbom.cdx.json"
    gen = run_cli(
        "generate",
        "--legal-info", LEGAL,
        "--show-info", SHOWINFO,
        "--out", out,
        "--cdx", cdx,
    )
    assert gen.returncode == 0, gen.stderr

    res = run_cli("validate", out)
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout and "SPDX" in res.stdout

    res = run_cli("validate", cdx)
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout and "CycloneDX" in res.stdout


def test_cli_validate_auto_detects_both_formats(tmp_path):
    spdx_path = write_json(tmp_path, "s.spdx.json", fixture_spdx())
    cdx_path = write_json(tmp_path, "s.cdx.json", fixture_cdx())
    assert run_cli("validate", spdx_path).returncode == constants.EXIT_OK
    assert run_cli("validate", cdx_path).returncode == constants.EXIT_OK


def test_cli_validate_tampered_document_fails(tmp_path):
    doc = fixture_spdx()
    del doc["spdxVersion"]
    path = write_json(tmp_path, "bad.spdx.json", doc)
    res = run_cli("validate", path)
    assert res.returncode == constants.EXIT_ERROR
    assert "spdxVersion" in res.stderr
    assert "problem(s) found" in res.stderr


def test_cli_validate_wrong_forced_format_fails(tmp_path):
    path = write_json(tmp_path, "s.spdx.json", fixture_spdx())
    res = run_cli("validate", path, "--format", "cyclonedx")
    assert res.returncode == constants.EXIT_ERROR
    assert "bomFormat" in res.stderr


def test_cli_validate_undetectable_document_fails(tmp_path):
    path = write_json(tmp_path, "mystery.json", {"mystery": True})
    res = run_cli("validate", path)
    assert res.returncode == constants.EXIT_ERROR
    assert "cannot detect" in res.stderr


def test_cli_validate_invalid_json_fails(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("definitely { not json", encoding="utf-8")
    res = run_cli("validate", path)
    assert res.returncode == constants.EXIT_ERROR
    assert "invalid JSON" in res.stderr


def test_cli_validate_missing_file_fails(tmp_path):
    res = run_cli("validate", tmp_path / "missing.json")
    assert res.returncode == constants.EXIT_ERROR
    assert "error" in res.stderr


def test_main_dispatches_validate_in_process(tmp_path):
    path = write_json(tmp_path, "s.spdx.json", fixture_spdx())
    assert cli.main(["validate", str(path)]) == constants.EXIT_OK
