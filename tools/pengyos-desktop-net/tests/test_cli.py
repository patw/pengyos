"""Tests for pengyos_desktop_net.cli."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pengyos_desktop_net import emit as em
from pengyos_desktop_net import lint as ln
from pengyos_desktop_net import profile as prof

from pengyos_desktop_net import cli


def test_cli_emit_writes_files(tmp_path: Path):
    out = str(tmp_path / "out")
    rc = cli.main(["emit", "--out", out])
    assert rc == 0
    artifacts = em.emit(out)
    assert len(artifacts) == 6
    # The panel default with the network applet is on disk.
    panel = next(a for a in artifacts if a.kind == "cosmic-panel")
    rel = panel.path.lstrip("/")
    assert rel.endswith("panel.toml")
    written = (Path(out) / rel).read_text("utf-8")
    assert prof.COSMIC_APPLET_NETWORK in written


def test_cli_lint_good_returns_zero(tmp_path: Path):
    out = str(tmp_path / "out")
    cli.main(["emit", "--out", out])
    assert cli.main(["lint", "--overlay", out]) == 0


def test_cli_lint_bad_returns_one(tmp_path: Path):
    out = str(tmp_path / "out")
    cli.main(["emit", "--out", out])
    (Path(out) / "wifi-firmware.txt").unlink()
    assert cli.main(["lint", "--overlay", out]) == 1


def test_cli_emit_json(capsys):
    # Drain anything (e.g. a prior emit's "wrote ..." lines) so the JSON is the
    # only thing in the captured buffer.
    capsys.readouterr()
    rc = cli.main(["emit", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "pengyos-desktop-net"


def test_cli_lint_json(tmp_path: Path, capsys):
    out = str(tmp_path / "out")
    cli.main(["emit", "--out", out])
    capsys.readouterr()  # drop the emit's "wrote ..." lines
    rc = cli.main(["lint", "--overlay", out, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_cli_version(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit from --version")


def test_cli_requires_a_command():
    import argparse

    try:
        cli.main([])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("expected SystemExit for missing command")


def test_cli_module_runs(tmp_path: Path):
    """``python -m pengyos_desktop_net emit`` works end to end."""
    out = str(tmp_path / "out")
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_desktop_net", "emit", "--out", out],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    artifacts = em.emit(out)
    assert len(artifacts) == 6
