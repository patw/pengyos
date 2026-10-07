"""Tests for the port-pin transition-state probe
(``pengyos_apparmor.portstatus`` + the ``port-pin-status`` CLI).

The port-pin item is blocked until PengyOS defines the model server's
default port upstream (the port must never be guessed). Everything so
far was write-side (scan upstream, plan, render the patch); this file
pins the read side: the probe must classify the repo's transition state
— blocked / ready / pinned / partial — and, above all, *catch a
half-applied transition* (some files pinned, others not, or an anchor
that drifted), which nothing else reports until a full test run.

All state tests run on ``tmp_path`` fixture repos with minimal files
carrying the exact transition anchor lines; the end-to-end test
performs the real rendered patch on a copy of the committed repo and
re-probes the result. One anchor test runs against the *live* repo and
*live* workspace: today it pins ``blocked`` (exit 3); the first time
PengyOS defines the port (or the transition is applied), this test goes
red and tells you exactly which re-check to update. No network, no root.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pengyos_apparmor import portdiscovery, portpin, portstatus
from pengyos_apparmor.cli import main

TOOL_ROOT = portdiscovery.TOOL_ROOT
UNBOUND = portpin.UNBOUND_MODEL_SERVER_RULE  # network inet tcp peer=(ip=127.0.0.1)


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------


def test_portstatus_public_api():
    for attr in (
        "STATE_BLOCKED",
        "STATE_READY",
        "STATE_PARTIAL",
        "STATE_PINNED",
        "FORM_UNBOUND",
        "FORM_PINNED",
        "FORM_DRIFTED",
        "PortStatusError",
        "FileState",
        "PortPinStatus",
        "inspect_transition",
    ):
        assert hasattr(portstatus, attr), f"portstatus.{attr} missing"


def test_portstatus_states_and_forms_are_distinct_strings():
    states = {
        portstatus.STATE_BLOCKED,
        portstatus.STATE_READY,
        portstatus.STATE_PARTIAL,
        portstatus.STATE_PINNED,
    }
    forms = {portstatus.FORM_UNBOUND, portstatus.FORM_PINNED, portstatus.FORM_DRIFTED}
    assert len(states) == 4
    assert len(forms) == 3
    # "pinned" appears in both namespaces on purpose: it is a *state*
    # (the whole repo) and a *form* (one file carries the pinned anchor).
    assert states & forms == {portstatus.STATE_PINNED}


# ---------------------------------------------------------------------------
# Fixture repo builder — the exact anchor lines, one form per file
# ---------------------------------------------------------------------------


def _write_file(root: Path, rel: str, *lines: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _profile_lines(form: str, port: int = 8080) -> list[str]:
    if form == "unbound":
        return ["  " + UNBOUND + ","]
    if form == "pinned":
        return ["  " + portpin.port_pinned_rule(port) + ","]
    # drifted: hand-edited, not the planner's output
    if form == "range":
        return ["  network inet tcp peer=(ip=127.0.0.1 port=8000-8005),"]
    if form == "badport":
        return ["  network inet tcp peer=(ip=127.0.0.1 port=99999),"]
    if form == "missing":
        return ["  /etc/passwd r,"]
    if form == "both":
        return ["  " + UNBOUND + ",", "  " + portpin.port_pinned_rule(port) + ","]
    raise ValueError(form)


def _validator_lines(form: str, port: int = 8080) -> list[str]:
    base = ['SANCTIONED_NETWORK_RULES = frozenset({']
    if form == "unbound":
        entry = f'    "{UNBOUND}",'
    elif form == "pinned":
        entry = f'    "{portpin.port_pinned_rule(port)}",'
    else:
        entry = '    "network inet udp peer=(ip=10.0.0.2)",'
    return base + [entry, "})"]


def _test_profiles_lines(form: str, port: int = 8080) -> list[str]:
    if form == "unbound":
        line = f'expected = {{"pengy-agent": ["{UNBOUND}"]}}'
    elif form == "pinned":
        line = f'expected = {{"pengy-agent": ["{portpin.port_pinned_rule(port)}"]}}'
    else:
        line = 'expected = {"pengy-agent": []}'
    return ["    " + line]


def _test_validator_lines(form: str, port: int = 8080) -> list[str]:
    if form == "unbound":
        return ['        rule = _model_server_rule()', '        assert "port=" not in rule']
    if form == "pinned":
        return [
            f'        rule = _model_server_rule("{port}")',
            f'        assert f"port={port}" in rule',
        ]
    if form == "assert-unflipped":
        # A half-done flip: the anchor line was edited, its assert was not.
        return [
            f'        rule = _model_server_rule("{port}")',
            '        assert "port=" not in rule',
        ]
    return ["        rule = _model_server_rule(123)"]


def build_repo(root: Path, port: int = 8080, **forms: str) -> Path:
    """Write the four transition files under *root* in the requested
    forms (default per file: ``unbound``). Returns *root*."""
    spec = {
        "profiles/pengy-agent": forms.get("profiles/pengy-agent", "unbound"),
        "pengyos_apparmor/validator.py": forms.get("pengyos_apparmor/validator.py", "unbound"),
        "tests/test_profiles.py": forms.get("tests/test_profiles.py", "unbound"),
        "tests/test_validator.py": forms.get("tests/test_validator.py", "unbound"),
    }
    writers = {
        "profiles/pengy-agent": lambda f: _profile_lines(f, port),
        "pengyos_apparmor/validator.py": lambda f: _validator_lines(f, port),
        "tests/test_profiles.py": lambda f: _test_profiles_lines(f, port),
        "tests/test_validator.py": lambda f: _test_validator_lines(f, port),
    }
    for rel, form in spec.items():
        _write_file(root, rel, *writers[rel](form))
    return root


@pytest.fixture
def empty_scan(tmp_path) -> Path:
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "README.md").write_text("nothing here defines a model server port\n")
    return scan


# ---------------------------------------------------------------------------
# State classification (pure inspect_transition on fixture repos)
# ---------------------------------------------------------------------------


def test_all_unbound_no_upstream_candidate_is_blocked(tmp_path, empty_scan):
    build_repo(tmp_path / "repo")
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_BLOCKED
    assert status.port is None
    assert status.candidates == ()
    assert all(f.form == portstatus.FORM_UNBOUND for f in status.files)
    assert [f.rel for f in status.files] == list(portpin.PORT_PIN_TRANSITION_FILES)


def test_all_unbound_with_upstream_candidate_is_ready(tmp_path, empty_scan):
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(tmp_path / "repo")
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_READY
    assert status.port is None
    assert [c.port for c in status.candidates] == [8080]
    assert all(f.form == portstatus.FORM_UNBOUND for f in status.files)


def test_all_pinned_to_one_port_is_pinned(tmp_path, empty_scan):
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.port == 8080
    assert all(f.form == portstatus.FORM_PINNED and f.port == 8080 for f in status.files)


def test_pinned_state_reports_upstream_candidate_as_evidence(tmp_path, empty_scan):
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.port == 8080
    assert [c.port for c in status.candidates] == [8080]


def test_profile_pinned_others_unbound_is_partial(tmp_path, empty_scan):
    build_repo(tmp_path / "repo", port=8080, **{"profiles/pengy-agent": "pinned"})
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    assert status.port is None
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_PINNED
    assert by_rel["profiles/pengy-agent"].port == 8080
    assert sum(
        1
        for f in status.files
        if f.form == portstatus.FORM_UNBOUND
    ) == 3


def test_two_different_ports_is_partial(tmp_path, empty_scan):
    # The profile pins 8080, the other three files pin 9000: a conflict.
    root = build_repo(tmp_path / "repo", port=8080, **{"profiles/pengy-agent": "pinned"})
    _write_file(root, "pengyos_apparmor/validator.py", *_validator_lines("pinned", 9000))
    _write_file(root, "tests/test_profiles.py", *_test_profiles_lines("pinned", 9000))
    _write_file(root, "tests/test_validator.py", *_test_validator_lines("pinned", 9000))
    status = portstatus.inspect_transition(root, empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].port == 8080
    assert by_rel["pengyos_apparmor/validator.py"].port == 9000


def test_port_range_in_profile_is_drifted_partial(tmp_path, empty_scan):
    build_repo(tmp_path / "repo", **{"profiles/pengy-agent": "range"})
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_DRIFTED
    assert "port range" in by_rel["profiles/pengy-agent"].detail


def test_out_of_range_port_in_profile_is_drifted_partial(tmp_path, empty_scan):
    build_repo(tmp_path / "repo", **{"profiles/pengy-agent": "badport"})
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_DRIFTED
    assert "invalid port" in by_rel["profiles/pengy-agent"].detail


def test_both_forms_in_profile_is_drifted_partial(tmp_path, empty_scan):
    build_repo(tmp_path / "repo", **{"profiles/pengy-agent": "both"})
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_DRIFTED
    assert "both" in by_rel["profiles/pengy-agent"].detail


def test_unrecognizable_profile_rule_is_drifted_partial(tmp_path, empty_scan):
    build_repo(tmp_path / "repo", **{"profiles/pengy-agent": "missing"})
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["profiles/pengy-agent"].form == portstatus.FORM_DRIFTED
    assert "neither" in by_rel["profiles/pengy-agent"].detail


def test_unflipped_companion_assert_is_drifted_partial(tmp_path, empty_scan):
    # The exact failure mode of a half-applied test-pin flip.
    build_repo(tmp_path / "repo", **{"tests/test_validator.py": "assert-unflipped"})
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    by_rel = {f.rel: f for f in status.files}
    assert by_rel["tests/test_validator.py"].form == portstatus.FORM_DRIFTED
    assert "companion assert" in by_rel["tests/test_validator.py"].detail


def test_missing_transition_file_raises_port_status_error(tmp_path, empty_scan):
    root = build_repo(tmp_path / "repo")
    (root / "tests" / "test_profiles.py").unlink()
    with pytest.raises(portstatus.PortStatusError) as exc:
        portstatus.inspect_transition(root, empty_scan)
    assert "tests/test_profiles.py" in str(exc.value)


def test_scan_root_not_a_directory_is_port_discovery_error(tmp_path):
    build_repo(tmp_path / "repo")
    with pytest.raises(portdiscovery.PortDiscoveryError):
        portstatus.inspect_transition(tmp_path / "repo", tmp_path / "nope")


# ---------------------------------------------------------------------------
# End to end: the real rendered patch on a copy of the committed repo
# ---------------------------------------------------------------------------


@pytest.fixture
def copied_repo(tmp_path) -> Path:
    """A tree with the four committed transition files, unmodified."""
    repo = tmp_path / "repo"
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        _write_file(repo, rel, *(TOOL_ROOT / rel).read_text(encoding="utf-8").splitlines())
    return repo


def test_copied_unmodified_repo_is_blocked(copied_repo, empty_scan):
    status = portstatus.inspect_transition(copied_repo, empty_scan)
    assert status.state == portstatus.STATE_BLOCKED
    assert all(f.form == portstatus.FORM_UNBOUND for f in status.files)


def test_applied_rendered_patch_makes_the_repo_pinned(copied_repo, empty_scan):
    patch = portpin.render_transition_patch("8080", copied_repo)
    git = shutil.which("git")
    assert git is not None, "this end-to-end test needs git"
    subprocess.run([git, "init", "-q", str(copied_repo)], check=True)
    subprocess.run([git, "apply", "-q", "-"], input=patch, text=True, cwd=copied_repo, check=True)
    status = portstatus.inspect_transition(copied_repo, empty_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.port == 8080
    assert all(f.form == portstatus.FORM_PINNED and f.port == 8080 for f in status.files)
    # The upstream scan is still empty: the repo is consistent, but the
    # decision is not upstream's yet (the CLI prints a caution for this).
    assert status.candidates == ()


def test_half_applied_patch_is_partial(copied_repo, empty_scan):
    # Only the profile half of the transition was applied (what a human
    # gets if they edit the profile by hand and never touch the other
    # three files). Nothing else in the tool reports this until pytest.
    plan = portpin.plan_port_pin("8080")
    rel = "profiles/pengy-agent"
    path = copied_repo / rel
    path.write_text(
        portpin.apply_port_pin(path.read_text(encoding="utf-8"), plan),
        encoding="utf-8",
    )
    status = portstatus.inspect_transition(copied_repo, empty_scan)
    assert status.state == portstatus.STATE_PARTIAL
    assert status.port is None
    by_rel = {f.rel: f for f in status.files}
    assert by_rel[rel].form == portstatus.FORM_PINNED
    assert by_rel[rel].port == 8080
    assert all(
        by_rel[r].form == portstatus.FORM_UNBOUND
        for r in portpin.PORT_PIN_TRANSITION_FILES
        if r != rel
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(args, capsys):
    try:
        code = main(args)
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_blocked_on_the_live_repo_and_workspace(capsys):
    # ANCHOR TEST: the committed repo is port-unbound and the PengyOS
    # workspace defines no model-server port, so the state is BLOCKED
    # with exit 3. The first time PengyOS defines the port (or the
    # transition is applied), this test goes red first and tells you
    # exactly which re-check to update — the same visible signal as the
    # find-model-server-port default-root anchor.
    code, out, _err = _cli(
        [
            "port-pin-status",
            str(TOOL_ROOT),
            "--scan-root",
            str(portdiscovery.DEFAULT_SCAN_ROOT),
        ],
        capsys,
    )
    assert code == 3, out + _err
    assert "BLOCKED" in out
    assert "unbound" in out
    assert "do not guess a port" in out


def test_cli_ready_prints_the_follow_up_command(tmp_path, empty_scan, capsys):
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(tmp_path / "repo")
    code, out, _err = _cli(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == 0, out + _err
    assert "READY" in out
    assert "candidate: SPEC.md:1: port 8080" in out
    assert "pengyos-apparmor plan-port-pin 8080 --patch" in out


def test_cli_pinned_without_upstream_is_not_yet_commitable_exit_4(
    tmp_path, empty_scan, capsys
):
    """A pinned repo whose port is NOT defined upstream is the same
    internally-consistent ``pinned`` state, but it must NOT be commit-able:
    the probe reports it and exits 4 (distinct from the commit-able
    exit 0), so a wrapper can gate the runbook's commit step on the code."""
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    code, out, _err = _cli(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == 4, out + _err
    assert code == portstatus.EXIT_NOT_YET_COMMITABLE
    assert "PINNED to port 8080" in out
    assert "commit-ability: not-yet-commit-able" in out
    assert "do not commit the pin until PengyOS defines it" in out


def test_cli_pinned_with_matching_upstream_candidate(tmp_path, empty_scan, capsys):
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    code, out, _err = _cli(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == 0, out + _err
    assert code == portstatus.EXIT_OK
    assert "PINNED to port 8080" in out
    assert "commit-ability: commit-able" in out
    assert "upstream evidence: port 8080 found" in out


def test_cli_pinned_matching_upstream_is_commitable_at_module_level(tmp_path, empty_scan):
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.commitable is True
    assert status.commit_ability() == portstatus.COMMITABLE


# ---------------------------------------------------------------------------
# Commit-ability (the runbook's final-step machine signal)
# ---------------------------------------------------------------------------


def test_public_api_exposes_commitability_and_exit_codes():
    for attr in (
        "COMMITABLE",
        "NOT_YET_COMMITABLE",
        "EXIT_OK",
        "EXIT_PARTIAL",
        "EXIT_BAD_INPUT",
        "EXIT_BLOCKED",
        "EXIT_NOT_YET_COMMITABLE",
    ):
        assert hasattr(portstatus, attr), f"portstatus.{attr} missing"
    assert portstatus.COMMITABLE == "commit-able"
    assert portstatus.NOT_YET_COMMITABLE == "not-yet-commit-able"
    assert portstatus.EXIT_OK == 0
    assert portstatus.EXIT_PARTIAL == 1
    assert portstatus.EXIT_BAD_INPUT == 2
    assert portstatus.EXIT_BLOCKED == 3
    assert portstatus.EXIT_NOT_YET_COMMITABLE == 4


def test_commitable_true_only_when_pinned_and_port_is_upstream(tmp_path, empty_scan):
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.commitable is True


def test_commitable_false_when_pinned_but_no_upstream(tmp_path, empty_scan):
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.commitable is False
    assert status.commit_ability() == portstatus.NOT_YET_COMMITABLE


def test_commitable_false_when_pinned_but_upstream_says_a_different_port(
    tmp_path, empty_scan
):
    # The repo pins 8080 but upstream defines 9001: the pinned port is
    # NOT what PengyOS defines, so the pin is not ratifiable — not commit-able.
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 9001.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    status = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert status.state == portstatus.STATE_PINNED
    assert status.port == 8080
    assert status.commitable is False
    assert status.commit_ability() == portstatus.NOT_YET_COMMITABLE


def test_commitability_is_none_for_non_pinned_states(tmp_path, empty_scan):
    # blocked / ready / partial: commit-ability is only meaningful once the
    # repo is internally consistent (pinned).
    build_repo(tmp_path / "repo")  # all unbound -> blocked (no upstream)
    blocked = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert blocked.state == portstatus.STATE_BLOCKED
    assert blocked.commitable is False
    assert blocked.commit_ability() is None

    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    ready = portstatus.inspect_transition(tmp_path / "repo", empty_scan)
    assert ready.state == portstatus.STATE_READY
    assert ready.commitable is False
    assert ready.commit_ability() is None

    build_repo(tmp_path / "repo2", port=8080, **{"profiles/pengy-agent": "pinned"})
    partial = portstatus.inspect_transition(tmp_path / "repo2", empty_scan)
    assert partial.state == portstatus.STATE_PARTIAL
    assert partial.commitable is False
    assert partial.commit_ability() is None


def test_cli_pinned_matching_upstream_is_commitable_exit_0(
    tmp_path, empty_scan, capsys
):
    """The terminal commit-able state: pinned AND the port is defined
    upstream -> exit 0 with the commit-able verdict (the runbook's commit
    step is unblocked)."""
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 8080.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    code, out, _err = _cli(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == portstatus.EXIT_OK, out + _err
    assert "commit-ability: commit-able" in out


def test_cli_pinned_with_a_different_upstream_port_is_not_yet_commitable(
    tmp_path, empty_scan, capsys
):
    """Pinned to 8080 but upstream defines 9001: still a ``pinned`` repo,
    still exit 4 (not commit-able), and the probe names the mismatch."""
    (empty_scan / "SPEC.md").write_text(
        "The model server's default port is 9001.\n", encoding="utf-8"
    )
    build_repo(
        tmp_path / "repo",
        port=8080,
        **{rel: "pinned" for rel in portpin.PORT_PIN_TRANSITION_FILES},
    )
    code, out, _err = _cli(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == portstatus.EXIT_NOT_YET_COMMITABLE, out + _err
    assert "commit-ability: not-yet-commit-able" in out
    assert "9001" in out
    assert "none is the pinned port 8080" in out


def test_cli_partial_exits_one_with_per_file_detail(tmp_path, empty_scan, capsys):
    build_repo(tmp_path / "repo", **{"profiles/pengy-agent": "pinned"})
    code, out, _err = _cli(
        ["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == 1, out + _err
    assert "PARTIAL" in out
    assert "profiles/pengy-agent: pinned" in out
    assert "pengyos_apparmor/validator.py: unbound" in out
    assert "tests/test_profiles.py: unbound" in out
    assert "tests/test_validator.py: unbound" in out
    assert "reconcile the files above" in out


def test_cli_bad_repo_root_exits_two(tmp_path, empty_scan, capsys):
    code, _out, err = _cli(
        ["port-pin-status", str(tmp_path / "missing"), "--scan-root", str(empty_scan)],
        capsys,
    )
    assert code == 2
    assert "transition file missing" in err


def test_cli_bad_scan_root_exits_two(tmp_path, capsys):
    code, _out, err = _cli(
        ["port-pin-status", str(tmp_path), "--scan-root", str(tmp_path / "nope")],
        capsys,
    )
    assert code == 2
    assert "no such directory" in err


def test_cli_repo_root_is_read_only(tmp_path, empty_scan, capsys):
    # The probe never modifies anything — a dry run is a dry run.
    build_repo(tmp_path / "repo")
    before = {
        rel: (tmp_path / "repo" / rel).read_bytes()
        for rel in portpin.PORT_PIN_TRANSITION_FILES
    }
    _cli(["port-pin-status", str(tmp_path / "repo"), "--scan-root", str(empty_scan)], capsys)
    after = {
        rel: (tmp_path / "repo" / rel).read_bytes()
        for rel in portpin.PORT_PIN_TRANSITION_FILES
    }
    assert before == after
