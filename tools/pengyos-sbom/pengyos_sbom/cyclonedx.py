"""Emit a CycloneDX 1.5 JSON BOM from an :class:`IngestResult`.

This is the CycloneDX side of the deliverable. Like the SPDX emitter it
consumes *only* the internal component model (``pengyos_sbom.model.IngestResult``)
— it never looks at the raw Buildroot output — so both formats stay
deterministic and are derived from one source of truth.

Mapping (CycloneDX 1.5)::

    bomFormat, specVersion, serialNumber, version, metadata, components[],
    licenses[]          (document-level, for custom LicenseRef ids)

* **Root** (``metadata.component``): the rootfs image becomes a single
  ``operating-system`` component named after its ``image_name`` (falling back to
  the target name; a synthetic ``PengyOS rootfs`` when no rootfs target is
  present), with the Buildroot version as its version. This mirrors the SPDX
  document's described ``SPDXRef-RootArtifact``.
* **Components**: one ``library``-less ``other`` component per model component.
  We deliberately do not classify each Buildroot package (we cannot know whether
  it is a library, an app, or firmware), so the schema-valid ``other`` type is
  the honest choice — the CycloneDX analogue of the SPDX ``OTHER`` purpose.
  Each carries a deterministic ``bom-ref`` keyed on ``(scope, name)`` (so a
  target and its ``host-`` version are distinct), a ``purl``, its single
  primary-source SHA-256 ``hash`` (only when a source was saved), its SPDX
  license ``expression``, its ``cpe`` (when known), and a ``distribution``
  external reference to the origin URL (when known).
* **License expressions** reuse :class:`pengyos_sbom.licenses.LicenseConverter`,
  so the SPDX and CycloneDX documents agree on every license spelling. Custom
  ``LicenseRef-<slug>`` ids are declared once in the BOM's top-level ``licenses``
  array (CycloneDX's mechanism for custom license identifiers) so the BOM stays
  self-consistent.

Determinism
-----------
Given the same :class:`IngestResult` and the same ``created`` timestamp the
emitted BOM is byte-stable: components are already deterministically ordered by
ingestion, ``bom-ref``/``purl`` derive from ``(scope, name, version)``, the
``serialNumber`` derives from a content hash (not a wall-clock UUID), and the
timestamp is only a parameter the caller injects. Signing normalizes it out of
the hashed body via ``pengyos_sbom.signing.canonical_bytes`` (the
"Deterministic output" contract), so a signature is stable across
re-generation at a different instant.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Dict, List, Optional

# The fixed name-space UUID used to derive deterministic v5 serial numbers.
# (uuid.uuid5 is a stable hash-based UUID, so the same input always maps to the
# same serialNumber and two distinct builds never collide.)
_SERIAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://pengyos.catbee.ca/sbom/cyclonedx")

# The internal model stores algorithms lower-case ("sha256"); CycloneDX's hash
# ``alg`` enum uses the dashed, upper-case spelling ("SHA-256").
_CYCLONEDX_ALG = {
    "sha256": "SHA-256",
    "sha1": "SHA-1",
    "md5": "MD5",
    "sha512": "SHA-512",
}

from . import __version__, constants as c
from .licenses import LicenseConverter
from .model import IngestResult, Rootfs

def _root_name(result: IngestResult) -> str:
    """Name of the root operating-system component (the rootfs image)."""
    if result.rootfs:
        r: Rootfs = result.rootfs[0]
        return r.image_name or r.name
    return "PengyOS rootfs"


def _serial_number(result: IngestResult) -> str:
    """A stable, content-derived BOM serial number.

    CycloneDX normally uses a random UUID here; we derive a UUIDv5-style hex
    string from the component set + rootfs + buildroot version so the same
    input always reproduces the same serialNumber (the "Deterministic output"
    goal), while two distinct builds never collide.
    """
    h = hashlib.sha256()
    for comp in result.components:
        h.update(f"{comp.scope}:{comp.name}:{comp.version}\n".encode("utf-8"))
    for rf in result.rootfs:
        h.update(f"rootfs:{rf.name}:{rf.image_name or ''}\n".encode("utf-8"))
    if result.buildroot_version:
        h.update(f"buildroot:{result.buildroot_version}\n".encode("utf-8"))
    return uuid.uuid5(_SERIAL_NAMESPACE, h.hexdigest()).urn


def _bom_ref(component) -> str:
    """Deterministic, unique ``bom-ref`` for a component (scoped to avoid
    target/host name collisions)."""
    return f"pkg-{component.scope}-{component.name}"


def _purl(component) -> str:
    """A deterministic package URL.

    We cannot derive a fully-qualified purl namespace for an arbitrary Buildroot
    package, so the honest, schema-valid form is ``pkg:generic/<name>[@<version>]``
    — unique per ``(name, version)`` and stable across runs.
    """
    base = f"pkg:generic/{component.name}"
    if component.version:
        base += f"@{component.version}"
    return base


def _licenses_field(expr: str) -> List[Dict]:
    """Map an SPDX license expression to a CycloneDX ``licenses`` array.

    CycloneDX 1.5 accepts an SPDX license ``expression`` on a component; we pass
    the (already-converted) expression through verbatim. ``NOASSERTION`` is a
    legal SPDX assertion token and is therefore valid here too.
    """
    return [{"expression": expr}]


def _component_for_component(comp, expr: str) -> Dict:
    comp_dict: Dict = {
        "type": "other",
        "bom-ref": _bom_ref(comp),
        "name": comp.name,
        "purl": _purl(comp),
        "licenses": _licenses_field(expr),
    }
    if comp.version:
        comp_dict["version"] = comp.version
    if comp.source_hash is not None:
        alg = _CYCLONEDX_ALG.get(comp.source_hash.algorithm, comp.source_hash.algorithm.upper())
        comp_dict["hashes"] = [{"alg": alg, "content": comp.source_hash.value}]
    if comp.cpe_id:
        comp_dict["cpe"] = comp.cpe_id
    if comp.origin:
        comp_dict["externalReferences"] = [
            {"type": "distribution", "url": comp.origin}
        ]
    return comp_dict


def _root_component(result: IngestResult) -> Dict:
    """The ``operating-system`` component in ``metadata.component`` (the root)."""
    rf: Optional[Rootfs] = result.rootfs[0] if result.rootfs else None
    comp: Dict = {
        "type": "operating-system",
        "bom-ref": "pkg-root",
        "name": _root_name(result),
    }
    if result.buildroot_version:
        comp["version"] = result.buildroot_version
    if rf is not None:
        comp["purl"] = f"pkg:generic/{rf.name}@{result.buildroot_version}" if result.buildroot_version else f"pkg:generic/{rf.name}"
    return comp


def build_cyclonedx_document(
    result: IngestResult,
    created: str,
    doc_name: str = "PengyOS SBOM",
) -> Dict:
    """Build the CycloneDX 1.5 BOM (as a plain dict) for *result*.

    :param created: ISO-8601 UTC timestamp (injected by the caller so the
        emitter is deterministic for a fixed input).
    :param doc_name: informational name only (does not alter the BOM body).
    :returns: the full CycloneDX 1.5 BOM, ready for ``json.dump``.
    """
    converter = LicenseConverter()
    components: List[Dict] = []
    for comp in result.components:
        expr = converter.convert(comp.licenses)
        components.append(_component_for_component(comp, expr))

    doc: Dict = {
        "bomFormat": c.CYCLONEDX_BOM_FORMAT,
        "specVersion": c.CYCLONEDX_SPEC_VERSION,
        "serialNumber": _serial_number(result),
        "version": 1,
        "metadata": {
            "timestamp": created,
            "tools": [{"name": "pengyos-sbom", "version": __version__}],
            "component": _root_component(result),
        },
        "components": components,
    }

    # Declare any custom LicenseRef ids so license expressions stay resolvable.
    extracted = converter.extracted()
    if extracted:
        doc["licenses"] = [
            {"licenseId": license_id, "license": {"name": name}}
            for license_id, name in extracted
        ]
    return doc
