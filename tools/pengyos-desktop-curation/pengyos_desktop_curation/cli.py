"""Command-line interface for pengyos-desktop-curation.

The CLI is the thin, documented surface over the curated default-app set stored
as declarative data (see ``pengyos_desktop_curation.data``).

Subcommands: ``list``, ``coverage``, ``emit``, ``lint``. Each is registered
here so ``--help`` advertises the full documented surface. A body that is not
implemented reports a clean "not implemented yet" (exit 1) rather than a
usage error or a crash.

* :mod:`pengyos_desktop_curation.data` — the committed curated set
  (category -> app -> lane -> ref -> rationale -> human_signed).
* ``list`` — print the curated set (``--json`` for a machine report).
* ``coverage`` — report gaps against the US-DSK-2 everyday list.
* ``emit`` — write the Flatpak ref list + mirror manifest to ``--out DIR``.
* ``lint`` — enforce the human-artistry rule (every entry has a rationale and is
  ``human_signed``; spec Principle 8).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import coverage as _coverage
from . import emit as _emit
from . import mime as _mime
from . import resolve as _resolve
from . import lint as _lint
from .data import load_default
from .emit import EmitResult

# Exit codes (documented in README.md).
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# Documented subcommands, in help order.
SUBCOMMANDS = ("list", "coverage", "emit", "lint")


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
        prog="pengyos-desktop-curation",
        description=(
            "Curated PengyOS Desktop default-app set as declarative data: "
            "list the curated set, report coverage against the US-DSK-2 "
            "everyday list, and emit a Flatpak ref list + mirror manifest."
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

    # list [--json]
    p_list = subparsers.add_parser(
        "list",
        help="print the curated default-app set",
        description="Print the curated default-app set.",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="emit a machine-readable (JSON) report instead of text",
    )

    # coverage [--json]
    p_coverage = subparsers.add_parser(
        "coverage",
        help="report coverage against the US-DSK-2 everyday list",
        description=(
            "Report coverage against the US-DSK-2 'everyday apps covered' list "
            "(browser, terminal, files, editor, media, office, PDF, archive, "
            "calculator, screenshots)."
        ),
    )
    p_coverage.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="emit a machine-readable (JSON) report instead of text",
    )

    # emit --out DIR [--json]
    p_emit = subparsers.add_parser(
        "emit",
        help="write the Flatpak ref list + mirror manifest to DIR",
        description=(
            "Emit the Flatpak ref list and the self-hosted-mirror manifest "
            "(for pengyos-flatpak-mirror) into --out DIR."
        ),
    )
    p_emit.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="directory to write the emitted artifacts into",
    )
    p_emit.add_argument(
        "--pin",
        action="store_true",
        dest="pin",
        help=(
            "resolve the UNPINNED commit placeholder to a real Flathub commit "
            "(from flathub-commits.toml) before writing, so the manifest is "
            "sync-ready; without it the manifest is a seed (commit = UNPINNED)"
        ),
    )
    p_emit.add_argument(
        "--refresh",
        action="store_true",
        dest="refresh",
        help=(
            "with --pin, fetch each ref's current commit live from Flathub "
            "(network) instead of the committed database; fails without network "
            "for refs not already in the database"
        ),
    )
    p_emit.add_argument(
        "--mime",
        action="store_true",
        dest="mime",
        help=(
            "also emit the .desktop/mime integration plan: a mimeapps.list (xdg-mime defaults) and the machine-readable "
            "integration-plan.json, from mime-integration.toml"
        ),
    )
    p_emit.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="emit a machine-readable (JSON) summary instead of text",
    )

    # lint [--json]
    p_lint = subparsers.add_parser(
        "lint",
        help="enforce the human-artistry rule (rationale + human_signed)",
        description=(
            "Enforce the human-artistry rule: every curated entry must carry a "
            "non-empty rationale and be human_signed (spec Principle 8). A clean "
            "set passes (exit 0); any violation fails (exit 1)."
        ),
    )
    p_lint.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="emit a machine-readable (JSON) report instead of text",
    )

    return parser


def _cmd_list(args: argparse.Namespace) -> None:
    """``list`` — print the curated set.

    Not implemented yet: the committed data lives in
    ``pengyos_desktop_curation.data``.
    """
    raise NotYetImplemented("list")


def _cmd_coverage(args: argparse.Namespace) -> None:
    """``coverage`` — report gaps against the everyday list.

    A missing everyday category is a reported gap (exit 1), not a crash; a
    complete set passes (exit 0).
    """

    curated = load_default()
    report = _coverage.build_report(curated)

    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())

    # A coverage gap is a failure (README exit-code contract), not a crash.
    return EXIT_OK if report.is_complete else EXIT_FAIL


def _cmd_lint(args: argparse.Namespace) -> int:
    """``lint`` — enforce the human-artistry rule.

    Every curated entry must carry a non-empty ``rationale`` and be
    ``human_signed = true`` (spec Principle 8). A clean set passes (exit 0); a
    set with any artistry-rule violation fails (exit 1) and prints the report.
   
    """

    curated = load_default()
    report = _lint.lint(curated)

    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())

    # An artistry-rule violation is a failure (exit 1), not a crash.
    return EXIT_OK if report.is_clean else EXIT_FAIL


def _cmd_emit(args: argparse.Namespace) -> int:
    """``emit`` — write the Flatpak ref list + mirror manifest into ``--out``.

    Writes two deterministic artifacts:

    * ``flatpak-refs.txt`` — the Flatpak ref list (one ref per line), and
    * ``mirror.toml`` — the mirror manifest for ``pengyos-flatpak-mirror``.

    By default the manifest is a **seed**: every app is pinned to the literal
    placeholder ``UNPINNED`` (the curated set carries Flathub refs but no pinned
    commits). With ``--pin`` the placeholder is resolved to a real Flathub commit
    from the committed ``flathub-commits.toml`` so the
    manifest is sync-ready (the mirror tool rejects the ``UNPINNED`` marker).
    ``--refresh`` (with ``--pin``) fetches each ref's current commit live from
    Flathub instead. Exit 0 on success, 1 if writing or resolving fails.

    With ``--refresh`` the tool does more than resolve: it fetches every curated
    ref's current Flathub commit live, merges those into the committed
    ``flathub-commits.toml`` database, and **persists it back** — so the committed database self-updates with real Flathub commits
    instead of requiring a manual rewrite. The merged (fresh) DB is then what
    resolves the manifest. A ref whose fetched commit is not a real 64-hex
    commit fails loudly (exit 1) *before* anything is written, so a bad fetch
    never corrupts the committed database.

    With ``--mime`` the tool additionally emits the
    .desktop/mime integration plan from the committed ``mime-integration.toml``:
    a ``mimeapps.list`` (the freedesktop ``xdg-mime`` default-associations file)
    and the machine-readable ``integration-plan.json``.
    """

    curated = load_default()
    result = _emit.emit(curated)

    manifest_toml = result.manifest_toml
    # When --refresh, the merged DB (fresh live values) is used to resolve the
    # manifest, and its last_verified is reported.
    refreshed_last_verified: str | None = None
    if args.pin:
        try:
            if args.refresh:
                # fetch every curated ref live, merge it
                # into the committed DB, and persist it back (self-update the
                # committed commit database with real Flathub commits). The
                # merged DB then resolves the manifest to fresh values.
                refreshed_db = _resolve.refresh(
                    curated, fetcher=_resolve.fetch_commit
                )
                refreshed_last_verified = refreshed_db.last_verified
                manifest_toml = _resolve.resolve_manifest(
                    manifest_toml, refreshed_db
                )
            else:
                manifest_toml = _resolve.resolve_manifest(manifest_toml)
        except _resolve.ResolveError as exc:
            print(f"pengyos-desktop-curation: {exc}", file=sys.stderr)
            return EXIT_FAIL

    base_result = EmitResult(
        flatpak_refs=result.flatpak_refs,
        manifest_toml=manifest_toml,
    )
    # with --mime, also emit the .desktop/mime integration
    # plan (mimeapps.list + integration-plan.json) from the committed plan.
    mime_plan = _mime.load_default() if args.mime else None
    mime_result = _emit.emit_mime(mime_plan)
    result = EmitResult(
        flatpak_refs=base_result.flatpak_refs,
        manifest_toml=base_result.manifest_toml,
        mimeapps_list=mime_result.mimeapps_list,
        integration_plan_json=mime_result.integration_plan_json,
        mime_artifacts=mime_result.mime_artifacts,
    )
    written = result.write(args.out)

    if args.json:
        print(
            json.dumps(
                {
                    "flatpak_refs": result.flatpak_refs.splitlines(),
                    "manifest_toml": result.manifest_toml,
                    "artifacts": [p.name for p in written],
                    "mimeapps_list": result.mimeapps_list,
                    "integration_plan_json": result.integration_plan_json,
                    "mime_artifacts": list(result.mime_artifacts),
                    "pinned": bool(args.pin),
                    "refreshed": bool(args.refresh),
                    "last_verified": refreshed_last_verified,
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"emitted {len(written)} artifact(s) to {args.out}:"
        )
        for path in written:
            print(f"  {path.name}")
        refs = len(result.flatpak_refs.splitlines())
        if args.pin:
            print(
                f"  {refs} Flatpak ref(s); mirror manifest is PINNED "
                f"(real Flathub commits; sync-ready)"
                + (" via --refresh (live)" if args.refresh else "")
            )
            if refreshed_last_verified:
                print(
                    f"  updated committed flathub-commits.toml "
                    f"(last_verified={refreshed_last_verified})"
                )
        else:
            print(
                f"  {refs} Flatpak ref(s); mirror manifest pins each to "
                f"'{_emit.UNPINNED_COMMIT}' (resolve with --pin; the mirror "
                f"tool resolves it at sync time)"
            )
        if args.mime:
            print(
                "  emitted the .desktop/mime integration plan "
                "(mimeapps.list + integration-plan.json) from "
                "mime-integration.toml"
            )

    return EXIT_OK


# The dispatch table maps each documented subcommand to its handler.
_HANDLERS = {
    "list": _cmd_list,
    "coverage": _cmd_coverage,
    "emit": _cmd_emit,
    "lint": _cmd_lint,
}


def _dispatch(args: argparse.Namespace) -> int:
    """Run the handler for the selected subcommand; return its exit code.

    A not-yet-implemented body raises :class:`NotYetImplemented`, which
    :func:`main` turns into a clean exit 1. The handler itself returns the
    process exit code (e.g. ``coverage`` returns ``EXIT_FAIL`` on a gap).
    """
    return _HANDLERS[args.command](args)


def main(args=None) -> int:
    """Entry point. Returns a process exit code.

    ``--version`` short-circuits before dispatch. A known subcommand that has
    not implemented its body yet yields a clean exit 1 with a stable message;
    unknown/missing arguments yield an argparse usage error (exit 2).
    """
    parser = build_parser()
    parsed = parser.parse_args(list(sys.argv[1:] if args is None else args))

    if parsed.version:
        print(f"pengyos-desktop-curation {__version__}")
        return EXIT_OK

    if not parsed.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    try:
        return _dispatch(parsed)
    except NotYetImplemented as exc:
        print(
            f"pengyos-desktop-curation: '{exc.args[0]}' not implemented yet",
            file=sys.stderr,
        )
        return EXIT_FAIL
