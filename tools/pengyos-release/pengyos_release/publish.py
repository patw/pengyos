"""Publish: turn a pengyos-build output into a signed, versioned release.

``publish`` collects the artifacts a build emits (rootfs image, UKI, SBOM,
attestation, reproducible-report), deterministically compresses the rootfs,
computes a real sha256 over every *stored* artifact, signs each one, and lays
the result out under ``<feed>/pengyos/<edition>/<version>/`` with a signed
``manifest.json``.

The published layout is::

    <feed>/pengyos/<edition>/<version>/
        manifest.json                 signed release manifest
        rootfs.img.gz                 deterministically gzip-compressed rootfs
        uki.efi                        uncompressed UKI
        sbom.json                      software bill of materials
        attestation.json               attestation document
        repro-report.txt               reproducible-build report

Every artifact is hashed (the digest is over the stored bytes) and signed; the
manifest itself is signed too. ``verify`` re-reads the stored files, recomputes
the digests, and re-checks every signature.

Determinism matters: the release *is* the unit of change for a reproducible
distro, so two identical builds must produce byte-identical archives. The gzip
stream is written with ``mtime=0`` and no embedded filename so the compressed
bytes — and therefore the digest — are reproducible across machines and runs.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .errors import BuildOutputError
from .feed import (
    index_path,
    version_dir,
    write_channel_pointer,
    write_index,
    write_manifest,
)
from .manifest import (
    Artifact,
    FeedIndex,
    ReleaseManifest,
    parse_version,
    to_canonical_bytes,
)
from .signing import Signer, StubSigner

# Compression level for the gzip stream (0-9). Kept explicit so the published
# bytes — and the digest over them — are reproducible.
GZIP_LEVEL = 6
# Fixed mtime so the gzip stream carries no wall-clock time (deterministic).
_GZIP_MTIME = 0
# Default channel a release is published under.
DEFAULT_CHANNEL = "stable"
# Default signer when the caller does not inject one: a deterministic stub so a
# local feed signs without needing a key on disk.
DEFAULT_SIGNER = StubSigner()

# The artifacts every release carries. Each entry names the artifact as it is
# *stored* in the published version dir (the digest is over that stored file).
# The rootfs is the only artifact compressed on publish; the rest are stored
# verbatim.
ARTIFACTS: tuple[str, ...] = (
    "rootfs.img.gz",
    "uki.efi",
    "sbom.json",
    "attestation.json",
    "repro-report.txt",
)


@dataclass(frozen=True)
class ArtifactSource:
    """Where one artifact lives in the build output and how it is published.

    ``source`` is the path relative to the build root (e.g. ``images/rootfs.img``);
    ``published`` is the name it is stored under in the version dir (e.g.
    ``rootfs.img.gz``). ``compress`` selects deterministic gzip compression.
    """

    published: str
    source: str
    compress: bool = False


# The canonical mapping from a build artifact to its published form. The rootfs
# is compressed (it is by far the largest artifact); everything else is stored
# verbatim.
_ARTIFACT_SOURCES: tuple[ArtifactSource, ...] = (
    ArtifactSource("rootfs.img.gz", "images/rootfs.img", compress=True),
    ArtifactSource("uki.efi", "images/uki.efi", compress=False),
    ArtifactSource("sbom.json", "sbom.json", compress=False),
    ArtifactSource("attestation.json", "attestation.json", compress=False),
    ArtifactSource("repro-report.txt", "repro-report.txt", compress=False),
)


def _find_source(build: Path, relative: str) -> Path:
    """Resolve a build-relative path, raising a build-scoped error if absent."""
    path = build / relative
    if not path.is_file():
        raise BuildOutputError(
            f"build output is missing {relative!r} (looked for {path})"
        )
    return path


def _hash_stream(src: Path, dst: Path) -> str:
    """Stream-copy ``src`` -> ``dst`` and return the sha256 hex of its contents.

    Streamed in 1 MiB blocks so a large rootfs never has to fit in memory.
    """
    digest = hashlib.sha256()
    with src.open("rb") as fin, dst.open("wb") as fout:
        chunk = bytearray(1024 * 1024)
        while True:
            n = fin.readinto(chunk)
            if n == 0:
                break
            digest.update(chunk[:n])
            fout.write(chunk[:n])
    return digest.hexdigest()


def _compress_stream(src: Path, dst: Path) -> str:
    """Deterministically gzip-compress ``src`` -> ``dst``; return sha256 hex.

    The digest is over the *stored* (compressed) bytes — what ``verify`` will
    re-read — so a manifest and the file on disk always agree. The gzip stream
    is written with ``mtime=0`` and an empty filename so the compressed bytes
    carry no wall-clock time or host name — the digest is reproducible.

    Streaming: the source is consumed in 1 MiB blocks; each compressed block is
    written to ``dst`` and hashed as it goes, so a large rootfs never has to
    fit in memory (neither the source nor the compressed output).
    """
    import io

    class _Tee(io.RawIOBase):
        # Writes through to ``out`` while feeding ``digest`` for hashing.

        def __init__(self, out) -> None:
            self._out = out
            self._digest = hashlib.sha256()

        def writable(self) -> bool:
            return True

        def write(self, data: bytes) -> int:  # pragma: no cover - exercised via gzip
            self._digest.update(data)
            return self._out.write(data)

        def flush(self) -> None:
            # The underlying file may already be closed during finalization;
            # ignore that — the compressed bytes were already written out.
            try:
                self._out.flush()
            except ValueError:
                pass

        def digest_hex(self) -> str:
            return self._digest.hexdigest()

    with src.open("rb") as fin, dst.open("wb") as fout:
        tee = _Tee(fout)
        # A single gzip member over the whole stream: deterministic (mtime=0)
        # and a well-formed .gz that any gzip reader decompresses.
        with gzip.GzipFile(fileobj=tee, mode="wb", mtime=_GZIP_MTIME, compresslevel=GZIP_LEVEL) as gz:
            chunk = bytearray(1024 * 1024)
            while True:
                n = fin.readinto(chunk)
                if n == 0:
                    break
                gz.write(chunk[:n])
    return tee.digest_hex()


def _collect_artifacts(build: Path, version_dir_path: Path, signer: Signer) -> list[Artifact]:
    """Collect, compress/hash, and sign every artifact into the version dir.

    Returns the list of :class:`Artifact` records (digest over the stored file)
    to embed in the manifest. Raises :class:`BuildOutputError` if any expected
    artifact is missing from the build output.
    """
    artifacts: list[Artifact] = []
    for source in _ARTIFACT_SOURCES:
        src = _find_source(build, source.source)
        dst = version_dir_path / source.published
        if source.compress:
            digest = _compress_stream(src, dst)
        else:
            digest = _hash_stream(src, dst)
        artifact = Artifact(name=source.published, digest=digest, signature="")
        artifact.signature = signer.sign(to_canonical_bytes(artifact.to_dict())).hex()
        artifacts.append(artifact)
    return artifacts


def _load_index(feed: Path) -> FeedIndex:
    if index_path(feed).is_file():
        return FeedIndex.from_dict(json.loads(index_path(feed).read_text(encoding="utf-8")))
    return FeedIndex(releases=[], channels={})


def _update_index(
    feed: Path,
    manifest: ReleaseManifest,
    signer: Signer,
    channel: str,
) -> None:
    """Register ``manifest`` in the signed index and point ``channel`` at it."""
    index = _load_index(feed)
    # Replace any prior release with the same edition+version (idempotent republish).
    index.releases = [
        r for r in index.releases if not (r.edition == manifest.edition and r.version == manifest.version)
    ]
    index.releases.append(manifest)
    index.set_channel(channel, manifest.version)
    index.sign(signer)
    write_index(feed, index)
    write_channel_pointer(feed, channel, manifest.version)


def publish(
    build: Path,
    edition: str,
    version: str,
    feed: Path,
    *,
    signer: Signer | None = None,
    channel: str | None = DEFAULT_CHANNEL,
) -> ReleaseManifest:
    """Publish ``build`` as edition/``version`` under ``<feed>/pengyos/<edition>/<version>/``.

    Collects the build artifacts, deterministically compresses the rootfs, signs
    each artifact, writes the version dir + signed ``manifest.json``, and (unless
    ``channel`` is ``None``) registers the release in the signed feed index and
    updates the channel pointer atomically.

    Returns the published :class:`ReleaseManifest`.
    """
    if not edition:
        raise BuildOutputError("edition must be non-empty")
    try:
        parse_version(version)
    except BuildOutputError:
        raise
    except Exception as exc:  # ManifestError from parse_version
        raise BuildOutputError(f"invalid version {version!r}: {exc}") from exc

    build = Path(build)
    if not build.is_dir():
        raise BuildOutputError(f"build output is not a directory: {build}")

    if signer is None:
        signer = DEFAULT_SIGNER

    version_dir_path = version_dir(feed, edition, version)
    version_dir_path.mkdir(parents=True, exist_ok=True)

    artifacts = _collect_artifacts(build, version_dir_path, signer)
    manifest = ReleaseManifest(edition=edition, version=version, artifacts=artifacts, notes="")
    manifest.sign(signer)
    write_manifest(feed, manifest)

    if channel is not None:
        _update_index(feed, manifest, signer, channel)

    return manifest
