"""Command-line interface for ``pengyos-podman``.

Subcommands:

* ``gen`` — emit a rootless podman ``systemd --user`` Quadlet
  (``.container`` unit) for a ``service.yaml`` into an output directory.
* ``validate`` — validate a ``service.yaml`` and report clean errors.
* ``render`` — print the parsed service as JSON (``--json``).
* ``check`` — report on the rootless-podman host prerequisites (subuid / subgid
  ranges + a ``storage.conf``). Report-only: it never runs podman/systemd and
  never modifies the host.

podman/systemd are never executed here; the CLI only parses, validates, emits
text and probes the host read-only. See spec.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

from . import __version__
from .hostcheck import check_host, render_report
from .loader import ServiceLoadError, load_service
from .render import render_json, render_unit, unit_filename
from .validate import (
    PolicyError,
    SchemaError,
    policy_warnings,
    validate_policy,
    validate_strict,
)


def _print_err(message: str) -> None:
    print(f"pengyos-podman: error: {message}", file=sys.stderr)


def _print_validation_errors(exc: SchemaError) -> None:
    """Print structural schema errors (exit 1)."""

    for error in exc.errors:
        print(f"pengyos-podman: validation error: {error}", file=sys.stderr)


def _print_policy_errors(exc: PolicyError) -> None:
    """Print semantic policy errors (exit 1). Distinct prefix from schema errors."""

    for error in exc.errors:
        print(f"pengyos-podman: policy error: {error}", file=sys.stderr)


def _print_policy_warnings(warnings: list[str]) -> None:
    """Print advisory policy warnings (do not affect the exit code).

    Volume-persistence violations are advisory by default: they are reported so
    the user is aware, but they do not make ``validate``/``gen`` fail (unless
    ``--strict`` is passed, in which case they are fatal instead).
    """

    for warning in warnings:
        print(f"pengyos-podman: policy warning: {warning}", file=sys.stderr)


def cmd_gen(args: argparse.Namespace) -> int:
    """Write the generated unit(s) into ``--out``."""

    try:
        service = load_service(args.service)
    except ServiceLoadError as exc:
        _print_err(str(exc))
        return 2

    try:
        validate_strict(service)  # raises SchemaError on invalid schema
    except SchemaError as exc:
        _print_validation_errors(exc)
        return 1

    # Semantic policy (item 4): reachability is always fatal; volume-persistence
    # is advisory by default and only fatal under --strict.
    try:
        validate_policy(service, strict=args.strict)
    except PolicyError as exc:
        _print_policy_errors(exc)
        return 1

    # Advisory (non-fatal) policy violations: volume-persistence when not
    # strict. Reported to stderr but do not stop the unit from being emitted.
    _print_policy_warnings(policy_warnings(service))

    body = render_unit(service)
    filename = unit_filename(service)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, filename)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(body)

    print(f"wrote {dest}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a ``service.yaml``; exit non-zero with clean errors if invalid."""

    try:
        service = load_service(args.service)
    except ServiceLoadError as exc:
        _print_err(str(exc))
        return 2

    try:
        validate_strict(service)
    except SchemaError as exc:
        _print_validation_errors(exc)
        return 1

    # Semantic policy (item 4): reachability is always fatal; volume-persistence
    # is advisory by default and only fatal under --strict.
    try:
        validate_policy(service, strict=args.strict)
    except PolicyError as exc:
        _print_policy_errors(exc)
        return 1

    # Advisory (non-fatal) policy violations: volume-persistence when not strict.
    _print_policy_warnings(policy_warnings(service))

    print(f"OK: {args.service} is a valid service definition")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Print the parsed service as JSON."""

    try:
        service = load_service(args.service)
    except ServiceLoadError as exc:
        _print_err(str(exc))
        return 2

    if args.json:
        sys.stdout.write(render_json(service))
    else:
        _print_err("render requires --json")
        return 2

    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Report on the rootless-podman host prerequisites (report-only).

    Probes the host for the subuid / subgid ranges and a storage.conf, then
    prints an aligned report. It never runs podman/systemd and never modifies
    the host, so it exits ``0`` even when something is missing (a missing
    prerequisite is a reported finding, not an error to fix in code).
    """

    sys.stdout.write(render_report(check_host()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-podman",
        description="Generate rootless podman systemd --user units from service.yaml",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_gen = subparsers.add_parser(
        "gen", help="emit a rootless podman Quadlet (.container) unit"
    )
    p_gen.add_argument("--service", required=True, help="path to service.yaml")
    p_gen.add_argument(
        "--out", required=True, help="output directory for the generated unit(s)"
    )
    p_gen.add_argument(
        "--strict",
        action="store_true",
        help="treat policy violations (volume persistence) as fatal, not advisory",
    )
    p_gen.set_defaults(func=cmd_gen)

    p_validate = subparsers.add_parser(
        "validate", help="validate a service.yaml against the schema"
    )
    p_validate.add_argument("--service", required=True, help="path to service.yaml")
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="treat policy violations (volume persistence) as fatal, not advisory",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_render = subparsers.add_parser(
        "render", help="print the parsed service as JSON"
    )
    p_render.add_argument("--service", required=True, help="path to service.yaml")
    p_render.add_argument(
        "--json", action="store_true", help="emit JSON (required)"
    )
    p_render.set_defaults(func=cmd_render)

    p_check = subparsers.add_parser(
        "check",
        help="report on rootless-podman host prerequisites (report only)",
    )
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        _print_err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
