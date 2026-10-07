"""Command-line interface for ``pengyos-desktop-session``.

The argument surface is defined and tested here so that later subtasks build on
a *stable, documented* interface (see ``spec.md``).

* ``--version`` — prints the version and exits 0.
* ``emit --out DIR [--json]`` — emit the COSMIC session definition (a greetd
  config + cosmic-greeter session entry + the cosmic-session systemd
  unit/launcher + environment) under ``--out`` deterministically
 .
* ``check --log FILE [--json]`` — check the VM-testable session facts over a
  boot log: present/absent patterns for cosmic-comp, a wayland socket,
  cosmic-session/panel, pipewire/wireplumber, and portals; the engine fails if
  a present pattern is missing.
* ``check --ssh HOST [--json]`` — collect the same facts over ssh from a
  running target (opt-in, ``PENGYOS_IT=1``; skips honestly when no host is
  given).

``--version`` and ``emit`` are wired up. The ``check --log`` handler (item 4)
runs the check engine; the ``check --ssh`` handler (item 5) runs the opt-in
ssh probe (``PENGYOS_IT=1``), reporting a SKIP honestly when the probe cannot
run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import check as check_engine
from .session import write_session

# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # success
EXIT_FAIL: int = 1  # an operation ran but reported a failure
EXIT_USAGE: int = 2  # argparse's default for bad usage
EXIT_NOT_IMPLEMENTED: int = 3  # (reserved) subcommand recognised but not implemented
EXIT_SKIP: int = 4  # the check could not run (e.g. the ssh probe was skipped)


def _add_out_flag(parser: argparse.ArgumentParser) -> None:
    """Register the shared ``--out`` flag (the target overlay dir to emit into)."""
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Target overlay directory to write the emitted session files into.",
    )


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    """Register the shared ``--json`` flag (emit machine-readable output)."""
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON instead of the human-readable form.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-desktop-session",
        description=(
            "The COSMIC session + a VM-testable smoke matrix (US-DSK-1) for "
            "PengyOS Desktop: emit a deterministic greetd/cosmic-greeter/"
            "cosmic-session definition, and check the session facts QEMU+OVMF "
            "can actually verify."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )

    sub = parser.add_subparsers(dest="command", metavar="{emit,check}")

    emit_p = sub.add_parser(
        "emit",
        help="Emit the deterministic COSMIC session definition into --out.",
        description=(
            "Emit the COSMIC session definition — a greetd config, a "
            "cosmic-greeter session entry, the cosmic-session systemd unit "
            "and launcher, and the session environment — under --out, "
            "deterministically (stable ordering + content)."
        ),
    )
    _add_out_flag(emit_p)
    _add_json_flag(emit_p)

    check_p = sub.add_parser(
        "check",
        help="Check the VM-testable session facts over a boot log or ssh.",
        description=(
            "Check the VM-testable session facts (cosmic-comp started, a "
            "wayland socket exists, cosmic-session/panel up, pipewire/"
            "wireplumber running, portals responding) over a boot log "
            "(``--log``) or an opt-in ssh probe (``--ssh``, PENGYOS_IT=1). The "
            "engine fails if a present pattern is missing."
        ),
    )
    # ``--log`` and ``--ssh`` are mutually exclusive, and exactly one is
    # required (neither given is a usage error).
    src = check_p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--log",
        metavar="FILE",
        help="Boot log to check the session facts over.",
    )
    src.add_argument(
        "--ssh",
        metavar="HOST",
        help="Collect the same facts over ssh from a running target (opt-in, PENGYOS_IT=1).",
    )
    _add_json_flag(check_p)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point. Returns a process exit code (never raises on good use).

    ``argv`` is accepted for testability and defaults to ``sys.argv[1:]``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pengyos-desktop-session {__version__}")
        return EXIT_OK

    if not args.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    if args.command == "emit":
        return _run_emit(args)

    if args.command == "check":
        return _run_check(args)

    # Unreachable for a parser we built, but never fall through silently.
    sys.stderr.write(
        f"pengyos-desktop-session: unknown subcommand '{args.command}'\n"
    )
    return EXIT_USAGE


def _run_emit(args: argparse.Namespace) -> int:
    """Implement the ``emit`` subcommand.

    Writes the emitted COSMIC session definition under ``--out`` — the greetd
    config, cosmic-greeter session entry, systemd unit/launcher, and
    environment — in canonical (stable) order, then prints what was written
    (human-readable, or JSON with ``--json``). Deterministic: re-emitting writes
    byte-for-byte identical content.
    """
    written = write_session(args.out)
    out = Path(args.out)

    if args.json:
        payload = {"out": str(out), "files": written}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK

    print(f"emitted COSMIC session to {out}")
    for name in written:
        print(f"  {name}")
    return EXIT_OK


def _run_check(args: argparse.Namespace) -> int:
    """Implement the ``check`` subcommand.

    ``--log FILE`` checks the VM-testable session facts over a boot log with the
    engine in :mod:`pengyos_desktop_session.check` — the engine fails if a
    present pattern is missing (no vacuous pass). ``--ssh HOST`` (item 5) runs
    the opt-in ssh probe: it collects the target's session state over ssh and
    checks it with the same engine, reporting a SKIP (``EXIT_SKIP``) honestly
    when the probe cannot run (opt-in not enabled, no host, no output, ssh
    failure).
    """
    if args.ssh is not None:
        # item 5: the opt-in ssh probe.
        outcome = check_engine.check_ssh(args.ssh)
    else:
        log_path = Path(args.log)
        try:
            log_text = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(
                f"pengyos-desktop-session: cannot read boot log '{log_path}': {exc}\n"
            )
            return EXIT_USAGE

        outcome = check_engine.check_boot_log(log_text, source="boot log")

    if args.json:
        print(json.dumps(check_engine.outcome_to_dict(outcome), indent=2, sort_keys=True))
    else:
        print(check_engine.render_findings(outcome))

    if outcome.skip:
        return EXIT_SKIP
    return EXIT_OK if outcome.ok else EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
