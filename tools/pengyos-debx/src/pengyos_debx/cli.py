"""Command-line interface for pengyos-debx.

This CLI intentionally NEVER executes a package's maintainer scripts. It only:

* ``extract``  -- extract a deb/rpm payload to an isolated install prefix.
* ``analyse``  -- analyse ``DT_NEEDED`` of the extracted tree by pure ELF
                  parsing (never ``ldd``, which can execute).
* ``list-scripts`` -- list maintainer scripts for inspection; they are never
                  run.

``extract`` and ``list-scripts`` are implemented (see
:mod:`pengyos_debx.extract` and :mod:`pengyos_debx.scripts`). The remaining
handlers that are not yet implemented raise
:class:`NotYetImplementedError` (which the CLI turns into exit code ``3``).
This is an honest placeholder, not a stub that pretends to work.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict

from . import __version__
from .analyse import analyse_path
from .extract import EXIT_PATH_ESCAPE, PathEscapeError, extract_path
from .scripts import format_listing, list_scripts_from_path

# Exit codes:
#   0  success (or help printed with no subcommand)
#   1  usage / unexpected error
#   2  argparse usage error
#   3  feature not yet implemented
#   5  payload tried to escape the install prefix (path traversal)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_IMPLEMENTED = 3
EXIT_PATH_ESCAPE = 5


class NotYetImplementedError(RuntimeError):
    """Raised by a subcommand whose implementation has not been written yet."""


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="pengyos-debx",
        description=(
            "Bounded deb/rpm extractor for PengyOS. Extract to an isolated "
            "prefix, list maintainer scripts, analyse DT_NEEDED. Never "
            "executes maintainer scripts."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="<command>", required=True
    )

    # extract: read the payload (ar+tar deb, cpio/zstd rpm) and write it into
    # an isolated install prefix.
    p_extract = subparsers.add_parser(
        "extract",
        help="extract a deb/rpm payload into an isolated install prefix",
        description=(
            "Extract a deb or rpm payload into an isolated install prefix. "
            "Maintainer scripts are never executed."
        ),
    )
    p_extract.add_argument(
        "pkg",
        metavar="pkg",
        help="path to a .deb or .rpm package",
    )
    p_extract.add_argument(
        "--into",
        dest="into",
        required=True,
        metavar="PREFIX",
        help="isolated install prefix to extract into",
    )

    # analyse: pure ELF parsing of DT_NEEDED against a set of libdirs.
    p_analyse = subparsers.add_parser(
        "analyse",
        help="analyse DT_NEEDED of an extracted prefix (pure ELF parsing)",
        description=(
            "Analyse the DT_NEEDED entries of the ELF files under an extracted "
            "prefix and report resolved vs missing libraries. Uses pure ELF "
            "parsing; never invokes ldd."
        ),
    )
    p_analyse.add_argument(
        "path",
        metavar="path",
        help="extracted install prefix (or a single ELF file) to analyse",
    )
    p_analyse.add_argument(
        "--libdirs",
        dest="libdirs",
        nargs="*",
        default=None,
        metavar="DIR",
        help="directories to resolve DT_NEEDED against (e.g. /lib /usr/lib)",
    )

    # list-scripts: SHOW maintainer scripts and explicitly refuse to run them.
    p_scripts = subparsers.add_parser(
        "list-scripts",
        help="list maintainer scripts without executing them",
        description=(
            "List the maintainer scripts (preinst/postinst/prerm/postrm for deb, "
            "pre/preun/post/uninstall for rpm) contained in a package. Scripts "
            "are shown, NEVER executed."
        ),
    )
    p_scripts.add_argument(
        "pkg",
        metavar="pkg",
        help="path to a .deb or .rpm package",
    )

    return parser


def _cmd_extract(args: argparse.Namespace) -> int:
    """Extract a deb/rpm payload into an isolated install prefix.

    Maintainer scripts are never executed: only the payload tarball is read.
    """
    result = extract_path(args.pkg, args.into)
    print(result.summary())
    return EXIT_OK


def _cmd_analyse(args: argparse.Namespace) -> int:
    """Analyse ``DT_NEEDED`` of an extracted prefix (or single ELF file).

    Pure ELF parsing — never ``ldd``. Each object's declared ``DT_NEEDED`` libs
    are resolved against the ``--libdirs`` set into a resolved-vs-missing report
    (see :func:`pengyos_debx.analyse.analyse_path`). A lib is *resolved* iff a
    file of that exact name exists in a libdir (a pure filesystem existence
    check — nothing is executed); otherwise it is *missing*.
    """
    report = analyse_path(args.path, libdirs=args.libdirs)
    print(report.format())
    return EXIT_OK


def _cmd_list_scripts(args: argparse.Namespace) -> int:
    """List a package's maintainer scripts for inspection.

    The scripts are read and printed; they are **never** executed. Any error
    reading the package (bad magic, truncated archive, …) is reported and turns
    into exit code ``1``.
    """
    listing = list_scripts_from_path(args.pkg)
    print(listing.format())
    return EXIT_OK


_HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "extract": _cmd_extract,
    "analyse": _cmd_analyse,
    "list-scripts": _cmd_list_scripts,
}


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args and dispatch to the subcommand handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS[args.command]
    try:
        return handler(args)
    except NotYetImplementedError as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    except PathEscapeError as exc:
        # A payload tried to write outside the install prefix.
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_PATH_ESCAPE
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
