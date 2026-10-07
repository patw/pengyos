"""Tests for the install engine.

``install`` executes the golden :class:`~pengyos_install.models.Plan` through the
injectable :class:`~pengyos_install.runner.Runner` seam: it refuses without
confirmation, re-checks the target each run, hard-fails on the first non-ok step,
and tears down (unmount the mounts it opened, then ``sync``) on success *and* on
failure.

The fixtures (``release_json``, ``layout_json``) live in ``conftest.py``; the
engine is driven entirely through a :class:`FakeRunner`, so no root / hardware /
network is needed.
"""

from __future__ import annotations

import pytest

from pengyos_install.install import (
    InstallEngine,
    InstallError,
    InstallRefused,
    InstallResult,
    install,
)
from pengyos_install.models import Layout, Plan, Release, Step, ValidationError
from pengyos_install.plan import render_plan
from pengyos_install.runner import FakeRunner, RunResult


def _plan(release_json, layout_json):
    return render_plan(
        Release.from_json(release_json), Layout.from_json(layout_json)
    )


# --------------------------------------------------------------------------- #
# Refusal
# --------------------------------------------------------------------------- #
def test_install_refuses_without_confirm(release_json, layout_json):
    """No --yes -> InstallRefused; nothing runs."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    with pytest.raises(InstallRefused):
        install(plan, runner, confirm=False)
    assert runner.calls == []  # no step executed


def test_install_engine_refuses_without_confirm(release_json, layout_json):
    engine = InstallEngine(_plan(release_json, layout_json), FakeRunner(), confirm=False)
    with pytest.raises(InstallRefused):
        engine.install()


def test_install_refused_is_an_install_error():
    assert issubclass(InstallRefused, InstallError)


# --------------------------------------------------------------------------- #
# Refusal: an unverified release is refused (even with --yes)
# --------------------------------------------------------------------------- #
def _unsigned_release(release_json):
    """A release whose signature has not been verified."""
    return release_json.replace('"verified": true', '"verified": false')


def test_install_refuses_unsigned_release(release_json, layout_json):
    """A release whose signature is not verified is refused (InstallRefused)."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    with pytest.raises(InstallRefused):
        install(plan, runner, confirm=True, release=Release.from_json(_unsigned_release(release_json)))


def test_install_refuses_unsigned_even_with_yes(release_json, layout_json):
    """--yes acknowledges the destructive write, but NOT an unverified release."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    release = Release.from_json(_unsigned_release(release_json))
    with pytest.raises(InstallRefused):
        install(plan, runner, confirm=True, release=release)
    # the refusal is a security gate: it fires *before* any step runs.
    assert runner.calls == []


def test_install_refuses_unsigned_before_any_io(release_json, layout_json):
    """The unsigned refusal happens before the target re-check / first step."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    engine = InstallEngine(
        plan, runner, confirm=True, release=Release.from_json(_unsigned_release(release_json))
    )
    with pytest.raises(InstallRefused):
        engine.install()
    assert runner.calls == []  # nothing executed — not even the re-check side effects


def test_install_signed_release_proceeds(release_json, layout_json):
    """A verified release (the fixture) installs normally through the runner."""
    plan = _plan(release_json, layout_json)
    release = Release.from_json(release_json)
    assert release.is_signed is True
    result = install(plan, FakeRunner(), confirm=True, release=release)
    assert result.success is True


def test_install_refuses_unsigned_is_an_install_error():
    assert issubclass(InstallRefused, InstallError)


