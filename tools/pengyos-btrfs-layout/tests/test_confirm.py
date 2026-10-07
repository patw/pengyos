"""Tests for the safe-by-default confirmation gate and dry-run semantics.

Covers ``pengyos_btrfs.confirm`` (the gate + dry-run/execute orchestration) and
the new ``--dry-run`` / ``--yes`` flags on the ``snapshot`` / ``rollback``
subparsers.

The whole contract is golden-testable without root, a real btrfs volume, or the
network: plans come from the pure generators in ``commands.py`` and execution
goes through ``MockRunner``.
"""

import pytest

from pengyos_btrfs import constants as c
from pengyos_btrfs.cli import build_parser, main
from pengyos_btrfs.commands import (
    BtrfsCommandError,
    MockRunner,
    Plan,
    Step,
    create_commands,
    rollback_commands,
    snapshot_commands,
)
from pengyos_btrfs.confirm import (
    DESTRUCTIVE_KINDS,
    Execution,
    execute_or_preview,
    is_destructive,
    require_confirmation,
    requires_confirmation,
)
from pengyos_btrfs.errors import RollbackError
from pengyos_btrfs.layout import canonical_layout


# --- The destructive classification -------------------------------------
def test_destructive_kinds_is_just_rollback():
    assert DESTRUCTIVE_KINDS == frozenset({"rollback"})


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("rollback", True),
        ("snapshot", False),
        ("create", False),
        ("something-else", False),
    ],
)
def test_is_destructive_and_requires_confirmation_agree(kind, expected):
    assert is_destructive(kind) is expected
    assert requires_confirmation(kind) is expected


# --- The gate (require_confirmation) ------------------------------------
def test_gate_allows_non_destructive_even_unconfirmed():
    for kind in ("create", "snapshot"):
        # Must not raise, regardless of confirm/dry-run.
        require_confirmation(kind, confirmed=False, dry_run=False)
        require_confirmation(kind, confirmed=False, dry_run=True)


def test_gate_refuses_unconfirmed_rollback():
    with pytest.raises(RollbackError) as exc:
        require_confirmation("rollback", confirmed=False, dry_run=False)
    msg = str(exc.value)
    # The message must point the user at both escape hatches.
    assert c.ROLLBACK_CONFIRM_FLAG in msg
    assert "--dry-run" in msg
    assert "rollback" in msg


def test_gate_allows_confirmed_rollback():
    require_confirmation("rollback", confirmed=True, dry_run=False)


def test_gate_allows_rollback_dry_run_without_confirm():
    # A dry-run previews a rollback without making it, so no --yes is needed.
    require_confirmation("rollback", confirmed=False, dry_run=True)


def test_gate_allows_confirmed_dry_run():
    require_confirmation("rollback", confirmed=True, dry_run=True)


# --- execute_or_preview: dry-run semantics ------------------------------
def _empty_plan(kind):
    return Plan(kind, (Step("noop", (c.BTRFS_BIN, "subvolume", "list")),))


def test_dry_run_never_calls_runner_rollback():
    layout = canonical_layout()
    plan = rollback_commands(layout, "snap1")
    runner = MockRunner()
    result = execute_or_preview(runner, plan, confirmed=False, dry_run=True)
    assert result.dry_run is True
    assert result.results == ()
    assert result.argvs == []
    assert runner.calls == [], "dry-run must not touch the runner"


def test_dry_run_never_calls_runner_even_unconfirmed_rollback():
    # The interesting guarantee: you can preview a destructive rollback
    # without --yes and nothing is executed.
    layout = canonical_layout()
    plan = rollback_commands(layout, "snap1")
    runner = MockRunner()
    execute_or_preview(runner, plan, confirmed=False, dry_run=True)
    assert runner.calls == []


def test_dry_run_execution_result_type_and_fields():
    result = execute_or_preview(MockRunner(), _empty_plan("snapshot"), dry_run=True)
    assert isinstance(result, Execution)
    assert result.dry_run is True
    assert result.results == ()


# --- execute_or_preview: the gate is enforced before any runner call -----
def test_unconfirmed_rollback_raises_and_does_not_run():
    layout = canonical_layout()
    plan = rollback_commands(layout, "snap1")
    runner = MockRunner()
    with pytest.raises(RollbackError):
        execute_or_preview(runner, plan, confirmed=False, dry_run=False)
    assert runner.calls == [], "the gate must fire before any btrfs call"


# --- execute_or_preview: confirmed destructive + non-destructive plans --
def test_confirmed_rollback_executes_all_steps():
    layout = canonical_layout()
    plan = rollback_commands(layout, "snap1")
    # 3 subvolumes (not @snapshots) x a 3-step triplet = 9 steps.
    assert len(plan.steps) == 9
    runner = MockRunner()
    result = execute_or_preview(runner, plan, confirmed=True, dry_run=False)
    assert result.dry_run is False
    assert len(result.results) == 9
    assert len(runner.calls) == 9
    assert result.argvs == plan.argvs()


def test_non_destructive_snapshot_runs_without_confirm():
    layout = canonical_layout()
    plan = snapshot_commands(layout, "snap1")
    assert plan.kind == "snapshot"
    runner = MockRunner()
    result = execute_or_preview(runner, plan, confirmed=False, dry_run=False)
    assert result.dry_run is False
    # @, @home, @var are snapshotted (3 steps), @snapshots is skipped.
    assert len(runner.calls) == 3
    assert result.argvs == plan.argvs()


def test_non_destructive_create_runs_without_confirm():
    layout = canonical_layout()
    plan = create_commands(layout)
    runner = MockRunner()
    result = execute_or_preview(runner, plan, confirmed=False, dry_run=False)
    assert result.dry_run is False
    # All four subvolumes are created.
    assert len(runner.calls) == 4


