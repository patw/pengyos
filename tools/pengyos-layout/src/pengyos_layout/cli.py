"""Command-line interface for the pengyos-layout planner.

The CLI is the thin, shell-facing layer over the three planner capabilities:

* ``plan`` — a target probe + edition/options → an ordered, golden layout
  written to ``plan.json`` (the data the executor consumes).
* ``validate`` — check a written plan against the layout invariants.
* ``emit`` — the ``sgdisk``/``mkfs``/``mount`` argv for the executor.

All three subcommands are implemented (see :mod:`pengyos_layout.emit` for the
emitter). ``plan`` lands first (reads a probe JSON, builds :class:`Options`,
calls :func:`~pengyos_layout.plan.plan`, and writes the resulting
:class:`~pengyos_layout.model.Plan` back out as JSON), ``validate`` second: it
reads a plan JSON, runs :class:`~pengyos_layout.validator.Validator`, and exits
non-zero listing every invariant violation, and ``emit`` third: it reads a plan
JSON and emits the ordered executor argv.

Exit codes: ``0`` on success, ``2`` on a usage error, ``1`` when a plan is
invalid or planning fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import __version__
from .model import Options, Plan, TargetProbe, ValidationError
from .plan import plan
from .validator import Validator


def _load_probe(path: str) -> TargetProbe:
    with open(path, encoding="utf-8") as fh:
        return TargetProbe.from_dict(json.load(fh))


def _dump_plan(plan: Plan) -> str:
    return json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n"


def _load_plan(path: str) -> Plan:
    """Load a :class:`~pengyos_layout.model.Plan` from a plan JSON file."""
    with open(path, encoding="utf-8") as fh:
        return Plan.from_dict(json.load(fh))


def _cmd_plan(args: argparse.Namespace) -> int:
    """``plan`` — derive a golden layout and write it to disk/stdout."""
    probe = _load_probe(args.target)

    options = Options(
        luks=args.luks,
        swap_size=args.swap_size,
        edition=args.edition,
    )

    try:
        computed = plan(probe, options)
    except Exception as exc:  # LayoutError: too-small disk, bad swap, ...
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = _dump_plan(computed)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        if not args.quiet:
            print(f"wrote {computed.total_size} bytes of layout to {args.out}")
    else:
        sys.stdout.write(text)

    # A golden layout must also be a valid one; surface any invariant breach.
    from .validator import Validator

    try:
        Validator().validate(computed)
    except ValidationError as exc:
        print("error: golden plan is invalid:", file=sys.stderr)
        for message in exc.messages:
            print(f"  - {message}", file=sys.stderr)
        return 1

    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """``validate`` — check a written plan against the layout invariants.

    Runs the :class:`~pengyos_layout.validator.Validator` over the plan and:

    * exits ``0`` and reports ``OK`` when the layout is valid;
    * exits ``1`` and lists every violation (one per line) when it is not —
      this is where the planner refuses a too-small disk, overlapping or
      misaligned partitions, asymmetric A/B slots, a missing/non-VAT32 ESP, or
      the live boot device;
    * exits ``2`` on a usage/IO error (missing file, malformed JSON).

    With ``--json`` the human lines are replaced by a structured object
    ``{"valid": bool, "violations": [...]}``.
    """
    try:
        plan = _load_plan(args.plan)
    except FileNotFoundError:
        print(f"error: plan file not found: {args.plan}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: cannot parse plan {args.plan}: {exc}", file=sys.stderr)
        return 2

    violations = Validator().violations(plan)
    if violations:
        if args.json:
            print(json.dumps({"valid": False, "violations": violations}))
        else:
            print(f"INVALID: {args.plan}", file=sys.stderr)
            for message in violations:
                print(f"  - {message}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"valid": True, "violations": []}))
    else:
        print(
            f"OK: {args.plan} is a valid layout "
            f"({len(plan.partitions)} partitions, {plan.total_size} bytes)"
        )
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    """``emit`` — emit the executor argv for a plan.

    Reads a plan JSON and produces the ordered executor pipeline: for every
    partition, ``sgdisk`` create (at the plan's byte offset) + unique GUID, then
    ``cryptsetup`` luksFormat/open for any LUKS-wrapped slot, then ``mkfs``/
    ``mkswap`` on the partition (or decrypted mapper), then ``mount`` for every
    slot with a mountpoint. Each command is a shell-quotable ``argv`` (safe to
    pass straight to ``subprocess.run`` with no shell).

    Without ``--json`` the argv is printed one per line (``shlex.join``), ready
    to paste into a script. With ``--json`` the whole pipeline is emitted as a
    structured object ``{"steps": [{"kind", "argv", "description"}, ...]}``.

    Exits ``0`` on success, ``2`` on a usage/IO error (missing file, malformed
    JSON).
    """
    try:
        plan = _load_plan(args.plan)
    except FileNotFoundError:
        print(f"error: plan file not found: {args.plan}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: cannot parse plan {args.plan}: {exc}", file=sys.stderr)
        return 2

    from .emit import emit

    steps = emit(plan)
    if args.json:
        print(json.dumps({"steps": [s.to_dict() for s in steps]}))
    else:
        for step in steps:
            print(step.render())
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-layout",
        description="Declarative disk/layout planner for PengyOS installs.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_plan = sub.add_parser(
        "plan", help="derive a golden layout from a target probe"
    )
    p_plan.add_argument(
        "--target",
        required=True,
        metavar="PROBE.json",
        help="JSON target probe (see TargetProbe.to_dict)",
    )
    p_plan.add_argument(
        "--edition",
        default="server",
        help="edition label recorded in the plan (default: server)",
    )
    p_plan.add_argument(
        "--luks",
        action="store_true",
        help="wrap every root/data slot in LUKS2",
    )
    p_plan.add_argument(
        "--swap-size",
        type=int,
        default=0,
        metavar="BYTES",
        help="append a swap slot of this many bytes (0 = none)",
    )
    p_plan.add_argument(
        "--out",
        metavar="PLAN.json",
        help="write the golden plan here (default: stdout)",
    )
    p_plan.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the human-readable summary line",
    )
    p_plan.set_defaults(func=_cmd_plan)

    p_validate = sub.add_parser(
        "validate",
        help="check a plan against the layout invariants (sizes, alignment, "
        "ESP bounds, A/B symmetry, live-device refusal)",
    )
    p_validate.add_argument(
        "--plan", required=True, metavar="PLAN.json", help="plan to validate"
    )
    p_validate.add_argument(
        "--json",
        action="store_true",
        help="emit a structured {valid, violations} object instead of text",
    )
    p_validate.set_defaults(func=_cmd_validate)

    p_emit = sub.add_parser(
        "emit",
        help="emit the executor argv for a plan (sgdisk/mkfs/cryptsetup/mount)",
    )
    p_emit.add_argument(
        "--plan", required=True, metavar="PLAN.json", help="plan to emit"
    )
    p_emit.add_argument(
        "--json",
        action="store_true",
        help="emit a structured {steps: [...]} object instead of argv lines",
    )
    p_emit.set_defaults(func=_cmd_emit)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
