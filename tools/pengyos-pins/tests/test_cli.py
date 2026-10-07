"""Tests for the CLI (pengyos_pins.cli)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import pengyos_pins
from pengyos_pins.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _run_cli(*argv: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a real process: ``python -m pengyos_pins ...``."""
    base_env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pengyos_pins", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=base_env,
    )


class TestModule:
    def test_version_string(self):
        assert pengyos_pins.__version__
        # The pyproject version and the package version must agree.
        import tomllib

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert pyproject["project"]["version"] == pengyos_pins.__version__

    def test_console_script_declared(self):
        import tomllib

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert pyproject["project"]["scripts"]["pengyos-pins"] == (
            "pengyos_pins.cli:main"
        )


class TestParser:
    def test_parser_prog_name(self):
        assert build_parser().prog == "pengyos-pins"

    def test_no_command_returns_2(self, capsys):
        assert main([]) == 2
        err = capsys.readouterr().err
        assert "usage:" in err

    def test_version_subcommand_returns_0(self, capsys):
        assert main(["version"]) == 0
        out = capsys.readouterr().out
        assert out.strip() == pengyos_pins.__version__

    def test_unknown_command_returns_2(self):
        # argparse exits with 2 on an unrecognised subcommand.
        with pytest.raises(SystemExit) as exc:
            main(["not-a-command"])
        assert exc.value.code == 2

    def test_help_exits_0(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "pengyos-pins" in out


class TestCliProcess:
    def test_version_subprocess(self):
        proc = _run_cli("version")
        assert proc.returncode == 0
        assert proc.stdout.strip() == pengyos_pins.__version__

    def test_global_version_flag(self):
        proc = _run_cli("--version")
        assert proc.returncode == 0
        assert f"pengyos-pins {pengyos_pins.__version__}" in proc.stdout


class TestLockAndDrift:
    """The `lock` (write a resolved lockfile) and `drift` (diff pins vs lock)
    subcommands.

    The committed fixture `tests/fixtures/valid.toml` is a valid manifest; we
    lock it, then drift against it (no drift) and against a mutated copy (drift).
    """

    def test_lock_writes_lockfile(self, tmp_path: Path, capsys):
        lock_path = tmp_path / "pins.lock.toml"
        rc = main(
            ["lock", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)]
        )
        assert rc == 0
        assert lock_path.is_file()
        assert "locked:" in capsys.readouterr().out

    def test_lock_then_drift_has_no_drift(self, tmp_path: Path, capsys):
        lock_path = tmp_path / "pins.lock.toml"
        assert main(["lock", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)]) == 0
        rc = main(["drift", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)])
        assert rc == 0
        assert "no drift" in capsys.readouterr().out

    def test_drift_detects_version_change(self, tmp_path: Path, capsys):
        # Lock a baseline, then bump a version in a copy of the manifest.
        lock_path = tmp_path / "pins.lock.toml"
        assert main(["lock", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)]) == 0
        bumped = tmp_path / "bumped.toml"
        bumped.write_text(
            (FIXTURES / "valid.toml").read_text().replace("7.2.5", "7.3.0")
        )
        rc = main(["drift", str(bumped), "--lock", str(lock_path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "drift detected" in out
        assert "7.2.5 -> 7.3.0" in out

    def test_drift_missing_lockfile_returns_1(self, tmp_path: Path, capsys):
        rc = main(
            ["drift", str(FIXTURES / "valid.toml"), "--lock", str(tmp_path / "nope.lock.toml")]
        )
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_lock_rejects_invalid_manifest(self, tmp_path: Path, capsys):
        # The SPDX fixture fails validation; lock must refuse to write it.
        rc = main(["lock", str(FIXTURES / "bad_spdx.toml"), "--lock", str(tmp_path / "x.lock.toml")])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err
        assert not (tmp_path / "x.lock.toml").is_file()

    def test_drift_rejects_invalid_manifest(self, tmp_path: Path, capsys):
        lock_path = tmp_path / "pins.lock.toml"
        assert main(["lock", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)]) == 0
        rc = main(["drift", str(FIXTURES / "bad_spdx.toml"), "--lock", str(lock_path)])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_lock_default_path_writes_pins_lock_toml(self, tmp_path: Path, monkeypatch):
        # With no --lock, `lock` writes pins.lock.toml in the CWD.
        monkeypatch.chdir(tmp_path)
        rc = main(["lock", str(FIXTURES / "valid.toml")])
        assert rc == 0
        assert (tmp_path / "pins.lock.toml").is_file()

    def test_drift_default_path_reads_pins_lock_toml(self, tmp_path: Path, monkeypatch, capsys):
        # With no --lock, `drift` reads pins.lock.toml from the CWD.
        (tmp_path / "pins.lock.toml").write_text(
            (FIXTURES / "valid.toml").read_text()
        )
        monkeypatch.chdir(tmp_path)
        rc = main(["drift", str(FIXTURES / "valid.toml")])
        assert rc == 0
        assert "no drift" in capsys.readouterr().out

    def test_lock_and_drift_subprocess(self, tmp_path: Path):
        lock_path = tmp_path / "pins.lock.toml"
        proc = _run_cli(
            "lock", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)
        )
        assert proc.returncode == 0
        proc = _run_cli(
            "drift", str(FIXTURES / "valid.toml"), "--lock", str(lock_path)
        )
        assert proc.returncode == 0
        assert "no drift" in proc.stdout