# --- fail-fast: an executed step failure propagates (never in dry-run) ---
def test_confirmed_rollback_fail_fast_stops_and_raises():
    layout = canonical_layout()
    plan = rollback_commands(layout, "snap1")
    first_argv = tuple(plan.steps[0].argv)
    runner = MockRunner(fail_on=[first_argv])
    with pytest.raises(BtrfsCommandError) as exc:
        execute_or_preview(runner, plan, confirmed=True, dry_run=False)
    # It stopped at the very first step.
    assert len(runner.calls) == 1
    assert exc.value.argv == first_argv
    assert exc.value.returncode == 1


def test_dry_run_never_raises_even_if_a_step_would_fail():
    layout = canonical_layout()
    plan = rollback_commands(layout, "snap1")
    first_argv = tuple(plan.steps[0].argv)
    runner = MockRunner(fail_on=[first_argv])
    result = execute_or_preview(runner, plan, confirmed=False, dry_run=True)
    assert result.dry_run is True
    assert result.results == ()
    assert runner.calls == []


# --- CLI argument contract: the new flags -------------------------------
def _subparser(name):
    parser = build_parser()
    for action in parser._subparsers._actions:
        choices = getattr(action, "choices", None)
        if choices and name in choices:
            return choices[name]
    raise AssertionError(f"subcommand {name!r} not found")


def test_rollback_has_yes_flag():
    rollback = _subparser("rollback")
    yes = next(a for a in rollback._actions if a.dest == "yes")
    assert yes.const is True  # store_true
    assert yes.default is False  # safe by default: --yes is OFF
    # The flag spelling is the pinned constant.
    option_strings = set(yes.option_strings)
    assert c.ROLLBACK_CONFIRM_FLAG in option_strings


def test_rollback_has_dry_run_flag():
    rollback = _subparser("rollback")
    dry = next(a for a in rollback._actions if a.dest == "dry_run")
    assert dry.const is True
    assert dry.default is False
    assert "--dry-run" in set(dry.option_strings)


def test_snapshot_has_dry_run_flag():
    snapshot = _subparser("snapshot")
    dry = next(a for a in snapshot._actions if a.dest == "dry_run")
    assert dry.const is True
    assert dry.default is False
    assert "--dry-run" in set(dry.option_strings)


def test_rollback_yes_default_false_when_parsed():
    parser = build_parser()
    args = parser.parse_args(["rollback", "snap1"])
    assert args.yes is False
    assert args.dry_run is False


def test_rollback_yes_set_when_flagged():
    parser = build_parser()
    args = parser.parse_args(["rollback", "snap1", c.ROLLBACK_CONFIRM_FLAG])
    assert args.yes is True


def test_rollback_dry_run_set_when_flagged():
    parser = build_parser()
    args = parser.parse_args(["rollback", "snap1", "--dry-run"])
    assert args.dry_run is True
    assert args.yes is False


def test_snapshot_dry_run_set_when_flagged():
    parser = build_parser()
    args = parser.parse_args(["snapshot", "snap1", "--dry-run"])
    assert args.dry_run is True


# --- End-to-end golden: confirm -> execute a real rollback plan ---------
def test_end_to_end_confirmed_rollback_golden_argv():
    layout = canonical_layout()
    plan = rollback_commands(layout, "boot-2026-09-16")
    runner = MockRunner()
    result = execute_or_preview(runner, plan, confirmed=True, dry_run=False)
    # The runner saw exactly the golden argvs, in order.
    assert runner.calls == [tuple(a) for a in plan.argvs()]
    assert result.argvs == plan.argvs()
    # And the plan is a real, non-trivial rollback (9 steps).
    assert len(runner.calls) == 9


def test_end_to_end_unconfirmed_rollback_refused_end_to_end():
    layout = canonical_layout()
    plan = rollback_commands(layout, "boot-2026-09-16")
    runner = MockRunner()
    with pytest.raises(RollbackError):
        execute_or_preview(runner, plan, confirmed=False, dry_run=False)
    assert runner.calls == []


# --- The snapshot/rollback handlers are wired (safe by default) ----------
def test_snapshot_rollback_handlers_are_wired_safely(monkeypatch):
    # The wiring item replaced the scaffold's EXIT_NOT_IMPLEMENTED dispatch
    # with real handlers (full behavior pinned in
    # tests/test_cli_handlers.py). Here the safe-by-default contract is
    # pinned at the CLI boundary: a dry-run rollback needs no --yes and
    # exits 0; an unconfirmed execution is refused with a clean EXIT_ERROR
    # (never a traceback); and none of these paths touches a real runner.
    runner = MockRunner()
    monkeypatch.setattr("pengyos_btrfs.cli.make_runner", lambda: runner)
    assert main(["rollback", "snap1", "--dry-run"]) == c.EXIT_OK
    assert main(["snapshot", "snap1", "--dry-run"]) == c.EXIT_OK
    assert runner.calls == [], "dry-run paths may not execute a btrfs command"
    # Unconfirmed execution is refused BEFORE any runner call.
    assert main(["rollback", "snap1"]) == c.EXIT_ERROR
    assert runner.calls == []
    # Confirmed, and non-destructive, plans execute their full step list.
    assert main(["rollback", "snap1", c.ROLLBACK_CONFIRM_FLAG]) == c.EXIT_OK
    assert len(runner.calls) == 9  # 3 subvolumes x the 3-step triplet
    assert main(["snapshot", "snap1"]) == c.EXIT_OK
    assert len(runner.calls) == 12  # 9 + the 3 snapshot steps
