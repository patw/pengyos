"""Tests for the wired ``snapshot`` / ``rollback`` CLI handlers.

The handlers (``pengyos_btrfs.cli.cmd_snapshot`` / ``cmd_rollback``) build a
:class:`Plan` via the pure generators, execute (or preview, or emit as JSON)
it through the mockable runner seam (``cli.make_runner``, patched here with a
:class:`MockRunner`), and print a human-readable step list. Every failure
path must be a clean ``EXIT_ERROR`` on stderr — never a traceback.

No network, no root, no real ``btrfs``: all execution goes through the mock.
"""

import json

import pytest

from pengyos_btrfs import constants as c
from pengyos_btrfs.cli import format_plan, main, make_runner
from pengyos_btrfs.commands import (
    MockRunner,
    RealRunner,
    rollback_commands,
    snapshot_commands,
)
from pengyos_btrfs.layout import canonical_layout

NAME = "snap1"
ROLLBACK_NAME = "boot-2026-09-16"


def _run(monkeypatch, *argv):
    """Run *argv* through ``main`` with a fresh ``MockRunner`` injected."""
    runner = MockRunner()
    monkeypatch.setattr("pengyos_btrfs.cli.make_runner", lambda: runner)
    return main(list(argv)), runner


# --- The runner seam ------------------------------------------------------
def test_make_runner_returns_a_real_runner():
    # Production default: the subprocess-backed runner. Tests never call
    # this directly — they patch it — but the default must be pinned.
    assert isinstance(make_runner(), RealRunner)


# --- format_plan (golden) -------------------------------------------------
def test_format_plan_snapshot_golden():
    plan = snapshot_commands(canonical_layout(), NAME)
    assert format_plan(plan) == (
        "pengyos-btrfs: snapshot plan (3 step(s))\n"
        "  [1/3] snapshot '@' -> /mnt/@snapshots/snap1/@\n"
        "        $ btrfs subvolume snapshot -r /mnt/@ /mnt/@snapshots/snap1/@\n"
        "  [2/3] snapshot '@home' -> /mnt/@snapshots/snap1/@home\n"
        "        $ btrfs subvolume snapshot -r /mnt/@home /mnt/@snapshots/snap1/@home\n"
        "  [3/3] snapshot '@var' -> /mnt/@snapshots/snap1/@var\n"
        "        $ btrfs subvolume snapshot -r /mnt/@var /mnt/@snapshots/snap1/@var\n"
    )


def test_format_plan_dry_run_header():
    plan = snapshot_commands(canonical_layout(), NAME)
    assert format_plan(plan, dry_run=True).startswith(
        "pengyos-btrfs: snapshot plan (3 step(s)) — dry-run, "
        "nothing will be executed\n"
    )
    # The step lines are identical in both modes — only the header differs.
    dry = format_plan(plan, dry_run=True).splitlines()
    run = format_plan(plan).splitlines()
    assert dry[1:] == run[1:]


def test_format_plan_rollback_numbering():
    plan = rollback_commands(canonical_layout(), ROLLBACK_NAME)
    text = format_plan(plan)
    assert text.startswith("pengyos-btrfs: rollback plan (9 step(s))\n")
    for i in range(1, 10):
        assert f"  [{i}/9] " in text


# --- snapshot: execution (golden) -----------------------------------------
def test_snapshot_executes_plan_golden(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "snapshot", NAME)
    assert code == c.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == (
        "pengyos-btrfs: snapshot plan (3 step(s))\n"
        "  [1/3] snapshot '@' -> /mnt/@snapshots/snap1/@\n"
        "        $ btrfs subvolume snapshot -r /mnt/@ /mnt/@snapshots/snap1/@\n"
        "  [2/3] snapshot '@home' -> /mnt/@snapshots/snap1/@home\n"
        "        $ btrfs subvolume snapshot -r /mnt/@home /mnt/@snapshots/snap1/@home\n"
        "  [3/3] snapshot '@var' -> /mnt/@snapshots/snap1/@var\n"
        "        $ btrfs subvolume snapshot -r /mnt/@var /mnt/@snapshots/snap1/@var\n"
        "pengyos-btrfs: snapshot 'snap1': 3 step(s) executed\n"
    )
    assert captured.err == ""
    # Exactly the golden argvs, in plan order.
    plan = snapshot_commands(canonical_layout(), NAME)
    assert runner.calls == [tuple(a) for a in plan.argvs()]


