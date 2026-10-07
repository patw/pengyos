"""GitHub-ready contract (spec.md "GitHub-ready contract").

Asserts the repo ships the files that make it publishable as-is:
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"),
CHANGELOG.md, .gitignore, and a collectable, green test-suite.
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from conftest import ROOT

REQUIRED_FILES = ["README.md", "CHANGELOG.md", ".gitignore"]

# Every file the GitHub-ready contract ships. Used by the tracking check below:
# a deliverable present on disk but left un-``git add``-ed would pass a presence
# check but never reach GitHub, so presence and tracking are asserted together.
GITHUB_READY_FILES = ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore"]


def _git_root(start: Path) -> Optional[Path]:
    """The repository root containing ``start``, or ``None`` when not in one.

    ``git rev-parse --show-toplevel`` walks up from ``start`` to the checkout
    root, so the gate works from the repo root, a subdirectory, or a checkout
    anywhere on disk.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _is_tracked(path: Path) -> bool:
    """True when ``path`` is tracked in the git index.

    The GitHub-ready contract (spec.md) requires the files to be *tracked in the
    repo*, not merely present on disk: a deliverable left un-``git add``-ed
    would pass a presence check but never reach GitHub. ``git ls-files
    --error-unmatch`` returns 0 only for a file that is actually in the index,
    so it distinguishes "present but untracked" from "committed". Outside a git
    checkout there is no index to consult, so tracking cannot be asserted.
    """
    if not path.exists():
        return False
    root = _git_root(path.parent)
    if root is None:
        return False
    rel = path.resolve().relative_to(root)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _nontrivial(path: Path, min_lines: int = 5) -> None:
    assert path.exists(), f"{path.name} is missing"
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) >= min_lines, f"{path.name} is too short ({len(lines)} lines)"


def _package_surface(root: Path) -> list[Path]:
    """The shipped package's real source files (the surface the README maps).

    Every file under ``pengyos_hardening/`` except the implied ``__init__.py``
    files and ``__pycache__`` build artefacts. ``__init__.py`` is excluded on
    purpose: a package's ``__init__`` is implied by the directory, and the
    README tree omits the nested ``check/__init__.py`` by convention — asserting
    it would be a false drift. This is the set ``test_docs_in_sync`` requires to
    be named in the README's file tree.
    """
    pkg = root / "pengyos_hardening"
    if not pkg.is_dir():
        return []
    surface = []
    for path in pkg.rglob("*"):
        if path.suffix == ".pyc":
            continue
        if any(part == "__pycache__" for part in path.parts):
            continue
        if path.name == "__init__.py":
            continue
        if path.is_file():
            surface.append(path)
    return sorted(surface)


def _named_in_tree(rel: Path, tree: str) -> bool:
    """True when a package file (``rel`` relative to ``pengyos_hardening/``) is
    named in the README tree.

    Matched by its parent-joined path (``check/readers.py``) OR its bare
    basename (``readers.py``): the README tree nests the ``check/`` readers under
    ``check/``, so they appear as ``readers.py`` there, not ``check/readers.py``.
    Either form counts as "documented".
    """
    return str(rel) in tree or rel.name in tree


def test_readme_present_and_nontrivial():
    _nontrivial(ROOT / "README.md")


def test_changelog_present_and_nontrivial():
    _nontrivial(ROOT / "CHANGELOG.md")


@pytest.mark.parametrize("name", REQUIRED_FILES)
def test_required_files_exist(name: str):
    path = ROOT / name
    assert path.is_file(), f"{name} must be present in the repo"


@pytest.mark.parametrize("name", GITHUB_READY_FILES)
def test_required_files_tracked(name: str):
    """The GitHub-ready files must be *tracked* in the repo, not merely present.

    spec.md's contract requires the files to ship in the repo. A deliverable
    left un-``git add``-ed passes a presence check but never reaches GitHub, so
    this asserts ``git ls-files --error-unmatch`` (index membership) rather than
    just ``is_file()``. Outside a git checkout there is no index to consult, so
    the check defers (the file still has to exist, checked above).
    """
    path = ROOT / name
    assert path.is_file(), f"{name} must be present in the repo"
    assert _is_tracked(path), f"{name} must be tracked in the repo (git add it)"


