"""Command-line interface for pengyos-flatpak-mirror.

The CLI is the thin, documented surface over three layers:

* :mod:`pengyos_flatpak_mirror.manifest` — load and validate ``mirror.toml``.
* :mod:`pengyos_flatpak_mirror.runner` — run ``flatpak``/``ostree`` behind a
  mockable interface (see the runner module for details).
* :mod:`pengyos_flatpak_mirror.engine` — the sync/list orchestration that ties a
  manifest to a :class:`~pengyos_flatpak_mirror.runner.Runner`.
* this module — parse argv, dispatch, and translate results into exit codes.

Subcommands: ``init``, ``sync``, ``list``, ``verify``, ``prune``. Each is
registered here so ``--help`` advertises the full documented surface.
``init`` (plus ``sync``/``list``/``verify``/``prune``) is now wired through the
runner; a not-yet-implemented body would report a clean "not implemented yet"
(exit 1) rather than a usage error or a crash.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, engine
from .manifest import ManifestError
from .runner import CommandError, Runner

# Exit codes (documented in README.md).
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# Documented subcommands, in help order.
SUBCOMMANDS = ("init", "sync", "list", "verify", "prune")


class NotYetImplemented(RuntimeError):
    """Raised by a subcommand whose body has not been implemented yet.

    The handler in :func:`main` turns this into a clean exit-1 message so the
    scaffold stays honest: the surface is real, the implementation is not.
    """


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    The parser is the single source of truth for the documented flag surface.
    Each subcommand is registered here (with its flags) even before its body is
    implemented, so ``--help`` and the parser tests stay accurate.
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-flatpak-mirror",
        description=(
            "Self-hosted Flatpak (OSTree) repo manager: mirror pinned apps, "
            "list refs + commits, verify GPG signatures, and prune old commits."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        dest="version",
        help="show the version and exit",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
    )

    # init --repo DIR
    p_init = subparsers.add_parser(
        "init",
        help="create/initialise a local OSTree repo to mirror into",
        description="Create an empty OSTree repo to mirror pinned apps into.",
    )
    p_init.add_argument(
        "--repo",
        required=True,
        metavar="DIR",
        help="path to the OSTree repo directory to (re)initialise",
    )

    # sync --manifest mirror.toml
    p_sync = subparsers.add_parser(
        "sync",
        help="mirror the pinned refs described by a manifest",
        description="Mirror the pinned refs described by a mirror manifest.",
    )
    p_sync.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="path to the mirror.toml manifest",
    )
    p_sync.add_argument(
        "--repo",
        metavar="DIR",
        help="override the repo path from the manifest",
    )

    # list
    p_list = subparsers.add_parser(
        "list",
        help="list refs and their commits in the local repo",
        description="List refs and their commits in the local repo.",
    )
    p_list.add_argument(
        "--repo",
        required=True,
        metavar="DIR",
        help="path to the OSTree repo to inspect",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="emit a deterministic machine-readable (JSON) report instead of text",
    )

    # verify --gpg-key K
    p_verify = subparsers.add_parser(
        "verify",
        help="verify the repo's GPG signatures",
        description="Verify the GPG signatures recorded in the OSTree repo.",
    )
    p_verify.add_argument(
        "--gpg-key",
        required=True,
        metavar="K",
        help="path to the trusted GPG public key (ASCII-armored)",
    )
    p_verify.add_argument(
        "--repo",
        required=True,
        metavar="DIR",
        help="path to the OSTree repo to verify",
    )

    # prune --keep N
    p_prune = subparsers.add_parser(
        "prune",
        help="keep only the N most recent commits per ref",
        description="Keep only the N most recent commits per ref and delete the rest.",
    )
    p_prune.add_argument(
        "--keep",
        type=int,
        required=True,
        metavar="N",
        help="number of most-recent commits to keep per ref",
    )
    p_prune.add_argument(
        "--repo",
        required=True,
        metavar="DIR",
        help="path to the OSTree repo to prune",
    )

    return parser


def _cmd_init(args: argparse.Namespace) -> None:
    """``init`` — create/initialise an empty OSTree repo to mirror into.

    Runs the golden argv ``flatpak build-update-repo --init <repo>`` via the
    runner and reports the target repo. A non-zero external exit surfaces as a
    clean exit 1 (``CommandError`` caught in :func:`main`), not a crash.
    """
    Runner().init_repo(args.repo)
    print(f"initialised {args.repo}")


def _cmd_sync(args: argparse.Namespace) -> None:
    """``sync`` — register the remote, mirror every pinned ref, pull each collection."""
    result = engine.sync_from_manifest(args.manifest, Runner())
    print(
        f"synced {len(result.refs_synced)} ref(s) and "
        f"{len(result.collections_pulled)} collection(s) from "
        f"remote '{result.remote_name}'"
    )
    for ref in result.refs_synced:
        print(f"  {ref}")
    for collection_id in result.collections_pulled:
        print(f"  collection: {collection_id}")


def _cmd_list(args: argparse.Namespace) -> None:
    """``list`` — print each ref and its commit from the local repo.

    Without ``--json`` this prints a short human summary (ref order preserved,
    faithful to ``ostree refs``). With ``--json`` it emits the deterministic
    machine report: ``(ref, commit)`` pairs sorted by ``(ref, commit)`` and
    serialised with ``sort_keys=True`` so two runs against the same repo are
    byte-identical.
    """
    res = Runner().list_refs(args.repo)
    if args.json:
        report = engine.list_report_dict(res.stdout, args.repo)
        print(json.dumps(report, sort_keys=True, indent=2))
        return
    parsed = engine.parse_refs(res.stdout)
    print(f"{len(parsed)} ref(s) in {args.repo}")
    for ref, commit in parsed:
        print(f"  {ref} {commit}")


def _cmd_verify(args: argparse.Namespace) -> None:
    """``verify`` — verify the repo's GPG signatures."""
    Runner().verify(args.repo, args.gpg_key)
    print(f"verify OK for {args.repo}")


