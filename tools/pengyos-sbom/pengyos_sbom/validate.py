"""Structural validation for SPDX 2.3 and CycloneDX 1.5 SBOM documents.

This is the engine behind the ``validate`` subcommand. It checks the
*structural* contract of each format — required fields, field types, enum
values, id uniqueness and referential integrity (``documentDescribes`` /
relationship endpoints must resolve to ids that actually exist in the
document) — without re-parsing any Buildroot output. A document is a plain
``dict`` (already ``json.loads``-ed); all findings are returned as a list of
human-readable problem strings, empty when the document is valid.

Scope note (see spec.md "Non-goals"): this is *not* a general-purpose SPDX
validator. It validates documents against the pinned contract this tool emits
(SPDX 2.3, CycloneDX 1.5) so that ``pengyos-sbom sign/verify`` only ever acts
on well-formed input.

Functions
---------
``detect_format(doc)``            "spdx" / "cyclonedx" / ``None``
``validate_spdx(doc)``            list of problems (empty = valid)
``validate_cyclonedx(doc)``       list of problems (empty = valid)
``validate_document(doc, fmt)``   auto-detect (``fmt="auto"``) then validate;
                                  returns ``(format, problems)``
``validate_file(path, fmt)``      reads + parses a JSON file then validates;
                                  raises :class:`SBOMError` on parse failure
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import constants as c
from .errors import SBOMError
from .spdx import DOC_SPDXID

# SPDX 2.3 relationship-type enum (spec 2.3, table "Relationship Types").
SPDX_RELATIONSHIP_TYPES = frozenset(
    {
        "DESCRIBES",
        "DESCRIBEDBY",
        "CONTAINS",
        "CONTAINEDBY",
        "GENERATED_BY",
        "GENERATES",
        "ANCESTOR_OF",
        "DESCENDANT_OF",
        "VARIANT_OF",
        "FILE_ADDED_TO",
        "FILE_DELETED_FROM",
        "EXPANDED_FROM_ARCHIVE",
        "DYNAMIC_LINK",
        "STATIC_LINK",
        "DATA_FLOW_FROM",
        "DATA_FLOW_TO",
        "DEPENDS_ON",
        "DEPENDENCY_OF",
        "DEPENDENT_OF",
        "OTHER",
    }
)

# SPDX 2.3 ``creationInfo.creators`` prefix enum.
SPDX_CREATOR_PREFIXES = ("Person", "Organization", "Tool")

# CycloneDX 1.5 component-type enum.
CYCLONEDX_COMPONENT_TYPES = frozenset(
    {
        "application",
        "container",
        "device",
        "file",
        "firmware",
        "framework",
        "library",
        "operating-system",
        "other",
    }
)

_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


# --- small predicates -----------------------------------------------------
def _is_iso8601(value: str) -> bool:
    """True if *value* parses as an ISO-8601 timestamp (``Z`` allowed)."""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _is_uuid(value: str) -> bool:
    """True if *value* is a UUID (bare, or ``urn:uuid:``-prefixed)."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("urn:uuid:"):
        value = value[len("urn:uuid:"):]
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and len(value) > 0


