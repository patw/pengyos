"""PengyOS server-toolset: the promised comfort tool list as data.

The tool list (tool name -> BR2_PACKAGE_ symbol) as data, and the deterministic
fragments/server-toolset.fragment emitted from it.

This module is the single source of truth for the comfort tool set. Each tool
maps to exactly one Buildroot ``BR2_PACKAGE_*`` symbol. The fragment emitted by
:func:`emit_fragment` is derived from this table, so the promised tool set and
the emitted fragment can never drift apart: the validator (item 4) re-derives
the fragment from this table and diffs it against the committed file.

There is no ``pengyos_*`` import package; this module lives at
the repo root and is importable because ``tests/conftest.py`` adds the tool root
to ``sys.path``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Repo-root-relative locations (resolved against this file).
ROOT = Path(__file__).resolve().parent
FRAGMENTS_DIR = ROOT / "fragments"
FRAGMENT_PATH = FRAGMENTS_DIR / "server-toolset.fragment"

# The promised comfort tool set (SPEC §Summary / §Purpose). Order is significant:
# the fragment is emitted in this order, so it must stay stable/deterministic.
#
# Each entry maps a human-readable tool name to the Buildroot package symbol that
# enables it. ``exists_in_buildroot`` records whether that symbol is currently
# defined in Buildroot (checked against git.buildroot.net as of 2026-09-18).
class Tool(NamedTuple):
    name: str
    """Human-readable tool name (lowercase, as a user would type it)."""
    symbol: str
    """Buildroot ``BR2_PACKAGE_*`` symbol that enables this tool."""
    exists_in_buildroot: bool
    """Whether this symbol is currently defined in Buildroot (2026-09-18)."""

# The comfort set: every tool below maps to a Buildroot package that actually
# exists in the tree. The eight core tools ship in upstream Buildroot (verified:
# `package/<name>/Config.in` is HTTP 200 on `master`). `fzf` and `starship` have
# no *upstream* Buildroot package, so we ship our own well-formed Buildroot
# packages for them (`package/fzf/` — a `golang-package`, `package/starship/` — a
# `cargo-package`; see their
# `tests/test_package_buildroot.py` structural/defconfig tests). Their symbols
# (`BR2_PACKAGE_FZF` / `BR2_PACKAGE_STARSHIP`) are therefore now defined in the
# tree, so they are re-enabled by the Buildroot *fragment* again and re-added to
# this table with `exists_in_buildroot=True`.
#
# This is data, not a stub: every symbol here is a real, current Buildroot
# package-enable symbol, and the emitted fragment round-trips back to this table.
TOOL_TABLE: tuple[Tool, ...] = (
    Tool("mosh", "BR2_PACKAGE_MOSH", True),
    Tool("tmux", "BR2_PACKAGE_TMUX", True),
    Tool("ripgrep", "BR2_PACKAGE_RIPGREP", True),
    Tool("fd", "BR2_PACKAGE_FD", True),
    Tool("bat", "BR2_PACKAGE_BAT", True),
    Tool("eza", "BR2_PACKAGE_EZA", True),
    Tool("fzf", "BR2_PACKAGE_FZF", True),
    Tool("zoxide", "BR2_PACKAGE_ZOXIDE", True),
    Tool("starship", "BR2_PACKAGE_STARSHIP", True),
    Tool("jq", "BR2_PACKAGE_JQ", True),
)

# Fixed header block prepended to the emitted fragment. Kept as data so the
# emitted file is byte-for-byte reproducible.
FRAGMENT_HEADER = (
    "# server-toolset.fragment — PengyOS Server remote-admin comfort set (US-SRV-1)\n"
    "#\n"
    "# Buildroot external fragment: enables the comfort-package set as\n"
    "# BR2_PACKAGE_* symbols. GENERATED FROM toolset.TOOL_TABLE (toolset.py);\n"
    "# re-emit with:  python3 -m toolset emit\n"
    "# Do not edit by hand.\n"
)

# A well-formed Buildroot package-enable symbol: BR2_PACKAGE_ followed by an
# uppercase identifier. Used by both the emitter's contract and the validator.
_SYMBOL_RE = re.compile(r"^BR2_PACKAGE_[A-Z][A-Z0-9_]*$")
# Tokens that must never appear in a symbol — they mark stubs / unfinished work.
_STUB_TOKENS = ("TODO", "FIXME", "XXX", "UNDEFINED", "PLACEHOLDER")


def emit_fragment(table: tuple[Tool, ...] = TOOL_TABLE) -> str:
    """Return the Buildroot fragment text for ``table``, deterministically.

    The output is stable across runs and Python versions: a fixed header block
    followed by one ``BR2_PACKAGE_X=y`` line per tool, in table order, with a
    trailing newline terminating the file.
    """
    lines = [FRAGMENT_HEADER.rstrip("\n")]
    for tool in table:
        lines.append(f"{tool.symbol}=y")
    return "\n".join(lines) + "\n"


def parse_fragment(text: str) -> list[str]:
    """Return the ordered list of enabled ``BR2_PACKAGE_*`` symbols in ``text``.

    Buildroot fragments are ``KEY=value`` assignments; the comfort set only
    enables packages, so we extract every ``BR2_PACKAGE_<X>=y`` symbol, in the
    order it appears. Comments, the header block, blank lines and any other
    assignment are ignored. This is the inverse of the symbol-emitting half of
    :func:`emit_fragment`, so :func:`validate` can assert the fragment
    round-trips back to the exact tool table.
    """
    symbols: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^(BR2_PACKAGE_[A-Z][A-Z0-9_]*)=y$", stripped)
        if match:
            symbols.append(match.group(1))
    return symbols


def validate(
    table: tuple[Tool, ...] = TOOL_TABLE,
    fragment: str | None = None,
    fragment_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Validate the comfort tool set for drift; return a list of error strings.

    This is the drift-proof validator. An empty
    return list means the tool set is well-formed, bijective, and the emitted
    fragment round-trips back to the exact same symbols — so the promised tool
    set and the emitted fragment can never drift apart.

    Checks (all of them):

    1. every tool maps to a well-formed ``BR2_PACKAGE_*`` symbol (no stubs);
    2. no duplicate tool names (bijective on names);
    3. no duplicate symbols (bijective on symbols);
    4. the fragment round-trips: re-parsing :func:`emit_fragment` yields the
       symbols in the same order as ``table``;
    5. optionally, a committed fragment at ``fragment_path`` (or the passed
       ``fragment`` text) equals what the table emits *and* parses back to the
       same ordered symbols.

    ``fragment`` and ``fragment_path`` are mutually exclusive; if both are
    given, :class:`ValueError` is raised. Passing neither still performs checks
    1-4.
    """
    errors: list[str] = []

    # 1. well-formed BR2_PACKAGE_* symbols (no stubs).
    for tool in table:
        if not _SYMBOL_RE.match(tool.symbol):
            errors.append(
                f"{tool.name!r} maps to a malformed symbol {tool.symbol!r} "
                "(expected BR2_PACKAGE_<UPPER_UNDERSCORE>)"
            )
        for token in _STUB_TOKENS:
            if token in tool.symbol:
                errors.append(
                    f"{tool.name!r} symbol {tool.symbol!r} contains stub token {token!r}"
                )

    # 2. duplicate tool names break the promise<->symbol mapping.
    names = [tool.name for tool in table]
    for n in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"duplicate tool name {n!r}")

    # 3. duplicate symbols break the bijective mapping.
    symbols = [tool.symbol for tool in table]
    for s in sorted({s for s in symbols if symbols.count(s) > 1}):
        errors.append(f"duplicate symbol {s!r}")

    # 4. the emitted fragment round-trips back to the same ordered symbols.
    emitted = emit_fragment(table)
    reparsed = parse_fragment(emitted)
    expected_symbols = [tool.symbol for tool in table]
    if reparsed != expected_symbols:
        errors.append(
            "fragment round-trip mismatch: emitted symbols "
            f"{reparsed} != table symbols {expected_symbols}"
        )

    # 5. committed/passed fragment must equal the emission and parse back too.
    if fragment is not None and fragment_path is not None:
        raise ValueError("pass only one of 'fragment' or 'fragment_path', not both")
    if fragment is not None:
        if fragment != emitted:
            errors.append("passed fragment text does not match emit_fragment(table)")
        if parse_fragment(fragment) != expected_symbols:
            errors.append("passed fragment does not round-trip to the table symbols")
    elif fragment_path is not None:
        committed = Path(fragment_path).read_text(encoding="utf-8")
        if committed != emitted:
            errors.append(
                f"committed fragment {fragment_path!r} drifted from "
                "emit_fragment(table) — re-emit and re-stage"
            )
        if parse_fragment(committed) != expected_symbols:
            errors.append(
                f"committed fragment {fragment_path!r} does not round-trip "
                "to the table symbols"
            )

    return errors


