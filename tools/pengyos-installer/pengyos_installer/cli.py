"""Command-line interface for ``pengyos-installer``.

Interface (spec.md):

    pengyos-installer plan    --target /dev/sdX [--esp-size 512M]
                              [--hostname H] [--rootfs-src DIR|TAR] [--json]
    pengyos-installer install --target /dev/sdX [ ...same... ] --yes

* ``plan`` computes the explicit, ordered install plan and prints it
  (deterministic; shell-quotable argv per step; ``--json`` for the
  machine-readable form). It is *pure*: it never writes to any disk.
* ``install`` executes the plan through an **injectable command runner**
  (see :mod:`pengyos_installer.install`); it refuses to run without
  ``--yes`` and re-checks the target every run.

``plan`` is implemented here (delegating to :mod:`pengyos_installer.plan`);
``install`` executes the plan via :mod:`pengyos_installer.install`, running
every command through an injectable runner (defaulting to a
:class:`~pengyos_installer.install.SubprocessRunner`).
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from pengyos_installer import __version__
from pengyos_installer import install as install_mod
from pengyos_installer.plan import (
    ConfigError,
    MIN_TARGET_SIZE_BYTES,
    InstallPlan,
    WORK_DIR,
    build_plan,
    render_plan_text,
)

#: Default ESP size (spec.md: ``--esp-size 512M``).
DEFAULT_ESP_SIZE = "512M"

#: The bootloader id used for the removable (fallback) EFI boot path.
BOOTLOADER_ID = "PengyOS"

# ``MIN_TARGET_SIZE_BYTES`` is re-exported from :mod:`pengyos_installer.plan`
# so callers/tests that import it from the CLI module keep working.


class ExitCode:
    """Process exit codes.

    ``NOT_IMPLEMENTED`` (3) is deliberately not ``USAGE`` (2, reserved for
    argparse failures) nor a plain error (1): it lets scripts and tests tell
    "the tool ran, but this phase of the build is not implemented yet" apart
    from "the tool ran and something went wrong".
    """

    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_IMPLEMENTED = 3


class NotYetImplementedError(Exception):
    """Raised by a subcommand handler that the scaffold has not filled in yet."""

    def __init__(self, command: str, task: str) -> None:
        super().__init__(f"'{command}' is not implemented yet: {task}")
        self.command = command
        self.task = task


Handler = Callable[[argparse.Namespace], int]


def _add_target_options(parser: argparse.ArgumentParser) -> None:
    """Options shared by ``plan`` and ``install`` (spec.md: "[...same...]")."""
    parser.add_argument(
        "--target",
        required=True,
        help="Target block device to install to, e.g. /dev/sdb. "
        "The device is sanity-checked before anything else runs.",
    )
    parser.add_argument(
        "--esp-size",
        default=DEFAULT_ESP_SIZE,
        help=f"Size of the EFI System Partition (default: {DEFAULT_ESP_SIZE})",
    )
    parser.add_argument(
        "--hostname",
        default=None,
        help="Hostname to configure on the installed system "
        "(default: the live system's hostname)",
    )
    parser.add_argument(
        "--rootfs-src",
        default=None,
        help="Root filesystem source: a directory or a tarball to install from "
        "(default: the running '/')",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable plan (JSON) instead of the numbered list",
    )


def run_plan(ns: argparse.Namespace) -> int:
    """The ``plan`` handler (spec.md: pure, deterministic, ``--json``).

    Computes the ordered install plan for ``ns.target``. If the target fails
    sanity (not a block device, is the live device, or is below the 4 GiB
    minimum), it prints the reasons to stderr and returns ``ExitCode.ERROR``
    (1) without producing a plan. Otherwise it prints the numbered step list
    (or the machine-readable JSON with ``--json``) and returns ``ExitCode.OK``.

    This handler is *pure*: it reads device facts through :func:`build_plan`
    (which only stats the path / reads ``/sys``) and never writes to any disk.
    """
    plan = build_plan(
        target=ns.target,
        esp_size=ns.esp_size,
        hostname=ns.hostname,
        rootfs_src=ns.rootfs_src,
    )
    if not plan.sanity.ok:
        print(
            f"pengyos-installer: refusing to plan an install for {ns.target}:",
            file=sys.stderr,
        )
        for err in plan.sanity.errors:
            print(f"pengyos-installer:   - [{err.code}] {err.message}", file=sys.stderr)
        return ExitCode.ERROR
    if ns.json:
        print(plan.to_json())
    else:
        print(render_plan_text(plan))
    return ExitCode.OK


#: The command runner the ``install`` handler builds by default. Kept as a
#: module-level seam so tests can swap in a recording fake (no test touches a
#: disk). Production behaviour is the real :class:`SubprocessRunner`.
def _install_runner():
    return install_mod.SubprocessRunner()


#: The work dir ``install`` writes the configured files under (the mount point
#: of the freshly formatted root partition). A module-level seam so tests can
#: redirect the file writes to a temporary directory.
INSTALL_WORK_ROOT = WORK_DIR


def run_install(ns: argparse.Namespace) -> int:
    """The ``install`` handler (spec.md: gated by ``--yes``, runs the plan
    through the runner, re-checks the target, tears down cleanly).

    Refuses (exit 1, before any command runs) when ``--yes`` is absent or the
    target fails its sanity re-check. Otherwise it executes the ordered plan
    via :func:`~pengyos_installer.install.execute_install` and prints the
    summary on success (exit 0). A step failure, a missing PARTUUID, or a
    teardown failure is a hard error (exit 1) — the executor has already torn
    down whatever it mounted.
    """
    if not ns.yes:
        print(
            "pengyos-installer: refusing to install without --yes "
            "(add --yes to actually run the plan)",
            file=sys.stderr,
        )
        return ExitCode.ERROR

    plan = build_plan(
        target=ns.target,
        esp_size=ns.esp_size,
        hostname=ns.hostname,
        rootfs_src=ns.rootfs_src,
    )
    if not plan.sanity.ok:
        print(
            f"pengyos-installer: refusing to install on {ns.target}:",
            file=sys.stderr,
        )
        for err in plan.sanity.errors:
            print(f"pengyos-installer:   - [{err.code}] {err.message}", file=sys.stderr)
        return ExitCode.ERROR

    runner = _install_runner()
    try:
        result = install_mod.execute_install(
            plan,
            runner,
            confirm=ns.yes,
            work_root=INSTALL_WORK_ROOT,
        )
    except (install_mod.InstallError, ConfigError) as exc:
        print(f"pengyos-installer: install failed: {exc}", file=sys.stderr)
        return ExitCode.ERROR

    print(result.summary)
    return ExitCode.OK


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``pengyos-installer`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-installer",
        description=(
            "Install PengyOS Core from the live stick onto a target block "
            "device (GPT: EFI System Partition + ext4 root), copy the running "
            "rootfs, and wire GRUB to the new disk."
        ),
        epilog=(
            "Examples:\n"
            "  pengyos-installer plan    --target /dev/sdb\n"
            "  pengyos-installer plan    --target /dev/sdb --esp-size 512M --json\n"
            "  pengyos-installer install --target /dev/sdb --hostname laptop --yes\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pengyos-installer {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    plan_p = sub.add_parser(
        "plan",
        help="Compute and print the ordered install plan (pure; writes nothing)",
        description=(
            "Compute the explicit, ordered install plan for the target device "
            "and print it as a numbered list of shell-quotable command lines. "
            "Pure: it does not modify the target or any disk. Use --json for "
            "the machine-readable plan."
        ),
    )
    _add_target_options(plan_p)
    plan_p.set_defaults(handler=run_plan)

    install_p = sub.add_parser(
        "install",
        help="Execute the plan on the target (requires --yes)",
        description=(
            "Execute the install plan for the target device. Runs only the "
            "planned commands, through the command runner; refuses to run "
            "without --yes; re-checks the target before and during the run."
        ),
    )
    _add_target_options(install_p)
    install_p.add_argument(
        "--yes",
        action="store_true",
        help="Actually run the plan (without --yes, `install` refuses)",
    )
    install_p.set_defaults(handler=run_install)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (tests call this
    directly; ``__main__`` turns it into ``sys.exit``)."""
    parser = build_parser()
    ns = parser.parse_args(list(argv) if argv is not None else None)
    handler: Handler = ns.handler
    try:
        return handler(ns)
    except NotYetImplementedError as exc:
        print(f"pengyos-installer: {exc}", file=sys.stderr)
        return ExitCode.NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