# --- SPDX 2.3 -------------------------------------------------------------
def _check_spdx_package(pkg, index: int, problems: List[str]) -> str:
    """Validate one SPDX package dict; return its SPDXID (or ``""``)."""
    where = f"packages[{index}]"
    if not isinstance(pkg, dict):
        problems.append(f"{where}: must be an object, got {type(pkg).__name__}")
        return ""

    spdxid = pkg.get("SPDXID")
    if not _nonempty_str(spdxid):
        problems.append(f"{where}: missing required field 'SPDXID' (non-empty string)")

    for field in (
        "name",
        "downloadLocation",
        "licenseConcluded",
        "licenseDeclared",
        "copyrightText",
    ):
        if not _nonempty_str(pkg.get(field)):
            problems.append(f"{where}: missing required field '{field}' (non-empty string)")

    if "filesAnalyzed" in pkg and not isinstance(pkg["filesAnalyzed"], bool):
        problems.append(f"{where}: 'filesAnalyzed' must be a boolean")

    if "checksums" in pkg:
        checksums = pkg["checksums"]
        if not isinstance(checksums, list):
            problems.append(f"{where}: 'checksums' must be an array")
        else:
            for i, cs in enumerate(checksums):
                if not isinstance(cs, dict):
                    problems.append(f"{where}: checksums[{i}] must be an object")
                    continue
                if not _nonempty_str(cs.get("algorithm")):
                    problems.append(f"{where}: checksums[{i}] missing required field 'algorithm'")
                if not _nonempty_str(cs.get("checksumValue")):
                    problems.append(f"{where}: checksums[{i}] missing required field 'checksumValue'")

    if "externalRefs" in pkg:
        refs = pkg["externalRefs"]
        if not isinstance(refs, list):
            problems.append(f"{where}: 'externalRefs' must be an array")
        else:
            for i, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    problems.append(f"{where}: externalRefs[{i}] must be an object")
                    continue
                for field in ("referenceCategory", "referenceType", "referenceLocator"):
                    if not _nonempty_str(ref.get(field)):
                        problems.append(
                            f"{where}: externalRefs[{i}] missing required field '{field}'"
                        )
    return spdxid if _nonempty_str(spdxid) else ""


