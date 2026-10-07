"""Deterministic Buildroot recipe emitter for the PengyOS Server comfort CLIs.

Emits, for every tool in :mod:`pengyos_recipe_server_clis.catalogue`, a Buildroot
``BR2_EXTERNAL`` recipe — ``package/<tool>/{Config.in, <tool>.mk, <tool>.hash}`` —
mirroring the ``fzf`` / ``starship`` recipe shape already in
``pengyos-server-toolset``.

The emitter is the single source of truth for the recipe *shape*; the catalogue
is the single source of truth for the recipe *data* (version, source URL,
license, digest).  The two never drift: :func:`emit_tool` derives every field of
every emitted file from the :class:`~pengyos_recipe_server_clis.catalogue.Tool`
entry, so regenerating the recipes is a pure function of the committed data.

Build-system families
----------------------
The only thing that differs between the six tools is the upstream build system,
which drives two things:

* the ``$(eval $(<MACRO>))`` build-macro evaluated in ``<tool>.mk`` —
  ``cargo-package`` (Rust), ``golang-package`` (Go) or ``cmake-package`` (C/C++
  with CMake); and
* the ``depends on …_TARGET_ARCH_SUPPORTS`` / ``select BR2_PACKAGE_HOST_*``
  host-dependency lines in ``Config.in`` — present for the host-toolchain
  families (cargo / golang), absent for ``cmake`` (Buildroot's ``cmake-package``
  infra installs its own host cmake; it needs no arch-support symbol).

The family of each tool is fixed by the tool's upstream language (verified via
the GitHub ``/languages`` API):

============  ==============  =========================
tool          language      family / macro
============  ==============  =========================
``zoxide``    Rust          cargo / ``cargo-package``
``sd``        Rust          cargo / ``cargo-package``
``node_exporter``  Go       golang / ``golang-package``
``btop``      C/C++ (CMake) cmake / ``cmake-package``
``helix``     Rust          cargo / ``cargo-package``
``neovim``    C/C++ (CMake) cmake / ``cmake-package``
============  ==============  =========================

``zoxide`` being Rust (not Go) is the one surprise — Buildroot itself ships it as
a ``cargo-package`` — so the family table is data, not inference.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import __version__
from . import lint as lint_mod
from .catalogue import TOOL_NAMES, Tool, load_catalogue

# --------------------------------------------------------------------------- #
# Build-system families
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BuildFamily:
    """How Buildroot builds one tool — the macro plus the host-dep lines.

    Attributes:
        macro: The ``$(eval $(<MACRO>))`` build-macro for ``<tool>.mk``.
        host_dep: The ``depends on …_TARGET_ARCH_SUPPORTS`` RHS for
            ``Config.in`` (``None`` for ``cmake``, which needs no host-arch
            symbol).
        host_select: The ``select BR2_PACKAGE_HOST_*`` RHS for ``Config.in``
            (``None`` for ``cmake``).
        gomod: Whether a ``<VAR>_GOMOD`` line must be emitted (only Go packages).
    """

    macro: str
    host_dep: str | None
    host_select: str | None
    gomod: bool


# cargo (Rust): host rustc arch-support + select, no GOMOD line.
CARGO = BuildFamily(
    macro="cargo-package",
    host_dep="BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS",
    host_select="BR2_PACKAGE_HOST_RUSTC",
    gomod=False,
)
# golang (Go): host go arch-support + select, plus a GOMOD line in the .mk.
GO = BuildFamily(
    macro="golang-package",
    host_dep="BR2_PACKAGE_HOST_GO_TARGET_ARCH_SUPPORTS",
    host_select="BR2_PACKAGE_HOST_GO",
    gomod=True,
)
# cmake (C/C++): no host-arch symbol; cmake-package installs its own cmake.
CMAKE = BuildFamily(
    macro="cmake-package",
    host_dep=None,
    host_select=None,
    gomod=False,
)

# The per-tool build family. Fixed by the upstream language (see the module
# docstring); deliberately a data table so it can be audited at a glance.
FAMILIES: dict[str, BuildFamily] = {
    "zoxide": CARGO,
    "sd": CARGO,
    "node_exporter": GO,
    "btop": CMAKE,
    "helix": CARGO,
    "neovim": CMAKE,
}

# Short, human one-liner help bodies for the Config.in `help` block.  Kept as
# emit-side data (not catalogue data): the catalogue owns release metadata, the
# help text is presentation.  The emitter appends the upstream URL below each.
HELP_DESCRIPTIONS: dict[str, str] = {
    "zoxide": (
        "zoxide is a smarter cd command — it remembers which directories you use "
        "most and lets you jump to them with a keyword."
    ),
    "sd": (
        "sd is an intuitive find-and-replace command line tool — a friendlier "
        "replacement for sed."
    ),
    "node_exporter": (
        "node_exporter runs a static HTTP server that exports Prometheus metrics "
        "about the running machine for collection."
    ),
    "btop": (
        "btop is a high-performance terminal hardware and process monitor — a "
        "modern replacement for top/htop showing CPU, memory, disks, network and "
        "processes."
    ),
    "helix": (
        "helix is a post-modern code editor written in Rust — an evolutionary fork "
        "of kakoune with a built-in language server protocol client."
    ),
    "neovim": (
        "neovim is a hyperextensible, Vim-based refactored text editor with "
        "first-class language server protocol support."
    ),
}


def family_of(tool: Tool) -> BuildFamily:
    """Return the :class:`BuildFamily` for *tool* (raises ``KeyError`` if unknown)."""
    return FAMILIES[tool.name]


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

# Buildroot's build-macro eval wrapper: "$(eval $(<macro>))".  The `{}` is filled
# with the family macro (cargo-package / golang-package / cmake-package).  Kept as
# a module constant so the exact parenthesisation is written once.
_EVAL_TEMPLATE = "$(eval $({}))"

# The 78-#-char banner fence shared by every .mk file (Buildroot convention).
_BANNER_LINE = "#" * 80

# Max width of a Config.in `help` line's *content* (after the "<tab><2 spaces>"
# indent).  Mirrors buildroot/utils/check-package's HelpText rule
# (`^\t  .{,62}$`), which caps help text at ~80 columns.  The trailing URL line
# is exempt (URL-only), so it is not wrapped.
_HELP_WIDTH = 62


def _banner(title: str) -> str:
    """The ``#``-fenced package banner (5-line fence, ``# <title>`` in the middle)."""
    return "\n".join(
        [
            _BANNER_LINE,
            "#",
            f"# {title}",
            "#",
            _BANNER_LINE,
        ]
    )


