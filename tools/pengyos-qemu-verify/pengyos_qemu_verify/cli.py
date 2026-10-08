"""Command-line interface for ``pengyos-qemu-verify``.

Current state
-------------
The argument surface is defined and tested here so that later subtasks build on
a *stable, documented* interface:

* ``--version`` — real and working now.
* ``boot <image> ...`` — real and working (items 5–7): builds the argv
  (items 2–4), boots the image headless, waits for readiness markers over the
  serial console, and reports the outcome — a human summary plus, when
  requested, the JUnit XML (``--junit OUT``) and JSON (``--json OUT``) reports
  (item 7's :mod:`.reports` emitters). The boot flow runs the default
  assertion set (item 6's :func:`.assertions.default_assertions`: reached
  ``login:``, no ``Kernel panic``) over the captured serial log and attaches
  the results to the :class:`.boot.BootResult`; the reports and the exit code
  are driven by those checks (``0`` only when the boot is ready **and** every
  check passed). The reports are written even when the boot fails (including
  pre-flight errors such as a missing image) — the spec's "always writes
  JSON" criterion. Exit code reflects the outcome (``0`` ready + all checks
  passed, ``1`` otherwise, ``2`` usage, ``3`` not-implemented).
  ``boot --dry-run`` (item 8) instead prints the resolved QEMU argv and exits
  without launching anything.
* ``probe <image> ...`` — real and working (item 8): prints the resolved QEMU
  argv for inspection **without launching** (no workdir, no serial log, no
  swtpm, no QEMU). It shares its render path with ``boot --dry-run``
  (:func:`render_argv`), so both print byte-for-byte the same argv.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from . import __version__
from . import desktop as _desktop

# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # boot reached readiness
EXIT_FAIL: int = 1  # boot ran but did not reach readiness (timeout/failed)
EXIT_USAGE: int = 2  # argparse's default for bad usage
EXIT_NOT_IMPLEMENTED: int = 3  # subcommand recognised but not yet implemented

# --- Documented interface defaults -----------------------------------------
DEFAULT_TIMEOUT: float = 120.0
DEFAULT_RAM: int = 512
DEFAULT_EXPECT: str = "login:"
DEFAULT_MATRIX_FILE: str = "core.toml"
# Mirrors boot.DEFAULT_SETTLE_SECONDS: keep reading the serial log after the
# login prompt, so the autologin banner and the shell prompt are captured too.
DEFAULT_SETTLE: float = 4.0
# The SSH lane's defaults (see .sshchecks and docs/qemu-testing.md §4).
from .sshchecks import DEFAULT_SSH_PORT, DEFAULT_SSH_USER


def _add_image_and_accel_flags(parser: argparse.ArgumentParser) -> None:
    """Register the flags shared by the ``boot`` and ``probe`` subcommands.

    ``image`` is a required positional. The accelerator/firmware flags mirror
    the interface in ``spec.md`` and are the exact inputs the argv-construction
    subtask will consume.
    """
    parser.add_argument(
        "image",
        help="Path to the PengyOS disk image to boot (raw or qcow2).",
    )
    parser.add_argument(
        "--kernel",
        metavar="BZIMAGE",
        default=None,
        help="Path to a bzImage to boot via -kernel (bare-metal kernel boot). "
        "Omit for firmware (OVMF/GRUB) disk boot, where the kernel lives on the "
        "image itself.",
    )
    parser.add_argument(
        "--uefi",
        action="store_true",
        default=False,
        help="Boot in UEFI/OVMF mode (wire pflash/-bios). PengyOS images are "
        "UEFI/GRUB2 disk images and need this.",
    )
    parser.add_argument(
        "--tpm",
        action="store_true",
        default=False,
        help="Attach an optional swtpm TPM2 device for measured boot. The "
             "swtpm state is provisioned before the boot (swtpm_setup when "
             "installed, else the committed state fixture).",
    )
    parser.add_argument(
        "--ram",
        type=int,
        default=DEFAULT_RAM,
        metavar="MB",
        help="Guest memory size in MiB (default: %(default)s).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help="Maximum seconds to wait for readiness before declaring a timeout "
        "(default: %(default)s).",
    )
    parser.add_argument(
        "--no-snapshot",
        dest="no_snapshot",
        action="store_true",
        default=False,
        help="Write guest disk changes through to the image instead of the "
        "default throwaway overlay (snapshot=on). The default leaves the "
        "artifact byte-identical, so a verification boot cannot change the "
        "image or its checksum; use --no-snapshot only when a boot must "
        "persist guest state.",
    )
    parser.add_argument(
        "--hostfwd",
        action="append",
        metavar="SPEC",
        default=None,
        help="Forward a host port into the guest over QEMU user networking "
             "(repeatable). SPEC is a QEMU hostfwd value, e.g. 'tcp::2222-:22' "
             "to reach the guest's sshd on host port 2222. Omit to keep "
             "QEMU's default (no forwarding).",
    )


def _add_ssh_flags(parser: argparse.ArgumentParser) -> None:
    """Register the in-guest (SSH) check flags on a subcommand.

    These are the checks the serial console cannot answer. They need an image
    built with `PENGYOS_AUTHORIZED_KEYS_FILE` (docs/qemu-testing.md §4); the
    port forward into the guest is added for you.
    """
    parser.add_argument(
        "--ssh",
        action="store_true",
        default=False,
        help="After readiness, run the default in-guest checks over the image's "
             "sshd (no failed units, sshd active). Requires an image built with "
             "PENGYOS_AUTHORIZED_KEYS_FILE; the hostfwd into port 22 is added "
             "for you.",
    )
    parser.add_argument(
        "--ssh-command",
        action="append",
        metavar="COMMAND",
        default=None,
        help="Run COMMAND in the booted guest as an extra check that must exit 0 "
             "(repeatable). Implies --ssh. Example: --ssh-command 'flatpak --version'.",
    )
    parser.add_argument(
        "--ssh-key",
        metavar="IDENTITY",
        default=None,
        help="Private key used to authenticate to the guest. Default: "
             "$PENGYOS_SSH_KEY, else ~/.ssh/id_ed25519 when it exists.",
    )
    parser.add_argument(
        "--ssh-port",
        type=int,
        default=DEFAULT_SSH_PORT,
        metavar="PORT",
        help="Host port forwarded into the guest's sshd (default: %(default)s).",
    )
    parser.add_argument(
        "--ssh-user",
        default=DEFAULT_SSH_USER,
        metavar="USER",
        help="Guest user to connect as (default: %(default)s; the image's sshd "
             "is PermitRootLogin prohibit-password).",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-qemu-verify",
        description="Headless QEMU/KVM boot-test harness for PengyOS.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )

    sub = parser.add_subparsers(
        dest="command", metavar="{boot,probe,smoke,desktop}"
    )

    boot_p = sub.add_parser(
        "boot",
        help="Boot the image headless, wait for readiness, run assertions.",
        description=(
            "Boot a PengyOS image under QEMU (q35), wait for readiness markers "
            "on the serial console, run the assertion set, and emit JUnit XML + "
            "JSON."
        ),
    )
    _add_image_and_accel_flags(boot_p)
    boot_p.add_argument(
        "--expect",
        action="append",
        default=None,
        metavar="REGEX",
        help="Serial-console readiness/existence regex; repeatable. "
        f"Default: {DEFAULT_EXPECT!r}.",
    )

    boot_p.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE,
        metavar="SECONDS",
        help="Seconds to keep reading the serial log after readiness is seen "
        "(default: %(default)s). The readiness marker is the login prompt; the "
        "login banner and the shell prompt that prove the login produced a "
        "working shell arrive just after it, and a check that wants them has "
        "to keep listening. 0 stops on readiness.",
    )
    boot_p.add_argument(
        "--json",
        dest="json_out",
        metavar="OUT",
        default=None,
        help="Write the JSON result to this path.",
    )
    boot_p.add_argument(
        "--junit",
        dest="junit_out",
        metavar="OUT",
        default=None,
        help="Write the JUnit XML to this path.",
    )
    boot_p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the resolved QEMU argv and exit without launching QEMU or "
             "creating any side effects (no workdir, no serial log, no swtpm).",
    )
    _add_ssh_flags(boot_p)

    probe_p = sub.add_parser(
        "probe",
        help="Print the resolved QEMU argv without launching.",
        description=(
            "Resolve and print the QEMU command line for the given image and "
            "flags, for inspection. Does not launch QEMU."
        ),
    )
    _add_image_and_accel_flags(probe_p)

    smoke_p = sub.add_parser(
        "smoke",
        help="Run a declarative smoke matrix over an image (SPEC 556).",
        description=(
            "Boot every variant declared in a smoke matrix, evaluate the "
            "matrix's checks over the captured serial log, and emit JSON + "
            "JUnit. The shipped matrix (smoke/core.toml) covers PengyOS Core."
        ),
    )
    smoke_p.add_argument(
        "--matrix",
        metavar="PATH",
        default=None,
        help=f"Matrix file to run (default: the shipped {DEFAULT_MATRIX_FILE!r}).",
    )
    smoke_p.add_argument(
        "--image",
        metavar="IMAGE",
        default=None,
        help="Override the matrix's image path (used verbatim).",
    )
    smoke_p.add_argument(
        "--variant",
        action="append",
        metavar="ID",
        default=None,
        help="Run only this variant id; repeatable. Default: every variant "
        "declared in the matrix.",
    )

    smoke_p.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE,
        metavar="SECONDS",
        help="Seconds to keep reading the serial log after each variant "
        "reaches readiness (default: %(default)s). The matrix's autologin "
        "checks need the shell prompt, which follows the login prompt.",
    )
    smoke_p.add_argument(
        "--json",
        dest="json_out",
        metavar="OUT",
        default=None,
        help="Write the matrix JSON report to this path.",
    )
    smoke_p.add_argument(
        "--junit",
        dest="junit_out",
        metavar="OUT",
        default=None,
        help="Write the matrix JUnit XML report to this path.",
    )
    smoke_p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the matrix plan and each variant's resolved QEMU argv, "
        "booting nothing.",
    )

    desktop_p = sub.add_parser(
        "desktop",
        help="Boot a Desktop installer stick and prove the COSMIC session runs.",
        description=(
            "Boot a Desktop installer stick under QEMU with a rendering GPU in a "
            "virtual X server, type a probe on the guest's serial console, and "
            "pass only if the console gave a shell, cosmic-comp, cosmic-panel and "
            "the installer GUI are running, and the system reports no failed "
            "units. Saves a screenshot and both logs. Needs KVM, OVMF, Xvfb and "
            "ffmpeg. This is the check a login prompt cannot make: 26.10.3 passed "
            "the login check with a compositor that died at every start."
        ),
    )
    desktop_p.add_argument(
        "image",
        help="Path to the Desktop installer stick (-installer.img) to boot.",
    )
    desktop_p.add_argument(
        "--no-installer",
        dest="require_installer",
        action="store_false",
        default=True,
        help="This image carries no installer payload (the plain live desktop "
        "image): require the COSMIC session but not the installer GUI. Without it "
        "the check would fail an image that is behaving correctly.",
    )
    desktop_p.add_argument(
        "--out",
        dest="out_prefix",
        metavar="PREFIX",
        default=None,
        help="Write PREFIX.png, PREFIX.serial.log and PREFIX.qemu.log. Default: "
        "'qemu-desktop-check' next to the image.",
    )
    desktop_p.add_argument(
        "--timeout",
        type=float,
        default=_desktop.DEFAULT_SESSION_TIMEOUT,
        metavar="SECONDS",
        help="Maximum seconds to wait for the console login prompt "
        "(default: %(default)s).",
    )
    desktop_p.add_argument(
        "--settle",
        type=float,
        default=_desktop.DEFAULT_SETTLE_SECONDS,
        metavar="SECONDS",
        help="Seconds to let the session come up after the login prompt before "
        "probing it (default: %(default)s). The compositor, panel and applets "
        "start after getty, not before.",
    )
    desktop_p.add_argument(
        "--no-screenshot",
        dest="screenshot",
        action="store_false",
        default=True,
        help="Skip the ffmpeg screenshot; the check still runs, only the "
        "evidence image is dropped.",
    )
    desktop_p.add_argument(
        "--json",
        dest="json_out",
        metavar="OUT",
        default=None,
        help="Write the session verdict as JSON to this path.",
    )

    return parser


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point. Returns a process exit code (never raises on good use).

    ``argv`` is accepted for testability and defaults to ``sys.argv[1:]``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pengyos-qemu-verify {__version__}")
        return EXIT_OK

    if not args.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    if args.command == "boot":
        if getattr(args, "dry_run", False):
            return _run_probe(args)
        return _run_boot(args)

    if args.command == "probe":
        return _run_probe(args)

    if args.command == "smoke":
        if args.dry_run:
            return _run_smoke_dry(args)
        return _run_smoke(args)

    if args.command == "desktop":
        return _run_desktop(args)

    # Defensive: only ``boot``/``probe``/``smoke``/``desktop`` are registered
    # subcommands, so this branch is unreachable. Kept so an unexpected command
    # value is a clean, distinct failure rather than a crash.
    sys.stderr.write(
        f"pengyos-qemu-verify: subcommand '{args.command}' is scheduled but "
        "not implemented yet.\n"
    )
    return EXIT_NOT_IMPLEMENTED


def desktop_required(require_installer: bool) -> "tuple[str, ...]":
    """The process set the ``desktop`` check requires for one image.

    Split out of :func:`_run_desktop` so the choice is testable without booting a
    guest: it is the only difference between checking an installer stick and
    checking the plain live image, and getting it wrong either fails a healthy
    image or excuses a boot that stopped at the greeter.
    """
    from . import desktop as _desktop

    if require_installer:
        return _desktop.REQUIRED_PROCESSES
    return tuple(p for p in _desktop.REQUIRED_PROCESSES if p != "pengyos-install")


def _run_desktop(args: "argparse.Namespace") -> int:
    """Run the ``desktop`` subcommand: the COSMIC session check.

    Exit code: :data:`EXIT_OK` when every session fact holds, :data:`EXIT_FAIL`
    when the boot ran but a fact failed (no shell, a process missing, a failed
    unit), :data:`EXIT_USAGE` when the live check could not run at all (no image,
    no Xvfb/ffmpeg/QEMU, no OVMF).
    """
    import json

    from . import desktop as desktop_mod

    out_prefix = args.out_prefix or os.path.join(
        os.path.dirname(os.path.abspath(args.image)), "qemu-desktop-check"
    )
    required = desktop_required(args.require_installer)
    try:
        outcome = desktop_mod.run_desktop_check(
            args.image,
            out_prefix,
            timeout=args.timeout,
            settle=args.settle,
            screenshot=args.screenshot,
            required=required,
        )
    except desktop_mod.DesktopCheckError as exc:
        sys.stderr.write(f"pengyos-qemu-verify: desktop check failed: {exc}\n")
        return EXIT_USAGE

    for check in outcome.checks:
        state = "PASS" if check.passed else "FAIL"
        line = f"check:    {check.name} {state}"
        if check.evidence:
            line += f" — {check.evidence}"
        print(line)
    if outcome.error:
        sys.stderr.write(f"pengyos-qemu-verify: {outcome.error}\n")
    print(f"logs:     {out_prefix}.serial.log, {out_prefix}.qemu.log")
    if args.screenshot:
        print(f"screenshot: {out_prefix}.png")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(outcome.to_dict(), handle, indent=2)
            handle.write("\n")
        print(f"report:   {args.json_out}")
    print(
        f"result:   {'pass' if outcome.passed else 'fail'} "
        f"({len(outcome.checks)} checks, {len(outcome.failed_checks)} failed)"
    )
    return EXIT_OK if outcome.passed else EXIT_FAIL


def _hostfwd_tuple(args: "argparse.Namespace") -> "tuple[str, ...] | None":
    """Normalise the ``--hostfwd`` CLI list to a tuple (or None when unset)."""
    vals = getattr(args, "hostfwd", None)
    return tuple(vals) if vals else None


def _ssh_requested(args: "argparse.Namespace") -> bool:
    """True when the caller asked for in-guest checks (--ssh or --ssh-command)."""
    return bool(getattr(args, "ssh", False) or getattr(args, "ssh_command", None))


def _ssh_check_name(command: str) -> str:
    """A report name for an ad-hoc ``--ssh-command``, derived from the command.

    Names land in JUnit ``<testcase name=...>`` and in JSON, so they stay
    ASCII-ish and bounded; the full command remains the check itself.
    """
    import re

    flat = re.sub(r"[^a-zA-Z0-9]+", "-", command).strip("-")
    return f"ssh:{flat[:48] or 'command'}"


def _ssh_checks_for(args: "argparse.Namespace") -> "tuple":
    """Build the check list the CLI asked for: defaults plus ad-hoc commands."""
    from .sshchecks import SshCheck, default_ssh_checks

    checks: "list" = list(default_ssh_checks()) if getattr(args, "ssh", False) else []
    for command in getattr(args, "ssh_command", None) or []:
        checks.append(SshCheck.exit_zero(_ssh_check_name(command), command))
    return tuple(checks)


def _ssh_target_for(args: "argparse.Namespace"):
    """Resolve the SSH target from the CLI flags.

    The identity is pinned only when the caller named one or the default key
    actually exists: passing a nonexistent `-i` would make ssh warn and then
    fall back to keys the caller never chose.
    """
    from .sshchecks import SshTarget

    identity = getattr(args, "ssh_key", None) or os.environ.get("PENGYOS_SSH_KEY")
    if identity is None:
        candidate = os.path.expanduser("~/.ssh/id_ed25519")
        identity = candidate if os.path.isfile(candidate) else None
    return SshTarget(
        port=getattr(args, "ssh_port", DEFAULT_SSH_PORT),
        user=getattr(args, "ssh_user", DEFAULT_SSH_USER),
        identity_file=identity,
    )


def _hostfwd_for_run(args: "argparse.Namespace") -> "tuple[str, ...] | None":
    """``--hostfwd`` plus the forward the SSH lane needs, when it was asked for.

    Shared by ``render_argv`` and ``_run_boot``, so ``--dry-run`` shows the argv
    that will actually run: an SSH check with no forwarded port is a guaranteed
    FAIL that reads like a broken image, so the forward is added here.
    """
    from .boot import _ssh_hostfwd

    fwd = list(_hostfwd_tuple(args) or [])
    if _ssh_requested(args):
        fwd = _ssh_hostfwd(fwd, _ssh_target_for(args))
    return tuple(fwd) if fwd else None


def render_argv(args: "argparse.Namespace") -> "list[str]":

    """Resolve and build the QEMU argv for ``args`` *without launching anything*.

    Pure render: it creates no workdir, writes no
    serial-log file, copies no OVMF VARS, starts no swtpm, and launches no
    QEMU. The only I/O is the read-only firmware location in
    :func:`_locate_for_render` (an ``os.path.isfile`` / ``os.access`` probe),
    which — being non-mutating — lets a *pending* firmware/image render too.
    The TPM datapath is rendered as the placeholder path ``tpm.sock`` because a
    dry-run has no running swtpm socket to point at.

    This is the single render path shared by the ``probe`` subcommand and
    ``boot --dry-run``; both therefore print the identical argv.

    Args:
        args: the parsed ``boot`` or ``probe`` namespace (must carry
            ``image``, ``kernel``, ``uefi``, ``tpm`` and ``ram``).

    Returns:
        The full ``qemu-system-x86_64`` argv (binary name included) that a
        real boot *would* use.

    Raises:
        .argv.OvmfNotFound: only in the (defensive) case where even the
            ``exist=False`` render finds nothing to point at.
        ValueError: for a config conflict such as ``--uefi`` + ``--kernel``.
    """
    from . import argv as argv_mod

    tpm_socket = "tpm.sock" if getattr(args, "tpm", False) else None
    ovmf_firmware = None
    if getattr(args, "uefi", False):
        ovmf_firmware = _locate_for_render(argv_mod)
    return argv_mod.build_argv(
        args.image,
        kernel=args.kernel,
        ram=args.ram,
        kvm=None,  # auto-detect via the read-only /dev/kvm probe
        uefi=getattr(args, "uefi", False),
        ovmf=ovmf_firmware,
        snapshot=not getattr(args, "no_snapshot", False),
        hostfwd=_hostfwd_for_run(args),
        tpm_socket=tpm_socket,
    )


def _locate_for_render(argv_mod) -> "object":
    """Locate OVMF for a dry-run render, never failing on a missing firmware.

    Tries the real, existence-checking locator first (read-only ``os.path``
    probes — not a side effect) so a present *split* firmware renders
    faithfully as two pflash drives. If no firmware is found at all — e.g. a
    pending build or a custom install path — it falls back to an
    ``exist=False`` render so a representative argv is still printed instead
    of an error.
    """
    try:
        return argv_mod.locate_ovmf()
    except argv_mod.OvmfNotFound:
        return argv_mod.locate_ovmf(exist=False)


def _run_probe(args: "argparse.Namespace") -> int:
    """Render and print the resolved QEMU argv; launch nothing (item 8).

    Shared by the ``probe`` subcommand and ``boot --dry-run``. Prints a
    shell-pasteable command to stdout: a leading ``#`` comment line, then the
    argv joined with :func:`shlex.join` (args containing spaces are quoted so
    the line is copy-paste runnable). Returns :data:`EXIT_OK` on success and
    :data:`EXIT_FAIL` when the argv cannot be resolved (e.g. ``--uefi`` and
    ``--kernel`` are both set).
    """
    from . import argv as argv_mod

    try:
        rendered = render_argv(args)
    except (argv_mod.OvmfNotFound, ValueError) as exc:
        sys.stderr.write(f"pengyos-qemu-verify: could not resolve argv: {exc}\n")
        return EXIT_FAIL
    print("# pengyos-qemu-verify: resolved QEMU argv (dry-run; nothing launched)")
    print(shlex.join(rendered))
    return EXIT_OK


def _emit_reports(args: argparse.Namespace, result: "object") -> "list":
    """Write the ``--json`` / ``--junit`` reports (item 7); return the paths.

    The reports carry the checks attached to ``result`` (the item-6 runner
    output the boot flow evaluates after the boot — see
    ``BootResult.checks``). Only the requested formats are written; each
    write failure (e.g. a non-writable location) is reported on stderr and
    does not change the boot's exit code — the boot outcome itself is the
    primary result.
    """
    from . import reports

    written: "list" = []
    if args.json_out:
        try:
            written.append(reports.write_json(args.json_out, result))
        except OSError as exc:
            sys.stderr.write(f"pengyos-qemu-verify: could not write {args.json_out!r}: {exc}\n")
    if args.junit_out:
        try:
            written.append(reports.write_junit(args.junit_out, result))
        except OSError as exc:
            sys.stderr.write(f"pengyos-qemu-verify: could not write {args.junit_out!r}: {exc}\n")
    return written


def _run_boot(args: argparse.Namespace) -> int:
    """Run the ``boot`` subcommand (items 5–7 + the item-6 wiring) and return
    the exit code.

    Boots the image headless, waits for the readiness markers on the serial
    console, runs the default assertion set over the captured log (item 6,
    wired into :func:`.boot.boot_image`), prints a summary, and — when
    requested — writes the JUnit XML (``--junit``) and JSON (``--json``)
    reports (item 7), including on pre-flight failures (a minimal error
    result is emitted). Exit code: :data:`EXIT_OK` when readiness was reached
    and all checks passed, else :data:`EXIT_FAIL` (timeout, QEMU failure, a
    failed check, or a harness-level error such as a missing image/firmware/
    swtpm or unprovisionable TPM state).
    """
    from . import reports
    from .argv import OvmfNotFound
    from .boot import DEFAULT_EXPECT, BootError, boot_image
    from .tpm import SwtpmError
    from .tpm_state import TpmStateError

    expect = tuple(args.expect) if args.expect else (DEFAULT_EXPECT,)
    try:
        result = boot_image(
            args.image,
            kernel=args.kernel,
            uefi=args.uefi,
            tpm=args.tpm,
            ram=args.ram,
            timeout=args.timeout,
            settle=getattr(args, "settle", 0.0),
            snapshot=not getattr(args, "no_snapshot", False),
            hostfwd=_hostfwd_for_run(args),
            expect=expect,
            # The SSH lane (see .sshchecks): in-guest checks run while the guest
            # is still up and land in result.checks like any log check.
            ssh_checks=_ssh_checks_for(args) if _ssh_requested(args) else None,
            ssh_target=_ssh_target_for(args) if _ssh_requested(args) else None,
            # Assertions: the default set (None); the boot flow attaches the
            # resulting checks to the result — they drive the reports and the
            # exit code below.
        )
    except (BootError, OvmfNotFound, SwtpmError, TpmStateError, ValueError) as exc:
        sys.stderr.write(f"pengyos-qemu-verify: boot failed: {exc}\n")
        # "Always writes JSON": even a pre-flight failure (no boot attempt
        # happened, so no full BootResult exists) gets a report (its attached
        # check list is empty — nothing was booted to assert over).
        _emit_reports(args, reports.make_error_result(args.image, str(exc)))
        return EXIT_FAIL

    print(f"image:    {result.image}")
    print(f"accel:    {result.accel}")
    print(f"duration: {result.duration:.2f}s")
    print(f"log:      {result.serial_log}")
    if result.tpm_state:
        detail = result.tpm_state_method
        if result.tpm_protocol:
            detail = f"{detail}, {result.tpm_protocol}"
        print(f"tpmstate: {result.tpm_state} ({detail})")
    for marker in result.markers:
        state = "MATCH" if marker.matched else "missing"
        print(f"expect:   {marker.pattern!r} {state}"
              + (f" — {marker.evidence}" if marker.evidence else ""))
    for check in result.checks:
        state = "PASS" if check.passed else "FAIL"
        print(f"check:    {check.name} {state}"
              + (f" — {check.evidence}" if check.evidence else ""))
    if result.error is not None:
        print(f"error:    {result.error}")
    print(f"result:   {result.status}" + (f" (exit={result.returncode})"
                                           if result.returncode is not None else ""))
    for path in _emit_reports(args, result):
        print(f"report:   {path}")
    return EXIT_OK if reports.overall_result(result) == reports.PASS_RESULT else EXIT_FAIL

def _emit_matrix_reports(args: "argparse.Namespace", outcome: "object") -> "list":
    """Write the ``smoke`` ``--json`` / ``--junit`` reports; return the paths.

    Only the requested formats are written; a write failure (e.g. a
    non-writable location) is reported on stderr and does not change the
    matrix verdict — the variant results themselves are the primary output.
    """
    from . import smoke as smoke_mod

    written: "list" = []
    if getattr(args, "json_out", None):
        try:
            written.append(smoke_mod.write_json(args.json_out, outcome))
        except OSError as exc:
            sys.stderr.write(
                f"pengyos-qemu-verify: could not write {args.json_out!r}: {exc}\n"
            )
    if getattr(args, "junit_out", None):
        try:
            written.append(smoke_mod.write_junit(args.junit_out, outcome))
        except OSError as exc:
            sys.stderr.write(
                f"pengyos-qemu-verify: could not write {args.junit_out!r}: {exc}\n"
            )
    return written


def _run_smoke(args: "argparse.Namespace") -> int:
    """Run the ``smoke`` subcommand: a declarative matrix over one image.

    Loads the matrix (default: the shipped ``smoke/core.toml``), boots every
    selected variant, evaluates that variant's checks over its serial log,
    prints a per-variant summary, and writes the requested reports. Exit code:
    :data:`EXIT_OK` only when every variant passed (booted ready *and* every
    check passed), :data:`EXIT_FAIL` when any variant failed, and
    :data:`EXIT_USAGE` for an unusable matrix (missing/invalid file, unknown
    variant id, or no image to boot) — a config problem, not a test result.
    """
    from . import smoke as smoke_mod

    try:
        matrix = smoke_mod.load_matrix(args.matrix)
        image = smoke_mod.resolve_image(matrix, args.image)
    except smoke_mod.MatrixError as exc:
        sys.stderr.write(f"pengyos-qemu-verify: {exc}\n")
        return EXIT_USAGE

    print(
        f"matrix:   {matrix.name}"
        + (f" — {matrix.description}" if matrix.description else "")
    )
    print(f"image:    {image}")
    try:
        outcome = smoke_mod.run_matrix(
            matrix, image, variants=args.variant, settle=args.settle
        )
    except smoke_mod.MatrixError as exc:
        sys.stderr.write(f"pengyos-qemu-verify: {exc}\n")
        return EXIT_USAGE

    for variant in outcome.variants:
        state = "PASS" if variant.passed else "FAIL"
        print(
            f"variant:  {variant.variant.id} {state}"
            + (f" — {variant.error}" if variant.error else "")
        )
        boot = variant.boot
        if boot is not None:
            print(
                f"          boot={boot.status} accel={boot.accel} "
                f"duration={boot.duration:.2f}s"
            )
            print(f"          log={boot.serial_log}")
            if boot.error:
                print(f"          error={boot.error}")
        for check in variant.checks:
            print(
                f"          check {check.name} "
                + ("PASS" if check.passed else "FAIL")
                + (f" — {check.evidence}" if check.evidence else "")
            )
    print(
        f"result:   {outcome.result} ({len(outcome.variants)} variant(s), "
        f"{outcome.checks_run} checks, {outcome.checks_failed} failed)"
    )
    for path in _emit_matrix_reports(args, outcome):
        print(f"report:   {path}")
    return EXIT_OK if outcome.passed else EXIT_FAIL


def _run_smoke_dry(args: "argparse.Namespace") -> int:
    """Print a smoke matrix's plan and each variant's argv; boot nothing.

    Shares :func:`smoke.plan_argv` with the real runner, so what is printed is
    the argv a variant *would* use (the TPM datapath renders the placeholder
    socket, exactly as ``boot --dry-run``/``probe`` do).
    """
    from . import argv as argv_mod
    from . import smoke as smoke_mod

    try:
        matrix = smoke_mod.load_matrix(args.matrix)
        image = smoke_mod.resolve_image(matrix, args.image)
        selected = (
            [matrix.variant(v) for v in args.variant]
            if args.variant
            else list(matrix.variants)
        )
    except smoke_mod.MatrixError as exc:
        sys.stderr.write(f"pengyos-qemu-verify: {exc}\n")
        return EXIT_USAGE

    print(
        f"# pengyos-qemu-verify: smoke matrix {matrix.name!r} "
        "(dry-run; nothing booted)"
    )
    print(f"# image: {image}")
    firmware = _locate_for_render(argv_mod)
    for variant in selected:
        label = variant.description or "(no description)"
        print(f"# variant {variant.id}: {label}")
        for check in matrix.checks_for(variant.id):
            print(f"#   check {check.id} ({check.kind}) /{check.pattern}/")
        rendered = smoke_mod.plan_argv(
            variant, image, ovmf=firmware if variant.uefi else None
        )
        print(shlex.join(rendered))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
