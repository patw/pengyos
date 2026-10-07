"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, bad usage),
* ``compare`` / ``score`` are wired: identical trees are a success, differing
  trees list the offending path and score below 1, ``--json`` writes the fixed
  schema, and ``--ignore`` hides a noisy file,
* error paths: an unreadable file is exit 1, an invalid ``--ignore`` glob is
  exit 2 (a low score is still a success — exit 0),
* the CLI argument surface (``compare`` / ``score``) is registered with the
  documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
import os
from pathlib import Path

import pytest

import pengyos_repro as pkg
from pengyos_repro import __version__
from pengyos_repro import cli
from pengyos_repro.cli import (
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
    assert 'name = "pengyos-repro"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-repro")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-repro is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-repro = "pengyos_repro.cli:main"' in text


def test_python_module_entry_point_exists():
    """``python -m pengyos_repro`` is wired to the same entry point."""
    text = (REPO_ROOT / "pengyos_repro" / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from .cli import main" in text
    assert "sys.exit(main())" in text


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-repro" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-repro" in out
    # Both documented subcommands are advertised.
    assert "compare" in out
    assert "score" in out


def _pair(tmp_path, *, a_extra=None, b_extra=None):
    """Create two sibling build dirs ``tmp_path/a`` and ``tmp_path/b`` sharing a
    common file, plus optional extra files on each side. Returns ``(a, b)``."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "common.txt").write_bytes(b"identical")
    (b / "common.txt").write_bytes(b"identical")
    for rel, data in (a_extra or {}).items():
        (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rel).write_bytes(data)
    for rel, data in (b_extra or {}).items():
        (b / rel).parent.mkdir(parents=True, exist_ok=True)
        (b / rel).write_bytes(data)
    return a, b


def test_main_compare_identical_trees_is_success(capsys, tmp_path):
    a, b = _pair(tmp_path)
    rc = main(["compare", str(a), str(b)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "REPRODUCIBLE" in out


def test_main_compare_lists_differing_file(capsys, tmp_path):
    a, b = _pair(tmp_path, a_extra={"changed.txt": b"first build"},
                 b_extra={"changed.txt": b"second build"})
    rc = main(["compare", str(a), str(b)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "changed.txt" in out
    assert "NOT REPRODUCIBLE" in out


def test_main_compare_writes_json(capsys, tmp_path):
    import json
    a, b = _pair(
        tmp_path,
        a_extra={"changed.txt": b"one"},
        b_extra={"changed.txt": b"two"},
    )
    out_json = tmp_path / "report.json"
    rc = main(["compare", str(a), str(b), "--json", str(out_json)])
    written = json.loads(out_json.read_text(encoding="utf-8"))
    assert rc == EXIT_OK
    assert set(written) == {"score", "identical", "differing", "missing"}
    assert written["identical"] == ["common.txt"]
    assert written["differing"] == [{"path": "changed.txt", "reason": "content differs"}]


def test_main_compare_ignore_hides_file(capsys, tmp_path):
    a, b = _pair(tmp_path, a_extra={"random-seed": b"aaa"},
                 b_extra={"random-seed": b"zzz"})
    # Without hiding: the random-seed differs.
    main(["compare", str(a), str(b)])
    capsys.readouterr()
    # With the ignore: identical again.
    rc = main(
        ["compare", str(a), str(b), "--ignore", "random-seed"]
    )
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "REPRODUCIBLE" in out


def test_main_score_prints_float_in_unit_interval(capsys, tmp_path):
    a, b = _pair(tmp_path)
    rc = main(["score", str(a), str(b)])
    out = capsys.readouterr().out.strip()
    assert rc == EXIT_OK
    value = float(out)
    assert value == 1.0


def test_main_score_differing_below_one(capsys, tmp_path):
    a, b = _pair(
        tmp_path,
        a_extra={"changed.txt": b"one"},
        b_extra={"changed.txt": b"two"},
    )
    rc = main(["score", str(a), str(b)])
    out = capsys.readouterr().out.strip()
    assert rc == EXIT_OK
    assert 0.0 <= float(out) < 1.0


def test_main_compare_bad_ignore_pattern_is_usage_error(capsys, tmp_path):
    a, b = _pair(tmp_path)
    # An invalid glob (unterminated char class) is a usage error (exit 2), not a
    # crash.
    rc = main(["compare", str(a), str(b), "--ignore", "[abc"])
    err = capsys.readouterr().err
    assert rc == 2  # EXIT_USAGE
    assert "unterminated" in err.lower()


def test_main_compare_unreadable_file_is_failure(capsys, tmp_path):
    a, b = _pair(tmp_path)
    secret = a / "secret.txt"
    secret.write_bytes(b"top secret")
    original = secret.stat().st_mode
    try:
        os.chmod(secret, 0o000)  # blocks even the owner (non-root test user)
        rc = main(["compare", str(a), str(b)])
    finally:
        os.chmod(secret, original)
    err = capsys.readouterr().err
    assert rc == 1  # EXIT_FAIL: the command ran but could not read a file
    assert "secret.txt" in err


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "compare", "a", "b"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def test_compare_parser_positionals_and_defaults(tmp_path):
    args = build_parser().parse_args(
        ["compare", str(tmp_path / "a"), str(tmp_path / "b")]
    )
    assert args.command == "compare"
    assert args.dirA == str(tmp_path / "a")
    assert args.dirB == str(tmp_path / "b")
    assert args.json_out is None
    assert args.ignore_patterns == []


def test_compare_parser_json_and_ignore_flags(tmp_path):
    args = build_parser().parse_args(
        [
            "compare",
            str(tmp_path / "a"),
            str(tmp_path / "b"),
            "--json",
            str(tmp_path / "report.json"),
            "--ignore",
            "**/*.so",
            "--ignore",
            "**/*.pyc",
        ]
    )
    assert args.json_out == str(tmp_path / "report.json")
    assert args.ignore_patterns == ["**/*.so", "**/*.pyc"]


def test_score_parser_positionals_and_defaults(tmp_path):
    args = build_parser().parse_args(
        ["score", str(tmp_path / "a"), str(tmp_path / "b")]
    )
    assert args.command == "score"
    assert args.dirA == str(tmp_path / "a")
    assert args.dirB == str(tmp_path / "b")
    assert args.ignore_patterns == []


def test_score_parser_ignore_flag(tmp_path):
    args = build_parser().parse_args(
        [
            "score",
            str(tmp_path / "a"),
            str(tmp_path / "b"),
            "--ignore",
            "**/*.tmp",
        ]
    )
    assert args.ignore_patterns == ["**/*.tmp"]


def test_compare_parser_requires_dirs():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["compare", "only-one"])
    assert exc.value.code == 2


def test_score_parser_requires_dirs():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["score", "only-one"])
    assert exc.value.code == 2
