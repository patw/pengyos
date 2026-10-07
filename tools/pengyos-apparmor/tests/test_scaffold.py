"""Smoke tests for the pengyos-apparmor repository scaffold.

These tests pin the scaffold contract: the package imports, its version
agrees with ``pyproject.toml``, the CLI advertises ``--version``/``--help``
and exits cleanly, and the repo layout (``profiles/``, GitHub-ready files)
exists. They run with no network, no root, and no AppArmor support.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_package_imports_and_has_version():
    pengyos_apparmor = importlib.import_module("pengyos_apparmor")
    assert pengyos_apparmor.__version__ == _pyproject_version()
    assert pengyos_apparmor.__version__ == "0.1.0"


def test_version_agrees_with_pyproject():
    import pengyos_apparmor

    assert pengyos_apparmor.__version__ == _pyproject_version()


def test_pyproject_declares_stdlib_only_runtime():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    # Stdlib-only runtime: no hard runtime dependencies.
    assert data["project"].get("dependencies", []) == []
    # The CLI entry point is declared.
    assert data["project"]["scripts"]["pengyos-apparmor"] == (
        "pengyos_apparmor.cli:main"
    )


def test_cli_main_returns_zero():
    from pengyos_apparmor.cli import main

    assert main([]) == 0


def test_cli_version_flag(capsys):
    from pengyos_apparmor.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "pengyos-apparmor" in out
    assert importlib.import_module("pengyos_apparmor").__version__ in out


def test_cli_help_flag(capsys):
    from pengyos_apparmor.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "pengyos-apparmor" in out
    assert "--version" in out


def test_profiles_directory_exists_with_readme():
    profiles = ROOT / "profiles"
    assert profiles.is_dir()
    assert (profiles / "README.md").is_file()


def test_github_ready_files_exist():
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        assert (ROOT / name).is_file(), f"missing {name}"
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_python_module_entrypoint(capsys, tmp_path):
    # `python -m pengyos_apparmor` works without an editable install.
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_apparmor", "--version"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "pengyos-apparmor" in proc.stdout
