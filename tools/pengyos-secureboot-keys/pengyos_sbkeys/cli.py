"""``pengyos-sbkeys`` command-line interface.

This module is the dispatcher for the ``pengyos-sbkeys`` CLI. It wires up the
three subcommands documented in ``spec.md`` (``init``, ``enroll-commands`` and
``sign``), their argument parsers and the routing table.

``init``, ``enroll-commands`` and ``sign`` (command generation) are all
implemented. Before ``enroll-commands`` and ``sign``
operate on existing key material they fail closed via
:class:`pengyos_sbkeys.security.PermCheckError` if a private key is found with
more permissive bits than the policy allows (mapped to exit 1). A
behaviour that is not implemented yet -- currently ``sign --execute``
(actually running ``sbsign``/``sbverify``) -- raises :class:`NotYetImplemented`,
which :func:`main` turns into a clean, non-zero exit code and a stderr message.
The plumbing is real and tested; the feature work is tracked separately.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .enroll import EnrollmentError, enroll_commands
from .generate import GenerateError, generate_all
from .keys import KEY_FILES, KeyPerms, perms_for
from .security import PermCheckError
from .sign import SignError, sign_commands

__all__ = [
    "NotYetImplemented",
    "build_parser",
    "cmd_init",
    "cmd_enroll",
    "cmd_sign",
    "generate_all",
    "main",
]

# Exit code used when a command's implementation is not present yet. Kept
# distinct from argparse's usage error (2) so tests can tell them apart.
EXIT_NOT_YET_IMPLEMENTED = 3


class NotYetImplemented(RuntimeError):
    """A command handler whose implementation is a future, tracked subtask."""


def _add_init_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Output directory for the generated PK/KEK/db key material.",
    )


def _add_enroll_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--keys",
        required=True,
        metavar="DIR",
        help="Directory containing the enrolled key material.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of shell commands.",
    )


def _add_sign_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "uki",
        metavar="UKI",
        help="Path to the Unified Kernel Image to sign.",
    )
    parser.add_argument(
        "--key",
        required=True,
        metavar="KEY",
        help="Path to the private key (PEM) used to sign.",
    )
    parser.add_argument(
        "--cert",
        required=False,
        default=None,
        metavar="CERT",
        help=(
            "Path to the signing certificate (X.509 PEM). Defaults to the "
            "certificate next to the key (KEY with a .cert suffix)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of shell commands.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually run sbsign/sbverify (not yet implemented; default is to "
            "only print the commands)."
        ),
    )


def cmd_init(args: argparse.Namespace) -> int:
    """Create ``--out`` and materialize the key set with the perms policy.

    Real PK/KEK/db key material is generated with ``openssl`` via
    :func:`pengyos_sbkeys.generate.generate_all` (EC P-256 private keys and
    self-signed X.509 certificates). Every file is then ``chmod``-ed to its
    policy bits (private keys ``0600``, certificates ``0644``) and the key
    directory is locked to ``0700`` when we create it. The command prints only
    file names and their permission bits -- never the key material itself.
    """
    out_dir = Path(args.out)

    if out_dir.exists() and not out_dir.is_dir():
        print(
            f"pengyos-sbkeys: error: {args.out!r} exists and is not a directory",
            file=sys.stderr,
        )
        return 1

    created_dir = False
    if not out_dir.exists():
        out_dir.mkdir(parents=True)
        os.chmod(out_dir, KeyPerms.DIR)
        created_dir = True

    # Real key material: generate the PK/KEK/db private keys and their
    # self-signed certificates with openssl (see pengyos_sbkeys.generate).
    try:
        generate_all(out_dir)
    except GenerateError as exc:
        print(f"pengyos-sbkeys: error: {exc}", file=sys.stderr)
        return 1

    # chmod sets the exact mode regardless of the process umask, so the policy
    # is enforced even on a host with a permissive umask.
    for key_file in KEY_FILES:
        os.chmod(out_dir / key_file.name, perms_for(key_file))

    for key_file in KEY_FILES:
        print(
            f"{key_file.name}  {key_file.description}  ({oct(perms_for(key_file))})"
        )
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    """Print the commands that enroll PK/KEK/db into firmware.

    Reads only the *public* certificates under ``--keys`` (the private keys are
    never opened) and prints either a shell script or a JSON summary. The
    output never contains key material.
    """
    try:
        output = enroll_commands(Path(args.keys), as_json=args.json)
    except (EnrollmentError, PermCheckError) as exc:
        print(f"pengyos-sbkeys: error: {exc}", file=sys.stderr)
        return 1

    print(output, end="")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    """Generate the sbsign/sbverify commands that sign a UKI and verify it.

    The commands are printed (a ``sh`` script by default, or a JSON summary with
    ``--json``). The private key is only referenced by path -- never opened -- so
    key material never appears in the output. ``--execute`` (actually running the
    tools) is a future subtask and raises :class:`NotYetImplemented`.
    """
    if args.execute:
        raise NotYetImplemented(
            "sign --execute: actually running sbsign/sbverify is a future "
            "subtask"
        )

    try:
        output = sign_commands(
            Path(args.uki),
            Path(args.key),
            cert=Path(args.cert) if args.cert else None,
            as_json=args.json,
        )
    except (SignError, PermCheckError) as exc:
        print(f"pengyos-sbkeys: error: {exc}", file=sys.stderr)
        return 1

    print(output, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for ``pengyos-sbkeys``."""
    parser = argparse.ArgumentParser(
        prog="pengyos-sbkeys",
        description=(
            "Manage PengyOS Secure Boot key material (PK/KEK/db) and generate "
            "the signing commands for a UKI."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    p_init = subparsers.add_parser(
        "init",
        help="Generate PK/KEK/db key material under an output directory.",
        description="Generate PK/KEK/db key material under an output directory.",
    )
    _add_init_args(p_init)
    p_init.set_defaults(func=cmd_init)

    p_enroll = subparsers.add_parser(
        "enroll-commands",
        help="Print the commands that enroll PK/KEK/db into firmware.",
        description="Print the commands that enroll PK/KEK/db into firmware.",
    )
    _add_enroll_args(p_enroll)
    p_enroll.set_defaults(func=cmd_enroll)

    p_sign = subparsers.add_parser(
        "sign",
        help="Sign a Unified Kernel Image (UKI) with sbsign.",
        description="Sign a Unified Kernel Image (UKI) with sbsign.",
    )
    _add_sign_args(p_sign)
    p_sign.set_defaults(func=cmd_sign)

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
        print(f"pengyos-sbkeys: error: {exc}", file=sys.stderr)
        return EXIT_NOT_YET_IMPLEMENTED
