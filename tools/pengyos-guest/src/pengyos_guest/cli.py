"""Command-line interface for ``pengyos-guest``.

Usage::

    pengyos-guest list                    # guests this machine knows about
    pengyos-guest enter dev               # shell in the dev guest (created on first use)
    pengyos-guest enter dev -- make -j8   # run one command in it
    pengyos-guest apply [name...]         # create + provision (idempotent)
    pengyos-guest export dev --app code   # host launcher (+ menu entry for GUI apps)
    pengyos-guest destroy dev             # remove the container (home is kept)

Guest definitions come from the image (``/usr/share/pengyos/guests.d/*.toml``)
and from ``~/.config/pengyos/guests.toml``, which wins by name. ``--config
FILE`` uses one file instead.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from pengyos_guest import __version__
from pengyos_guest.config import Guest, Guestbook, load, load_default
from pengyos_guest.engines import GuestError, GuestManager


class CommandError(Exception):
    """A user-facing command error (reported cleanly, non-zero exit)."""


def _book(config: str | None) -> Guestbook:
    try:
        return load(config) if config else load_default()
    except FileNotFoundError:
        raise CommandError(f"config file not found: {config}") from None
    except (ValueError, OSError) as exc:
        raise CommandError(str(exc)) from None


def _guest(book: Guestbook, name: str) -> Guest | None:
    return next((g for g in book.guests if g.name == name), None)


def cmd_list(config: str | None, *, out=None, manager=None) -> int:
    """One line per declared guest: name, image, whether it exists, description."""
    out = out if out is not None else sys.stdout
    book = _book(config)
    if manager is None:
        manager = GuestManager()
    for guest in book.guests:
        state = "created" if manager.container_exists(guest.name) else "not created"
        line = f"{guest.name}\t{guest.image}\t{state}"
        if guest.description:
            line += f"\t{guest.description}"
        print(line, file=out, flush=True)
    return 0


def _report(result, out) -> None:
    # The steps' own output streams straight to the terminal; keep ours in order.
    if result.created:
        print(f"created guest {result.name} from {result.image}", file=out, flush=True)
    else:
        print(f"guest {result.name} already exists", file=out, flush=True)
    if result.provisioned:
        print(f"provisioned {len(result.packages)} package(s) in {result.name}", file=out, flush=True)
    if result.setup:
        print(f"ran {len(result.setup)} setup step(s) in {result.name}", file=out, flush=True)


def cmd_apply(config: str | None, names: Sequence[str] = (), *, out=None, manager=None) -> int:
    """Create + provision the named guests (all declared guests when none are
    named). Idempotent: an existing container is not recreated; packages and
    setup steps are (re)applied regardless."""
    out = out if out is not None else sys.stdout
    book = _book(config)
    guests = list(book.guests)
    if names:
        unknown = [n for n in names if _guest(book, n) is None]
        if unknown:
            raise CommandError(f"no such guest: {', '.join(unknown)}")
        guests = [g for g in guests if g.name in names]
    if manager is None:
        manager = GuestManager()
    for guest in guests:
        _report(manager.apply(guest, stream=True), out)
    return 0


def cmd_enter(name: str, command: Sequence[str] = (), config: str | None = None, *,
              out=None, manager=None) -> int:
    """Shell (or one command) in *name*. A declared guest that does not exist
    yet is created and provisioned first; the exit code is the command's."""
    out = out if out is not None else sys.stdout
    if manager is None:
        manager = GuestManager()
    if not manager.container_exists(name):
        guest = _guest(_book(config), name)
        if guest is None:
            raise CommandError(f"no such guest: {name} (see pengyos-guest list)")
        print(f"guest {name} does not exist yet; creating it (first time only)", file=out, flush=True)
        _report(manager.apply(guest, stream=True), out)
    return manager.enter(name, list(command) or None).returncode


def cmd_export(name: str, app: str, *, out=None, manager=None) -> int:
    """Write a host-side launcher for *app* in guest *name*."""
    out = out if out is not None else sys.stdout
    if manager is None:
        manager = GuestManager()
    result = manager.export(name, app)
    print(f"exported {result.app} -> {result.launcher_path}", file=out, flush=True)
    return 0


def cmd_destroy(name: str, *, out=None, manager=None) -> int:
    """Remove guest *name*'s container. Home is shared with the host and kept."""
    out = out if out is not None else sys.stdout
    if manager is None:
        manager = GuestManager()
    manager.destroy(name)
    print(f"destroyed guest {name}", file=out, flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-guest",
        description="Declarative distrobox/podman guests (PengyOS, Lane 4).",
    )
    parser.add_argument("--version", action="version", version=f"pengyos-guest {__version__}")
    parser.add_argument("--config", metavar="FILE",
                        help="read guests from FILE only (default: the image's guests.d "
                             "plus ~/.config/pengyos/guests.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_apply = sub.add_parser("apply", help="create + provision guests (all when none named)")
    p_apply.add_argument("names", nargs="*")

    p_enter = sub.add_parser("enter", help="shell in a guest, or `enter NAME -- CMD...`")
    p_enter.add_argument("name")
    p_enter.add_argument("cmd", nargs=argparse.REMAINDER)

    p_export = sub.add_parser("export", help="write a host-side launcher for a guest app")
    p_export.add_argument("name")
    p_export.add_argument("--app", required=True)

    p_destroy = sub.add_parser("destroy", help="remove a guest's container")
    p_destroy.add_argument("name")

    sub.add_parser("list", help="list declared guests")
    return parser


def main(argv: Sequence[str] | None = None, *, out=None, err=None, manager=None) -> int:
    """Entry point. Returns a process exit code."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "list":
            return cmd_list(args.config, out=out, manager=manager)
        if args.command == "apply":
            return cmd_apply(args.config, args.names, out=out, manager=manager)
        if args.command == "enter":
            cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
            return cmd_enter(args.name, cmd, args.config, out=out, manager=manager)
        if args.command == "export":
            return cmd_export(args.name, args.app, out=out, manager=manager)
        if args.command == "destroy":
            return cmd_destroy(args.name, out=out, manager=manager)
    except (CommandError, GuestError) as exc:
        print(f"error: {exc}", file=err)
        return 2

    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
