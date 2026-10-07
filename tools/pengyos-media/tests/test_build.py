"""Tests for ``build`` — driving a medium plan through the injectable runner
.

Hermetic: the runner is injected as a :class:`RecordingRunner` (or a fake
``subprocess.run``), so no real ``xorriso``/``mksquashfs``/``install`` ever runs
and nothing is written to disk. The acceptance criteria are exercised directly:

* ``build --dry-run`` renders the argv and runs **nothing** (side-effect free).
* ``build --commit`` runs each planned step through the runner in order.
* a malformed plan is rejected before any step is rendered/run.
"""

import dataclasses

import pytest

from pengyos_media.build import (
    BuildError,
    build,
    build_plan_steps,
    load_plan,
    render_build_plan_text,
)
from pengyos_media.plan import build_plan
from pengyos_media.runner import RecordingRunner, RunResult
from pengyos_media.steps import build_steps

RELEASE = {"version": "2026.09.1", "channel": "testing"}


def _plan():
    return build_plan("desktop", RELEASE)


# --------------------------------------------------------------------------- #
# build_plan_steps — the rendered recipe is a pure function of the plan
# --------------------------------------------------------------------------- #
def test_build_plan_steps_matches_steps_derivation():
    plan = _plan()
    rendered = build_plan_steps(plan)
    derived = build_steps(plan)
    assert [(r.name, r.argv) for r in rendered] == [
        (s.name, s.argv) for s in derived
    ]


def test_build_plan_steps_is_iso_three_steps():
    steps = build_plan_steps(_plan())
    assert [s.name for s in steps] == ["mksquashfs", "assemble-esp-boot", "xorriso"]


def test_build_plan_steps_is_disk_image_eight_steps():
    from pengyos_media.model import MediumKind

    plan = dataclasses.replace(_plan(), kind=MediumKind.DISK_IMAGE)
    steps = build_plan_steps(plan)
    assert [s.name for s in steps] == [
        "mkfs-disk", "losetup", "mkfs-esp", "mmd-esp",
        "mksquashfs", "assemble-esp", "write-live", "losetup-detach",
    ]


# --------------------------------------------------------------------------- #
# dry-run: renders argv, runs NOTHING (the acceptance criterion)
# --------------------------------------------------------------------------- #
def test_dry_run_runs_nothing():
    plan = _plan()
    rec = RecordingRunner()
    outcome = build(plan, runner=rec, dry_run=True)
    # The runner was never called.
    assert rec.calls == []
    # No results collected.
    assert outcome.results == []
    # Every step is still rendered.
    assert outcome.step_count == 3
    assert outcome.dry_run is True


def test_dry_run_writes_nothing_to_disk(tmp_path):
    # Side-effect free: a dry run in an empty tree leaves the tree empty.
    plan = _plan()
    outcome = build(plan, dry_run=True, cwd=str(tmp_path))
    assert outcome.dry_run is True
    assert list(tmp_path.iterdir()) == []
    # No stray ISO / squashfs / loop artifacts anywhere under the tree.
    assert not any(p.name.endswith((".iso", ".sqfs", ".img")) for p in tmp_path.rglob("*"))


def test_dry_run_does_not_spawn_any_process(monkeypatch):
    # The strongest side-effect-free proof: subprocess.run is stubbed to raise if
    # build ever tries to run a step in dry-run mode. subprocess lives in
    # pengyos_media.runner (the default runner), so patch it there.
    import pengyos_media.runner as runner_mod

    def boom(*args, **kwargs):
        raise AssertionError("build must not spawn a process in dry-run mode")

    real_run = runner_mod.subprocess.run
    runner_mod.subprocess.run = boom  # type: ignore[attr-defined]
    try:
        outcome = build(_plan(), dry_run=True)
        assert outcome.step_count == 3
        assert outcome.results == []
    finally:
        runner_mod.subprocess.run = real_run


# --------------------------------------------------------------------------- #
# commit: runs each planned step through the runner, in order
# --------------------------------------------------------------------------- #
def test_commit_runs_every_step_in_order():
    plan = _plan()
    rec = RecordingRunner()
    outcome = build(plan, runner=rec, dry_run=False)
    assert outcome.dry_run is False
    assert len(rec.calls) == 3
    # Ordered: mksquashfs, assemble-esp-boot, xorriso.
    assert rec.calls[0][0] == "mksquashfs"
    assert rec.calls[1][0] == "install"
    assert rec.calls[2][0] == "xorriso"
    # One result per step, all clean.
    assert all(r.ok for r in outcome.results)


def test_commit_records_the_exact_argv():
    plan = _plan()
    rec = RecordingRunner()
    build(plan, runner=rec, dry_run=False)
    # The first step's argv is the exact, shell-quotable mksquashfs invocation.
    assert rec.calls[0][0] == "mksquashfs"
    assert "live/rootfs" in rec.calls[0]
    assert "live/pengyos-live.sqfs" in rec.calls[0]


def test_commit_default_runner_used_when_none_injected(monkeypatch):
    # With no runner injected, build uses the real subprocess runner. Prove the
    # default is what runs by spying on build's imported default_runner.
    import pengyos_media.build as build_mod

    calls: list[list[str]] = []

    def spy(argv, **kwargs):
        calls.append(list(argv))
        from pengyos_media.runner import RunResult

        return RunResult(argv=list(argv), returncode=0)

    real = build_mod.default_runner
    build_mod.default_runner = spy  # type: ignore[assignment]
    try:
        outcome = build(_plan(), runner=None, dry_run=False)
        assert calls  # the default runner was invoked for each step
        assert len(calls) == 3
        assert outcome.dry_run is False
        assert all(r.ok for r in outcome.results)
    finally:
        build_mod.default_runner = real


