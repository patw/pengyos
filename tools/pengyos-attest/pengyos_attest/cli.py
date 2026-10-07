"""Command-line interface for ``pengyos-attest``.

The argument surface is defined and tested here so that later subtasks build on
a *stable, documented* interface (see ``spec.md``).
All four subcommands are implemented:

* ``--version`` — prints the version and exits 0.
* ``append --log L --type T --data JSON`` — appends one record to the hash
  chain atomically (see :mod:`.chain`).
* ``verify --log L [--anchor HASH]`` — walks the chain, recomputes each hash,
  detects tamper / truncation / reordering, and reports the first bad seq
  (see :func:`.chain.verify_chain`). Exit 0 on a clean chain, 1 on a finding
  (or I/O error).
* ``show --log L [--tail N] [--json]`` — prints the records (pretty, or one
  compact JSON line each with ``--json``); a missing/empty log prints nothing
  and exits 0; a corrupt log exits 1.
* ``export --log L --out F`` — bundles the chain with a Merkle-ish root hash
  (see :func:`.chain.export_chain`); a corrupt source log exits 1.
* ``sign --log L --signing-key FILE`` — signs the current chain head with an
  ed25519 key (item 7): a self-verifying entry is appended to the
  ``<log>.sigs`` sidecar (see :func:`.chain.sign_head`). A corrupt or empty
  chain exits 1, an already-signed head exits 1, a bad key file exits 2.
* ``keygen --seed PATH`` — generates a fresh ed25519 keypair: the 32-byte
  seed at PATH (mode 0600) and the derived public key at ``PATH.pub``
  (mode 0644); never overwrites an existing file (exit 1).
* ``verify --public-key FILE`` — additionally requires the current head to
  be covered by a valid signature from that key (see
  :func:`.chain.verify_chain`); a missing/invalid coverage exits 1.

``EXIT_NOT_IMPLEMENTED`` (``3``) is retained for the exit-code contract but no
subcommand currently returns it.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, chain, signing
from .record import RecordError, canonical_json, to_json_line

# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # success
EXIT_FAIL: int = 1  # an operation ran but reported a failure (e.g. verify failed)
EXIT_USAGE: int = 2  # argparse's default for bad usage
EXIT_NOT_IMPLEMENTED: int = 3  # (reserved) subcommand recognised but not implemented

# --- Documented interface defaults -----------------------------------------
# The event type is the record's ``type`` field; a neutral default keeps the
# append usable even when the caller omits it. ``--data`` is a JSON string;
# ``null`` (no data) is a valid, common case.
DEFAULT_EVENT_TYPE: str = "event"
DEFAULT_DATA: str = "null"


def _add_log_flag(parser: argparse.ArgumentParser, required: bool = True) -> None:
    """Register the shared ``--log`` flag (the path to the JSONL hash chain)."""
    parser.add_argument(
        "--log",
        metavar="L",
        required=required,
        help="Path to the append-only JSONL hash-chain log file.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-attest",
        description=(
            "Tamper-evident, append-only hash-chained attestation log for "
            "PengyOS build and change events."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )

    sub = parser.add_subparsers(
        dest="command", metavar="{append,show,verify,export,sign,keygen}")

    append_p = sub.add_parser(
        "append",
        help="Append one event record to the hash chain.",
        description=(
            "Read the last record, compute hash = sha256(prev_hash || "
            "canonical(record)), and append a new JSONL record atomically."
        ),
    )
    _add_log_flag(append_p)
    append_p.add_argument(
        "--type",
        dest="event_type",
        default=DEFAULT_EVENT_TYPE,
        metavar="T",
        help="The event type (record ``type`` field). Default: %(default)s.",
    )
    append_p.add_argument(
        "--data",
        default=DEFAULT_DATA,
        metavar="JSON",
        help="The event payload as a JSON string (record ``data`` field). "
        "Default: %(default)s.",
    )

    show_p = sub.add_parser(
        "show",
        help="Print the records in the log.",
        description="Pretty-print the log's records, optionally the last N.",
    )
    _add_log_flag(show_p)
    show_p.add_argument(
        "--tail",
        type=int,
        default=None,
        metavar="N",
        help="Show only the last N records.",
    )
    show_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit one record per line as JSON instead of the pretty format.",
    )

    verify_p = sub.add_parser(
        "verify",
        help="Verify the integrity of the hash chain.",
        description=(
            "Walk the chain, recompute each hash, and detect tamper, "
            "truncation, or reordering; report the first bad seq. With "
            "--anchor, the chain must end at the anchored head hash, which "
            "is what makes a lost (truncated) tail provable."
        ),
    )
    _add_log_flag(verify_p)
    verify_p.add_argument(
        "--anchor",
        default=None,
        metavar="HASH",
        help=(
            "Optional: the hash of a previously committed head record. The "
            "chain must end at exactly this hash; otherwise the chain is "
            "reported as truncated."
        ),
    )
    verify_p.add_argument(
        "--public-key",
        default=None,
        metavar="FILE",
        help=(
            "Optional: a 32-byte ed25519 public key file (or seed file; the "
            "file that signed can verify). The current chain head must be "
            "covered by a valid signature from that key in the <log>.sigs "
            "sidecar; every stored signature is also re-checked."
        ),
    )

    export_p = sub.add_parser(
        "export",
        help="Bundle the log with a Merkle-ish root hash.",
        description=(
            "Write a self-contained export of the log plus a root hash that "
            "anchors the whole chain for external attestation."
        ),
    )
    _add_log_flag(export_p)
    export_p.add_argument(
        "--out",
        required=True,
        metavar="F",
        help="Path to write the export bundle.",
    )

    sign_p = sub.add_parser(
        "sign",
        help="Sign the current chain head with an ed25519 key (external anchoring).",
        description=(
            "Sign the hash of the current head record with an ed25519 "
            "signing key and append a self-verifying signature entry to the "
            "<log>.sigs sidecar, anchoring the chain head (and everything "
            "before it) to the key for external verification."
        ),
    )
    _add_log_flag(sign_p)
    sign_p.add_argument(
        "--signing-key",
        required=True,
        metavar="FILE",
        help=(
            "Path to the 32-byte ed25519 signing key (secret-key seed): 32 "
            "raw bytes, or a text file with its 64-character hex. Generate "
            "one with the keygen subcommand."
        ),
    )

    keygen_p = sub.add_parser(
        "keygen",
        help="Generate a fresh ed25519 keypair (seed + public key).",
        description=(
            "Generate a fresh ed25519 keypair from the platform CSPRNG: the "
            "32-byte seed at --seed PATH (mode 0600) and the derived public "
            "key at PATH.pub (mode 0644). Never overwrites an existing file."
        ),
    )
    keygen_p.add_argument(
        "--seed",
        required=True,
        metavar="PATH",
        help=(
            "Where to write the signing key (32-byte seed, mode 0600); the "
            "public key is written to PATH.pub (mode 0644)."
        ),
    )

    return parser


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point. Returns a process exit code (never raises on good use).

    ``argv`` is accepted for testability and defaults to ``sys.argv[1:]``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pengyos-attest {__version__}")
        return EXIT_OK

    if not args.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    if args.command == "append":
        return _run_append(args)

    if args.command == "verify":
        return _run_verify(args)

    if args.command == "show":
        return _run_show(args)

    if args.command == "export":
        return _run_export(args)

    if args.command == "sign":
        return _run_sign(args)

    if args.command == "keygen":
        return _run_keygen(args)

    # Unreachable for a parser we built, but never fall through silently.
    sys.stderr.write(f"pengyos-attest: unknown subcommand '{args.command}'\n")
    return EXIT_USAGE


def _run_append(args: argparse.Namespace) -> int:
    """Implement the ``append`` subcommand. Returns a process exit code.

    ``--data`` is a JSON string parsed into the record's ``data`` field; a
    malformed payload is a usage error (``2``), as is an invalid ``--type``.
    A corrupt existing log or an I/O failure is an operation failure (``1``);
    a well-formed append succeeds (``0``).
    """
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"pengyos-attest: --data is not valid JSON: {exc}\n")
        return EXIT_USAGE
    try:
        record = chain.append_event(args.log, args.event_type, data)
    except RecordError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_USAGE
    except chain.LogError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_FAIL
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot write log: {exc}\n")
        return EXIT_FAIL
    print(f"appended seq={record['seq']} hash={record['hash']}")
    return EXIT_OK


def _run_verify(args: argparse.Namespace) -> int:
    """Implement the ``verify`` subcommand. Returns a process exit code.

    A clean chain (including a missing/empty log) exits 0 and prints a
    summary line; any finding (tamper, reorder, truncation against the
    anchor, unparseable line, missing/invalid head signature) exits 1 and
    prints the finding (with the first bad seq/line for chain findings) to
    stderr. An I/O error exits 1 as well; an unreadable or malformed
    ``--public-key`` file is a usage error (2).
    """
    candidates = None
    if args.public_key is not None:
        try:
            candidates = signing.verification_key_candidates(args.public_key)
        except signing.SigningError as exc:
            sys.stderr.write(f"pengyos-attest: {exc}\n")
            return EXIT_USAGE
        except OSError as exc:
            sys.stderr.write(f"pengyos-attest: cannot read key file: {exc}\n")
            return EXIT_USAGE
    try:
        report = chain.verify_chain(args.log, anchor=args.anchor,
                                    public_key=candidates)
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot read log: {exc}\n")
        return EXIT_FAIL
    if report.ok:
        print(f"OK: chain verified ({report.record_count} record"
              f"{'s' if report.record_count != 1 else ''})")
        if candidates is not None:
            print(f"head signed by key {candidates[0].hex()[:16]}...")
        elif report.signature_count:
            print(f"{report.signature_count} stored signature"
                  f"{'s' if report.signature_count != 1 else ''} verified")
        return EXIT_OK
    where = []
    if report.first_bad_seq is not None:
        where.append(f"first bad seq={report.first_bad_seq}")
    if report.first_bad_line is not None:
        where.append(f"line {report.first_bad_line}")
    suffix = f" ({', '.join(where)})" if where else ""
    sys.stderr.write(f"FAIL: {report.reason}{suffix}\n")
    return EXIT_FAIL


def _pretty_record(record: dict) -> str:
    """Render one record in the pretty, grep-friendly ``show`` format.

    First line: ``seq=N ts=… type=…`` (single space between fields);
    second/third: the full ``hash`` and the compact canonical ``data``.
    Tolerated extra keys (e.g. a future ``sig``) are not shown here —
    ``show --json`` always emits the complete record.
    """
    return (
        f"seq={record['seq']} ts={record['ts']} type={record['type']}\n"
        f"  hash: {record['hash']}\n"
        f"  data: {canonical_json(record['data'])}"
    )


def _run_show(args: argparse.Namespace) -> int:
    """Implement the ``show`` subcommand. Returns a process exit code.

    Pretty by default; ``--json`` emits one compact JSON line per record
    (byte-identical to the line stored in the log for valid records).
    ``--tail N`` limits the output to the last N records (N >= 1, else a
    usage error). A missing/empty log is a valid empty chain: nothing is
    printed and the exit code is 0. A corrupt log is an operation failure
    (1), as is an I/O error.
    """
    if args.tail is not None and args.tail < 1:
        sys.stderr.write("pengyos-attest: --tail must be >= 1\n")
        return EXIT_USAGE
    try:
        records = chain.read_records(args.log)
    except chain.LogError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_FAIL
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot read log: {exc}\n")
        return EXIT_FAIL
    if args.tail is not None:
        records = records[-args.tail:]
    if args.json:
        for record in records:
            print(to_json_line(record))
    else:
        for record in records:
            print(_pretty_record(record))
    return EXIT_OK


def _run_export(args: argparse.Namespace) -> int:
    """Implement the ``export`` subcommand. Returns a process exit code.

    Bundles the log with its Merkle root into ``--out`` (exit 0, prints a
    summary); a corrupt source chain is an operation failure (1), as is an
    I/O error writing the bundle.
    """
    try:
        bundle = chain.export_chain(args.log, args.out)
    except chain.LogError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_FAIL
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot write export: {exc}\n")
        return EXIT_FAIL
    count = bundle["record_count"]
    plural = "s" if count != 1 else ""
    sigs = len(bundle["sigs"])
    sig_note = (f", {sigs} signature{'s' if sigs != 1 else ''}"
                if sigs else "")
    print(f"exported {count} record{plural} to {args.out} "
          f"(root={bundle['root_hash']}{sig_note})")
    return EXIT_OK


def _run_sign(args: argparse.Namespace) -> int:
    """Implement the ``sign`` subcommand. Returns a process exit code.

    Loads the 32-byte signing key (a bad key file is a usage error, 2),
    signs the current head, and appends the entry to the ``<log>.sigs``
    sidecar (exit 0, prints ``signed seq=N head=...``). A corrupt or empty
    chain or an already-signed head is an operation failure (1), as is an
    I/O error.
    """
    try:
        seed = signing.load_key_bytes(args.signing_key)
    except signing.SigningError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_USAGE
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot read key file: {exc}\n")
        return EXIT_USAGE
    try:
        entry = chain.sign_head(args.log, seed)
    except chain.LogError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_FAIL
    except signing.SigningError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_FAIL
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot write signature log: {exc}\n")
        return EXIT_FAIL
    print(f"signed seq={entry['seq']} head={entry['head_hash']} "
          f"key={entry['public_key'][:16]}...")
    return EXIT_OK


def _run_keygen(args: argparse.Namespace) -> int:
    """Implement the ``keygen`` subcommand. Returns a process exit code.

    Generates a fresh ed25519 keypair into ``--seed PATH`` (0600) and
    ``PATH.pub`` (0644) and prints the locations plus the public key (exit
    0). An existing target file is an operation failure (1); so is an I/O
    error.
    """
    try:
        seed, pub, seed_path, pub_path = signing.generate_keypair(args.seed)
    except signing.KeygenError as exc:
        sys.stderr.write(f"pengyos-attest: {exc}\n")
        return EXIT_FAIL
    except OSError as exc:
        sys.stderr.write(f"pengyos-attest: cannot write key file: {exc}\n")
        return EXIT_FAIL
    print(f"generated ed25519 keypair: seed={seed_path} (mode 0600), "
          f"public key={pub_path} (mode 0644)")
    print(f"public key: {pub.hex()}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
