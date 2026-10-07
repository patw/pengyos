"""Tests for btrfs command generation (create / snapshot / rollback).

Golden-testable: every generator is checked against the *exact* ``btrfs``
``argv`` it must emit, with no real ``btrfs`` and no root — the runner seam
(:class:`MockRunner`) records what would run.

Covers:

- the golden argv for ``create``, ``snapshot`` and ``rollback`` on the
  canonical layout;
- the staging-mount-point convention and its override;
- snapshot/rollback name validation (bad names -> :class:`SnapshotError`);
- the Plan/Step data model (immutability, ``argvs()``, ``to_dict()`` golden);
- the mockable runner seam: recording, configured failures, and
  ``execute_plan`` stopping at the first failure.

No network, no root, no external tools — pure in-process checks plus one
opt-in integration test gated behind ``PENGYOS_IT=1``.
"""

import dataclasses
import os

import pytest

from pengyos_btrfs.commands import (
    BtrfsCommandError,
    BtrfsRunner,
    MockRunner,
    RealRunner,
    RunResult,
    create_commands,
    execute_plan,
    rollback_commands,
    snapshot_commands,
)
from pengyos_btrfs.errors import SnapshotError
from pengyos_btrfs.layout import Subvolume, canonical_layout, validate_layout

NAME = "boot-2026-09-16"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def test_create_commands_golden_default_mount():
    plan = create_commands(canonical_layout())
    assert plan.kind == "create"
    assert plan.argvs() == [
        ["btrfs", "subvolume", "create", "/mnt/@"],
        ["btrfs", "subvolume", "create", "/mnt/@home"],
        ["btrfs", "subvolume", "create", "/mnt/@var"],
        ["btrfs", "subvolume", "create", "/mnt/@snapshots"],
    ]


def test_create_commands_creates_all_subvolumes_in_order():
    plan = create_commands(canonical_layout())
    # One step per subvolume, in layout order — including the top-level "@".
    assert len(plan.steps) == len(canonical_layout().subvolumes)
    for step, sv in zip(plan.steps, canonical_layout().subvolumes):
        assert step.argv[:3] == ("btrfs", "subvolume", "create")
        assert step.argv[3] == "/mnt/" + sv.path.lstrip("/")


def test_create_commands_custom_mount_point():
    plan = create_commands(canonical_layout(), mount_point="/staging")
    assert plan.argvs()[0] == ["btrfs", "subvolume", "create", "/staging/@"]
    assert plan.argvs()[-1] == [
        "btrfs",
        "subvolume",
        "create",
        "/staging/@snapshots",
    ]


def test_create_commands_mount_point_trailing_slash_normalized():
    plan = create_commands(canonical_layout(), mount_point="/mnt/")
    assert plan.argvs()[0] == ["btrfs", "subvolume", "create", "/mnt/@"]


def test_create_commands_relative_mount_point_rejected():
    with pytest.raises(SnapshotError, match="absolute path"):
        create_commands(canonical_layout(), mount_point="mnt")


def test_create_commands_empty_mount_point_rejected():
    with pytest.raises(SnapshotError, match="non-empty string"):
        create_commands(canonical_layout(), mount_point="")


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------
def test_snapshot_commands_golden_default_mount():
    plan = snapshot_commands(canonical_layout(), NAME)
    assert plan.kind == "snapshot"
    assert plan.argvs() == [
        [
            "btrfs", "subvolume", "snapshot", "-r",
            "/mnt/@",
            f"/mnt/@snapshots/{NAME}/@",
        ],
        [
            "btrfs", "subvolume", "snapshot", "-r",
            "/mnt/@home",
            f"/mnt/@snapshots/{NAME}/@home",
        ],
        [
            "btrfs", "subvolume", "snapshot", "-r",
            "/mnt/@var",
            f"/mnt/@snapshots/{NAME}/@var",
        ],
    ]


def test_snapshot_commands_skips_snapshot_dir():
    plan = snapshot_commands(canonical_layout(), NAME)
    # @snapshots is the destination, never a source: 3 of 4 subvolumes.
    assert len(plan.steps) == 3
    for argv in plan.argvs():
        src, dest = argv[-2], argv[-1]
        assert dest.startswith("/mnt/@snapshots/")  # every destination is under it
        assert "/@snapshots" not in src  # no source is the snapshot dir


def test_snapshot_commands_are_read_only():
    plan = snapshot_commands(canonical_layout(), NAME)
    for argv in plan.argvs():
        assert argv[3] == "-r"