def render_mk(tool: Tool, family: BuildFamily) -> str:
    """Render the ``<tool>.mk`` recipe body for *tool*."""
    var = tool.name.upper()
    # Buildroot requires a blank line between the banner fence and the first
    # variable (utils/check-package: "<file>:N: should be a blank line").  This
    # is the exact shape of the fzf / starship recipes in pengyos-server-toolset.
    lines: list[str] = [_banner(tool.name), ""]
    lines.append(f"{var}_VERSION = {tool.version}")
    lines.append(
        f"{var}_SITE = $(call github,{tool.repo.split('/')[0]},{tool.repo.split('/')[1]},"
        f"{tool.site_version})"
    )
    if family.gomod:
        # Go packages need the module root; it is the repo's canonical path.
        lines.append(f"{var}_GOMOD = https://github.com/{tool.repo}")
    lines.append(f"{var}_LICENSE = {tool.license}")
    lines.append(f"{var}_LICENSE_FILES = {tool.license_file}")
    lines.append("")
    # Buildroot's build-macro eval is "$(eval $(<macro>))" — the inner "$(…)"
    # is not an f-string expression, so build it with a format template.
    lines.append(_EVAL_TEMPLATE.format(family.macro))
    return "\n".join(lines) + "\n"


def render_config_in(tool: Tool, family: BuildFamily) -> str:
    """Render the ``Config.in`` block for *tool*.

    Uses a hidden ``config`` symbol with a ``bool`` prompt — the same shape as
    ``fzf`` / ``starship`` — because these CLIs expose no sub-options (a
    ``menuconfig`` entry is only for packages that carry nested options).
    """
    symbol = f"BR2_PACKAGE_{tool.name.upper()}"
    lines: list[str] = [f"config {symbol}"]
    lines.append(f'\tbool "{tool.name}"')
    if family.host_dep is not None:
        lines.append(f"\tdepends on {family.host_dep}")
    if family.host_select is not None:
        lines.append(f"\tselect {family.host_select}")
    lines.append("\thelp")
    # Buildroot's check-package `HelpText` rule requires every help line to be
    # "<tab><2 spaces><≤62 chars>" (the manual caps help text at 80 columns);
    # the trailing URL line is exempt (URL-only).  Word-wrap the description to
    # fit, indenting each wrapped line one tab + two spaces.
    description = HELP_DESCRIPTIONS[tool.name]
    for wrapped in textwrap.wrap(description, width=_HELP_WIDTH):
        lines.append(f"\t  {wrapped}")
    lines.append("")
    lines.append(f"\t  {tool.homepage}")
    return "\n".join(lines) + "\n"


