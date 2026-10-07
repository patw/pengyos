"""Validator tests.

Covers the validator: every promised tool maps to a real BR2 symbol; no
duplicate symbols; the fragment round-trips from the tool table.

:func:`toolset.validate` is the drift-proof validator. It returns a list of
error strings (empty == drift-free) and asserts:

1. every promised tool maps to a well-formed ``BR2_PACKAGE_*`` symbol (no
   stubs / placeholder tokens);
2. no duplicate tool names (bijective on names);
3. no duplicate symbols (bijective on symbols);
4. the emitted fragment round-trips back to the exact same ordered symbols;
5. (optionally) a committed / passed fragment equals what the table emits and
   parses back to the same symbols.

These tests prove the validator has teeth: each failure mode below is exercised
with a deliberately broken table and asserted to be reported, while the real
table (and the committed fragment) validate clean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import toolset

REPO_ROOT = toolset.ROOT
FRAGMENT_PATH = toolset.FRAGMENT_PATH


# --- helpers ----------------------------------------------------------------


def _broken_table(**overrides) -> tuple[toolset.Tool, ...]:
    """Return a copy of the real table with a few entries overridden.

    Each override is a ``name=new_symbol`` pair where ``new_symbol`` is a symbol
    string (or ``None`` to drop a tool). Used to manufacture specific drift.
    """
    result = []
    for tool in toolset.TOOL_TABLE:
        if tool.name in overrides:
            new_symbol = overrides[tool.name]
            if new_symbol is None:
                continue
            result.append(tool._replace(symbol=new_symbol))
        else:
            result.append(tool)
    return tuple(result)


# --- the real table validates clean -----------------------------------------


def test_real_table_is_drift_free():
    assert toolset.validate() == [], f"real table should validate clean: {toolset.validate()}"


def test_real_table_round_trips_through_committed_fragment():
    # The committed fragment must both equal the emission and parse back to the
    # same ordered symbols.
    assert toolset.validate(fragment_path=FRAGMENT_PATH) == []


def test_assert_valid_raises_on_drift():
    assert toolset.assert_valid() is None  # real table: no raise
    # A malformed (lowercase) symbol trips the well-formedness check.
    bad = _broken_table(mosh="br2_package_mosh")
    try:
        toolset.assert_valid(bad)
    except AssertionError as exc:
        assert "not drift-free" in str(exc)
    else:  # pragma: no cover - must not reach here
        raise AssertionError("assert_valid did not raise on a broken table")


def test_assert_valid_reports_committed_fragment(tmp_path: Path):
    # A drifted fragment passed via fragment_path must make assert_valid fail.
    drifted = tmp_path / "drift.fragment"
    drifted.write_text("BR2_PACKAGE_TAMPERED=y\n", encoding="utf-8")
    try:
        toolset.assert_valid(fragment_path=drifted)
    except AssertionError as exc:
        assert "drifted" in str(exc) or "round-trip" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("assert_valid did not fail on a drifted fragment")


# --- check 1: well-formed symbols (no stubs) --------------------------------


def test_validate_flags_malformed_symbol():
    bad = _broken_table(mosh="br2_package_mosh")  # lowercase -> invalid
    errors = toolset.validate(bad)
    assert any("malformed symbol" in e for e in errors), errors


def test_validate_flags_stub_token_in_symbol():
    bad = _broken_table(mosh="BR2_PACKAGE_TODO")
    errors = toolset.validate(bad)
    assert any("stub token" in e for e in errors), errors


def test_validate_accepts_only_uppercase_br2_package():
    import re

    pattern = re.compile(r"^BR2_PACKAGE_[A-Z][A-Z0-9_]*$")
    for tool in toolset.TOOL_TABLE:
        assert pattern.match(tool.symbol)
    assert toolset.validate() == []


# --- check 2: no duplicate names --------------------------------------------


def test_validate_flags_duplicate_names():
    # Two tools sharing the name "fd" (each mapped to a distinct symbol).
    dup = (
        toolset.Tool("mosh", "BR2_PACKAGE_MOSH", True),
        toolset.Tool("fd", "BR2_PACKAGE_FD", True),
        toolset.Tool("fd", "BR2_PACKAGE_BAT", True),
    )
    errors = toolset.validate(dup)
    assert any("duplicate tool name" in e for e in errors), errors


# --- check 3: no duplicate symbols (bijective) ------------------------------


def test_validate_flags_duplicate_symbols():
    # Two tools mapping to the same symbol breaks the bijection.
    dup = (
        toolset.Tool("mosh", "BR2_PACKAGE_MOSH", True),
        toolset.Tool("tmux", "BR2_PACKAGE_MOSH", True),
    )
    errors = toolset.validate(dup)
    assert any("duplicate symbol" in e for e in errors), errors


def test_validate_accepts_bijective_real_table():
    symbols = [t.symbol for t in toolset.TOOL_TABLE]
    assert len(symbols) == len(set(symbols))
    assert toolset.validate() == []


# --- check 4: fragment round-trips from the table ---------------------------


def test_emit_then_parse_round_trips():
    reparsed = toolset.parse_fragment(toolset.emit_fragment())
    expected = [t.symbol for t in toolset.TOOL_TABLE]
    assert reparsed == expected


def test_validate_flags_round_trip_mismatch():
    # A table with a malformed symbol cannot round-trip: emit renders it, but
    # parse cannot recover the original symbol, so the round-trip check fires.
    broken = (toolset.Tool("a", "BAD SYMBOL", True),)
    assert toolset.validate(broken)


def test_parse_fragment_ignores_comments_and_header():
    text = (
        "# a comment\n"
        "BR2_PACKAGE_MOSH=y\n"
        "# another comment\n"
        "BR2_PACKAGE_TMUX=y\n"
        "\n"
    )
    assert toolset.parse_fragment(text) == ["BR2_PACKAGE_MOSH", "BR2_PACKAGE_TMUX"]


def test_parse_fragment_rejects_non_enable_assignments():
    # Only `=y` package enables are extracted; `=n`, non-package keys, and
    # malformed lines are ignored.
    text = (
        "BR2_PACKAGE_MOSH=y\n"
        "BR2_PACKAGE_OFF=n\n"
        "BR2_FOO_BAR=y\n"
        "SOME_OTHER_KEY=1\n"
        "br2_package_bad=y\n"  # lowercase prefix -> not a BR2_PACKAGE_* id
    )
    assert toolset.parse_fragment(text) == ["BR2_PACKAGE_MOSH"]


def test_validate_flags_committed_fragment_drift(tmp_path: Path):
    # A fragment with a stray extra symbol no longer round-trips to the table.
    drifted = tmp_path / "drift.fragment"
    drifted.write_text(
        toolset.emit_fragment() + "BR2_PACKAGE_STRAY=y\n", encoding="utf-8"
    )
    errors = toolset.validate(fragment_path=drifted)
    assert errors, "drift in the fragment should be reported"
    assert any("round-trip" in e or "drifted" in e for e in errors), errors


# --- mutually exclusive fragment / fragment_path ----------------------------


def test_validate_rejects_both_fragment_and_path():
    try:
        toolset.validate(fragment="x", fragment_path="/tmp/y")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("validate should reject both fragment and fragment_path")


def test_validate_accepts_passed_fragment_text():
    # A passed fragment equal to the emission and round-tripping validates clean.
    clean = toolset.emit_fragment()
    assert toolset.validate(fragment=clean) == []
    # A passed fragment that does not match the emission is reported.
    assert toolset.validate(fragment="BR2_PACKAGE_NOPE=y\n")


# --- CLI: validate subcommand -----------------------------------------------


def test_cli_validate_exits_zero_when_clean():
    proc = subprocess.run(
        [sys.executable, "-m", "toolset", "validate"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "drift-free" in proc.stdout


def test_cli_validate_exits_nonzero_on_drift(tmp_path: Path):
    # A drifted fragment passed to the CLI must make it exit non-zero with a
    # report on stderr.
    bad = tmp_path / "drift.fragment"
    bad.write_text("BR2_PACKAGE_TAMPERED=y\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "toolset", "validate", str(bad)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout
    assert "drift detected" in proc.stderr


def test_cli_validate_accepts_explicit_path(tmp_path: Path):
    # The committed fragment validates clean via an explicit path too.
    proc = subprocess.run(
        [sys.executable, "-m", "toolset", "validate", str(FRAGMENT_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
