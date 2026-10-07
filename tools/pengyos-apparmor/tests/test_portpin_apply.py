"""Tests for the apply side of the port-pin transition
(``portpin.compute_transition`` / ``portpin.apply_transition`` + the
``apply-port-pin`` CLI subcommand).

The port-pin item is blocked until PengyOS defines the model server's
default port upstream (the port must never be guessed). Everything
before this file was read-side or dry-run-side: scan upstream
(``find-model-server-port``), plan and render the patch
(``plan-port-pin --patch``), and probe the state (``port-pin-status``).
This file pins the *apply side*: one command that writes the four
transition files in place, gated so the requested port must carry
upstream evidence (the scan found it as a candidate) — otherwise
nothing is written and the command exits 3. After a successful apply
the state re-probe must report ``pinned`` and the profile must be
strict-clean.

All checks run on ``tmp_path`` fixture repos and fixture scan roots —
no network, no root, no AppArmor tooling. One anchor test runs against
the *live* repo and *live* workspace: today it pins exit 3 (no
upstream definition, nothing modified); it is the visible signal that
the gate refuses to guess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_apparmor import portdiscovery, portpin, portstatus, validator
from pengyos_apparmor.cli import main

TOOL_ROOT = portdiscovery.TOOL_ROOT
UNBOUND = portpin.UNBOUND_MODEL_SERVER_RULE

PORT = 8080


# ---------------------------------------------------------------------------
# Module contract / lockstep
# ---------------------------------------------------------------------------


def test_portpin_public_api_includes_the_apply_side():
    for attr in (
        "TransitionFileEdit",
        "POST_APPLY_STEPS",
        "compute_transition",
        "apply_transition",
        "render_transition_patch",
    ):
        assert hasattr(portpin, attr), f"portpin.{attr} missing"


def test_post_apply_steps_cover_the_recipe():
    # Applying the diff is not the end: the reviewed prose docs, the
    # full suite, and flipping the checklist item still must ship.
    steps = "\n".join(portpin.POST_APPLY_STEPS)
    assert "CHANGELOG" in steps
    assert "validate --strict" in steps
    assert "TODO.md" in steps
    # The "apply the rendered unified diff" step belongs to the dry-run
    # recipe, not the post-apply one (the diff is already applied).
    assert "git apply" not in steps


# ---------------------------------------------------------------------------
# Fixture repo builder — the exact transition anchor lines
# ---------------------------------------------------------------------------


def _write_file(root: Path, rel: str, *lines: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _profile_lines(grant: str = UNBOUND + ",", extra: list[str] | None = None) -> list[str]:
    lines = [
        "pengy-agent (pengy-agent.bin) {",
        f"  {grant}",
        "}",
    ]
    if extra:
        # Extra rule lines go before the closing brace.
        lines = lines[:2] + [f"  {rule}" for rule in extra] + lines[2:]
    return lines


def _validator_lines(entry: str) -> list[str]:
    return [
        "SANCTIONED_NETWORK_RULES = frozenset({",
        f"    {entry}",
        "})",
    ]


def _test_profiles_lines(rule: str) -> list[str]:
    return [f'    expected = {{"pengy-agent": ["{rule}"]}}']


def _test_validator_lines(port: int | None) -> list[str]:
    if port is None:
        return ['        rule = _model_server_rule()', '        assert "port=" not in rule']
    return [
        f'        rule = _model_server_rule("{port}")',
        f'        assert f"port={port}" in rule',
    ]


def build_repo(
    root: Path,
    port: int = PORT,
    profile_grant: str | None = None,
    validator_entry: str | None = None,
    test_pin_rule: str | None = None,
    test_validator_port: int | None = None,
    extra_profile_rules: list[str] | None = None,
) -> Path:
    """Write the four transition files under *root* in the pre-transition
    (unbound) form unless a specific part is overridden. Returns *root*."""
    _write_file(
        root,
        "profiles/pengy-agent",
        *_profile_lines(
            profile_grant if profile_grant is not None else UNBOUND + ",",
            extra_profile_rules,
        ),
    )
    _write_file(
        root,
        "pengyos_apparmor/validator.py",
        *_validator_lines(validator_entry if validator_entry is not None else f'"{UNBOUND}",'),
    )
    _write_file(
        root,
        "tests/test_profiles.py",
        *_test_profiles_lines(test_pin_rule if test_pin_rule is not None else UNBOUND),
    )
    _write_file(
        root,
        "tests/test_validator.py",
        *_test_validator_lines(test_validator_port),
    )
    return root


@pytest.fixture
def upstream_scan(tmp_path) -> Path:
    """A fixture scan root whose SPEC.md defines PORT (8080)."""
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "SPEC.md").write_text(
        f"The model server's default port is {PORT}.\n", encoding="utf-8"
    )
    return scan


def _file_map(repo: Path) -> dict[str, bytes]:
    return {rel: (repo / rel).read_bytes() for rel in portpin.PORT_PIN_TRANSITION_FILES}


# ---------------------------------------------------------------------------
# compute_transition (pure)
# ---------------------------------------------------------------------------


def test_compute_returns_one_edit_per_transition_file_in_order(tmp_path):
    build_repo(tmp_path / "repo")
    before = _file_map(tmp_path / "repo")
    edits = portpin.compute_transition(PORT, tmp_path / "repo")
    assert [e.rel for e in edits] == list(portpin.PORT_PIN_TRANSITION_FILES)
    for e in edits:
        assert e.new != e.old
        assert e.old == before[e.rel].decode("utf-8")
    # Pure: nothing was written.
    assert _file_map(tmp_path / "repo") == before


def test_compute_profile_edit_pins_and_sanctions_and_is_strict_clean(tmp_path):
    build_repo(tmp_path / "repo")
    edit = portpin.compute_transition(PORT, tmp_path / "repo")[0]
    assert edit.rel == "profiles/pengy-agent"
    new_lines = [l.strip() for l in edit.new.splitlines()]
    pinned = portpin.port_pinned_rule(PORT)
    assert pinned + "," in new_lines
    assert validator.SANCTION_DIRECTIVE + pinned in new_lines
    # The unbound grant is gone; the directive sits directly above the rule.
    assert UNBOUND + "," not in new_lines
    i = new_lines.index(pinned + ",")
    assert new_lines[i - 1] == validator.SANCTION_DIRECTIVE + pinned
    report = validator.validate_text(edit.new, "pengy-agent")
    assert report.errors == [] and report.warnings == []


def test_compute_locksteps_with_render_transition_patch(tmp_path):
    # The refactor: render_transition_patch is a diff over
    # compute_transition's edits — the same anchors, the same output.
    build_repo(tmp_path / "repo")
    edits = portpin.compute_transition(PORT, tmp_path / "repo")
    patch = portpin.render_transition_patch(PORT, tmp_path / "repo")
    for e in edits:
        assert f"+++ b/{e.rel}" in patch
    added = [l for e in edits for l in e.new.splitlines() if l not in e.old.splitlines()]
    for line in added:
        assert f"+{line}" in patch, f"added line missing from the patch: {line!r}"


def test_compute_missing_file_raises(tmp_path):
    build_repo(tmp_path / "repo")
    (tmp_path / "repo" / "tests" / "test_profiles.py").unlink()
    with pytest.raises(portpin.PortPinTransitionError, match="tests/test_profiles.py"):
        portpin.compute_transition(PORT, tmp_path / "repo")


def test_compute_drifted_anchor_raises_with_file_label(tmp_path):
    # A duplicated lockstep entry is drift: the mechanical edit must not
    # guess which one to replace.
    build_repo(
        tmp_path / "repo",
        validator_entry=f'"{UNBOUND}", "{UNBOUND}",',
    )
    with pytest.raises(
        portpin.PortPinTransitionError, match="pengyos_apparmor/validator.py"
    ):
        portpin.compute_transition(PORT, tmp_path / "repo")


def test_compute_profile_without_the_unbound_grant_raises(tmp_path):
    build_repo(tmp_path / "repo", profile_grant="/etc/hosts r,")
    with pytest.raises(portpin.PortPinTransitionError, match="does not carry the unbound"):
        portpin.compute_transition(PORT, tmp_path / "repo")


def test_compute_already_pinned_profile_raises(tmp_path):
    build_repo(tmp_path / "repo", profile_grant=portpin.port_pinned_rule(PORT) + ",")
    with pytest.raises(portpin.PortPinTransitionError, match="already pins a port"):
        portpin.compute_transition(PORT, tmp_path / "repo")


# ---------------------------------------------------------------------------
# apply_transition (in place)
# ---------------------------------------------------------------------------


def test_apply_writes_all_four_files_to_the_post_transition_form(tmp_path):
    build_repo(tmp_path / "repo")
    edits = portpin.apply_transition(PORT, tmp_path / "repo")
    assert [e.rel for e in edits] == list(portpin.PORT_PIN_TRANSITION_FILES)
    for e in edits:
        assert (tmp_path / "repo" / e.rel).read_text(encoding="utf-8") == e.new


def test_apply_result_is_pinned_state(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    build_repo(tmp_path / "repo")
    portpin.apply_transition(PORT, tmp_path / "repo")
    status = portstatus.inspect_transition(tmp_path / "repo", scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.port == PORT
    assert all(f.form == portstatus.FORM_PINNED for f in status.files)


def test_apply_is_one_way_a_double_transition_cannot_stack(tmp_path):
    build_repo(tmp_path / "repo")
    portpin.apply_transition(PORT, tmp_path / "repo")
    with pytest.raises(portpin.PortPinTransitionError, match="already pins a port"):
        portpin.apply_transition(PORT, tmp_path / "repo")
    # And re-applying a *different* port must fail the same way.
    with pytest.raises(portpin.PortPinTransitionError, match="already pins a port"):
        portpin.apply_transition(9001, tmp_path / "repo")


def test_apply_refuses_to_write_when_the_profile_is_not_strict_clean(tmp_path):
    # An over-broad tree write in the profile (an error + a warning) must
    # block the write: apply validates the transformed profile before
    # touching any file.
    build_repo(tmp_path / "repo", extra_profile_rules=["/srv/demo/** rw,"])
    before = _file_map(tmp_path / "repo")
    with pytest.raises(portpin.PortPinTransitionError, match="not strict-clean"):
        portpin.apply_transition(PORT, tmp_path / "repo")
    assert _file_map(tmp_path / "repo") == before


# ---------------------------------------------------------------------------
# CLI: apply-port-pin
# ---------------------------------------------------------------------------


def test_cli_applies_when_upstream_defines_the_port(tmp_path, upstream_scan, capsys):
    build_repo(tmp_path / "repo")
    code = main(
        [
            "apply-port-pin",
            str(PORT),
            str(tmp_path / "repo"),
            "--scan-root",
            str(upstream_scan),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert f"port-pin transition applied: port {PORT} pinned in 4 files" in out
    assert f"state re-probe: pinned:{PORT}" in out
    assert "upstream evidence:" in out
    # The remaining reviewed steps (docs, suite, checklist) are printed.
    for step in portpin.POST_APPLY_STEPS:
        assert step in out
    status = portstatus.inspect_transition(tmp_path / "repo", upstream_scan)
    assert status.state == portstatus.STATE_PINNED and status.port == PORT


def test_cli_refuses_a_port_with_no_upstream_evidence(tmp_path, capsys):
    scan = tmp_path / "scan"
    scan.mkdir()
    build_repo(tmp_path / "repo")
    before = _file_map(tmp_path / "repo")
    code = main(
        ["apply-port-pin", str(PORT), str(tmp_path / "repo"), "--scan-root", str(scan)]
    )
    assert code == 3
    out = capsys.readouterr().out
    assert "refusing to pin" in out
    assert _file_map(tmp_path / "repo") == before


def test_cli_refuses_when_the_evidence_is_a_different_port(tmp_path, capsys):
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "SPEC.md").write_text("The model server's default port is 9001.\n")
    build_repo(tmp_path / "repo")
    before = _file_map(tmp_path / "repo")
    code = main(
        ["apply-port-pin", str(PORT), str(tmp_path / "repo"), "--scan-root", str(scan)]
    )
    assert code == 3
    out = capsys.readouterr().out
    assert "9001" in out
    assert "refusing to pin" in out
    assert _file_map(tmp_path / "repo") == before


def test_cli_allows_the_requested_port_among_multiple_candidates(tmp_path, capsys):
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "SPEC.md").write_text(
        f"The model server's default port is {PORT}.\n"
        "The model server's fallback port is 9001.\n",
        encoding="utf-8",
    )
    build_repo(tmp_path / "repo")
    code = main(
        ["apply-port-pin", str(PORT), str(tmp_path / "repo"), "--scan-root", str(scan)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "2 distinct candidate ports" in out
    assert f"pinned:{PORT}" in out


def test_cli_dry_run_writes_nothing(tmp_path, upstream_scan, capsys):
    build_repo(tmp_path / "repo")
    before = _file_map(tmp_path / "repo")
    code = main(
        [
            "apply-port-pin",
            str(PORT),
            str(tmp_path / "repo"),
            "--scan-root",
            str(upstream_scan),
            "--dry-run",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "dry run — no files were modified" in out
    assert _file_map(tmp_path / "repo") == before


def test_cli_bad_repo_root_exits_two(tmp_path, upstream_scan, capsys):
    code = main(
        [
            "apply-port-pin",
            str(PORT),
            str(tmp_path / "nope"),
            "--scan-root",
            str(upstream_scan),
        ]
    )
    assert code == 2


def test_cli_bad_scan_root_exits_two(tmp_path, capsys):
    build_repo(tmp_path / "repo")
    code = main(
        ["apply-port-pin", str(PORT), str(tmp_path / "repo"), "--scan-root", str(tmp_path / "missing")]
    )
    assert code == 2


def test_cli_invalid_port_exits_two(tmp_path, upstream_scan):
    build_repo(tmp_path / "repo")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "apply-port-pin",
                "0",
                str(tmp_path / "repo"),
                "--scan-root",
                str(upstream_scan),
            ]
        )
    assert exc.value.code == 2


def test_cli_drifted_repo_exits_two_and_writes_nothing(tmp_path, upstream_scan, capsys):
    build_repo(tmp_path / "repo", validator_entry=f'"{UNBOUND}", "{UNBOUND}",')
    before = _file_map(tmp_path / "repo")
    code = main(
        ["apply-port-pin", str(PORT), str(tmp_path / "repo"), "--scan-root", str(upstream_scan)]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "pengyos_apparmor/validator.py" in err
    assert _file_map(tmp_path / "repo") == before


def test_cli_then_port_pin_status_reports_pinned(tmp_path, upstream_scan, capsys):
    build_repo(tmp_path / "repo")
    assert (
        main(
            [
                "apply-port-pin",
                str(PORT),
                str(tmp_path / "repo"),
                "--scan-root",
                str(upstream_scan),
            ]
        )
        == 0
    )
    capsys.readouterr()
    code = main(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(upstream_scan)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert f"PINNED to port {PORT}" in out


def test_cli_then_validate_strict_passes_on_the_pinned_profile(tmp_path, upstream_scan):
    build_repo(tmp_path / "repo")
    assert (
        main(
            [
                "apply-port-pin",
                str(PORT),
                str(tmp_path / "repo"),
                "--scan-root",
                str(upstream_scan),
            ]
        )
        == 0
    )
    assert (
        main(["validate", "--strict", str(tmp_path / "repo" / "profiles")]) == 0
    )


# ---------------------------------------------------------------------------
# Anchor: the live repo + live workspace (the never-guess gate, end to end)
# ---------------------------------------------------------------------------


def test_cli_live_workspace_stays_blocked_and_writes_nothing(capsys):
    """Against the *live* repo and the *live* PengyOS workspace the gate
    must refuse: the port is not defined upstream (verified 2026-09-16,
    7th check — the find-model-server-port scan exits 3). Nothing is
    written. If the live workspace ever defines port 8080 for the model
    server, this test goes red and tells you the gate no longer needs
    re-verifying by hand — apply the transition with the port the scan
    reports."""
    before = _file_map(TOOL_ROOT)
    code = main(
        ["apply-port-pin", str(PORT), str(TOOL_ROOT), "--scan-root", str(portdiscovery.DEFAULT_SCAN_ROOT)]
    )
    capsys.readouterr()
    assert code == 3
    assert _file_map(TOOL_ROOT) == before


def test_cli_on_the_live_repo_with_a_fixture_upstream_definition(tmp_path):
    # The inverse of the anchor: with a fixture scan root that DOES
    # define 8080, the live repo is still refused because the live
    # workspace is the default scan root only — this pins that an
    # explicit --scan-root is what carries the evidence (a fake scan
    # root is upstream evidence for the command, but the repo files on
    # disk must stay pristine for this test).
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "SPEC.md").write_text(f"The model server's default port is {PORT}.\n")
    before = _file_map(TOOL_ROOT)
    # Dry run: computes the full transition against the LIVE repo (all
    # four anchors must still match the recipe), writes nothing.
    code = main(
        [
            "apply-port-pin",
            str(PORT),
            str(TOOL_ROOT),
            "--scan-root",
            str(scan),
            "--dry-run",
        ]
    )
    assert code == 0
    assert _file_map(TOOL_ROOT) == before