def validate_spdx(doc) -> List[str]:
    """Validate an SPDX 2.3 document dict; return a list of problems."""
    problems: List[str] = []
    if not isinstance(doc, dict):
        return [f"root must be a JSON object, got {type(doc).__name__}"]

    spdx_version = doc.get("spdxVersion")
    expected_version = f"SPDX-{c.SPDX_VERSION}"
    if spdx_version is None:
        problems.append("missing required field 'spdxVersion'")
    elif spdx_version != expected_version:
        problems.append(f"'spdxVersion' must be {expected_version!r}, got {spdx_version!r}")

    if doc.get("dataLicense") != "CC0-1.0":
        problems.append(f"'dataLicense' must be 'CC0-1.0', got {doc.get('dataLicense')!r}")

    if doc.get("SPDXID") != DOC_SPDXID:
        problems.append(f"'SPDXID' must be {DOC_SPDXID!r}, got {doc.get('SPDXID')!r}")

    if not _nonempty_str(doc.get("name")):
        problems.append("missing required field 'name' (non-empty string)")

    namespace = doc.get("documentNamespace")
    if not _nonempty_str(namespace):
        problems.append("missing required field 'documentNamespace' (non-empty string)")
    elif not _URI_SCHEME_RE.match(namespace):
        problems.append(
            f"'documentNamespace' must be a URI with a scheme, got {namespace!r}"
        )

    describes = doc.get("documentDescribes")
    if not isinstance(describes, list) or not describes:
        problems.append(
            "missing required field 'documentDescribes' (non-empty array of SPDX ids)"
        )
    else:
        for i, ref in enumerate(describes):
            if not _nonempty_str(ref):
                problems.append(f"documentDescribes[{i}] must be a non-empty string")

    info = doc.get("creationInfo")
    if not isinstance(info, dict):
        problems.append("missing required field 'creationInfo' (object)")
    else:
        created = info.get("created")
        if not _nonempty_str(created):
            problems.append("creationInfo: missing required field 'created'")
        elif not _is_iso8601(created):
            problems.append(
                f"creationInfo: 'created' must be an ISO-8601 timestamp, got {created!r}"
            )
        creators = info.get("creators")
        if not isinstance(creators, list) or not creators:
            problems.append(
                "creationInfo: missing required field 'creators' (non-empty array)"
            )
        else:
            for i, creator in enumerate(creators):
                if not _nonempty_str(creator):
                    problems.append(f"creationInfo: creators[{i}] must be a non-empty string")
                    continue
                if not any(creator.startswith(p + ":") for p in SPDX_CREATOR_PREFIXES):
                    problems.append(
                        f"creationInfo: creators[{i}] must start with one of "
                        f"{', '.join(SPDX_CREATOR_PREFIXES)}:, got {creator!r}"
                    )

    known_ids = {DOC_SPDXID}

    # Resolution is checked after packages are collected (below), since
    # documentDescribes must point at packages that exist in this document.
    describes_refs = describes if isinstance(describes, list) else []

    packages = doc.get("packages")
    if not isinstance(packages, list):
        problems.append("missing required field 'packages' (array)")
        packages = []
    seen_pkg_ids = set()
    for i, pkg in enumerate(packages):
        pkg_id = _check_spdx_package(pkg, i, problems)
        if pkg_id:
            if pkg_id in known_ids:
                problems.append(f"packages[{i}]: SPDXID {pkg_id!r} collides with the document id")
            if pkg_id in seen_pkg_ids:
                problems.append(f"packages[{i}]: duplicate SPDXID {pkg_id!r}")
            seen_pkg_ids.add(pkg_id)
            known_ids.add(pkg_id)

    for i, ref in enumerate(describes_refs):
        if _nonempty_str(ref) and ref not in known_ids:
            problems.append(
                f"documentDescribes[{i}] references unknown SPDX id {ref!r}"
            )

    relationships = doc.get("relationships")
    if relationships is not None:
        if not isinstance(relationships, list):
            problems.append("'relationships' must be an array")
        else:
            for i, rel in enumerate(relationships):
                where = f"relationships[{i}]"
                if not isinstance(rel, dict):
                    problems.append(f"{where} must be an object")
                    continue
                if rel.get("relationshipType") not in SPDX_RELATIONSHIP_TYPES:
                    problems.append(
                        f"{where}: 'relationshipType' must be one of the SPDX 2.3 "
                        f"relationship types, got {rel.get('relationshipType')!r}"
                    )
                for field in ("spdxElementId", "relatedSpdxElement"):
                    ref = rel.get(field)
                    if not _nonempty_str(ref):
                        problems.append(f"{where}: missing required field '{field}'")
                    elif ref not in known_ids:
                        problems.append(
                            f"{where}: '{field}' references unknown SPDX id {ref!r}"
                        )

    extracted = doc.get("hasExtractedLicensingInfos")
    if extracted is not None:
        if not isinstance(extracted, list):
            problems.append("'hasExtractedLicensingInfos' must be an array")
        else:
            seen_license_ids = set()
            for i, info_entry in enumerate(extracted):
                where = f"hasExtractedLicensingInfos[{i}]"
                if not isinstance(info_entry, dict):
                    problems.append(f"{where} must be an object")
                    continue
                license_id = info_entry.get("licenseId")
                if not _nonempty_str(license_id):
                    problems.append(f"{where}: missing required field 'licenseId'")
                else:
                    if license_id in known_ids:
                        problems.append(
                            f"{where}: 'licenseId' {license_id!r} collides with an "
                            f"element SPDXID"
                        )
                    if license_id in seen_license_ids:
                        problems.append(f"{where}: duplicate 'licenseId' {license_id!r}")
                    seen_license_ids.add(license_id)
                if not _nonempty_str(info_entry.get("name")):
                    problems.append(f"{where}: missing required field 'name'")

    return problems