def assert_valid(
    table: tuple[Tool, ...] = TOOL_TABLE,
    fragment_path: str | os.PathLike[str] | None = None,
) -> None:
    """Raise :class:`AssertionError` if :func:`validate` reports any drift.

    Convenience wrapper so callers (tests, CI, the CLI) can assert the comfort
    set is drift-free in one call. Raises with a combined message listing every
    error found.
    """
    errors = validate(table, fragment_path=fragment_path)
    if errors:
        raise AssertionError(
            "comfort tool set is not drift-free:\n  - "
            + "\n  - ".join(errors)
        )


def main(argv: list[str] | None = None) -> int:
    """CLI for the fragment emitter / validator.

    * ``python3 -m toolset emit [PATH]`` writes the fragment to ``PATH`` (default
      ``fragments/server-toolset.fragment``) and prints where it went.
    * ``python3 -m toolset`` (no args) prints the fragment to stdout (handy for
      previews and for the drift test, which compares this output to the
      committed file).
    * ``python3 -m toolset validate [PATH]`` runs :func:`validate` over the
      committed fragment (or ``PATH`` if given) and exits non-zero with a report
      if any drift is found.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "emit":
        out = argv[1] if len(argv) > 1 else FRAGMENT_PATH
        write_fragment(out)
        print(f"wrote {out}")
        return 0
    if argv and argv[0] == "validate":
        path = argv[1] if len(argv) > 1 else FRAGMENT_PATH
        errors = validate(fragment_path=path)
        if errors:
            print("drift detected:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        print("OK: tool set is drift-free (validator, item 4)")
        return 0
    # default: print to stdout
    sys.stdout.write(emit_fragment())
    return 0


def write_fragment(
    path: str | os.PathLike[str] = FRAGMENT_PATH,
    table: tuple[Tool, ...] = TOOL_TABLE,
) -> str:
    """Write the fragment for ``table`` to ``path`` and return the path written."""
    text = emit_fragment(table)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return os.fspath(path)


if __name__ == "__main__":
    raise SystemExit(main())
