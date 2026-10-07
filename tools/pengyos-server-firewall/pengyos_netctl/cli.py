"""pengyos-netctl — the PengyOS server-firewall CLI.

Three subcommands (see spec.md / the README):

* ``rules`` — render a validated default-deny nftables ruleset from a
  declared-ports config.
* ``wg`` — render a ``wg0`` server config + per-peer configs from a spec,
  injecting keys from a key dir, chmod 0600, never echoing key material.
* ``validate`` — validate a rendered ruleset offline (no ``nft`` binary,
  no root, no network).

The commands are registered in :data:`COMMANDS`; each maps a name to a
``(func, help)`` pair. ``main`` dispatches to the selected command and
turns :class:`~pengyos_netctl.errors.NetctlError` into a clean ``exit 2``
message.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, Tuple

from . import __version__
from .config import load_ports_config, load_wg_spec
from .errors import NetctlError
from .nft import validate_ports_ruleset
from .render import render_ports_ruleset, write_ruleset
from .wireguard import generate as generate_wg

# command name -> (handler, help text). Handlers are added as each
# subtask lands; the scaffold ships ``validate`` only.
COMMANDS: Dict[str, Tuple[Callable[..., int], str]] = {}


def register(name: str, help_text: str) -> Callable[[Callable[..., int]], Callable[..., int]]:
    """Decorator registering a command handler under ``name``."""

    def wrapper(func: Callable[..., int]) -> Callable[..., int]:
        COMMANDS[name] = (func, help_text)
        return func

    return wrapper


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-netctl",
        description="PengyOS server-firewall: default-deny nftables ruleset "
        "+ WireGuard config generator (US-SRV-5).",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    for name, (_, help_text) in COMMANDS.items():
        sp = sub.add_parser(name, help=help_text, description=help_text)
        _add_command_args(name, sp)

    return parser


def _add_command_args(name: str, sub: argparse.ArgumentParser) -> None:
    """Add a subcommand's own ``--flag`` arguments to its parser.

    Handlers are added one subtask at a time (see the ``COMMANDS``
    registry); each may extend its subparser here. ``validate`` and
    ``rules`` ship so far.
    """
    if name == "validate":
        sub.add_argument(
            "--rules",
            required=True,
            metavar="server.nft",
            help="the rendered nftables ruleset to validate",
        )
        sub.add_argument(
            "--config",
            required=True,
            metavar="ports.yaml",
            help="the declared-ports config the ruleset must satisfy",
        )
    if name == "rules":
        sub.add_argument(
            "--config",
            required=True,
            metavar="ports.yaml",
            help="the declared-ports config to render the ruleset from",
        )
        sub.add_argument(
            "--out",
            required=True,
            metavar="server.nft",
            help="where to write the rendered ruleset (default server.nft)",
        )
    if name == "wg":
        sub.add_argument(
            "--spec",
            required=True,
            metavar="wg.yaml",
            help="the WireGuard server+peer spec to render configs from",
        )
        sub.add_argument(
            "--out",
            required=True,
            metavar="wg/",
            help="where to write wg0.conf and the per-peer configs",
        )


# ------------------------------------------------------------- commands


@register("validate", "validate a rendered ruleset (default-deny, declared ports)")
def _validate(args: argparse.Namespace) -> int:
    """Validate a rendered ruleset against a declared-ports config.

    Reads ``--rules`` (the ``.nft`` file) and ``--config`` (the
    ``ports.yaml`` whose declared ports the ruleset must expose), runs
    the offline structural/declared-ports validator, and prints a
    summary. Exits ``0`` when the ruleset is valid, ``2`` otherwise.
    """
    from .errors import NetctlError, NftValidationError  # local import

    try:
        ports = load_ports_config(args.config)
        result = validate_ports_ruleset(args.rules, ports)
    except OSError as exc:
        raise NetctlError(f"cannot read ruleset {args.rules}: {exc}") from exc
    if not result.ok:
        raise NftValidationError(
            "ruleset failed validation:\n  - "
            + "\n  - ".join(result.errors)
        )
    declared = ", ".join(
        f"{proto}/{p}" for proto in ("tcp", "udp") for p in ports.allow_list()[proto]
    )
    print(f"OK: {args.rules} is default-deny and exposes only the declared ports ({declared})")
    return 0


@register("rules", "render a validated default-deny nftables ruleset")
def _rules(args: argparse.Namespace) -> int:
    """Render a validated default-deny ruleset and write it to ``--out``.

    Reads ``--config`` (the declared-ports config), renders the ruleset
    with :func:`~pengyos_netctl.render.render_ports_ruleset`, validates the
    rendered text (it must pass, or we raise), and writes it to ``--out``.
    Exits ``0`` on success, ``2`` on any error.
    """
    from .errors import NftValidationError  # local import

    ports = load_ports_config(args.config)
    try:
        ruleset = render_ports_ruleset(ports)
    except RuntimeError as exc:
        # The renderer is built to satisfy the policy; a RuntimeError here
        # means the policy changed and the renderer must be fixed.
        raise NftValidationError(str(exc)) from exc
    try:
        write_ruleset(args.out, ruleset)
    except OSError as exc:
        raise NetctlError(f"cannot write ruleset {args.out}: {exc}") from exc
    declared = ", ".join(
        f"{proto}/{p}"
        for proto in ("tcp", "udp")
        for p in ports.allow_list()[proto]
    )
    print(f"OK: wrote {args.out} (default-deny, exposed ports: {declared})")
    return 0


@register("wg", "render a wg0 server config + per-peer configs (keys 0600)")
def _wg(args: argparse.Namespace) -> int:
    """Render ``wg0.conf`` + per-peer configs from ``--spec`` into ``--out``.

    Private keys are read from (or generated into) the spec's ``key_dir``
    at mode ``0600`` and injected only into the config that belongs to each
    key; nothing is echoed to stdout. Exits ``0`` on success, ``2`` on any
    error.
    """
    spec = load_wg_spec(args.spec)
    output = generate_wg(spec, args.out)
    peers = ", ".join(output.peer_configs) or "(none)"
    print(
        f"OK: wrote {args.out}/wg0.conf + {peers} "
        f"(keys chmod 0600 in {spec.key_dir})"
    )
    return 0


def main(argv=None) -> int:
    """Entry point: parse args, dispatch, translate errors to exit codes."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    func, _ = COMMANDS[args.command]
    try:
        return func(args)
    except NetctlError as exc:
        print(f"pengyos-netctl: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