# --- CycloneDX 1.5 --------------------------------------------------------
def _check_cdx_component(comp, where: str, problems: List[str], bom_refs: set) -> None:
    """Validate one CycloneDX component dict (used for metadata.component too)."""
    if not isinstance(comp, dict):
        problems.append(f"{where}: must be an object, got {type(comp).__name__}")
        return

    comp_type = comp.get("type")
    if comp_type not in CYCLONEDX_COMPONENT_TYPES:
        problems.append(
            f"{where}: 'type' must be one of {sorted(CYCLONEDX_COMPONENT_TYPES)}, "
            f"got {comp_type!r}"
        )

    if not _nonempty_str(comp.get("name")):
        problems.append(f"{where}: missing required field 'name' (non-empty string)")

    bom_ref = comp.get("bom-ref")
    if bom_ref is not None:
        if not _nonempty_str(bom_ref):
            problems.append(f"{where}: 'bom-ref' must be a non-empty string")
        elif bom_ref in bom_refs:
            problems.append(f"{where}: duplicate bom-ref {bom_ref!r}")
        else:
            bom_refs.add(bom_ref)

    if "version" in comp and not _nonempty_str(comp.get("version")):
        problems.append(f"{where}: 'version' must be a non-empty string when present")

    purl = comp.get("purl")
    if purl is not None and (not _nonempty_str(purl) or not purl.startswith("pkg:")):
        problems.append(f"{where}: 'purl' must start with 'pkg:', got {purl!r}")

    cpe = comp.get("cpe")
    if cpe is not None and (not _nonempty_str(cpe) or not cpe.startswith("cpe:")):
        problems.append(f"{where}: 'cpe' must start with 'cpe:', got {cpe!r}")

    hashes = comp.get("hashes")
    if hashes is not None:
        if not isinstance(hashes, list):
            problems.append(f"{where}: 'hashes' must be an array")
        else:
            for i, h in enumerate(hashes):
                if not isinstance(h, dict):
                    problems.append(f"{where}: hashes[{i}] must be an object")
                    continue
                for field in ("alg", "content"):
                    if not _nonempty_str(h.get(field)):
                        problems.append(
                            f"{where}: hashes[{i}] missing required field '{field}'"
                        )

    _check_cdx_licenses(comp.get("licenses"), f"{where}.licenses", problems)

    refs = comp.get("externalReferences")
    if refs is not None:
        if not isinstance(refs, list):
            problems.append(f"{where}: 'externalReferences' must be an array")
        else:
            for i, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    problems.append(f"{where}: externalReferences[{i}] must be an object")
                    continue
                if not _nonempty_str(ref.get("url")):
                    problems.append(
                        f"{where}: externalReferences[{i}] missing required field 'url'"
                    )


def _check_cdx_licenses(licenses, where: str, problems: List[str]) -> None:
    if licenses is None:
        return
    if not isinstance(licenses, list):
        problems.append(f"{where} must be an array")
        return
    for i, entry in enumerate(licenses):
        if not isinstance(entry, dict):
            problems.append(f"{where}[{i}] must be an object")
            continue
        expression = entry.get("expression")
        license_obj = entry.get("license")
        has_expression = _nonempty_str(expression)
        has_license = isinstance(license_obj, dict) and (
            _nonempty_str(license_obj.get("id")) or _nonempty_str(license_obj.get("name"))
        )
        if not has_expression and not has_license:
            problems.append(
                f"{where}[{i}] must carry a non-empty 'expression' or a 'license' "
                f"object with 'id' or 'name'"
            )
        if expression is not None and not has_expression:
            problems.append(f"{where}[{i}]: 'expression' must be a non-empty string")