def test_snapshot_commands_custom_mount_and_name():
    plan = snapshot_commands(canonical_layout(), "pre-upgrade", mount_point="/srv/vol")
    assert plan.argvs()[1] == [
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        "/srv/vol/@home",
        "/srv/vol/@snapshots/pre-upgrade/@home",
    ]


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------
def test_rollback_commands_golden_default_mount():
    plan = rollback_commands(canonical_layout(), NAME)
    assert plan.kind == "rollback"
    assert plan.argvs() == [
        # "@"
        [
            "btrfs", "subvolume", "snapshot",
            f"/mnt/@snapshots/{NAME}/@",
            "/mnt/@.rollback",
        ],
        ["btrfs", "subvolume", "delete", "/mnt/@"],
        ["btrfs", "subvolume", "rename", "/mnt/@.rollback", "/mnt/@"],
        # "@home"
        [
            "btrfs", "subvolume", "snapshot",
            f"/mnt/@snapshots/{NAME}/@home",
            "/mnt/@home.rollback",
        ],
        ["btrfs", "subvolume", "delete", "/mnt/@home"],
        ["btrfs", "subvolume", "rename", "/mnt/@home.rollback", "/mnt/@home"],
        # "@var"
        [
            "btrfs", "subvolume", "snapshot",
            f"/mnt/@snapshots/{NAME}/@var",
            "/mnt/@var.rollback",
        ],
        ["btrfs", "subvolume", "delete", "/mnt/@var"],
        ["btrfs", "subvolume", "rename", "/mnt/@var.rollback", "/mnt/@var"],
    ]


def test_rollback_commands_promote_before_delete_before_rename():
    plan = rollback_commands(canonical_layout(), NAME)
    # 9 steps: 3 subvolumes x (promote, delete, rename).
    assert len(plan.steps) == 9
    for i in range(0, 9, 3):
        promote, delete, rename = plan.steps[i : i + 3]
        assert promote.argv[2] == "snapshot"
        assert delete.argv[2] == "delete"
        assert rename.argv[2] == "rename"
        # the promoted sibling is deleted? no — the LIVE subvolume is deleted.
        assert delete.argv[3] != promote.argv[-1]
        # rename moves the promoted sibling into the live location.
        assert rename.argv[3] == promote.argv[-1]
        assert rename.argv[4] == delete.argv[3]


def test_rollback_commands_promoted_sibling_is_same_parent_as_live():
    """The promote step must land in the top-level so rename (same-parent)
    can move it into place."""
    plan = rollback_commands(canonical_layout(), NAME)
    for i in range(0, 9, 3):
        promote, _, rename = plan.steps[i : i + 3]
        # promote source is under @snapshots/<name>/, dest is top-level.
        assert "/@snapshots/" in promote.argv[3]
        assert promote.argv[4] == rename.argv[3]
        # dest and live share the top-level parent (no @snapshots in dest).
        assert "@snapshots" not in promote.argv[4]


def test_rollback_commands_skip_snapshot_dir():
    plan = rollback_commands(canonical_layout(), NAME)
    # Only @, @home, @var are restored (3 x 3 = 9 steps).
    assert len(plan.argvs()) == 9


# ---------------------------------------------------------------------------
# snapshot-name validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad", ["", ".", "..", "a/b", "a b", "a\tb", "\u00e9", "a/b/c"]
)
def test_snapshot_name_rejects_invalid(bad):
    for fn in (lambda: snapshot_commands(canonical_layout(), bad),
               lambda: rollback_commands(canonical_layout(), bad)):
        with pytest.raises(SnapshotError):
            fn()


def test_snapshot_name_accepts_allowed_chars():
    for good in ("a", "A1._-", "boot-2026-09-16", "pre.upgrade_1"):
        # should not raise
        snapshot_commands(canonical_layout(), good)
        rollback_commands(canonical_layout(), good)


def test_snapshot_name_snapshot_error_is_btrfs_layout_error():
    from pengyos_btrfs.errors import BtrfsLayoutError

    with pytest.raises(BtrfsLayoutError):
        snapshot_commands(canonical_layout(), "a/b")


def test_snapshot_commands_custom_snapshot_dir():
    """A layout whose snapshot dir is not the default also snapshots into it."""
    layout = validate_layout(
        "fs",
        [
            Subvolume(name="@", path="@", mount="/"),
            Subvolume(name="@snap", path="/@snap", read_only=True, mount=None),
        ],
        snapshot_dir="@snap",
    )
    plan = snapshot_commands(layout, NAME)
    assert plan.argvs()[0][-1] == f"/mnt/@snap/{NAME}/@"


