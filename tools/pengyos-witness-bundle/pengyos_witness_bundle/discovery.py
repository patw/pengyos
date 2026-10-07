"""Artifact discovery for pengyos-witness-bundle (implementation task 3).

Builds on the canonical layout data in :mod:`pengyos_witness_bundle.layout`.
Given a build directory, discovery locates each artifact kind (SBOM, CycloneDX,
SBOM signature, repro report) by walking the tree and matching the kind's alias
globs, then reports exactly what is present and what is missing.

A missing artifact is a *normal* discovery outcome — the witness layout may be
incomplete — so discovery never raises on a missing file. It only raises on
structural problems it cannot mean anything (an unknown kind, or a build dir that
points at a regular file rather than a directory). The CLI turns the "build dir
missing" case into a clean, non-zero exit rather than a traceback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .layout import CANONICAL_LAYOUT, find_matching, spec_for


@dataclass(frozen=True)
class DiscoveredArtifact:
    """The discovery outcome for a single artifact kind.

    Attributes:
        kind:      Logical artifact kind, e.g. ``sbom``.
        canonical: Conventional name under ``<build>/.witness/``.
        found:     ``True`` when an existing file matched one of the aliases.
        path:      Build-relative POSIX path of the matched file, else ``None``.
        aliases:   The alias globs that were tried (for reporting / debugging).
    """

    kind: str
    canonical: str
    found: bool
    path: str | None
    aliases: tuple[str, ...]


@dataclass
class DiscoveryResult:
    """The outcome of scanning a build dir for the canonical artifacts.

    Attributes:
        build_dir: The build directory that was scanned (POSIX string).
        artifacts: One :class:`DiscoveredArtifact` per kind, in canonical order.
        present:   The kinds that were found, in canonical order.
        missing:   The kinds that were NOT found, in canonical order.
    """

    build_dir: str
    artifacts: tuple[DiscoveredArtifact, ...] = field(default=tuple())
    present: tuple[str, ...] = field(default=tuple())
    missing: tuple[str, ...] = field(default=tuple())

    @property
    def complete(self) -> bool:
        """``True`` when every artifact kind was discovered."""
        return len(self.missing) == 0

    def missing_message(self) -> str:
        """A human-readable list of what is missing, or ``""`` when complete."""
        if self.complete:
            return ""
        lines = [f"missing artifacts in {self.build_dir}:"]
        for kind in self.missing:
            spec = spec_for(kind)
            lines.append(f"  - {kind} (expected {spec.canonical})")
        return "\n".join(lines)


def discover(build_dir: str | Path) -> DiscoveryResult:
    """Locate each artifact kind under ``build_dir`` by the alias table.

    Walks ``build_dir`` once and, for every kind in the canonical layout, takes
    the first alias that matches an existing file. Returns a
    :class:`DiscoveryResult` describing what was found and what is missing.

    A missing directory yields a complete-empty result (nothing found, nothing
    raised); a build dir that points at a regular file yields an empty result too
    (there are no files *under* it).
    """
    build_dir_str = str(Path(build_dir))

    artifacts: list[DiscoveredArtifact] = []
    present: list[str] = []
    missing: list[str] = []

    for spec in CANONICAL_LAYOUT:
        found_path = find_matching(build_dir_str, spec.kind)
        discovered = DiscoveredArtifact(
            kind=spec.kind,
            canonical=spec.canonical,
            found=found_path is not None,
            path=found_path,
            aliases=spec.aliases,
        )
        artifacts.append(discovered)
        (present if discovered.found else missing).append(spec.kind)

    return DiscoveryResult(
        build_dir=build_dir_str,
        artifacts=tuple(artifacts),
        present=tuple(present),
        missing=tuple(missing),
    )


def missing_kinds(build_dir: str | Path) -> tuple[str, ...]:
    """Convenience: the sorted-by-canonical-order list of missing kinds."""
    return discover(build_dir).missing


def present_kinds(build_dir: str | Path) -> tuple[str, ...]:
    """Convenience: the canonical-order list of present kinds."""
    return discover(build_dir).present
