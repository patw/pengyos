"""Tests for the ``plan`` subcommand (spec.md Interface).

``plan`` reads a ``backup.yaml`` and emits the systemd timer + service (golden
restic/rustic argv) plus the alert hook into an output directory. These tests
run the CLI end-to-end via ``python -m pengyos_backup`` and assert on the
emitted files.
"""

from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest

from pengyos_backup.units import ALERT_SCRIPT_NAME, SERVICE_NAME, TIMER_NAME

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


def test_plan_emits_units(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr

    # The timer + service (and the alert hook) are written into --out.
    assert (out / TIMER_NAME).is_file()
    assert (out / SERVICE_NAME).is_file()
    assert (out / ALERT_SCRIPT_NAME).is_file()


def test_plan_emits_exactly_four_files(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    emitted = sorted(p.name for p in out.iterdir())
    assert emitted == sorted(
        [
            TIMER_NAME,
            SERVICE_NAME,
            "pengyos-backup-alert.service",
            ALERT_SCRIPT_NAME,
        ]
    )


def test_plan_creates_output_dir(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "nested" / "units"  # does not exist yet
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert (out / TIMER_NAME).is_file()


def test_plan_stdout_lists_written_files(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert str(out / TIMER_NAME) in proc.stdout
    assert str(out / SERVICE_NAME) in proc.stdout


def test_plan_reports_onfailure_link(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "OnFailure=pengyos-backup-alert.service" in proc.stdout


def test_plan_writes_valid_timer_contents(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    timer = (out / TIMER_NAME).read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 02:30:00" in timer
    assert "RandomizedDelaySec=10min" in timer
    assert "WantedBy=timers.target" in timer


def test_plan_service_contains_golden_argv(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    service = (out / SERVICE_NAME).read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert (
        "ExecStart=restic backup /etc /home /var/lib/pengyos "
        "--repo /var/backups/pengyos"
    ) in service
    assert "OnFailure=pengyos-backup-alert.service" in service


def test_plan_alert_script_is_executable(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    script = out / ALERT_SCRIPT_NAME
    assert script.is_file()
    mode = stat.S_IMODE(script.stat().st_mode)
    assert mode & 0o111, "alert hook must be executable"
    assert script.read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_plan_is_idempotent(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    first = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert first.returncode == 0, first.stderr
    before = {p.name: p.read_text() for p in out.iterdir()}

    second = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert second.returncode == 0, second.stderr
    after = {p.name: p.read_text() for p in out.iterdir()}
    assert before == after


# --- binary selection --------------------------------------------------------


def test_plan_default_binary_is_restic(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    service = (out / SERVICE_NAME).read_text(encoding="utf-8")
    assert "ExecStart=restic backup" in service
    assert "ExecStart=rustic" not in service


def test_plan_rustic_binary(run_cli_fn, tmp_path):
    cfg = _write_config(tmp_path)
    out = tmp_path / "units"
    proc = run_cli_fn(
        "plan", "--config", str(cfg), "--out", str(out), "--binary", "rustic"
    )
    assert proc.returncode == 0, proc.stderr
    service = (out / SERVICE_NAME).read_text(encoding="utf-8")
    assert "ExecStart=rustic backup" in service
    assert "ExecStart=restic" not in service


# --- errors ------------------------------------------------------------------


def test_plan_missing_config_returns_one(run_cli_fn, tmp_path):
    proc = run_cli_fn(
        "plan",
        "--config",
        str(tmp_path / "nope.yaml"),
        "--out",
        str(tmp_path / "units"),
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr.lower()


def test_plan_bad_config_returns_one(run_cli_fn, tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("just a scalar\n", encoding="utf-8")
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(tmp_path / "units"))
    assert proc.returncode == 1
    assert "error:" in proc.stderr.lower()


def test_plan_requires_config_and_out(run_cli_fn):
    # Missing the required --config/--out makes argparse exit 2.
    proc = run_cli_fn("plan")
    assert proc.returncode == 2


# --- retention validation (never zero kept snapshots) ------------------------


def test_plan_rejects_zero_keep_policy(run_cli_fn, tmp_path):
    cfg = _write_config(
        tmp_path,
        textwrap.dedent(
            """
            version: 1
            sources:
              - /etc
            targets:
              local:
                path: /var/backups
            schedule:
              on_calendar: daily
            retention:
              keep_last: 0
            """
        ),
    )
    out = tmp_path / "units"
    proc = run_cli_fn("plan", "--config", str(cfg), "--out", str(out))
    assert proc.returncode == 1
    assert "zero snapshots" in proc.stderr.lower()
    # A rejected config leaves no stray units behind.
    assert not out.exists() or not any(out.iterdir())
