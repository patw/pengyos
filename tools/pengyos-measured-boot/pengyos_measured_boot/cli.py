"""Command-line interface for pengyos-measured-boot.

The CLI is the only public surface of the tool. It is fully wired for
argument parsing and dispatch; subcommand backends are implemented in the
modules referenced by ``run``/``verify`` (see :mod:`pengyos_measured_boot.swtpm`,
:mod:`pengyos_measured_boot.pcrs`, and :mod:`pengyos_measured_boot.policy`).

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .boot import run_measured_boot
from .errors import UsageError, PengyOSMeasuredBootError
from .pcrs import parse_pcrs_file
from .policy import Policy, load_policy
from .swtpm import SwtpmBootConfig


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    Kept as a separate function so tests can exercise parsing without
    invoking the real backends.
    """

    parser = argparse.ArgumentParser(
        prog="pengyos-measured-boot",
        description=(
            "Boot under a software TPM (swtpm), read PCRs, and verify them "
            "against an expected event-log policy."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    run = sub.add_parser(
        "run",
        help="boot an image, read PCRs, and compare to expected values",
        description=(
            "Boot an image under a (mockable) swtpm, read the PCR values, "
            "and compare them to the values required by --expect."
        ),
    )
    run.add_argument(
        "--image",
        required=True,
        metavar="IMG",
        help="path to the boot image to measure",
    )
    run.add_argument(
        "--expect",
        action="append",
        required=True,
        metavar="PCR=VALUE",
        help=(
            "required PCR value, e.g. pcr7=0xdeadbeef. May be repeated. "
            "VALUE is parsed as hex (0x-prefixed) or decimal."
        ),
    )
    run.add_argument(
        "--json",
        dest="json_out",
        metavar="OUT",
        help="write the result as JSON to OUT instead of stdout",
    )

    verify = sub.add_parser(
        "verify",
        help="verify committed PCRs against a policy file",
        description=(
            "Load committed PCR values from --pcrs and an expected-value "
            "policy from --policy, then verify the PCRs against the policy."
        ),
    )
    verify.add_argument(
        "--pcrs",
        required=True,
        metavar="PCRS.json",
        help="path to a committed PCR values file (JSON)",
    )
    verify.add_argument(
        "--policy",
        required=True,
        metavar="policy.toml",
        help="path to the expected-value policy (TOML)",
    )
    verify.add_argument(
        "--json",
        dest="json_out",
        metavar="OUT",
        help="write the result as JSON to OUT instead of stdout",
    )

    return parser


def _parse_expect(pairs: Sequence[str]) -> dict[int, int]:
    """Parse ``pcrN=VALUE`` strings into ``{index: value}``.

    ``VALUE`` may be hex (``0x``-prefixed) or decimal. Raises
    :class:`UsageError` on malformed input so the caller can report it.
    """

    result: dict[int, int] = {}
    for pair in pairs:
        if "=" not in pair:
            raise UsageError(f"malformed --expect {pair!r}: expected PCR=VALUE")
        name, _, raw = pair.partition("=")
        key = name.strip().lower()
        if not key.startswith("pcr"):
            raise UsageError(
                f"malformed --expect {pair!r}: key must start with 'pcr'"
            )
        try:
            index = int(key[len("pcr"):])
        except ValueError as exc:
            raise UsageError(
                f"malformed --expect {pair!r}: PCR index is not an integer"
            ) from exc
        try:
            value = int(raw.strip(), 0)
        except ValueError as exc:
            raise UsageError(
                f"malformed --expect {pair!r}: VALUE is not hex/decimal"
            ) from exc
        if index < 0:
            raise UsageError(
                f"malformed --expect {pair!r}: PCR index must be >= 0"
            )
        result[index] = value
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 = success)."""

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            _parse_expect(args.expect)  # validate usage up front
            return _cmd_run(args)
        if args.command == "verify":
            return _cmd_verify(args)
    except PengyOSMeasuredBootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


def _emit_json(result: dict[str, Any], out: str | None) -> None:
    """Serialise ``result`` as JSON to ``out`` (a path) or stdout."""

    text = json.dumps(result, indent=2, sort_keys=True)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _cmd_run(args: argparse.Namespace) -> int:
    """Boot an image, read PCRs, and compare them to ``--expect``.

    Boots the image under a (mockable) swtpm, reads the PCR values, parses them,
    and verifies them against the values required by ``--expect``. On success the
    result is emitted as JSON; on a mismatch a :class:`PolicyMismatch` names every
    mismatching PCR (mapped to exit code 2 by :func:`main`).
    """

    expect = _parse_expect(args.expect)
    config = SwtpmBootConfig(image=args.image)
    result = run_measured_boot(config, expect)

    _emit_json(result, args.json_out)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Verify committed PCRs against an expected-value policy.

    Loads the policy from ``--policy`` and the PCRs from ``--pcrs``, then
    asserts the PCRs satisfy the policy. On success the check result is emitted
    as JSON; on failure a :class:`PolicyMismatch` names every mismatching PCR
    (mapped to exit code 2 by :func:`main`).
    """

    policy = load_policy(args.policy)
    pcrs = parse_pcrs_file(args.pcrs)
    policy.verify(pcrs)  # raises PolicyMismatch on any mismatch

    result = {
        "ok": True,
        "policy": str(args.policy),
        "pcrs": str(args.pcrs),
        "required": sorted(policy.indices()),
    }
    _emit_json(result, args.json_out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
