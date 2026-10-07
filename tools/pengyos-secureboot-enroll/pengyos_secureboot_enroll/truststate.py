"""Model the Secure Boot trust state as data.

This module is the *data layer* of ``pengyos_secureboot_enroll``. It defines,
purely as data (no I/O, no crypto), the two things the rest of the tool needs to
reason about:

1. **The key set** -- the PK/KEK/db trust root and how each key is *identified*.
   A key is identified by the SHA-256 fingerprint of its DER certificate (the
   same identifier MOK/mokutil uses to name an enrolled key). The fingerprint is
   derived from the key *material*, so rotating a key changes its id -- which is
   exactly what ``keys --rotate`` must do.

2. **The expected binaries** -- the set of EFI binaries the ESP is expected to
   ship, each signed by a key. An :class:`EspBinary` records its path (relative
   to the ESP root) and, optionally, the specific key id it must be signed with.

The key-set definition mirrors the UEFI Secure Boot model and the sibling
``pengyos-sbkeys`` package:

==================  ==========  ==========================================
Key               Role        Responsibility
==================  ==========  ==========================================
``PK``              Platform    Master key. Owns the KEK. One per platform.
``KEK``             Key Exchange Owns ``db``/``dbx``. Used by the firmware.
``db``              Database    Signs bootloaders / kernel images (UKIs).
==================  ==========  ==========================================

This module is deliberately independent of any I/O so that both the CLI and the
test-suite can import and assert on the model without touching the filesystem.
The fingerprint derivation lives in :func:`key_fingerprint`; :class:`KeyEntry`
and :class:`EspManifest` are the assembled views the rest of the tool consumes
.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Iterator, List, Optional  # noqa: F401 (Iterator used below)

__all__ = [
    "KeyKind",
    "KeyFile",
    "KeyPerms",
    "KeyEntry",
    "EspBinary",
    "EspManifest",
    "KEY_FILES",
    "KEY_ROLES",
    "key_fingerprint",
    "role_of",
    "is_private",
    "perms_for",
    "key_file_for_name",
    "esp_relative_path",
    "esp_key_id",
    "load_expected_binaries",
]


class KeyKind(Enum):
    """Whether a key file holds private material or the public certificate."""

    PRIVATE = "private"
    PUBLIC = "public"


@dataclass(frozen=True)
class KeyFile:
    """One generated file: a name, whether it is private or a certificate, and
    the key role it belongs to."""

    name: str
    kind: KeyKind
    role: str
    description: str

    @property
    def path(self) -> str:
        """The filename as used on disk (no directory component)."""
        return self.name


class KeyPerms:
    """Octal permission bits applied to generated key material.

    Private key material is never group/world readable; certificates are world
    readable because they are, by definition, public.
    """

    PRIVATE = 0o600
    PUBLIC = 0o644
    DIR = 0o700


def is_private(key_file: KeyFile) -> bool:
    """Return ``True`` when ``key_file`` holds private material."""
    return key_file.kind is KeyKind.PRIVATE


def perms_for(key_file: KeyFile) -> int:
    """Return the octal permission bits that must protect ``key_file``."""
    return KeyPerms.PRIVATE if is_private(key_file) else KeyPerms.PUBLIC


#: The managed key set in Secure Boot role order (PK, KEK, db), each with its
#: private key and self-signed certificate.
KEY_FILES: tuple[KeyFile, ...] = (
    KeyFile(
        "PK.key",
        KeyKind.PRIVATE,
        "PK",
        "Platform Key (master) — private signing key",
    ),
    KeyFile(
        "PK.cert",
        KeyKind.PUBLIC,
        "PK",
        "Platform Key certificate",
    ),
    KeyFile(
        "KEK.key",
        KeyKind.PRIVATE,
        "KEK",
        "Key Exchange Key — private signing key",
    ),
    KeyFile(
        "KEK.cert",
        KeyKind.PUBLIC,
        "KEK",
        "Key Exchange Key certificate",
    ),
    KeyFile(
        "DB.key",
        KeyKind.PRIVATE,
        "DB",
        "Secure Boot database signing key — signs UKIs",
    ),
    KeyFile(
        "DB.cert",
        KeyKind.PUBLIC,
        "DB",
        "Secure Boot database signing certificate",
    ),
)


def key_file_for_name(name: str) -> Optional[KeyFile]:
    """Return the managed :class:`KeyFile` whose filename matches ``name``.

    ``None`` when no managed file has that name.
    """
    for key_file in KEY_FILES:
        if key_file.name == name:
            return key_file
    return None


def role_of(key_file: KeyFile) -> str:
    """Return the Secure Boot role (``PK``/``KEK``/``DB``) of ``key_file``."""
    return key_file.role


#: The roles in enrollment order: PK first, then KEK, then db.
KEY_ROLES: tuple[str, ...] = tuple(key.role for key in KEY_FILES if is_private(key))


def _normalize_path(absolute: str) -> str:
    """Return ``absolute`` in canonical POSIX form, resolving ``.``/``..``.

    Repeated separators are dropped, ``.`` is ignored and ``..`` pops the last
    path component (never below the root). This makes paths that differ only in
    form compare equal and is a small path-traversal guard: a binary whose
    normalised path escapes the ESP root cannot be a real boot entry.
    """
    stack: List[str] = []
    for part in absolute.split("/"):
        if part == "" or part == ".":
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    return "/" + "/".join(stack)


def key_fingerprint(der: bytes) -> str:
    """Return the SHA-256 fingerprint (lowercase hex) of a DER certificate.

    This is the identifier used for a key in ``db`` and by MOK/mokutil: the
    fingerprint of the certificate's DER encoding. It is derived purely from the
    key material, so rotating the key changes the fingerprint -- and therefore
    the key id.
    """
    return hashlib.sha256(der).hexdigest()


@dataclass(frozen=True)
class KeyEntry:
    """A key in the trust root, identified by its certificate fingerprint.

    ``key_id`` is the SHA-256 fingerprint of the DER certificate. It is the
    value recorded in ``db`` and the value ``lint`` compares a binary's signer
    against. ``role`` is the Secure Boot role (``PK``/``KEK``/``DB``); only the
    ``DB`` key is allowed to sign bootloaders/kernel images.
    """

    role: str
    key_id: str
    cert_path: str
    key_path: Optional[str] = None
    perms: int = KeyPerms.PUBLIC

    @property
    def is_signing_key(self) -> bool:
        """``True`` when this key is allowed to sign binaries (the ``db`` key)."""
        return self.role == "DB"


@dataclass(frozen=True)
class EspBinary:
    """One EFI binary on the ESP that is expected to be signed.

    ``path`` is relative to the ESP root and stored in POSIX form (``/``
    separators), e.g. ``/EFI/PengyOS/linux.efi``. ``key_id`` is the specific
    signing-key fingerprint this binary must be signed with; ``None`` means "any
    key present in ``db`` is acceptable" (used for the generic bootloader entry
    whose signer is not pinned to a particular PengyOS key).
    """

    path: str
    key_id: Optional[str] = None
    description: str = ""

    def normalized_path(self) -> str:
        """Return ``path`` in canonical POSIX form.

        Collapses repeated separators, resolves ``.``/``..`` lexically and drops
        a trailing slash, so paths that differ only in form compare equal.
        Resolving ``..`` is also a small path-traversal guard: a binary whose
        normalised path escapes the ESP root cannot be a real boot entry.
        """
        return _normalize_path(self.path)

    def normalized_entry(self) -> "EspBinary":
        """Return a copy of this binary with a normalised path."""
        return replace(self, path=self.normalized_path())


@dataclass
class EspManifest:
    """The ordered set of EFI binaries the ESP is expected to ship, each signed.

    Backed by an insertion-ordered list so iteration order (and therefore the
    lint report) is stable. Use :meth:`key_ids` to get the distinct set of
    signing keys the manifest references, and :meth:`by_path` to look a binary
    up by its ESP-relative path.
    """

    binaries: List[EspBinary] = ()

    def __post_init__(self) -> None:
        # Normalise stored paths so lookups and reports are canonical.
        self.binaries = [
            EspBinary(
                path=b.normalized_path(),
                key_id=b.key_id,
                description=b.description,
            )
            for b in self.binaries
        ]

    def __iter__(self) -> Iterator[EspBinary]:
        return iter(self.binaries)

    def __len__(self) -> int:
        return len(self.binaries)

    def __contains__(self, path: object) -> bool:
        return self.get(path) is not None

    def add(self, binary: EspBinary) -> "EspManifest":
        """Return a new manifest with ``binary`` appended, deduped by path.

        If a binary with the same normalised path already exists the append is a
        no-op (the first occurrence wins): the manifest is therefore keyed by
        path, mirroring the fact that the ESP has exactly one file at each path.
        """
        target = binary.normalized_path()
        if any(b.normalized_path() == target for b in self.binaries):
            return self
        merged = list(self.binaries) + [binary.normalized_entry()]
        return EspManifest(binaries=merged)

    def get(self, path: str) -> Optional[EspBinary]:
        """Return the manifest entry whose normalised path equals ``path``."""
        target = EspBinary(path=path).normalized_path()
        for binary in self.binaries:
            if binary.normalized_path() == target:
                return binary
        return None

    def key_ids(self) -> List[str]:
        """Return the distinct signing-key ids referenced by the manifest."""
        seen: List[str] = []
        for binary in self.binaries:
            if binary.key_id is not None and binary.key_id not in seen:
                seen.append(binary.key_id)
        return seen

    def to_json(self) -> dict:
        """Return a JSON-serialisable representation of the manifest."""
        return {
            "binaries": [
                {
                    "path": b.path,
                    "key_id": b.key_id,
                    "description": b.description,
                }
                for b in self.binaries
            ]
        }


def esp_relative_path(absolute: str) -> str:
    """Return an absolute ESP path in canonical ESP-relative POSIX form.

    Strips a leading ``/`` and collapses redundant separators, so both
    ``/EFI/systemd/systemd-boot.efi`` and ``EFI//systemd/systemd-boot.efi`` map
    to ``/EFI/systemd/systemd-boot.efi``.
    """
    return EspBinary(path=absolute).normalized_path()


def esp_key_id(binary: EspBinary) -> Optional[str]:
    """Return the signing-key id ``binary`` must be signed with (or ``None``)."""
    return binary.key_id


def load_expected_binaries(
    binaries: Iterable[str],
    key_ids: Optional[Iterable[str]] = None,
    descriptions: Optional[Iterable[str]] = None,
) -> EspManifest:
    """Build an :class:`EspManifest` from ESP-relative paths.

    ``key_ids`` / ``descriptions`` are optional parallel lists aligned by index
    with ``binaries``; a missing value leaves that field at its default
    (``None`` / ``""``). Every path is normalised via :func:`esp_relative_path`.
    """
    key_id_list = list(key_ids) if key_ids is not None else []
    desc_list = list(descriptions) if descriptions is not None else []

    manifest: EspManifest = EspManifest()
    for index, path in enumerate(binaries):
        key_id = key_id_list[index] if index < len(key_id_list) else None
        description = desc_list[index] if index < len(desc_list) else ""
        manifest = manifest.add(EspBinary(path=path, key_id=key_id, description=description))
    return manifest
