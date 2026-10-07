"""Tests for the CLI dispatch layer."""

from __future__ import annotations

import argparse

import pytest

from pengyos_mutations import __version__
from pengyos_mutations.cli import build_parser, main


def _subparsers(parser: argparse.ArgumentParser):
    """Return the subparsers action for ``parser`` via public attributes.

    The concrete subparsers action class is private and named differently
    across Python versions, so we detect it by shape: an ``Action`` whose
    ``choices`` maps names to ``ArgumentParser`` instances.
    """
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and all(
            isinstance(v, argparse.ArgumentParser) for v in choices.values()
        ):
            return action
    return None


def test_build_parser_has_all_subcommands() -> None:
    parser = build_parser()
    subparsers = _subparsers(parser)
    assert subparsers is not None
    assert {"apply", "revert", "log", "converge"} <= set(subparsers.choices)


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_parser_description_states_purpose() -> None:
    parser = build_parser()
    assert "Journaled, reversible system changes for PengyOS" in parser.description


def test_apply_subparser_exposes_desc_and_store() -> None:
    parser = build_parser()
    apply_p = _subparsers(parser).choices["apply"]
    flags = {opt for action in apply_p._actions for opt in action.option_strings}
    assert "--desc" in flags
    assert "--store" in flags


def test_converge_subparser_exposes_out() -> None:
    parser = build_parser()
    converge_p = _subparsers(parser).choices["converge"]
    flags = {opt for action in converge_p._actions for opt in action.option_strings}
    assert "--out" in flags
