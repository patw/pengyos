"""Scaffold tests for pengyos-btrfs-layout.

These lock in the package structure, the ``plan``/``snapshot``/``rollback``
subcommand argument contract (from ``spec.md``'s Interface), the normalized
exit codes, the ``plan`` handler's behavior (writes ``layout.toml`` to
``--out``), the wired ``snapshot``/``rollback`` dispatch (safe by default —
their full behavior is pinned in ``tests/test_cli_handlers.py``), and the
``python -m`` entry point.

No network, no root, no external tools — only in-process CLI calls and one
``python -m`` subprocess.
"""

import subprocess
import sys

import tomllib

from pengyos_btrfs import __version__
from pengyos_btrfs import constants as c
from pengyos_btrfs.cli import build_parser, main
from pengyos_btrfs.commands import MockRunner
from pengyos_btrfs.layout import canonical_layout
from pengyos_btrfs.toml_io import layout_from_toml


def test_scaffold_version_is_semver():
    assert __version__ == "0.1.0"


def test_scaffold_constants_present():
    # Exit codes.
    assert (c.EXIT_OK, c.EXIT_ERROR, c.EXIT_USAGE, c.EXIT_NOT_IMPLEMENTED) == (
        0,
        1,
        2,
        3,
    )
    # The btrfs binary name and snapshot naming conventions.
    assert c.BTRFS_BIN == "btrfs"
    assert c.SNAPSHOT_DIR == "@snapshots"
    assert c.ROLLBACK_CONFIRM_FLAG == "--yes"


def _subparser_action(parser, name):
    """Return the ``ArgumentParser`` action for subcommand *name*."""
    for action in parser._subparsers._actions:
        if getattr(action, "choices", None) and name in action.choices:
            return action
    raise AssertionError(f"subcommand {name!r} not found")


def test_scaffold_parser_has_all_subcommands():
    parser = build_parser()
    for name in ("plan", "snapshot", "rollback"):
        action = _subparser_action(parser, name)
        assert name in action.choices


def test_scaffold_plan_arg_contract():
    """``plan`` exposes the spec.md Interface contract (``--out``)."""
    plan = _subparser_action(build_parser(), "plan").choices["plan"]
    dests = {a.dest for a in plan._actions}
    assert "out" in dests, "plan is missing --out"

    out = next(a for a in plan._actions if a.dest == "out")
    assert out.required is True, "plan --out must be required"


def test_scaffold_snapshot_arg_contract():
    """``snapshot <name> --json``: a positional name and a --json flag."""
    snapshot = _subparser_action(build_parser(), "snapshot").choices["snapshot"]
    dests = {a.dest for a in snapshot._actions}
    assert "name" in dests, "snapshot is missing positional <name>"
    assert "json" in dests, "snapshot is missing --json"

    # The name is a single positional (nargs None => exactly one).
    name = next(a for a in snapshot._actions if a.dest == "name")
    assert name.nargs is None
    # --json is a store_true flag.
    js = next(a for a in snapshot._actions if a.dest == "json")
    assert js.const is True
    assert not js.default


def test_scaffold_rollback_arg_contract():
    """``rollback <name>``: a single positional snapshot name."""
    rollback = _subparser_action(build_parser(), "rollback").choices["rollback"]
    dests = {a.dest for a in rollback._actions}
    assert "name" in dests, "rollback is missing positional <name>"
    name = next(a for a in rollback._actions if a.dest == "name")
    assert name.nargs is None


def test_plan_dispatch_writes_layout_toml(tmp_path, capsys):
    """``plan`` is implemented: it writes the canonical layout to ``--out``,
    and the written document is valid TOML that round-trips to the canonical
    layout."""
    out = tmp_path / "layout.toml"
    code = main(["plan", "--out", str(out)])
    assert code == c.EXIT_OK
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    # Valid TOML per the authoritative reader.
    assert tomllib.loads(text)["filesystem"] == "pengyos"
    # And it round-trips to the canonical layout, byte-faithfully.
    assert layout_from_toml(text) == canonical_layout()
    # The success message names the file it wrote.
    captured = capsys.readouterr()
    assert "planned 4 subvolumes" in captured.out
    assert str(out) in captured.out


def test_plan_dispatch_unwritable_path_is_a_clean_error(tmp_path, capsys):
    """An unwritable ``--out`` (missing parent dir) is a clean EXIT_ERROR on
    stderr — never a traceback."""
    out = tmp_path / "missing_dir" / "layout.toml"
    code = main(["plan", "--out", str(out)])
    assert code == c.EXIT_ERROR
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "could not write" in captured.err
    assert str(out) in captured.err
    assert not out.exists()


def test_snapshot_dispatch_is_wired(monkeypatch):
    """``snapshot`` is implemented (no longer EXIT_NOT_IMPLEMENTED): with
    ``--json`` it generates the plan and exits 0 without executing anything.
    Full handler behavior is pinned in tests/test_cli_handlers.py."""
    runner = MockRunner()
    monkeypatch.setattr("pengyos_btrfs.cli.make_runner", lambda: runner)
    assert main(["snapshot", "boot-2026-09-16", "--json"]) == c.EXIT_OK
    assert runner.calls == [], "--json must be generation-only"


def test_rollback_dispatch_is_wired_and_safe(monkeypatch):
    """``rollback`` is implemented and safe by default: without ``--yes`` the
    gate refuses it (EXIT_ERROR, no runner call). Full handler behavior is
    pinned in tests/test_cli_handlers.py."""
    runner = MockRunner()
    monkeypatch.setattr("pengyos_btrfs.cli.make_runner", lambda: runner)
    assert main(["rollback", "boot-2026-09-16"]) == c.EXIT_ERROR
    assert runner.calls == []


def test_scaffold_plan_missing_out_is_usage_error():
    """A missing required --out is an argparse usage error (exit 2)."""
    code = main(["plan"])
    assert code == c.EXIT_USAGE


def test_scaffold_snapshot_missing_name_is_usage_error():
    code = main(["snapshot"])
    assert code == c.EXIT_USAGE


def test_scaffold_no_command_is_usage_error():
    code = main([])
    assert code == c.EXIT_USAGE


def test_scaffold_version_flag():
    # --version exits 0 through the normalized path.
    code = main(["--version"])
    assert code == 0


def test_scaffold_python_m_entry_point():
    # `python -m pengyos_btrfs --version` runs the package entry point.
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_btrfs", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert __version__ in (result.stdout + result.stderr)
