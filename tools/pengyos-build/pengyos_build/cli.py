"""Command-line entry point for ``pengyos-build``.

The subcommand surface grows over time (configure / build / clean / status /
manifest); each is added by its own implementation task.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from . import __version__
from .acquire import AcquireError, BUILDROOT_REMOTE, acquire_target_tree
from .build import BuildError, build_target, status_for
from .clean import CleanError, clean_target
from .configure import ConfigureError, configure_target
from .manifest import ManifestError, write_manifest
from .sbom import SbomError, generate_sbom
from .targets import TargetsFileError, load_targets, load_target


def _quoted(argv: tuple[str, ...]) -> str:
    """Render an argv as the exact shell invocation it corresponds to.

    Each element is ``shlex.quote``d and joined by single spaces, so the
    printed line can be pasted straight into a shell and reproduces the
    invocation byte-for-byte.
    """
    return " ".join(shlex.quote(arg) for arg in argv)


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-build",
        description="Drive a reproducible Buildroot build for a named PengyOS target.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print the pengyos-build version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("version", help="print the pengyos-build version and exit")

    acq_p = sub.add_parser(
        "acquire",
        help="acquire (clone + pin) the Buildroot source tree for a target "
        "before configure/build",
    )
    acq_p.add_argument("target", help="target name from the targets file")
    acq_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    acq_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )
    acq_p.add_argument(
        "--remote",
        default=BUILDROOT_REMOTE,
        metavar="URL",
        help="upstream Buildroot git remote "
        "(default: %(default)s)",
    )

    list_p = sub.add_parser("list", help="list targets defined in targets.toml")
    list_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )

    cfg_p = sub.add_parser(
        "configure",
        help="run the configure make step for a target (requires the tree to "
        "be acquired first)",
    )
    cfg_p.add_argument("target", help="target name from the targets file")
    cfg_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    cfg_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )
    cfg_p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact make invocation and exit without running make",
    )

    clean_p = sub.add_parser(
        "clean",
        help="run the clean make step for a configured target (removes build "
        "artifacts from the output dir only, keeps .config)",
    )
    clean_p.add_argument("target", help="target name from the targets file")
    clean_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    clean_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )
    clean_p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact make invocation and exit without running make",
    )

    build_p = sub.add_parser(
        "build",
        help="run the build make step for a configured target (streams to the "
        "build log)",
    )
    build_p.add_argument("target", help="target name from the targets file")
    build_p.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help="parallel jobs -jN (default: number of host cores, i.e. -j$(nproc))",
    )
    build_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    build_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )
    build_p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact make invocation (with the resolved -j<jobs>) "
        "and exit without running make",
    )

    status_p = sub.add_parser(
        "status",
        help="report the build stage for a target by scanning its build log",
    )
    status_p.add_argument("target", help="target name from the targets file")
    status_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    status_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )

    man_p = sub.add_parser(
        "manifest",
        help="write a build manifest (inputs, versions, hashes, timestamps) "
        "as deterministic JSON",
    )
    man_p.add_argument("target", help="target name from the targets file")
    man_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    man_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )
    man_p.add_argument(
        "--json",
        default=None,
        metavar="OUT",
        dest="json_out",
        help="write the manifest JSON to OUT (default: "
        "<output>/build-manifest.json)",
    )

    sbom_p = sub.add_parser(
        "sbom",
        help="generate a signed SBOM (SPDX + CycloneDX) from Buildroot's "
        "legal-info/show-info; with --embed, bake it into the image",
    )
    sbom_p.add_argument("target", help="target name from the targets file")
    sbom_p.add_argument(
        "--targets",
        default="targets.toml",
        help="path to the targets file (default: ./targets.toml)",
    )
    sbom_p.add_argument(
        "--build-root",
        default=None,
        help="build root (default: $PENGYOS_BUILD_ROOT or ~/build/pengyos)",
    )
    sbom_p.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help="parallel jobs for the legal-info pass (default: host cores)",
    )
    sbom_p.add_argument(
        "--sign-key",
        default=None,
        metavar="KEY",
        help="ed25519 seed file; sign the SPDX SBOM (writes <spdx>.sig)",
    )
    sbom_p.add_argument(
        "--embed",
        action="store_true",
        default=False,
        help="re-run the build so the board post-build script bakes the SBOM "
        "into the rootfs (/etc/pengyos/)",
    )
    sbom_p.add_argument(
        "--repo-root",
        default=None,
        help="PengyOS repo root, used to locate tools/pengyos-sbom "
        "(default: the directory holding the targets file)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "acquire":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        try:
            result = acquire_target_tree(
                target, args.build_root, remote=args.remote
            )
        except AcquireError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(result)
        return 0

    if args.command == "list":
        try:
            targets = load_targets(args.targets)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        for target in targets:
            board = f"\tboard={target.board}" if target.board else ""
            print(
                f"{target.name}\tbr_version={target.br_version}\t"
                f"defconfig={target.defconfig}{board}\t"
                f"external_tree={target.external_tree}"
            )
        return 0

    if args.command == "configure":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        try:
            result = configure_target(
                target, args.build_root, dry_run=args.dry_run
            )
        except ConfigureError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(_quoted(result.argv) if args.dry_run else result)
        return 0

    if args.command == "clean":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        try:
            result = clean_target(target, args.build_root, dry_run=args.dry_run)
        except CleanError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(_quoted(result.argv) if args.dry_run else result)
        return 0

    if args.command == "build":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        try:
            result = build_target(
                target, args.build_root, jobs=args.jobs, dry_run=args.dry_run
            )
        except BuildError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(_quoted(result.argv) if args.dry_run else result)
        return 0

    if args.command == "status":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(status_for(target, args.build_root))
        return 0

    if args.command == "manifest":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        try:
            path = write_manifest(target, args.build_root, out_path=args.json_out)
        except ManifestError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(f"manifest for {target.name} written to {path}")
        return 0

    if args.command == "sbom":
        try:
            target = load_target(args.targets, args.target)
        except TargetsFileError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        repo_root = args.repo_root or Path(args.targets).resolve().parent
        try:
            result = generate_sbom(
                target,
                args.build_root,
                jobs=args.jobs,
                embed=args.embed,
                sign_key=args.sign_key,
                repo_root=repo_root,
            )
        except SbomError as exc:
            print(f"pengyos-build: error: {exc}", file=sys.stderr)
            return 1
        print(result)
        return 0

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
