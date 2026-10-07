"""Release metadata and feed-index schema.

Two committed, validated data documents live in the feed:

* A **release manifest** at ``<feed>/pengyos/<edition>/<version>/manifest.json``.
  It lists every artifact in the release with its algorithm, sha256, and the
  signer's signature — the thing ``verify`` checks and ``index`` hashes.
* The **feed index** at ``<feed>/index.json``. It is the signed table of contents
  over every release manifest, plus the ``stable``/``testing`` channel pointers
  that tell install/update which version to fetch. ``index`` writes it and
  ``verify`` re-checks it.

Both are canonicalised (stable key order, UTF-8, no trailing whitespace) so the
byte output is reproducible across machines and runs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ChannelError, ManifestError
from .signing import Signer

# --- sha256 digest --------------------------------------------------------
#
# Artifacts in a release are identified by their sha256 digest. These helpers
# compute the real digest over bytes or a file (streamed in chunks so a large
# rootfs image never has to fit in memory). Digests are stored as lowercase
# hex, matching ``sha256sum`` output.

_CHUNK = 1024 * 1024
DIGEST_ALGORITHM = "sha256"
# A sha256 hex digest: 64 lowercase hex characters.
HEXDIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def is_sha256_hex(digest: str) -> bool:
    """True iff ``digest`` is a lowercase hex sha256 digest."""
    return isinstance(digest, str) and bool(HEXDIGEST_RE.match(digest))


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of the file at ``path`` (streamed)."""
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"cannot hash: not a file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


# --- canonicalisation -----------------------------------------------------

