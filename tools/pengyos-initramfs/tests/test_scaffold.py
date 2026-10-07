"""Smoke tests for the repository scaffold.

These verify the project is wired up and importable: the package exposes a
version, the CLI surface matches spec.md, and the GitHub-ready files exist.
The deterministic cpio builder itself is covered by dedicated test modules.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

import pengyos_initramfs
from pengyos_initramfs.cli import (
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
    main,
)

# The tool directory (parent of tests/).
TOOL_DIR = Path(__file__).resolve().parent.parent


def _run(argv):
    """Run ``main(argv)``, capturing stdout/stderr.

    Returns (exit_code_or_None, out, err). A SystemExit is translated to its
    int code (or None when it is a non-int).
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    return code, out.getvalue(), err.getvalue()


def test_version_string():
    assert isinstance(pengyos_initramfs.__version__, str)
    assert pengyos_initramfs.__version__.count(".") == 2


def test_version_matches_pyproject():
    """pyproject.toml sources its version from the package (single source of truth)."""
    pyproject = (TOOL_DIR / "pyproject.toml").read_text()
    assert 'attr = "pengyos_initramfs.__version__"' in pyproject


def test_cli_version():
    code, out, _ = _run(["--version"])
    assert code == EXIT_OK or code == 0
    assert "pengyos-initramfs" in out
    assert pengyos_initramfs.__version__ in out


def test_parser_requires_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([])
    assert exc.value.code == EXIT_USAGE


def test_build_requires_all_flags():
    # Missing --root.
    with pytest.raises(SystemExit) as exc:
        main(["build", "--spec", "s.toml", "--out", "o.gz"])
    assert exc.value.code == EXIT_USAGE


def test_build_root_not_found():
    code, _, err = _run(
        ["build", "--root", "/does/not/exist-xyz", "--spec", "s.toml", "--out", "o.gz"]
    )
    assert code == EXIT_USAGE
    assert "root directory not found" in err


def test_build_spec_not_found(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    code, _, err = _run(
        ["build", "--root", str(root), "--spec", str(tmp_path / "missing.toml"), "--out", "o.gz"]
    )
    assert code == EXIT_USAGE
    assert "spec file not found" in err


def test_build_valid_inputs_succeeds(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    spec = tmp_path / "initramfs.toml"
    spec.write_text("# spec\n", encoding="utf-8")
    out = tmp_path / "init.cpio.gz"
    code, _, err = _run(
        ["build", "--root", str(root), "--spec", str(spec), "--out", str(out)]
    )
    assert code == EXIT_OK, err
    assert out.is_file()
    assert out.read_bytes()[:2] == b"\x1f\x8b"  # gzip container


def _repo_files():
    return {
        "README.md": TOOL_DIR / "README.md",
        "LICENSE": TOOL_DIR / "LICENSE",
        "CHANGELOG.md": TOOL_DIR / "CHANGELOG.md",
        ".gitignore": TOOL_DIR / ".gitignore",
    }


def test_github_ready_files_exist():
    for name, path in _repo_files().items():
        assert path.is_file(), f"missing {name}"
        assert path.stat().st_size > 0, f"{name} is empty"


def test_license_is_mit_with_copyright():
    text = (TOOL_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_readme_documents_interface():
    text = (TOOL_DIR / "README.md").read_text(encoding="utf-8")
    assert "pengyos-initramfs build --root DIR --spec initramfs.toml --out init.cpio.gz" in text


def test_python_m_entrypoint_version():
    """``python -m pengyos_initramfs --version`` works (package-level __main__)."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_initramfs", "--version"],
        cwd=TOOL_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "pengyos-initramfs" in proc.stdout
    assert pengyos_initramfs.__version__ in proc.stdout


def test_pyproject_declares_console_script_and_no_deps():
    text = (TOOL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert "pengyos-initramfs = " in text
    assert "pengyos_initramfs.cli:main" in text
    # No runtime dependencies (standard library only).
    assert "dependencies = []" in text
