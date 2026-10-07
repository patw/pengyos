"""Permission-policy enforcement for Secure Boot key material.

This module is the *enforcement* side of the permission policy defined in
:mod:`pengyos_sbkeys.keys`. Where ``keys.py`` only describes the policy (private
keys ``0600``, certificates ``0644``), this module inspects the filesystem and
refuses to operate when a private key has been loosened.

The rule is simple and fail-closed: before the CLI enrolls PK/KEK/db or signs a
UKI it verifies that every private key it is about to touch is protected with no
group or world bits. A private key found with more permissive bits is treated as
compromised -- it can never be safely enrolled into firmware or used to sign a
kernel image -- so the command refuses to proceed (exit 1) instead of handling
an insecure key.

Public certificates are always considered secure: they are, by definition,
world-readable. Only private keys are subject to the refusal.

The policy bits live in :mod:`pengyos_sbkeys.keys`; this module only performs
the (testable) filesystem checks and error mapping on top of them.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import List, Optional

from .keys import KEY_FILES, KeyFile, key_file_for_name, is_private

__all__ = [
    "PermCheckError",
    "PERMISSIONS_MASK",
    "perms_are_secure",
    "check_dir",
    "enforce_dir",
    "check_key",
    "enforce_key",
]


class PermCheckError(RuntimeError):
    """Raised when key material is found with insecure permissions.

    Raised by the ``enforce_*`` helpers when a private key is more permissive
    than the policy allows. The CLI maps this to exit 1 with a stderr message.
    """


#: Group + world permission bits. A private key must have none of these set
#: (``0600`` or stricter, e.g. ``0400``) to be considered secure.
PERMISSIONS_MASK = 0o077


def perms_are_secure(key_file: KeyFile, path: Path) -> bool:
    """Return ``True`` when ``path`` does not expose ``key_file`` to group/world.

    Public certificates are always considered secure (they are, by definition,
    world-readable). A private key is secure only when none of the group or
    world bits are set, i.e. its mode is ``0600`` or stricter. A ``None`` key
    (an unmanaged key the guard cannot classify) is treated as secure so it
    never blocks legitimate use.
    """
    if key_file is None or not is_private(key_file):
        return True
    mode = os.stat(path).st_mode
    return (stat.S_IMODE(mode) & PERMISSIONS_MASK) == 0


def check_dir(keys_dir: Path) -> List[str]:
    """Return the private keys under ``keys_dir`` with insecure permissions.

    Iterates the managed key set and reports every *present* private key whose
    mode exposes it to group or world. Missing files are not reported here (they
    are reported as missing elsewhere). The list is empty when every present
    private key is protected.
    """
    keys_dir = Path(keys_dir)
    violations: List[str] = []
    for key_file in KEY_FILES:
        if not is_private(key_file):
            continue
        path = keys_dir / key_file.name
        if path.is_file() and not perms_are_secure(key_file, path):
            violations.append(str(path))
    return violations


def enforce_dir(keys_dir: Path) -> None:
    """Fail closed if any private key under ``keys_dir`` is insecure.

    Raises :class:`PermCheckError` listing every offending file when the key
    directory contains a private key with too-permissive permissions.
    """
    violations = check_dir(keys_dir)
    if violations:
        raise PermCheckError(
            "insecure key permissions: "
            + "; ".join(violations)
            + " (expected 0600 or stricter on private keys)"
        )


def resolve_key_file(path: Path) -> Optional[KeyFile]:
    """Return the managed :class:`KeyFile` for ``path`` if it is one of ours.

    Matches on the filename (``keys/DB.key`` -> the managed ``DB.key`` entry).
    Returns ``None`` for an unmanaged key, which the single-key guard treats as
    "cannot tell, so do not block" -- only the managed set is enforced.
    """
    return key_file_for_name(path.name)


def check_key(path: Path, key_file: Optional[KeyFile] = None) -> bool:
    """Return ``True`` when ``path`` is protected per policy.

    For the single-key ``sign`` path: resolve ``key_file`` from the filename
    (when not supplied) and confirm a private key has no group/world bits. An
    unmanaged key (``key_file is None`` and no managed match) cannot be
    classified, so it is treated as secure rather than blocking legitimate use.
    """
    if key_file is None:
        key_file = resolve_key_file(path)
    return perms_are_secure(key_file, path)


def enforce_key(path: Path, key_file: Optional[KeyFile] = None) -> None:
    """Fail closed if ``path`` (a private key) is not protected per policy.

    Raises :class:`PermCheckError` when a managed private key is more permissive
    than the policy allows. Public certificates and unmanaged keys always pass.
    """
    if not check_key(path, key_file):
        raise PermCheckError(
            f"insecure key permissions: {path} "
            "(expected 0600 or stricter on private keys)"
        )
