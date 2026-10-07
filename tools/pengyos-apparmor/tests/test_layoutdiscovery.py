"""Tests for upstream agent-workspace layout discovery
(``pengyos_apparmor.layoutdiscovery``).

The follow-up "pin the pengy-agent's workspace file-write
surface" is blocked until PengyOS defines the agent's real working
layout upstream — the path set must never be invented (the same
never-guess rule as the model-server port). This file pins the
automated re-check: the conservative pattern (a candidate line must
name a concrete path *below* the agent's workspace root), the shared
skip rules (every checkout of this tool, VCS/build noise, binary and
oversized files), the candidate record shape, and the
``find-agent-workspace-layout`` CLI end to end. All filesystem tests
run on ``tmp_path`` fixture trees; no network, no root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_apparmor import layoutdiscovery, portdiscovery
from pengyos_apparmor.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Shorthand for the workspace root in fixtures and patterns.
W = layoutdiscovery.AGENT_WORKSPACE_ROOT


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------


def test_layoutdiscovery_public_api():
    for attr in (
        "AGENT_WORKSPACE_ROOT",
        "LAYOUT_PATTERN",
        "LayoutDiscoveryError",
        "LayoutCandidate",
        "scan_text",
        "find_agent_workspace_layout",
    ):
        assert hasattr(layoutdiscovery, attr), f"layoutdiscovery.{attr} missing"


def test_default_scan_root_is_shared_with_portdiscovery():
    # The layout re-check scans the same PengyOS workspace root as the
    # port re-check (layoutdiscovery has no separate root constant —
    # the CLI default is portdiscovery.DEFAULT_SCAN_ROOT): the layout,
    # when defined, would be defined next to the port.
    assert layoutdiscovery is not portdiscovery
    ws = portdiscovery.DEFAULT_SCAN_ROOT
    assert (ws / "tools" / "pengyos-apparmor").resolve() == portdiscovery.TOOL_ROOT


def test_workspace_root_constant_is_locked_to_the_committed_profile():
    # The scan's root constant and the profile's grants must be
    # lockstep: if the profile's workspace root ever moves, the scan
    # constant (and this test) must move with it.
    profile = (REPO_ROOT / "profiles" / "pengy-agent").read_text(encoding="utf-8")
    assert f"{W} rw," in profile
    assert f"{W}/.git rw," in profile
    assert f"{W}/.git/* rw," in profile
    # And the root grant is the only rule that carries the bare root.
    assert profile.count(f"{W} rw,") == 1


# ---------------------------------------------------------------------------
# scan_text — the pure per-text half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        # Prose definition.
        (f"The agent writes build output to {W}/build/**.", [(1, ("build/**",))]),
        # Markdown table row.
        (f"| scratch | {W}/scratch | temporary files |", [(1, ("scratch",))]),
        # Nested surface.
        (f"the working tree is {W}/work/repo-a", [(1, ("work/repo-a",))]),
        # Two surfaces on one line, both reported in line order.
        (f"{W}/work/<repo> and {W}/build/**", [(1, ("work", "build/**"))]),
        # AppArmor glob characters are part of the surface.
        (f"{W}/venv3.[0-9]* holds the interpreter", [(1, ("venv3.[0-9]*",))]),
        # A trailing slash after the surface is not part of it.
        (f"{W}/logs/ is where it writes", [(1, ("logs",))]),
        # At line start (no preceding character) the lookbehind passes.
        (f"{W}/artifacts", [(1, ("artifacts",))]),
    ],
)
def test_scan_text_finds_definitions(line, expected):
    assert layoutdiscovery.scan_text(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        # The root alone is not a layout definition — the root grant
        # already exists in the profile.
        f"the agent's workspace is {W}",
        f"workspace: {W} .",
        # A different root / a lookalike: the constant must match
        # exactly, including the trailing separator.
        f"{W}2/work",
        "/opt/pengy/workspaces/pengy-other/work",
        f"{W}.work",
        # A root that is a suffix of a longer path is not the root.
        f"/srv/{W}/work",
        # A double slash after the root is not a path.
        f"{W}//work",
        # No workspace path at all.
        "the agent clones into its workspace",
        "workspaces/pengy-agent/work is writable",
    ],
)
def test_scan_text_rejects_non_definitions(line):
    assert layoutdiscovery.scan_text(line) == []


def test_scan_text_is_per_line_and_1_based():
    text = (
        "no mention here\n"
        f"{W}/build/** is for output\n"
        "and nothing else\n"
    )
    assert layoutdiscovery.scan_text(text) == [(2, ("build/**",))]


def test_scan_text_dedupes_surfaces_within_a_line():
    line = f"{W}/build/** and again {W}/build/**"
    assert layoutdiscovery.scan_text(line) == [(1, ("build/**",))]


def test_scan_text_multiple_lines_report_independently():
    text = (
        f"{W}/build/**\n"
        f"{W}/work/repo-a\n"
        f"{W}/build/**\n"
    )
    assert layoutdiscovery.scan_text(text) == [
        (1, ("build/**",)),
        (2, ("work/repo-a",)),
        (3, ("build/**",)),
    ]


# ---------------------------------------------------------------------------
# find_agent_workspace_layout — the tree walk
# ---------------------------------------------------------------------------


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tool_checkout(root: Path):
    _write(root / "pengyos_apparmor" / "__init__.py", "")


def _fixture_tree(root: Path) -> None:
    # Positive candidates in three shapes, nested at different depths.
    _write(
        root / "SPEC.md",
        "Agent layout\n\n"
        f"The agent writes build output to {W}/build/**.\n"
        f"| working tree | {W}/work/<repo> | per repo |\n",
    )
    _write(root / "tools" / "agent" / "spec.md", f"scratch dir: {W}/scratch\n")
    # Negatives: no candidate may come from these.
    _write(
        root / "negatives.md",
        f"the agent's workspace is {W}\n"
        f"{W}2/work\n"
        "the agent clones into its workspace\n",
    )
    # Noise that must be skipped entirely.
    _write(root / ".git" / "config", f"{W}/build/**\n")
    _write(root / "__pycache__" / "x.py", f"{W}/build/**\n")
    (root / "binfile.bin").parent.mkdir(parents=True, exist_ok=True)
    (root / "binfile.bin").write_bytes(f"\x00\x01{W}/build/**\x00".encode())
    big = f"{W}/build/**\n" + ("padding line\n" * 90000)
    _write(root / "big.txt", big)
    assert (root / "big.txt").stat().st_size > portdiscovery.MAX_SCAN_BYTES


def test_find_returns_sorted_candidates_with_relpaths_and_linenumbers(tmp_path):
    _fixture_tree(tmp_path)
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.lineno, c.surfaces) for c in candidates] == [
        ("SPEC.md", 3, ("build/**",)),
        ("SPEC.md", 4, ("work",)),
        ("tools/agent/spec.md", 1, ("scratch",)),
    ]
    # The evidence line is the stripped source line.
    assert candidates[0].line == f"The agent writes build output to {W}/build/**."


def test_find_on_empty_tree_finds_nothing(tmp_path):
    (tmp_path / "README.md").write_text("nothing to see\n", encoding="utf-8")
    assert layoutdiscovery.find_agent_workspace_layout(tmp_path) == []


def test_find_raises_when_root_is_not_a_directory(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(layoutdiscovery.LayoutDiscoveryError):
        layoutdiscovery.find_agent_workspace_layout(missing)
    f = tmp_path / "file.txt"
    f.write_text("x\n", encoding="utf-8")
    with pytest.raises(layoutdiscovery.LayoutDiscoveryError):
        layoutdiscovery.find_agent_workspace_layout(f)


def test_find_skips_an_extra_skip_directory(tmp_path):
    _write(tmp_path / "a" / "spec.md", f"{W}/build/**\n")
    _write(tmp_path / "b" / "spec.md", f"{W}/scratch\n")
    candidates = layoutdiscovery.find_agent_workspace_layout(
        tmp_path, extra_skip=[tmp_path / "a"]
    )
    assert [(c.path, c.surfaces) for c in candidates] == [
        ("b/spec.md", ("scratch",))
    ]


def test_find_skips_oversized_files(tmp_path):
    big = f"{W}/build/**\n" + ("padding line\n" * 90000)
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    assert (tmp_path / "big.txt").stat().st_size > portdiscovery.MAX_SCAN_BYTES
    assert layoutdiscovery.find_agent_workspace_layout(tmp_path) == []


def test_find_does_not_descend_into_symlink_loops_or_broken_symlinks(tmp_path):
    (tmp_path / "ok.txt").write_text(f"{W}/build/**\n", encoding="utf-8")
    (tmp_path / "self").symlink_to(tmp_path)  # loop
    (tmp_path / "broken").symlink_to(tmp_path / "gone")
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.surfaces) for c in candidates] == [
        ("ok.txt", ("build/**",))
    ]


def test_sibling_checkouts_of_this_tool_are_pruned(tmp_path):
    _write(tmp_path / "SPEC.md", f"The agent writes to {W}/build/**.\n")
    _tool_checkout(tmp_path / "other-ws" / "tools" / "pengyos-apparmor")
    _write(
        tmp_path
        / "other-ws"
        / "tools"
        / "pengyos-apparmor"
        / "SPEC.md",
        f"{W}/should-not-be-reported\n",
    )
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.surfaces) for c in candidates] == [
        ("SPEC.md", ("build/**",))
    ]


def test_scan_root_that_is_a_tool_checkout_reports_no_candidates(tmp_path):
    # The root itself is a checkout: every line under it is this tool, so
    # every hit would be a self-match — "no upstream layout defined" is
    # the honest answer.
    _tool_checkout(tmp_path)
    _write(tmp_path / "SPEC.md", f"{W}/build/**\n")
    assert layoutdiscovery.find_agent_workspace_layout(tmp_path) == []


def test_vendored_checkout_does_not_hide_upstream_definitions_around_it(tmp_path):
    # A PengyOS repo that vendors this tool in a subdirectory: the
    # upstream definitions at the repo root and in a sibling tool must
    # still be found; only the vendored checkout is pruned.
    _write(tmp_path / "SPEC.md", f"{W}/work/<repo>\n")
    _write(tmp_path / "tools" / "agent" / "spec.md", f"{W}/scratch\n")
    _tool_checkout(tmp_path / "tools" / "pengyos-apparmor")
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.surfaces) for c in candidates] == [
        ("SPEC.md", ("work",)),
        ("tools/agent/spec.md", ("scratch",)),
    ]


def test_merely_mentioning_the_package_name_is_not_a_checkout(tmp_path):
    # The checkout marker is the package itself (a pengyos_apparmor
    # subdirectory carrying __init__.py); a note file with the name, or
    # a directory without the marker, is ordinary content and gets
    # scanned.
    _write(tmp_path / "pengyos_apparmor.txt", f"{W}/build/**\n")
    _write(tmp_path / "pengyos_apparmor" / "notes.md", f"{W}/scratch\n")
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.surfaces) for c in candidates] == [
        ("pengyos_apparmor.txt", ("build/**",)),
        ("pengyos_apparmor/notes.md", ("scratch",)),
    ]


# ---------------------------------------------------------------------------
# File decode rule — the leading-BOM rule shared with the port scan
# ---------------------------------------------------------------------------


def _write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


BOM = "\ufeff"


def test_find_strips_a_leading_bom_from_the_line_one_evidence(tmp_path):
    # A BOM is a file-level encoding marker, not line content: a
    # BOM-marked spec whose first line defines the layout must be found,
    # and the printed evidence line must not carry the invisible BOM
    # (the same decode rule the port scan applies).
    _write_bytes(
        tmp_path / "cfg.toml",
        (BOM + f"the agent writes to {W}/build/**\n").encode("utf-8"),
    )
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.lineno, c.surfaces) for c in candidates] == [
        ("cfg.toml", 1, ("build/**",))
    ]
    assert BOM not in candidates[0].line
    assert candidates[0].line == f"the agent writes to {W}/build/**"


def test_find_strips_only_the_file_level_leading_bom(tmp_path):
    # The BOM on line 1 is stripped; a line-2 definition is found and
    # its evidence line is clean (there was never a BOM on line 2).
    _write_bytes(
        tmp_path / "cfg.toml",
        (BOM + "intro line\n" + f"{W}/scratch\n").encode("utf-8"),
    )
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.lineno, c.surfaces) for c in candidates] == [
        ("cfg.toml", 2, ("scratch",))
    ]
    assert candidates[0].line == f"{W}/scratch"


def test_find_keeps_a_later_line_bom_in_place(tmp_path):
    # A U+FEFF on a LATER line is line content (concatenated-file
    # content), not a file marker: it is NOT stripped. The layout
    # pattern is not line-start-anchored, so the candidate is still
    # found (the BOM sits where the lookbehind tolerates it), and the
    # evidence line keeps the BOM exactly as the file carries it —
    # the same rule the port scan pins.
    _write_bytes(
        tmp_path / "cfg.txt",
        ("intro\n" + BOM + f"{W}/build/**\n").encode("utf-8"),
    )
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.lineno, c.surfaces) for c in candidates] == [
        ("cfg.txt", 2, ("build/**",))
    ]
    assert BOM in candidates[0].line


def test_find_strips_exactly_one_leading_bom(tmp_path):
    # Two BOMs in a row: the first is the file marker (stripped), the
    # second is line content (kept) — the same one-BOM-only rule the
    # port scan pins.
    _write_bytes(
        tmp_path / "cfg.txt",
        (BOM + BOM + f"{W}/build/**\n").encode("utf-8"),
    )
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.lineno, c.surfaces) for c in candidates] == [(1, ("build/**",))]
    assert candidates[0].line == BOM + f"{W}/build/**"


def test_bom_is_not_a_binary_skip_trigger(tmp_path):
    # A BOM-marked text file has no NUL bytes: it must be scanned, not
    # skipped as binary.
    _write_bytes(
        tmp_path / "cfg.toml",
        (BOM + f"layout: {W}/work/repo-a\n").encode("utf-8"),
    )
    assert not portdiscovery.is_binary((tmp_path / "cfg.toml").read_bytes())
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.surfaces) for c in candidates] == [
        ("cfg.toml", ("work/repo-a",))
    ]


def test_bom_less_files_decode_identically(tmp_path):
    # For a BOM-less file utf-8-sig decodes identically to utf-8: the
    # pre-existing tree-walk behavior is a pure no-op for them.
    _write(tmp_path / "SPEC.md", f"the agent writes to {W}/build/**.\n")
    candidates = layoutdiscovery.find_agent_workspace_layout(tmp_path)
    assert [(c.path, c.lineno, c.surfaces) for c in candidates] == [
        ("SPEC.md", 1, ("build/**",))
    ]
    assert candidates[0].line == f"the agent writes to {W}/build/**."


def test_cli_prints_bom_free_evidence_lines(tmp_path, capsys):
    # End to end: the CLI's printed evidence line for a BOM-marked file
    # must be clean — a human reviewing the candidate never sees an
    # invisible BOM at the start of the line.
    _write_bytes(
        tmp_path / "cfg.toml",
        (BOM + f"{W}/build/**\n").encode("utf-8"),
    )
    assert main(["find-agent-workspace-layout", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: cfg.toml:1: build/**" in out
    assert BOM not in out
    assert f"{W}/build/**" in out
    assert "one layout candidate (surface: build/**)" in out


def test_cli_bom_marked_file_with_no_definition_still_reports_blocked(
    tmp_path, capsys
):
    # A BOM-marked file whose content is not a layout definition stays
    # a no-candidate file (the decode rule changes nothing about what
    # IS a candidate).
    _write_bytes(tmp_path / "cfg.toml", (BOM + "no layout here\n").encode("utf-8"))
    assert main(["find-agent-workspace-layout", str(tmp_path)]) == 3
    assert "no agent workspace layout definition found" in capsys.readouterr().out


def test_cli_scan_of_this_tools_own_root_reports_blocked(capsys):
    # Anchor against self-matches: the tool's own tree carries the
    # workspace path in the profile, the docs and the fixtures.
    # Scanning it must exit 3 with "no agent workspace layout
    # definition found", never list its own files as candidates.
    assert main(["find-agent-workspace-layout", str(portdiscovery.TOOL_ROOT)]) == 3
    assert "no agent workspace layout definition found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CLI — find-agent-workspace-layout
# ---------------------------------------------------------------------------


def test_cli_finds_candidates_and_prints_next_step(tmp_path, capsys):
    _write(tmp_path / "SPEC.md", f"{W}/build/**\n")
    assert main(["find-agent-workspace-layout", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"candidate: SPEC.md:1: build/**" in out
    assert f"{W}/build/**" in out
    assert "one layout candidate (surface: build/**)" in out
    assert "pin the workspace file-write surface" in out


def test_cli_reports_all_distinct_candidates(tmp_path, capsys):
    _write(tmp_path / "a.md", f"{W}/build/**\n")
    _write(tmp_path / "b.md", f"{W}/scratch\n")
    assert main(["find-agent-workspace-layout", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "build/**" in out
    assert "scratch" in out
    assert "2 distinct surface candidate(s)" in out
    # No single-surface follow-up while ambiguous.
    assert "one layout candidate" not in out


def test_cli_no_candidates_exits_three(tmp_path, capsys):
    _write(tmp_path / "SPEC.md", f"the agent's workspace is {W}\n")
    assert main(["find-agent-workspace-layout", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "no agent workspace layout definition found" in out
    assert "do not invent a path set" in out


def test_cli_missing_root_exits_two(tmp_path, capsys):
    assert main(["find-agent-workspace-layout", str(tmp_path / "nope")]) == 2
    assert "no such directory" in capsys.readouterr().err


def test_cli_default_root_is_the_pengyos_workspace_and_still_reports_blocked(capsys):
    # Anchor test: the default scan target is the PengyOS workspace root
    # (the parent of this tool), and — as of this commit — it contains
    # no agent working-layout definition, so the command reports
    # "blocked" (exit 3). When PengyOS upstream defines the layout,
    # THIS test is the visible, reviewable signal that the blocked item
    # has unblocked.
    assert main(["find-agent-workspace-layout"]) == 3
    out = capsys.readouterr().out
    assert "no agent workspace layout definition found" in out


def test_cli_help_advertises_find_agent_workspace_layout(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "find-agent-workspace-layout" in out
