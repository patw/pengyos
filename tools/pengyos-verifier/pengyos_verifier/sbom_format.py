"""SBOM structural validity rules (witness-level).

Defines what "the SBOM is valid" means for the verifier. The witness
is NOT a general-purpose SPDX/CycloneDX validator (that is
``pengyos-sbom``'s job); it checks the structural contract a build
output dir's SBOM must meet for the witness to trust it as provenance
evidence: the format detection, the document-level required fields,
the per-package/per-component required fields, id uniqueness, and
relationship resolution — the same rules ``pengyos-sbom validate``
enforces, copied in-tree so the witness never depends on the emitter.

Detection: a JSON object with ``spdxVersion`` is SPDX; one with
``bomFormat`` is CycloneDX; one with neither (or BOTH) markers is not
a recognizable SBOM (an error, reported as such).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

SPDX_VERSION = "SPDX-2.3"
SPDX_DATA_LICENSE = "CC0-1.0"
SPDX_DOCUMENT_ID = "SPDXRef-DOCUMENT"

CYCLONEDX_FORMAT = "CycloneDX"
CYCLONEDX_SPEC_VERSION = "1.5"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

#: The SPDX 2.3 relationshipType enum (SPDX spec, section 20).
SPDX_RELATIONSHIP_TYPES = frozenset({
    "DESCRIBES", "DESCRIBED_BY",
    "DEPENDS_ON", "DEPENDS_ON_BUILD", "DEPENDS_ON_RUNTIME",
    "DEPENDS_ON_OPTIONAL", "DEPENDS_ON_TEST", "DEPENDS_ON_DEV",
    "DEPENDENT_OF",
    "CONTAINS", "CONTAINED_BY",
    "VARIANT_OF", "VARIANT_OF_BUILD", "VARIANT_OF_RUNTIME",
    "VARIANT_OF_TEST", "VARIANT_OF_DEV",
    "DISTRIBUTION_ARTIFACT", "PATCH_FOR", "PATCH_APPLIED",
    "INSTANCES_OF", "GENERATED_FROM",
    "ANCESTOR_OF", "DESCENDANT_OF", "MIRROR_OF",
    "FILE_ANALYZED", "FILE_DROPPED", "OTHER",
})

#: The CycloneDX 1.5 component type enum.
CYCLONEDX_COMPONENT_TYPES = frozenset({
    "application", "framework", "library", "container", "device",
    "firmware", "file", "operating-system", "data",
    "machine-learning-model", "other",
})


def detect_format(data: Any) -> Optional[str]:
    """Return ``"spdx"``, ``"cyclonedx"``, or ``None``.

    ``None`` covers: not a JSON object, no marker at all, or BOTH
    markers present (ambiguous — not a recognizable SBOM).
    """
    if not isinstance(data, dict):
        return None
    has_spdx = "spdxVersion" in data
    has_cdx = "bomFormat" in data
    if has_spdx and not has_cdx:
        return "spdx"
    if has_cdx and not has_spdx:
        return "cyclonedx"
    return None


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _spdx_errors(data: dict) -> List[str]:
    errors: List[str] = []

    def expect_str(doc: Any, field: str) -> None:
        if not _nonempty_str(doc.get(field)):
            errors.append(f"document missing string field {field!r}")

    if data.get("spdxVersion") != SPDX_VERSION:
        errors.append(
            f"spdxVersion must be {SPDX_VERSION!r}, "
            f"got {data.get('spdxVersion')!r}"
        )
    if data.get("dataLicense") != SPDX_DATA_LICENSE:
        errors.append(
            f"dataLicense must be {SPDX_DATA_LICENSE!r}, "
            f"got {data.get('dataLicense')!r}"
        )
    if data.get("SPDXID") != SPDX_DOCUMENT_ID:
        errors.append(
            f"SPDXID must be {SPDX_DOCUMENT_ID!r}, "
            f"got {data.get('SPDXID')!r}"
        )
    expect_str(data, "name")
    expect_str(data, "documentNamespace")

    describes = data.get("documentDescribes")
    if not isinstance(describes, list) or not describes or \
            not all(_nonempty_str(d) for d in describes):
        errors.append(
            "documentDescribes must be a non-empty list of non-empty "
            "strings"
        )

    creation = data.get("creationInfo")
    if not isinstance(creation, dict):
        errors.append("creationInfo must be an object")
    else:
        if not _nonempty_str(creation.get("created")):
            errors.append("creationInfo.created must be a non-empty string")
        creators = creation.get("creators")
        if not isinstance(creators, list) or not creators or \
                not all(_nonempty_str(c) for c in creators):
            errors.append(
                "creationInfo.creators must be a non-empty list of "
                "non-empty strings"
            )

    # -- packages -----------------------------------------------------
    packages = data.get("packages")
    if not isinstance(packages, list):
        errors.append("packages must be a list")
        packages = []
    required_package_fields = (
        "SPDXID", "name", "downloadLocation", "filesAnalyzed",
        "licenseConcluded", "licenseDeclared", "copyrightText",
    )
    for i, pkg in enumerate(packages):
        if not isinstance(pkg, dict):
            errors.append(f"packages[{i}] is not an object")
            continue
        for field in required_package_fields:
            if field not in pkg:
                errors.append(f"packages[{i}] missing {field!r}")
        if "filesAnalyzed" in pkg and \
                not isinstance(pkg["filesAnalyzed"], bool):
            errors.append(f"packages[{i}].filesAnalyzed must be a boolean")

    # -- relationships --------------------------------------------------
    relationships = data.get("relationships")
    if not isinstance(relationships, list):
        errors.append("relationships must be a list")
        relationships = []
    for i, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            errors.append(f"relationships[{i}] is not an object")
            continue
        for field in ("spdxElementId", "relatedSpdxElement",
                      "relationshipType"):
            if not _nonempty_str(rel.get(field)):
                errors.append(f"relationships[{i}] missing {field!r}")
        rel_type = rel.get("relationshipType")
        if isinstance(rel_type, str) and rel_type not in \
                SPDX_RELATIONSHIP_TYPES:
            errors.append(
                f"relationships[{i}].relationshipType {rel_type!r} is "
                "not in the SPDX 2.3 enum"
            )

    # -- id space: uniqueness + resolution ------------------------------
    ids = set()       # for resolution
    ordered_ids = []  # for duplicate detection
    def add_id(spdx_id: str) -> None:
        ids.add(spdx_id)
        ordered_ids.append(spdx_id)

    doc_id = data.get("SPDXID")
    if _nonempty_str(doc_id):
        add_id(doc_id)
    for i, pkg in enumerate(packages):
        if isinstance(pkg, dict) and _nonempty_str(pkg.get("SPDXID")):
            add_id(pkg["SPDXID"])
    extracted = data.get("hasExtractedLicensingInfos")
    if extracted is not None and not isinstance(extracted, list):
        errors.append("hasExtractedLicensingInfos must be a list")
    elif isinstance(extracted, list):
        for i, info in enumerate(extracted):
            if isinstance(info, dict) and \
                    _nonempty_str(info.get("licenseId")):
                add_id(info["licenseId"])

    seen = set()
    for spdx_id in ordered_ids:
        if spdx_id in seen:
            errors.append(f"duplicate SPDX id {spdx_id!r}")
        seen.add(spdx_id)

    if isinstance(describes, list):
        for d in describes:
            if isinstance(d, str) and d not in ids:
                errors.append(
                    f"documentDescribes entry {d!r} does not resolve "
                    "to any id in the document"
                )
    for i, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            continue
        for field in ("spdxElementId", "relatedSpdxElement"):
            ref = rel.get(field)
            if isinstance(ref, str) and ref not in ids:
                errors.append(
                    f"relationships[{i}].{field} {ref!r} does not "
                    "resolve to any id in the document"
                )
    return errors


def _cyclonedx_errors(data: dict) -> List[str]:
    errors: List[str] = []

    if data.get("bomFormat") != CYCLONEDX_FORMAT:
        errors.append(
            f"bomFormat must be {CYCLONEDX_FORMAT!r}, "
            f"got {data.get('bomFormat')!r}"
        )
    if data.get("specVersion") != CYCLONEDX_SPEC_VERSION:
        errors.append(
            f"specVersion must be {CYCLONEDX_SPEC_VERSION!r}, "
            f"got {data.get('specVersion')!r}"
        )
    serial = data.get("serialNumber")
    if not (isinstance(serial, str) and
            (_UUID_RE.match(serial) or
             (serial.startswith("urn:uuid:") and
              _UUID_RE.match(serial[len("urn:uuid:"):])))):
        errors.append(
            "serialNumber must be a UUID (bare or urn:uuid:)"
        )
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        errors.append("version must be an integer")
    components = data.get("components")
    if not isinstance(components, list):
        errors.append("components must be a list")
        components = []

    def check_component(comp: Any, where: str) -> None:
        if not isinstance(comp, dict):
            errors.append(f"{where} is not an object")
            return
        comp_type = comp.get("type")
        if comp_type not in CYCLONEDX_COMPONENT_TYPES:
            errors.append(
                f"{where}.type {comp_type!r} is not in the CycloneDX "
                "1.5 component type enum"
            )
        if not _nonempty_str(comp.get("name")):
            errors.append(f"{where} missing non-empty name")
        purl = comp.get("purl")
        if purl is not None and (
                not isinstance(purl, str) or not purl.startswith("pkg:")):
            errors.append(f"{where}.purl must start with 'pkg:'")
        cpe = comp.get("cpe")
        if cpe is not None and (
                not isinstance(cpe, str) or not cpe.startswith("cpe:")):
            errors.append(f"{where}.cpe must start with 'cpe:'")
        hashes = comp.get("hashes")
        if hashes is not None:
            if not isinstance(hashes, list):
                errors.append(f"{where}.hashes must be a list")
            else:
                for j, entry in enumerate(hashes):
                    if not isinstance(entry, dict) or \
                            not _nonempty_str(entry.get("alg")) or \
                            not _nonempty_str(entry.get("content")):
                        errors.append(
                            f"{where}.hashes[{j}] must carry "
                            "non-empty 'alg' and 'content'"
                        )

    metadata = data.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            errors.append("metadata must be an object")
        elif "component" in metadata:
            check_component(metadata["component"], "metadata.component")
    for i, comp in enumerate(components):
        check_component(comp, f"components[{i}]")

    refs = set()       # not needed beyond uniqueness, kept for clarity
    ordered_refs = []  # for duplicate detection
    def add_ref(bom_ref: str) -> None:
        ordered_refs.append(bom_ref)

    if isinstance(metadata, dict) and isinstance(
            metadata.get("component"), dict):
        bom_ref = metadata["component"].get("bom-ref")
        if isinstance(bom_ref, str) and bom_ref:
            add_ref(bom_ref)
    for i, comp in enumerate(components):
        if isinstance(comp, dict) and isinstance(comp.get("bom-ref"), str) \
                and comp["bom-ref"]:
            add_ref(comp["bom-ref"])
    seen = set()
    for ref in ordered_refs:
        if ref in seen:
            errors.append(f"duplicate bom-ref {ref!r}")
        seen.add(ref)

    top_licenses = data.get("licenses")
    if top_licenses is not None:
        if not isinstance(top_licenses, list):
            errors.append("top-level licenses must be a list")
        else:
            license_ids = set()
            for i, entry in enumerate(top_licenses):
                if not isinstance(entry, dict):
                    errors.append(f"licenses[{i}] is not an object")
                    continue
                license_id = entry.get("licenseId")
                if not _nonempty_str(license_id):
                    errors.append(f"licenses[{i}] missing 'licenseId'")
                elif license_id in license_ids:
                    errors.append(
                        f"duplicate top-level licenseId {license_id!r}"
                    )
                else:
                    license_ids.add(license_id)
                license_obj = entry.get("license")
                if not isinstance(license_obj, dict) or not (
                        _nonempty_str(license_obj.get("name"))):
                    errors.append(
                        f"licenses[{i}].license must be an object with "
                        "a non-empty 'name'"
                    )
    return errors


def sbom_errors(data: Any) -> List[str]:
    """All structural validity errors of a parsed SBOM document.

    ``[]`` means the document is a structurally valid SPDX 2.3 or
    CycloneDX 1.5 document per the rules in this module. The first
    error is specific enough to pinpoint the problem; the rest are the
    remaining problems found on the same walk (the witness reports
    everything it sees, not just the first red flag).
    """
    fmt = detect_format(data)
    if fmt is None:
        if not isinstance(data, dict):
            return ["document is not a JSON object"]
        if "spdxVersion" in data and "bomFormat" in data:
            return [
                "document carries BOTH spdxVersion and bomFormat — "
                "not a recognizable SBOM"
            ]
        return ["document has neither spdxVersion nor bomFormat — "
                "not a recognizable SBOM"]
    return _spdx_errors(data) if fmt == "spdx" else _cyclonedx_errors(data)
