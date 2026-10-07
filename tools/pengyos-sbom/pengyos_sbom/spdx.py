"""Emit an SPDX 2.3 JSON SBOM from an :class:`IngestResult`.

This is the SPDX side of the deliverable. It consumes *only* the internal
component model (``pengyos_sbom.model.IngestResult``) — it never looks at the
raw Buildroot output — so the Buildroot parsing stays in one place and the
emitter stays deterministic and format-agnostic.

Document shape (SPDX 2.3 tag-value JSON)::

    spdxVersion, dataLicense, SPDXID (SPDXRef-DOCUMENT), name,
    documentDescribes -> [SPDXRef-RootArtifact], creationInfo,
    documentNamespace, packages[], relationships[],
    hasExtractedLicensingInfos[]

The *root artifact* is the rootfs image (``IngestResult.rootfs``); when a build
declares no rootfs target a synthetic one is used so the document always has
exactly one described root. Every package component is ``CONTAINS``-linked from
the root artifact, and the document ``DESCRIBES`` the root artifact.

Determinism
-----------
Given the same :class:`IngestResult` and the same ``created`` timestamp the
emitted document is byte-stable: components are already deterministically
ordered by ingestion, package ids derive from ``(scope, name)``, the document
namespace derives from a content hash, and JSON is serialized with
``sort_keys=True``. A wall-clock timestamp is only used by the CLI when the
caller does not pass one; signing normalizes it out of the hashed body via
``pengyos_sbom.signing.canonical_bytes`` (the "Deterministic output" contract),
so the signed bytes are stable regardless of when the document was generated.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from . import __version__, constants as c
from .licenses import LicenseConverter
from .model import IngestResult, SCOPE_BUILD, SCOPE_HOST, SCOPE_TARGET, Rootfs

# The root artifact's SPDX id (referenced by documentDescribes and by the
# CONTAINS relationships). Kept as a constant so relationships stay stable.
ROOT_SPDXID = "SPDXRef-RootArtifact"
DOC_SPDXID = "SPDXRef-DOCUMENT"

_ID_SPDXID_RE = re.compile(r"[^A-Za-z0-9.-]+")


def _package_spdxid(component) -> str:
    """Deterministic, unique SPDX id for a package component.

    Keyed on ``(scope, name)`` so a target package and its ``host-`` version do
    not collide. Non-SPDX-safe characters in the name are collapsed.
    """
    safe = _ID_SPDXID_RE.sub("-", component.name).strip("-") or "package"
    return f"SPDXRef-Package-{component.scope}-{safe}"


def _spdxid_safe(name: str) -> str:
    return _ID_SPDXID_RE.sub("-", name.strip()).strip("-")


def _root_name(result: IngestResult) -> str:
    """Name of the root artifact (the described rootfs image)."""
    if result.rootfs:
        r: Rootfs = result.rootfs[0]
        return r.image_name or r.name
    return "PengyOS rootfs"


def _document_namespace(result: IngestResult, doc_name: str) -> str:
    """A stable, content-derived document namespace URI.

    The namespace is unique per distinct SBOM (it hashes every component and
    rootfs entry) yet deterministic for the same input — the SPDX requirement
    that two *different* SBOMs not share a namespace.
    """
    h = hashlib.sha256()
    h.update(doc_name.encode("utf-8"))
    for comp in result.components:
        h.update(f"{comp.scope}:{comp.name}:{comp.version}\n".encode("utf-8"))
    for rf in result.rootfs:
        h.update(f"rootfs:{rf.name}\n".encode("utf-8"))
    if result.buildroot_version:
        h.update(f"buildroot:{result.buildroot_version}\n".encode("utf-8"))
    return f"{c.SPDX_NAMESPACE_PREFIX}/{_spdxid_safe(doc_name)}/{h.hexdigest()[:16]}"


def _package_for_component(comp, expr: str) -> Dict:
    pkg: Dict = {
        "SPDXID": _package_spdxid(comp),
        "name": comp.name,
        "downloadLocation": comp.origin or "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": expr,
        "licenseDeclared": expr,
        "copyrightText": "NOASSERTION",
        # We do not classify every package's purpose (we cannot know whether a
        # Buildroot target is a library or an app); "OTHER" is the honest,
        # schema-valid choice.
        "primaryPackagePurpose": "OTHER",
    }
    if comp.version:
        pkg["versionInfo"] = comp.version
    if comp.source_hash is not None:
        pkg["checksums"] = [
            {"algorithm": comp.source_hash.algorithm.upper(), "checksumValue": comp.source_hash.value}
        ]
    if comp.cpe_id:
        pkg["externalRefs"] = [
            {
                "referenceCategory": "SECURITY",
                "referenceType": "cpe23Type",
                "referenceLocator": comp.cpe_id,
            }
        ]
    if comp.origin:
        pkg["sourceInfo"] = f"origin: {comp.origin}"
    return pkg


def _root_package(result: IngestResult) -> Dict:
    """The SPDX package that represents the rootfs image (the described root)."""
    rf: Optional[Rootfs] = result.rootfs[0] if result.rootfs else None
    name = _root_name(result)
    pkg: Dict = {
        "SPDXID": ROOT_SPDXID,
        "name": name,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "primaryPackagePurpose": "OPERATING-SYSTEM",
    }
    if result.buildroot_version:
        pkg["versionInfo"] = result.buildroot_version
    if rf is not None:
        pkg["sourceInfo"] = f"PengyOS rootfs target '{rf.name}'"
    return pkg


def build_spdx_document(
    result: IngestResult,
    created: str,
    doc_name: str = "PengyOS SBOM",
) -> Dict:
    """Build the SPDX 2.3 document (as a plain dict) for *result*.

    :param created: ISO-8601 UTC creation timestamp. Injected by the caller so
        the emitter itself is deterministic for a fixed input.
    :returns: the full SPDX 2.3 document, ready for ``json.dump``.
    """
    converter = LicenseConverter()
    packages: List[Dict] = [_root_package(result)]
    contains: List[Dict] = []

    for comp in result.components:
        expr = converter.convert(comp.licenses)
        pkg = _package_for_component(comp, expr)
        packages.append(pkg)
        contains.append(
            {
                "spdxElementId": ROOT_SPDXID,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": pkg["SPDXID"],
            }
        )

    relationships: List[Dict] = [
        {
            "spdxElementId": DOC_SPDXID,
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": ROOT_SPDXID,
        }
    ]
    relationships.extend(contains)

    doc: Dict = {
        "spdxVersion": f"SPDX-{c.SPDX_VERSION}",
        "dataLicense": "CC0-1.0",
        "SPDXID": DOC_SPDXID,
        "name": doc_name,
        "documentDescribes": [ROOT_SPDXID],
        "creationInfo": {
            "created": created,
            "creators": [f"Tool:pengyos-sbom/{__version__}"],
        },
        "documentNamespace": _document_namespace(result, doc_name),
        "packages": packages,
        "relationships": relationships,
    }

    extracted = converter.extracted()
    if extracted:
        doc["hasExtractedLicensingInfos"] = [
            {
                "licenseId": license_id,
                "name": name,
                "comment": "license identifier not in the SPDX License List",
            }
            for license_id, name in extracted
        ]
    return doc
