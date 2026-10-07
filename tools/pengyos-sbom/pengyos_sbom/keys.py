"""Ed25519 keypair generation — the engine behind the ``keygen`` subcommand.

Users no longer need external tooling (``openssl rand 32`` or a Python
snippet) to create signing keys: ``pengyos-sbom keygen`` produces a fresh
keypair in the same file formats :mod:`pengyos_sbom.signing` already
accepts, so the output is immediately usable by ``sign`` and ``verify``.

Contract:

* the **seed** (32 bytes, the RFC 8032 secret key) comes from
  ``os.urandom`` — the platform's CSPRNG;
* the **public key** (32 bytes) is derived in-tree by
  :func:`pengyos_sbom.ed25519.publickey`;
* files are written **raw** (exactly 32 bytes each) — the format
  :func:`pengyos_sbom.signing.load_signing_key` and
  :func:`pengyos_sbom.signing.load_verification_key` both accept;
* the seed file is created with mode ``0o600`` (owner read/write only) —
  it is the only copy of the secret — and the public key file with
  ``0o644`` (it is public);
* :func:`generate_keypair` **never overwrites** an existing seed or
  public-key file (a :class:`KeygenError` names the first offender).
  Accidentally clobbering the only copy of a signing seed is the exact
  failure the subcommand must not cause;

Only the standard library is used.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple, Union

from . import ed25519
from .errors import SBOMError

__all__ = [
    "KeygenError",
    "default_pub_path",
    "generate_keypair",
    "write_keypair",
]

PathLike = Union[str, Path]

# Unix file modes for the two artifacts: the seed is secret (0600), the
# public key is not (0644).
SEED_MODE = 0o600
PUB_MODE = 0o644


class KeygenError(SBOMError):
    """Key generation failed (e.g. an existing file would be overwritten)."""


def default_pub_path(seed_path: PathLike) -> Path:
    """Derive the conventional public-key path from a seed path.

    ``seed.key`` → ``seed.key.pub``; a path already ending in ``.pub``
    gets its suffix replaced (``pub.pub`` is never produced).
    """
    path = Path(seed_path)
    if path.name.endswith(".pub"):
        return path.with_name(path.name[: -len(".pub")])
    return path.with_name(path.name + ".pub")


def _write_raw(path: Path, data: bytes, mode: int) -> None:
    """Write exactly *data* to *path* with *mode*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def write_keypair(seed: bytes, seed_path: PathLike, pub_path: PathLike) -> Tuple[Path, Path]:
    """Write *seed* and its derived public key to *seed_path* / *pub_path*.

    The public key is always derived from the seed (never taken as input),
    so the two files can never be mismatched. Parents are created. The seed
    file is written ``0o600``, the public key ``0o644``. Neither file may
    already exist (:class:`KeygenError` names the first offender); nothing
    is written when either would be overwritten.

    :param seed: the 32-byte ed25519 secret-key seed.
    :raises ValueError: if *seed* is not exactly 32 bytes.
    :raises KeygenError: if *seed_path* or *pub_path* already exists.
    """
    if len(seed) != ed25519.SEED_LEN:
        raise ValueError(
            f"ed25519 seed must be {ed25519.SEED_LEN} bytes, got {len(seed)}"
        )
    seed_path = Path(seed_path)
    pub_path = Path(pub_path)
    for existing in (seed_path, pub_path):
        if existing.exists():
            raise KeygenError(
                f"refusing to overwrite existing file {existing} "
                f"(keygen never replaces an existing key file)"
            )
    pub = ed25519.publickey(seed)
    _write_raw(seed_path, seed, SEED_MODE)
    _write_raw(pub_path, pub, PUB_MODE)
    return seed_path, pub_path


def generate_keypair(
    seed_path: PathLike,
    pub_path: Optional[PathLike] = None,
) -> Tuple[bytes, bytes, Path, Path]:
    """Generate a fresh ed25519 keypair and write it to disk.

    The seed comes from ``os.urandom(32)`` (the platform CSPRNG); the
    public key is derived from it. When *pub_path* is omitted it defaults
    to :func:`default_pub_path` of *seed_path* (``seed.key`` →
    ``seed.key.pub``).

    :returns: ``(seed, pub, seed_path, pub_path)``.
    :raises KeygenError: if either target file already exists.
    """
    seed = os.urandom(ed25519.SEED_LEN)
    if pub_path is None:
        pub_path = default_pub_path(seed_path)
    seed_path, pub_path = write_keypair(seed, seed_path, pub_path)
    return seed, ed25519.publickey(seed), seed_path, pub_path