# --------------------------------------------------------------------------- #
# Happy path: run every step, then teardown
# --------------------------------------------------------------------------- #
def test_install_runs_every_step(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    result = install(plan, runner, confirm=True)

    assert isinstance(result, InstallResult)
    assert result.success is True
    assert result.steps_run == len(plan)
    # every plan step argv was run, in order — followed by the teardown sync
    assert runner.calls[: len(plan)] == [s.to_argv() for s in plan]
    assert runner.calls[-1] == ["sync"]


def test_install_ends_with_teardown_sync(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    result = install(plan, runner, confirm=True)
    assert result.success is True
    # the last thing the engine does is sync (durability)
    assert runner.calls[-1] == ["sync"]


def test_install_result_reports_step_count(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    result = install(plan, FakeRunner(), confirm=True)
    assert result.steps_run == len(plan)
    assert result.success is True


def test_install_executes_in_plan_order(release_json, layout_json):
    """The runner sees the steps in the golden order (partition first, provision last)."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    install(plan, runner, confirm=True)
    first = runner.calls[0]
    last = runner.calls[-2]  # -1 is the sync teardown
    assert first[:2] == ["parted", "/dev/sda"]
    assert "mklabel" in first
    assert last[0] == "pengyos-install"
    assert last[1] == "provision-plan"


# --------------------------------------------------------------------------- #
# Atomic write: the plan writes the inactive slot, then commits the active
# --------------------------------------------------------------------------- #
def test_install_executes_atomic_slot_write(release_json, layout_json):
    """The two rootfs writes run in order: inactive slot first, active slot committed."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    install(plan, runner, confirm=True)

    rootfs_writes = [c for c in runner.calls if "if=rootfs.squashfs" in c]
    assert len(rootfs_writes) == 2
    # slot 'a' is active -> write hits slot b (/dev/sda3) first, commits /dev/sda2
    assert rootfs_writes[0][2] == "of=/dev/sda3"
    assert rootfs_writes[1][2] == "of=/dev/sda2"


def test_install_atomic_write_reversed_when_active_is_b(
    release_json, layout_json
):
    text = release_json.replace('"slot": "a"', '"slot": "b"')
    plan = _plan(text, layout_json)
    runner = FakeRunner()
    install(plan, runner, confirm=True)
    rootfs_writes = [c for c in runner.calls if "if=rootfs.squashfs" in c]
    assert rootfs_writes[0][2] == "of=/dev/sda2"  # slot a inactive when b active
    assert rootfs_writes[1][2] == "of=/dev/sda3"  # commit slot b


# --------------------------------------------------------------------------- #
# Hard fail on a required step
# --------------------------------------------------------------------------- #
def test_install_hard_fails_on_non_ok_step(release_json, layout_json):
    """The first non-ok step aborts; no further steps run."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    # make the mkfs.fat step (unique prefix) fail
    failing = plan[5]
    assert failing.argv[0] == "mkfs.fat"
    runner.respond(["mkfs.fat"],
                   RunResult(argv=failing.argv, returncode=1, stderr="boom"))
    with pytest.raises(InstallError):
        install(plan, runner, confirm=True)
    # the 5 steps before it ran ok, the failing step was attempted (recorded),
    # and nothing after it ran — only the teardown ``sync`` follows.
    assert runner.calls[:5] == [s.to_argv() for s in plan[:5]]
    assert runner.calls[5] == list(failing.argv)
    assert runner.calls[-1] == ["sync"]
    assert len(runner.calls) == 7  # 5 ok + 1 failing + 1 sync


def test_install_error_reports_the_failing_step(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    failing = plan[12]
    assert failing.argv[0] == "install-bootctl"  # unique prefix
    runner.respond(
        ["install-bootctl"],
        RunResult(argv=failing.argv, returncode=7, stderr="disk full"),
    )
    with pytest.raises(InstallError) as excinfo:
        install(plan, runner, confirm=True)
    msg = str(excinfo.value)
    assert "disk full" in msg
    assert "failed" in msg


# --------------------------------------------------------------------------- #
# Teardown: unmount tracked mounts on success AND on failure
# --------------------------------------------------------------------------- #
def test_install_tears_down_mounts_on_success(release_json, layout_json):
    """Mounts the engine opens are unmounted (reverse order) and synced."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    engine = InstallEngine(plan, runner, confirm=True)
    engine.mount("/dev/sda4", "/mnt/data")
    engine.mount("/dev/sda2", "/mnt/root")
    result = engine.install()

    assert result.success is True
    # both mounts unmounted, in reverse order of opening
    assert ["umount", "/mnt/root"] in runner.calls
    assert ["umount", "/mnt/data"] in runner.calls
    assert result.unmounted == ["/mnt/root", "/mnt/data"]
    assert result.unmounted_all is True
    # sync ran after the unmounts
    assert runner.calls[-1] == ["sync"]


def test_install_tears_down_mounts_on_failure(release_json, layout_json):
    """A mid-run failure still unmounts everything the engine opened."""
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    engine = InstallEngine(plan, runner, confirm=True)
    engine.mount("/dev/sda4", "/mnt/data")
    # fail the first step
    runner.respond(
        plan[0].argv[:2],
        RunResult(argv=plan[0].argv, returncode=1, stderr="refused"),
    )
    with pytest.raises(InstallError):
        engine.install()
    # the mount was unmounted and synced even though the install aborted
    assert ["umount", "/mnt/data"] in runner.calls
    assert runner.calls[-1] == ["sync"]


def test_install_unmounts_in_reverse_order(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    runner = FakeRunner()
    engine = InstallEngine(plan, runner, confirm=True)
    engine.mount("/dev/sda4", "/mnt/a")
    engine.mount("/dev/sda2", "/mnt/b")
    engine.mount("/dev/sda3", "/mnt/c")
    engine.install()
    # reverse of open order: c, b, a
    umounts = [c for c in runner.calls if c[0] == "umount"]
    assert umounts == [["umount", "/mnt/c"], ["umount", "/mnt/b"], ["umount", "/mnt/a"]]


def test_install_result_unmounted_all_true_without_mounts(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    result = install(plan, FakeRunner(), confirm=True)
    # no mounts opened -> vacuously all unmounted
    assert result.unmounted_all is True
    assert result.mounted == []


# --------------------------------------------------------------------------- #
# Re-check the target each run
# --------------------------------------------------------------------------- #
def test_install_rechecks_target_slot(release_json, layout_json):
    """The release slot is re-checked against the declared layout each run."""
    plan = _plan(release_json, layout_json)
    # a layout that no longer declares the release's slot ('a') -> re-check fails.
    bad_layout = """\
{
  "target": "/dev/sda",
  "table": "gpt",
  "slots": {
    "b": { "partition": "/dev/sda3", "type": "root", "index": 3 }
  },
  "esp": { "partition": "/dev/sda1", "index": 1 },
  "data": { "partition": "/dev/sda4", "index": 4 }
}
"""
    bad = Layout.from_json(bad_layout)
    engine = InstallEngine(plan, FakeRunner(), confirm=True, layout=bad,
                           release=Release.from_json(release_json))
    # the release slot 'a' is not declared in the bad layout -> re-check fails.
    with pytest.raises(ValidationError):
        engine.install()


def test_install_recheck_passes_for_valid_target(release_json, layout_json):
    plan = _plan(release_json, layout_json)
    layout = Layout.from_json(layout_json)
    release = Release.from_json(release_json)
    # round-trips cleanly -> no re-check error; install proceeds.
    result = install(plan, FakeRunner(), confirm=True, layout=layout, release=release)
    assert result.success is True


# --------------------------------------------------------------------------- #
# InstallResult shape
# --------------------------------------------------------------------------- #
def test_install_result_defaults():
    result = InstallResult(steps_run=0, success=False)
    assert result.success is False
    assert result.steps_run == 0
    assert result.mounted == []
    assert result.unmounted == []
    assert result.result.returncode == 0


# --------------------------------------------------------------------------- #
# A bare plan (no render) also executes
# --------------------------------------------------------------------------- #
def test_install_runs_a_bare_plan():
    plan = Plan.from_steps(
        [
            Step.from_argv(["dd", "if=a", "of=/dev/sda2"]),
            Step.from_argv(["dd", "if=b", "of=/dev/sda3"]),
        ]
    )
    runner = FakeRunner()
    result = install(plan, runner, confirm=True)
    assert result.success is True
    assert runner.calls[0] == ["dd", "if=a", "of=/dev/sda2"]
    assert runner.calls[-1] == ["sync"]


def test_install_runs_steps_with_env(release_json, layout_json):
    """Per-step env overlays are passed to the runner."""
    plan = Plan.from_steps(
        [Step.from_argv(["dd", "if=a"], env={"FOO": "bar"})]
    )
    runner = FakeRunner()
    install(plan, runner, confirm=True)
    assert runner.envs[0] == {"FOO": "bar"}
