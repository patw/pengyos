"""UEFI Secure Boot key-set definition and file-permission policy.

This module is the single source of truth for *which* key material PengyOS
manages and *how* the generated files must be protected. It is deliberately
independent of any I/O so that both the ``init`` command and the test-suite can
import and assert on the policy without touching the filesystem.

The key set mirrors the UEFI Secure Boot model:

==================  ==========  ==========================================
Key               Role        Responsibility
==================  ==========  ==========================================
``PK``              Platform    Master key. Owns the KEK. One per platform.
``KEK``             Key Exchange Owns ``db``/``dbx``. Used by the firmware.
``db``              Database    Signs bootloaders / kernel images (UKIs).
==================  ==========  ==========================================

The file-permission policy (see ``spec.md`` "Security posture") is:

* private keys -> ``0600`` (owner read/write only)
* certificates -> ``0644`` (world-readable, they are public anyway)

The CLI fails closed if a private key is found with more permissive bits, so
loosened keys can never be committed or shipped by accident.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional

__all__ = [
    "KeyKind",
    "KeyFile",
    "KeyPerms",
    "KEY_FILES",
    "KEY_ROLES",
    "KEY_NAMESPACE",
    "VARIABLE_FOR_ROLE",
    "guid_for",
    "role_of",
    "is_private",
    "perms_for",
    "variable_for_role",
    "key_file_for_name",
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
    readable because they are, by definition, public. The key directory itself
    is locked down to the owner so the private keys inside cannot be read by
    other users on a shared host.
    """

    PRIVATE = 0o600
    PUBLIC = 0o644
    DIR = 0o700


def perms_for(key_file: KeyFile) -> int:
    """Return the octal permission bits that must protect ``key_file``."""
    return KeyPerms.PRIVATE if is_private(key_file) else KeyPerms.PUBLIC


def is_private(key_file: KeyFile) -> bool:
    """Return ``True`` when ``key_file`` holds private material."""
    return key_file.kind is KeyKind.PRIVATE


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

# The roles in enrollment order: PK first, then KEK, then db.
KEY_ROLES: tuple[str, ...] = tuple(key.role for key in KEY_FILES if is_private(key))


def role_of(key_file: KeyFile) -> str:
    """Return the Secure Boot role (``PK``/``KEK``/``DB``) of ``key_file``."""
    return key_file.role


def key_file_for_name(name: str) -> Optional[KeyFile]:
    """Return the managed :class:`KeyFile` whose filename matches ``name``.

    ``None`` when no managed file has that name -- used by the permission guard
    to decide whether an arbitrary signing key is a private key that must be
    protected.
    """
    for key_file in KEY_FILES:
        if key_file.name == name:
            return key_file
    return None


#: Fixed RFC 4122 version-5 namespace for PengyOS Secure Boot GUIDs. Deriving
#: each role's GUID from this namespace makes the values reproducible across
#: machines -- a hard requirement for golden-testing the enrollment commands.
KEY_NAMESPACE = uuid.UUID("8f4d2b1c-9a6e-4c3b-8d7f-1e2a3b4c5d6e")

#: EFI variable each role is enrolled into. PK and KEK keep their role name;
#: the signing database is the ``db`` variable.
VARIABLE_FOR_ROLE: dict[str, str] = {
    "PK": "PK",
    "KEK": "KEK",
    "DB": "db",
}


def guid_for(role: str) -> str:
    """Return the deterministic EFI GUID for Secure Boot role ``role``.

    Uses an RFC 4122 version-5 (name-based, SHA-1) UUID derived from
    :data:`KEY_NAMESPACE` and the role name, so the same role always maps to
    the same GUID on every machine.
    """
    return str(uuid.uuid5(KEY_NAMESPACE, f"pengyos-secureboot-keys:{role}"))


def variable_for_role(role: str) -> str:
    """Return the EFI variable name a role is enrolled into."""
    try:
        return VARIABLE_FOR_ROLE[role]
    except KeyError:
        raise KeyError(f"unknown Secure Boot role: {role!r}") from None
