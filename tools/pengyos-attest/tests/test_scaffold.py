"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, all subcommands
  dispatch, bad usage),
* the CLI argument surface (``append`` / ``show`` / ``verify`` / ``export``) is
  registered with the documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_attest as pkg
from pengyos_attest import cli
from pengyos_attest import __version__
from pengyos_attest.cli import (
    DEFAULT_DATA,
    DEFAULT_EVENT_TYPE,
    EXIT_OK,
    build_parser,
    main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- package metadata -------------------------------------------------------


def test_package_exposes_semver_string():
    assert isinstance(pkg.__version__, str)
    parts = pkg.__version__.split(".")
    # At least a numeric MAJOR.MINOR.PATCH core.
    assert len(parts) >= 3
    assert parts[0].isdigit()
    assert parts[1].isdigit()
    assert parts[2][:1].isdigit()


def test_version_matches_pyproject():
    """__version__ must not drift from the declared build metadata."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text
    assert 'name = "pengyos-attest"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-attest")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-attest is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-attest = "pengyos_attest.cli:main"' in text


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-attest" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-attest" in out
    # All six documented subcommands are advertised.
    for name in ("append", "show", "verify", "export", "sign", "keygen"):
        assert name in out


def test_all_subcommands_are_implemented(tmp_path, capsys):
    # Items 3–7 landed: ``append``, ``verify``, ``show``, ``export``, ``sign``
    # and ``keygen`` are all real. Against a scratch chain each must succeed
    # (exit 0) — none of them may fall through to the (now unused)
    # not-implemented exit code (3).
    log = tmp_path / "chain.jsonl"
    seed = tmp_path / "keygen.key"
    cases = [
        ["append", "--log", str(log), "--type", "build",
         "--data", '{"commit": "abc123"}'],
        ["verify", "--log", str(log)],
        ["show", "--log", str(log)],
        ["show", "--log", str(log), "--json", "--tail", "1"],
        ["export", "--log", str(log), "--out", str(tmp_path / "bundle.json")],
        ["keygen", "--seed", str(seed)],
        ["sign", "--log", str(log), "--signing-key", str(seed)],
        ["verify", "--log", str(log), "--public-key",
         str(Path(seed).with_name("keygen.key.pub"))],
    ]
    for argv in cases:
        rc = main(argv)
        capsys.readouterr()
        assert rc == EXIT_OK, argv


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "append", "--log", "c.jsonl"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def test_append_parser_defaults():
    args = build_parser().parse_args(["append", "--log", "chain.jsonl"])
    assert args.command == "append"
    assert args.log == "chain.jsonl"
    assert args.event_type == DEFAULT_EVENT_TYPE
    assert args.data == DEFAULT_DATA


def test_append_parser_full():
    args = build_parser().parse_args(
        [
            "append",
            "--log",
            "c.jsonl",
            "--type",
            "build",
            "--data",
            '{"commit": "abc123"}',
        ]
    )
    assert args.log == "c.jsonl"
    assert args.event_type == "build"
    assert args.data == '{"commit": "abc123"}'


def test_append_log_is_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["append", "--type", "build"])
    assert exc.value.code == 2


def test_show_parser_defaults():
    args = build_parser().parse_args(["show", "--log", "chain.jsonl"])
    assert args.command == "show"
    assert args.log == "chain.jsonl"
    assert args.tail is None
    assert args.json is False


def test_show_parser_full():
    args = build_parser().parse_args(["show", "--log", "c.jsonl", "--tail", "5", "--json"])
    assert args.tail == 5
    assert isinstance(args.tail, int)
    assert args.json is True


def test_verify_parser():
    args = build_parser().parse_args(["verify", "--log", "chain.jsonl"])
    assert args.command == "verify"
    assert args.log == "chain.jsonl"
    assert args.anchor is None
    assert args.public_key is None


def test_sign_parser():
    args = build_parser().parse_args(
        ["sign", "--log", "c.jsonl", "--signing-key", "k.key"])
    assert args.command == "sign"
    assert args.log == "c.jsonl"
    assert args.signing_key == "k.key"


def test_sign_signing_key_is_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["sign", "--log", "c.jsonl"])
    assert exc.value.code == 2


def test_keygen_parser():
    args = build_parser().parse_args(["keygen", "--seed", "k.key"])
    assert args.command == "keygen"
    assert args.seed == "k.key"


def test_keygen_seed_is_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["keygen"])
    assert exc.value.code == 2


def test_verify_parser_anchor():
    args = build_parser().parse_args(
        ["verify", "--log", "chain.jsonl", "--anchor", "ab" * 32]
    )
    assert args.anchor == "ab" * 32


def test_export_parser_defaults_and_required_out():
    args = build_parser().parse_args(["export", "--log", "c.jsonl", "--out", "b.json"])
    assert args.command == "export"
    assert args.log == "c.jsonl"
    assert args.out == "b.json"


def test_export_out_is_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["export", "--log", "c.jsonl"])
    assert exc.value.code == 2
