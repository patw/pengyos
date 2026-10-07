"""Emit a witness-ready view (implementation task 5).

Given a build dir and the :class:`~pengyos_witness_bundle.discovery.DiscoveryResult`
for it, this module materialises the *canonical witness layout* under
``<build>/.witness/`` by copying (or symlinking) each **discovered** artifact to
its conventional name. Whatever ``pengyos-build`` actually emitted — under
``images/<target>.spdx.json`` and friends — now also exists at the conventional
name the external witness looks for, so the witness can *see what was built*.

The bundler is a pure side-effecting operation on the filesystem: it never raises
on a missing artifact (an incomplete build simply bundles fewer artifacts) and
only raises on a structural failure it cannot mean anything (a ``.witness``
destination that points at a directory, or a copy that fails). The CLI turns
those into clean, non-zero exits rather than tracebacks.

Two materialisation modes are offered:

* ``"copy"`` (default) — the artifact's bytes are written to the destination.
  Robust and portable: the view survives the build dir being moved or deleted.
* ``"symlink"`` — a symlink is written at the destination pointing at the
  artifact's absolute source path. The ``.witness/`` dir is then a *view* of the
  build rather than a duplicate (matters for the ~12 MB M2 SBOM).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .discovery import DiscoveryResult

# The canonical witness directory, relative to the build dir.
WITNESS_DIRNAME = ".witness"


@dataclass(frozen=True)
class BundledArtifact:
    """The bundling outcome for a single artifact kind.

    Attributes:
        kind:        Logical artifact kind, e.g. ``sbom``.
        canonical:   Conventional name under ``<build>/.witness/``.
        source:      Build-relative POSIX path of the discovered artifact.
        destination: Build-relative POSIX path written
                     (``<witness>/<canonical>``).
        method:      ``"copy"`` or ``"symlink"`` — how the destination was made.
    """

    kind: str
    canonical: str
    source: str
    destination: str
    method: str


@dataclass
class BundleResult:
    """The outcome of materialising the witness layout.

    Attributes:
        witness_dir: The ``<build>/.witness`` directory that was materialised
                     (POSIX string).
        bundled:     One :class:`BundledArtifact` per artifact that was written,
                     in canonical order.
        skipped:     The kinds that were not present in the build (nothing to
                     bundle), in canonical order.
    """

    witness_dir: str
    bundled: tuple[BundledArtifact, ...] = field(default=tuple())
    skipped: tuple[str, ...] = field(default=tuple())

    @property
    def count(self) -> int:
        """How many artifacts were materialised."""
        return len(self.bundled)


def _witness_dir(build_dir: Path, witness_dir: str | Path | None) -> Path:
    """Return the ``.witness`` directory: the explicit path if given, else
    ``<build>/.witness``."""
    if witness_dir is not None:
        return Path(witness_dir)
    return build_dir / WITNESS_DIRNAME


def bundle(
    build_dir: str | Path,
    discovery: DiscoveryResult,
    *,
    mode: str = "copy",
    witness_dir: str | Path | None = None,
) -> BundleResult:
    """Materialise the canonical witness layout under ``<build>/.witness/``.

    For every **present** artifact kind in *discovery*, the discovered file is
    copied (``mode="copy"``) or symlinked (``mode="symlink"``) to
    ``<witness_dir>/<canonical>``. The ``.witness`` directory is created if it
    does not exist. Kinds that were not found are reported in ``skipped`` and are
    left alone.

    Args:
        build_dir:     The build directory that was discovered.
        discovery:     The :class:`DiscoveryResult` for that build dir.
        mode:          ``"copy"`` (default) or ``"symlink"``.
        witness_dir:   Optional explicit destination directory; defaults to
                       ``<build>/.witness``.

    Returns:
        A :class:`BundleResult` describing what was written and what was skipped.

    Raises:
        ValueError: If *mode* is not ``"copy"`` or ``"symlink"``.
        OSError:    If the ``.witness`` directory cannot be created, or a
                    destination already exists as a non-empty directory, or a
                    copy fails.
    """
    if mode not in ("copy", "symlink"):
        raise ValueError(f"unknown bundle mode {mode!r}; use 'copy' or 'symlink'")

    build = Path(build_dir)
    dest_root = _witness_dir(build, witness_dir)
    dest_root.mkdir(parents=True, exist_ok=True)

    bundled: list[BundledArtifact] = []
    skipped: list[str] = []

    for art in discovery.artifacts:
        if not art.found or art.path is None:
            skipped.append(art.kind)
            continue

        src = build / art.path
        dest = dest_root / art.canonical
        try:
            _materialize(src, dest, mode)
        except OSError as exc:
            raise OSError(
                f"cannot bundle {art.kind} ({src} -> {dest}): {exc}"
            ) from exc
        try:
            dest_rel = dest.relative_to(build).as_posix()
        except ValueError:
            # A custom witness_dir outside the build dir has no build-relative
            # path; fall back to the absolute POSIX path.
            dest_rel = dest.as_posix()
        bundled.append(
            BundledArtifact(
                kind=art.kind,
                canonical=art.canonical,
                source=art.path,
                destination=dest_rel.replace("\\", "/"),
                method=mode,
            )
        )

    return BundleResult(
        witness_dir=str(dest_root),
        bundled=tuple(bundled),
        skipped=tuple(skipped),
    )


def _materialize(src: Path, dest: Path, mode: str) -> None:
    """Create *dest* as a copy or symlink of *src*.

    *dest* must not already exist as a non-empty directory. A pre-existing empty
    directory is removed first (a leftover ``.witness`` subdir); a pre-existing
    regular file / symlink is overwritten.
    """
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        if any(dest.iterdir()):
            raise OSError(
                f"destination exists and is a non-empty directory: {dest}"
            )
        dest.rmdir()

    if mode == "symlink":
        os.symlink(src.resolve(), dest)
    else:  # copy
        shutil.copy2(src, dest)
