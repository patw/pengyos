"""Feed directory layout, discovery, and atomic persistence.

Feed layout::

    <feed>/index.json                       signed table of contents + channels
    <feed>/channels/<name>                  plain-text channel pointer (version)
    <feed>/pengyos/<edition>/<version>/
        manifest.json                       signed release manifest

``index.json`` is the single signed artifact both install and update consume;
the ``channels/<name>`` files are a human/greppable mirror of the pointers it
carries.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from dataclasses import dataclass, field

from .errors import ChannelError, FeedError, ManifestError
from .manifest import FeedIndex, ReleaseManifest, parse_version, sha256_file
from .signing import Signer, StubSigner

# The default signer for feed-level operations that must sign/verify without the
# caller supplying one. It matches publish's default (a deterministic stub) so a
# feed published with the default stub round-trips through ``index``/``verify``.
DEFAULT_SIGNER = StubSigner()

PENGYOS_DIRNAME = "pengyos"
CHANNELS_DIRNAME = "channels"
INDEX_FILENAME = "index.json"
MANIFEST_FILENAME = "manifest.json"

DEFAULT_CHANNELS = ("stable", "testing")


def index_path(feed: Path) -> Path:
    return Path(feed) / INDEX_FILENAME


def channels_dir(feed: Path) -> Path:
    return Path(feed) / CHANNELS_DIRNAME


def channel_pointer_path(feed: Path, name: str) -> Path:
    return channels_dir(feed) / name


def edition_dir(feed: Path, edition: str) -> Path:
    return Path(feed) / PENGYOS_DIRNAME / edition


def version_dir(feed: Path, edition: str, version: str) -> Path:
    return edition_dir(feed, edition) / version


def manifest_path(feed: Path, edition: str, version: str) -> Path:
    return version_dir(feed, edition, version) / MANIFEST_FILENAME


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + ``os.replace``).

    The temp file is created in the destination directory so the replace is on
    the same filesystem. Used for the index so a crash can never leave a torn
    ``index.json`` behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FeedError(f"expected file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeedError(f"invalid JSON in {path}: {exc}") from exc


def list_editions(feed: Path) -> list[str]:
    base = Path(feed) / PENGYOS_DIRNAME
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def list_versions(feed: Path, edition: str) -> list[str]:
    base = edition_dir(feed, edition)
    if not base.is_dir():
        return []
    versions = [p.name for p in base.iterdir() if p.is_dir() and (p / MANIFEST_FILENAME).is_file()]
    return sorted(versions, key=parse_version)


def list_all(feed: Path) -> list[tuple[str, str]]:
    """All ``(edition, version)`` pairs, ordered by edition then version."""
    out: list[tuple[str, str]] = []
    for edition in list_editions(feed):
        out.extend((edition, v) for v in list_versions(feed, edition))
    return out


def read_manifest(feed: Path, edition: str, version: str) -> ReleaseManifest:
    return ReleaseManifest.from_dict(read_json(manifest_path(feed, edition, version)))


def write_manifest(feed: Path, manifest: ReleaseManifest) -> Path:
    path = manifest_path(feed, manifest.edition, manifest.version)
    write_atomic(path, json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8"))
    return path


def read_index(feed: Path) -> FeedIndex:
    return FeedIndex.from_dict(read_json(index_path(feed)))


def write_index(feed: Path, index: FeedIndex) -> Path:
    payload = json.dumps(index.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    write_atomic(index_path(feed), payload)
    return index_path(feed)


def write_channel_pointer(feed: Path, name: str, version: str) -> None:
    path = channel_pointer_path(feed, name)
    write_atomic(path, f"{version}\n".encode("utf-8"))


def read_channel_pointer(feed: Path, name: str) -> str | None:
    path = channel_pointer_path(feed, name)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def existing_channel_names(feed: Path) -> list[str]:
    """Every channel pointer file present under ``<feed>/channels/`` (sorted)."""
    base = channels_dir(feed)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_file())


def regenerate_index(
    feed: Path,
    *,
    signer: Signer | None = None,
    channels: list[str] | tuple[str, ...] | None = None,
) -> FeedIndex:
    """Regenerate the signed index + channel pointers from the on-disk feed.

    Discovers every release manifest under ``<feed>/pengyos/<edition>/<version>/``
    (via :func:`list_all`) and rebuilds a fresh signed :class:`FeedIndex` over
    them. The channel pointers are read from the durable plain-text mirror under
    ``<feed>/channels/<name>`` (written by :func:`publish`/:func:`regenerate_index`)
    and mirrored back into the rebuilt index, which is then written atomically.

    This is the ``index`` command's job: it recovers a missing/corrupt
    ``index.json`` as long as the channel pointer files survive, and it keeps the
    signed index in lock-step with the manifests on disk.

    ``channels`` optionally filters which channels are (re)written; ``None``
    writes every channel that has a pointer file. Returns the rebuilt index.

    Raises :class:`FeedError` if a discovered manifest is missing/corrupt or the
    signer is not a :class:`Signer`.
    """
    signer = signer or DEFAULT_SIGNER
    if not isinstance(signer, Signer):
        raise FeedError("regenerate_index requires a signer")

    feed = Path(feed)
    if not feed.is_dir():
        raise FeedError(f"feed directory not found: {feed}")
    releases = [read_manifest(feed, edition, version) for edition, version in list_all(feed)]
    index = FeedIndex(releases=releases, channels={})

    wanted = set(channels) if channels is not None else None
    for name in existing_channel_names(feed):
        if wanted is not None and name not in wanted:
            continue
        version = read_channel_pointer(feed, name)
        if version is None:
            continue
        index.set_channel(name, version)

    index.sign(signer)
    write_index(feed, index)
    for name, version in index.channels.items():
        write_channel_pointer(feed, name, version)
    return index


# --- feed validation ------------------------------------------------------

@dataclass
class FeedValidation:
    """Result of validating a feed tree's committed, validated data.

    ``ok`` is True iff there are no structural errors. ``errors`` collects every
    problem found so a caller can report all of them at once.
    """

    feed: Path
    manifests_validated: int = 0
    channels_validated: int = 0
    index_valid: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_feed(
    feed: Path,
    *,
    check_signatures: bool = False,
    signer=None,
) -> FeedValidation:
    """Validate every committed data document in a feed tree.

    Walks ``<feed>/pengyos/<edition>/<version>/manifest.json`` for every release
    and validates each manifest's structure (schema, edition/version, sha256
    digests, signatures), then validates the signed index and its channel
    pointers. Structural validation is always performed; when ``check_signatures``
    is True the index and every manifest are additionally checked against
    ``signer`` (required in that case).

    Returns a :class:`FeedValidation`; inspect ``.ok`` / ``.errors``. Raises
    :class:`FeedError` if ``feed`` is not a directory, or if ``check_signatures``
    is requested without a signer.
    """
    feed = Path(feed)
    if not feed.is_dir():
        raise FeedError(f"feed directory not found: {feed}")
    if check_signatures and not isinstance(signer, Signer):
        raise FeedError("check_signatures=True requires a signer")

    result = FeedValidation(feed=feed)

    # 1. The signed index is the single artifact both install and update consume.
    if not index_path(feed).is_file():
        result.errors.append(f"missing signed index: {index_path(feed)}")
    else:
        try:
            index = read_index(feed)
            index.validate()
            result.channels_validated = len(index.channels)
            if check_signatures and not index.verify(signer):  # type: ignore[arg-type]
                result.errors.append("index signature did not verify")
            else:
                result.index_valid = True
        except (ManifestError, ChannelError) as exc:
            result.errors.append(f"index: {exc}")

    # 2. Every release manifest in the feed.
    for edition, version in list_all(feed):
        try:
            manifest = read_manifest(feed, edition, version)
            manifest.validate()
            result.manifests_validated += 1
            if check_signatures and not manifest.verify(signer):  # type: ignore[arg-type]
                result.errors.append(f"{edition}/{version}: manifest signature did not verify")
        except (ManifestError, ChannelError) as exc:
            result.errors.append(f"{edition}/{version}: {exc}")

    return result


@dataclass
class FeedVerification:
    """Result of :func:`verify_feed`: hashes + signatures re-checked on disk.

    ``ok`` is True iff every artifact's stored digest matches its recorded sha256,
    every signature verifies, the index is structurally valid, and no channel
    pointer dangles (points at a version with no manifest). ``problems`` lists
    every issue found so a caller can report them all at once.
    """

    feed: Path
    artifacts_verified: int = 0
    manifests_verified: int = 0
    index_valid: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_feed(
    feed: Path,
    *,
    check_signatures: bool = True,
    signer: Signer | None = None,
) -> FeedVerification:
    """Verify a feed: every artifact's on-disk hash + every signature re-checked.

    The ``verify`` command's job. Beyond the structural checks in
    :func:`validate_feed`, this re-reads every stored artifact file and confirms
    its sha256 matches the digest recorded in the manifest (catches a tampered or
    partially-written rootfs/UKI/SBOM), then verifies every manifest + the index
    signature against ``signer``. When ``check_signatures`` is True (the default)
    a signer is required; otherwise only the hashes are re-checked.

    Also flags a dangling channel pointer (a pointer whose version has no manifest
    on disk) so a feed that lost a version does not silently keep pointing at it.

    The three mutations the release feed must catch are reported distinctly: an
    **unsigned** artifact/manifest/index (no signature present) reads "has no
    signature", a **tampered** stored file reads "digest mismatch", and a
    **dangling** channel pointer reads "dangles". A *wrong* (but present)
    signature still reads "did not verify".

    Returns a :class:`FeedVerification`; inspect ``.ok`` / ``.problems``. Raises
    :class:`FeedError` if ``feed`` is not a directory or if
    ``check_signatures`` is requested without a signer.
    """
    feed = Path(feed)
    if not feed.is_dir():
        raise FeedError(f"feed directory not found: {feed}")
    signer = signer or DEFAULT_SIGNER
    if check_signatures and not isinstance(signer, Signer):
        raise FeedError("verify_feed requires a signer")

    result = FeedVerification(feed=feed)

    # 1. Every release: re-hash each stored artifact against its recorded digest.
    for edition, version in list_all(feed):
        try:
            manifest = read_manifest(feed, edition, version)
        except (ManifestError, FeedError) as exc:
            result.problems.append(f"{edition}/{version}: {exc}")
            continue

        vdir = version_dir(feed, edition, version)
        for artifact in manifest.artifacts:
            stored = vdir / artifact.name
            try:
                actual = sha256_file(stored)
            except ManifestError as exc:
                result.problems.append(f"{edition}/{version}: {artifact.name}: {exc}")
                continue
            if actual != artifact.digest:
                result.problems.append(
                    f"{edition}/{version}: {artifact.name}: digest mismatch "
                    f"(recorded {artifact.digest}, stored {actual})"
                )
            else:
                result.artifacts_verified += 1

        if check_signatures:
            # An *unsigned* manifest (no signature present) is a distinct mutation
            # from a *wrong* signature: report it as such so `verify` can tell
            # "never signed" apart from "signed by the wrong key".
            if not manifest.signature:
                result.problems.append(f"{edition}/{version}: manifest has no signature")
            elif not manifest.verify(signer):  # type: ignore[arg-type]
                result.problems.append(f"{edition}/{version}: manifest signature did not verify")
            else:
                result.manifests_verified += 1

    # 2. The signed index is structurally valid and (when asked) verifies.
    if index_path(feed).is_file():
        try:
            index = read_index(feed)
            index.validate()
            if check_signatures:
                # Distinguish an *unsigned* index (never signed) from a *wrong*
                # signature, exactly as the manifest check does above.
                if not index.signature:
                    result.problems.append("index has no signature")
                elif not index.verify(signer):  # type: ignore[arg-type]
                    result.problems.append("index signature did not verify")
                else:
                    result.index_valid = True
        except (ManifestError, ChannelError) as exc:
            result.problems.append(f"index: {exc}")
    else:
        result.problems.append(f"missing signed index: {index_path(feed)}")

    # 3. No channel pointer dangles: every pointed-at version must have a manifest
    #    on disk, so a feed that lost a version does not keep pointing at it.
    try:
        index = read_index(feed)
        present = {v for _, v in list_all(feed)}
        for name, version in index.channels.items():
            if version not in present:
                result.problems.append(f"channel {name!r} dangles: no manifest for {version}")
    except (ManifestError, ChannelError, FeedError):
        pass  # index problems are already reported above; a broken index does not
              # add dangling-pointer noise.

    return result


# --- retention ------------------------------------------------------------

@dataclass
class RetainResult:
    """The outcome of :func:`retain`: which versions were kept and pruned.

    ``pruned`` lists the ``(edition, version)`` pairs that were (or, in a
    ``dry_run``, would be) removed. ``kept`` is the complement — the releases
    retained. ``removed`` names the version dirs actually deleted on disk (empty
    for a dry-run). ``dry_run`` echoes the request.
    """

    feed: Path
    kept: list[tuple[str, str]] = field(default_factory=list)
    pruned: list[tuple[str, str]] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return True


def retain(
    feed: Path,
    *,
    keep: int,
    dry_run: bool = True,
    signer: Signer | None = None,
) -> RetainResult:
    """Prune old versions from a feed, keeping the newest ``keep`` per edition.

    The ``retain`` command's job. For every edition in the feed, keep the
    newest ``keep`` versions (by :func:`parse_version` order) and prune the
    rest. A version that a channel points at is **never pruned** — the updater
    fetches the pointed-at version, so pruning it would dangle the pointer.

    ``keep`` must be >= 1 (there is nothing useful to keep otherwise).

    This is a **dry run by default**: it returns :class:`RetainResult` describing
    what *would* be pruned without touching the feed. Pass ``dry_run=False`` to
    actually delete the pruned version dirs. When applied, the signed index is
    regenerated (re-signed with ``signer``, default: the deterministic stub) so
    the feed is left consistent — the pruned releases are dropped from the index
    and the surviving channel pointers still resolve.

    Returns a :class:`RetainResult`. Raises :class:`FeedError` if ``feed`` is not
    a directory, ``keep`` < 1, or the signer is not a :class:`Signer`.
    """
    feed = Path(feed)
    if not feed.is_dir():
        raise FeedError(f"feed directory not found: {feed}")
    if keep < 1:
        raise FeedError(f"--keep must be >= 1 (got {keep})")
    signer = signer or DEFAULT_SIGNER
    if not isinstance(signer, Signer):
        raise FeedError("retain requires a signer")

    # The versions a channel points at must survive retention: the updater fetches
    # the pointed-at version, so pruning it would dangle the channel pointer.
    protected: set[str] = set()
    try:
        index = read_index(feed)
        protected.update(index.channels.values())
    except (ManifestError, ChannelError, FeedError):
        pass  # a broken/missing index protects nothing; retention still runs.

    kept: list[tuple[str, str]] = []
    pruned: list[tuple[str, str]] = []

    # Group versions by edition (sorted), keep the newest ``keep`` plus any
    # protected version. list_versions orders oldest-first, so the newest ``keep``
    # are the last ``keep`` entries of that list; everything before it is pruned
    # (unless protected). kept/pruned come back oldest-first, edition-grouped, in
    # lock-step with list_all.
    for edition in list_editions(feed):
        versions = list_versions(feed, edition)  # oldest-first
        newest_keep = set(versions[-keep:])  # the newest ``keep`` (all if keep >= n)
        for version in versions:
            if version in newest_keep or version in protected:
                kept.append((edition, version))
            else:
                pruned.append((edition, version))

    removed: list[Path] = []
    if not dry_run:
        for edition, version in pruned:
            # The version dir holds only the manifest + its artifacts, so the
            # whole dir is the unit of deletion.
            target = version_dir(feed, edition, version)
            shutil.rmtree(target)
            removed.append(target)
        # Re-sign the index so the pruned releases drop out and surviving pointers
        # still resolve. Regenerating from the on-disk manifests is the same
        # recovery path `index` uses.
        regenerate_index(feed, signer=signer)

    return RetainResult(
        feed=feed,
        kept=kept,
        pruned=pruned,
        removed=removed,
        dry_run=dry_run,
    )