def test_is_tracked_detects_untracked_file(root_path: Path):
    """Regression: a file present on disk but untracked is *not* reported as
    tracked — this is what makes ``test_required_files_tracked`` meaningful.

    A presence-only check would let an un-``git add``-ed deliverable through.
    The probe is written inside the checkout (so the index is consulted), staged
    to prove the positive, then removed so the repo is left clean.
    """
    probe = root_path / ".ghr_probe_tmp"
    try:
        probe.write_text("probe\n", encoding="utf-8")
        assert probe.exists()
        assert not _is_tracked(probe), "a freshly written file is untracked"

        subprocess.run(
            ["git", "add", str(probe)],
            cwd=str(root_path),
            check=True,
            capture_output=True,
        )
        assert _is_tracked(probe), "a staged file is tracked"
    finally:
        subprocess.run(
            ["git", "rm", "--cached", "--quiet", str(probe)],
            cwd=str(root_path),
            capture_output=True,
        )
        probe.unlink(missing_ok=True)


def test_license_is_mit():
    license_path = ROOT / "LICENSE"
    text = license_path.read_text(encoding="utf-8")
    assert "MIT License" in text, "LICENSE must be the MIT license"
    assert "Permission is hereby granted, free of charge" in text


def test_license_copyright_holder():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Pat Wendorf" in text, (
        "LICENSE must carry the exact copyright line"
    )


def test_suite_is_collectable():
    """The suite must at least *collect* — a collection error is a failure."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"pytest collection failed:\n{result.stdout}\n{result.stderr}"
    )
    # At least one real test must exist to be a meaningful suite.
    assert "test" in (result.stdout + result.stderr).lower()


# The classic "mark-a-stub-done" placeholders we refuse to ship.
_PLACEHOLDER_MARKERS = ("TODO", "FIXME", "XXX", "STUB")


def _test_function_code(tree: ast.AST, src: str):
    """Yield the executable code (docstrings stripped) of each ``test_*`` fn."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        body = list(node.body)
        # strip a leading docstring statement
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        if not body:
            continue
        starts = [b.lineno for b in body]
        ends = [getattr(b, "end_lineno", b.lineno) for b in body]
        yield "\n".join(src.splitlines()[min(starts) - 1 : max(ends)])


# Sentinel env var set on the gate subprocess so a nested ``pytest`` run knows
# it is already nested and returns early instead of spawning another ``pytest``
# (which would recurse forever: the gate runs the suite, the suite runs the
# gate, ...). The guard is the standard break for "a test that runs the runner".
GATE_SENTINEL = "PENGYOS_SUITE_GATE"


def _run_suite() -> subprocess.CompletedProcess:
    """Run the whole suite the way a CI/GitHub check would (fresh process).

    The GitHub-ready contract requires *a full green test suite*, not merely a
    collectable one, so this runs ``pytest -q`` in a subprocess against the repo
    root. Integration tests stay opt-in (``PENGYOS_IT=1``), so a plain run is
    hermetic and fast; the subprocess inherits the repo's ``pyproject.toml``
    (strict markers, deprecation-as-error) exactly as a real check would.
    """
    env = dict(os.environ)
    env["PENGYOS_IT"] = "0"  # keep the suite hermetic for the github gate
    env[GATE_SENTINEL] = "1"  # and stop the gate from recursing into itself
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_github_ready(root_path: Path) -> None:
    """The whole GitHub-ready contract, as a single atomic gate (spec.md).

    spec.md's **GitHub-ready contract**: README.md, LICENSE (MIT,
    "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md, `.gitignore`, and a full
    green test suite. Docs are authored — keep them in sync. This is the one
    assertion the spec asks for ("assert with a ``test_github_ready``"); the
    granular checks below are its decomposition for better failure messages.

    The ``PENGYOS_SUITE_GATE`` sentinel breaks the recursion that would otherwise
    occur (the gate runs the suite, whose own gate runs the suite, ...): the
    first (top-level) invocation spawns the subprocess; every nested one returns
    early, having done the work.
    """
    if os.environ.get(GATE_SENTINEL) == "1":
        # We are the nested gate run — the subprocess above already asserted the
        # suite is green; do not spawn another ``pytest``.
        return

    for name in ("README.md", "CHANGELOG.md", ".gitignore"):
        assert (root_path / name).is_file(), f"{name} must ship in the repo"
    _nontrivial(root_path / "README.md")
    _nontrivial(root_path / "CHANGELOG.md")

    license_text = (root_path / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text, "LICENSE must be the MIT license"
    assert "Permission is hereby granted, free of charge" in license_text
    assert (
        "Copyright (c) 2026 Pat Wendorf" in license_text
    ), "LICENSE must carry the exact copyright line"

    suite = _run_suite()
    assert suite.returncode == 0, (
        "the GitHub-ready suite is not green:\n"
        f"--- stdout ---\n{suite.stdout}\n--- stderr ---\n{suite.stderr}"
    )


def test_docs_in_sync(root_path: Path) -> None:
    """The shipped docs must describe what is actually in the repo.

    spec.md's GitHub-ready contract says "Docs are authored — keep them in
    sync". The README ships a file-tree; it must list every test file that
    exists (so a new test that is never documented is caught) and the key
    deliverables. A tree that omits a real file is drift.
    """
    readme = (root_path / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()

    def tree_text() -> str:
        """The README's monospace file tree (the ```...``` code block)."""
        blocks = [ln for ln in lines if ln.startswith("```")]
        assert blocks, "README must contain a file-tree code block"
        start = lines.index(blocks[0])
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("```")),
            len(lines),
        )
        return "\n".join(lines[start + 1 : end])

    tree = tree_text()
    # Every test file that ships must be named in the README tree.
    for test_file in sorted((root_path / "tests").glob("test_*.py")):
        assert (
            test_file.name in tree
        ), f"{test_file.name} exists but is missing from the README file tree"
    # The package entry points must be named in the README tree.
    for artifact in (
        "pengyos_hardening",
        "cli.py",
    ):
        assert artifact in tree, f"{artifact} must appear in the README file tree"
    # Every GitHub-ready deliverable must be named in the README tree. The
    # contract ships these files; a tree that omits one is drift — the README
    # is meant to be the map of what the repo actually contains.
    for name in GITHUB_READY_FILES:
        assert name in tree, f"{name} ships but is missing from the README tree"
    # The packaging manifest is the source of truth for the console script and
    # metadata; it must appear in the tree too.
    assert "pyproject.toml" in tree, "pyproject.toml must appear in the README tree"
    # The full package surface must be named in the README tree, not just the
    # entry points and deliverables. A shipped module that is never mapped is
    # drift — the README is meant to be the map of what the package actually
    # contains (baseline.py, errors.py, data/baseline.json, the check/ readers…).
    pkg = root_path / "pengyos_hardening"
    for surface_file in _package_surface(root_path):
        rel = surface_file.relative_to(pkg)
        assert _named_in_tree(rel, tree), (
            f"{rel} is a shipped package file but is missing from the README "
            f"file tree"
        )


