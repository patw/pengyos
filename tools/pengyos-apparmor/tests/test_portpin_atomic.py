"""Failure-injection tests for the staged-atomic write side of the
port-pin transition.

The port-pin item (still blocked — the port must never be guessed) has
a complete mechanical transition: ``apply-transition`` commits the four
transition files and ``sync_docs`` commits the four prose doc surfaces.
Both previously used plain sequential ``write_text``: an interrupted
write (SIGKILL, OOM, disk full) could leave a *torn* file — syntactically
broken, classified by the ``port-pin-status`` probe as ``drifted`` for
the wrong reason ("carries neither anchor"), and in the case of
``pengyos_apparmor/validator.py`` broken enough that the suite cannot
even import the package.

This group pins the hardened contract of ``portpin.atomic_commit``
(staged temp file + fsync + atomic ``os.replace`` rename, staging files
cleaned up on every failure path):

* a failure during the *write phase* (staging) leaves every target
  byte-identical and no staging file behind;
* a failure during the *commit phase* (renames) leaves a *mixed* —
  never *torn* — state: some files carry their complete new content,
  the rest their complete old content, and the probe reports
  ``partial``;
* the torn-file failure mode of the pre-hardening writer is
  demonstrated and shown to be exactly what the probe classifies as
  ``drifted``/``partial``;
* both CLI commands (``apply-port-pin``, ``sync-port-pin-docs``) map an
  interrupted commit to a clean exit 2 with an actionable message, and
  an interrupted docs sync can be finished by an idempotent re-run.

All checks run on ``tmp_path`` fixture repos and fixture scan roots —
no network, no root, no AppArmor tooling, and never a guessed port (the
evidence for every apply comes from a fixture scan root that defines
the port).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pengyos_apparmor import portdiscovery, portdocsync, portpin, portstatus
from pengyos_apparmor.cli import main
from test_portpin_apply import PORT, UNBOUND, build_repo
from test_transition_end_to_end import copy_repo

TOOL_ROOT = portdiscovery.TOOL_ROOT


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def upstream_scan(tmp_path) -> Path:
    """A fixture scan root whose SPEC.md defines PORT (8080)."""
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "SPEC.md").write_text(
        f"The model server's default port is {PORT}.\n", encoding="utf-8"
    )
    return scan


def _transition_map(repo: Path) -> dict[str, bytes]:
    return {rel: (repo / rel).read_bytes() for rel in portpin.PORT_PIN_TRANSITION_FILES}


def _doc_map(repo: Path) -> dict[str, bytes]:
    return {rel: (repo / rel).read_bytes() for rel in portdocsync.PORT_PIN_DOC_FILES}


def _staging_files(root: Path) -> list[Path]:
    return sorted(root.rglob(f"*{portpin.STAGING_SUFFIX}"))


def _fail_fsync_after(n: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the (n+1)-th fsync in ``portpin`` raise a disk-full OSError."""
    real = portpin.os.fsync
    calls = {"n": 0}

    def fake(fd):
        calls["n"] += 1
        if calls["n"] > n:
            raise OSError(28, "No space left on device (injected)")
        return real(fd)

    monkeypatch.setattr(portpin.os, "fsync", fake)