# --- snapshot: --json -----------------------------------------------------
def test_snapshot_json_emits_plan_to_dict(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "snapshot", NAME, "--json")
    assert code == c.EXIT_OK
    captured = capsys.readouterr()
    plan = snapshot_commands(canonical_layout(), NAME)
    # stdout is exactly Plan.to_dict() as JSON (indent=2), nothing else.
    assert captured.out == json.dumps(plan.to_dict(), indent=2) + "\n"
    assert captured.err == ""
    parsed = json.loads(captured.out)
    assert parsed == plan.to_dict()
    assert parsed["kind"] == "snapshot"
    assert [s["argv"] for s in parsed["steps"]] == plan.argvs()
    # --json is generation-only: the runner is never called.
    assert runner.calls == []


def test_snapshot_json_argv_golden(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "snapshot", NAME, "--json")
    assert code == c.EXIT_OK
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["steps"][0]["argv"] == [
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        "/mnt/@",
        f"/mnt/@snapshots/{NAME}/@",
    ]
    assert len(parsed["steps"]) == 3
    assert runner.calls == []


def test_snapshot_json_wins_over_dry_run(monkeypatch, capsys):
    # --json + --dry-run: still generation-only, still JSON on stdout.
    code, runner = _run(monkeypatch, "snapshot", NAME, "--json", "--dry-run")
    assert code == c.EXIT_OK
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["kind"] == "snapshot"
    assert "pengyos-btrfs: snapshot plan" not in captured.out
    assert runner.calls == []


# --- snapshot: --dry-run --------------------------------------------------
def test_snapshot_dry_run_previews_without_executing(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "snapshot", NAME, "--dry-run")
    assert code == c.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.startswith(
        "pengyos-btrfs: snapshot plan (3 step(s)) — dry-run, "
        "nothing will be executed\n"
    )
    assert captured.out.endswith(
        "pengyos-btrfs: dry-run preview — nothing was executed\n"
    )
    assert "step(s) executed" not in captured.out
    assert runner.calls == []
    assert captured.err == ""


# --- snapshot: error paths ------------------------------------------------
@pytest.mark.parametrize(
    "bad_name",
    ["snap/name", "snap\\name", ".", "..", "bad name", "sp@ce"],
)
def test_snapshot_bad_name_is_a_clean_error(monkeypatch, capsys, bad_name):
    code, runner = _run(monkeypatch, "snapshot", bad_name)
    assert code == c.EXIT_ERROR
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "pengyos-btrfs: error:" in captured.err
    # Names with '/' get the dedicated message; others the grammar message.
    if "/" in bad_name:
        assert "snapshot name must not contain '/'" in captured.err
    else:
        assert "invalid snapshot name" in captured.err
    assert runner.calls == []
    assert captured.out == ""


def test_snapshot_execution_failure_is_a_clean_error(monkeypatch, capsys):
    first_argv = tuple(
        snapshot_commands(canonical_layout(), NAME).steps[0].argv
    )
    runner = MockRunner(fail_on=[first_argv])
    monkeypatch.setattr("pengyos_btrfs.cli.make_runner", lambda: runner)
    code = main(["snapshot", NAME])
    assert code == c.EXIT_ERROR
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "pengyos-btrfs: error:" in captured.err
    assert "command failed (exit 1)" in captured.err
    assert first_argv[3] in captured.err  # the failing step is named
    # Fail-fast: it stopped at the very first step.
    assert runner.calls == [first_argv]


# --- rollback: refused without --yes --------------------------------------
def test_rollback_refused_without_yes(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "rollback", ROLLBACK_NAME)
    assert code == c.EXIT_ERROR
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    err = captured.err
    assert "pengyos-btrfs: error:" in err
    assert "refusing to run destructive 'rollback' plan" in err
    # The message names BOTH escape hatches.
    assert c.ROLLBACK_CONFIRM_FLAG in err
    assert "--dry-run" in err
    # The plan is still shown on stdout (dry-run rendering) so the user
    # sees exactly what --yes would do.
    assert captured.out.startswith(
        "pengyos-btrfs: rollback plan (9 step(s)) — dry-run, "
        "nothing will be executed\n"
    )
    # The gate fired BEFORE any runner call.
    assert runner.calls == []