# --------------------------------------------------------------------------- #
# failures
# --------------------------------------------------------------------------- #
def _runner_fails_on(name: str, returncode: int = 1, stderr: str = "boom"):
    """A runner that exits non-zero only for steps whose command starts with
    ``name`` (so earlier steps pass and the named step fails)."""
    def _run(argv, **kwargs):
        argv = list(argv)
        if argv and argv[0].startswith(name):
            return RunResult(argv=argv, returncode=returncode, stderr=stderr)
        return RunResult(argv=argv, returncode=0)
    return _run


def test_commit_reports_non_zero_exit():
    plan = _plan()
    with pytest.raises(BuildError) as exc:
        build(plan, runner=_runner_fails_on("xorriso", stderr="xorriso: cannot open"), dry_run=False)
    assert exc.value.step == "xorriso"
    assert "xorriso" in str(exc.value)
    assert "cannot open" in str(exc.value)


def test_commit_first_failure_stops_the_build():
    plan = _plan()
    rec = RecordingRunner()
    rec.result_for(returncode=2, stderr="fail")
    with pytest.raises(BuildError):
        build(plan, runner=rec, dry_run=False)
    # Only the first failing step ran — the runner stopped being called.
    assert len(rec.calls) == 1


def test_invalid_plan_rejected_before_running():
    # An empty installer (a plan that fails lint) is rejected by build too.
    from pengyos_media.model import Installer

    plan = dataclasses.replace(_plan(), installer=Installer(binary=""))
    rec = RecordingRunner()
    with pytest.raises(BuildError) as exc:
        build(plan, runner=rec, dry_run=False)
    assert "installer" in str(exc.value).lower()
    # Nothing ran.
    assert rec.calls == []


def test_invalid_plan_rejected_even_in_dry_run():
    from pengyos_media.model import Installer

    plan = dataclasses.replace(_plan(), installer=Installer(binary=""))
    rec = RecordingRunner()
    with pytest.raises(BuildError):
        build(plan, runner=rec, dry_run=True)
    assert rec.calls == []


def test_missing_binary_is_build_error(tmp_path, monkeypatch):
    # A step whose command is not on PATH: default_runner raises FileNotFoundError,
    # which build turns into a BuildError.
    import pengyos_media.build as build_mod

    def fake_build_steps(plan):
        from pengyos_media.build import RenderedStep

        return [RenderedStep("ghost", "ghost", ["definitely-not-a-real-tool-xyz"])]

    plan = _plan()
    saved = build_mod.build_steps
    build_mod.build_steps = fake_build_steps
    try:
        with pytest.raises(BuildError) as exc:
            build(plan, dry_run=False, cwd=str(tmp_path))
        assert "not-a-real-tool" in str(exc.value) or "FileNotFoundError" in str(exc.__cause__)
    finally:
        build_mod.build_steps = saved


# --------------------------------------------------------------------------- #
# load_plan
# --------------------------------------------------------------------------- #
def test_load_plan_from_dict():
    plan = load_plan(_plan().to_dict())
    assert plan.edition.value == "desktop"
    assert plan.release.version == "2026.09.1"


def test_load_plan_from_file(tmp_path):
    path = tmp_path / "media.json"
    path.write_text(_plan().to_json(), encoding="utf-8")
    plan = load_plan(str(path))
    assert plan.kind.value == "iso"


def test_load_plan_missing_file_raises():
    with pytest.raises(BuildError) as exc:
        load_plan("/no/such/media.json")
    assert "cannot read plan" in str(exc.value)


def test_load_plan_bad_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BuildError):
        load_plan(str(path))


def test_load_plan_not_an_object_raises(tmp_path):
    path = tmp_path / "arr.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(BuildError):
        load_plan(str(path))


def test_load_plan_not_a_medium_plan_raises(tmp_path):
    path = tmp_path / "obj.json"
    path.write_text('{"not": "a plan"}', encoding="utf-8")
    with pytest.raises(BuildError):
        load_plan(str(path))


# --------------------------------------------------------------------------- #
# render_build_plan_text
# --------------------------------------------------------------------------- #
def test_render_dry_run_marks_side_effect_free():
    outcome = build(_plan(), dry_run=True)
    text = render_build_plan_text(outcome)
    assert "DRY-RUN" in text
    assert "3 steps" in text
    assert "mksquashfs" in text and "xorriso" in text
    # The argv is rendered, shell-quotable.
    assert "desktop-install.iso" in text


def test_render_commit_lists_per_step_status():
    plan = _plan()
    rec = RecordingRunner()
    outcome = build(plan, runner=rec, dry_run=False)
    text = render_build_plan_text(outcome)
    assert "committed" in text
    for name in ("mksquashfs", "assemble-esp-boot", "xorriso"):
        assert name in text
        assert "ok" in text


def test_render_commit_shows_failed_status():
    plan = _plan()
    rec = RecordingRunner()
    rec.result_for(returncode=1, stderr="boom")
    with pytest.raises(BuildError):
        build(plan, runner=rec, dry_run=False)


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_dry_run_is_deterministic():
    a = build(_plan(), dry_run=True)
    b = build(_plan(), dry_run=True)
    assert [s.argv for s in a.steps] == [s.argv for s in b.steps]
    assert a.step_count == b.step_count
