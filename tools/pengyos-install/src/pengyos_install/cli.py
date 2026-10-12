"""Command-line interface for ``pengyos-install``.

Usage::

    pengyos-install plan --release rel.json --layout plan.json [--config install.toml] [--json]
    pengyos-install install --release rel.json --layout plan.json [--config install.toml] --yes [--runner CMD]
    pengyos-install provision-plan --config install.toml [--layout layout.json] [--slot a] [--json]
    pengyos-install disks [--json]
    pengyos-install image --target DEV --hostname H --user U --yes [--payload DIR]
                          [--password-stdin] [--authorized-keys FILE] [--autologin]
                          [--progress json]

This module wires argparse + dispatch. The engine logic lives in
:mod:`pengyos_install.plan` (the pure renderer),
:mod:`pengyos_install.install` (the runner-driven executor), and
:mod:`pengyos_install.provision` (the first-boot renderer); all three commands
are implemented.

``disks`` and ``image`` are the install path the live medium runs today (see
:mod:`pengyos_install.image`): list candidate target disks, and write the
medium's payload image to one of them.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Sequence

from pengyos_install import __version__
from pengyos_install import image as image_install
from pengyos_install.install import InstallError, install
from pengyos_install.models import (
    Layout,
    Plan,
    Release,
    ProvisionConfig,
    ValidationError,
)
from pengyos_install.plan import render_plan
from pengyos_install.provision import render_provision
from pengyos_install.runner import SubprocessRunner, WrapperRunner


class CommandError(Exception):
    """A user-facing command error (reported cleanly, non-zero exit)."""


class CommandNotImplemented(CommandError):
    """The command handler has not been implemented yet.

    Raised by the scaffolded command entry points so the interface is real and
    testable while the engine logic is a follow-up unit.
    """


def _default_config_path() -> str:
    return "install.toml"


def _default_layout_path() -> str:
    return "layout.json"


def cmd_plan(args, *, out=None) -> int:
    """Render the ordered install plan (argv per step).

    Loads the release + layout, renders the plan, and prints it as a shell
    script (default) or JSON (``--json``). The provision phase points at
    ``install.toml`` (see ``provision-plan``); the config is read at first boot.
    """
    release = Release.from_json_file(args.release)
    layout = Layout.from_json_file(args.layout)
    plan = render_plan(release, layout, layout_path=args.layout)

    if args.json:
        print(plan.to_json(), file=out)
    else:
        # ``to_shell_script`` already ends in a newline; write it verbatim so the
        # rendered script is byte-exact (golden).
        out.write(plan.to_shell_script())
    return 0


def cmd_install(args, *, out=None, runner=None) -> int:
    """Execute the install plan against the target (install = first update).

    Loads the release + layout, renders the golden plan, and runs it through the
    injectable runner. Refuses without ``--yes`` (exit 2); hard-fails on the first
    non-ok step, tearing down (unmount + sync) on success *and* failure (exit 2 on
    error). ``--runner CMD`` prefixes every step with *CMD* (e.g. ``echo`` for a
    trace / dry run).

    *runner* is an optional injected :class:`~pengyos_install.runner.Runner` — a
    test seam (see :func:`main`). When omitted, the runner is selected from
    ``--runner`` (a shell wrapper) or the built-in ``SubprocessRunner``.
    """
    # Refuse the destructive write before doing any work (exit 2).
    if not args.yes:
        raise CommandError(
            "refusing to install without confirmation; pass --yes to proceed"
        )

    release = Release.from_json_file(args.release)
    layout = Layout.from_json_file(args.layout)
    plan = render_plan(release, layout)

    if runner is None:
        if args.runner:
            # Inject an external runner: prefix every step with the given command
            # (e.g. ``--runner echo`` traces the commands that would run).
            runner = WrapperRunner(args.runner.split())
        else:
            runner = SubprocessRunner()

    try:
        result = install(
            plan,
            runner,
            confirm=args.yes,
            layout=layout,
            release=release,
        )
    except (InstallError, ValidationError) as exc:
        raise CommandError(str(exc)) from exc

    print(
        f"installed {release.name}-{release.version} to slot {release.slot}: "
        f"{result.steps_run}/{len(plan)} steps ok",
        file=out,
    )
    return 0


def cmd_provision(args, *, out=None) -> int:
    """Render the first-boot provisioning steps (fstab / hostname / users / network).

    Loads the ``install.toml`` config + the target layout, and renders the
    ordered first-boot steps (the same steps the ``install`` engine emits as its
    trailing ``provision-plan`` CLI step). ``--json`` emits JSON instead of a
    shell script. The active slot (which root ``fstab`` lists first) defaults to
    ``a``.
    """
    config = ProvisionConfig.from_toml_file(args.config)
    layout = Layout.from_json_file(args.layout)
    plan = render_provision(config, layout, args.slot)

    if args.json:
        print(plan.to_json(), file=out)
    else:
        out.write(plan.to_shell_script())
    return 0


def cmd_disks(args, *, out=None) -> int:
    """List the disks this machine could install to (the boot disk flagged)."""
    out = out if out is not None else sys.stdout
    disks = image_install.list_disks()
    if args.json:
        print(json.dumps([d.to_dict() for d in disks], indent=2), file=out)
        return 0
    for d in disks:
        flags = []
        if d.is_boot:
            flags.append("running system — not installable")
        if d.removable:
            flags.append("removable")
        if d.mounted:
            flags.append("mounted: " + ", ".join(d.mounted))
        note = f"  ({'; '.join(flags)})" if flags else ""
        print(f"{d.path:<16} {d.size_label:>9}  {d.model or '-'}{note}", file=out)
    return 0


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    if not sys.stdin.isatty():
        raise CommandError("no password given: pass --password-stdin and write it to stdin")
    first = getpass.getpass("Password for the new user (also root): ")
    if first != getpass.getpass("Repeat the password: "):
        raise CommandError("the passwords do not match")
    return first


def _read_pengy_api_key(args) -> str:
    """The assistant's API key.

    ``--pengy-api-key-stdin`` reads it from the **second** line of stdin (the
    first is the password, when ``--password-stdin`` is used) so a secret never
    appears in argv, where any local process could read it from ``/proc``.
    ``--pengy-api-key`` is the convenience form for a key that is not a secret
    (a local endpoint that accepts anything).
    """
    if args.pengy_api_key_stdin:
        return sys.stdin.readline().rstrip("\n")
    return args.pengy_api_key or ""


def cmd_image(args, *, out=None, err=None) -> int:
    """Write the install medium's payload image to --target and provision it."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    keys = ""
    if args.authorized_keys:
        try:
            keys = Path(args.authorized_keys).read_text()
        except OSError as exc:
            raise CommandError(f"cannot read {args.authorized_keys}: {exc.strerror}") from None
    # Order matters: the password consumes stdin's first line, the Pengy API key
    # the second.
    answers = image_install.ProvisionAnswers(
        hostname=args.hostname, username=args.user, password=_read_password(args),
        authorized_keys=keys, autologin=args.autologin,
        pengy_base_url=args.pengy_base_url, pengy_api_key=_read_pengy_api_key(args),
        pengy_model=args.pengy_model,
    )

    if args.progress == "json":
        def report(phase, fraction, message):
            print(json.dumps({"phase": phase, "fraction": round(fraction, 4),
                              "message": message}), file=out, flush=True)
    else:
        last = {"key": None}

        def report(phase, fraction, message):
            key = (phase, message, int(fraction * 20))
            if key != last["key"]:
                last["key"] = key
                print(f"[{phase:>9}] {fraction * 100:5.1f}%  {message}", file=err, flush=True)

    try:
        image_install.install_image(
            args.target, answers, payload_dir=args.payload, confirm=args.yes, report=report
        )
    except image_install.ImageInstallError as exc:
        if args.progress == "json":
            print(json.dumps({"phase": "error", "fraction": 0, "message": str(exc)}),
                  file=out, flush=True)
        print(f"error: {exc}", file=err)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-install",
        description=(
            "Edition-agnostic install engine for PengyOS: "
            "(release, target, layout) -> ordered plan -> execute."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"pengyos-install {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser(
        "plan", help="render the ordered install plan (argv per step)"
    )
    p_plan.add_argument("--release", required=True, help="release manifest JSON")
    p_plan.add_argument("--layout", required=True, help="target layout JSON")
    p_plan.add_argument(
        "--config",
        nargs="?",
        default=_default_config_path(),
        help="first-boot provisioning config (install.toml)",
    )
    p_plan.add_argument(
        "--json",
        action="store_true",
        help="emit the plan as JSON instead of a shell script",
    )

    p_install = sub.add_parser(
        "install", help="execute the plan against the target (install = first update)"
    )
    p_install.add_argument("--release", required=True, help="release manifest JSON")
    p_install.add_argument("--layout", required=True, help="target layout JSON")
    p_install.add_argument(
        "--config",
        nargs="?",
        default=_default_config_path(),
        help="first-boot provisioning config (install.toml)",
    )
    p_install.add_argument(
        "--yes",
        action="store_true",
        help="acknowledge the destructive write (refused without this flag)",
    )
    p_install.add_argument(
        "--runner",
        default=None,
        help="optional external runner command (defaults to the built-in runner)",
    )

    p_prov = sub.add_parser(
        "provision-plan", help="render the first-boot provisioning steps"
    )
    p_prov.add_argument(
        "--config",
        nargs="?",
        default=_default_config_path(),
        help="first-boot provisioning config (install.toml)",
    )
    p_prov.add_argument(
        "--layout",
        nargs="?",
        default=_default_layout_path(),
        help="target layout JSON (needed for the fstab partition paths)",
    )
    p_prov.add_argument(
        "--slot",
        nargs="?",
        default="a",
        help="active root slot the fstab lists first (default: a)",
    )
    p_prov.add_argument(
        "--json",
        action="store_true",
        help="emit the provisioning plan as JSON instead of a shell script",
    )

    p_disks = sub.add_parser("disks", help="list the disks PengyOS could be installed to")
    p_disks.add_argument("--json", action="store_true", help="emit JSON")

    p_image = sub.add_parser(
        "image",
        help="install the medium's disk image onto a whole disk (erases it) and provision it",
    )
    p_image.add_argument("--target", required=True, help="whole disk to erase, e.g. /dev/nvme0n1")
    p_image.add_argument("--hostname", required=True, help="hostname of the installed system")
    p_image.add_argument("--user", required=True, help="first user account to create")
    p_image.add_argument(
        "--password-stdin", action="store_true",
        help="read the password (user + root) from the first line of stdin",
    )
    p_image.add_argument("--authorized-keys", default=None, metavar="FILE",
                         help="SSH public keys for the user (an authorized_keys file); "
                              "the Server's sshd accepts keys only")
    p_image.add_argument("--autologin", action="store_true",
                         help="Desktop: log the user in automatically at boot instead of "
                              "showing the login screen (off by default)")
    p_image.add_argument("--pengy-base-url", default="", metavar="URL",
                         help="assistant: the OpenAI-compatible endpoint to seed "
                              "~/.config/pengy/settings.json with (http/https). "
                              "Omit it and Pengy keeps its own defaults")
    p_image.add_argument("--pengy-api-key", default="", metavar="KEY",
                         help="assistant: the endpoint's API key (for a real secret use "
                              "--pengy-api-key-stdin so it never appears in argv)")
    p_image.add_argument("--pengy-api-key-stdin", action="store_true",
                         help="assistant: read the API key from the second line of stdin "
                              "(the first line is the password with --password-stdin)")
    p_image.add_argument("--pengy-model", default="", metavar="NAME",
                         help="assistant: the model name to start with (e.g. qwen3:8b)")
    p_image.add_argument("--payload", default=None,
                         help=f"payload directory (default: {image_install.PAYLOAD_MOUNT})")
    p_image.add_argument("--yes", action="store_true",
                         help="acknowledge that the target disk is erased")
    p_image.add_argument("--progress", choices=("text", "json"), default="text",
                         help="progress format: text on stderr, or JSON lines on stdout")

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    out=None,
    err=None,
    runner=None,
) -> int:
    """Entry point. Returns a process exit code.

    *runner* is an optional injected :class:`~pengyos_install.runner.Runner` — a
    test seam so the ``install`` handler can be driven by a :class:`FakeRunner`
    (no root / hardware / network). When omitted, ``install`` selects its runner
    from ``--runner`` (a shell wrapper) or the built-in ``SubprocessRunner``.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handlers = {
        "plan": lambda a: cmd_plan(a, out=out),
        "install": lambda a: cmd_install(a, out=out, runner=runner),
        "provision-plan": lambda a: cmd_provision(a, out=out),
        "disks": lambda a: cmd_disks(a, out=out),
        "image": lambda a: cmd_image(a, out=out, err=err),
    }
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choices
        parser.error(f"unknown command {args.command!r}")
        return 2

    try:
        return handler(args)
    except CommandNotImplemented as exc:
        print(f"error: {exc}", file=err)
        return 3
    except CommandError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except ValidationError as exc:
        # A malformed release/layout/config: a clean user-facing error.
        print(f"error: {exc}", file=err)
        return 2
    except OSError as exc:
        # A missing release/layout/config file: a clean user-facing error.
        print(f"error: {exc}", file=err)
        return 2


# Console-script entry points (declared in pyproject.toml). All three delegate to
# the single dispatcher, which routes on the subcommand.
plan_main = main
install_main = main
provision_main = main


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
