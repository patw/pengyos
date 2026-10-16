"""GitHub-ready contract + the stdlib-only promise.

The repo's tool contract (CONTRIBUTING.md, ``tools/README.md``) says a tool
ships a README, an MIT LICENSE naming Pat Wendorf, a CHANGELOG, a .gitignore, a
spec, a verify.toml and a green suite — and that the docs stay in sync with the
code. This test is the guard that keeps them in sync: it reads the subcommands
out of the real parser and checks the README documents each one.

The second half is the spec's hard constraint: **standard library only**. It is
checked by walking the package's imports, so a dependency cannot sneak in
through a helper module.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

try:  # tomllib is 3.11+; the tool itself is 3.9-clean, only this check needs it
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    pytest.skip("tomllib requires Python 3.11", allow_module_level=True)

from pengyos_storage.cli import build_parser

HERE = Path(__file__).parent
ROOT = HERE.parent
PACKAGE = ROOT / "pengyos_storage"

STDLIB = {
    "__future__",
    "argparse", "ast", "datetime", "fnmatch", "heapq", "html", "json", "math",
    "os", "re", "shutil", "stat", "sys", "textwrap", "time", "typing",
    "dataclasses", "itertools", "pathlib", "collections", "enum", "io", "tomllib",
}


def _read(*parts: str) -> str:
    return (ROOT / Path(*parts)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The contract files
# ---------------------------------------------------------------------------


def test_contract_files_exist_and_are_not_empty():
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore",
                 "pyproject.toml", "spec.md", "verify.toml"):
        path = ROOT / name
        assert path.is_file(), f"{name} is missing — the tool is not GitHub-ready"
        assert path.stat().st_size > 20, f"{name} is empty"


def test_license_is_mit_with_the_right_copyright():
    text = _read("LICENSE")
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_changelog_is_keep_a_changelog_and_names_the_version():
    text = _read("CHANGELOG.md")
    assert text.startswith("# Changelog")
    assert "## [0.1.0]" in text
    assert "Keep a Changelog" in text


def test_changelog_version_matches_the_package_version():
    from pengyos_storage import VERSION

    assert f"## [{VERSION}]" in _read("CHANGELOG.md")
    assert f'version = "{VERSION}"' in _read("pyproject.toml")


def test_readme_documents_every_subcommand_the_cli_advertises():
    readme = _read("README.md").lower()
    parser = build_parser()
    action = parser._subparsers._group_actions[0]
    commands = sorted(action.choices)
    assert commands, "the parser advertises no subcommands"
    for cmd in commands:
        assert cmd in readme, f"README does not document `{cmd}`"


def test_readme_states_the_read_only_and_stdlib_promises():
    readme = _read("README.md").lower()
    assert "read-only" in readme
    assert "standard library" in readme or "stdlib" in readme


def test_verify_toml_runs_this_suite_in_the_right_directory():
    with (ROOT / "verify.toml").open("rb") as fh:
        checks = tomllib.load(fh).get("check", [])
    assert checks, "verify.toml declares no checks"
    runs = [c for c in checks if "pytest" in c["run"]]
    assert runs, "verify.toml does not run pytest"
    expected = f"tools/{ROOT.name}"
    assert runs[0]["cwd"] == expected, f"cwd is {runs[0]['cwd']}, expected {expected}"
    assert runs[0].get("expect", "exit0") == "exit0"


def test_gitignore_keeps_reports_out_of_version_control():
    text = _read(".gitignore")
    assert "__pycache__" in text
    assert "html" in text or "reports" in text


def test_pyproject_declares_no_runtime_dependencies():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    assert data["project"]["dependencies"] == [], "runtime deps must be stdlib-only"
    assert "pengyos-storage" in data["project"]["scripts"]


# ---------------------------------------------------------------------------
# Standard library only
# ---------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_package_imports_only_the_standard_library():
    files = sorted(PACKAGE.glob("*.py"))
    assert files, "no package modules found"
    for path in files:
        for name in _imports(path):
            assert name in STDLIB, f"{path.name} imports {name!r}, which is not stdlib"


def test_tests_import_only_stdlib_and_the_package():
    for path in sorted((ROOT / "tests").glob("*.py")):
        for name in _imports(path):
            assert name in STDLIB | {"pengyos_storage", "conftest", "pytest"}, (
                f"{path.name} imports {name!r}"
            )


def test_no_network_or_subprocess_anywhere():
    for path in sorted(PACKAGE.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("import socket", "import urllib", "import requests",
                          "import subprocess", "urlopen", "Popen"):
            assert forbidden not in text, f"{path.name} uses {forbidden}"


def test_the_tool_never_writes_inside_the_scanned_tree():
    # The only writes in the package are the -o document; check the call sites.
    for path in sorted(PACKAGE.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "open(" in text:
            assert path.name in {"cli.py", "scan.py"}, (
                f"{path.name} opens a file — the scan is read-only"
            )
