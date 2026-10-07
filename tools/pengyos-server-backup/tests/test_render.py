"""Tests for the ``render`` subcommand (spec.md Interface).

``render`` parses a ``backup.yaml`` and dumps the resulting plan as JSON. These
tests cover the happy path, the ``--json`` flag, the emitted structure, and the
config-error path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pengyos_backup import __version__
from pengyos_backup.cli import main

# A representative, valid config reused across the happy-path tests.
VALID_YAML = textwrap.dedent(
    """
    version: 1

    sources:
      - /etc
      - /home
      - /var/lib/pengyos

    targets:
      local:
        path: /var/backups/pengyos
      remote:
        url: s3:example.com/pengyos
        host: backup-host

    schedule:
      on_calendar: "Mon..Fri *-*-* 02:30:00"
      randomized_delay: "10min"

    retention:
      keep_last: 7
      keep_daily: 7
      keep_weekly: 4
      keep_monthly: 6
      keep_yearly: 0
      keep_within: "720h"
    """
)


def _write_config(tmp_path: Path, yaml_text: str = VALID_YAML) -> Path:
    cfg = tmp_path / "backup.yaml"
    cfg.write_text(yaml_text, encoding="utf-8")
    return cfg


# --- happy path --------------------------------------------------------------


def test_render_dumps_valid_json(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    proc = run_cli_fn("render", "--config", str(cfg))
    assert proc.returncode == 0, proc.stderr
    # The output parses as JSON.
    data = json.loads(proc.stdout)
    assert data["version"] == 1
    assert data["sources"] == ["/etc", "/home", "/var/lib/pengyos"]


def test_render_output_structure_matches_plan(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    proc = run_cli_fn("render", "--config", str(cfg))
    data = json.loads(proc.stdout)

    # Top-level keys mirror the backup.yaml sections, in order.
    assert list(data.keys()) == [
        "version",
        "sources",
        "targets",
        "schedule",
        "retention",
    ]
    # Targets are kind-specific (local -> path, remote -> url).
    local, remote = data["targets"]
    assert local == {"kind": "local", "path": "/var/backups/pengyos"}
    assert remote == {
        "kind": "remote",
        "url": "s3:example.com/pengyos",
        "host": "backup-host",
    }
    assert data["schedule"] == {
        "on_calendar": "Mon..Fri *-*-* 02:30:00",
        "randomized_delay": "10min",
    }
    assert data["retention"]["keep_last"] == 7


def test_render_flag_json_is_accepted(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    proc = run_cli_fn("render", "--config", str(cfg), "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["version"] == 1


def test_render_main_returns_zero(capsys, tmp_path):
    cfg = _write_config(tmp_path)
    assert main(["render", "--config", str(cfg)]) == 0


def test_render_writes_to_stdout_only(capsys, tmp_path):
    cfg = _write_config(tmp_path)
    assert main(["render", "--config", str(cfg)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["version"] == 1


# --- clean JSON (no nulls) ---------------------------------------------------


def test_render_omits_unset_optional_fields(run_cli_fn, tmp_path):
    minimal = textwrap.dedent(
        """
        version: 1
        sources:
          - /etc
        targets:
          local:
            path: /var/backups
        schedule:
          on_calendar: daily
        """
    )
    cfg = _write_config(tmp_path, minimal)
    proc = run_cli_fn("render", "--config", str(cfg))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    # No randomized_delay, empty retention object, no JSON nulls anywhere.
    assert data["schedule"] == {"on_calendar": "daily"}
    assert data["retention"] == {}
    assert "null" not in proc.stdout


# --- errors ------------------------------------------------------------------


def test_render_bad_config_returns_one(run_cli_fn, tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("just a scalar\n", encoding="utf-8")
    proc = run_cli_fn("render", "--config", str(cfg))
    assert proc.returncode == 1
    assert "error:" in proc.stderr.lower()


def test_render_missing_file_returns_one(run_cli_fn, tmp_path):
    proc = run_cli_fn("render", "--config", str(tmp_path / "nope.yaml"))
    assert proc.returncode == 1
    assert "not found" in proc.stderr.lower()


def test_render_requires_config(run_cli_fn):
    # Missing the required --config makes argparse exit 2 (distinct from the
    # config-error exit 1), so the contract is still observable.
    proc = run_cli_fn("render")
    assert proc.returncode == 2


# --- module entrypoint -------------------------------------------------------


def test_module_entrypoint_render(root, tmp_path):
    cfg = _write_config(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_backup", "render", "--config", str(cfg)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["version"] == 1