def validate_cyclonedx(doc) -> List[str]:
    """Validate a CycloneDX 1.5 document dict; return a list of problems."""
    problems: List[str] = []
    if not isinstance(doc, dict):
        return [f"root must be a JSON object, got {type(doc).__name__}"]

    if doc.get("bomFormat") != c.CYCLONEDX_BOM_FORMAT:
        problems.append(
            f"'bomFormat' must be {c.CYCLONEDX_BOM_FORMAT!r}, got {doc.get('bomFormat')!r}"
        )

    spec_version = doc.get("specVersion")
    if not _nonempty_str(spec_version):
        problems.append("missing required field 'specVersion'")
    elif spec_version != c.CYCLONEDX_SPEC_VERSION:
        problems.append(
            f"'specVersion' must be {c.CYCLONEDX_SPEC_VERSION!r}, got {spec_version!r}"
        )

    serial = doc.get("serialNumber")
    if not _nonempty_str(serial):
        problems.append("missing required field 'serialNumber' (non-empty string)")
    elif not _is_uuid(serial):
        problems.append(f"'serialNumber' must be a UUID, got {serial!r}")

    if not isinstance(doc.get("version"), int) or isinstance(doc.get("version"), bool):
        problems.append("'version' must be an integer")

    bom_refs: set = set()

    metadata = doc.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            problems.append("'metadata' must be an object")
        else:
            timestamp = metadata.get("timestamp")
            if timestamp is not None and (
                not _nonempty_str(timestamp) or not _is_iso8601(timestamp)
            ):
                problems.append(
                    f"metadata: 'timestamp' must be an ISO-8601 string, got {timestamp!r}"
                )
            tools = metadata.get("tools")
            if tools is not None:
                if not isinstance(tools, list):
                    problems.append("metadata: 'tools' must be an array")
                else:
                    for i, tool in enumerate(tools):
                        if not isinstance(tool, dict) or not _nonempty_str(tool.get("name")):
                            problems.append(
                                f"metadata: tools[{i}] must be an object with a "
                                f"non-empty 'name'"
                            )
            if "component" in metadata:
                _check_cdx_component(
                    metadata["component"], "metadata.component", problems, bom_refs
                )

    components = doc.get("components")
    if components is None:
        problems.append("missing required field 'components' (array)")
    elif not isinstance(components, list):
        problems.append("'components' must be an array")
    else:
        for i, comp in enumerate(components):
            _check_cdx_component(comp, f"components[{i}]", problems, bom_refs)

    top_licenses = doc.get("licenses")
    if top_licenses is not None:
        if not isinstance(top_licenses, list):
            problems.append("'licenses' (top-level) must be an array")
        else:
            seen_license_ids = set()
            for i, entry in enumerate(top_licenses):
                where = f"licenses[{i}]"
                if not isinstance(entry, dict):
                    problems.append(f"{where} must be an object")
                    continue
                license_id = entry.get("licenseId")
                if not _nonempty_str(license_id):
                    problems.append(f"{where}: missing required field 'licenseId'")
                else:
                    if license_id in seen_license_ids:
                        problems.append(f"{where}: duplicate 'licenseId' {license_id!r}")
                    seen_license_ids.add(license_id)
                license_obj = entry.get("license")
                if not isinstance(license_obj, dict) or not _nonempty_str(
                    license_obj.get("name")
                ):
                    problems.append(
                        f"{where}: must carry a 'license' object with a non-empty "
                        f"'name'"
                    )

    return problems


# --- auto-detection + entry points ----------------------------------------
def detect_format(doc) -> Optional[str]:
    """Best-effort format detection.

    Returns ``"spdx"`` for objects carrying ``spdxVersion``, ``"cyclonedx"``
    for objects carrying ``bomFormat``, and ``None`` when the document is not
    an object, or carries neither (or both) markers.
    """
    if not isinstance(doc, dict):
        return None
    is_spdx = "spdxVersion" in doc
    is_cdx = "bomFormat" in doc
    if is_spdx and is_cdx:
        return None
    if is_spdx:
        return "spdx"
    if is_cdx:
        return "cyclonedx"
    return None


def validate_document(doc, fmt: str = "auto") -> Tuple[str, List[str]]:
    """Validate *doc*; returns ``(format, problems)``.

    ``fmt`` is ``"auto"`` (detect), ``"spdx"`` or ``"cyclonedx"`` (force — the
    document is validated against that format even if it looks like the other
    one, so forced-format mismatches surface as problems rather than an
    ambiguous-detection error).
    """
    if fmt == "auto":
        detected = detect_format(doc)
        if detected is None:
            return (
                "unknown",
                [
                    "cannot detect SBOM format: document is not an SPDX 2.3 or "
                    "CycloneDX JSON object (expected 'spdxVersion' or 'bomFormat')"
                ],
            )
        fmt = detected
    if fmt == "spdx":
        return "spdx", validate_spdx(doc)
    if fmt == "cyclonedx":
        return "cyclonedx", validate_cyclonedx(doc)
    raise SBOMError(f"unknown format {fmt!r} (expected 'auto', 'spdx' or 'cyclonedx')")


def validate_file(path, fmt: str = "auto") -> Tuple[str, List[str]]:
    """Read + parse + validate the JSON file at *path*.

    :raises FileNotFoundError: if the file does not exist.
    :raises SBOMError: if the file is not valid JSON.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SBOMError(f"invalid JSON: {exc}") from exc
    return validate_document(doc, fmt)
