"""Tests for the emitted Buildroot fragment (``fragments/server-clis.fragment``).

Covers the "Emit fragments/server-clis.fragment" subtask: the fragment must
enable **exactly** the ``BR2_PACKAGE_<TOOL>`` symbols emitted by
:func:`pengyos_recipe_server_clis.emit.emit_tool` — no more, no less — with one
``BR2_PACKAGE_<TOOL>=y`` line per tool in catalogue order.

The fragment is the inverse half of the symbol-emitting contract: the promised
tool set (the catalogue) and the emitted fragment can never drift apart.  We
assert this three ways — byte-for-byte against a golden fixture, by round-tripping
through :func:`parse_fragment`, and by cross-checking every symbol against the
symbol each tool's emitted ``Config.in`` actually declares.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_recipe_server_clis import emit
from pengyos_recipe_server_clis.catalogue import TOOL_NAMES, load_catalogue

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "emit"

# The symbols the fragment must enable, taken straight from the emitted Config.in
# headers — NOT from the emitter's own tables, so a wrong symbol here is a real
# failure.
EXPECTED_SYMBOLS = tuple(f"BR2_PACKAGE_{t.name.upper()}" for t in load_catalogue())


# --------------------------------------------------------------------------- #
# golden output — byte-for-byte against the committed fixture
# --------------------------------------------------------------------------- #


def test_fragment_matches_golden_fixture(tmp_path: Path) -> None:
    """The emitted fragment is byte-for-byte the golden fixture."""
    frag_path = emit.emit_fragment(load_catalogue(), tmp_path)
    got = frag_path.read_text(encoding="utf-8")
    want = (FIXTURE_ROOT / "fragments" / emit.FRAGMENT_NAME).read_text(encoding="utf-8")
    assert got == want, "server-clis.fragment drifted from the golden fixture"


def test_fragment_written_under_fragments_dir(tmp_path: Path) -> None:
    """The fragment lands at <out>/fragments/<FRAGMENT_NAME> (Buildroot layout)."""
    frag_path = emit.emit_fragment(load_catalogue(), tmp_path)
    assert frag_path.parent.name == "fragments", frag_path
    assert frag_path.name == emit.FRAGMENT_NAME, frag_path
    assert frag_path.stat().st_size > 0


def test_fragment_is_deterministic(tmp_path: Path) -> None:
    """Two emits of the same catalogue are byte-identical (no timestamps/random)."""
    first = emit.emit_fragment(load_catalogue(), tmp_path / "a")
    second = emit.emit_fragment(load_catalogue(), tmp_path / "b")
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the core contract — the fragment enables EXACTLY the emitted symbols
# --------------------------------------------------------------------------- #


def test_fragment_enables_exactly_the_emitted_symbols() -> None:
    """The fragment lists exactly the emitted symbols — no more, no less.

    This is the spec's headline assertion ("the fragment lists exactly the
    emitted symbols (no more, no less)"): the parsed set equals the emitted
    symbol set, with the same count, in catalogue order.
    """
    text = emit.render_fragment(load_catalogue())
    symbols = emit.parse_fragment(text)
    assert symbols == list(EXPECTED_SYMBOLS), (
        f"fragment symbols {symbols} != emitted symbols {list(EXPECTED_SYMBOLS)}"
    )


def test_fragment_symbol_count_matches_tool_count() -> None:
    """One enable line per tool — one symbol per emitted package."""
    text = emit.render_fragment(load_catalogue())
    symbols = emit.parse_fragment(text)
    assert len(symbols) == len(TOOL_NAMES) == 6, (
        f"fragment enabled {len(symbols)} symbols, expected {len(TOOL_NAMES)}"
    )


def test_fragment_symbols_are_unique() -> None:
    """No symbol is enabled twice (a duplicate enable is a recipe smell)."""
    text = emit.render_fragment(load_catalogue())
    symbols = emit.parse_fragment(text)
    assert len(symbols) == len(set(symbols)), "fragment has duplicate symbols"


def test_fragment_symbols_match_emitted_config_in() -> None:
    """Every fragment symbol is exactly the symbol each tool's Config.in declares.

    Cross-checks the fragment against the *emitted* Config.in headers, so a drift
    between the two halves of the contract is caught independently of the
    emitter's own symbol table.
    """
    import re

    text = emit.render_fragment(load_catalogue())
    symbols = emit.parse_fragment(text)
    for tool in load_catalogue():
        config_in = emit.render_config_in(tool, emit.family_of(tool))
        emitted_symbol = config_in.splitlines()[0].removeprefix("config ")
        assert emitted_symbol == f"BR2_PACKAGE_{tool.name.upper()}"
        assert emitted_symbol in symbols, (
            f"{tool.name}: emitted symbol {emitted_symbol!r} not in fragment"
        )


def test_fragment_order_follows_catalogue() -> None:
    """Fragment lines appear in committed catalogue order (stable across runs)."""
    text = emit.render_fragment(load_catalogue())
    symbols = emit.parse_fragment(text)
    assert symbols == list(EXPECTED_SYMBOLS)


# --------------------------------------------------------------------------- #
# parse_fragment — the inverse contract, with teeth
# --------------------------------------------------------------------------- #


def test_parse_fragment_ignores_comments_and_blank_lines() -> None:
    """parse_fragment skips comment / blank lines and keeps only ``BR2_PACKAGE_=y``."""
    junk = (
        "# a comment\n"
        "\n"
        "BR2_PACKAGE_ZOXIDE=y\n"
        "# mid comment\n"
        "BR2_PACKAGE_SD=y\n"
        "\n"
    )
    assert emit.parse_fragment(junk) == ["BR2_PACKAGE_ZOXIDE", "BR2_PACKAGE_SD"]


def test_parse_fragment_rejects_non_y_values() -> None:
    """Only ``=y`` enables a package; other RHS values are not enable lines."""
    text = "BR2_PACKAGE_ZOXIDE=n\nBR2_PACKAGE_SD=y\n"
    assert emit.parse_fragment(text) == ["BR2_PACKAGE_SD"]


def test_parse_fragment_rejects_malformed_symbols() -> None:
    """A malformed symbol (lowercase / bad chars) fails the shape assertion."""
    with pytest.raises(AssertionError):
        emit.parse_fragment("BR2_PACKAGE_zoxide=y\n")


def test_render_parse_round_trip() -> None:
    """render_fragment followed by parse_fragment is the identity on symbols."""
    tools = load_catalogue()
    symbols = emit.parse_fragment(emit.render_fragment(tools))
    assert symbols == [f"BR2_PACKAGE_{t.name.upper()}" for t in tools]


# --------------------------------------------------------------------------- #
# CLI: emit also writes and reports the fragment
# --------------------------------------------------------------------------- #


def test_cli_emit_writes_fragment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = emit.main(["emit", "--out", str(tmp_path)])
    assert rc == 0
    frag = tmp_path / "fragments" / emit.FRAGMENT_NAME
    assert frag.is_file(), "CLI emit did not write the fragment"
    assert "fragment: wrote" in capsys.readouterr().out


def test_cli_emit_json_includes_fragment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    assert emit.main(["emit", "--out", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fragment"].endswith(emit.FRAGMENT_NAME)
    assert set(payload["tools"]) == set(TOOL_NAMES)


def test_emit_all_writes_fragment_as_side_effect(tmp_path: Path) -> None:
    """emit_all (not just the CLI) writes the fragment under <out>/fragments/."""
    emit.emit_all(load_catalogue(), tmp_path)
    assert (tmp_path / "fragments" / emit.FRAGMENT_NAME).is_file()


def test_emit_all_fragment_matches_cli_fragment(tmp_path: Path) -> None:
    """emit_all and emit_fragment agree on the fragment bytes."""
    emit.emit_all(load_catalogue(), tmp_path / "a")
    emit.emit_fragment(load_catalogue(), tmp_path / "b")
    a = (tmp_path / "a" / "fragments" / emit.FRAGMENT_NAME).read_text(encoding="utf-8")
    b = (tmp_path / "b" / "fragments" / emit.FRAGMENT_NAME).read_text(encoding="utf-8")
    assert a == b
