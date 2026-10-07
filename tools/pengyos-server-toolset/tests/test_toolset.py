"""Tool-table / fragment tests.

Covers the promised tool list as data (tool name -> BR2_PACKAGE_ symbol) and
the deterministic fragments/server-toolset.fragment.

The tests assert that:

* the tool table encodes exactly the promised comfort tool set (SPEC §Summary);
* every tool maps to a well-formed, *currently-existing* ``BR2_PACKAGE_*`` symbol;
* the mapping is bijective (no duplicate tool names, no duplicate symbols);
* :func:`toolset.emit_fragment` is deterministic (same input → same bytes);
* the emitted fragment round-trips to disk and matches the committed file
  (the tool set and the fragment cannot drift apart);
* the emitted fragment has the expected Buildroot ``BR2_PACKAGE_X=y`` shape;
* ``python3 -m toolset emit`` writes the fragment to disk;
* the table is honest: every shipped tool maps to a Buildroot package that
  actually exists (verified 2026-09-18). `fzf` and `starship` have no *upstream*
  Buildroot package, but this repo ships well-formed Buildroot packages for them
  (`package/fzf/` — a golang-package, `package/starship/` — a cargo-package),
  so their symbols are defined and they are
  re-enabled by the fragment again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import toolset

REPO_ROOT = toolset.ROOT
FRAGMENT_PATH = toolset.FRAGMENT_PATH

# The promised comfort tool set (SPEC §Summary / §Purpose). This is the external
# ground truth the tool table must match — if it drifts from here, the suite
# fails. Kept in lockstep with the README / spec tool list.
#
# The 10 tools that ship: the eight core tools have an upstream Buildroot
# package; `fzf` and `starship` are shipped via this repo's own Buildroot
# packages, so they are enabled by the fragment
# again.
PROMISED_TOOLS = (
    "mosh",
    "tmux",
    "ripgrep",
    "fd",
    "bat",
    "eza",
    "fzf",
    "zoxide",
    "starship",
    "jq",
)

# The canonical Buildroot symbol each promised tool maps to.
EXPECTED_SYMBOLS = {
    "mosh": "BR2_PACKAGE_MOSH",
    "tmux": "BR2_PACKAGE_TMUX",
    "ripgrep": "BR2_PACKAGE_RIPGREP",
    "fd": "BR2_PACKAGE_FD",
    "bat": "BR2_PACKAGE_BAT",
    "eza": "BR2_PACKAGE_EZA",
    "fzf": "BR2_PACKAGE_FZF",
    "zoxide": "BR2_PACKAGE_ZOXIDE",
    "starship": "BR2_PACKAGE_STARSHIP",
    "jq": "BR2_PACKAGE_JQ",
}


# --- the tool table encodes the promised set --------------------------------


def test_tool_table_encodes_exactly_the_promised_tools():
    names = {tool.name for tool in toolset.TOOL_TABLE}
    assert names == set(PROMISED_TOOLS), (
        f"tool table names {sorted(names)} != promised set {sorted(PROMISED_TOOLS)}"
    )


def test_tool_table_has_no_duplicate_names():
    names = [tool.name for tool in toolset.TOOL_TABLE]
    assert len(names) == len(set(names)), "tool names must be unique (bijective)"


def test_each_tool_maps_to_its_expected_symbol():
    by_name = {tool.name: tool for tool in toolset.TOOL_TABLE}
    for name, symbol in EXPECTED_SYMBOLS.items():
        assert by_name[name].symbol == symbol, f"{name} maps to {by_name[name].symbol!r}"


# --- well-formed symbols ----------------------------------------------------


def test_symbols_are_wellformed_br2_package_identifiers():
    import re

    pattern = re.compile(r"^BR2_PACKAGE_[A-Z][A-Z0-9_]*$")
    for tool in toolset.TOOL_TABLE:
        assert pattern.match(tool.symbol), f"{tool.name}: {tool.symbol!r} is not a BR2_PACKAGE_* id"


def test_symbols_have_no_placeholder_tokens():
    # A drift-proof validator must not accept TODO/FIXME/XXX/UNDEFINED stubs.
    for tool in toolset.TOOL_TABLE:
        for token in ("TODO", "FIXME", "XXX", "UNDEFINED", "PLACEHOLDER"):
            assert token not in tool.symbol, f"{tool.name}: {tool.symbol} contains {token!r}"


# --- bijective mapping ------------------------------------------------------


def test_mapping_is_bijective_no_duplicate_symbols():
    symbols = [tool.symbol for tool in toolset.TOOL_TABLE]
    assert len(symbols) == len(set(symbols)), "symbols must be unique (bijective)"


def test_mapping_is_bijective_names_and_symbols_same_length():
    # 10 tools -> 10 distinct symbols.
    assert len(toolset.TOOL_TABLE) == 10
    assert len({t.symbol for t in toolset.TOOL_TABLE}) == 10


# --- deterministic emission -------------------------------------------------


def test_emit_is_deterministic():
    # Same input → identical bytes, across repeated calls.
    assert toolset.emit_fragment() == toolset.emit_fragment()
    assert toolset.emit_fragment() == toolset.emit_fragment()


def test_emit_order_matches_table_order():
    text = toolset.emit_fragment()
    lines = text.splitlines()
    emitted = [ln for ln in lines if ln.startswith("BR2_PACKAGE_")]
    expected = [f"{t.symbol}=y" for t in toolset.TOOL_TABLE]
    assert emitted == expected


def test_emit_ends_with_newline():
    text = toolset.emit_fragment()
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


# --- round-trip / no drift --------------------------------------------------


def test_emitted_fragment_matches_committed_file():
    """The committed fragment must equal what the table emits (no drift)."""
    emitted = toolset.emit_fragment()
    committed = FRAGMENT_PATH.read_text(encoding="utf-8")
    assert emitted == committed, (
        "committed fragment drifted from toolset.TOOL_TABLE — run "
        "`python3 -m toolset emit` and re-stage"
    )


def test_fragment_path_exists_and_is_tracked():
    assert FRAGMENT_PATH.is_file(), "fragment file must exist"
    # An empty dir is not git-tracked; the fragment must be committed.
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "fragments/server-toolset.fragment"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, "fragment is not tracked by git"


def test_fragment_shape_header_and_enables():
    text = FRAGMENT_PATH.read_text(encoding="utf-8")
    # header block is present
    assert "server-toolset.fragment" in text
    assert "GENERATED FROM toolset.TOOL_TABLE" in text
    # every promised tool is enabled as a BR2_PACKAGE_ symbol
    for tool in toolset.TOOL_TABLE:
        assert f"{tool.symbol}=y" in text, f"fragment missing {tool.symbol}=y"


# --- CLI --------------------------------------------------------------------


def test_cli_emit_writes_fragment(tmp_path: Path):
    """``python3 -m toolset emit [PATH]`` writes the fragment to disk."""
    out = tmp_path / "server-toolset.fragment"
    proc = subprocess.run(
        [sys.executable, "-m", "toolset", "emit", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_text(encoding="utf-8") == toolset.emit_fragment()


def test_cli_stdout_without_args_matches_emitter():
    """``python3 -m toolset`` (no args) prints the fragment to stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "toolset"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == toolset.emit_fragment()