def canonicalise(obj: Any) -> Any:
    """Return ``obj`` with dict keys sorted recursively (deterministic JSON)."""
    if isinstance(obj, dict):
        return {k: canonicalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [canonicalise(v) for v in obj]
    return obj


def to_canonical_bytes(obj: Any) -> bytes:
    """Serialise ``obj`` to deterministic UTF-8 JSON bytes for hashing/signing."""
    return json.dumps(canonicalise(obj), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


# --- version --------------------------------------------------------------

def parse_version(version: str) -> tuple[int, ...]:
    """Parse a dotted numeric version into a tuple of ints for ordering.

    Accepts ``N.N.N`` (semantic-ish) and rejects anything non-numeric.
    """
    if not version or not isinstance(version, str):
        raise ManifestError(f"version must be a non-empty string, got {version!r}")
    parts = version.split(".")
    if len(parts) < 2:
        raise ManifestError(f"version must be dotted (e.g. 1.2.3), got {version!r}")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise ManifestError(f"version must contain only dotted integers, got {version!r}")


# --- channel --------------------------------------------------------------
#
# A *channel* is a named pointer from a promotion tier (``stable``, ``testing``,
# ...) to a single release version. The feed carries one pointer per channel in
# the signed index and as a plain-text mirror under ``channels/<name>``.

_CHANNEL_RE = re.compile(r"^[a-z][a-z0-9-]*$")
KNOWN_CHANNELS = ("stable", "testing")


def validate_channel_name(name: str) -> str:
    """Return ``name`` if it is a valid channel name, else raise ``ChannelError``.

    Valid names start with a lowercase letter and contain only lowercase
    letters, digits, and hyphens (e.g. ``stable``, ``testing``, ``nightly``).
    """
    if not isinstance(name, str) or not name:
        raise ChannelError(f"channel name must be a non-empty string, got {name!r}")
    if not _CHANNEL_RE.match(name):
        raise ChannelError(
            f"channel name must match ^[a-z][a-z0-9-]*$ (got {name!r})"
        )
    return name


@dataclass
class Channel:
    """A named pointer to a single release version."""

    name: str
    version: str

    def __post_init__(self) -> None:
        self.name = validate_channel_name(self.name)
        try:
            parse_version(self.version)  # validate the pointed-at version
        except ManifestError as exc:
            raise ChannelError(
                f"channel {self.name!r} points at an invalid version: {exc}"
            ) from exc

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Channel":
        if "name" not in d or "version" not in d:
            raise ChannelError(f"channel missing fields: {sorted(d)}")
        return cls(name=d["name"], version=d["version"])

    def verify_pointer(self, version: str) -> bool:
        """True iff this channel points at ``version``."""
        return self.version == version


# --- release manifest -----------------------------------------------------

@dataclass
class Artifact:
    """One file in a release, with integrity + provenance."""

    name: str
    algorithm: str = "sha256"
    digest: str = ""
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "algorithm": self.algorithm, "digest": self.digest, "signature": self.signature}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Artifact":
        missing = [k for k in ("name", "algorithm", "digest", "signature") if k not in d]
        if missing:
            raise ManifestError(f"artifact missing fields: {missing}")
        return cls(name=d["name"], algorithm=d["algorithm"], digest=d["digest"], signature=d.get("signature", ""))

    def validate(self) -> "Artifact":
        """Validate the artifact's structure; raise :class:`ManifestError` otherwise.

        Checks the name is present, the digest is a real sha256 hex digest, and
        a signature (provenance) is present. Does *not* check the signature is
        cryptographically valid — see :meth:`Signer.verify`.
        """
        if not self.name:
            raise ManifestError("artifact name must be non-empty")
        if self.algorithm != DIGEST_ALGORITHM:
            raise ManifestError(f"unsupported digest algorithm: {self.algorithm!r}")
        if not is_sha256_hex(self.digest):
            raise ManifestError(f"artifact {self.name!r} has a non-sha256 digest: {self.digest!r}")
        if not self.signature:
            raise ManifestError(f"artifact {self.name!r} has no signature")
        return self


@dataclass
class ReleaseManifest:
    """A single release: edition, version, artifacts, provenance, notes."""

    edition: str
    version: str
    artifacts: list[Artifact] = field(default_factory=list)
    notes: str = ""

    def _payload_dict(self) -> dict[str, Any]:
        # The signed payload deliberately excludes the signature field: a
        # signature cannot be part of what it signs.
        return {
            "schema": "pengyos-release/manifest",
            "manifest": "1",
            "edition": self.edition,
            "version": self.version,
            "notes": self.notes,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_dict()
        payload["signature"] = self.signature
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReleaseManifest":
        if d.get("schema") != "pengyos-release/manifest":
            raise ManifestError(f"unknown or missing manifest schema: {d.get('schema')!r}")
        missing = [k for k in ("edition", "version") if k not in d]
        if missing:
            raise ManifestError(f"manifest missing fields: {missing}")
        m = cls(
            edition=d["edition"],
            version=d["version"],
            artifacts=[Artifact.from_dict(a) for a in d.get("artifacts", [])],
            notes=d.get("notes", ""),
        )
        m._signature = d.get("signature", "")
        return m

    def signed_payload(self) -> bytes:
        """The canonical bytes signed for this manifest."""
        return to_canonical_bytes(self._payload_dict())

    def sign(self, signer: Signer) -> None:
        self._signature = signer.sign(self.signed_payload()).hex()

    @property
    def signature(self) -> str:
        return getattr(self, "_signature", "")

    def verify(self, signer: Signer) -> bool:
        return self.signature == signer.sign(self.signed_payload()).hex()

    def validate(self) -> "ReleaseManifest":
        """Validate the manifest's structure; raise :class:`ManifestError` otherwise.

        Edition and version are well-formed, at least one artifact is present
        (every release carries at least the rootfs), every artifact validates,
        and the manifest itself is signed.
        """
        if not self.edition:
            raise ManifestError("edition must be non-empty")
        parse_version(self.version)
        if not self.artifacts:
            raise ManifestError("manifest must list at least one artifact")
        for artifact in self.artifacts:
            artifact.validate()
        if not self.signature:
            raise ManifestError("manifest has no signature")
        return self


# --- feed index -----------------------------------------------------------

@dataclass
class FeedIndex:
    """The signed table of contents over all release manifests + channel pointers."""

    releases: list[ReleaseManifest] = field(default_factory=list)
    channels: dict[str, str] = field(default_factory=dict)  # name -> version
    notes: str = ""

    def _payload_dict(self) -> dict[str, Any]:
        # The signed payload deliberately excludes the signature field.
        return {
            "schema": "pengyos-release/index",
            "version": "1",
            "notes": self.notes,
            "channels": dict(sorted(self.channels.items())),
            "releases": [r.to_dict() for r in sorted(self.releases, key=lambda r: parse_version(r.version))],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_dict()
        payload["signature"] = self.signature
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FeedIndex":
        if d.get("schema") != "pengyos-release/index":
            raise ManifestError(f"unknown or missing index schema: {d.get('schema')!r}")
        releases = [ReleaseManifest.from_dict(r) for r in d.get("releases", [])]
        channels = dict(d.get("channels", {}))
        idx = cls(releases=releases, channels=channels, notes=d.get("notes", ""))
        idx._signature = d.get("signature", "")
        return idx

    def signed_payload(self) -> bytes:
        return to_canonical_bytes(self._payload_dict())

    def sign(self, signer: Signer) -> None:
        self._signature = signer.sign(self.signed_payload()).hex()

    @property
    def signature(self) -> str:
        return getattr(self, "_signature", "")

    def verify(self, signer: Signer) -> bool:
        return self.signature == signer.sign(self.signed_payload()).hex()

    def channel(self, name: str) -> Channel | None:
        """Return the :class:`Channel` pointer for ``name``, or ``None``."""
        validate_channel_name(name)
        version = self.channels.get(name)
        return Channel(name=name, version=version) if version is not None else None

    def set_channel(self, name: str, version: str) -> None:
        """Set (validating) a channel pointer; returns ``self`` for chaining."""
        Channel(name=name, version=version)  # validates name + version
        self.channels[name] = version

    def channels_as_list(self) -> list[Channel]:
        """Return every channel pointer as a :class:`Channel`, ordered by name."""
        return [Channel(name=name, version=version) for name, version in sorted(self.channels.items())]

    def validate(self) -> "FeedIndex":
        """Validate the index's structure; raise :class:`ChannelError` otherwise.

        Every channel name is valid and points at a parseable version, every
        release validates, and the index itself is signed.
        """
        for name, version in self.channels.items():
            validate_channel_name(name)
            parse_version(version)
        for release in self.releases:
            release.validate()
        if not self.signature:
            raise ManifestError("index has no signature")
        return self
