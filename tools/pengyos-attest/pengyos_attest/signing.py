"""Ed25519 head signing — the engine behind the ``sign`` / ``keygen``
subcommands and the ``verify --public-key`` check.

A chain head is anchored with a **detached ed25519 signature** over a small
canonical message derived from the head record's ``seq`` and ``hash``.
Signatures are NOT stored inside chain records: adding a ``sig`` key to a
record would change its own hash (extra keys participate in the hash), so a
signature can only ride alongside the chain. Signatures therefore live in an
**append-only sidecar** ``<log>.sigs`` (one JSON line per signature, each
self-verifying because the entry carries its own ``public_key``):

    {"head_hash": ..., "public_key": <64 hex>, "seq": N, "sig": <128 hex>,
     "ts": <ISO-8601 Z>}

Key handling: a **signing key** is a 32-byte ed25519 *seed*; a **verification
key** is a 32-byte *public key*. Key files hold either exactly 32 raw bytes
or a text file containing the 64-character hex of those bytes (whitespace
tolerant). A verify-key file may also contain a *seed*: the two
interpretations of 32 bytes are ambiguous (a random seed is itself a valid
curve point with ~50% probability), so :func:`verification_key_candidates`
returns up to both candidates and a check passes iff one candidate matches —
the same resolution as the sibling ``pengyos-sbom`` tool.

``keygen`` generates a fresh keypair (seed from ``os.urandom``) in the same
file formats, writing the seed ``0o600`` and the public key ``0o644``, and
never overwrites an existing key file.

Only the standard library is used (the ed25519 primitive is in-tree,
:mod:`pengyos_attest.ed25519`, pinned by the RFC 8032 §7.1 vectors).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import ed25519
from .record import utc_now

__all__ = [
    "SigningError",
    "KeygenError",
    "SIGS_SUFFIX",
    "HEAD_MESSAGE_VERSION",
    "sigs_path",
    "head_message",
    "load_key_bytes",
    "verification_key_candidates",
    "new_signature_entry",
    "entry_message",
    "load_signatures",
    "entry_shape_error",
    "entry_is_valid",
    "generate_keypair",
]

PathLike = Union[str, Path]

#: Suffix appended to a log's filename to name its signature sidecar file.
SIGS_SUFFIX: str = ".sigs"

#: Version tag in the signed message; bumping it invalidates old signatures.
HEAD_MESSAGE_VERSION: int = 1

#: Lowercase 64-hex-digit (a public key or record hash, ascii).
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class SigningError(ValueError):
    """A key file, signature entry, or signing operation is unusable."""


class KeygenError(SigningError):
    """Key generation failed (e.g. an existing file would be overwritten)."""


def sigs_path(log_path: PathLike) -> Path:
    """The signature sidecar path for a log: ``<log>.sigs`` (same directory).

    Like the ``.lock`` sidecar, the sidecar is a sibling in the same
    directory — but it is a plain data file, not a lock, and it is part of
    the *anchoring* record: verify re-checks every entry in it.
    """
    path = Path(log_path)
    return path.with_name(path.name + SIGS_SUFFIX)


def head_message(seq: int, head_hash: str) -> bytes:
    """The canonical byte string a head signature is computed over.

    The one and only definition (see ``spec.md``, "Head signing")::

        b"pengyos-attest/head-v1\\n"
        + b"seq=<seq>\\n"
        + b"hash=<64-hex head hash>\\n"

    The signature therefore binds BOTH the head's position in the chain
    (``seq``) and its content (``hash``, which binds the whole chain prefix
    through the hash rule). Ed25519 is deterministic, so a given head + seed
    always yields the same 64-byte signature (re-signable, diffable).
    """
    return (
        f"pengyos-attest/head-v{HEAD_MESSAGE_VERSION}\n"
        f"seq={seq}\n"
        f"hash={head_hash}\n"
    ).encode("ascii")


# --- Key files ------------------------------------------------------------

def load_key_bytes(path: PathLike) -> bytes:
    """Read *path* and return the raw 32-byte key it contains.

    Accepts a file of exactly 32 raw bytes, or a text file holding the
    64-character lowercase hex of those bytes (surrounding whitespace
    ignored). Anything else raises :class:`SigningError`. A missing file
    raises :class:`FileNotFoundError`.
    """
    path = Path(path)
    data = path.read_bytes()  # raises FileNotFoundError
    if len(data) == ed25519.SEED_LEN:
        return data
    text = data.decode("utf-8", errors="replace").strip()
    if len(text) == 64 and _HEX64_RE.match(text):
        return bytes.fromhex(text)
    raise SigningError(
        f"key file {path}: not 32 raw bytes and not a 64-character hex key"
    )


def verification_key_candidates(path: PathLike) -> List[bytes]:
    """Return the public keys a verify-key file may denote.

    Every 32-byte key file has (up to) two valid interpretations:

    * the decoded bytes **as a public key**, when they are a valid curve
      point;
    * the decoded bytes **as a secret-key seed**, from which the matching
      public key is derived — always, since derivation is deterministic and
      valid for any 32-byte input.

    The two are ambiguous: an ed25519 seed is itself a valid curve point
    with ~50% probability. Returning both candidates (deduplicated, public
    key first) lets a check pass iff *any* candidate matches — so the
    promise "the file that signed can verify" holds for every key,
    including on-curve seeds.
    """
    raw = load_key_bytes(path)
    candidates: List[bytes] = []
    if ed25519.is_on_curve(raw):
        candidates.append(raw)
    derived = ed25519.publickey(raw)
    if derived not in candidates:
        candidates.append(derived)
    return candidates


# --- Signature entries ------------------------------------------------------

_ENTRY_FIELDS = ("head_hash", "public_key", "seq", "sig", "ts")


def new_signature_entry(
    seq: int,
    head_hash: str,
    seed: bytes,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a signature entry for the head ``(seq, head_hash)`` signed with *seed*.

    The entry is self-verifying: it carries its own ``public_key`` (64 hex,
    derived from the seed) and its 64-byte ``sig`` (128 hex) over
    :func:`head_message`.

    :raises ValueError: if *seed* is not 32 bytes or *head_hash* is not
        64 lowercase hex.
    """
    if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1):
        raise ValueError(f"seq must be an integer >= 1, got {seq!r}")
    if not (isinstance(head_hash, str) and _HEX64_RE.match(head_hash)):
        raise ValueError(f"head_hash must be 64 lowercase hex chars, got {head_hash!r}")
    if len(seed) != ed25519.SEED_LEN:
        raise ValueError(f"ed25519 seed must be {ed25519.SEED_LEN} bytes, got {len(seed)}")
    pub = ed25519.publickey(seed)
    sig = ed25519.sign(head_message(seq, head_hash), seed)
    return {
        "seq": seq,
        "head_hash": head_hash,
        "ts": ts if ts is not None else utc_now(),
        "public_key": pub.hex(),
        "sig": sig.hex(),
    }


