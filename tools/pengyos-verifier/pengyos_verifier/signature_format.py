"""The ``pengyos-sig-v1`` .sig wire format, independently defined.

A ``.sig`` file for an artifact is a header line (terminated by a
newline) followed by the raw 64-byte ed25519 signature — the format
v1 defined by ``pengyos-sign`` (its ``FORMAT.md`` is the upstream
authority; this module is the verifier's own copy of the contract so
the witness never depends on the signer):

    pengyos-sig-v1 1 ed25519 <keyid-hex> sha256 <hash-hex>\\n<64 raw signature bytes>

The header is six whitespace-separated fields: ``magic``, ``version``,
``algorithm``, ``keyid`` (``SHA-256(public_key)``, 64 lowercase hex),
``hash-algorithm``, and ``hash`` (``SHA-256(artifact)``, 64 lowercase
hex). The format is versioned via ``version`` and ``magic``; verifiers
reject unknown versions.

What the witness can check WITHOUT a public key (what the signatures
check enforces today):

* the file exists, is exactly header-line + 64 body bytes,
* the header is well-formed (six fields, correct magic/version/
  algorithm/hash-algorithm, 64-hex keyid and hash),
* the embedded ``hash`` equals the artifact's actual sha256 — the
  signature is *bound* to this exact artifact.

The header's ``keyid`` is what binds a signature to the supplied
public key: the witness requires ``keyid == SHA-256(public_key)``
(:func:`keyid_of`) before it will accept the 64-byte ed25519 body, and
``--pub FILE`` supplies the key itself (loaded by
:func:`load_public_key`; malformed key files are a CLI usage error,
not a check finding).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional

MAGIC = "pengyos-sig-v1"
VERSION = 1
ALGORITHM = "ed25519"
HASH_ALGORITHM = "sha256"
BODY_BYTES = 64  # raw ed25519 signature (RFC 8032)
HEADER_FIELDS = ("magic", "version", "algorithm", "keyid", "hash_algorithm", "hash")

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SigHeader:
    """A parsed, field-valid v1 signature header (see module doc)."""

    magic: str
    version: int
    algorithm: str
    keyid: str
    hash_algorithm: str
    hash: str


def parse_sig_header_line(line: str) -> Optional[SigHeader]:
    """Parse a header line; return None if it is not a v1 header.

    Well-formed means: exactly six whitespace-separated fields, the
    first three matching the fixed constants, ``version`` an
    unsigned decimal integer, and ``keyid``/``hash`` 64 lowercase hex
    chars. This is a shape check only — the ``hash`` is NOT checked
    against an artifact here (that is the caller's job, with the
    file to hash).
    """
    fields = line.split()
    if len(fields) != 6:
        return None
    magic, version_s, algorithm, keyid, hash_algorithm, digest = fields
    if magic != MAGIC or algorithm != ALGORITHM \
            or hash_algorithm != HASH_ALGORITHM:
        return None
    if not version_s.isdigit() or int(version_s) != VERSION:
        return None
    if not _HEX64_RE.match(keyid) or not _HEX64_RE.match(digest):
        return None
    return SigHeader(
        magic=magic,
        version=int(version_s),
        algorithm=algorithm,
        keyid=keyid,
        hash_algorithm=hash_algorithm,
        hash=digest,
    )


def sig_header_errors(line: str) -> List[str]:
    """Human-readable header errors (empty list iff the line parses).

    The first error is the specific one; callers may surface it as the
    evidence line.
    """
    fields = line.split()
    errors: List[str] = []
    if len(fields) != 6:
        return [
            f"header must have exactly 6 whitespace-separated fields, "
            f"got {len(fields)}"
        ]
    magic, version_s, algorithm, keyid, hash_algorithm, digest = fields
    if magic != MAGIC:
        errors.append(f"bad magic {magic!r} (expected {MAGIC!r})")
    if not version_s.isdigit() or int(version_s) != VERSION:
        errors.append(
            f"bad version {version_s!r} (expected {VERSION})"
        )
    if algorithm != ALGORITHM:
        errors.append(f"bad algorithm {algorithm!r} (expected {ALGORITHM!r})")
    if hash_algorithm != HASH_ALGORITHM:
        errors.append(
            f"bad hash-algorithm {hash_algorithm!r} (expected {HASH_ALGORITHM!r})"
        )
    if not _HEX64_RE.match(keyid):
        errors.append("keyid must be 64 lowercase hex chars")
    if not _HEX64_RE.match(digest):
        errors.append("hash must be 64 lowercase hex chars")
    return errors


def sig_file_errors(raw: bytes) -> List[str]:
    """Structural errors of a whole .sig file (no artifact involved).

    Checks, in order: exactly one header line followed by a newline
    (no extra text on the line), a field-valid header, and a body of
    exactly ``BODY_BYTES`` bytes. Returns ``[]`` when the file is a
    structurally valid v1 signature.
    """
    if not raw:
        return ["empty signature file"]
    if b"\n" not in raw:
        return ["missing newline after header line"]
    header_line, _, body = raw.partition(b"\n")
    try:
        line = header_line.decode("ascii")
    except UnicodeDecodeError:
        return ["header line is not ASCII"]
    if len(body) != BODY_BYTES:
        return [
            f"body must be exactly {BODY_BYTES} bytes "
            f"(the ed25519 signature), got {len(body)}"
        ]
    errors = sig_header_errors(line)
    return errors


def sha256_file(path: str) -> str:
    """Streaming sha256 hex of a file (the digest a signature must bind)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_PUBLIC_KEY_LEN = 32
_HEX64_KEY_RE = re.compile(r"^([0-9a-fA-F]{64})$")


def keyid_of(pub: bytes) -> str:
    """The v1 header ``keyid`` for a 32-byte ed25519 public key.

    Per ``pengyos-sig-v1`` the keyid is ``SHA-256(public_key)`` as 64
    lowercase hex — it is what lets the witness bind a signature to the
    key a verifier later supplies with ``--pub``.

    :raises ValueError: if *pub* is not exactly 32 bytes.
    """
    if len(pub) != _PUBLIC_KEY_LEN:
        raise ValueError(
            f"ed25519 public key must be {_PUBLIC_KEY_LEN} bytes, "
            f"got {len(pub)}"
        )
    return hashlib.sha256(pub).hexdigest()


class PublicKeyError(ValueError):
    """A ``--pub`` key file is unusable (a CLI usage error, not a check
    finding — the witness was told where the key is and could not get
    one)."""


def load_public_key(path: str) -> bytes:
    """Load a 32-byte ed25519 public key from *path*.

    Accepts a file of exactly 32 raw bytes, or a text file holding the
    64-character hex of those bytes (case-insensitive, surrounding
    whitespace ignored) — the same two key-file formats the sibling
    signer/attest tools use for ``--public-key``. A file that is
    neither raises :class:`PublicKeyError` (the CLI turns it into
    exit 2); a missing file raises :class:`FileNotFoundError`.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) == _PUBLIC_KEY_LEN:
        return raw
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise PublicKeyError(
            f"{path}: not 32 raw bytes and not ASCII text "
            f"({len(raw)} bytes)"
        ) from None
    m = _HEX64_KEY_RE.match(text)
    if m is None:
        raise PublicKeyError(
            f"{path}: expected 32 raw bytes or 64 hex characters, "
            f"got {len(raw)} byte(s): {text[:32]!r}"
        )
    return bytes.fromhex(m.group(1))
