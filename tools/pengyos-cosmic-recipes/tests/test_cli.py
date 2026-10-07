"""Tests for the recipes-lane CLI (``pengyos_cosmic_recipes.cli``).

Exercise the ``manifest`` (list), ``emit`` (emit a group's recipes) and ``lint``
(closure linter) subcommands. In-process calls to ``cli.main``; no subprocess,
network, root, or hardware.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from pengyos_cosmic_recipes.cli import main


def _run(*args: str) -> int:
    """Invoke the CLI in-process and return its exit code."""
    return main(list(args))


def test_manifest_human_lists_core_group(capsys):
    """``manifest`` prints the core group with pinned tags + licenses."""
    assert _run("manifest") == 0
    out = capsys.readouterr().out
    assert "# core" in out
    for name in ("cosmic-protocols", "libcosmic", "cosmic-comp", "cosmic-session"):
        assert name in out


def test_manifest_group_filter(capsys):
    """``manifest --group core`` lists only the core components."""
    assert _run("manifest", "--group", "core") == 0
    out = capsys.readouterr().out
    assert "# core" in out
    # Nothing outside core is printed.
    assert "apps" not in out


def test_manifest_json_schema():
    """``manifest --json`` emits a machine-readable summary."""
    payload = json.loads(_capture_stdout())
    assert payload["command"] == "manifest"
    assert payload["ok"] is True
    names = [c["name"] for c in payload["components"]]
    assert names == [
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
        "cosmic-greeter",
        "cosmic-panel",
        "cosmic-launcher",
        "cosmic-applets",
        "cosmic-bg",
        "cosmic-randr",
        "cosmic-settings",
        "cosmic-settings-daemon",
        "cosmic-osd",
        "cosmic-notifications",
        "cosmic-idle",
        "cosmic-icons",
        "cosmic-files",
        "cosmic-edit",
        "cosmic-term",
        "cosmic-store",
        "cosmic-screenshot",
        "cosmic-player",
    ]
    by = {c["name"]: c for c in payload["components"]}
    assert by["cosmic-comp"]["tag"]  # a pinned tag is present
    assert by["cosmic-comp"]["license"]  # a license is present
    assert set(by["cosmic-comp"]["deps"]) == {"libcosmic", "cosmic-protocols"}


def _capture_stdout() -> str:
    """Run ``manifest --json`` and return its stdout (capsys-free helper)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert _run("manifest", "--json") == 0
    return buf.getvalue()


def test_emit_via_main_writes_recipes(tmp_path):
    """``emit --out DIR --group core`` writes the four core recipes, exit 0."""
    out = tmp_path / "package"
    assert _run("emit", "--out", str(out), "--group", "core") == 0
    for name in ("cosmic-protocols", "libcosmic", "cosmic-comp", "cosmic-session"):
        assert (out / name / f"{name}.mk").is_file()


def test_emit_via_main_json(tmp_path):
    """``emit --json`` emits a machine-readable summary with ok=True."""
    out = tmp_path / "package"
    payload = json.loads(_capture_emit_json(out))
    assert payload["command"] == "emit"
    assert payload["ok"] is True
    assert payload["group"] == "core"
    assert payload["emitted"] == [
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
    ]


def _capture_emit_json(out) -> str:
    """Run ``emit --json`` and return its stdout (capsys-free helper)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert _run("emit", "--out", str(out), "--group", "core", "--json") == 0
    return buf.getvalue()


def test_lint_via_main_closed(tmp_path):
    """``lint --dir DIR`` exits 0 once core + login + shell + apps are emitted."""
    out = tmp_path / "package"
    assert _run("emit", "--out", str(out), "--group", "core") == 0
    assert _run("emit", "--out", str(out), "--group", "login") == 0
    assert _run("emit", "--out", str(out), "--group", "shell") == 0
    assert _run("emit", "--out", str(out), "--group", "apps") == 0
    assert _run("lint", "--dir", str(out)) == 0


def test_lint_via_main_open(tmp_path):
    """``lint --dir DIR`` exits 1 when a recipe is missing."""
    out = tmp_path / "package"
    assert _run("emit", "--out", str(out), "--group", "core") == 0
    import shutil

    shutil.rmtree(out / "libcosmic")
    assert _run("lint", "--dir", str(out)) == 1


def test_emit_login_via_main_writes_greeter(tmp_path):
    """``emit --group login`` writes cosmic-greeter's recipe, exit 0."""
    out = tmp_path / "package"
    assert _run("emit", "--out", str(out), "--group", "login") == 0
    assert (out / "cosmic-greeter" / "cosmic-greeter.mk").is_file()


def test_lint_core_login_via_main_closed(tmp_path):
    """``lint`` closes once core + login + shell + apps are emitted."""
    out = tmp_path / "package"
    assert _run("emit", "--out", str(out), "--group", "core") == 0
    assert _run("emit", "--out", str(out), "--group", "login") == 0
    assert _run("emit", "--out", str(out), "--group", "shell") == 0
    assert _run("emit", "--out", str(out), "--group", "apps") == 0
    assert _run("lint", "--dir", str(out)) == 0


def test_no_command_is_an_error():
    """A bare invocation (no subcommand) is an error (argparse exits non-zero)."""
    with pytest.raises(SystemExit):
        main([])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
