"""Tests for the post-apply docs sync of the port-pin transition
(``portdocsync`` + the ``sync-port-pin-docs`` CLI subcommand).

The port-pin item is blocked until PengyOS defines the model server's
default port upstream (the port must never be guessed). Everything
before this file is mechanical: scan upstream (``find-model-server-port``),
plan/render the patch (``plan-port-pin --patch``), probe the state
(``port-pin-status``), and apply the four transition files in place
(``apply-port-pin``). This file pins the *docs side* of the first
``portpin.POST_APPLY_STEPS`` step: once the repo is ``pinned:<N>``, the
four prose doc surfaces (the profiles/pengy-agent network comment block,
the README profile row, the spec.md pinned-grant paragraph, and the
CHANGELOG entry) still describe the unbound state — ``sync-port-pin-docs``
rewrites them to the pinned form.

Contract pinned here:

* per-surface **editable / synced / drifted** detection by exact
  pre-sync anchors and pinned markers — idempotent (a re-run is a
  no-op) and all-or-nothing (one drifted surface → nothing written);
* the gate: the repo must already be ``pinned:<port>`` (a different
  port, or the unbound/partial state, exits 1 and writes nothing);
* the edited profile comment is re-checked for strict-clean;
* ``--dry-run`` prints the diff and writes nothing;
* exit codes: 0 synced/already-in-sync, 1 not pinned, 2 bad input or
  drift.

All checks run on ``tmp_path`` fixture repos (copies of the live repo —
the docs anchors ARE the live docs) and fixture scan roots: no network,
no root, no AppArmor tooling. One anchor test runs against the *live*
repo: today it pins exit 1 (the transition is not applied) with the
docs byte-identical.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pengyos_apparmor import portdiscovery, portdocsync, portpin, portstatus, validator
from pengyos_apparmor.cli import main

TOOL_ROOT = portdiscovery.TOOL_ROOT
UNBOUND = portpin.UNBOUND_MODEL_SERVER_RULE

PORT = 8080
OTHER_PORT = 9001


# ---------------------------------------------------------------------------
# Fixture builders
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


def copy_repo(dest: Path) -> Path:
    """Copy the live repo (the docs anchors are the live docs) into
    *dest*, skipping ``.git`` and cache dirs. Returns *dest*."""
    def ignore(_dir, names):
        return [n for n in names if n in (".git", ".pytest_cache", "__pycache__")]

    shutil.copytree(TOOL_ROOT, dest, ignore=ignore, symlinks=True)
    return dest


def apply_pin(repo: Path, scan: Path, port: int = PORT) -> None:
    """Run the mechanical transition in *repo* (must exit 0)."""
    code = main(
        ["apply-port-pin", str(port), str(repo), "--scan-root", str(scan)]
    )
    assert code == 0, f"apply-port-pin {port} failed on {repo}"


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        rel: (root / rel).read_bytes()
        for rel in portdocsync.PORT_PIN_DOC_FILES
    }


# ---------------------------------------------------------------------------
# Module contract / lockstep
# ---------------------------------------------------------------------------


def test_public_api():
    for attr in (
        "PORT_PIN_DOC_FILES",
        "DOC_FILE_EDITABLE",
        "DOC_FILE_SYNCED",
        "DocSyncError",
        "RepoNotPinnedError",
        "DocFileEdit",
        "check_transition_port",
        "compute_doc_edits",
        "sync_docs",
        "render_doc_diff",
    ):
        assert hasattr(portdocsync, attr), f"portdocsync.{attr} missing"
    assert list(portdocsync.PORT_PIN_DOC_FILES) == [
        "profiles/pengy-agent",
        "README.md",
        "spec.md",
        "CHANGELOG.md",
    ]
    # The doc files are deliberately NOT the mechanical transition
    # files — except the profile itself, which is the one surface the
    # transition (grant line + local sanction) and the docs sync
    # (comment block) both edit.
    assert set(portdocsync.PORT_PIN_DOC_FILES) & set(
        portpin.PORT_PIN_TRANSITION_FILES
    ) == {"profiles/pengy-agent"}


def test_live_docs_carry_the_pre_sync_anchors():
    # The sync recipe is pinned to the *live* docs: every surface must be
    # editable against the committed repo as it stands (unbound state).
    for rel in portdocsync.PORT_PIN_DOC_FILES:
        text = (TOOL_ROOT / rel).read_text(encoding="utf-8")
        new, status = portdocsync._DOC_TRANSFORMS[rel](text, PORT)
        assert status == portdocsync.DOC_FILE_EDITABLE, (
            f"{rel} is not editable against the live docs — the recipe "
            "anchors have drifted from the committed prose"
        )
        assert new != text


# ---------------------------------------------------------------------------
# Per-surface transforms (pure, unit level)
# ---------------------------------------------------------------------------


def test_profile_transform_edits_the_comment_block_only():
    text = (TOOL_ROOT / "profiles/pengy-agent").read_text(encoding="utf-8")
    new, status = portdocsync._transform_profile(text, PORT)
    assert status == portdocsync.DOC_FILE_EDITABLE
    assert f"# pinned to {PORT}" in new
    assert "deliberately unbound" not in new
    # The grant line itself is untouched (that is the mechanical
    # transition's job, not the docs sync's).
    assert text.count(UNBOUND + ",") == new.count(UNBOUND + ",")
    # Re-strict-clean: a comment edit must not break the profile.
    report = validator.validate_text(new, "pengy-agent")
    assert report.errors == [] and report.warnings == []


def test_profile_transform_is_idempotent_on_the_post_form():
    text = (TOOL_ROOT / "profiles/pengy-agent").read_text(encoding="utf-8")
    new, _ = portdocsync._transform_profile(text, PORT)
    again, status = portdocsync._transform_profile(new, PORT)
    assert status == portdocsync.DOC_FILE_SYNCED
    assert again == new


def test_profile_transform_drifts_on_missing_and_doubled_block():
    text = (TOOL_ROOT / "profiles/pengy-agent").read_text(encoding="utf-8")
    # Missing: the pre block is hand-edited away.
    missing = text.replace(
        "  # deliberately unbound: the model server's port is a deployment choice\n",
        ""
    )
    with pytest.raises(portdocsync.DocSyncError, match="profiles/pengy-agent"):
        portdocsync._transform_profile(missing, PORT)
    # Doubled: two unbound blocks is ambiguity a mechanical edit must
    # not guess through (the pre block is the 4 lines ending at the
    # "(including ::1 …") line).
    lines = text.splitlines(keepends=True)
    at = next(
        i
        for i, l in enumerate(lines)
        if "(including ::1, which the model server does not bind) stays denied." in l
    )
    block = lines[at - 3 : at + 1]
    doubled = "".join(lines[: at - 3] + block + block + lines[at + 1 :])
    with pytest.raises(portdocsync.DocSyncError, match="occurs 2 times"):
        portdocsync._transform_profile(doubled, PORT)


def test_readme_transform_replaces_the_profile_row_anchor():
    text = (TOOL_ROOT / "README.md").read_text(encoding="utf-8")
    new, status = portdocsync._transform_readme(text, PORT)
    assert status == portdocsync.DOC_FILE_EDITABLE
    assert f"`peer=(ip=127.0.0.1 port={PORT})`, pure client" in new
    assert "`peer=(ip=127.0.0.1)`, pure client" not in new
    again, synced = portdocsync._transform_readme(new, PORT)
    assert synced == portdocsync.DOC_FILE_SYNCED and again == new


def test_readme_transform_drifts():
    text = (TOOL_ROOT / "README.md").read_text(encoding="utf-8")
    with pytest.raises(portdocsync.DocSyncError, match="README.md"):
        portdocsync._transform_readme(text.replace(portdocsync.README_PRE_SUB, ""), PORT)
    doubled = text.replace(
        portdocsync.README_PRE_SUB, portdocsync.README_PRE_SUB + "\n" + portdocsync.README_PRE_SUB
    )
    with pytest.raises(portdocsync.DocSyncError, match="README.md"):
        portdocsync._transform_readme(doubled, PORT)


def test_spec_transform_replaces_the_pinned_grant_paragraph():
    text = (TOOL_ROOT / "spec.md").read_text(encoding="utf-8")
    new, status = portdocsync._transform_spec(text, PORT)
    assert status == portdocsync.DOC_FILE_EDITABLE
    assert f"`network inet tcp peer=(ip=127.0.0.1 port={PORT}),`" in new
    assert "the port is deliberately" not in new
    again, synced = portdocsync._transform_spec(new, PORT)
    assert synced == portdocsync.DOC_FILE_SYNCED and again == new
    with pytest.raises(portdocsync.DocSyncError, match="spec.md"):
        portdocsync._transform_spec(
            text.replace("TODO.md follow-up). Every other profile is socket-free.", ""),
            PORT,
        )


def test_changelog_transform_inserts_first_entry_under_unreleased_added():
    text = (TOOL_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    new, status = portdocsync._transform_changelog(text, PORT)
    assert status == portdocsync.DOC_FILE_EDITABLE
    marker = portdocsync._changelog_marker(PORT)
    assert new.count(marker) == 1
    # The new entry is the FIRST bullet of the Unreleased '### Added'
    # section (newest-first, Keep a Changelog style).
    added = new.index("### Added")
    next_line = new[new.index("\n", added) + 1 :]
    assert next_line.startswith(marker)
    # Released sections below are untouched.
    assert "## [0.1.0] — 2026-09-16" in new
    again, synced = portdocsync._transform_changelog(new, PORT)
    assert synced == portdocsync.DOC_FILE_SYNCED and again == new


def test_changelog_transform_drifts_without_unreleased_or_added():
    text = (TOOL_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    with pytest.raises(portdocsync.DocSyncError, match="CHANGELOG.md"):
        portdocsync._transform_changelog(text.replace("## [Unreleased]", "## [0.2.0]"), PORT)
    with pytest.raises(portdocsync.DocSyncError, match="CHANGELOG.md"):
        portdocsync._transform_changelog(
            text.replace("### Added", "### Changed", 1), PORT
        )


# ---------------------------------------------------------------------------
# check_transition_port — the gate
# ---------------------------------------------------------------------------


def test_gate_refuses_an_unbound_repo(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    with pytest.raises(portdocsync.RepoNotPinnedError, match="apply-port-pin"):
        portdocsync.check_transition_port(repo, upstream_scan, PORT)


def test_gate_refuses_a_different_port(tmp_path, upstream_scan):
    scan_other = tmp_path / "scan_other"
    scan_other.mkdir()
    (scan_other / "SPEC.md").write_text(
        f"The model server's default port is {OTHER_PORT}.\n", encoding="utf-8"
    )
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, scan_other, OTHER_PORT)
    with pytest.raises(portdocsync.RepoNotPinnedError, match=f"port {PORT}"):
        portdocsync.check_transition_port(repo, upstream_scan, PORT)


def test_gate_refuses_a_partial_repo(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    # Undo one mechanical file → the probe reports 'partial'.
    (repo / "pengyos_apparmor/validator.py").write_text(
        (TOOL_ROOT / "pengyos_apparmor/validator.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    status = portstatus.inspect_transition(repo, upstream_scan)
    assert status.state == portstatus.STATE_PARTIAL
    with pytest.raises(portdocsync.RepoNotPinnedError, match="partial"):
        portdocsync.check_transition_port(repo, upstream_scan, PORT)


def test_gate_passes_on_a_pinned_repo(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    status = portdocsync.check_transition_port(repo, upstream_scan, PORT)
    assert status.state == portstatus.STATE_PINNED and status.port == PORT


# ---------------------------------------------------------------------------
# compute_doc_edits / sync_docs
# ---------------------------------------------------------------------------


def test_compute_is_pure_and_returns_one_edit_per_doc_file(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    before = _file_map(repo)
    edits = portdocsync.compute_doc_edits(PORT, repo, upstream_scan)
    assert [e.rel for e in edits] == list(portdocsync.PORT_PIN_DOC_FILES)
    for e in edits:
        assert e.status == portdocsync.DOC_FILE_EDITABLE
        assert e.new != e.old
    assert _file_map(repo) == before  # nothing written


def test_sync_updates_all_four_surfaces_and_stays_strict_clean(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    portdocsync.sync_docs(PORT, repo, upstream_scan)
    profile = (repo / "profiles/pengy-agent").read_text(encoding="utf-8")
    assert f"# pinned to {PORT}" in profile
    assert "deliberately unbound" not in profile
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert f"`peer=(ip=127.0.0.1 port={PORT})`, pure client" in readme
    spec = (repo / "spec.md").read_text(encoding="utf-8")
    assert f"port is the model" in spec
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.count(portdocsync._changelog_marker(PORT)) == 1
    # The synced profile comment keeps the profile strict-clean.
    report = validator.validate_text(profile, "pengy-agent")
    assert report.errors == [] and report.warnings == []
    # The unbound prose is gone from every surface.
    assert "`peer=(ip=127.0.0.1)`, pure client" not in readme


def test_sync_is_idempotent_a_second_run_writes_nothing(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    portdocsync.sync_docs(PORT, repo, upstream_scan)
    after_first = _file_map(repo)
    edits = portdocsync.sync_docs(PORT, repo, upstream_scan)
    assert all(
        e.status == portdocsync.DOC_FILE_SYNCED for e in edits
    )
    assert _file_map(repo) == after_first


def test_sync_drift_on_one_surface_writes_nothing(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    # Drift the spec.md anchor (a hand edit).
    spec = (repo / "spec.md").read_text(encoding="utf-8")
    (repo / "spec.md").write_text(
        spec.replace("TODO.md follow-up). Every other profile is socket-free.", ""),
        encoding="utf-8",
    )
    before = _file_map(repo)
    with pytest.raises(portdocsync.DocSyncError, match="spec.md"):
        portdocsync.sync_docs(PORT, repo, upstream_scan)
    assert _file_map(repo) == before  # all-or-nothing


def test_sync_missing_doc_file_raises(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    (repo / "README.md").unlink()
    with pytest.raises(portdocsync.DocSyncError, match="README.md"):
        portdocsync.compute_doc_edits(PORT, repo, upstream_scan)


def test_sync_rejects_a_port_outside_range(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    with pytest.raises(portpin.PortPinTransitionError):
        portdocsync.compute_doc_edits(0, repo, upstream_scan)


# ---------------------------------------------------------------------------
# render_doc_diff
# ---------------------------------------------------------------------------


def test_render_doc_diff_shows_only_the_edited_surfaces(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    edits = portdocsync.compute_doc_edits(PORT, repo, upstream_scan)
    diff = portdocsync.render_doc_diff(edits)
    for e in edits:
        assert f"+++ b/{e.rel}" in diff
    # After a full sync, there is nothing left to diff.
    portdocsync.sync_docs(PORT, repo, upstream_scan)
    edits2 = portdocsync.compute_doc_edits(PORT, repo, upstream_scan)
    assert portdocsync.render_doc_diff(edits2) == ""


# ---------------------------------------------------------------------------
# CLI: sync-port-pin-docs
# ---------------------------------------------------------------------------


def test_cli_syncs_after_apply(tmp_path, upstream_scan, capsys):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    capsys.readouterr()
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "upstream evidence:" in out
    for rel in portdocsync.PORT_PIN_DOC_FILES:
        assert f"{rel}: editable" in out
    assert f"docs synced for port {PORT}: 4 file(s) updated" in out
    # The remaining reviewed steps are the post-docs ones (the docs step
    # itself is no longer remaining).
    assert "update the docs" not in out
    for step in portpin.POST_APPLY_STEPS[1:]:
        assert step in out


def test_cli_rerun_is_a_noop(tmp_path, upstream_scan, capsys):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    capsys.readouterr()
    assert (
        main(
            ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
        )
        == 0
    )
    after = _file_map(repo)
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 0
    out = capsys.readouterr().out
    for rel in portdocsync.PORT_PIN_DOC_FILES:
        assert f"{rel}: synced" in out
    assert "docs already in sync for port" in out
    assert _file_map(repo) == after


def test_cli_refuses_when_the_repo_is_not_pinned(tmp_path, upstream_scan, capsys):
    repo = copy_repo(tmp_path / "repo")
    before = _file_map(repo)
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert f"not pinned to port {PORT}" in err
    assert "apply-port-pin" in err
    assert _file_map(repo) == before


def test_cli_refuses_a_port_the_repo_does_not_pin(tmp_path, upstream_scan, capsys):
    scan_other = tmp_path / "scan_other"
    scan_other.mkdir()
    (scan_other / "SPEC.md").write_text(
        f"The model server's default port is {OTHER_PORT}.\n", encoding="utf-8"
    )
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, scan_other, OTHER_PORT)
    before = _file_map(repo)
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 1
    assert _file_map(repo) == before


def test_cli_reports_evidence_for_the_pinned_port_and_notes_a_mismatch(
    tmp_path, capsys
):
    scan_other = tmp_path / "scan_other"
    scan_other.mkdir()
    (scan_other / "SPEC.md").write_text(
        f"The model server's default port is {OTHER_PORT}.\n", encoding="utf-8"
    )
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, scan_other, OTHER_PORT)
    # Evidence matches the pinned port:
    capsys.readouterr()
    code = main(
        ["sync-port-pin-docs", str(OTHER_PORT), str(repo), "--scan-root", str(scan_other)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert f"upstream evidence: SPEC.md:1: port {OTHER_PORT}" in out
    # Evidence for a *different* port: the sync still proceeds (the repo
    # pin stands) but notes the mismatch.
    scan_third = tmp_path / "scan_third"
    scan_third.mkdir()
    (scan_third / "SPEC.md").write_text("The model server's default port is 9999.\n")
    capsys.readouterr()
    code = main(
        ["sync-port-pin-docs", str(OTHER_PORT), str(repo), "--scan-root", str(scan_third)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "not the pinned port" in out


def test_cli_dry_run_prints_the_diff_and_writes_nothing(tmp_path, upstream_scan, capsys):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    before = _file_map(repo)
    code = main(
        [
            "sync-port-pin-docs",
            str(PORT),
            str(repo),
            "--scan-root",
            str(upstream_scan),
            "--dry-run",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "docs diff (dry run):" in out
    assert f"+++ b/profiles/pengy-agent" in out
    assert "dry run — no files were modified" in out
    assert _file_map(repo) == before


def test_cli_drifted_docs_exit_two_and_write_nothing(tmp_path, upstream_scan, capsys):
    repo = copy_repo(tmp_path / "repo")
    apply_pin(repo, upstream_scan, PORT)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    (repo / "README.md").write_text(
        readme.replace(portdocsync.README_PRE_SUB, ""), encoding="utf-8"
    )
    before = _file_map(repo)
    code = main(
        ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    )
    assert code == 2
    assert "README.md" in capsys.readouterr().err
    assert _file_map(repo) == before


def test_cli_bad_roots_and_port_exit_two(tmp_path, upstream_scan):
    repo = copy_repo(tmp_path / "repo")
    assert (
        main(
            ["sync-port-pin-docs", str(PORT), str(tmp_path / "nope"), "--scan-root", str(upstream_scan)]
        )
        == 2
    )
    assert (
        main(
            ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(tmp_path / "missing")]
        )
        == 2
    )
    with pytest.raises(SystemExit) as exc:
        main(
            ["sync-port-pin-docs", "0", str(repo), "--scan-root", str(upstream_scan)]
        )
    assert exc.value.code == 2
    # A missing transition file is bad input (exit 2), not a gate failure.
    (repo / "tests/test_profiles.py").unlink()
    assert (
        main(
            ["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
        )
        == 2
    )


# ---------------------------------------------------------------------------
# Anchor: the live repo (the transition is not applied yet)
# ---------------------------------------------------------------------------


def test_cli_on_the_live_repo_is_refused_and_writes_nothing(capsys):
    """Against the *live* repo the transition has not been applied
    (the port is still undefined upstream — re-verified 2026-09-17, 8th
    check): the docs sync must refuse (exit 1) and the live docs must
    stay byte-identical. Once the port exists and the transition is
    applied, this test goes red — at which point run the sync."""
    before = _file_map(TOOL_ROOT)
    code = main(
        [
            "sync-port-pin-docs",
            str(PORT),
            str(TOOL_ROOT),
            "--scan-root",
            str(portdiscovery.DEFAULT_SCAN_ROOT),
        ]
    )
    capsys.readouterr()
    assert code == 1
    assert _file_map(TOOL_ROOT) == before
