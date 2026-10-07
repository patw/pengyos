"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, missing/invalid input),
* the CLI argument surface (``run``) is registered with the documented flags
  and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest

import pengyos_upstream_watch as pkg
from pengyos_upstream_watch import __version__
from pengyos_upstream_watch import cli
from pengyos_upstream_watch.cli import EXIT_FAIL, EXIT_OK, build_parser, main

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
    assert 'name = "pengyos-upstream-watch"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-upstream-watch")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-upstream-watch is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-upstream-watch = "pengyos_upstream_watch.cli:main"' in text


def test_python_module_entry_point_exists():
    """``python -m pengyos_upstream_watch`` is wired to the same entry point."""
    text = (REPO_ROOT / "pengyos_upstream_watch" / "__main__.py").read_text(
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
    assert "pengyos-upstream-watch" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    err = capsys.readouterr().err
    assert rc == EXIT_OK
    assert "pengyos-upstream-watch" in err
    # The documented subcommand is advertised.
    assert "run" in err


def test_main_run_missing_pins_is_ran_but_failed(capsys, tmp_path):
    # ``run`` with a pins path that does not exist is a *ran-but-failed*
    # outcome (exit 1), not a usage error and not a crash.
    rc = main(
        [
            "run",
            "--pins",
            str(tmp_path / "pins.toml"),  # does not exist
            "--fetcher",
            "mock",
            "--out",
            str(tmp_path / "proposals.json"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "not found" in err


def test_main_run_invalid_pins_is_ran_but_failed(capsys, tmp_path):
    # A pins file that exists but is not valid TOML is a clean ran-but-failed
    # (exit 1), not a traceback.
    pins = tmp_path / "pins.toml"
    pins.write_text("this is = = not valid toml [[[\n", encoding="utf-8")
    rc = main(
        [
            "run",
            "--pins",
            str(pins),
            "--fetcher",
            "mock",
            "--out",
            str(tmp_path / "proposals.json"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "traceback" not in err.lower()


def test_main_run_valid_pins_fetcher_no_answer_is_ran_but_failed(tmp_path, capsys):
    # A pins file that parses cleanly is validated; but with the default empty
    # mock fetcher the latest version cannot be determined, so this is a clean
    # ran-but-failed (exit 1) via a FetcherError — never a traceback.
    pins = tmp_path / "pins.toml"
    pins.write_text(
        '[[component]]\nname = "busybox"\nversion = "1.36.1"\n',
        encoding="utf-8",
    )
    rc = main(
        [
            "run",
            "--pins",
            str(pins),
            "--fetcher",
            "mock",
            "--out",
            str(tmp_path / "proposals.json"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "traceback" not in err.lower()


def test_main_run_valid_pins_emits_proposals(tmp_path, capsys, monkeypatch):
    """A valid pins manifest plus a fetcher that reports a newer release writes
    a proposals JSON document and exits 0. The mock fetcher is injected via a
    spy on ``build_fetcher`` so no network is touched."""
    import json

    from pengyos_upstream_watch import cli
    from pengyos_upstream_watch.fetcher import MockFetcher

    pins = tmp_path / "pins.toml"
    pins.write_text(
        '[[component]]\nname = "busybox"\nversion = "1.36.1"\n'
        '[[component]]\nname = "glibc"\nversion = "2.39"\n',
        encoding="utf-8",
    )
    out = tmp_path / "proposals.json"

    def fake_build(kind, **kwargs):
        # busybox has drifted; glibc is up-to-date (2.39 == 2.39).
        return MockFetcher({"busybox": "1.37.0", "glibc": "2.39"})

    monkeypatch.setattr("pengyos_upstream_watch.fetcher.build_fetcher", fake_build)

    rc = main(
        [
            "run",
            "--pins",
            str(pins),
            "--fetcher",
            "mock",
            "--out",
            str(out),
        ]
    )
    err = capsys.readouterr().err
    assert rc == cli.EXIT_OK
    assert "traceback" not in err.lower()

    proposals = json.loads(out.read_text(encoding="utf-8"))
    # Only busybox drifted; glibc is up-to-date (no proposal).
    assert [p["component"] for p in proposals] == ["busybox"]
    assert proposals[0] == {
        "component": "busybox",
        "current": "1.36.1",
        "available": "1.37.0",
        "lts": False,
        "url": None,
    }


def test_main_run_empty_proposals_writes_empty_array(tmp_path, capsys, monkeypatch):
    """When no pin has drifted, the run still succeeds (exit 0) and writes an
    empty JSON array."""
    import json

    from pengyos_upstream_watch import cli
    from pengyos_upstream_watch.fetcher import MockFetcher

    pins = tmp_path / "pins.toml"
    pins.write_text(
        '[[component]]\nname = "busybox"\nversion = "1.37.0"\n',
        encoding="utf-8",
    )
    out = tmp_path / "proposals.json"

    def fake_build(kind, **kwargs):
        return MockFetcher({"busybox": "1.37.0"})

    monkeypatch.setattr("pengyos_upstream_watch.fetcher.build_fetcher", fake_build)

    rc = main(
        [
            "run",
            "--pins",
            str(pins),
            "--fetcher",
            "mock",
            "--out",
            str(out),
        ]
    )
    assert rc == cli.EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "run", "--pins", "p", "--out", "o"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def test_run_parser_required_flags_and_defaults(tmp_path):
    args = build_parser().parse_args(
        [
            "run",
            "--pins",
            str(tmp_path / "pins.toml"),
            "--fetcher",
            "mock",
            "--out",
            str(tmp_path / "proposals.json"),
        ]
    )
    assert args.command == "run"
    assert args.pins == str(tmp_path / "pins.toml")
    assert args.fetcher == "mock"
    assert args.out == str(tmp_path / "proposals.json")


def test_run_parser_fetcher_defaults_to_mock():
    args = build_parser().parse_args(
        ["run", "--pins", "p", "--out", "o"]
    )
    assert args.fetcher == "mock"


def test_run_parser_fetcher_choice_http():
    args = build_parser().parse_args(
        ["run", "--pins", "p", "--out", "o", "--fetcher", "http"]
    )
    assert args.fetcher == "http"


def test_run_parser_fetcher_rejects_unknown(tmp_path):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            ["run", "--pins", "p", "--out", "o", "--fetcher", "bogus"]
        )
    assert exc.value.code == 2


def test_run_parser_requires_out(tmp_path):
    # ``--out`` is required per the documented interface.
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["run", "--pins", "p", "--fetcher", "mock"])
    assert exc.value.code == 2


def test_run_parser_requires_pins(tmp_path):
    # ``--pins`` is required per the documented interface.
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["run", "--out", "o", "--fetcher", "mock"])
    assert exc.value.code == 2


# --- GitHub-ready contract (lightweight) ------------------------------------


def test_github_ready_files_present():
    """Lightweight presence subset of the GitHub-ready contract. The full
    ``test_github_ready`` suite lives in ``tests/test_github_ready.py``
   ."""
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore",
                 "pyproject.toml"):
        assert (REPO_ROOT / name).is_file(), f"missing GitHub-ready file: {name}"


def test_gitignored_artifacts_are_not_committed():
    """A gitignored file should not also be tracked by git. The tool lives
    under ``tools/pengyos-upstream-watch`` of the PengyOS monorepo, so
    ``.git`` can be an ancestor, not the repo root."""
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=REPO_ROOT,
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        pytest.skip("not inside a git work tree")
    proc = subprocess.run(
        ["git", "ls-files", "--cached"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    )
    tracked = proc.stdout.splitlines()
    offenders = [
        name for name in tracked
        if name.endswith((".pyc", ".egg-info")) or "__pycache__" in name
        or ".pytest_cache" in name
    ]
    assert not offenders, f"gitignored artifacts are tracked: {offenders}"


def test_full_suite_green_opt_in():
    """Opt-in integration gate: re-runs the whole suite in a subprocess and
    asserts it exits 0. ``PENGYOS_IT`` is removed from the child environment so
    this cannot recurse."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("opt-in integration gate: run with PENGYOS_IT=1")
    env = {k: v for k, v in os.environ.items() if k != "PENGYOS_IT"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = (proc.stdout or "")[-2000:] + "\n" + (proc.stderr or "")[-2000:]
    assert proc.returncode == 0, f"inner suite failed (rc={proc.returncode}):\n{tail}"


if __name__ == "__main__":
    sys.exit(main())
