"""Command-line interface for ``pengyos-media``.

Interface (spec.md):

    pengyos-media plan    --edition desktop --release rel.json --out media.json [--json]
    pengyos-media build   --plan media.json [--dry-run|--commit] [--runner CMD]
    pengyos-media lint    --plan media.json [--json]

All three subcommands are implemented: ``plan`` (edition + release -> the
medium's layout), ``build`` (drive the plan through the injectable runner), and
``lint`` (validate a plan against the medium's required parts). :class:
`NotYetImplementedError` is retained for the documented exit-code contract
(``NOT_IMPLEMENTED`` == 3), which :func:`main` turns into that exit code; a
future subcommand that is not yet a real body raises it.

* ``plan`` (implemented) turns an edition + release into a validated,
  serialisable plan of the medium's layout (via :mod:`pengyos_media.plan` and
  :mod:`pengyos_media.model`); ``--json`` for the machine-readable form.
* ``build`` drives the plan through an injectable runner; ``--dry-run`` renders
  the argv and runs nothing.
* ``lint`` validates a medium plan, rejecting one that omits the installer, a
  boot entry, the release, or a correct os-release edition id.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Sequence

from pengyos_media import __version__
from pengyos_media.model import MediumPlan, render_plan_text
from pengyos_media.plan import PlanBuildError, build_plan, read_release_manifest
from pengyos_media.validate import PlanError

#: Editions the medium can be built for (spec.md + pengyos-live).
EDITIONS = ("desktop", "core", "server")


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
        super().__init__(
            f"'{command}' is not implemented yet: {task}"
        )
        self.command = command
        self.task = task


Handler = Callable[[argparse.Namespace], int]


def _run_plan(ns: argparse.Namespace) -> int:
    """``plan`` handler: edition + release -> a validated, serialisable plan.

    Reads the release manifest, builds the plan, writes it to ``--out`` when
    given, and prints it (JSON with ``--json``, otherwise a human-readable
    summary). A build/validation failure is a hard error (exit 1, reason on
    stderr, no plan written/emitted).
    """
    from pathlib import Path

    try:
        release = read_release_manifest(ns.release)
        plan = build_plan(ns.edition, release)
    except PlanBuildError as exc:
        print(f"pengyos-media: {exc}", file=sys.stderr)
        return ExitCode.ERROR

    if ns.out:
        try:
            Path(ns.out).write_text(plan.to_json(), encoding="utf-8")
        except OSError as exc:
            print(
                f"pengyos-media: cannot write plan to {ns.out}: {exc}",
                file=sys.stderr,
            )
            return ExitCode.ERROR

    if ns.json:
        print(plan.to_json(), end="")
    else:
        # The layout (a serialisable value) plus the ordered build argv that
        # turns it into a medium (item 3): every step's argv is emitted.
        from pengyos_media.steps import build_steps, render_build_steps_text

        print(render_plan_text(plan))
        print()
        print(render_build_steps_text(build_steps(plan)))
    return ExitCode.OK


def _run_build(ns: argparse.Namespace) -> int:
    """``build`` handler: drive the plan through the injectable runner.

    Loads the plan, resolves the runner (``--runner CMD`` wraps a command name
    or path around each step; otherwise the real subprocess runner), and drives
    the build. ``--commit`` actually runs the steps; without it (the default)
    ``build`` is a **side-effect-free dry run** that only renders the argv. A
    missing/unreadable/invalid plan is a hard error (exit 1).
    """
    from pengyos_media.build import BuildError, build, load_plan, render_build_plan_text
    from pengyos_media.runner import default_runner, wrap_runner

    # --runner CMD wraps each step's argv with a command (a recorder, sudo, a
    # tracer…); omit it to use the real subprocess runner. In dry-run the runner
    # is never called, so --runner only matters for --commit.
    runner = wrap_runner(ns.runner) if ns.runner else default_runner

    # Default to a side-effect-free dry run; --commit actually runs the steps.
    try:
        plan = load_plan(ns.plan)
        outcome = build(plan, runner=runner, dry_run=not ns.commit)
    except BuildError as exc:
        print(f"pengyos-media: {exc}", file=sys.stderr)
        return ExitCode.ERROR

    print(render_build_plan_text(outcome))
    return ExitCode.OK


def _run_lint(ns: argparse.Namespace) -> int:
    """``lint`` handler: validate a medium plan.

    Loads the plan emitted by ``plan`` and runs it through
    :func:`pengyos_media.validate.validate_plan` (the single source of truth for
    "a well-formed plan"). A plan that cannot be read/parsed (missing/unreadable
    file, bad JSON, not a plan object) is a hard error (exit 1); a plan that
    ``validate_plan`` rejects is a FAILED verdict naming every problem (exit 1);
    a clean plan is an OK verdict (exit 0). ``--json`` emits the
    machine-readable verdict; otherwise a short human line is printed.
    """
    from pengyos_media.build import BuildError, load_plan
    from pengyos_media.validate import validate_plan

    try:
        plan = load_plan(ns.plan)
    except BuildError as exc:
        print(f"pengyos-media: {exc}", file=sys.stderr)
        return ExitCode.ERROR

    problems: list[PlanError] = validate_plan(plan)
    if ns.json:
        print(json.dumps(verdict_to_dict(plan, problems), indent=2, sort_keys=True))
    else:
        print(verdict_to_text(plan, problems))
    return ExitCode.OK if not problems else ExitCode.ERROR


def verdict_to_text(plan: MediumPlan, problems: list["PlanError"]) -> str:
    """A human-readable lint verdict (OK, or a numbered list of problems)."""
    if not problems:
        return f"lint: OK — {plan.kind.value} plan for edition '{plan.edition.value}' is well-formed"
    noun = "problem" if len(problems) == 1 else "problems"
    lines = [f"lint: FAILED — {len(problems)} {noun}:"]
    for error in problems:
        lines.append(f"  - {error.code}: {error.message}")
    return "\n".join(lines)


def verdict_to_dict(plan: MediumPlan, problems: list["PlanError"]) -> dict[str, Any]:
    """The machine-readable lint verdict: validity, the plan, and its problems."""
    return {
        "valid": not problems,
        "plan": plan.to_dict(),
        "problems": [{"code": e.code, "message": e.message} for e in problems],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``pengyos-media`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-media",
        description=(
            "Build the PengyOS live install medium (ISO/disk image) for an "
            "edition: the live rootfs, the installer, and a boot entry, as an "
            "explicit plan plus an injectable executor."
        ),
        epilog=(
            "Examples:\n"
            "  pengyos-media plan    --edition desktop --release rel.json --out media.json\n"
            "  pengyos-media build   --plan media.json --dry-run\n"
            "  pengyos-media lint    --plan media.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pengyos-media {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    plan_p = sub.add_parser(
        "plan",
        help="Turn an edition + release into the medium's layout and build argv",
        description=(
            "Turn an edition + release into the medium's layout (ESP, live "
            "squashfs/rootfs, embedded installer, boot entry) and the ordered, "
            "deterministic build argv. Pure: it never runs any tool. Use --json "
            "for the machine-readable form."
        ),
    )
    plan_p.add_argument(
        "--edition",
        required=True,
        choices=EDITIONS,
        help="Edition the medium is built for (e.g. desktop)",
    )
    plan_p.add_argument(
        "--release",
        required=True,
        help="Path to the edition release manifest (JSON)",
    )
    plan_p.add_argument(
        "--out",
        help="Path to write the emitted medium plan (JSON). Omit to print only.",
    )
    plan_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable plan (JSON) instead of the numbered list",
    )
    plan_p.set_defaults(handler=_run_plan)

    build_p = sub.add_parser(
        "build",
        help="Drive the plan through the injectable runner",
        description=(
            "Build the medium described by a plan file. Runs the planned "
            "commands through the command runner; --dry-run renders the argv "
            "and runs nothing; --commit actually runs it."
        ),
    )
    build_p.add_argument(
        "--plan",
        required=True,
        help="Path to the medium plan emitted by `plan`",
    )
    build_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the build argv and run nothing (side-effect free)",
    )
    build_p.add_argument(
        "--commit",
        action="store_true",
        help="Actually run the build (without --dry-run)",
    )
    build_p.add_argument(
        "--runner",
        default=None,
        help="Override the command runner (a command name or path)",
    )
    build_p.set_defaults(handler=_run_build)

    lint_p = sub.add_parser(
        "lint",
        help="Validate a medium plan (installer, boot entry, release, os-release)",
        description=(
            "Validate a medium plan emitted by `plan`: the medium must carry "
            "the installer, a boot entry, the release, and an os-release that "
            "names the edition. --json for the machine-readable verdict."
        ),
    )
    lint_p.add_argument(
        "--plan",
        required=True,
        help="Path to the medium plan emitted by `plan`",
    )
    lint_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable verdict (JSON) instead of the human text",
    )
    lint_p.set_defaults(handler=_run_lint)

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
        print(f"pengyos-media: {exc}", file=sys.stderr)
        return ExitCode.NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