# ---------------------------------------------------------------------------
# Plan / Step data model
# ---------------------------------------------------------------------------
def test_plan_is_immutable():
    plan = create_commands(canonical_layout())
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.steps = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.steps[0].argv = ()


def test_step_as_list_returns_fresh_list():
    plan = create_commands(canonical_layout())
    a = plan.steps[0].as_list()
    a[0] = "MUTATED"
    assert plan.steps[0].argv[0] == "btrfs"


def test_plan_argvs_returns_fresh_lists():
    plan = create_commands(canonical_layout())
    got = plan.argvs()
    got[0][0] = "MUTATED"
    assert plan.argvs()[0][0] == "btrfs"


def test_plan_to_dict_golden():
    plan = create_commands(canonical_layout())
    assert plan.to_dict() == {
        "kind": "create",
        "steps": [
            {
                "description": "create subvolume '@' at /mnt/@",
                "argv": ["btrfs", "subvolume", "create", "/mnt/@"],
            },
            {
                "description": "create subvolume '@home' at /mnt/@home",
                "argv": ["btrfs", "subvolume", "create", "/mnt/@home"],
            },
            {
                "description": "create subvolume '@var' at /mnt/@var",
                "argv": ["btrfs", "subvolume", "create", "/mnt/@var"],
            },
            {
                "description": "create subvolume '@snapshots' at /mnt/@snapshots",
                "argv": ["btrfs", "subvolume", "create", "/mnt/@snapshots"],
            },
        ],
    }


def test_plan_kind_values():
    assert create_commands(canonical_layout()).kind == "create"
    assert snapshot_commands(canonical_layout(), NAME).kind == "snapshot"
    assert rollback_commands(canonical_layout(), NAME).kind == "rollback"


# ---------------------------------------------------------------------------
# The mockable runner seam
# ---------------------------------------------------------------------------
def test_mock_runner_records_calls():
    runner = MockRunner()
    plan = create_commands(canonical_layout())
    execute_plan(runner, plan)
    assert runner.calls == [tuple(a) for a in plan.argvs()]


def test_mock_runner_default_success_results():
    runner = MockRunner()
    plan = create_commands(canonical_layout())
    results = execute_plan(runner, plan)
    assert [r.returncode for r in results] == [0] * len(plan.steps)
    assert all(isinstance(r, RunResult) for r in results)


def test_execute_plan_stops_at_first_failure():
    plan = rollback_commands(canonical_layout(), NAME)
    first_delete_argv = plan.argvs()[4]  # the @ delete step
    runner = MockRunner(fail_on=[first_delete_argv])
    with pytest.raises(BtrfsCommandError) as exc:
        execute_plan(runner, plan)
    # Stopped right after the failing step: 5 steps ran (indices 0..4).
    assert len(runner.calls) == 5
    assert exc.value.argv == tuple(first_delete_argv)
    assert exc.value.returncode == 1
    assert "command failed (exit 1)" in str(exc.value)


def test_execute_plan_success_runs_every_step():
    runner = MockRunner()
    plan = rollback_commands(canonical_layout(), NAME)
    results = execute_plan(runner, plan)
    assert len(results) == 9
    assert runner.calls == [tuple(a) for a in plan.argvs()]


def test_mock_runner_configured_result():
    plan = create_commands(canonical_layout())
    target = tuple(plan.argvs()[0])
    custom = RunResult(target, 0, "custom-stdout", "")
    runner = MockRunner(results={target: custom})
    results = execute_plan(runner, plan)
    assert results[0].stdout == "custom-stdout"
    assert results[0] is custom


def test_btrfs_command_error_carries_argv():
    err = BtrfsCommandError("boom", argv=("btrfs", "x"), returncode=7,
                            stderr="details")
    assert err.argv == ("btrfs", "x")
    assert err.returncode == 7
    assert err.stderr == "details"


def test_real_runner_is_a_btrfs_runner():
    assert issubclass(RealRunner, BtrfsRunner)


# ---------------------------------------------------------------------------
# Integration (opt-in): real btrfs, gated behind PENGYOS_IT=1
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_integration_real_create_requires_btrfs():
    """Against a real, root-owned btrfs volume (PENGYOS_IT=1), actually run
    the create plan through the RealRunner. Skipped in the unit suite."""
    plan = create_commands(canonical_layout(), mount_point=os.environ["PENGYOS_IT_MOUNT"])
    execute_plan(RealRunner(), plan)
