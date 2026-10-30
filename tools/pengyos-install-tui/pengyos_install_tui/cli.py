"""Command-line interface for ``pengyos-install-tui``.

    pengyos-install-tui install [--payload DIR] [--plain] [--reboot]

Exit codes: 0 installed, 1 not installed (quit, refused or failed), 2 usage.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from pengyos_install_tui import __version__


class ExitCode:
    """Process exit codes. ``USAGE`` (2) is argparse's own and reserved for it."""

    OK = 0
    ERROR = 1
    USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-install-tui",
        description="The PengyOS console installer (Core and Server).",
        epilog=(
            "Examples:\n"
            "  pengyos-install-tui install\n"
            "  pengyos-install-tui install --plain\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"pengyos-install-tui {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    install_p = sub.add_parser(
        "install",
        help="Install PengyOS from this medium onto a disk (ERASES it)",
        description=(
            "Pick a disk, a computer name, an account, SSH keys and Pengy's "
            "endpoint, confirm, and install the medium's image through the "
            "pengyos-install engine (the same path as the GUI). Full screen on "
            "a terminal of at least 80x24, line by line otherwise."
        ),
    )
    install_p.add_argument(
        "--payload", default=None,
        help="payload directory (default: the live medium's /run/pengyos/payload)",
    )
    install_p.add_argument(
        "--plain", action="store_true",
        help="ask line by line instead of drawing the full-screen installer",
    )
    install_p.add_argument(
        "--reboot", action="store_true",
        help="restart the machine after a finished full-screen install",
    )
    install_p.set_defaults(handler=_install_handler)
    return parser


def _install_handler(ns: argparse.Namespace) -> int:
    from pengyos_install_tui.install import run_install

    return run_install(payload=ns.payload, plain=True if ns.plain else None,
                       reboot=ns.reboot)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the exit code (``__main__`` exits with it)."""
    ns = build_parser().parse_args(list(argv) if argv is not None else None)
    return ns.handler(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
