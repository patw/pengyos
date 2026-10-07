"""Command-line interface for ``pengyos-hwprofile``.

Two subcommands, both thin wrappers over the pure logic in the rest of the
package:

* ``detect`` — collect a hardware profile from a *source* and emit it as
  deterministic ``profile.toml``. The source is a directory (``--from``) in
  tests/fixtures, or — when ``--from`` is omitted — the live ``/proc`` + ``/sys``
  (the integration path, opt-in behind ``PENGYOS_IT=1``).
* ``diff`` — compare two profiles and report added / removed / changed devices.

The subcommands are registered lazily as they are implemented so the parser
never advertises a command that does not yet exist. Every function returns a
process exit code; errors are reported to ``stderr`` with a non-zero code.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Iterable, Mapping, Sequence

from . import __version__
from .emitter import emit
from .schema import ProfileError, load_profile
from .source import SourceError, live_source, open_source

# The live-system integration path is opt-in, exactly like the integration
# tests. Without it, ``detect`` without ``--from`` refuses to read the machine
# so the behaviour is explicit rather than a surprise.
_INTEGRATION_ENV = "PENGYOS_IT"
_INTEGRATION_VALUES = ("1", "true", "True")


# ---------------------------------------------------------------------------
# Source selection.
# ---------------------------------------------------------------------------


def _open_source(args: argparse.Namespace) -> object:
    """Open the ``detect`` source: a directory, or the live system (opt-in)."""

    if getattr(args, "from_dir", None):
        return open_source(args.from_dir)

    if os.environ.get(_INTEGRATION_ENV) not in _INTEGRATION_VALUES:
        raise SystemExit(
            f"{_INTEGRATION_ENV}={_INTEGRATION_VALUES[0]} to profile the live "
            "system, or pass --from <dir> (e.g. tests/fixtures/sys)."
        )
    try:
        return live_source()
    except SourceError as exc:
        raise SystemExit(f"cannot read the live system: {exc}") from exc


# ---------------------------------------------------------------------------
# ``detect``.
# ---------------------------------------------------------------------------


def _cmd_detect(args: argparse.Namespace) -> int:
    """Emit a profile from the selected source to ``--out`` (or stdout)."""

    from .collectors import collect  # local import: collectors are stable

    try:
        source = _open_source(args)
        document = collect(source)  # validates, raises ProfileError
    except ProfileError as exc:
        raise SystemExit(f"invalid profile: {exc}") from exc
    except SourceError as exc:
        raise SystemExit(f"cannot read source: {exc}") from exc

    text = emit(document)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            raise SystemExit(f"cannot write {args.out!r}: {exc}") from exc
    else:
        sys.stdout.write(text)
    return 0


def _add_detect_parser(subparsers: argparse._SubparsersAction) -> None:
    parser = subparsers.add_parser(
        "detect",
        help="detect hardware and emit a declarative profile.toml",
        description=(
            "Collect hardware from a source and emit a deterministic, "
            "version-controllable profile.toml. With --from DIR the source is a "
            "directory (a fixture, or a real /sys mirror); without --from the "
            "live system is read (opt-in behind PENGYOS_IT=1)."
        ),
    )
    parser.add_argument(
        "--from",
        dest="from_dir",
        metavar="DIR",
        default=None,
        help="source directory (a fixture tree, or a /sys + /proc mirror). "
        "Omit to read the live system (requires PENGYOS_IT=1).",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="write the profile here (default: stdout).",
    )
    parser.set_defaults(func=_cmd_detect)


# ---------------------------------------------------------------------------
# ``diff``.
# ---------------------------------------------------------------------------


def _load_named(path: str) -> dict[str, Any]:
    """Load and validate a profile from ``path``, returning its document."""

    import tomllib

    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise SystemExit(f"cannot read {path!r}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path!r} is not valid TOML: {exc}") from exc
    try:
        load_profile(data)  # raises ProfileError on a schema violation
    except ProfileError as exc:
        raise SystemExit(f"{path!r}: {exc}") from exc
    return data


# Each hardware section has its own *identity* fields: the fields that say which
# physical device an item refers to. Two items with the same identity are the
# same device (so a value change is reported as ``~ changed``); items with a
# unique identity are added/removed. This is what lets ``diff`` distinguish a
# changed device from a replaced one.
_SECTION_IDENTITY: dict[str, tuple[str, ...]] = {
    "memory": ("locator",),
    "gpu": ("bus",),
    "storage": ("serial", "bus", "model"),
    "network": ("interface", "mac"),
    "peripherals": ("bus", "vendor_id", "product_id"),
}


def _diff_documents(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """Return human-readable lines describing how ``a`` differs from ``b``.

    Only the hardware list sections are diffed (``metadata`` and ``cpu`` are
    machine/tool identity and are not compared). Each section is keyed by its
    identity fields, so a device present in both but with a changed value is
    reported as ``~ changed``; a device only in ``b`` is ``+ added``; a device
    only in ``a`` is ``- removed``.
    """

    lines: list[str] = []
    for section in ("memory", "gpu", "storage", "network", "peripherals"):
        id_fields = _SECTION_IDENTITY[section]
        by_id_a = {_identity(item, id_fields): item for item in a.get(section, [])}
        by_id_b = {_identity(item, id_fields): item for item in b.get(section, [])}

        for key in sorted(by_id_b):
            if key not in by_id_a:
                lines.append(f"+ {section}: {key}")
        for key in sorted(by_id_a):
            if key not in by_id_b:
                lines.append(f"- {section}: {key}")
        for key in sorted(set(by_id_a) & set(by_id_b)):
            if _values(by_id_a[key]) != _values(by_id_b[key]):
                lines.append(f"~ {section}: {key}")
    return lines


def _identity(item: Mapping[str, Any], fields: Iterable[str]) -> tuple[str, ...]:
    """A stable identity for ``item`` from ``fields`` (missing -> ``""``)."""

    return tuple(str(item.get(field) or "") for field in fields)


def _values(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """The non-identity values of ``item``, for change detection."""

    return tuple(sorted((str(k), _scalar(v)) for k, v in item.items()))


def _scalar(value: Any) -> str:
    """Render a scalar for comparison (numbers normalised, others as strings)."""

    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _cmd_diff(args: argparse.Namespace) -> int:
    """Diff two profiles; exit 0 if identical, 1 if they differ."""

    a = _load_named(args.a)
    b = _load_named(args.b)
    lines = _diff_documents(a, b)
    if not lines:
        sys.stdout.write(f"{args.a} and {args.b} are identical\n")
        return 0
    sys.stdout.write("\n".join(lines) + "\n")
    return 1


def _add_diff_parser(subparsers: argparse._SubparsersAction) -> None:
    parser = subparsers.add_parser(
        "diff",
        help="diff two profiles (added / removed / changed devices)",
        description=(
            "Compare two profile.toml files and report added (+), removed (-), "
            "and changed (~) devices in the hardware list sections."
        ),
    )
    parser.add_argument("a", metavar="A", help="the first (baseline) profile")
    parser.add_argument("b", metavar="B", help="the second (updated) profile")
    parser.set_defaults(func=_cmd_diff)


# ---------------------------------------------------------------------------
# Parser.
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    Subcommands are registered lazily as features are implemented so that the
    parser never advertises a command that does not yet exist.
    """

    parser = argparse.ArgumentParser(
        prog="pengyos-hwprofile",
        description=(
            "Detect hardware and emit a declarative, version-controllable "
            "per-machine profile; diff and validate."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
    )
    # ``detect`` and ``diff`` are added as each subtask lands.
    _add_detect_parser(subparsers)
    _add_diff_parser(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 0

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
