"""End-to-end acceptance tests for the port-pin transition.

Every earlier port-pin test file pins ONE piece of the machine in
isolation: scanning upstream (``portdiscovery``), planning/rendering the
patch (``portpin``), probing the state (``portstatus``), applying the four
transition files (``portpin`` apply side), and syncing the four prose doc
surfaces (``portdocsync``). Those pieces were built across separate
iterations and tested against small, purpose-built fixtures. Nothing, until
this file, asserted that the pieces *compose*: that one coherent run of the
real commands drives a real copy of the repo from the unbound ``ready``
state all the way to a fully-consistent, strict-clean, docs-synced
``pinned`` state — and that a half-broken composition (a piece whose output
no longer matches another piece's anchor) is caught.

This is the acceptance test for the whole feature. It is a *test group*,
not a new feature: no production code changes, only the composed invariants
pinned. It exercises the CLI end to end on a ``tmp_path`` copy of the live
repo (so the transition anchors AND the doc anchors are the real ones) with
a fixture scan root that defines the port — no network, no root, no
AppArmor tooling, and never a guessed port (the port always comes from the
fixture scan, and one test pins the gate refusing without it).

Terminal invariants asserted *simultaneously* after the full run:

* the probe reports ``pinned`` to the fixture port;
* ``validate --strict profiles/`` exits 0 (0 errors, 0 warnings);
* all four transition files carry the port-pinned form;
* all four prose doc surfaces are synced (a re-run is a no-op);
* the transition is one-way (a re-apply is refused).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pengyos_apparmor import portdiscovery, portdocsync, portpin, portstatus
from pengyos_apparmor.cli import main

TOOL_ROOT = portdiscovery.TOOL_ROOT

PORT = 8080
OTHER_PORT = 9001
PINNED_RULE = portpin.port_pinned_rule(PORT)


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


@pytest.fixture
def empty_scan(tmp_path) -> Path:
    """A scan root with no model-server port definition (gate refuses)."""
    scan = tmp_path / "empty-scan"
    scan.mkdir()
    (scan / "NOTES.md").write_text("no ports defined here\n", encoding="utf-8")
    return scan


def copy_repo(dest: Path) -> Path:
    """Copy the live repo (all transition + doc anchors are the real ones)
    into *dest*, skipping ``.git`` and cache dirs. Returns *dest*."""
    def ignore(_dir, names):
        return [n for n in names if n in (".git", ".pytest_cache", "__pycache__")]

    shutil.copytree(TOOL_ROOT, dest, ignore=ignore, symlinks=True)
    return dest


def _all_files(repo: Path) -> dict[str, bytes]:
    """The eight transition + doc files as a bytes map (deduplicated)."""
    rels = sorted(set(portpin.PORT_PIN_TRANSITION_FILES) | set(portdocsync.PORT_PIN_DOC_FILES))
    return {rel: (repo / rel).read_bytes() for rel in rels}


def _apply(repo: Path, scan: Path, port: int = PORT) -> None:
    assert main(["apply-port-pin", str(port), str(repo), "--scan-root", str(scan)]) == 0


def _sync(repo: Path, scan: Path, port: int = PORT) -> None:
    assert main(["sync-port-pin-docs", str(port), str(repo), "--scan-root", str(scan)]) == 0


def _probe(repo: Path, scan: Path) -> portstatus.PortPinStatus:
    return portstatus.inspect_transition(repo, scan)


def run_full_transition(repo: Path, scan: Path) -> None:
    """Drive the whole transition: apply the four files, then sync the docs."""
    _apply(repo, scan)
    _sync(repo, scan)


# ---------------------------------------------------------------------------
# Before the transition
# ---------------------------------------------------------------------------


def test_acceptance_scan_reports_ready_before_the_transition(tmp_path, upstream_scan):
    """With upstream evidence present but nothing applied, the repo is
    ``ready``: all transition files unbound AND candidates found."""
    repo = copy_repo(tmp_path / "repo")
    status = _probe(repo, upstream_scan)
    assert status.state == portstatus.STATE_READY
    assert status.port is None
    assert {f.form for f in status.files} == {portstatus.FORM_UNBOUND}
    assert len(status.candidates) == 1
    assert status.candidates[0].port == PORT


def test_acceptance_find_command_lists_the_fixture_port(tmp_path, upstream_scan, capsys):
    """The standalone scan command reports the fixture port as a candidate
    (exit 0), the human-review evidence the apply gate relies on."""
    code = main(["find-model-server-port", str(upstream_scan)])
    assert code == 0
    out = capsys.readouterr().out
    assert f"port {PORT}" in out


# ---------------------------------------------------------------------------
# The full transition: blocked -> (ready) -> pinned + docs synced + strict
# ---------------------------------------------------------------------------


def test_acceptance_full_transition_reaches_pinned_docs_synced_strict_clean(
    tmp_path, upstream_scan
):
    """One composed run drives a live-repo copy from unbound to the terminal
    state, and every terminal invariant holds at once."""
    repo = copy_repo(tmp_path / "repo")
    run_full_transition(repo, upstream_scan)

    # 1. the probe reports pinned to the fixture port
    status = _probe(repo, upstream_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.port == PORT
    assert all(f.form == portstatus.FORM_PINNED for f in status.files)

    # 2. the profile is strict-clean (0 errors, 0 warnings)
    assert main(["validate", "--strict", str(repo / "profiles")]) == 0

    # 3. every transition file carries the port-pinned form
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        text = (repo / rel).read_text(encoding="utf-8")
        assert f"port={PORT}" in text, f"{rel} does not carry the pinned port"
    # the profile carries both the grant and its local sanction
    profile = (repo / "profiles/pengy-agent").read_text(encoding="utf-8")
    assert f"network inet tcp peer=(ip=127.0.0.1 port={PORT})" in profile
    assert f"# sanctioned: {PINNED_RULE}" in profile

    # 4. the docs are synced: a re-run is a no-op that reports 'synced'
    after = _all_files(repo)
    out = main(["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)])
    assert out == 0
    assert _all_files(repo) == after


def test_acceptance_docs_surfaces_carry_the_pinned_port(tmp_path, upstream_scan):
    """After the full transition each of the four prose doc surfaces carries
    the pinned port in its rewritten region (not just 'changed bytes')."""
    repo = copy_repo(tmp_path / "repo")
    run_full_transition(repo, upstream_scan)
    for rel in portdocsync.PORT_PIN_DOC_FILES:
        text = (repo / rel).read_text(encoding="utf-8")
        assert f"{PORT}" in text, f"{rel} does not mention the pinned port"


def test_acceptance_full_transition_docs_sync_is_idempotent(tmp_path, upstream_scan):
    """A second docs sync after the full transition is a no-op: exit 0,
    'already in sync', every file byte-identical."""
    repo = copy_repo(tmp_path / "repo")
    run_full_transition(repo, upstream_scan)
    after = _all_files(repo)
    out = main(["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(upstream_scan)])
    assert out == 0
    assert _all_files(repo) == after


def test_acceptance_full_transition_is_one_way(tmp_path, upstream_scan, capsys):
    """Once the transition is complete, a re-apply (same or different port)
    is refused and writes nothing: a double transition cannot stack."""
    repo = copy_repo(tmp_path / "repo")
    run_full_transition(repo, upstream_scan)
    after = _all_files(repo)
    assert main(
        ["apply-port-pin", str(PORT), str(repo), "--scan-root", str(upstream_scan)]
    ) != 0
    assert main(
        ["apply-port-pin", str(OTHER_PORT), str(repo), "--scan-root", str(upstream_scan)]
    ) != 0
    assert _all_files(repo) == after


# ---------------------------------------------------------------------------
# The never-guess gate, end to end
# ---------------------------------------------------------------------------


def test_acceptance_gate_refuses_without_upstream_evidence_and_writes_nothing(
    tmp_path, empty_scan, capsys
):
    """With no upstream port definition the apply gate exits 3 and leaves
    every transition AND doc file byte-identical — the port is never
    invented, end to end through the whole command chain."""
    repo = copy_repo(tmp_path / "repo")
    before = _all_files(repo)
    code = main(["apply-port-pin", str(PORT), str(repo), "--scan-root", str(empty_scan)])
    assert code == 3
    assert _all_files(repo) == before

    # The docs sync is likewise refused: the repo is not pinned.
    code = main(["sync-port-pin-docs", str(PORT), str(repo), "--scan-root", str(empty_scan)])
    assert code == 1
    assert _all_files(repo) == before