def render_hash(tool: Tool) -> str:
    """Render the ``<tool>.hash`` file for *tool* (sha256 of the pinned tarball).

    Buildroot accepts a ``.hash`` file carrying only a ``sha256`` line; the
    ``sha512`` line is optional.  We emit ``sha256`` only — the committed
    catalogue carries the authoritative tarball digest, and a ``sha512`` would
    require re-fetching the tarball (network / opt-in).
    """
    return (
        "# Locally generated\n"
        f'sha256  {tool.sha256}  {tool.source}\n'
    )


# --------------------------------------------------------------------------- #
# Emission
# --------------------------------------------------------------------------- #


# The Buildroot external fragment filename (lives under <out>/fragments/).
FRAGMENT_NAME = "server-clis.fragment"

# Fixed header block prepended to the emitted fragment. Kept as data so the
# emitted file is byte-for-byte reproducible. Mirrors the
# server-toolset.fragment header shape in pengyos-server-toolset.
FRAGMENT_HEADER = (
    "# server-clis.fragment — PengyOS Server comfort CLIs Buildroot recipes (US-SRV-1/3)\n"
    "#\n"
    "# Buildroot external fragment: enables the missing comfort CLIs as\n"
    "# BR2_PACKAGE_* symbols. GENERATED FROM the tool catalogue (catalogue.py);\n"
    "# re-emit with:  python3 -m pengyos_recipe_server_clis emit\n"
    "# Do not edit by hand.\n"
)

# A well-formed Buildroot package-enable symbol: BR2_PACKAGE_ followed by an
# uppercase identifier. Used by the fragment's contract and its parser.
_SYMBOL_RE = re.compile(r"^BR2_PACKAGE_[A-Z][A-Z0-9_]*$")


def render_fragment(tools: Iterable[Tool]) -> str:
    """Return the Buildroot fragment text for *tools*, deterministically.

    A fixed header block followed by one ``BR2_PACKAGE_<TOOL>=y`` line per tool,
    in the given order — each line exactly matching the symbol emitted in that
    tool's ``Config.in``. The output is stable across runs and Python versions
    and is terminated by a trailing newline.
    """
    lines = [FRAGMENT_HEADER.rstrip("\n")]
    for tool in tools:
        lines.append(f"BR2_PACKAGE_{tool.name.upper()}=y")
    return "\n".join(lines) + "\n"


def parse_fragment(text: str) -> list[str]:
    """Return the ordered list of enabled ``BR2_PACKAGE_*`` symbols in *text*.

    Buildroot fragments are ``KEY=value`` assignments; the comfort set only
    enables packages, so we extract every ``BR2_PACKAGE_<X>=y`` symbol, in the
    order it appears. Comments, the header block, blank lines and any other
    assignment are ignored. This is the inverse of the symbol-emitting half of
    :func:`render_fragment`, so the promised tool set and the emitted fragment
    can never drift apart.
    """
    symbols: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("BR2_PACKAGE_") and stripped.endswith("=y"):
            symbol = stripped[: -len("=y")]
            assert _SYMBOL_RE.match(symbol), f"malformed fragment symbol: {symbol!r}"
            symbols.append(symbol)
    return symbols


def emit_fragment(tools: Iterable[Tool], out_dir: Path) -> Path:
    """Write ``<out_dir>/fragments/<FRAGMENT_NAME>`` and return its path.

    The fragment enables exactly the ``BR2_PACKAGE_<TOOL>`` symbols emitted by
    :func:`emit_tool` — no more, no less — so the promised tool set and the
    emitted fragment can never drift.
    """
    frag_dir = Path(out_dir) / "fragments"
    frag_dir.mkdir(parents=True, exist_ok=True)
    frag_path = frag_dir / FRAGMENT_NAME
    frag_path.write_text(render_fragment(tools), encoding="utf-8")
    return frag_path


def emit_tool(tool: Tool, out_dir: Path) -> list[Path]:
    """Write ``<tool>``'s three recipe files into ``out_dir/<tool>/``.

    Returns the list of paths written (``Config.in``, ``<tool>.mk``,
    ``<tool>.hash``), in that order.
    """
    family = family_of(tool)
    pkg_dir = Path(out_dir) / "package" / tool.name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    config_in = pkg_dir / "Config.in"
    mk = pkg_dir / f"{tool.name}.mk"
    hash_file = pkg_dir / f"{tool.name}.hash"

    config_in.write_text(render_config_in(tool, family), encoding="utf-8")
    mk.write_text(render_mk(tool, family), encoding="utf-8")
    hash_file.write_text(render_hash(tool), encoding="utf-8")

    return [config_in, mk, hash_file]


