"""Internal component model for pengyos-sbom.

This is the single canonical representation of "everything we know about a
package that went into the PengyOS image", merged from Buildroot
``legal-info`` (manifest CSVs + saved license texts + saved source
artifacts) and ``show-info`` (per-package JSON blurbs).

The SPDX / CycloneDX emitters (later subtasks) consume this model only; they
never look at the raw Buildroot output. That keeps the Buildroot-specific
parsing in one place and lets every downstream format stay deterministic.

Scope of a component
--------------------
Buildroot splits packages into two manifests:

* ``manifest.csv``       -- *target* packages (installed into the image),
* ``host-manifest.csv``  -- *host* (build-time) packages, plus one special
  ``buildroot`` row (the Buildroot build system itself).

The same raw name can legitimately appear in both (a package and its host
version), so the model keys components on ``(scope, name)``:

* ``scope == "target"``  -- from ``manifest.csv``; show-info key ``<name>``,
* ``scope == "host"``    -- from ``host-manifest.csv``; show-info key
  ``host-<name>``,
* ``scope == "build"``   -- the special ``buildroot`` row; show-info key
  ``host-buildroot``.

Hashes
------
Buildroot 2026.x ``legal-info`` does *not* carry per-package digests in the
CSV. Instead it saves the source material under
``sources/<name>-<version>/`` (target) / ``host-sources/<name>-<version>/``
(host) -- the original download, any extra downloads, the applied patches
and a ``series`` file. Ingestion hashes every saved file with SHA-256 so the
model carries real, reproducible digests (see ``Component.hashes``).
``Component.source_hash`` picks the digest of the component's primary
source artifact when one was saved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# The three component scopes, in deterministic sort order.
SCOPE_TARGET = "target"
SCOPE_HOST = "host"
SCOPE_BUILD = "build"

_SCOPE_ORDER = {SCOPE_BUILD: 0, SCOPE_HOST: 1, SCOPE_TARGET: 2}


def scope_sort_key(component: "Component"):
    """Deterministic ordering key: scope (build, host, target) then name."""
    return (_SCOPE_ORDER.get(component.scope, 99), component.name)


@dataclass(frozen=True)
class Hash:
    """A content hash: algorithm name + lowercase hex digest."""

    algorithm: str
    value: str

    def spdx(self) -> str:
        """SPDX checksum spelling, e.g. ``sha256=abcdef...``."""
        return f"{self.algorithm}={self.value}"


@dataclass(frozen=True)
class Download:
    """One downloaded source file and the URIs it can be fetched from.

    Mirrors a show-info ``downloads[]`` entry:
    ``{"source": "zlib-1.3.1.tar.xz", "uris": ["https://zlib.net/zlib-1.3.1.tar.xz", ...]}``.
    """

    source: str
    uris: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ShowInfoEntry:
    """One parsed entry from a ``make show-info`` JSON document.

    Only the fields the SBOM cares about are extracted; everything else in
    the blurb (stamp dirs, install flags, ...) is deliberately dropped.
    ``None`` marks a field the blurb did not carry (virtual packages have
    no version/licenses, rootfs entries have no package details at all).
    """

    key: str
    kind: str  # "target" | "host" | "rootfs" | <future types kept verbatim>
    virtual: bool = False
    version: Optional[str] = None
    licenses: Optional[str] = None  # free-form, e.g. "MIT" or "GPL-2.0+, LGPL-2.1+"
    license_files: Tuple[str, ...] = ()
    redistributable: Optional[bool] = None
    downloads: Tuple[Download, ...] = ()
    hash_files: Tuple[str, ...] = ()  # buildroot .hash file *paths* (not digests)
    patches: Tuple[str, ...] = ()
    provides: Tuple[str, ...] = ()
    cpe_id: Optional[str] = None
    dependencies: Tuple[str, ...] = ()
    reverse_dependencies: Tuple[str, ...] = ()
    image_name: Optional[str] = None  # rootfs entries only
    package_dir: Optional[str] = None


@dataclass(frozen=True)
class Rootfs:
    """A root filesystem target (e.g. ``rootfs-ext4``) from show-info.

    Rootfs targets are *not* package components; in the SBOM they become
    the document/root artifact the packages are contained in.
    """

    name: str
    image_name: Optional[str] = None
    dependencies: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Component:
    """One package known to have gone into the PengyOS build.

    Fields filled by the legal-info manifests are authoritative; fields
    filled from show-info are marked with comments where they differ.
    """

    name: str
    scope: str
    version: str = ""
    licenses: str = ""  # manifest LICENSE column; e.g. "MIT" or "GPL-2.0+,LGPL-2.1+"
    license_files: Tuple[str, ...] = ()  # manifest LICENSE FILES column
    license_texts: Tuple[Tuple[str, str], ...] = ()  # (filename, text)
    source_site: str = ""  # manifest SOURCE SITE column ("" when "not saved")
    archive: str = ""  # manifest SOURCE ARCHIVE column ("" when "not saved")
    origin: str = ""  # best origin URL: first download URI, else source_site
    downloads: Tuple[Download, ...] = ()  # show-info
    hashes: Tuple[Tuple[str, Hash], ...] = ()  # (path relative to legal-info/, Hash)
    patches: Tuple[str, ...] = ()  # show-info patch paths
    provides: Tuple[str, ...] = ()  # show-info
    cpe_id: str = ""  # show-info
    dependencies: Tuple[str, ...] = ()  # show-info dependency names
    redistributable: Optional[bool] = None  # show-info
    virtual: bool = False  # show-info
    package_dir: str = ""  # show-info package_dir (buildroot source dir)

    @property
    def source_hash(self) -> Optional[Hash]:
        """Digest of the component's primary saved source artifact.

        Prefers the original download named by ``self.archive``; falls back
        to a repackaged ``<name>-<version>.tar.gz`` (local/override
        packages); else ``None`` (no source was saved).
        """
        for path, h in self.hashes:
            if path.endswith("/" + self.archive):
                return h
        for path, h in self.hashes:
            stem = f"{self.name}-{self.version}" if self.version else self.name
            if path.endswith("/" + stem + ".tar.gz"):
                return h
        return None


@dataclass(frozen=True)
class IngestResult:
    """Everything `ingest()` knows about the build, in deterministic order."""

    components: Tuple[Component, ...]
    rootfs: Tuple[Rootfs, ...] = ()
    warnings: Tuple[str, ...] = ()
    buildroot_version: str = ""  # from the special buildroot manifest row
    skipped_virtual: Tuple[str, ...] = ()  # show-info virtual aliases, no component

    def component(self, scope: str, name: str) -> Optional[Component]:
        for c in self.components:
            if c.scope == scope and c.name == name:
                return c
        return None