def entry_message(entry: Dict[str, Any]) -> bytes:
    """The byte string an entry's signature must cover (see :func:`head_message`)."""
    return head_message(entry["seq"], entry["head_hash"])


def entry_shape_error(entry: Any) -> Optional[str]:
    """``None`` iff *entry* is a well-formed signature entry shape, else a
    human-readable error string (NO cryptographic check).

    The shape is a JSON object with ``seq`` (integer >= 1) and
    ``head_hash`` / ``public_key`` / ``sig`` / ``ts`` (strings). The single
    definition of "well-formed" shared by :func:`load_signatures` (sidecar
    lines) and the export-bundle checks (``chain.verify_export``), so both
    enforce byte-identical shape rules.
    """
    if not isinstance(entry, dict):
        return "entry must be a JSON object"
    seq = entry.get("seq")
    if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1):
        return "'seq' must be an integer >= 1"
    for field in ("head_hash", "public_key", "sig", "ts"):
        if not isinstance(entry.get(field), str):
            return f"'{field}' must be a string"
    return None


def load_signatures(sigs_file: PathLike) -> List[Dict[str, Any]]:
    """Read the signature sidecar at *sigs_file* into a list of entry dicts.

    A missing or empty file yields an empty list (no signatures is a valid
    state — signing is optional). Blank lines are ignored. Any non-blank
    line that is not a well-formed entry (JSON object with the required
    fields of the right types — NO cryptographic check; the shape is
    :func:`entry_shape_error`) raises :class:`SigningError` naming the
    1-based line number.
    """
    path = Path(sigs_file)
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SigningError(f"signature log line {lineno}: not valid JSON: {exc.msg}") from exc
        shape_error = entry_shape_error(raw)
        if shape_error is not None:
            raise SigningError(f"signature log line {lineno}: {shape_error}")
        entries.append(raw)
    return entries


def entry_is_valid(entry: Dict[str, Any]) -> bool:
    """True iff *entry* carries a valid ed25519 signature over its own
    ``(seq, head_hash)`` under its own ``public_key``.

    Malformed hex fields or a public key that is not a valid curve point
    simply fail (return ``False``) — this function never raises.
    """
    try:
        pub = bytes.fromhex(entry["public_key"])
        sig = bytes.fromhex(entry["sig"])
    except (KeyError, TypeError, ValueError):
        return False
    if len(pub) != ed25519.PUBLIC_KEY_LEN or len(sig) != ed25519.SIGNATURE_LEN:
        return False
    try:
        message = entry_message(entry)
    except (KeyError, TypeError, ValueError):
        return False
    return ed25519.verify(message, sig, pub)


# --- Key generation ---------------------------------------------------------

# Unix file modes for the two artifacts: the seed is secret (0600), the
# public key is not (0644).
SEED_MODE = 0o600
PUB_MODE = 0o644


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


def generate_keypair(
    seed_path: PathLike,
    pub_path: Optional[PathLike] = None,
) -> Tuple[bytes, bytes, Path, Path]:
    """Generate a fresh ed25519 keypair and write it to disk.

    The seed comes from ``os.urandom(32)`` (the platform CSPRNG); the public
    key is derived from it. When *pub_path* is omitted it defaults to
    :func:`default_pub_path` of *seed_path* (``seed.key`` → ``seed.key.pub``).

    :returns: ``(seed, pub, seed_path, pub_path)``.
    :raises KeygenError: if either target file already exists (keygen never
        overwrites — clobbering the only copy of a signing seed is the exact
        failure this must not cause).
    """
    seed = os.urandom(ed25519.SEED_LEN)
    if pub_path is None:
        pub_path = default_pub_path(seed_path)
    seed_path, pub_path = Path(seed_path), Path(pub_path)
    for existing in (seed_path, pub_path):
        if existing.exists():
            raise KeygenError(
                f"refusing to overwrite existing file {existing} "
                f"(keygen never replaces an existing key file)"
            )
    pub = ed25519.publickey(seed)
    _write_raw(seed_path, seed, SEED_MODE)
    _write_raw(pub_path, pub, PUB_MODE)
    return seed, pub, seed_path, pub_path