def emit_all(tools: Iterable[Tool], out_dir: Path) -> dict[str, list[Path]]:
    """Emit every tool in *tools* into ``out_dir/<tool>/``.

    Writes each tool's three recipe files into ``out_dir/package/<tool>/`` and
    the enabling Buildroot fragment into ``out_dir/fragments/``.

    Returns a mapping of tool name -> list of paths written, in catalogue order.
    The emitted fragment lives at
    :data:`emit.FRAGMENT_NAME` under ``out_dir/fragments/`` (see
    :func:`emit_fragment`).
    """
    out_dir = Path(out_dir)
    written: dict[str, list[Path]] = {
        tool.name: emit_tool(tool, out_dir) for tool in tools
    }
    emit_fragment(written_tools(tools), out_dir)
    return written


def written_tools(tools: Iterable[Tool]) -> list[Tool]:
    """Return *tools* as a de-duplicated list in committed catalogue order.

    The fragment must enable exactly the emitted tools, so the same set drives
    both the per-tool recipes and the fragment.  Order follows the catalogue
    (not the input order) so the emitted fragment is stable across runs.
    """
    ordered = list(tools)
    seen: set[str] = set()
    deduped = [t for t in ordered if not (t.name in seen or seen.add(t.name))]
    by_name = {t.name: t for t in deduped}
    return [by_name[name] for name in TOOL_NAMES if name in by_name]


def list_tools() -> list[Tool]:
    """Return the committed catalogue (canonical order)."""
    return load_catalogue()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _cmd_list(args: argparse.Namespace) -> int:
    tools = list_tools()
    if args.json:
        payload = {
            "version": __version__,
            "tools": [
                {
                    "name": t.name,
                    "repo": t.repo,
                    "version": t.version,
                    "symbol": f"BR2_PACKAGE_{t.name.upper()}",
                    "build": family_of(t).macro,
                    "license": t.license,
                }
                for t in tools
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"# pengyos-recipe-server-clis {__version__}")
        print(f"# {'tool':<14} {'symbol':<26} {'build':<16} license")
        for t in tools:
            print(
                f"{t.name:<14} {f'BR2_PACKAGE_{t.name.upper()}':<26} "
                f"{family_of(t).macro:<16} {t.license}"
            )
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    tools = list_tools()
    written: dict[str, list[Path]] = emit_all(tools, Path(args.out))
    frag_path = emit_fragment(written_tools(tools), Path(args.out))
    if args.json:
        payload = {
            "out": str(args.out),
            "tools": {name: [str(p) for p in paths] for name, paths in written.items()},
            "fragment": str(frag_path),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for name in TOOL_NAMES:
            files = ", ".join(p.name for p in written[name])
            print(f"{name}: wrote {files}")
        print(f"fragment: wrote {frag_path}")
    return 0


def _cmd_lint_passthrough(args: argparse.Namespace) -> int:
    """Delegate the ``lint`` subcommand to :mod:`pengyos_recipe_server_clis.lint`.

    The lint module owns the rule set and argument definitions; the top-level
    CLI only forwards, so there is a single source of truth for ``lint``.
    """
    argv = ["lint", "--dir", args.dir]
    if getattr(args, "json", False):
        argv.append("--json")
    if getattr(args, "rules", None):
        argv += ["--rules", args.rules]
    return lint_mod.main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-recipe-server-clis",
        description="Emit and lint PengyOS Buildroot recipes for the Server "
        "comfort CLIs (zoxide, sd, node_exporter, btop, helix, neovim).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="print the committed tool catalogue")
    p_list.add_argument("--json", action="store_true", help="machine-readable output")
    p_list.set_defaults(func=_cmd_list)

    p_emit = sub.add_parser("emit", help="write package/<tool>/{Config.in,.mk,.hash}")
    p_emit.add_argument(
        "--out",
        required=True,
        help="output directory (recipes land in <out>/package/<tool>/)",
    )
    p_emit.add_argument("--json", action="store_true", help="machine-readable output")
    p_emit.set_defaults(func=_cmd_emit)

    # ``lint`` forwards to lint.py's own parser (single source of truth for the
    # rule set + argument definitions); only the flags it needs are declared here.
    p_lint = sub.add_parser(
        "lint",
        help="lint an emitted recipe directory (hash, version, license, source)",
        description=(
            "Lint an emitted pengyos-recipe-server-clis recipe directory against "
            "the grammar rules (hash present, version pinned, license + license "
            "file declared, absolute https source URL). Delegates to "
            "pengyos_recipe_server_clis.lint."
        ),
    )
    p_lint.add_argument(
        "--dir",
        required=True,
        help="recipe directory to lint (output root, package/, or a <tool> dir)",
    )
    p_lint.add_argument(
        "--rules",
        default=None,
        help="comma-separated subset of the lint rules (default: all)",
    )
    p_lint.add_argument("--json", action="store_true", help="machine-readable output")
    p_lint.set_defaults(func=_cmd_lint_passthrough)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