def test_docs_package_surface_detects_missing_doc(root_path: Path) -> None:
    """Regression: a shipped package file that is *not* named in the README tree
    is caught by ``test_docs_in_sync`` — this is what makes the package-surface
    check meaningful.

    A docs check that only asserts a few entry points would let a whole module
    go undocumented. This constructs the negative case: the real README tree
    with one shipped package filename removed, then asserts the same naming
    logic that ``test_docs_in_sync`` uses rejects it.
    """
    readme = (root_path / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("```")]
    assert starts, "README must contain a file-tree code block"
    start = starts[0]
    end = next(
        i for i in range(start + 1, len(lines)) if lines[i].startswith("```")
    )
    tree = "\n".join(lines[start + 1 : end])

    pkg = root_path / "pengyos_hardening"
    surface = _package_surface(root_path)
    assert surface, "expected at least one shipped package file"

    # A currently-documented file, with its name stripped from the tree.
    target = surface[0]
    rel = target.relative_to(pkg)
    assert _named_in_tree(rel, tree), (
        f"{rel} should be documented in the real README (sanity for the negative)"
    )
    stripped = re.sub(re.escape(rel.name), "", tree, count=1)
    assert rel.name not in stripped, "the probe did not actually remove the name"
    assert not _named_in_tree(rel, stripped), (
        "a package file removed from the README tree must fail the naming check"
    )

    # And the same for the parent-joined path form (the check/ readers).
    joined = str(rel)
    if joined in tree:
        stripped_joined = re.sub(re.escape(joined), "", tree, count=1)
        assert joined not in stripped_joined
        assert not _named_in_tree(rel, stripped_joined)


def test_no_stubs_marked_done():
    """Never mark a stub as done — grep test bodies for the anti-pattern.

    Legitimate opt-in skips (``skipif`` gating on ``PENGYOS_IT``) are allowed;
    what we forbid is a *feature body* that has been gutted and labelled done
    with a placeholder stub marker. Only test-function code is inspected, so
    the marker list itself (a module constant) and docstrings never self-match.
    """
    for test_file in (ROOT / "tests").glob("*.py"):
        if test_file.name == "conftest.py":
            continue
        src = test_file.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for snippet in _test_function_code(tree, src):
            for marker in _PLACEHOLDER_MARKERS:
                assert marker not in snippet, (
                    f"{test_file.name} contains a {marker} stub anti-pattern"
                )