# --- rollback: confirmed (golden) -----------------------------------------
def test_rollback_yes_executes_plan_golden(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "rollback", ROLLBACK_NAME, c.ROLLBACK_CONFIRM_FLAG)
    assert code == c.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == (
        "pengyos-btrfs: rollback plan (9 step(s))\n"
        "  [1/9] promote snapshot 'boot-2026-09-16' of '@' to /mnt/@.rollback\n"
        "        $ btrfs subvolume snapshot /mnt/@snapshots/boot-2026-09-16/@ /mnt/@.rollback\n"
        "  [2/9] delete live subvolume '@' at /mnt/@\n"
        "        $ btrfs subvolume delete /mnt/@\n"
        "  [3/9] rename /mnt/@.rollback -> /mnt/@\n"
        "        $ btrfs subvolume rename /mnt/@.rollback /mnt/@\n"
        "  [4/9] promote snapshot 'boot-2026-09-16' of '@home' to /mnt/@home.rollback\n"
        "        $ btrfs subvolume snapshot /mnt/@snapshots/boot-2026-09-16/@home /mnt/@home.rollback\n"
        "  [5/9] delete live subvolume '@home' at /mnt/@home\n"
        "        $ btrfs subvolume delete /mnt/@home\n"
        "  [6/9] rename /mnt/@home.rollback -> /mnt/@home\n"
        "        $ btrfs subvolume rename /mnt/@home.rollback /mnt/@home\n"
        "  [7/9] promote snapshot 'boot-2026-09-16' of '@var' to /mnt/@var.rollback\n"
        "        $ btrfs subvolume snapshot /mnt/@snapshots/boot-2026-09-16/@var /mnt/@var.rollback\n"
        "  [8/9] delete live subvolume '@var' at /mnt/@var\n"
        "        $ btrfs subvolume delete /mnt/@var\n"
        "  [9/9] rename /mnt/@var.rollback -> /mnt/@var\n"
        "        $ btrfs subvolume rename /mnt/@var.rollback /mnt/@var\n"
        "pengyos-btrfs: rollback to 'boot-2026-09-16': 9 step(s) executed\n"
    )
    assert captured.err == ""
    plan = rollback_commands(canonical_layout(), ROLLBACK_NAME)
    assert runner.calls == [tuple(a) for a in plan.argvs()]
    assert len(runner.calls) == 9


# --- rollback: --dry-run --------------------------------------------------
def test_rollback_dry_run_needs_no_yes_and_executes_nothing(
    monkeypatch, capsys
):
    code, runner = _run(monkeypatch, "rollback", ROLLBACK_NAME, "--dry-run")
    assert code == c.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.startswith(
        "pengyos-btrfs: rollback plan (9 step(s)) — dry-run, "
        "nothing will be executed\n"
    )
    assert captured.out.endswith(
        "pengyos-btrfs: dry-run preview — nothing was executed\n"
    )
    assert runner.calls == []
    assert captured.err == ""


def test_rollback_yes_with_dry_run_prefers_dry_run(monkeypatch, capsys):
    # --yes --dry-run is still a preview: nothing executes.
    code, runner = _run(
        monkeypatch, "rollback", ROLLBACK_NAME, c.ROLLBACK_CONFIRM_FLAG, "--dry-run"
    )
    assert code == c.EXIT_OK
    assert capsys.readouterr().out.startswith(
        "pengyos-btrfs: rollback plan (9 step(s)) — dry-run, "
        "nothing will be executed\n"
    )
    assert runner.calls == []


# --- rollback: error paths ------------------------------------------------
def test_rollback_bad_name_is_a_clean_error(monkeypatch, capsys):
    code, runner = _run(monkeypatch, "rollback", "snap/name")
    assert code == c.EXIT_ERROR
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "snapshot name must not contain '/'" in captured.err
    assert runner.calls == []


def test_rollback_confirmed_execution_failure_is_a_clean_error(
    monkeypatch, capsys
):
    first_argv = tuple(
        rollback_commands(canonical_layout(), ROLLBACK_NAME).steps[0].argv
    )
    runner = MockRunner(fail_on=[first_argv])
    monkeypatch.setattr("pengyos_btrfs.cli.make_runner", lambda: runner)
    code = main(["rollback", ROLLBACK_NAME, c.ROLLBACK_CONFIRM_FLAG])
    assert code == c.EXIT_ERROR
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "command failed (exit 1)" in captured.err
    # Fail-fast: exactly one step was attempted.
    assert runner.calls == [first_argv]


def test_rollback_rejects_json_flag(monkeypatch, capsys):
    # --json is part of the snapshot contract only; on rollback it is an
    # argparse usage error (exit 2) — not a traceback.
    code, runner = _run(monkeypatch, "rollback", ROLLBACK_NAME, "--json")
    assert code == c.EXIT_USAGE
    assert "Traceback" not in capsys.readouterr().err
    assert runner.calls == []
