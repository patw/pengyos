"""pengyos-attest — tamper-evident, append-only hash-chained event log.

The trust record for everything PengyOS builds or changes (SPEC §1.5/§3). Each
event is a JSON record appended to a JSONL file, linked to its predecessor by a
``sha256`` hash chain so that any modification, reordering, or truncation of a
committed record is detectable by :func:`.chain.verify_chain`.

:mod:`.record` defines the record schema (``seq``/``ts``/``type``/``data``/
``prev_hash``/``hash``), the canonical JSON serialization, and the hash rule;
:mod:`.chain` implements the atomic, crash-safe append (read the last record,
compute the next hash, temp-file + fsync + rename) and the chain verifier
(walk, recompute hashes, report the first bad seq, optional anchor for
truncation detection, optional ed25519 head-signature check) and the export
bundle (Merkle root over the record hashes, re-checkable with
:func:`.chain.verify_export`); :mod:`.signing` holds the detached ed25519
head signatures (the ``<log>.sigs`` sidecar, key files, keygen) and
:mod:`.ed25519` the pure-stdlib RFC 8032 primitive; :mod:`.cli` defines the
command-line surface (``append``, ``show``, ``verify``, ``export``,
``sign``, ``keygen``).

See ``spec.md`` and ``README.md`` for the full interface and behaviour
contract.
"""

from . import chain, record  # noqa: E402,F401  # record schema + hash rule; append I/O

__version__ = "0.10.0"

__all__ = ["__version__", "chain", "record"]
