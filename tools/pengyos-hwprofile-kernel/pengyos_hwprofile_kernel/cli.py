"""Command-line entry point for ``pengyos-hwprofile-kernel``.

``select`` / ``exclude`` / ``explain`` / ``diff`` are all wired here.

Every handler returns a process exit code (see :func:`main`); errors are
reported to ``stderr`` with a non-zero code and never as a traceback.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import exclude as exclude_mod
from . import explain as explain_mod
from . import profiles
from . import select as select_mod
from . import diff as diff_mod

# The tool ships a bundled ``fragments/`` directory (every fragment its mapping
# references). ``explain`` / ``diff`` default ``--fragments`` here so their
# spec signature (``explain --profile ... --symbol ...`` /
# ``diff --a ... --b ...``) works out of the box; ``--fragments`` overrides it.
_BUNDLED_FRAGMENTS = str(Path(__file__).resolve().parents[1] / "fragments")


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser.

    ``select`` / ``exclude`` / ``explain`` / ``diff`` are all wired up.
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-hwprofile-kernel",
        description=(
            "Turn a pengyos-hwprofile hardware profile into a minimal kernel "
            "fragment set + defconfig fragment for PengyOS."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print the pengyos-hwprofile-kernel version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # select.
    sel = sub.add_parser(
        "select",
        help="map a profile's device classes to fragments and emit a defconfig",
        description=(
            "Read a pengyos-hwprofile profile, map its device classes to kernel "
            "fragments, merge them (later-wins) into a deterministic defconfig "
            "fragment, and print it. Conflict warnings and unknown-device "
            "warnings go to stderr; the defconfig goes to stdout (or --out)."
        ),
    )
    sel.add_argument(
        "--profile",
        "-p",
        required=True,
        metavar="PROFILE.toml",
        help="the pengyos-hwprofile profile to read",
    )
    sel.add_argument(
        "--fragments",
        "-f",
        required=True,
        metavar="DIR",
        help="directory of *.fragment files to compose from",
    )
    sel.add_argument(
        "--out",
        "-o",
        default=None,
        metavar="fragment.config",
        help="write the defconfig here (default: stdout)",
    )
    sel.set_defaults(func=_cmd_select)

    # exclude — report the drivers this machine intentionally leaves out.
    exc = sub.add_parser(
        "exclude",
        help="report the kernel symbols this machine intentionally omits",
        description=(
            "Read a pengyos-hwprofile profile, map its device classes to "
            "kernel fragments and merge them (the set this machine carries), "
            "then report the symbols the generic/full kernel has that this "
            "machine does not — the drivers intentionally left out. The report "
            "goes to stdout (or --out); a missing fragment is a hard error."
        ),
    )
    exc.add_argument(
        "--profile",
        "-p",
        required=True,
        metavar="PROFILE.toml",
        help="the pengyos-hwprofile profile to read",
    )
    exc.add_argument(
        "--fragments",
        "-f",
        required=True,
        metavar="DIR",
        help="directory of *.fragment files to compose from",
    )
    exc.add_argument(
        "--out",
        "-o",
        default=None,
        metavar="excluded_drivers.txt",
        help="write the excluded-drivers report here (default: stdout)",
    )
    exc.set_defaults(func=_cmd_exclude)

    # explain — trace a symbol back to the device that pulled it in.
    expl = sub.add_parser(
        "explain",
        help="trace a CONFIG_ symbol to the device that pulled it in",
        description=(
            "Read a pengyos-hwprofile profile, map its devices to kernel "
            "fragments, and trace a CONFIG_ symbol back to the device(s) that "
            "pulled the carrying fragment into this machine's image (and the "
            "symbol's final merged value). The report goes to stdout (or "
            "--out); a missing fragment is a hard error."
        ),
    )
    expl.add_argument(
        "--profile",
        "-p",
        required=True,
        metavar="PROFILE.toml",
        help="the pengyos-hwprofile profile to read",
    )
    expl.add_argument(
        "--fragments",
        "-f",
        default=_BUNDLED_FRAGMENTS,
        metavar="DIR",
        help=(
            "directory of *.fragment files to compose from "
            f"(default: the bundled {_BUNDLED_FRAGMENTS})"
        ),
    )
    expl.add_argument(
        "--symbol",
        "-s",
        required=True,
        metavar="CONFIG_SYMBOL",
        help="the CONFIG_ symbol to explain",
    )
    expl.add_argument(
        "--out",
        "-o",
        default=None,
        metavar="explain.txt",
        help="write the report here (default: stdout)",
    )
    expl.set_defaults(func=_cmd_explain)

    # diff — fragment delta between two profiles = hardware-change detection.
    dif = sub.add_parser(
        "diff",
        help="report the fragment delta between two profiles",
        description=(
            "Read two pengyos-hwprofile profiles and report the fragment delta "
            "between them — the fragments the second profile carries that the "
            "first does not (added) and the fragments the first carried that the "
            "second does not (removed). The delta is hardware-change detection. "
            "The report goes to stdout (or --out); pass --json for machine "
            "readable output."
        ),
    )
    dif.add_argument(
        "--a",
        "-a",
        required=True,
        metavar="PROFILE.toml",
        help="the first (baseline) profile",
    )
    dif.add_argument(
        "--b",
        "-b",
        required=True,
        metavar="PROFILE.toml",
        help="the second (target) profile",
    )
    dif.add_argument(
        "--fragments",
        "-f",
        default=_BUNDLED_FRAGMENTS,
        metavar="DIR",
        help=(
            "directory of *.fragment files to compose from "
            f"(default: the bundled {_BUNDLED_FRAGMENTS})"
        ),
    )
    dif.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON",
    )
    dif.add_argument(
        "--out",
        "-o",
        default=None,
        metavar="diff.txt",
        help="write the report here (default: stdout)",
    )
    dif.set_defaults(func=_cmd_diff)

    return parser


def _cmd_select(args: argparse.Namespace) -> int:
    """Handle the ``select`` subcommand.

    Exit codes: 0 (defconfig emitted), 1 (profile could not be loaded), 2 (a
    selected fragment is missing from ``--fragments``).
    """

    # Load + validate the profile (raises ProfileError on any problem).
    try:
        document = profiles.load_profile(args.profile)
    except profiles.ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Map devices to fragments and compose the defconfig.
    report = select_mod.compose(document, args.fragments)

    # A selected fragment absent from the fragments dir is a hard error.
    missing = select_mod.render_missing(report)
    if missing:
        print(missing, file=sys.stderr)
        return 2

    # Conflict warnings (later-wins overrides) go to stderr so a downstream
    # consumer of stdout gets a clean CONFIG set.
    warnings = select_mod.render_warnings(report)
    if warnings:
        print(warnings, file=sys.stderr)

    # Unknown devices (matched no rule) are reported, never dropped.
    if report.unknown:
        names = ", ".join(
            u.device.get("interface")
            or u.device.get("vendor")
            or u.section
            for u in report.unknown
        )
        print(
            f"warning: {len(report.unknown)} unmapped device(s) "
            f"(no fragment): {names}",
            file=sys.stderr,
        )

    defconfig = select_mod.render_defconfig(report)
    if args.out:
        Path(args.out).write_text(
            defconfig, encoding="utf-8"
        )
    elif defconfig:
        print(defconfig, end="")

    return 0


def _cmd_exclude(args: argparse.Namespace) -> int:
    """Handle the ``exclude`` subcommand.

    Exit codes: 0 (report produced), 1 (profile could not be loaded), 2 (a
    selected fragment is missing from ``--fragments`` or a usage error).
    """

    # Load + validate the profile (raises ProfileError on any problem).
    try:
        document = profiles.load_profile(args.profile)
    except profiles.ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Compute the excluded-drivers report (composes the profile first).
    report = exclude_mod.compute_excluded(document, args.fragments)

    # A selected fragment absent from the fragments dir is a hard error, the
    # same as `select`: without the included set the report is meaningless.
    if not report.ok:
        print(
            f"error: profile references missing fragment(s): "
            f"{', '.join(report.missing)} (not found in the fragments directory)",
            file=sys.stderr,
        )
        return 2

    # Unknown-device warnings (reported, never dropped) go to stderr; the
    # report itself goes to stdout (or --out).
    if report.unknown:
        names = ", ".join(
            u.device.get("interface")
            or u.device.get("vendor")
            or u.section
            for u in report.unknown
        )
        print(
            f"warning: {len(report.unknown)} unmapped device(s) "
            f"(no fragment): {names}",
            file=sys.stderr,
        )

    out = exclude_mod.render_excluded(report)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    else:
        print(out, end="")

    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    """Handle the ``explain`` subcommand.

    Exit codes: 0 (report produced), 1 (profile could not be loaded), 2 (a
    selected fragment is missing from ``--fragments`` or a usage error).
    """

    # Load + validate the profile (raises ProfileError on any problem).
    try:
        document = profiles.load_profile(args.profile)
    except profiles.ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Trace the symbol back to the device(s) that pulled it in.
    report = explain_mod.explain(document, args.fragments, args.symbol)

    # A selected fragment absent from the fragments dir is a hard error, the
    # same as `select` / `exclude`: without the library the trace is meaningless.
    if report.missing:
        print(
            f"error: profile references missing fragment(s): "
            f"{', '.join(report.missing)} (not found in the fragments directory)",
            file=sys.stderr,
        )
        return 2

    # Unknown-device warnings (reported, never dropped) go to stderr.
    if report.unknown:
        names = ", ".join(
            u.device.get("interface")
            or u.device.get("vendor")
            or u.section
            for u in report.unknown
        )
        print(
            f"warning: {len(report.unknown)} unmapped device(s) "
            f"(no fragment): {names}",
            file=sys.stderr,
        )

    out = explain_mod.render_explain(report)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    else:
        print(out, end="")

    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Handle the ``diff`` subcommand.

    Exit codes: 0 (report produced), 1 (a profile could not be loaded), 2 (a
    selected fragment is missing from ``--fragments`` or a usage error).
    """

    # Load + validate both profiles (raises ProfileError on any problem).
    try:
        document_a = profiles.load_profile(args.a)
        document_b = profiles.load_profile(args.b)
    except profiles.ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Compute the fragment delta (hardware-change detection).
    report = diff_mod.diff(
        document_a,
        document_b,
        args.fragments,
        profile_a=args.a,
        profile_b=args.b,
    )

    # A selected fragment absent from the fragments dir is a hard error, the
    # same as the other subcommands: without the library the delta is partial.
    if report.missing:
        print(
            f"error: a profile references a missing fragment(s): "
            f"{', '.join(report.missing)} (not found in the fragments directory)",
            file=sys.stderr,
        )
        return 2

    # Unknown-device warnings (reported, never dropped) go to stderr.
    if report.unknown:
        names = ", ".join(
            u.device.get("interface")
            or u.device.get("vendor")
            or u.section
            for u in report.unknown
        )
        print(
            f"warning: {len(report.unknown)} unmapped device(s) "
            f"(no fragment): {names}",
            file=sys.stderr,
        )

    if args.json:
        # json.dumps emits no trailing newline; normalise below.
        out = json.dumps(
            {
                "a": report.a,
                "b": report.b,
                "added": [
                    {
                        "fragment": d.fragment,
                        "kind": d.kind,
                        "symbols": list(d.symbols),
                    }
                    for d in report.added
                ],
                "removed": [
                    {
                        "fragment": d.fragment,
                        "kind": d.kind,
                        "symbols": list(d.symbols),
                    }
                    for d in report.removed
                ],
                "a_fragments": list(report.a_fragments),
                "b_fragments": list(report.b_fragments),
                "changed": report.changed,
            },
            indent=2,
            sort_keys=True,
        )
    else:
        text = diff_mod.render_diff(report)
        # render_diff ends with a newline; strip it so we add exactly one.
        out = text[:-1] if text.endswith("\n") else text

    payload = out + "\n"
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code.

    ``main`` never raises ``SystemExit``: argparse's ``--help`` / ``--version``
    and usage errors are normalised to a return code so tests can invoke the CLI
    in-process (``python -m`` and the console script wrap this with
    ``sys.exit(main(...))``).
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help / --version / usage errors
        # argparse has already printed; normalize the exit code.
        return exc.code if isinstance(exc.code, int) else 0

    handler = getattr(args, "func", None)
    if handler is None:
        # No subcommand: print help and return 0 (matches the hwprofile family
        # convention; the sibling kernel-fragments returns 2 here).
        parser.print_help(sys.stderr)
        return 0
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
