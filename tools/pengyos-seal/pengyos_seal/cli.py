"""Command-line interface for ``pengyos-seal``.

The provenance-closing glue for US-CORE-6. Two subcommands:

``plan``
    Enumerate the seal plan for a pengyos-build output: discover the real
    artifact/SBOM names in ``--build`` (reusing the witness-bundle alias
    convention) and print the ordered steps the seal will take. Pure — no side
    effects. The full deterministic plan model is the follow-up item; ``plan``
    here validates the build dir and reports the plan is ready.

``run``
    Execute the seal: sign the expected artifacts, normalise the SBOM to the
    verifier's conventional root name, surface the repro report, build the
    witness layout, and run ``pengyos-verifier`` — surfacing its verdict and
    exit code. Every external tool call goes through the injected runner seam
    (:mod:`.runner`); the seal stages themselves live in :mod:`.seal`.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, plan, runner, seal
from .runner import Runner

# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # success
EXIT_FAIL: int = 1  # an operation ran but reported a failure (clean, no traceback)
EXIT_USAGE: int = 2  # bad/missing arguments

# --- Documented interface defaults -----------------------------------------
# The verifier's conventional SBOM root name (see pengyos-verifier checks.py:
# SBOM_FILENAMES[0]); the seal normalises the build's images/<target>.spdx.json
# to this path so the witness can find it.
SBOM_ROOT_FILENAME: str = "sbom.spdx.json"
# The expected artifacts the seal signs (see pengyos-verifier checks.py:
# EXPECTED_ARTIFACTS, M1 board layout).
EXPECTED_ARTIFACTS: tuple[str, ...] = ("images/rootfs.ext4", "images/bzImage")
# The pengyos-sig-v1 signature suffix the verifier expects as a sibling.
SIGNATURE_SUFFIX: str = ".sig"
# The verifier's conventional repro-report root name (see
# pengyos-verifier checks.py: REPRO_REPORT_FILENAME); the seal surfaces the
# build's repro report to this path so the witness can find it.
REPRO_REPORT_FILENAME: str = "repro-report.json"


class SealError(Exception):
    """A user-facing seal failure.

    Raised for every clean, non-crash failure (a missing build dir, a missing
    artifact to sign, a missing key file). The CLI maps it to a one-line
    ``pengyos-seal: error: ...`` message on stderr and exit 1 — never a raw
    traceback.
    """


# --- Argument surface -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-seal",
        description=(
            "Wire provenance into a pengyos-build output: sign the expected "
            "artifacts, normalise the SBOM to the verifier's conventional root "
            "name, surface the repro report, build the witness layout, and run "
            "pengyos-verifier."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Enumerate the seal plan for a build dir (no side effects).",
        description="Enumerate the seal plan for a pengyos-build output dir.",
    )
    plan_parser.add_argument(
        "--build",
        required=True,
        metavar="DIR",
        help="The pengyos-build output dir to seal.",
    )
    plan_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the seal plan as JSON instead of a one-line summary.",
    )
    plan_parser.set_defaults(func=cmd_plan)

    run_parser = subparsers.add_parser(
        "run",
        help="Seal a build dir: sign, name, repro, witness, verify.",
        description=(
            "Seal a pengyos-build output: sign the expected artifacts with "
            "pengyos-sign, normalise the SBOM to the verifier's conventional "
            "root name, surface the repro report, build the witness layout with "
            "pengyos-witness-bundle, and run pengyos-verifier."
        ),
    )
    run_parser.add_argument(
        "--build",
        required=True,
        metavar="DIR",
        help="The pengyos-build output dir to seal.",
    )
    run_parser.add_argument(
        "--key",
        required=True,
        metavar="SEED",
        help="The signing key seed passed through to pengyos-sign.",
    )
    run_parser.add_argument(
        "--pub",
        default=None,
        metavar="PUB",
        help="Public key passed through to pengyos-verifier (and "
        "pengyos-witness-bundle) for cryptographic signature verification.",
    )
    run_parser.add_argument(
        "--attest",
        default=None,
        metavar="LOG",
        help="Attest log path passed through to pengyos-verifier.",
    )
    run_parser.add_argument(
        "--prev",
        default=None,
        metavar="DIR",
        help="A previous build dir for the repro diff (pengyos-repro-report).",
    )
    run_parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        metavar="OUT",
        help="Emit the machine-readable seal result as JSON. With no argument "
        "('--json') it prints to stdout; with 'OUT' it writes to that file "
        "(use '-' for stdout).",
    )
    run_parser.add_argument(
        "--sign-bin",
        default=None,
        metavar="PATH",
        help="Override the pengyos-sign binary (test seam).",
    )
    run_parser.add_argument(
        "--verify-bin",
        default=None,
        metavar="PATH",
        help="Override the pengyos-verifier binary (test seam).",
    )
    run_parser.add_argument(
        "--witness-bin",
        default=None,
        metavar="PATH",
        help="Override the pengyos-witness-bundle binary (test seam).",
    )
    run_parser.add_argument(
        "--repro-bin",
        default=None,
        metavar="PATH",
        help="Override the pengyos-repro-report binary (test seam).",
    )
    run_parser.set_defaults(func=cmd_run)

    return parser


def _require_runner(args: argparse.Namespace) -> Runner:
    """Build the production runner, honouring the per-tool binary overrides.

    Only *provided* ``--*-bin`` flags become overrides. An absent flag means
    "run the tool by name from ``PATH``" (the documented default) — it must NOT
    register a ``None`` override, which would blank ``argv[0]`` and crash
    ``subprocess`` before the tool ever runs.
    """
    overrides = {
        "pengyos-sign": args.sign_bin,
        "pengyos-verifier": args.verify_bin,
        "pengyos-witness-bundle": args.witness_bin,
        "pengyos-repro-report": args.repro_bin,
    }
    return runner.FileSystemRunner(
        overrides={tool: path for tool, path in overrides.items() if path},
        # The build dir is the cwd for every external tool call: the sign argv
        # uses build-relative artifact paths (``images/rootfs.ext4``), so without
        # this they resolve against the seal process's own cwd and pengyos-sign
        # fails with "No such file or directory".
        build_dir=args.build,
    )


def _validate_build_dir(args: argparse.Namespace, r: Runner) -> None:
    """Fail clean (SealError) if ``--build`` is not an existing directory."""
    if not r.is_dir(args.build):
        raise SealError(
            f"--build '{args.build}' is not a directory: no pengyos-build "
            "output to seal"
        )


# --- Command handlers -------------------------------------------------------


def cmd_plan(args: argparse.Namespace, r: Runner | None = None) -> int:
    """Build the deterministic seal plan for the build dir and render it.

    Pure: ``plan`` only reads the build tree (via the injected runner) and prints
    the ordered steps the seal would take. It never writes and never invokes an
    external tool. The ``--json`` flag emits the machine-readable plan (discovered
    names + each step's exact argv) instead of the text view. A missing build dir
    fails clean (a :class:`SealError`), never a traceback.
    """
    r = r or runner.FileSystemRunner()
    if not r.is_dir(args.build):
        raise SealError(
            f"--build '{args.build}' is not a directory: no pengyos-build "
            "output to seal"
        )
    plan_model = plan.build_plan(args.build, runner=r)
    if getattr(args, "json", False):
        print(plan_model.to_json())
    else:
        print(plan_model.render())
    return EXIT_OK


def cmd_run(args: argparse.Namespace, r: Runner | None = None) -> int:
    """Run the provenance seal and surface its result.

    Validates that a real build dir and key are supplied, then runs the seal
    stages (``sign`` and, by follow-up items, ``name``/``repro``/``witness``/
    ``verify``) through the injected runner seam. The whole run is modelled by a
    deterministic :class:`~pengyos_seal.plan.SealPlan` (built first, so the
    stages execute the exact argv the plan enumerated); the single
    :class:`SealError` (a missing build dir, a missing artifact, a signing
    failure) maps to a clean one-line stderr message and exit 1 — never a
    traceback. With ``--json`` (or ``--json -``/``--json OUT``) the human stage
    lines are suppressed and the machine-readable :class:`~pengyos_seal.seal.SealResult`
    is emitted as JSON instead (to stdout, or to the ``OUT`` file); the
    verifier's exit code still propagates as the return code.
    """
    r = r or _require_runner(args)
    _validate_build_dir(args, r)

    # Model the seal first (pure): discover the real names, then run the stages
    # that exist, handing the plan to the driver so the name stage symlinks the
    # discovered SBOM to its conventional root name. ``prev`` shapes the repro
    # status (diff vs skip), so it must reach the plan — otherwise a --prev'd
    # run would wrongly see the repro stage as a skip. ``pub`` / ``attest`` must
    # reach the plan too: the witness/verify argv carry them, so a sealed run
    # verifies against the same key it witnessed with.
    plan_model = plan.build_plan(
        args.build, runner=r, prev=args.prev, pub=args.pub, attest=args.attest,
    )
    result = seal.run_seal(
        args.build,
        args.key,
        r,
        plan=plan_model,
        pub=args.pub,
        attest=args.attest,
        prev=args.prev,
    )
    # The verifier is the closing gate: the seal returns its exit code (0 =
    # PASS, any non-zero = FAIL). A failing run is a clean non-zero exit — no
    # traceback, just the verdict line printed above and the verifier's code.
    rc = (
        result.verifier.exit_code if result.verifier is not None else EXIT_OK
    )
    if getattr(args, "json", None) is not None:
        # Machine-readable: emit ONLY the JSON (to the file, or stdout for
        # '--json' / '--json -'), never interleaved with the human lines.
        _emit_seal_result_json(args.json, result)
    else:
        _print_seal_result(result)
    return rc


def _print_seal_result(result: "seal.SealResult") -> None:
    """Render the seal result as the human ``run`` output (one line per stage)."""
    for artifact in result.signed:
        print(f"pengyos-seal: signed {artifact}")
    if result.sbom_symlinked is not None:
        source, root = result.sbom_symlinked
        print(f"pengyos-seal: normalised SBOM {source} -> {root}")
    if result.repro is not None:
        if result.repro.symlinked is not None:
            source, root = result.repro.symlinked
            print(f"pengyos-seal: repro {source} -> {root}")
        elif result.repro.status == plan.REPRO_EXISTING:
            print("pengyos-seal: repro report already at repro-report.json")
    if result.witness is not None and result.witness.status == seal.WITNESS_BUILT:
        print("pengyos-seal: witness built the layout")
    if result.verifier is not None:
        # The verifier is the closing gate: its verdict is surfaced and its exit
        # code is the seal's return code (mapped by `main` for a failing run).
        print(f"pengyos-seal: verifier {result.verifier.verdict}")
    for note in result.errors:
        print(f"pengyos-seal: {note}")
    if not result.signed:
        print(
            f"pengyos-seal: signed no artifacts in '{result.build_dir}' "
            "(see the message above)."
        )


def _emit_seal_result_json(output: str, result: "seal.SealResult") -> None:
    """Emit the machine-readable seal result as JSON.

    ``output == '-'`` prints to stdout; any other value writes the JSON to that
    path (a trailing newline is added for editor-friendliness). The JSON mirrors
    the spec's ``--json`` contract: the sealed plan, the artifacts signed, the
    names normalised, the repro status, and the verifier's checks + verdict +
    exit code.
    """
    text = result.to_json() + "\n"
    if output == "-":
        print(text, end="")
    else:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text)


# --- Entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SealError as exc:
        # The single, user-facing failure type: a clean one-line message and a
        # non-zero exit, never a traceback. Covers a missing/unreadable build
        # dir and (later) a missing artifact, key file, or signing failure.
        print(f"pengyos-seal: error: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