def _cmd_prune(args: argparse.Namespace) -> None:
    """``prune`` — prune old deployments, keeping the N most-recent per ref."""
    res = Runner().prune(args.repo, args.keep)
    print(f"pruned {args.repo} (keep={res.keep})")


def _dispatch(args: argparse.Namespace) -> int:
    """Run the handler for the selected subcommand.

    ``init`` creates/initialises the local repo, and ``sync``, ``list``,
    ``verify``, and ``prune`` are implemented here; each returns ``EXIT_OK`` on
    success and lets :class:`ManifestError` / :class:`CommandError` escape to
    :func:`main`, which prints the message and exits 1.
    """
    if args.command == "init":
        _cmd_init(args)
        return EXIT_OK
    if args.command == "sync":
        _cmd_sync(args)
        return EXIT_OK
    if args.command == "list":
        _cmd_list(args)
        return EXIT_OK
    if args.command == "verify":
        _cmd_verify(args)
        return EXIT_OK
    if args.command == "prune":
        _cmd_prune(args)
        return EXIT_OK
    # Unreachable: argparse enforces a known command.
    raise NotYetImplemented(args.command)  # pragma: no cover


def main(args=None) -> int:
    """Entry point. Returns a process exit code.

    ``--version`` short-circuits before dispatch. A known subcommand that has
    not implemented its body yet yields a clean exit 1 with a stable message;
    unknown/missing arguments yield an argparse usage error (exit 2).
    """
    parser = build_parser()
    parsed = parser.parse_args(list(sys.argv[1:] if args is None else args))

    if parsed.version:
        print(f"pengyos-flatpak-mirror {__version__}")
        return EXIT_OK

    if not parsed.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    try:
        return _dispatch(parsed)
    except NotYetImplemented as exc:
        print(
            f"pengyos-flatpak-mirror: '{exc.args[0]}' not implemented yet",
            file=sys.stderr,
        )
        return EXIT_FAIL
    except (ManifestError, CommandError) as exc:
        # A bad manifest, or an external command that exited non-zero: print the
        # actionable message (which names the path / argv / stderr) and exit 1.
        print(f"pengyos-flatpak-mirror: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
