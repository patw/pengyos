"""Tests for the pengyos-tiers CLI front-end.

Covers the ``version`` subcommand (scaffold) plus the classification/gating
subcommands wired in the classify/check subtask: ``classify`` (informational,
exit 0 on success) and ``check`` (gate: distinct non-zero exit when the
resolved tier needs a human signature).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pengyos_tiers.cli import EXIT_NEEDS_HUMAN

ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pengyos_tiers", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def _write_change(tmp_path: Path, change: dict) -> Path:
    """Write a change dict to a temp JSON file and return its path."""

    path = tmp_path / "change.json"
    path.write_text(json.dumps(change))
    return path


def _rules_text(tier: int, path: str, name: str = "rule") -> str:
    return f'[[rule]]\ntier = {tier}\npath = "{path}"\nname = "{name}"\n'


def test_version_flag():
    proc = _run(["--version"])
    assert proc.returncode == 0
    assert "0.1.0" in proc.stdout


def test_version_subcommand():
    proc = _run(["version"])
    assert proc.returncode == 0
    assert proc.stdout.strip() == "0.1.0"


def test_no_args_prints_help():
    # No command: argparse prints help to stderr and exits 0.
    proc = _run([])
    assert proc.returncode == 0
    assert "usage:" in proc.stderr.lower()
    assert "version" in proc.stderr.lower()


def test_unknown_command_is_error():
    proc = _run(["bogus"])
    assert proc.returncode != 0


# --- classify (informational) ------------------------------------------------


def test_classify_json_tier1():
    proc = _run(["classify", "--action", "modify", "--path", "/etc/foo", "--json"])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["tier"] == 1
    assert out["matched"] is True
    assert out["needs_human"] is False
    assert out["approvals"] == ["agent-auto"]


def test_classify_json_tier2_is_informational():
    # classify is informational: even a human-sign tier exits 0.
    proc = _run(
        ["classify", "--action", "modify", "--path", "/boot/vmlinuz-pengyos", "--json"]
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["tier"] == 2
    assert out["needs_human"] is True
    assert out["approvals"] == ["human-sign"]


def test_classify_human_readable():
    proc = _run(["classify", "--action", "modify", "--path", "/etc/foo"])
    assert proc.returncode == 0
    assert "tier=1" in proc.stdout
    assert "agent-auto" in proc.stdout
    assert "GATE" not in proc.stdout


def test_classify_human_readable_tier2_marks_gate():
    proc = _run(["classify", "--action", "modify", "--path", "/boot/vmlinuz-pengyos"])
    assert proc.returncode == 0
    assert "tier=2" in proc.stdout
    assert "GATE" in proc.stdout


def test_classify_with_target():
    proc = _run(
        [
            "classify",
            "--action", "modify",
            "--path", "/etc/pengyos/x",
            "--target", "update-trust-root",
            "--json",
        ]
    )
    assert proc.returncode == 0
    # /etc/pengyos/* with the critical target is Tier 2 in the shipped ruleset.
    assert json.loads(proc.stdout)["tier"] == 2


def test_classify_custom_rules(tmp_path: Path):
    rules = tmp_path / "rules.toml"
    rules.write_text(_rules_text(0, "/srv/*", "image"))
    proc = _run(
        ["classify", "--action", "modify", "--path", "/srv/img",
         "--rules", str(rules), "--json"]
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["tier"] == 0


def test_classify_missing_action_exits_nonzero():
    # argparse rejects the required --action before our handler runs.
    proc = _run(["classify", "--path", "/etc/foo"])
    assert proc.returncode != 0


# --- check (gate) ------------------------------------------------------------


def test_check_tier1_exits_zero(tmp_path: Path):
    change = {"action": "modify", "path": "/etc/bar"}
    path = _write_change(tmp_path, change)
    proc = _run(["check", str(path), "--json"])
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["needs_human"] is False


def test_check_tier2_trips_gate(tmp_path: Path):
    change = {"action": "modify", "path": "/boot/vmlinuz-pengyos"}
    path = _write_change(tmp_path, change)
    proc = _run(["check", str(path), "--json"])
    assert proc.returncode == EXIT_NEEDS_HUMAN
    assert proc.returncode != 0
    assert json.loads(proc.stdout)["needs_human"] is True


def test_check_default_deny_trips_gate(tmp_path: Path):
    change = {"action": "modify", "path": "/opt/unknown"}
    path = _write_change(tmp_path, change)
    proc = _run(["check", str(path), "--json"])
    assert proc.returncode == EXIT_NEEDS_HUMAN
    out = json.loads(proc.stdout)
    assert out["matched"] is False
    assert out["needs_human"] is True


def test_check_human_readable_gate(tmp_path: Path):
    # Without --json the check output is a human-readable line, and the gate
    # still trips on a human-sign tier.
    change = {"action": "modify", "path": "/boot/vmlinuz-pengyos"}
    path = _write_change(tmp_path, change)
    proc = _run(["check", str(path)])
    assert proc.returncode == EXIT_NEEDS_HUMAN
    assert "tier=2" in proc.stdout
    assert "GATE" in proc.stdout


def test_check_json_flag_emits_single_line(tmp_path: Path):
    change = {"action": "modify", "path": "/etc/foo"}
    path = _write_change(tmp_path, change)
    proc = _run(["check", str(path), "--json"])
    assert proc.returncode == 0
    assert proc.stdout.count("\n") == 1
    assert json.loads(proc.stdout)["tier"] == 1


def test_check_stdin():
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_tiers", "check", "-", "--json"],
        input='{"action":"install","path":"/usr/lib/modules/k"}',
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == EXIT_NEEDS_HUMAN
    assert json.loads(proc.stdout)["tier"] == 2


def test_check_missing_file_exits_1():
    proc = _run(["check", "/tmp/definitely-not-here-12345.json"])
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_check_invalid_json_exits_1(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    proc = _run(["check", str(bad)])
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_check_missing_path_field_exits_1(tmp_path: Path):
    bad = tmp_path / "nopath.json"
    bad.write_text('{"action":"modify"}')
    proc = _run(["check", str(bad)])
    assert proc.returncode == 1
    assert "error:" in proc.stderr


def test_check_custom_rules_tier0_exits_zero(tmp_path: Path):
    rules = tmp_path / "rules.toml"
    rules.write_text(_rules_text(0, "/srv/*", "image"))
    change = _write_change(tmp_path, {"action": "modify", "path": "/srv/img"})
    proc = _run(["check", str(change), "--rules", str(rules)])
    # Tier 0 → witness → NOT needs human → exit 0.
    assert proc.returncode == 0
    assert "ok:" in proc.stdout


def test_check_bad_rules_path_exits_1(tmp_path: Path):
    change = _write_change(tmp_path, {"action": "modify", "path": "/etc/foo"})
    proc = _run(["check", str(change), "--rules", "/tmp/nope-12345.toml"])
    assert proc.returncode == 1
    assert "error:" in proc.stderr