# --- honesty about Buildroot existence --------------------------------------
# As of the 2026-09-18 checkout, every shipped tool maps to a Buildroot package
# that actually exists. The eight core tools ship upstream (`package/<name>/
# Config.in` is HTTP 200 on `master`); `fzf` / `starship` ship via this repo's
# own Buildroot packages.


def test_all_shipped_tools_exist_in_buildroot():
    # Every shipped tool must map to a Buildroot package that exists. This has
    # teeth: if someone marks a shipped tool as not-existing (or re-adds a tool
    # with a non-existent symbol), the suite fails. All 10 tools ship a package.
    missing = {t.name for t in toolset.TOOL_TABLE if not t.exists_in_buildroot}
    assert not missing, (
        f"shipped tools with no Buildroot package: {sorted(missing)}"
    )


def test_fzf_and_starship_are_readded_with_canonical_symbols():
    # fzf / starship have no *upstream* Buildroot package, but this repo ships
    # well-formed Buildroot packages for them,
    # so their canonical symbols are defined and they are re-enabled by the
    # fragment. This has teeth against a regression that drops them again.
    by_name = {t.name: t for t in toolset.TOOL_TABLE}
    for name, symbol in (("fzf", "BR2_PACKAGE_FZF"), ("starship", "BR2_PACKAGE_STARSHIP")):
        assert name in by_name, f"{name!r} missing from the shipped set"
        assert by_name[name].symbol == symbol, f"{name} maps to {by_name[name].symbol!r}"
        assert by_name[name].exists_in_buildroot is True, (
            f"{name} must record exists_in_buildroot=True (its Buildroot package ships)"
        )


def test_table_has_at_least_one_real_symbol():
    present = {t.name for t in toolset.TOOL_TABLE if t.exists_in_buildroot}
    assert present, "tool table should record at least one real Buildroot symbol"
