"""``pengyos-secureboot-enroll`` command-line interface.

This module is the dispatcher for the ``pengyos-secureboot-enroll`` CLI. It wires
up the four subcommands documented in ``spec.md`` (``keys``, ``sign-chain``,
``enroll`` and ``lint``), their argument parsers and the routing table.

All four subcommands are implemented; the command
bodies that are not yet implemented raise :class:`NotYetImplemented`, which
:func:`main` turns into a clean, non-zero exit code and a stderr message. The
plumbing is real and tested; the remaining feature work is tracked separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import keys as keys_module
from .keys import (
    PrivateKeyError,
    MissingKeyError,
    GenerateError,
    SignError,
)
from . import sign_chain as sign_chain_module
from .sign_chain import SIGNATURES_JSON, load_bindings
from .signer import select_signer
from . import enroll as enroll_module
from .enroll import EnrollError
from . import lint as lint_module
from .lint import LintError

__all__ = [
    "EXIT_NOT_YET_IMPLEMENTED",
    "NotYetImplemented",
    "build_parser",
    "cmd_keys",
    "cmd_sign_chain",
    "cmd_enroll",
    "cmd_lint",
    "main",
]

#: Filenames the ``keys`` command writes alongside the key material.
_METADATA_JSON = "metadata.json"
_METADATA_SIG = "metadata.sig"

# Exit code used when a command's implementation is not present yet. Kept
# distinct from argparse's usage error (2) so tests can tell them apart.
EXIT_NOT_YET_IMPLEMENTED = 3

# The four subcommands documented in spec.md / implemented across the run.
SUBCOMMANDS = ("keys", "sign-chain", "enroll", "lint")


class NotYetImplemented(RuntimeError):
    """A command handler whose implementation is a future, tracked subtask."""


def _add_keys_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Output directory for the PK/KEK/db key material and metadata.",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Generate fresh key material and re-sign the existing chain.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of a human report.",
    )


def _add_sign_chain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--esp",
        required=True,
        metavar="DIR",
        help="Path to the mounted EFI System Partition (contains the UKI + boot).",
    )
    parser.add_argument(
        "--keys",
        required=True,
        metavar="DIR",
        help="Directory containing the PK/KEK/db key material.",
    )
    parser.add_argument(
        "--edition",
        default=sign_chain_module.DEFAULT_EDITION,
        metavar="NAME",
        help="PengyOS edition for the pinned UKI path (default: "
        f"{sign_chain_module.DEFAULT_EDITION}).",
    )
    parser.add_argument(
        "--version",
        default=sign_chain_module.DEFAULT_VERSION,
        metavar="VER",
        help="PengyOS version for the pinned UKI path (default: "
        f"{sign_chain_module.DEFAULT_VERSION}).",
    )
    parser.add_argument(
        "--slots",
        default=",".join(sign_chain_module.DEFAULT_SLOTS),
        metavar="SLOT[,SLOT...]",
        help="Installed A/B slots for the pinned UKI paths (default: "
        f"{sign_chain_module.DEFAULT_SLOTS}).",
    )
    parser.add_argument(
        "--signer",
        choices=("stdlib", "openssl"),
        default=None,
        metavar="NAME",
        help="Signer to use: stdlib (Ed25519, default) or openssl (real ECDSA).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of a human report.",
    )


def _add_enroll_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--keys",
        required=True,
        metavar="DIR",
        help="Directory containing the PK/KEK/db key material.",
    )
    parser.add_argument(
        "--out",
        default=".",
        metavar="DIR",
        help="Directory to write the enrolment material into (default: cwd).",
    )
    parser.add_argument(
        "--mode",
        choices=("mok", "custom"),
        required=True,
        metavar="MODE",
        help="Enrolment target: MOK (MOKmgr) or a custom-key enrolment bundle.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of instructions.",
    )


def _add_lint_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--esp",
        required=True,
        metavar="DIR",
        help="Path to the mounted EFI System Partition to lint.",
    )
    parser.add_argument(
        "--keys",
        required=True,
        metavar="DIR",
        help="Directory containing the PK/KEK/db key material (db is the trust root).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of a human report.",
    )


def _has_key_set(keys_dir: Path) -> bool:
    """Return ``True`` when ``keys_dir`` already holds a full PK/KEK/db set.

    All three certificates must be present (that is what
    :func:`keys.load_key_set` requires). Used to decide whether ``keys`` should
    generate fresh material or reload the existing trust root.
    """
    return all((keys_dir / f"{role}.cert").is_file() for role in ("PK", "KEK", "DB"))


def _write_metadata(keys_dir: Path, metadata, signature: bytes) -> None:
    """Persist the signed key metadata as ``metadata.json`` + ``metadata.sig``.

    The metadata (key fingerprints) and its signature are public enrolment
    material, so they are written world-readable (0644) regardless of the
    process umask.
    """
    meta_path = keys_dir / _METADATA_JSON
    sig_path = keys_dir / _METADATA_SIG
    meta_path.write_text(
        json.dumps(metadata.to_json(), indent=2) + "\n", encoding="utf-8"
    )
    sig_path.write_bytes(signature)
    meta_path.chmod(0o644)
    sig_path.chmod(0o644)


def cmd_keys(args: argparse.Namespace) -> int:
    """Generate/rotate the PK/KEK/db trust root and emit signed key metadata.

    With ``--rotate`` (or when ``--out`` holds no key set yet) fresh key material
    is generated and the trust root rotated; otherwise the existing key set is
    reloaded and its metadata re-signed. Either way the metadata is signed by the
    ``PK`` key and written as ``metadata.json`` + ``metadata.sig`` next to the
    key material.

    The report lists each role's key id (the cert fingerprint -- never the key
    itself) and the metadata digest. External tools run through the injectable
    openssl shim, so this never needs root or a real NVRAM.
    """
    out_dir = Path(args.out)

    if out_dir.exists() and not out_dir.is_dir():
        print(
            f"pengyos-secureboot-enroll: error: {args.out!r} exists and is not a directory",
            file=sys.stderr,
        )
        return 1

    try:
        if not args.rotate and _has_key_set(out_dir):
            trust_root = keys_module.load_key_set(out_dir)
            rotated = False
        else:
            keys_module.generate_all(out_dir)
            trust_root = keys_module.load_key_set(out_dir)
            rotated = True

        metadata = keys_module.build_metadata(trust_root)
        signature = keys_module.sign_metadata(metadata, out_dir)
        _write_metadata(out_dir, metadata, signature)
    except (
        GenerateError,
        MissingKeyError,
        PrivateKeyError,
        SignError,
    ) as exc:
        print(f"pengyos-secureboot-enroll: error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "out": str(out_dir),
                    "rotated": rotated,
                    "roles": trust_root.to_json()["roles"],
                    "metadata": metadata.to_json(),
                    "metadata_path": str(out_dir / _METADATA_JSON),
                    "signature_path": str(out_dir / _METADATA_SIG),
                },
                indent=2,
            )
        )
        return 0

    print(f"PengyOS Secure Boot key material: {out_dir}")
    for role, entry in trust_root.to_json()["roles"].items():
        print(f"  {role:<3} {entry['key_id']}")
    print(f"  metadata digest: {metadata.digest}")
    print(f"  metadata signed by: {keys_module.METADATA_SIGNER_ROLE}")
    print(f"  metadata: {out_dir / _METADATA_JSON}")
    print(f"  signature: {out_dir / _METADATA_SIG}")
    return 0


def cmd_sign_chain(args: argparse.Namespace) -> int:
    """Sign the UKI + systemd-boot, verify, and record the signature bindings.

    Loads the ``db`` trust root from ``--keys``, builds the manifest of EFI
    binaries to sign (the pinned PengyOS boot-chain layout, or an explicit
    ``--paths`` list), signs each binary present in ``--esp`` with the db key,
    verifies every signature, and persists the bindings to
    ``<keys>/signatures.json`` (plus the Ed25519 verification-key sidecar next
    to the db cert) so ``lint`` can check them.

    Signing runs through the injectable signer (stdlib Ed25519 by default, or
    openssl for real ECDSA); no root, no real NVRAM.
    """
    esp_dir = Path(args.esp)
    keys_dir = Path(args.keys)

    if not esp_dir.is_dir():
        print(
            f"pengyos-secureboot-enroll: error: ESP not found: {esp_dir}",
            file=sys.stderr,
        )
        return 1

    # Load the trust root (needs the db key) before touching anything.
    try:
        keys_module.check_key_set(keys_dir)
        trust_root = keys_module.load_key_set(keys_dir)
    except (MissingKeyError, PrivateKeyError, GenerateError) as exc:
        print(
            f"pengyos-secureboot-enroll: error: {exc}",
            file=sys.stderr,
        )
        return 1

    slots = [s for s in args.slots.split(",") if s]
    manifest = sign_chain_module.build_manifest(
        edition=args.edition,
        version=args.version,
        slots=slots,
    )

    signer = select_signer(args.signer)

    try:
        signed = sign_chain_module.sign_binaries(
            esp_dir, manifest, trust_root, signer=signer
        )
    except sign_chain_module.SignChainError as exc:
        print(
            f"pengyos-secureboot-enroll: error: {exc}",
            file=sys.stderr,
        )
        return 1

    sig_path = sign_chain_module.save_bindings(signed.bindings, keys_dir / SIGNATURES_JSON)

    if args.json:
        print(json.dumps(signed.to_json(), indent=2))
        return 0

    print(f"PengyOS Secure Boot chain signed with {signer.name}:")
    for binding in signed.bindings:
        print(f"  {binding.path}")
        print(f"    sha256 {binding.sha256}")
        print(f"    signed by db {binding.key_id}")
    print(f"  bindings: {sig_path}")
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    """Emit MOK/custom-key enrolment material + operator instructions.

    Loads the trust root from ``--keys`` (refusing insecure-perm keys), builds
    the enrolment material in ``--mode`` (``mok`` -- a ``mokutil --import``-ready
    DER cert, or ``custom`` -- a self-authenticating JSON bundle), writes it to
    ``--out`` (default: the current directory), and prints a report (or
    ``--json``). The material is public; the private keys are never read out.
    No firmware NVRAM is written (that is an operator step on a real machine).
    """
    try:
        material = enroll_module.build_enrolment(Path(args.keys), mode=args.mode)
        out_dir = enroll_module.write_enrolment(material, Path(args.out))
    except (MissingKeyError, PrivateKeyError, SignError, EnrollError) as exc:
        print(f"pengyos-secureboot-enroll: error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(material.to_json(), indent=2))
        return 0

    print(f"PengyOS Secure Boot enrolment material ({material.mode}): {out_dir}")
    print(f"  db key id: {material.db_key_id}")
    print(f"  db cert:   {out_dir / 'db.cert.der'}")
    if not material.is_mok:
        print(f"  bundle:    {out_dir / enroll_module.ENROLMENT_JSON}")
    print(f"  instructions: {out_dir / enroll_module.INSTRUCTIONS}")
    print("  Next: run the instructions on the target machine to enrol the db key.")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    """Lint the ESP: every expected EFI binary must be signed by a key in db.

    Loads the ``db`` trust root from ``--keys`` (refusing insecure-perm keys),
    builds the manifest of EFI binaries to check (the pinned PengyOS boot-chain
    layout, or an explicit ``--paths`` list), loads the signature bindings that
    :mod:`pengyos_secureboot_enroll.sign_chain` wrote to ``signatures.json`` and
    runs :func:`pengyos_secureboot_enroll.lint.lint_esp` over the ESP.

    The ESP lints clean only when every expected binary is present, signed by the
    ``db`` key, and the signature verifies against the current bytes. Any
    violation (unsigned / untrusted signer / signature does not verify) is
    reported and the command exits 1 (fail closed). Never prints key material.
    """
    esp_dir = Path(args.esp)
    keys_dir = Path(args.keys)

    if not esp_dir.is_dir():
        print(
            f"pengyos-secureboot-enroll: error: ESP not found: {esp_dir}",
            file=sys.stderr,
        )
        return 1

    # Load the trust root (needs the db key) before touching anything.
    try:
        keys_module.check_key_set(keys_dir)
        trust_root = keys_module.load_key_set(keys_dir)
    except (MissingKeyError, PrivateKeyError, GenerateError) as exc:
        print(f"pengyos-secureboot-enroll: error: {exc}", file=sys.stderr)
        return 1

    # Lint checks the pinned PengyOS boot-chain manifest (default single ``a``
    # slot). ``--slots/--edition/--version`` are ``sign-chain`` knobs; the lint
    # subcommand interface (spec.md) is just ``--esp --keys [--json]``.
    manifest = sign_chain_module.build_manifest()

    # The recorded signature bindings (signatures.json) stand in for the
    # Authenticode signature the firmware reads from the binary.
    bindings = load_bindings(keys_dir / SIGNATURES_JSON)

    try:
        result = lint_module.lint_esp(esp_dir, manifest, trust_root, bindings=bindings)
    except LintError as exc:
        print(f"pengyos-secureboot-enroll: error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "esp": str(esp_dir),
                    "verified": result.verified,
                    "binaries_checked": result.binaries_checked,
                    "violations": [
                        {"path": v.path, "reason": v.reason}
                        for v in result.violations
                    ],
                },
                indent=2,
            )
        )
        return 0 if result.verified else 1

    if result.verified:
        noun = "binary" if result.binaries_checked == 1 else "binaries"
        print(
            f"PengyOS Secure Boot ESP lint OK: {result.binaries_checked} {noun} "
            f"signed by db ({trust_root.db.key_id})"
        )
        return 0

    print(
        f"pengyos-secureboot-enroll: ESP lint FAILED: {len(result.violations)} "
        f"violation(s)",
        file=sys.stderr,
    )
    for violation in result.violations:
        print(f"  {violation.path}: {violation.reason}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for ``pengyos-secureboot-enroll``."""
    parser = argparse.ArgumentParser(
        prog="pengyos-secureboot-enroll",
        description=(
            "Sign and enrol the PengyOS Secure Boot chain: generate/rotate "
            "PK/KEK/db, sign the UKI + systemd-boot, emit enrolment material, "
            "and lint the ESP."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    p_keys = subparsers.add_parser(
        "keys",
        help="Generate/rotate PK/KEK/db key material and signed metadata.",
        description="Generate/rotate PK/KEK/db key material and signed metadata.",
    )
    _add_keys_args(p_keys)
    p_keys.set_defaults(func=cmd_keys)

    p_sign_chain = subparsers.add_parser(
        "sign-chain",
        help="Sign the UKI + systemd-boot and verify.",
        description="Sign the UKI + systemd-boot and verify.",
    )
    _add_sign_chain_args(p_sign_chain)
    p_sign_chain.set_defaults(func=cmd_sign_chain)

    p_enroll = subparsers.add_parser(
        "enroll",
        help="Emit MOK/custom-key enrolment material + instructions.",
        description="Emit MOK/custom-key enrolment material + instructions.",
    )
    _add_enroll_args(p_enroll)
    p_enroll.set_defaults(func=cmd_enroll)

    p_lint = subparsers.add_parser(
        "lint",
        help="Lint the ESP: every EFI binary must be signed by a key in db.",
        description="Lint the ESP: every EFI binary must be signed by a key in db.",
    )
    _add_lint_args(p_lint)
    p_lint.set_defaults(func=cmd_lint)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "func", None)
    if handler is None:  # pragma: no cover - argparse `required` guards this.
        parser.error(f"unknown command: {args.command!r}")

    try:
        return handler(args)
    except NotYetImplemented as exc:
        print(f"pengyos-secureboot-enroll: error: {exc}", file=sys.stderr)
        return EXIT_NOT_YET_IMPLEMENTED
