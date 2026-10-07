"""End-to-end integration tests for the ``pengyos-mutations`` CLI.

Opt-in behind ``PENGYOS_IT=1``: they shell out to the real module entry point
and run a real (non-privileged) command under the snapshot. They still need only
a temp dir + the Python interpreter — no network, root, display, or hardware.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_mutations import attest


@pytest.fixture
def it_enabled() -> bool:
    """Integration tests run only when ``PENGYOS_IT=1`` is set."""
    return os.environ.get("PENGYOS_IT") == "1"


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args: "list[str]", cwd: Path) -> "subprocess.CompletedProcess[str]":
    # Make the package importable in the subprocess regardless of its cwd.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "pengyos_mutations", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


def test_apply_via_cli_records_and_modifies(it_enabled, tmp_path) -> None:
    if not it_enabled:
        pytest.skip("set PENGYOS_IT=1 to run integration tests")

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v1", encoding="utf-8")
    store = tmp_path / "store"

    cmd = [sys.executable, "-c", "open('a.conf','w').write('v2')"]
    proc = _run_cli(
        ["apply", "--desc", "cli edit", "--store", str(store),
         "--root", str(root), "--", *cmd],
        cwd=root,
    )
    assert proc.returncode == 0, proc.stderr
    assert (root / "a.conf").read_text() == "v2"

    log = store / "events.jsonl"
    assert attest.chain_length(log) == 1
    rec = attest.last_record(log)
    assert rec["data"]["desc"] == "cli edit"
    assert attest.verify_chain(log).ok


def test_apply_via_cli_failing_command_still_records(it_enabled, tmp_path) -> None:
    if not it_enabled:
        pytest.skip("set PENGYOS_IT=1 to run integration tests")

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v1", encoding="utf-8")
    store = tmp_path / "store"

    cmd = [sys.executable, "-c", "raise SystemExit(7)"]
    proc = _run_cli(
        ["apply", "--desc", "cli boom", "--store", str(store),
         "--root", str(root), "--", *cmd],
        cwd=root,
    )
    assert proc.returncode == 7
    assert attest.chain_length(store / "events.jsonl") == 1
    assert attest.last_record(store / "events.jsonl")["data"]["exit_code"] == 7


def test_cli_log_subcommand_emits_json(it_enabled, tmp_path) -> None:
    if not it_enabled:
        pytest.skip("set PENGYOS_IT=1 to run integration tests")

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v1", encoding="utf-8")
    store = tmp_path / "store"
    cmd = [sys.executable, "-c", "open('a.conf','w').write('v2')"]
    _run_cli(
        ["apply", "--desc", "json", "--store", str(store), "--root", str(root), "--", *cmd],
        cwd=root,
    )
    proc = _run_cli(["log", "--json", "--store", str(store)], cwd=root)
    assert proc.returncode == 0, proc.stderr
    import json

    entries = json.loads(proc.stdout)
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["data"]["desc"] == "json"