def _fail_replace_after(n: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the (n+1)-th rename in ``portpin`` raise an OSError."""
    real = portpin.os.replace
    calls = {"n": 0}

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] > n:
            raise OSError(5, "I/O error during rename (injected)")
        return real(src, dst)

    monkeypatch.setattr(portpin.os, "replace", fake)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_apply_success_leaves_no_staging_files_and_pins_all_files(tmp_path):
    repo = build_repo(tmp_path / "repo")
    edits = portpin.apply_transition(PORT, repo)
    assert [e.rel for e in edits] == list(portpin.PORT_PIN_TRANSITION_FILES)
    for e in edits:
        assert (repo / e.rel).read_bytes() == e.new.encode("utf-8")
    assert _staging_files(repo) == [], "staging files must not survive a commit"


def test_staging_name_carries_the_sequence_position_and_suffix(tmp_path):
    """The staging temp name is ``<target>.<index><STAGING_SUFFIX>`` so
    each file of the commit stages into its own temp file (no collision
    between concurrent retries of the same transition)."""
    assert portpin.STAGING_SUFFIX == ".pin-tmp"
    repo = build_repo(tmp_path / "repo")
    edits = portpin.compute_transition(PORT, repo)

    staged_paths = []
    real = portpin.os.replace

    def spy(src, dst):
        staged_paths.append(Path(src))
        return real(src, dst)

    orig = portpin.os.replace
    portpin.os.replace = spy
    try:
        portpin.atomic_commit(repo, edits)
    finally:
        portpin.os.replace = orig
    for i, edit in enumerate(edits):
        target = repo / edit.rel
        expected = target.parent / f"{target.name}.{i}{portpin.STAGING_SUFFIX}"
        assert staged_paths[i] == expected, (
            f"edit {i} ({edit.rel}) staged as {staged_paths[i]}, expected {expected}"
        )


def test_apply_result_is_pinned_and_probe_agrees(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    repo = build_repo(tmp_path / "repo")
    portpin.apply_transition(PORT, repo)
    status = portstatus.inspect_transition(repo, scan)
    assert status.state == portstatus.STATE_PINNED and status.port == PORT


# ---------------------------------------------------------------------------
# Write-phase (staging) failures — nothing may change
# ---------------------------------------------------------------------------


def test_write_phase_failure_leaves_every_target_unchanged_and_no_staging(tmp_path, monkeypatch):
    """A disk-full failure while staging the second file must leave all
    four targets byte-identical and remove every staging file — the repo
    is exactly where it was before the apply."""
    repo = build_repo(tmp_path / "repo")
    before = _transition_map(repo)
    _fail_fsync_after(1, monkeypatch)
    with pytest.raises(OSError, match="No space left on device"):
        portpin.apply_transition(PORT, repo)
    assert _transition_map(repo) == before
    assert _staging_files(repo) == []
    # The state probe sees the untouched, pre-transition repo.
    scan = tmp_path / "scan"
    scan.mkdir()
    status = portstatus.inspect_transition(repo, scan)
    assert status.state == portstatus.STATE_BLOCKED
    assert all(f.form == portstatus.FORM_UNBOUND for f in status.files)


def test_write_phase_failure_on_the_first_file_stages_nothing(tmp_path, monkeypatch):
    repo = build_repo(tmp_path / "repo")
    before = _transition_map(repo)
    _fail_fsync_after(0, monkeypatch)
    with pytest.raises(OSError):
        portpin.apply_transition(PORT, repo)
    assert _transition_map(repo) == before
    assert _staging_files(repo) == []


def test_write_phase_failure_removes_the_failing_file_own_staging(tmp_path, monkeypatch):
    """The staging file of the file whose OWN fsync failed (not yet
    appended to the success list) must be removed too."""
    repo = build_repo(tmp_path / "repo")
    _fail_fsync_after(2, monkeypatch)  # third file's fsync fails
    with pytest.raises(OSError):
        portpin.apply_transition(PORT, repo)
    assert _staging_files(repo) == [], (
        "the staging file of the failed write itself must not leak"
    )


# ---------------------------------------------------------------------------
# Commit-phase (rename) failures — mixed, never torn
# ---------------------------------------------------------------------------


def test_commit_phase_failure_leaves_mixed_never_torn_state(tmp_path, monkeypatch):
    """A failure on the second rename leaves file 0 with its COMPLETE new
    content and the rest with their COMPLETE old content: a mixed state
    the probe classifies ``partial``, not a torn file."""
    repo = build_repo(tmp_path / "repo")
    before = _transition_map(repo)
    edits = portpin.compute_transition(PORT, repo)
    _fail_replace_after(1, monkeypatch)
    with pytest.raises(OSError, match="I/O error during rename"):
        portpin.apply_transition(PORT, repo)

    after = _transition_map(repo)
    # file 0 committed, files 1-3 untouched — every file is one of the
    # two COMPLETE contents (the never-torn invariant).
    assert after["profiles/pengy-agent"] == edits[0].new.encode("utf-8")
    for rel in portpin.PORT_PIN_TRANSITION_FILES[1:]:
        assert after[rel] == before[rel]
    for rel, content in after.items():
        assert content in (before[rel], edits[next(i for i, e in enumerate(edits) if e.rel == rel)].new.encode("utf-8")), (
            f"{rel} is torn: carries neither complete content"
        )
    assert _staging_files(repo) == []

    # The probe reports exactly the partial (mixed) state — and the
    # committed file is recognized as PINNED, not drifted.
    scan = tmp_path / "scan"
    scan.mkdir()
    status = portstatus.inspect_transition(repo, scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_PINNED
    assert by_rel["profiles/pengy-agent"].port == PORT
    for rel in portpin.PORT_PIN_TRANSITION_FILES[1:]:
        assert by_rel[rel].form == portstatus.FORM_UNBOUND


def test_commit_phase_failure_on_the_last_rename_leaves_three_committed(tmp_path, monkeypatch):
    repo = build_repo(tmp_path / "repo")
    before = _transition_map(repo)
    edits = portpin.compute_transition(PORT, repo)
    _fail_replace_after(3, monkeypatch)
    with pytest.raises(OSError):
        portpin.apply_transition(PORT, repo)
    after = _transition_map(repo)
    for i, rel in enumerate(portpin.PORT_PIN_TRANSITION_FILES[:3]):
        assert after[rel] == edits[i].new.encode("utf-8")
    assert after[portpin.PORT_PIN_TRANSITION_FILES[3]] == before[portpin.PORT_PIN_TRANSITION_FILES[3]]
    assert _staging_files(repo) == []


def test_commit_phase_failure_then_probe_then_one_way_guard(tmp_path, monkeypatch, upstream_scan):
    """After a mid-commit crash, the one-way guard still holds (a
    re-apply is refused) and the mixed state is stable for a human to
    reconcile (the rendered patch remains the reference)."""
    repo = build_repo(tmp_path / "repo")
    _fail_replace_after(1, monkeypatch)
    with pytest.raises(OSError):
        portpin.apply_transition(PORT, repo)
    monkeypatch.undo()
    with pytest.raises(portpin.PortPinTransitionError, match="already pins a port"):
        portpin.apply_transition(PORT, repo)
    # The transition files are untouched by the refused re-apply.
    assert (repo / "profiles/pengy-agent").read_text(encoding="utf-8").startswith(
        "pengy-agent (pengy-agent.bin) {"
    )


# ---------------------------------------------------------------------------
# The pre-hardening failure mode, pinned for the record
# ---------------------------------------------------------------------------


def test_torn_file_from_the_old_writer_is_classified_partial(tmp_path):
    """Demonstration: the pre-hardening writer truncated mid-line leaves
    a torn file that the probe can only classify as ``drifted`` (for the
    wrong reason — 'carries neither anchor'). This is the failure mode
    the staged-atomic commit removes from the apply path; the probe's
    partial state remains the detector for anything that gets torn by
    other means (a manual edit, a partial ``git apply``)."""
    repo = build_repo(tmp_path / "repo")
    profile = repo / "profiles/pengy-agent"
    text = profile.read_text(encoding="utf-8")
    # Truncate mid-anchor-line: cut off the second line halfway.
    first = text.splitlines(keepends=True)[0]
    profile.write_text(first + "  network inet tc", encoding="utf-8")
    scan = tmp_path / "scan"
    scan.mkdir()
    status = portstatus.inspect_transition(repo, scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_DRIFTED
    assert "neither" in by_rel["profiles/pengy-agent"].detail


# ---------------------------------------------------------------------------
# CLI: interrupted commits map to a clean exit 2 + actionable message
# ---------------------------------------------------------------------------


def test_cli_apply_interruption_exits_two_writes_nothing(tmp_path, upstream_scan, monkeypatch, capsys):
    repo = build_repo(tmp_path / "repo")
    before = _transition_map(repo)
    _fail_fsync_after(1, monkeypatch)
    code = main(["apply-port-pin", str(PORT), str(repo), "--scan-root", str(upstream_scan)])
    assert code == 2
    err = capsys.readouterr().err
    assert "staged-atomic commit was interrupted" in err
    assert "port-pin-status" in err  # the actionable re-check command
    assert _transition_map(repo) == before
    assert _staging_files(repo) == []


def test_cli_apply_interruption_mid_rename_exits_two_and_probe_reports_partial(
    tmp_path, upstream_scan, monkeypatch, capsys
):
    repo = build_repo(tmp_path / "repo")
    _fail_replace_after(1, monkeypatch)
    code = main(["apply-port-pin", str(PORT), str(repo), "--scan-root", str(upstream_scan)])
    assert code == 2
    capsys.readouterr()
    status = portstatus.inspect_transition(repo, upstream_scan)
    assert status.state == portstatus.STATE_PARTIAL
    assert _staging_files(repo) == []


def test_cli_docs_sync_interruption_is_finishable_by_an_idempotent_rerun(
    tmp_path, upstream_scan, monkeypatch, capsys
):
    """A docs sync interrupted mid-commit leaves every doc file complete
    (pre-sync content); the idempotent re-run then finishes the sync and
    the surfaces carry the pinned form."""
    repo = copy_repo(tmp_path / "repo")
    assert main(["apply-port-pin", str(PORT), str(repo), "--scan-root", str(upstream_scan)]) == 0
    before = _doc_map(repo)
    _fail_fsync_after(1, monkeypatch)
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "staged-atomic docs commit was interrupted" in err
    assert "idempotent" in err
    assert _doc_map(repo) == before  # every surface still complete, pre-sync
    assert _staging_files(repo) == []

    # The re-run (no failure injected) finishes the job.
    monkeypatch.undo()
    capsys.readouterr()
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "docs synced" in out
    for rel in portdocsync.PORT_PIN_DOC_FILES:
        assert str(PORT) in (repo / rel).read_text(encoding="utf-8")
    assert _staging_files(repo) == []


def test_cli_docs_sync_interruption_before_any_write_exits_two(tmp_path, monkeypatch, capsys):
    """A docs sync interrupted on the very first staging write changes
    nothing — the gate (repo must be pinned) already passed, so this
    exercises the write phase itself on a live-repo copy."""
    repo = copy_repo(tmp_path / "repo")
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "SPEC.md").write_text(f"The model server's default port is {PORT}.\n")
    assert main(["apply-port-pin", str(PORT), str(repo), "--scan-root", str(scan)]) == 0
    before = _doc_map(repo)
    _fail_fsync_after(0, monkeypatch)
    code = main(["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(scan)])
    assert code == 2
    capsys.readouterr()
    assert _doc_map(repo) == before
    assert _staging_files(repo) == []
